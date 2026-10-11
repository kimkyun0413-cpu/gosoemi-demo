-- [수정안 · 운영 미적용] 이용 연령 만 18세 기준 약관(새 버전)에 맞춰 동의 기록 함수가 새 약관 버전을 받도록 한다.
-- 왜: Gemini API 이용 조건(18세 이상, 18세 미만이 이용할 서비스에서 사용 금지). docs/decisions-2026-10-12.md 결정 B.
-- 적용 전 할 일: 아래 두 값을 운영자가 정한 날짜로 바꾼다.
--   __TERMS_V2_DATE__   : 새 약관 시행일 = 앱 설정 TERMS_EFFECTIVE_DATE_V2 (예: 2026-11-01)
--   __PRIVACY_V3_DATE__ : 만 18세 기준을 담은 방침(v3) 시행일 = 앱 설정 PRIVACY_POLICY_EFFECTIVE_DATE
-- - 기존 동의 기록은 지우지 않는다(누가 언제 어떤 버전에 동의했는지 남긴다).
-- - 새 약관 버전만 받는다: 앱은 새 버전 동의(만 18세 이상 확인 포함)가 있어야 이용을 허용하므로,
--   옛 버전 동의를 새로 넣어도 이용할 수 없다. 앱 설정(MIN_USER_AGE=18)과 이 SQL은 같은 때에 적용한다.
-- 되돌리기: supabase/proposals/age18/record_consent_terms_v2_rollback.sql
create or replace function public.record_consent(
    p_terms_version text,
    p_privacy_version text
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_current_terms_version constant text := '__TERMS_V2_DATE__';
begin
    if auth.uid() is null then
        raise exception 'record_consent: unauthenticated';
    end if;

    if p_terms_version <> v_current_terms_version
       or p_privacy_version <> '__PRIVACY_V3_DATE__' then
        raise exception 'record_consent: version mismatch';
    end if;

    insert into public.consent_records (
        user_id,
        terms_version,
        privacy_version
    )
    values (
        auth.uid(),
        p_terms_version,
        p_privacy_version
    )
    on conflict (user_id, terms_version, privacy_version) do nothing;
end;
$$;

revoke execute on function public.record_consent(text, text) from public, anon;
grant execute on function public.record_consent(text, text) to authenticated;
