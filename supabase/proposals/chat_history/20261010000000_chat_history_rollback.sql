-- [제안] 20261010000000_chat_history.sql 되돌리기
-- 주의: 저장된 AI 상담 대화가 모두 삭제됩니다. 사건·초안·증거파일은 영향이 없습니다.
-- 앱의 대화 저장 기능을 먼저 끈 뒤 실행하세요.

drop function if exists public.purge_expired_chat_conversations();
drop trigger if exists chat_conversations_limit on public.chat_conversations;
drop function if exists public.chat_conversations_limit();
drop table if exists public.chat_messages;
drop table if exists public.chat_conversations;
