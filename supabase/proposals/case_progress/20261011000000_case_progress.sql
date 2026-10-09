-- [제안, 운영 미적용] 사건 진행 체크리스트 저장
-- - 이용자가 직접 표시하는 준비 기록(경위 정리·증거 준비·초안·검토·제출 여부·제출한 곳·제출일·다음 할 일 메모).
--   경찰·법원의 실제 진행 상황이 아니다.
-- - 사건(public.cases)마다 1개. 본인 사건에만 만들고 고칠 수 있다(auth.uid() 기준). 비로그인(anon)은 접근할 수 없다.
-- - 회원탈퇴(auth.users 삭제) 시 외래키 on delete cascade로 삭제. 사건을 지우면 트리거로 함께 삭제.
-- - 기존 cases 테이블의 열·정책·권한은 바꾸지 않는다(트리거 하나만 추가).
-- - 되돌리기: 20261011000000_case_progress_rollback.sql

create table public.case_progress (
    user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
    case_id text not null check (char_length(case_id) between 1 and 64),
    items jsonb not null default '{}'::jsonb check (jsonb_typeof(items) = 'object' and pg_column_size(items) <= 512),
    submitted_to text not null default '' check (char_length(submitted_to) <= 60),
    submitted_on date,
    next_note text not null default '' check (char_length(next_note) <= 300),
    updated_at timestamptz not null default now(),
    primary key (user_id, case_id)
);

comment on table public.case_progress is '[제안] 이용자가 직접 표시한 사건 준비 기록. 사건당 1개, 본인만 접근.';

alter table public.case_progress enable row level security;
alter table public.case_progress force row level security;

revoke all on table public.case_progress from public, anon, authenticated;
grant select, insert, update, delete on table public.case_progress to authenticated;

create policy "case progress select own" on public.case_progress
for select to authenticated
using ((select auth.uid()) = user_id);

create policy "case progress insert own case" on public.case_progress
for insert to authenticated
with check (
    (select auth.uid()) = user_id
    and exists (select 1 from public.cases c where c.case_id::text = case_progress.case_id and c.user_id = (select auth.uid()))
);

create policy "case progress update own case" on public.case_progress
for update to authenticated
using ((select auth.uid()) = user_id)
with check (
    (select auth.uid()) = user_id
    and exists (select 1 from public.cases c where c.case_id::text = case_progress.case_id and c.user_id = (select auth.uid()))
);

create policy "case progress delete own" on public.case_progress
for delete to authenticated
using ((select auth.uid()) = user_id);

create function public.case_progress_touch_updated_at()
returns trigger language plpgsql set search_path = '' as $$
begin
    new.updated_at := now();
    return new;
end $$;

create trigger case_progress_touch before update on public.case_progress
for each row execute function public.case_progress_touch_updated_at();

-- 사건을 지우면 그 사건의 체크리스트도 지운다.
create function public.delete_case_progress_with_case()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
    delete from public.case_progress where user_id = old.user_id and case_id = old.case_id::text;
    return old;
end $$;
revoke all on function public.delete_case_progress_with_case() from public, anon, authenticated;

create trigger cases_delete_case_progress after delete on public.cases
for each row execute function public.delete_case_progress_with_case();
