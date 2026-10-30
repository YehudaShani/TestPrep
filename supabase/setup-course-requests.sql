-- COURSE REQUESTS: complete one-time setup
-- Paste this entire file into Supabase > SQL Editor > New query and click Run.
-- Includes signed-in-only requests, private access, and a three-course limit.
-- Use this instead of running migrations 202610300002 and 202610300003 separately.
-- The existing question-progress table is not changed.

begin;

create table public.course_requests (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    course_name text not null check (
        char_length(course_name) between 2 and 160 and course_name = btrim(course_name)
    ),
    created_at timestamptz not null default now()
);

create unique index course_requests_user_course
    on public.course_requests (user_id, lower(course_name));

alter table public.course_requests enable row level security;
revoke all on public.course_requests from anon, authenticated;
grant insert (user_id, course_name) on public.course_requests to authenticated;

create policy "Submit own course requests" on public.course_requests
    for insert to authenticated with check ((select auth.uid()) = user_id);

-- Requests are visible to the project owner in the Supabase Table Editor.
-- Browser clients cannot list, edit, or delete requests.

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
