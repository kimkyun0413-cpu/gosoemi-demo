-- 20261023000000_case_draft_archive.sql 되돌리기
-- 주의: 보관한 이전 초안이 모두 삭제됩니다. 저장된 초안(case_drafts)과 사건은 그대로입니다.
-- 앱의 DRAFT_ARCHIVE_ENABLED를 먼저 끈 뒤 실행하세요.

drop trigger if exists cases_delete_case_draft_archive on public.cases;
drop function if exists public.delete_case_draft_archive_with_case();
drop table if exists public.case_draft_archive;
drop function if exists public.case_draft_archive_touch();
