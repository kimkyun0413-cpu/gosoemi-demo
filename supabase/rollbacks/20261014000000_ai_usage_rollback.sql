-- Deletes usage metadata only. Does not change cases, files, accounts or consent.
begin;
drop function if exists public.my_ai_usage();
drop function if exists public.prune_ai_usage();
drop function if exists public.ai_usage_report();
drop function if exists public.reserve_ai_usage(uuid,uuid,text,text,integer,integer,integer,integer);
drop function if exists public.finish_ai_usage(uuid,text,bigint,bigint,bigint,bigint);
drop table if exists ai_private.requests;
drop schema if exists ai_private;
drop role if exists gosomi_ai_usage;
commit;
