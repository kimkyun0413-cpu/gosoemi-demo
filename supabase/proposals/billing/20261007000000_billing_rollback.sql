-- [제안] 20261007000000_billing.sql 되돌리기
-- 주의: 결제 기록이 생긴 뒤에는 실행하지 마세요. 전자상거래법 시행령 제6조에 따라 대금결제 기록은 5년 보존해야 합니다.
-- 적용 직후 테스트 단계에서만 사용합니다.
drop function if exists public.billing_retain_orders_for_user(uuid);
drop function if exists public.billing_has_entitlement(text);
drop function if exists public.billing_apply_event(text, text, text, text, uuid, text, integer, text);
drop function if exists public.billing_create_order(text, text);
drop table if exists billing_private.retained_orders;
drop table if exists public.billing_events;
drop table if exists public.billing_subscriptions;
drop table if exists public.billing_entitlements;
drop table if exists public.billing_orders;
drop table if exists public.billing_plans;
drop schema if exists billing_private;
