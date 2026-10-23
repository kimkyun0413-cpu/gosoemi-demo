-- A안 운영 반영(20261023000000 이전 초안 보관 + 20261023000001 이용 후기) 적용 전 사전 점검 (읽기 전용)
-- 조회만 하며 어떤 행도 만들거나 바꾸지 않는다. 사용자 자료의 내용은 읽지 않고 구조와 개수만 본다.
-- ok 열이 모두 true여야 적용할 수 있다. 맨 아래 '개수' 결과는 적어 두었다가 적용 뒤와 비교한다.

with checks as (
    select 1 as no, '실행 역할이 RLS를 건너뛸 수 있음(후기 함수용)' as item,
           (select (rolbypassrls or rolsuper)::text from pg_roles where rolname = current_user) as actual, 'true' as expected
    union all
    select 2, 'cases 표 있음', (to_regclass('public.cases') is not null)::text, 'true'
    union all
    select 3, 'cases.user_id 형식',
           coalesce((select data_type from information_schema.columns
                     where table_schema = 'public' and table_name = 'cases' and column_name = 'user_id'), '없음'), 'uuid'
    union all
    select 4, 'case_drafts 표 있음(초안 저장, 2026-10 적용분)', (to_regclass('public.case_drafts') is not null)::text, 'true'
    union all
    select 5, 'case_draft_archive 표 없음(새로 만듦)', (to_regclass('public.case_draft_archive') is null)::text, 'true'
    union all
    select 6, 'service_reviews·review_admins 표 없음(새로 만듦)',
           (to_regclass('public.service_reviews') is null and to_regclass('public.review_admins') is null)::text, 'true'
    union all
    select 7, '같은 이름 함수 없음',
           (select (count(*) = 0)::text from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in (
                'case_draft_archive_touch', 'delete_case_draft_archive_with_case', 'service_reviews_owner_write',
                'published_reviews', 'reviews_for_moderation', 'moderate_review')), 'true'
    union all
    select 8, '같은 이름 트리거 없음',
           (select (count(*) = 0)::text from pg_trigger
            where tgname in ('case_draft_archive_touch', 'cases_delete_case_draft_archive', 'service_reviews_owner_write')), 'true'
    union all
    select 9, '기존 사건 삭제 트리거 2개 있음(초안·작업 공간, 유지)',
           (select count(*)::text from pg_trigger where tgname in ('cases_delete_case_draft', 'cases_delete_case_workspace')), '2'
    union all
    select 10, 'gen_random_uuid() 사용 가능', (to_regprocedure('gen_random_uuid()') is not null)::text, 'true'
)
select no, item, actual, expected, actual = expected as ok from checks order by no;

-- 개수(적용 뒤에도 같아야 한다). 내용은 읽지 않는다.
select (select count(*) from public.cases) as cases,
       (select count(*) from public.case_drafts) as drafts,
       (select count(*) from public.case_workspace) as workspaces,
       (select count(*) from auth.users) as users;
