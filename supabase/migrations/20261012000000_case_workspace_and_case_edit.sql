-- [준비, 운영 미적용] 사건 작업 공간 저장 + 저장한 사건 수정
-- 1) public.cases: 본인 사건의 내용 칸만 고칠 수 있게 한다(update 권한·정책 추가). 동시에 고친 것을 알아채도록
--    edit_version(고칠 때마다 1씩 증가, 트리거)과 updated_at을 더한다. case_id·user_id·created_at은 고칠 수 없다(열 단위 권한).
-- 2) public.case_workspace: 사건마다 1개. 사건 경위(timeline)·증거 설명(evidence_notes)·일정(schedules)·준비 체크(checklist).
--    version은 저장할 때마다 트리거가 1씩 올린다. 앱은 "읽은 version과 같을 때만" 고쳐서, 다른 곳에서 먼저 저장한 내용을
--    확인 없이 덮어쓰지 않는다.
-- - 초안 본문은 기존 public.case_drafts를 그대로 쓴다(이 표에 넣지 않는다).
-- - 본인 것만 조회·수정·삭제(auth.uid()). 비로그인(anon) 접근 불가. 사건 삭제 시 트리거로, 회원탈퇴 시 외래키 cascade로 삭제.
-- - 시험: supabase/tests/local_case_workspace_edit_test.py (로컬 임시 Postgres)
-- - 되돌리기: supabase/rollbacks/20261012000000_case_workspace_and_case_edit_rollback.sql

-- ── 1. 사건 수정 ──────────────────────────────────────────────
alter table public.cases add column if not exists edit_version integer not null default 1;
alter table public.cases add column if not exists updated_at timestamptz;

create function public.cases_touch_edit()
returns trigger language plpgsql set search_path = '' as $$
begin
    new.edit_version := old.edit_version + 1;
    new.updated_at := now();
    return new;
end $$;

create trigger cases_touch_edit before update on public.cases
for each row execute function public.cases_touch_edit();

grant update (
    case_type, reporter_info, suspect_info, incident_datetime, incident_location,
    incident_description, damage_details, evidence, witness, punishment_intent
) on table public.cases to authenticated;

create policy "cases update own" on public.cases
for update to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

-- ── 2. 사건 작업 공간 ─────────────────────────────────────────
create table public.case_workspace (
    user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
    case_id text not null check (char_length(case_id) between 1 and 64),
    timeline jsonb not null default '[]'::jsonb check (jsonb_typeof(timeline) = 'array' and pg_column_size(timeline) <= 32768),
    evidence_notes jsonb not null default '[]'::jsonb check (jsonb_typeof(evidence_notes) = 'array' and pg_column_size(evidence_notes) <= 16384),
    schedules jsonb not null default '[]'::jsonb check (jsonb_typeof(schedules) = 'array' and pg_column_size(schedules) <= 16384),
    checklist jsonb not null default '{}'::jsonb check (jsonb_typeof(checklist) = 'object' and pg_column_size(checklist) <= 2048),
    version integer not null default 1,
    updated_at timestamptz not null default now(),
    primary key (user_id, case_id)
);

comment on table public.case_workspace is '이용자가 직접 정리한 사건 경위·증거 설명·일정·준비 체크. 사건당 1개, 본인만 접근.';

alter table public.case_workspace enable row level security;
alter table public.case_workspace force row level security;

revoke all on table public.case_workspace from public, anon, authenticated;
grant select, insert, delete on table public.case_workspace to authenticated;
grant update (timeline, evidence_notes, schedules, checklist) on table public.case_workspace to authenticated;

create policy "case workspace select own" on public.case_workspace
for select to authenticated using ((select auth.uid()) = user_id);

create policy "case workspace insert own case" on public.case_workspace
for insert to authenticated
with check (
    (select auth.uid()) = user_id
    and exists (select 1 from public.cases c where c.case_id::text = case_workspace.case_id and c.user_id = (select auth.uid()))
);

create policy "case workspace update own" on public.case_workspace
for update to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

create policy "case workspace delete own" on public.case_workspace
for delete to authenticated using ((select auth.uid()) = user_id);

create function public.case_workspace_touch()
returns trigger language plpgsql set search_path = '' as $$
begin
    new.version := old.version + 1;
    new.updated_at := now();
    return new;
end $$;

create trigger case_workspace_touch before update on public.case_workspace
for each row execute function public.case_workspace_touch();

create function public.delete_case_workspace_with_case()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
    delete from public.case_workspace where user_id = old.user_id and case_id = old.case_id::text;
    return old;
end $$;
revoke all on function public.delete_case_workspace_with_case() from public, anon, authenticated;

create trigger cases_delete_case_workspace after delete on public.cases
for each row execute function public.delete_case_workspace_with_case();
