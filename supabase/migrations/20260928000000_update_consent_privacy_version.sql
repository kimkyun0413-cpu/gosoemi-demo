-- Align the authenticated consent RPC with the policy versions used by app.py.
-- Existing consent rows are preserved.
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
    v_current_privacy_version constant text := '2026-09-27';
begin
    if auth.uid() is null then
        raise exception 'record_consent: unauthenticated';
    end if;

    if p_terms_version <> v_current_terms_version
       or p_privacy_version <> v_current_privacy_version then
        raise exception 'record_consent: version mismatch';
    end if;

    insert into public.consent_records (
        user_id,
        terms_version,
        privacy_version
    )
    values (
        auth.uid(),
        v_current_terms_version,
        v_current_privacy_version
    )
    on conflict (user_id, terms_version, privacy_version) do nothing;
end;
$$;

revoke execute on function public.record_consent(text, text) from public, anon;
grant execute on function public.record_consent(text, text) to authenticated;
