-- [제안] 20261011000000_case_progress.sql 되돌리기
-- 주의: 저장된 사건 진행 체크리스트가 모두 삭제됩니다. 사건·초안·증거파일은 영향이 없습니다.

drop trigger if exists cases_delete_case_progress on public.cases;
drop function if exists public.delete_case_progress_with_case();
drop table if exists public.case_progress;
drop function if exists public.case_progress_touch_updated_at();
