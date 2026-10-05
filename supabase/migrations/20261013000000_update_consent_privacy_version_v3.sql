-- 개인정보처리방침 2026-10-13 개정(고소장 초안 저장, 동의·연령 확인 기록, 파기, 브라우저 저장소,
-- 국외 이전 항목 보완)에 맞춰 동의 기록 함수가 새 버전을 받도록 한다.
-- - 기존 동의 기록은 그대로 둔다.
-- - 이전 버전(2026-09-27)도 계속 받아 두어, 앱 설정을 바꾸는 시점과 이 SQL을 실행하는 시점이 달라도
--   동의 저장이 실패하지 않게 한다. 앱은 설정된 시행일 버전의 동의가 있어야 이용을 허용한다.
-- - 시행일을 다른 날로 정하면 아래 '2026-10-13'과 앱의 PRIVACY_POLICY_EFFECTIVE_DATE를 같은 값으로 바꾼다.
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
    v_current_terms_version constant text := '2026-09-15';
begin
    if auth.uid() is null then
        raise exception 'record_consent: unauthenticated';
    end if;

    if p_terms_version <> v_current_terms_version
       or p_privacy_version not in ('2026-09-27', '2026-10-13') then
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
