-- case_drafts 마이그레이션 적용 전 사전 점검 (읽기 전용)
-- 조회만 하며 어떤 행도 만들거나 바꾸지 않는다. 사용자 자료의 내용은 읽지 않고 구조와 개수만 본다.
-- 결과의 ok 열이 모두 true여야 20261006000000_case_drafts.sql을 그대로 적용할 수 있다.

with checks as (
    -- 1. cases.user_id는 uuid여야 정책의 auth.uid() 비교와 트리거의 ::uuid 변환이 맞는다
    select 1 as no, 'cases.user_id 형식' as item,
           coalesce((select data_type from information_schema.columns
                     where table_schema = 'public' and table_name = 'cases' and column_name = 'user_id'), '없음') as actual,
           'uuid' as expected
    union all
    -- 2. cases.case_id는 uuid 또는 text (마이그레이션은 ::text로 비교한다)
    select 2, 'cases.case_id 형식',
           coalesce((select data_type from information_schema.columns
                     where table_schema = 'public' and table_name = 'cases' and column_name = 'case_id'), '없음'),
           'uuid 또는 text'
    union all
    -- 3. 같은 이름의 테이블·함수·트리거가 아직 없어야 한다
    select 3, 'case_drafts 테이블 없음', coalesce(to_regclass('public.case_drafts')::text, '없음'), '없음'
    union all
    select 4, '같은 이름 함수 없음',
           coalesce((select string_agg(proname, ', ') from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                     where n.nspname = 'public'
                       and proname in ('case_drafts_touch_updated_at', 'delete_case_draft_with_case')), '없음'),
           '없음'
    union all
    select 5, 'cases에 같은 이름 트리거 없음',
           coalesce((select string_agg(tgname, ', ') from pg_trigger
                     where tgrelid = 'public.cases'::regclass and tgname = 'cases_delete_case_draft'), '없음'),
           '없음'
    union all
    -- 6. cases RLS가 켜져 있어야 초안 정책의 "본인 사건" 확인이 의미가 있다
    select 6, 'cases RLS 사용',
           (select relrowsecurity::text from pg_class where oid = 'public.cases'::regclass), 'true'
    union all
    -- 7. auth.users.id는 uuid (외래키 대상)
    select 7, 'auth.users.id 형식',
           coalesce((select data_type from information_schema.columns
                     where table_schema = 'auth' and table_name = 'users' and column_name = 'id'), '없음'),
           'uuid'
    union all
    -- 8. 동의 함수가 저장소의 이전 버전(2026-09-27 고정)과 같은지
    select 8, 'record_consent 현재 버전 문자열',
           case when pg_get_functiondef('public.record_consent(text, text)'::regprocedure) like '%2026-09-27%'
                then '2026-09-27 포함' else '다름: 직접 확인 필요' end,
           '2026-09-27 포함'
    union all
    -- 9. consent_records의 중복 방지 제약 (on conflict 대상)
    select 9, 'consent_records 고유 제약',
           coalesce((select string_agg(pg_get_constraintdef(c.oid), '; ') from pg_constraint c
                     where c.conrelid = 'public.consent_records'::regclass and c.contype in ('u', 'p')), '없음'),
           'UNIQUE (user_id, terms_version, privacy_version) 포함'
)
select no, item, actual, expected,
       case no
           when 1 then actual = 'uuid'
           when 2 then actual in ('uuid', 'text', 'character varying')
           when 3 then actual = '없음'
           when 4 then actual = '없음'
           when 5 then actual = '없음'
           when 6 then actual = 'true'
           when 7 then actual = 'uuid'
           when 8 then actual = '2026-09-27 포함'
           when 9 then actual like '%(user_id, terms_version, privacy_version)%'
       end as ok
from checks
order by no;

-- 참고용: cases의 현재 정책과 열 목록 (구조만, 자료 내용은 읽지 않음)
select policyname, cmd, roles, qual, with_check
from pg_policies where schemaname = 'public' and tablename = 'cases'
order by policyname;
