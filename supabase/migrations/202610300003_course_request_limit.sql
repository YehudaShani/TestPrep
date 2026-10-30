-- Run once after 202610300002_course_requests.sql.
-- Atomic counters enforce the cap even for simultaneous requests on two devices.
begin;

create table public.course_request_quotas (
    user_id uuid primary key references auth.users(id) on delete cascade,
    request_count integer not null check (request_count >= 0)
);
alter table public.course_request_quotas enable row level security;
revoke all on public.course_request_quotas from public, anon, authenticated;

-- Preserve existing requests and count them toward the limit.
lock table public.course_requests in share row exclusive mode;
insert into public.course_request_quotas (user_id, request_count)
    select user_id, count(*)::integer from public.course_requests group by user_id;

create function public.enforce_course_request_limit() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
    reserved_count integer;
begin
    if tg_op = 'DELETE' then
        update public.course_request_quotas
            set request_count = greatest(0, request_count - 1)
            where user_id = old.user_id;
        return old;
    end if;

    if (select auth.uid()) is distinct from new.user_id then
        raise exception 'Requests must belong to the signed-in user' using errcode = '42501';
    end if;

    insert into public.course_request_quotas as quota (user_id, request_count)
        values (new.user_id, 1)
        on conflict (user_id) do update
            set request_count = quota.request_count + 1
            where quota.request_count < 3
        returning request_count into reserved_count;

    if reserved_count is null then
        raise exception 'course_request_limit_reached' using errcode = 'P0001';
    end if;
    -- A failed/duplicate course insert rolls back this reservation too.
    return new;
end;
$$;
revoke all on function public.enforce_course_request_limit() from public, anon, authenticated;

create trigger course_request_limit before insert on public.course_requests
    for each row execute function public.enforce_course_request_limit();
create trigger course_request_release after delete on public.course_requests
    for each row execute function public.enforce_course_request_limit();

commit;
