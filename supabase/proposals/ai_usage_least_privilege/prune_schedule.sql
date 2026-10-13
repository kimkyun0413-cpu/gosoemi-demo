-- [선택 · 운영 미적용] 30일 지난 사용량 기록을 매일 DB 안에서 지운다(키 불필요). pg_cron 확장이 켜져 있어야 한다
-- (Supabase 대시보드 → Integrations → Cron. Free 요금제에서 쓸 수 있는지는 적용 전 확인).
select cron.schedule('gosomi-prune-ai-usage', '30 18 * * *', $$select public.prune_ai_usage()$$);  -- 매일 03:30(한국)
-- 되돌리기: select cron.unschedule('gosomi-prune-ai-usage');
