-- 네이버 간편로그인
-- - OAuth state(해시만 저장)와 네이버 계정 ↔ Supabase 사용자 연결을 naver_private 스키마에 보관한다.
-- - naver_private 스키마는 Data API에 노출하지 않으며, 모든 접근은 아래 SECURITY DEFINER 함수로만 한다.
-- - 기존 cases / consent_records / record_consent / auth 트리거는 변경하지 않는다.

create schema if not exists naver_private;
revoke all on schema naver_private from public, anon, authenticated;

create table naver_private.naver_oauth_states (
    state_hash text primary key check (state_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    used_at timestamptz
);

alter table naver_private.naver_oauth_states enable row level security;
revoke all on table naver_private.naver_oauth_states from public, anon, authenticated;

create table naver_private.naver_identities (
    naver_id text primary key check (length(naver_id) between 1 and 128),
    user_id uuid not null unique references auth.users (id) on delete cascade,
    created_at timestamptz not null default now(),
    age_confirmed_at timestamptz
);

alter table naver_private.naver_identities enable row level security;
revoke all on table naver_private.naver_identities from public, anon, authenticated;


-- ── Edge Function(service_role) 전용 ─────────────────────────────

create or replace function public.naver_create_oauth_state(p_state_hash text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
    delete from naver_private.naver_oauth_states
    where expires_at < now() - interval '1 day';

    insert into naver_private.naver_oauth_states (state_hash, expires_at)
    values (p_state_hash, now() + interval '10 minutes');
end;
$$;

-- 만료되지 않았고 아직 사용되지 않은 state만 1회 소비한다.
-- 동시 요청은 행 잠금 후 used_at 조건을 다시 평가하므로 하나만 true를 받는다.
create or replace function public.naver_consume_oauth_state(p_state_hash text)
returns boolean
language sql
security definer
set search_path = ''
as $$
    with consumed as (
        update naver_private.naver_oauth_states
        set used_at = now()
        where state_hash = p_state_hash
          and used_at is null
          and expires_at > now()
        returning 1
    )
    select exists (select 1 from consumed);
$$;

create or replace function public.naver_get_user_id(p_naver_id text)
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
    select user_id from naver_private.naver_identities where naver_id = p_naver_id;
$$;

create or replace function public.naver_link_identity(p_naver_id text, p_user_id uuid)
returns void
language sql
security definer
set search_path = ''
as $$
    insert into naver_private.naver_identities (naver_id, user_id)
    values (p_naver_id, p_user_id);
$$;


-- ── 로그인 사용자(authenticated) 본인 전용: auth.uid() 기준 ─────────

create or replace function public.naver_age_status()
returns table (is_naver boolean, age_confirmed boolean)
language sql
stable
security definer
set search_path = ''
as $$
    select
        exists (select 1 from naver_private.naver_identities where user_id = auth.uid()),
        exists (
            select 1 from naver_private.naver_identities
            where user_id = auth.uid() and age_confirmed_at is not null
        );
$$;

create or replace function public.naver_confirm_age()
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_user_id uuid := auth.uid();
begin
    if v_user_id is null then
        raise exception 'not authenticated' using errcode = '42501';
    end if;

    update naver_private.naver_identities
    set age_confirmed_at = coalesce(age_confirmed_at, now())
    where user_id = v_user_id;

    return found;
end;
$$;


-- ── 실행 권한 ──────────────────────────────────────────────────
-- Supabase 기본 권한(anon/authenticated에 EXECUTE)을 명시적으로 회수한다.

revoke execute on function public.naver_create_oauth_state(text) from public, anon, authenticated;
revoke execute on function public.naver_consume_oauth_state(text) from public, anon, authenticated;
revoke execute on function public.naver_get_user_id(text) from public, anon, authenticated;
revoke execute on function public.naver_link_identity(text, uuid) from public, anon, authenticated;
revoke execute on function public.naver_age_status() from public, anon;
revoke execute on function public.naver_confirm_age() from public, anon;

grant execute on function public.naver_create_oauth_state(text) to service_role;
grant execute on function public.naver_consume_oauth_state(text) to service_role;
grant execute on function public.naver_get_user_id(text) to service_role;
grant execute on function public.naver_link_identity(text, uuid) to service_role;
grant execute on function public.naver_age_status() to authenticated;
grant execute on function public.naver_confirm_age() to authenticated;
