-- 이전 초안 보관 (A안: '새로 만들기' 전에 지금 초안을 보관하고, 새로고침·재로그인 뒤에도 되돌릴 수 있게)
-- 운영 미적용. 운영자 승인 후 Supabase SQL Editor에서 begin/commit으로 실행한다. 적용 전에는 앱의 DRAFT_ARCHIVE_ENABLED를 켜지 않는다.
-- - 사건마다 '이전 초안' 1개. 새로 만들기 직전 화면의 초안(직접 고친 내용 포함, 아직 저장하지 않은 것 포함)을 넣는다.
--   되돌리면 지금 초안과 서로 바꾸므로(앱) 어느 쪽도 사라지지 않는다.
-- - 기존 public.case_drafts·cases의 열·정책·권한은 바꾸지 않는다. cases에는 "사건이 삭제되면 보관 초안도 삭제"하는 트리거 하나만 더한다.
-- - 본인 사건에 대해서만 만들고 고칠 수 있다(auth.uid() 기준). 비로그인(anon) 접근 불가.
-- - 회원탈퇴(auth.users 삭제) 시 외래키 on delete cascade로 삭제. delete-account Edge Function은 cases를 먼저 지우므로 트리거로도 삭제된다.
-- - 시험: supabase/tests/local_case_draft_archive_test.py (로컬 임시 Postgres)
-- - 되돌리기: supabase/rollbacks/20261023000000_case_draft_archive_rollback.sql

create table public.case_draft_archive (
    user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
    case_id text not null check (char_length(case_id) between 1 and 64),
    draft_text text not null check (char_length(draft_text) between 1 and 20000),
    archived_at timestamptz not null default now(),
    primary key (user_id, case_id)
);

comment on table public.case_draft_archive is '고소장 초안을 새로 만들기 전에 보관한 이전 초안. 사건당 1개, 본인만 접근.';

alter table public.case_draft_archive enable row level security;
alter table public.case_draft_archive force row level security;

revoke all on table public.case_draft_archive from public, anon, authenticated;
grant select, insert, delete on table public.case_draft_archive to authenticated;
grant update (draft_text) on table public.case_draft_archive to authenticated;

create policy "case draft archive select own"
on public.case_draft_archive for select to authenticated
using ((select auth.uid()) = user_id);

create policy "case draft archive insert own case"
on public.case_draft_archive for insert to authenticated
with check (
    (select auth.uid()) = user_id
    and exists (
        select 1 from public.cases c
        where c.case_id::text = case_draft_archive.case_id and c.user_id = (select auth.uid())
    )
);

create policy "case draft archive update own case"
on public.case_draft_archive for update to authenticated
using ((select auth.uid()) = user_id)
with check (
    (select auth.uid()) = user_id
    and exists (
        select 1 from public.cases c
        where c.case_id::text = case_draft_archive.case_id and c.user_id = (select auth.uid())
    )
);

create policy "case draft archive delete own"
on public.case_draft_archive for delete to authenticated
using ((select auth.uid()) = user_id);

-- 고칠 때마다 보관 시각을 서버 시각으로 기록한다(이용자가 시각을 정하지 못한다).
create function public.case_draft_archive_touch()
returns trigger language plpgsql set search_path = '' as $$
begin
    new.archived_at := now();
    return new;
end $$;

create trigger case_draft_archive_touch before update on public.case_draft_archive
for each row execute function public.case_draft_archive_touch();

-- 사건이 삭제되면 그 사건의 보관 초안도 삭제한다. 삭제한 사람의 권한(RLS)으로 실행된다.
create function public.delete_case_draft_archive_with_case()
returns trigger language plpgsql set search_path = '' as $$
begin
    delete from public.case_draft_archive
    where user_id = old.user_id::uuid and case_id = old.case_id::text;
    return old;
end $$;

create trigger cases_delete_case_draft_archive after delete on public.cases
for each row execute function public.delete_case_draft_archive_with_case();

revoke execute on function public.case_draft_archive_touch() from public, anon, authenticated;
revoke execute on function public.delete_case_draft_archive_with_case() from public, anon, authenticated;
