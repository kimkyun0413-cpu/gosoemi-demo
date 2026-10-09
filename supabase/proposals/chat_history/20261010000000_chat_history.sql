-- [제안, 운영 미적용] AI 상담 대화 저장·다시 열기
-- - 지금 앱은 대화를 DB에 저장하지 않는다. 이 파일은 운영자가 저장 기능을 원할 때 쓰도록 준비만 한 것이다.
-- - 적용 전에 필요한 것: 개인정보처리방침 개정(이 폴더 README의 문구안), 보관 기간 결정, 앱 코드 추가, 로컬 Postgres 검사.
-- - 본인 대화만 보고 만들고 지울 수 있다(auth.uid() 기준). 비로그인(anon)은 접근할 수 없다.
-- - 회원탈퇴(auth.users 삭제) 시 외래키 on delete cascade로 대화와 메시지가 함께 삭제된다.
-- - 보관 기간: expires_at(기본 30일, 운영자 결정 필요)이 지난 대화는 purge_expired_chat_conversations()로 지운다.
--   Free 요금제에서 주기 실행 방법(pg_cron 사용 여부)은 따로 결정한다. 앱도 목록을 열 때 만료된 것은 보여 주지 않는다.
-- - 음성은 저장하지 않는다. 메시지 본문(글)만 저장한다.
-- - 되돌리기: 20261010000000_chat_history_rollback.sql

create table public.chat_conversations (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
    title text not null default '' check (char_length(title) <= 80),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    expires_at timestamptz not null default now() + interval '30 days'
);

create table public.chat_messages (
    id bigint generated always as identity primary key,
    conversation_id uuid not null references public.chat_conversations (id) on delete cascade,
    user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
    role text not null check (role in ('user', 'assistant')),
    content text not null check (char_length(content) between 1 and 8000),
    via_voice boolean not null default false,
    created_at timestamptz not null default now()
);

create index chat_conversations_user_updated on public.chat_conversations (user_id, updated_at desc);
create index chat_messages_conversation on public.chat_messages (conversation_id, id);

comment on table public.chat_conversations is '[제안] 이용자가 저장한 AI 상담 대화. 본인만 접근, 보관 기간 후 삭제.';
comment on table public.chat_messages is '[제안] 저장한 대화의 질문·답변 글. 음성은 저장하지 않음.';

alter table public.chat_conversations enable row level security;
alter table public.chat_conversations force row level security;
alter table public.chat_messages enable row level security;
alter table public.chat_messages force row level security;

revoke all on table public.chat_conversations, public.chat_messages from public, anon, authenticated;
grant select, insert, update, delete on table public.chat_conversations to authenticated;
grant select, insert, delete on table public.chat_messages to authenticated;

create policy "chat conversations own" on public.chat_conversations
for all to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

create policy "chat messages select own" on public.chat_messages
for select to authenticated
using ((select auth.uid()) = user_id);

-- 본인 대화에만 메시지를 넣을 수 있다.
create policy "chat messages insert own conversation" on public.chat_messages
for insert to authenticated
with check (
    (select auth.uid()) = user_id
    and exists (
        select 1 from public.chat_conversations c
        where c.id = chat_messages.conversation_id and c.user_id = (select auth.uid())
    )
);

create policy "chat messages delete own" on public.chat_messages
for delete to authenticated
using ((select auth.uid()) = user_id);

-- 이용자당 저장 대화 수 상한(비용·남용 방지). 값은 운영자 결정.
create function public.chat_conversations_limit()
returns trigger language plpgsql security invoker set search_path = '' as $$
begin
    if (select count(*) from public.chat_conversations where user_id = new.user_id) >= 50 then
        raise exception 'chat conversation limit reached' using errcode = 'P0001';
    end if;
    return new;
end $$;

create trigger chat_conversations_limit before insert on public.chat_conversations
for each row execute function public.chat_conversations_limit();

-- 보관 기간이 지난 대화 삭제(메시지는 cascade). 서비스 역할로만 실행한다.
create function public.purge_expired_chat_conversations()
returns integer language sql security definer set search_path = '' as $$
    with gone as (delete from public.chat_conversations where expires_at < now() returning 1)
    select count(*)::integer from gone
$$;
revoke all on function public.purge_expired_chat_conversations() from public, anon, authenticated;
