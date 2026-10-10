-- 20261012000000_case_workspace_and_case_edit 적용 전 사전 점검 (읽기 전용)
-- 조회만 하며 어떤 행도 만들거나 바꾸지 않는다. 사용자 자료의 내용은 읽지 않고 구조와 개수만 본다.
-- ok 열이 모두 true여야 변경안을 그대로 적용할 수 있다.

with content_cols(col) as (
    values ('case_type'), ('reporter_info'), ('suspect_info'), ('incident_datetime'), ('incident_location'),
           ('incident_description'), ('damage_details'), ('evidence'), ('witness'), ('punishment_intent')
), checks as (
    select 1 as no, 'cases.user_id 형식' as item,
           coalesce((select data_type from information_schema.columns
                     where table_schema = 'public' and table_name = 'cases' and column_name = 'user_id'), '없음') as actual,
           'uuid' as expected
    union all
    select 2, 'cases.case_id 형식',
           coalesce((select data_type from information_schema.columns
                     where table_schema = 'public' and table_name = 'cases' and column_name = 'case_id'), '없음'),
           'uuid 또는 text'
    union all
    select 3, '사건 내용 칸 10개 모두 있음',
           (select count(*)::text from information_schema.columns c join content_cols k on c.column_name = k.col
            where c.table_schema = 'public' and c.table_name = 'cases'),
           '10'
    union all
    select 4, 'cases에 edit_version·updated_at 없음(새로 추가)',
           (select count(*)::text from information_schema.columns
            where table_schema = 'public' and table_name = 'cases' and column_name in ('edit_version', 'updated_at')),
           '0'
    union all
    select 5, 'cases RLS 사용·강제',
           (select (relrowsecurity and relforcerowsecurity)::text from pg_class where oid = to_regclass('public.cases')),
           'true'
    union all
    -- 표 전체 update 권한이 이미 있으면 열 단위 제한(사건 번호·소유자 고치기 금지)이 효과가 없다
    select 6, 'authenticated·anon에 cases 표 전체 UPDATE 권한 없음',
           (select (count(*) = 0)::text from information_schema.role_table_grants
            where table_schema = 'public' and table_name = 'cases' and grantee in ('authenticated', 'anon')
              and privilege_type = 'UPDATE'),
           'true'
    union all
    select 7, 'cases에 UPDATE 정책 없음(새로 추가)',
           (select (count(*) = 0)::text from pg_policies
            where schemaname = 'public' and tablename = 'cases' and cmd in ('UPDATE', 'ALL')),
           'true'
    union all
    select 8, 'case_workspace 표 없음(새로 만듦)', (to_regclass('public.case_workspace') is null)::text, 'true'
    union all
    select 9, '같은 이름 트리거 없음',
           (select (count(*) = 0)::text from pg_trigger
            where tgname in ('cases_touch_edit', 'cases_delete_case_workspace', 'case_workspace_touch')),
           'true'
    union all
    select 10, '같은 이름 함수 없음',
           (select (count(*) = 0)::text from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public'
              and p.proname in ('cases_touch_edit', 'case_workspace_touch', 'delete_case_workspace_with_case')),
           'true'
    union all
    select 11, '기존 초안 삭제 트리거 있음(유지)',
           (select (count(*) = 1)::text from pg_trigger where tgname = 'cases_delete_case_draft'),
           'true'
    union all
    select 12, 'cases에 BEFORE UPDATE 트리거 없음(겹침 확인)',
           (select (count(*) = 0)::text from pg_trigger t
            where t.tgrelid = to_regclass('public.cases') and not t.tgisinternal
              and (t.tgtype & 2) = 2 and (t.tgtype & 16) = 16),
           'true'
)
select no, item, actual, expected,
       case when expected = 'uuid 또는 text' then actual in ('uuid', 'text') else actual = expected end as ok
from checks order by no;

-- 참고(개수만): 지금 사건 수와 초안 수. 적용 뒤에도 같아야 한다.
-- select (select count(*) from public.cases) as cases, (select count(*) from public.case_drafts) as drafts;
-- 참고: cases의 현재 정책·권한
-- select policyname, cmd, roles from pg_policies where schemaname='public' and tablename='cases';
-- select grantee, privilege_type from information_schema.role_table_grants where table_schema='public' and table_name='cases';
