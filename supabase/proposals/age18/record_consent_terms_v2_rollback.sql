-- [수정안 되돌리기] 만 18세 기준 전의 동의 기록 함수(약관 2026-09-15, 방침 2026-09-27·2026-10-13)로 되돌린다.
-- 20261013000000_update_consent_privacy_version_v3.sql과 같은 내용. 동의 기록은 지우지 않는다.
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
