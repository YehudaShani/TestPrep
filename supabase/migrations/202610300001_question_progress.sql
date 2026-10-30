-- Run once in the Supabase SQL editor before connecting the viewer.
begin;

create table public.question_progress (
    user_id uuid not null references auth.users(id) on delete cascade,
    course_id text not null check (char_length(course_id) between 1 and 100),
    exam_id text not null check (char_length(exam_id) between 1 and 300),
    question_number integer not null check (question_number > 0),
    done boolean not null default false,
    flagged boolean not null default false,
    updated_at timestamptz not null default now(),
    primary key (user_id, course_id, exam_id, question_number)
);

alter table public.question_progress enable row level security;
revoke all on public.question_progress from anon, authenticated;
grant select, insert, update on public.question_progress to authenticated;

create policy "Read own progress" on public.question_progress
    for select to authenticated using ((select auth.uid()) = user_id);
create policy "Insert own progress" on public.question_progress
    for insert to authenticated with check ((select auth.uid()) = user_id);
create policy "Update own progress" on public.question_progress
    for update to authenticated using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

create function public.set_progress_updated_at() returns trigger
language plpgsql set search_path = '' as $$
begin
    new.updated_at = now();
    return new;
end;
$$;
create trigger progress_updated_at before update on public.question_progress
    for each row execute function public.set_progress_updated_at();

commit;
