-- A안 운영 반영 SQL: 이전 초안 보관 + 이용 후기. Supabase Dashboard > SQL Editor에 전체를 붙여 넣고 Run.
-- 먼저 supabase/checks/preflight_design_a.sql 을 실행해 ok가 모두 true인지 확인한다.
-- 한 덩어리(begin … commit)로 실행되므로 중간에 오류가 나면 아무것도 바뀌지 않는다.
-- 이 파일은 아래 두 마이그레이션을 그대로 이어 붙인 것이다(tests/test_release_sql.py가 같은지 검사).
--   supabase/migrations/20261023000000_case_draft_archive.sql
--   supabase/migrations/20261023000001_service_reviews.sql
-- 기존 표(cases·case_drafts·case_workspace·consent_records)의 열·행은 바꾸지 않는다. 새 표·함수·트리거만 더한다.

begin;

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

-- 이용 후기 (A안 2026-10-23 확정)
-- 운영 미적용. 운영자 승인 후 Supabase SQL Editor에서 begin/commit으로 실행한다. 적용 전에는 앱의 REVIEWS_ENABLED를 켜지 않는다.
-- 새 개인정보(후기 글)를 받으므로 이를 적은 개인정보처리방침(v3) 시행 전에는 앱이 관리자에게만 연다.
-- - 이용자: 별점(1~5), 도움이 된 점, 개선할 점(각 500자), 공개 방식(operator: 운영자에게만 / public: 익명 공개)을 적는다.
--   본인 후기만 보고·고치고·지운다. 상태(status)·게시 시각·반려 이유는 이용자가 정하지 못한다(트리거가 정함).
--   익명 공개를 고르면 '확인 대기(pending)', 운영자에게만이면 'private'. 이미 게시된 후기를 고치면 다시 '확인 대기'.
-- - 공개 목록: published_reviews() 함수로 '게시됨'만, 작성자 정보(user_id) 없이 별점·글·게시 시각만 돌려준다.
-- - 운영자: review_admins 표에 있는 계정만 reviews_for_moderation()·moderate_review()를 쓸 수 있다(작성자 정보는 주지 않음).
--   review_admins는 운영자가 SQL Editor에서 직접 넣는다(앱·이용자는 읽거나 고칠 수 없음).
-- - 회원탈퇴(auth.users 삭제) 시 외래키 on delete cascade로 삭제. 계정마다 최대 20개.
-- - 시험: supabase/tests/local_service_reviews_test.py (로컬 임시 Postgres)
-- - 되돌리기: supabase/rollbacks/20261023000001_service_reviews_rollback.sql

-- 공개 목록·운영자 함수는 security definer로 표를 읽는다. 표에 force row level security를 걸었으므로 함수 소유자(이 SQL을
-- 실행하는 역할)가 RLS를 건너뛸 수 있어야 한다(Supabase의 postgres 역할). 아니면 적용을 멈춘다.
do $$ begin
    if not exists (select 1 from pg_roles where rolname = current_user and (rolbypassrls or rolsuper)) then
        raise exception '이 SQL은 RLS를 건너뛸 수 있는 역할(Supabase SQL Editor의 postgres)로 실행해야 합니다. 지금 역할: %', current_user;
    end if;
end $$;

create table public.review_admins (
    user_id uuid primary key references auth.users (id) on delete cascade
);
comment on table public.review_admins is '후기 검토 권한이 있는 운영자 계정. SQL Editor에서만 관리.';
alter table public.review_admins enable row level security;
alter table public.review_admins force row level security;
revoke all on table public.review_admins from public, anon, authenticated;

create table public.service_reviews (
    review_id uuid primary key default gen_random_uuid(),
    user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
    rating smallint not null check (rating between 1 and 5),
    helpful text not null default '' check (char_length(helpful) <= 500),
    improve text not null default '' check (char_length(improve) <= 500),
    visibility text not null check (visibility in ('operator', 'public')),
    status text not null default 'pending' check (status in ('pending', 'published', 'rejected', 'private')),
    reject_reason text check (reject_reason is null or char_length(reject_reason) <= 100),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    published_at timestamptz
);
comment on table public.service_reviews is '이용 후기. 본인만 접근. 공개는 운영자 확인 뒤 published_reviews()로만.';
create index service_reviews_status_idx on public.service_reviews (status, published_at desc);

alter table public.service_reviews enable row level security;
alter table public.service_reviews force row level security;
revoke all on table public.service_reviews from public, anon, authenticated;
grant select, insert, delete on table public.service_reviews to authenticated;
grant update (rating, helpful, improve, visibility) on table public.service_reviews to authenticated;

