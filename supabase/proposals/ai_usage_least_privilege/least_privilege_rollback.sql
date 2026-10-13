-- [되돌리기] 최소 권한 역할을 없애고 PR 원안(service_role 실행)으로 돌린다. 사용량 기록은 지우지 않는다.
begin;
grant execute on function public.reserve_ai_usage(uuid,uuid,text,text,integer,integer,integer,integer) to service_role;
grant execute on function public.finish_ai_usage(uuid,text,bigint,bigint,bigint,bigint) to service_role;
grant execute on function public.prune_ai_usage() to service_role;
grant execute on function public.ai_usage_report() to service_role;
revoke execute on function public.reserve_ai_usage(uuid,uuid,text,text,integer,integer,integer,integer) from gosomi_ai_usage;
revoke execute on function public.finish_ai_usage(uuid,text,bigint,bigint,bigint,bigint) from gosomi_ai_usage;
drop role if exists gosomi_ai_usage;
commit;
