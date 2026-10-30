-- Run once in the Supabase SQL editor to enable course requests.
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
commit;
