-- [제안] 20261012000000_case_workspace.sql 되돌리기
-- 주의: 저장된 사건 경위·증거 설명·일정·체크리스트가 모두 삭제됩니다. 사건·초안·증거파일은 영향이 없습니다.

drop trigger if exists cases_delete_case_workspace on public.cases;
drop function if exists public.delete_case_workspace_with_case();
drop table if exists public.case_workspace;
drop function if exists public.case_workspace_touch_updated_at();
