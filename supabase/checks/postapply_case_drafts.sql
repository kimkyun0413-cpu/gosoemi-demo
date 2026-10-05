-- case_drafts 마이그레이션 적용 직후 확인 (읽기 전용)
-- 구조와 개수만 조회한다. ok 열이 모두 true여야 한다.

with checks as (
    select 1 as no, 'case_drafts 테이블 있음' as item,
           (to_regclass('public.case_drafts') is not null)::text as actual, 'true' as expected
    union all
    select 2, 'RLS 사용·강제',
           (select (relrowsecurity and relforcerowsecurity)::text from pg_class where oid = to_regclass('public.case_drafts')),
           'true'
    union all
    select 3, '정책 4개 (select/insert/update/delete, authenticated)',
           (select count(*)::text from pg_policies
            where schemaname = 'public' and tablename = 'case_drafts' and roles = '{authenticated}'),
           '4'
    union all
    select 4, 'anon 권한 없음',
           (select (count(*) = 0)::text from information_schema.role_table_grants
            where table_schema = 'public' and table_name = 'case_drafts' and grantee = 'anon'),
           'true'
    union all
    select 5, 'authenticated 권한 (DELETE, INSERT, SELECT, UPDATE)',
           (select string_agg(privilege_type, ', ' order by privilege_type) from information_schema.role_table_grants
            where table_schema = 'public' and table_name = 'case_drafts' and grantee = 'authenticated'),
           'DELETE, INSERT, SELECT, UPDATE'
    union all
    select 6, 'cases 삭제 트리거',
           (select count(*)::text from pg_trigger
            where tgrelid = 'public.cases'::regclass and tgname = 'cases_delete_case_draft' and not tgisinternal),
           '1'
    union all
    select 7, 'updated_at 트리거',
           (select count(*)::text from pg_trigger
            where tgrelid = to_regclass('public.case_drafts') and tgname = 'case_drafts_touch_updated_at'),
           '1'
    union all
    select 8, 'auth.users 외래키 on delete cascade',
           (select count(*)::text from pg_constraint
            where conrelid = to_regclass('public.case_drafts') and contype = 'f' and confdeltype = 'c'),
           '1'
    union all
    select 9, '저장된 초안 수 (적용 직후)',
           (select count(*)::text from public.case_drafts),
           '0'
)
select no, item, actual, expected, actual = expected as ok
from checks
order by no;
