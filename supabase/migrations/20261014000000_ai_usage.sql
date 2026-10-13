-- Unapplied. Server-only usage ledger; no case text, prompts, answers, audio or credentials.
-- 기록은 전용 최소 권한 로그인 역할 gosomi_ai_usage만 쓴다(예약·완료 두 함수 실행만 가능, 어떤 표도 읽지 못함).
-- service_role 키는 쓰지 않는다. 이용자(anon·authenticated)는 예약·완료를 부를 수 없다(조작 불가).
-- 비밀번호는 이 파일에 없다. 적용 뒤 SQL Editor에서: alter role gosomi_ai_usage password '<긴 무작위 값>';
-- 30일 정리: 예약 때마다 30일 지난 기록을 함께 지운다(요금제와 무관). pg_cron이 되면 매일 정리도 추가 가능(docs).
begin;
do $$ begin
  if not exists (select from pg_roles where rolname = 'gosomi_ai_usage') then
    create role gosomi_ai_usage login noinherit nosuperuser nocreatedb nocreaterole noreplication nobypassrls
      connection limit 5;
  end if;
end $$;
alter role gosomi_ai_usage set statement_timeout = '5s';
alter role gosomi_ai_usage set idle_in_transaction_session_timeout = '10s';
create schema if not exists ai_private;
revoke all on schema ai_private from public, anon, authenticated;
create table ai_private.requests (
  request_id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  feature text not null check(feature in ('chat','voice_chat','draft','rewrite','transcribe','extract','summary','tts')),
  model text not null check(length(model) between 1 and 100),
  started_at timestamptz not null default clock_timestamp(),
  finished_at timestamptz,
  status text not null default 'reserved' check(status in ('reserved','succeeded','failed')),
  input_tokens bigint check(input_tokens >= 0),
  output_tokens bigint check(output_tokens >= 0),
  thinking_tokens bigint check(thinking_tokens >= 0),
  total_tokens bigint check(total_tokens >= 0)
);
alter table ai_private.requests enable row level security;
revoke all on ai_private.requests from public, anon, authenticated;
create index on ai_private.requests(started_at);
create index on ai_private.requests(user_id, started_at);

create function public.reserve_ai_usage(p_user_id uuid, p_request_id uuid, p_feature text, p_model text,
 p_user_hour integer, p_total_hour integer, p_user_day integer, p_total_day integer)
returns text language plpgsql security definer set search_path = '' as $$
declare n timestamptz := clock_timestamp();
begin
 if least(p_user_hour,p_total_hour,p_user_day,p_total_day) < 1 or
    p_user_hour is null or p_total_hour is null or p_user_day is null or p_total_day is null then
   raise exception 'invalid usage limits';
 end if;
 -- Same transaction-wide lock for all callers, including separate servers/processes.
 perform pg_advisory_xact_lock(713140001);
 if exists(select 1 from ai_private.requests where request_id=p_request_id) then
   raise exception 'duplicate AI request';
 end if;
 -- 보관 기간 30일: 지난 기록은 여기서 함께 지운다(started_at 색인 사용, 보통 0건).
 delete from ai_private.requests where started_at < n - interval '30 days';
 if (select count(*) from ai_private.requests where user_id=p_user_id and started_at > n-interval '1 hour') >= p_user_hour then return 'user_hour'; end if;
 if (select count(*) from ai_private.requests where started_at > n-interval '1 hour') >= p_total_hour then return 'total_hour'; end if;
 if (select count(*) from ai_private.requests where user_id=p_user_id and started_at > n-interval '24 hours') >= p_user_day then return 'user_day'; end if;
 if (select count(*) from ai_private.requests where started_at > n-interval '24 hours') >= p_total_day then return 'total_day'; end if;
 insert into ai_private.requests(request_id,user_id,feature,model) values(p_request_id,p_user_id,p_feature,p_model);
 return 'reserved';
end $$;

create function public.finish_ai_usage(p_request_id uuid, p_status text,
 p_input bigint, p_output bigint, p_thinking bigint, p_total bigint)
returns boolean language plpgsql security definer set search_path = '' as $$
begin
 if p_status not in ('succeeded','failed') or p_status is null then raise exception 'invalid result'; end if;
 update ai_private.requests set status=p_status, finished_at=clock_timestamp(),
   input_tokens=p_input, output_tokens=p_output, thinking_tokens=p_thinking, total_tokens=p_total
 where request_id=p_request_id and status='reserved';
 return found;
end $$;
revoke all on function public.reserve_ai_usage(uuid,uuid,text,text,integer,integer,integer,integer) from public, anon, authenticated;
revoke all on function public.finish_ai_usage(uuid,text,bigint,bigint,bigint,bigint) from public, anon, authenticated;
grant execute on function public.reserve_ai_usage(uuid,uuid,text,text,integer,integer,integer,integer) to gosomi_ai_usage;
grant execute on function public.finish_ai_usage(uuid,text,bigint,bigint,bigint,bigint) to gosomi_ai_usage;
create function public.my_ai_usage() returns jsonb language plpgsql security definer set search_path='' as $$
declare uid uuid := auth.uid(); result jsonb;
begin
 if uid is null then raise exception 'login required'; end if;
 select coalesce(jsonb_agg(to_jsonb(r) order by r.started_at), '[]'::jsonb) into result
 from ai_private.requests r where r.user_id=uid and r.started_at >= clock_timestamp()-interval '30 days';
 return result;
end $$;
revoke all on function public.my_ai_usage() from public, anon;
grant execute on function public.my_ai_usage() to authenticated;

create function public.prune_ai_usage() returns bigint language plpgsql security definer set search_path='' as $$
declare deleted bigint;
begin
 delete from ai_private.requests where started_at < clock_timestamp()-interval '30 days';
 get diagnostics deleted=row_count;
 return deleted;
end $$;
revoke all on function public.prune_ai_usage() from public, anon, authenticated;
-- 실행: 운영자가 SQL Editor에서 또는 pg_cron(소유자 권한). 서버 계정·service_role에는 주지 않는다.

create function public.ai_usage_report() returns jsonb language sql security definer set search_path='' as $$
 select coalesce(jsonb_agg(to_jsonb(r)), '[]'::jsonb) from (
   select model,feature,count(*) as requests,
     count(*) filter(where status='reserved') as unfinished,
     count(*) filter(where status='failed') as failed,
     count(*) filter(where total_tokens is null) as unknown_usage,
     sum(input_tokens) as input_tokens,sum(output_tokens) as output_tokens,
     sum(thinking_tokens) as thinking_tokens,sum(total_tokens) as total_tokens
   from ai_private.requests where started_at >= clock_timestamp()-interval '30 days'
   group by model,feature order by model,feature
 ) r
$$;
revoke all on function public.ai_usage_report() from public, anon, authenticated;
-- 보고: 운영자가 SQL Editor에서 select public.ai_usage_report(); (계정 식별자·내용 없음)
commit;
