-- Unapplied. Replace read-then-upsert conflict check with one atomic DB operation.
begin;
create function public.save_case_draft_checked(p_case_id text,p_text text,p_expected timestamptz,p_check boolean)
returns setof public.case_drafts language plpgsql security invoker set search_path='' as $$
declare uid uuid := auth.uid();
begin
 if uid is null then raise exception 'login required'; end if;
 if p_check is null then raise exception 'expected-version check required'; end if;
 if p_check and p_expected is null then
   return query insert into public.case_drafts(user_id,case_id,draft_text) values(uid,p_case_id,p_text)
   on conflict(user_id,case_id) do nothing returning *;
 elsif p_check then
   return query update public.case_drafts set draft_text=p_text
   where user_id=uid and case_id=p_case_id and updated_at=p_expected returning *;
 else
   -- Explicit overwrite chosen by the user, or legacy standalone save screen.
   return query insert into public.case_drafts(user_id,case_id,draft_text) values(uid,p_case_id,p_text)
   on conflict(user_id,case_id) do update set draft_text=excluded.draft_text returning *;
 end if;
end $$;
revoke all on function public.save_case_draft_checked(text,text,timestamptz,boolean) from public,anon;
grant execute on function public.save_case_draft_checked(text,text,timestamptz,boolean) to authenticated;
commit;
