-- 20261012000000_case_workspace_and_case_edit 적용 직후 확인 (읽기 전용)
-- 구조·권한·개수만 조회한다. ok 열이 모두 true여야 한다.

with checks as (
    select 1 as no, 'case_workspace 표 있음' as item, (to_regclass('public.case_workspace') is not null)::text as actual, 'true' as expected
    union all
    select 2, 'case_workspace RLS 사용·강제',
           (select (relrowsecurity and relforcerowsecurity)::text from pg_class where oid = to_regclass('public.case_workspace')), 'true'
    union all
    select 3, 'case_workspace 정책 4개(authenticated)',
           (select count(*)::text from pg_policies where schemaname = 'public' and tablename = 'case_workspace' and roles = '{authenticated}'), '4'
    union all
    select 4, 'case_workspace anon 권한 없음',
           (select (count(*) = 0)::text from information_schema.role_table_grants
            where table_schema = 'public' and table_name = 'case_workspace' and grantee = 'anon'), 'true'
    union all
    select 5, 'case_workspace 표 전체 UPDATE 권한 없음(열 단위만)',
           (select (count(*) = 0)::text from information_schema.role_table_grants
            where table_schema = 'public' and table_name = 'case_workspace' and grantee = 'authenticated' and privilege_type = 'UPDATE'), 'true'
    union all
    select 6, 'case_workspace 고칠 수 있는 열 4개',
           (select count(distinct column_name)::text from information_schema.column_privileges
            where table_schema = 'public' and table_name = 'case_workspace' and grantee = 'authenticated' and privilege_type = 'UPDATE'), '4'
    union all
    select 7, 'cases 고칠 수 있는 열 10개(내용 칸만)',
           (select count(distinct column_name)::text from information_schema.column_privileges
            where table_schema = 'public' and table_name = 'cases' and grantee = 'authenticated' and privilege_type = 'UPDATE'), '10'
    union all
    select 8, 'cases 사건 번호·소유자·작성 시각은 고칠 수 없음',
           (select (count(*) = 0)::text from information_schema.column_privileges
            where table_schema = 'public' and table_name = 'cases' and grantee = 'authenticated' and privilege_type = 'UPDATE'
              and column_name in ('case_id', 'user_id', 'created_at', 'edit_version', 'updated_at')), 'true'
    union all
    select 9, 'cases UPDATE 정책 1개(본인)',
           (select count(*)::text from pg_policies where schemaname = 'public' and tablename = 'cases' and cmd = 'UPDATE'), '1'
    union all
    select 10, '트리거 3개 있음',
           (select count(*)::text from pg_trigger where tgname in ('cases_touch_edit', 'cases_delete_case_workspace', 'case_workspace_touch')), '3'
    union all
    select 11, '기존 사건 edit_version이 모두 1',
           (select (count(*) filter (where edit_version <> 1) = 0)::text from public.cases), 'true'
    union all
    select 12, '기존 초안 삭제 트리거 유지',
           (select (count(*) = 1)::text from pg_trigger where tgname = 'cases_delete_case_draft'), 'true'
)
select no, item, actual, expected, actual = expected as ok from checks order by no;

-- 사전 점검 때 센 사건 수·초안 수와 같은지 확인
-- select (select count(*) from public.cases) as cases, (select count(*) from public.case_drafts) as drafts;
