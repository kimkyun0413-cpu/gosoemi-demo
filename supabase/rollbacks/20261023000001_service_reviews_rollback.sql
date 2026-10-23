-- 20261023000001_service_reviews.sql 되돌리기
-- 주의: 모든 이용 후기와 후기 검토 운영자 목록이 삭제됩니다. 앱의 REVIEWS_ENABLED를 먼저 끈 뒤 실행하세요.

drop function if exists public.moderate_review(uuid, text, text);
drop function if exists public.reviews_for_moderation(text);
drop function if exists public.published_reviews(integer, integer);
drop table if exists public.service_reviews;
drop function if exists public.service_reviews_owner_write();
drop table if exists public.review_admins;
