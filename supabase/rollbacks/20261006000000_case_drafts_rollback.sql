-- 20261006000000_case_drafts.sql 되돌리기
-- 주의: 저장된 고소장 초안이 모두 삭제됩니다. 사건·증거파일·동의 기록은 영향이 없습니다.
-- 앱의 DRAFT_SAVE_ENABLED 설정을 먼저 끈 뒤 실행하세요.

drop trigger if exists cases_delete_case_draft on public.cases;
drop function if exists public.delete_case_draft_with_case();
drop table if exists public.case_drafts;
drop function if exists public.case_drafts_touch_updated_at();