create policy "service reviews select own" on public.service_reviews for select to authenticated
using ((select auth.uid()) = user_id);
create policy "service reviews insert own" on public.service_reviews for insert to authenticated
with check ((select auth.uid()) = user_id);
create policy "service reviews update own" on public.service_reviews for update to authenticated
using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "service reviews delete own" on public.service_reviews for delete to authenticated
using ((select auth.uid()) = user_id);

-- 이용자가 쓰거나 고칠 때: 상태는 공개 방식으로만 정하고(공개 → 확인 대기), 게시 시각·반려 이유는 비운다. 계정당 20개까지.
-- 운영자 함수(moderate_review)는 함수 소유자 권한으로 실행되므로 이 규칙을 거치지 않는다.
create function public.service_reviews_owner_write()
returns trigger language plpgsql set search_path = '' as $$
begin
    if current_user not in ('authenticated', 'anon') then
        return new;
    end if;
    if tg_op = 'INSERT' then
        if (select count(*) from public.service_reviews where user_id = new.user_id) >= 20 then
            raise exception '후기는 계정마다 20개까지 쓸 수 있어요.';
        end if;
        new.created_at := now();
    else
        new.user_id := old.user_id;
        new.created_at := old.created_at;
    end if;
    new.status := case when new.visibility = 'public' then 'pending' else 'private' end;
    new.published_at := null;
    new.reject_reason := null;
    new.updated_at := now();
    return new;
end $$;

create trigger service_reviews_owner_write before insert or update on public.service_reviews
for each row execute function public.service_reviews_owner_write();

-- 공개 목록: 게시된 후기만, 작성자 정보 없이.
create function public.published_reviews(p_limit integer default 20, p_offset integer default 0)
returns table (rating smallint, helpful text, improve text, published_at timestamptz)
language sql stable security definer set search_path = '' as $$
    select r.rating, r.helpful, r.improve, r.published_at
    from public.service_reviews r
    where r.status = 'published'
    order by r.published_at desc
    limit least(greatest(coalesce(p_limit, 20), 1), 50) offset greatest(coalesce(p_offset, 0), 0)
$$;

-- 운영자 검토 목록(작성자 정보 없음). 운영자가 아니면 빈 목록.
create function public.reviews_for_moderation(p_status text default 'pending')
returns table (review_id uuid, rating smallint, helpful text, improve text, visibility text, status text,
               reject_reason text, created_at timestamptz, updated_at timestamptz, published_at timestamptz)
language sql stable security definer set search_path = '' as $$
    select r.review_id, r.rating, r.helpful, r.improve, r.visibility, r.status, r.reject_reason,
           r.created_at, r.updated_at, r.published_at
    from public.service_reviews r
    where exists (select 1 from public.review_admins a where a.user_id = (select auth.uid()))
      and r.status = p_status
    order by r.updated_at desc
    limit 200
$$;

-- 운영자 게시/게시 안 함. 공개를 고른 후기(확인 대기·게시됨·반려)만 바꿀 수 있다. 바꾼 건수(0 또는 1)를 돌려준다.
create function public.moderate_review(p_review_id uuid, p_action text, p_reason text default null)
returns integer language plpgsql security definer set search_path = '' as $$
declare changed integer;
begin
    if not exists (select 1 from public.review_admins a where a.user_id = (select auth.uid())) then
        raise exception 'not allowed';
    end if;
    if p_action not in ('publish', 'reject') then
        raise exception 'unknown action';
    end if;
    update public.service_reviews
    set status = case when p_action = 'publish' then 'published' else 'rejected' end,
        published_at = case when p_action = 'publish' then now() else null end,
        reject_reason = case when p_action = 'reject' then left(nullif(p_reason, ''), 100) else null end,
        updated_at = now()
    where review_id = p_review_id and visibility = 'public';
    get diagnostics changed = row_count;
    return changed;
end $$;

revoke execute on function public.service_reviews_owner_write() from public, anon, authenticated;
revoke execute on function public.published_reviews(integer, integer) from public, anon;
revoke execute on function public.reviews_for_moderation(text) from public, anon;
revoke execute on function public.moderate_review(uuid, text, text) from public, anon;
grant execute on function public.published_reviews(integer, integer) to authenticated;
grant execute on function public.reviews_for_moderation(text) to authenticated;
grant execute on function public.moderate_review(uuid, text, text) to authenticated;

commit;
