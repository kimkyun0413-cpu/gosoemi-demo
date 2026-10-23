-- A안 운영 반영 되돌리기. 앱 설정 DRAFT_ARCHIVE_ENABLED·REVIEWS_ENABLED를 먼저 지운 뒤 실행한다.
-- 주의: 보관한 이전 초안과 모든 이용 후기가 삭제된다. 사건·초안·사건 정리 기록은 그대로다.
-- 아래 두 되돌리기 파일을 그대로 이어 붙인 것이다(tests/test_release_sql.py가 같은지 검사).

begin;

-- 20261023000001_service_reviews.sql 되돌리기
-- 주의: 모든 이용 후기와 후기 검토 운영자 목록이 삭제됩니다. 앱의 REVIEWS_ENABLED를 먼저 끈 뒤 실행하세요.

drop function if exists public.moderate_review(uuid, text, text);
drop function if exists public.reviews_for_moderation(text);
drop function if exists public.published_reviews(integer, integer);
drop table if exists public.service_reviews;
drop function if exists public.service_reviews_owner_write();
drop table if exists public.review_admins;

-- 20261023000000_case_draft_archive.sql 되돌리기
-- 주의: 보관한 이전 초안이 모두 삭제됩니다. 저장된 초안(case_drafts)과 사건은 그대로입니다.
-- 앱의 DRAFT_ARCHIVE_ENABLED를 먼저 끈 뒤 실행하세요.

drop trigger if exists cases_delete_case_draft_archive on public.cases;
drop function if exists public.delete_case_draft_archive_with_case();
drop table if exists public.case_draft_archive;
drop function if exists public.case_draft_archive_touch();

commit;
