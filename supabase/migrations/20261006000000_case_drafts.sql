-- 고소장 초안 저장
-- - 사건(public.cases)마다 사용자가 화면에서 고친 고소장 초안 1개를 저장한다.
-- - 기존 cases 테이블의 열·정책·권한은 바꾸지 않는다. cases에는 "사건이 삭제되면 초안도 함께 삭제"하는
--   트리거 하나만 추가한다.
-- - 본인 사건에 대해서만 초안을 만들고 고칠 수 있다(auth.uid() 기준). 비로그인(anon)은 접근할 수 없다.
-- - 회원탈퇴(auth.users 삭제) 시 외래키 on delete cascade로 초안도 삭제된다. 배포된 delete-account
--   Edge Function은 cases를 먼저 지우므로 트리거로도 한 번 더 삭제된다. Edge Function은 바꾸지 않는다.
-- - 되돌리기: supabase/rollbacks/20261006000000_case_drafts_rollback.sql

create table public.case_drafts (
    user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
    -- cases.case_id의 형식(uuid/text)과 관계없이 비교할 수 있도록 text로 저장한다.
    case_id text not null check (char_length(case_id) between 1 and 64),
    draft_text text not null check (char_length(draft_text) between 1 and 20000),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (user_id, case_id)
);

comment on table public.case_drafts is '사용자가 저장한 고소장 초안. 사건당 1개, 본인만 접근.';

alter table public.case_drafts enable row level security;
alter table public.case_drafts force row level security;

revoke all on table public.case_drafts from public, anon, authenticated;
grant select, insert, update, delete on table public.case_drafts to authenticated;

create policy "case drafts select own"
on public.case_drafts
for select
to authenticated
using ((select auth.uid()) = user_id);

-- 본인 이름으로, 본인이 저장한 사건에 대해서만 만들 수 있다.
create policy "case drafts insert own case"
on public.case_drafts
for insert
to authenticated
with check (
    (select auth.uid()) = user_id
    and exists (
        select 1 from public.cases c
        where c.case_id::text = case_drafts.case_id
          and c.user_id = (select auth.uid())
    )
);

create policy "case drafts update own case"
on public.case_drafts
for update
to authenticated
using ((select auth.uid()) = user_id)
with check (
    (select auth.uid()) = user_id
    and exists (
        select 1 from public.cases c
        where c.case_id::text = case_drafts.case_id
          and c.user_id = (select auth.uid())
    )
);

create policy "case drafts delete own"
on public.case_drafts
for delete
to authenticated
using ((select auth.uid()) = user_id);


-- 고칠 때마다 updated_at을 서버 시각으로 기록한다.
create or replace function public.case_drafts_touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at := now();
    new.created_at := old.created_at;
    return new;
end;
$$;

create trigger case_drafts_touch_updated_at
before update on public.case_drafts
for each row execute function public.case_drafts_touch_updated_at();


-- 사건이 삭제되면 그 사건의 초안도 삭제한다. 삭제한 사람의 권한(RLS)으로 실행된다.
create or replace function public.delete_case_draft_with_case()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    delete from public.case_drafts
    where user_id = old.user_id::uuid
      and case_id = old.case_id::text;
    return old;
end;
$$;

create trigger cases_delete_case_draft
after delete on public.cases
for each row execute function public.delete_case_draft_with_case();

revoke execute on function public.case_drafts_touch_updated_at() from public, anon, authenticated;
revoke execute on function public.delete_case_draft_with_case() from public, anon, authenticated;
