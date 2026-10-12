-- [수정안 · 운영 미적용] 만 18세 기준 전환용 동의 기록 함수: 옛 약관과 새 약관 버전을 모두 받는다.
-- 왜: record_consent_terms_v2.sql(새 버전만 받음)을 앱 설정보다 먼저/나중에 적용하면 그 사이 몇 분 동안 동의 저장이 실패한다.
--     이 함수를 먼저 적용해 두면 앱 설정(MIN_USER_AGE=18 등)을 바꾸기 전·후 어느 쪽에서도 동의 저장이 실패하지 않는다.
-- 안전한 이유: 만 18세 설정의 앱은 새 약관 버전 동의가 있어야만 이용을 허용한다. 옛 버전 동의를 새로 기록해도 이용할 수 없다.
-- 적용 전 할 일: 아래 두 값을 운영자가 정한 날짜로 바꾼다(record_consent_terms_v2.sql과 같은 값).
--   __TERMS_V2_DATE__   : 새 약관 시행일 = 앱 설정 TERMS_EFFECTIVE_DATE_V2
--   __PRIVACY_V3_DATE__ : 만 18세 기준을 담은 방침(v3) 시행일 = 앱 설정 PRIVACY_POLICY_EFFECTIVE_DATE
-- - 기존 동의 기록은 지우지 않는다. 받는 버전 외의 값은 거부한다. 비로그인은 거부한다.
-- 다음 단계(선택): 시행 확인 후 record_consent_terms_v2.sql로 새 버전만 받게 좁힌다.
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
begin
    if auth.uid() is null then
        raise exception 'record_consent: unauthenticated';
    end if;

    if not (
        (p_terms_version = '2026-09-15' and p_privacy_version in ('2026-09-27', '__PRIVACY_V3_DATE__'))
        or (p_terms_version = '__TERMS_V2_DATE__' and p_privacy_version = '__PRIVACY_V3_DATE__')
    ) then
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
