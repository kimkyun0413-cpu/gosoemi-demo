-- 20261012000000_case_workspace_and_case_edit.sql 되돌리기
-- 주의: 저장된 사건 작업 공간(경위·증거 설명·일정·체크)이 모두 삭제됩니다. 이미 고친 사건 내용은 고친 그대로 남습니다.
-- 앱의 CASE_WORKSPACE_SAVE_ENABLED를 먼저 끈 뒤 실행하세요.

drop trigger if exists cases_delete_case_workspace on public.cases;
drop function if exists public.delete_case_workspace_with_case();
drop table if exists public.case_workspace;
drop function if exists public.case_workspace_touch();

drop policy if exists "cases update own" on public.cases;
revoke update (
    case_type, reporter_info, suspect_info, incident_datetime, incident_location,
    incident_description, damage_details, evidence, witness, punishment_intent
) on table public.cases from authenticated;
drop trigger if exists cases_touch_edit on public.cases;
drop function if exists public.cases_touch_edit();
alter table public.cases drop column if exists edit_version;
alter table public.cases drop column if exists updated_at;
