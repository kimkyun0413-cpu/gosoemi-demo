-- [제안, 운영 미적용] 사건 작업 공간 저장: 사건 경위(타임라인)·증거 설명·일정·준비 체크리스트·이 화면에서 채운 답
-- - case_progress 제안(20261011000000)을 대신한다(체크리스트를 이 표의 checklist로 옮김). 둘 중 하나만 적용한다.
-- - 사건(public.cases)마다 1개. 본인 사건에만 만들고 고칠 수 있다(auth.uid() 기준). 비로그인(anon)은 접근할 수 없다.
-- - 회원탈퇴(auth.users 삭제) 시 외래키 cascade, 사건 삭제 시 트리거로 함께 삭제. 기존 cases 열·정책·권한은 바꾸지 않는다.
-- - 초안 본문은 기존 case_drafts에 그대로 둔다(이 표에 넣지 않는다).
-- - 되돌리기: 20261012000000_case_workspace_rollback.sql

create table public.case_workspace (
    user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
    case_id text not null check (char_length(case_id) between 1 and 64),
    answers jsonb not null default '{}'::jsonb check (jsonb_typeof(answers) = 'object' and pg_column_size(answers) <= 8192),
    timeline jsonb not null default '[]'::jsonb check (jsonb_typeof(timeline) = 'array' and pg_column_size(timeline) <= 32768),
    evidence_notes jsonb not null default '[]'::jsonb check (jsonb_typeof(evidence_notes) = 'array' and pg_column_size(evidence_notes) <= 16384),
    schedules jsonb not null default '[]'::jsonb check (jsonb_typeof(schedules) = 'array' and pg_column_size(schedules) <= 16384),
    checklist jsonb not null default '{}'::jsonb check (jsonb_typeof(checklist) = 'object' and pg_column_size(checklist) <= 2048),
    updated_at timestamptz not null default now(),
    primary key (user_id, case_id)
);

comment on table public.case_workspace is '[제안] 이용자가 직접 정리한 사건 경위·증거 설명·일정·체크리스트. 사건당 1개, 본인만 접근.';

alter table public.case_workspace enable row level security;
alter table public.case_workspace force row level security;

revoke all on table public.case_workspace from public, anon, authenticated;
grant select, insert, update, delete on table public.case_workspace to authenticated;

create policy "case workspace select own" on public.case_workspace
for select to authenticated using ((select auth.uid()) = user_id);

create policy "case workspace insert own case" on public.case_workspace
for insert to authenticated
with check (
    (select auth.uid()) = user_id
    and exists (select 1 from public.cases c where c.case_id::text = case_workspace.case_id and c.user_id = (select auth.uid()))
);

create policy "case workspace update own case" on public.case_workspace
for update to authenticated
using ((select auth.uid()) = user_id)
with check (
    (select auth.uid()) = user_id
    and exists (select 1 from public.cases c where c.case_id::text = case_workspace.case_id and c.user_id = (select auth.uid()))
);

create policy "case workspace delete own" on public.case_workspace
for delete to authenticated using ((select auth.uid()) = user_id);

create function public.case_workspace_touch_updated_at()
returns trigger language plpgsql set search_path = '' as $$
begin
    new.updated_at := now();
    return new;
end $$;

create trigger case_workspace_touch before update on public.case_workspace
for each row execute function public.case_workspace_touch_updated_at();

create function public.delete_case_workspace_with_case()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
    delete from public.case_workspace where user_id = old.user_id and case_id = old.case_id::text;
    return old;
end $$;
revoke all on function public.delete_case_workspace_with_case() from public, anon, authenticated;

create trigger cases_delete_case_workspace after delete on public.cases
for each row execute function public.delete_case_workspace_with_case();
