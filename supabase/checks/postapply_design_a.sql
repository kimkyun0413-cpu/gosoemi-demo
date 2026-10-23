-- A안 운영 반영 적용 직후 확인 (읽기 전용). 구조·권한·개수만 조회한다. ok 열이 모두 true여야 한다.
-- 15번(후기 검토 운영자 1명 이상)은 운영자 지정 SQL을 실행한 뒤에 true가 된다.

with checks as (
    select 1 as no, 'case_draft_archive 표 있음, RLS 사용·강제' as item,
           coalesce((select (relrowsecurity and relforcerowsecurity)::text from pg_class where oid = to_regclass('public.case_draft_archive')), 'false') as actual,
           'true' as expected
    union all
    select 2, 'case_draft_archive 정책 4개', (select count(*)::text from pg_policies where schemaname = 'public' and tablename = 'case_draft_archive'), '4'
    union all
    select 3, 'case_draft_archive: 이용자가 고칠 수 있는 열은 draft_text 1개',
           (select string_agg(distinct column_name, ',') from information_schema.column_privileges
            where table_schema = 'public' and table_name = 'case_draft_archive' and grantee = 'authenticated' and privilege_type = 'UPDATE'),
           'draft_text'
    union all
    select 4, 'case_draft_archive anon 권한 없음',
           (select (count(*) = 0)::text from information_schema.role_table_grants
            where table_schema = 'public' and table_name = 'case_draft_archive' and grantee = 'anon'), 'true'
    union all
    select 5, '사건 삭제 트리거 3개(초안·작업 공간·이전 초안)',
           (select count(*)::text from pg_trigger
            where tgname in ('cases_delete_case_draft', 'cases_delete_case_workspace', 'cases_delete_case_draft_archive')), '3'
    union all
    select 6, 'service_reviews·review_admins RLS 사용·강제',
           (select (bool_and(relrowsecurity and relforcerowsecurity))::text from pg_class
            where oid in (to_regclass('public.service_reviews'), to_regclass('public.review_admins'))), 'true'
    union all
    select 7, 'service_reviews 정책 4개', (select count(*)::text from pg_policies where schemaname = 'public' and tablename = 'service_reviews'), '4'
    union all
    select 8, 'service_reviews: 이용자는 상태·게시 시각을 못 고침(고칠 수 있는 열 4개)',
           (select string_agg(distinct column_name, ',' order by column_name) from information_schema.column_privileges
            where table_schema = 'public' and table_name = 'service_reviews' and grantee = 'authenticated' and privilege_type = 'UPDATE'),
           'helpful,improve,rating,visibility'
    union all
    select 9, 'service_reviews·review_admins anon 권한 없음',
           (select (count(*) = 0)::text from information_schema.role_table_grants
            where table_schema = 'public' and table_name in ('service_reviews', 'review_admins') and grantee = 'anon'), 'true'
    union all
    select 10, 'review_admins는 이용자가 읽지도 못함',
           (select (count(*) = 0)::text from information_schema.role_table_grants
            where table_schema = 'public' and table_name = 'review_admins' and grantee = 'authenticated'), 'true'
    union all
    select 11, '후기 트리거 있음', (select (count(*) = 1)::text from pg_trigger where tgname = 'service_reviews_owner_write'), 'true'
    union all
    select 12, '후기 함수 3개는 security definer',
           (select count(*)::text from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.prosecdef and p.proname in ('published_reviews', 'reviews_for_moderation', 'moderate_review')), '3'
    union all
    select 13, '후기 함수: 비로그인(anon)은 실행 못 함',
           (select (not bool_or(has_function_privilege('anon', p.oid, 'execute')))::text from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('published_reviews', 'reviews_for_moderation', 'moderate_review')), 'true'
    union all
    select 14, '내부 함수(트리거용)는 이용자가 직접 실행 못 함',
           (select (not bool_or(has_function_privilege('authenticated', p.oid, 'execute')))::text from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('case_draft_archive_touch', 'delete_case_draft_archive_with_case', 'service_reviews_owner_write')), 'true'
    union all
    select 15, '후기 검토 운영자 1명 이상', (select (count(*) >= 1)::text from public.review_admins), 'true'
)
select no, item, actual, expected, actual = expected as ok from checks order by no;

-- 개수(적용 전과 같아야 한다). 새 표는 0이어야 한다.
select (select count(*) from public.cases) as cases,
       (select count(*) from public.case_drafts) as drafts,
       (select count(*) from public.case_workspace) as workspaces,
       (select count(*) from auth.users) as users,
       (select count(*) from public.case_draft_archive) as archives_new,
       (select count(*) from public.service_reviews) as reviews_new;
