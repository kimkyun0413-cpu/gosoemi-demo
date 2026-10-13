-- [수정안 · 운영 미적용] AI 사용량 기록을 service_role(전체 권한) 대신 전용 최소 권한 역할로 쓰게 한다.
-- 전제: supabase/migrations/20261014000000_ai_usage.sql 이 먼저 적용되어 있어야 한다.
-- 왜: service_role 키는 RLS를 건너뛰어 모든 이용자의 사건·파일·계정을 읽고 고칠 수 있다. 사용량 기록에는
--     '예약(reserve)'과 '완료(finish)' 두 함수만 필요하므로, 그 둘만 실행할 수 있는 로그인 역할을 만든다.
-- 이 역할이 할 수 없는 것: 어떤 표도 읽기·쓰기 불가(사건·초안·동의·사용량 표 포함), 정리·보고·본인 기록 함수 실행 불가,
--     다른 역할로 바꾸기 불가, RLS 우회 불가. 이용자(anon·authenticated)는 지금처럼 예약·완료를 부를 수 없다(조작 불가).
-- 비밀번호는 이 파일에 없다. 적용한 뒤 운영자가 SQL Editor에서 정한다:
--     alter role gosomi_ai_usage password '<긴 무작위 값>';   (값은 서버 .env의 AI_USAGE_DB_URL 에만 둔다)
-- 연결 주소(서버 .env): postgresql://gosomi_ai_usage.<프로젝트ref>:<비밀번호>@<지역>.pooler.supabase.com:6543/postgres
-- 되돌리기: least_privilege_rollback.sql
begin;

do $$ begin
  if not exists (select from pg_roles where rolname = 'gosomi_ai_usage') then
    create role gosomi_ai_usage login noinherit nosuperuser nocreatedb nocreaterole noreplication nobypassrls
      connection limit 5;
  end if;
end $$;
alter role gosomi_ai_usage set statement_timeout = '5s';
alter role gosomi_ai_usage set idle_in_transaction_session_timeout = '10s';

-- 예약·완료 두 함수만 실행 가능
grant execute on function public.reserve_ai_usage(uuid,uuid,text,text,integer,integer,integer,integer) to gosomi_ai_usage;
grant execute on function public.finish_ai_usage(uuid,text,bigint,bigint,bigint,bigint) to gosomi_ai_usage;

-- service_role 은 더 이상 필요 없다(서버에 service_role 키를 두지 않는다)
revoke execute on function public.reserve_ai_usage(uuid,uuid,text,text,integer,integer,integer,integer) from service_role;
revoke execute on function public.finish_ai_usage(uuid,text,bigint,bigint,bigint,bigint) from service_role;
-- 30일 정리는 DB 안 예약 작업(pg_cron, prune_schedule.sql)으로, 보고는 운영자가 SQL Editor에서 실행한다(키 불필요)
revoke execute on function public.prune_ai_usage() from service_role;
revoke execute on function public.ai_usage_report() from service_role;

commit;

-- 적용 뒤 점검(읽기만): PUBLIC이 실행할 수 있는 public 스키마 함수 목록. 새 역할도 PUBLIC에 속하므로,
-- 여기에 나오는 SECURITY DEFINER 함수가 로그인 확인(auth.uid()) 없이 자료를 다루면 별도로 막아야 한다.
-- select p.proname, p.prosecdef from pg_proc p join pg_namespace n on n.oid = p.pronamespace
--  where n.nspname = 'public' and has_function_privilege('public', p.oid, 'execute') order by 1;
