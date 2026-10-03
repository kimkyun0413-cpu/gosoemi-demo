-- 고소음이 계정 간 접근 차단 테스트
-- __A__ / __B__ 자리에 테스트 계정 ID를 넣어 실행한다.
-- 각 쓰기 시도는 하위 트랜잭션 안에서 실행한 뒤 일부러 예외를 일으켜 항상 되돌린다.
-- 결과는 트랜잭션 범위 설정값(t.r)에만 쌓이므로 DB에 남는 변경이 없다.

select set_config('t.r', '', true);

do $$
declare
  pairs uuid[][] := array[array['__A__'::uuid, '__B__'::uuid], array['__B__'::uuid, '__A__'::uuid]];
  me uuid; other uuid; who text; n bigint; e text; d text; j jsonb; i int;
begin
  for i in 1..2 loop
    me := pairs[i][1]; other := pairs[i][2];
    who := case when i = 1 then 'A가 B에게' else 'B가 A에게' end;

    perform set_config('role', 'authenticated', true);
    perform set_config('request.jwt.claim.sub', me::text, true);
    perform set_config('request.jwt.claims', json_build_object('sub', me, 'role', 'authenticated')::text, true);

    -- 대조군: 본인 자료는 보여야 테스트가 의미 있다
    select count(*) into n from public.cases where user_id = me;
    perform set_config('t.r', current_setting('t.r') || who || '|0 본인 사건 조회(대조)|1 이상|' || n || E'\n', true);
    select count(*) into n from storage.objects where bucket_id = 'case-evidence' and (storage.foldername(name))[1] = me::text;
    perform set_config('t.r', current_setting('t.r') || who || '|0 본인 증거파일 조회(대조)|1 이상|' || n || E'\n', true);

    -- 1. 다른 사용자 사건 읽기
    select count(*) into n from public.cases where user_id <> me;
    perform set_config('t.r', current_setting('t.r') || who || '|1 다른 사용자 사건 읽기|0|' || n || E'\n', true);

    -- 2. 다른 사용자 사건 수정
    begin
      update public.cases set user_id = user_id where user_id = other;
      get diagnostics n = row_count;
      raise exception using message = '__probe__', detail = n::text;
    exception when others then
      get stacked diagnostics e = message_text, d = pg_exception_detail;
      perform set_config('t.r', current_setting('t.r') || who || '|2 다른 사용자 사건 수정|0건 또는 거부|' ||
        case when e = '__probe__' then d || '건' else '거부: ' || e end || E'\n', true);
    end;

    -- 3. 다른 사용자 사건 삭제
    begin
      delete from public.cases where user_id = other;
      get diagnostics n = row_count;
      raise exception using message = '__probe__', detail = n::text;
    exception when others then
      get stacked diagnostics e = message_text, d = pg_exception_detail;
      perform set_config('t.r', current_setting('t.r') || who || '|3 다른 사용자 사건 삭제|0건 또는 거부|' ||
        case when e = '__probe__' then d || '건' else '거부: ' || e end || E'\n', true);
    end;

    -- 4. 다른 사용자 이름으로 사건 만들기 (본인 사건을 복제해 user_id만 바꿈)
    begin
      select to_jsonb(c) into j from public.cases c where user_id = me limit 1;
      j := j || jsonb_build_object('case_id', gen_random_uuid(), 'user_id', other);
      insert into public.cases select * from jsonb_populate_record(null::public.cases, j);
      raise exception using message = '__probe__', detail = '1';
    exception when others then
      get stacked diagnostics e = message_text, d = pg_exception_detail;
      perform set_config('t.r', current_setting('t.r') || who || '|4 다른 사용자 이름으로 사건 생성|거부|' ||
        case when e = '__probe__' then '허용됨(' || d || '건)' else '거부: ' || e end || E'\n', true);
    end;

    -- 5. 다른 사용자 증거파일 조회(다운로드·서명 URL은 이 조회 권한이 있어야 가능)
    select count(*) into n from storage.objects where bucket_id = 'case-evidence' and (storage.foldername(name))[1] <> me::text;
    perform set_config('t.r', current_setting('t.r') || who || '|5 다른 사용자 증거파일 조회|0|' || n || E'\n', true);

    -- 6. 다른 사용자 증거파일 이름 변경(이동)
    begin
      update storage.objects set name = name where bucket_id = 'case-evidence' and (storage.foldername(name))[1] = other::text;
      get diagnostics n = row_count;
      raise exception using message = '__probe__', detail = n::text;
    exception when others then
      get stacked diagnostics e = message_text, d = pg_exception_detail;
      perform set_config('t.r', current_setting('t.r') || who || '|6 다른 사용자 증거파일 수정|0건 또는 거부|' ||
        case when e = '__probe__' then d || '건' else '거부: ' || e end || E'\n', true);
    end;

    -- 7. 다른 사용자 증거파일 삭제 (SQL 직접 삭제는 Supabase 보호 장치가 먼저 막으므로 RLS 판정은 정책 검토로 대신한다)
    begin
      delete from storage.objects where bucket_id = 'case-evidence' and (storage.foldername(name))[1] = other::text;
      get diagnostics n = row_count;
      raise exception using message = '__probe__', detail = n::text;
    exception when others then
      get stacked diagnostics e = message_text, d = pg_exception_detail;
      perform set_config('t.r', current_setting('t.r') || who || '|7 다른 사용자 증거파일 삭제|0건 또는 거부|' ||
        case when e = '__probe__' then d || '건' else '거부: ' || e end || E'\n', true);
    end;

    -- 8. 다른 사용자 폴더에 파일 올리기
    begin
      insert into storage.objects (bucket_id, name, owner, owner_id)
      values ('case-evidence', other::text || '/access-probe/probe.txt', me, me::text);
      raise exception using message = '__probe__', detail = '1';
    exception when others then
      get stacked diagnostics e = message_text, d = pg_exception_detail;
      perform set_config('t.r', current_setting('t.r') || who || '|8 다른 사용자 폴더에 업로드|거부|' ||
        case when e = '__probe__' then '허용됨(' || d || '건)' else '거부: ' || e end || E'\n', true);
    end;

    perform set_config('role', 'none', true);
  end loop;

  -- 9. 비로그인(anon) 사용자
  perform set_config('role', 'anon', true);
  perform set_config('request.jwt.claim.sub', '', true);
  perform set_config('request.jwt.claims', '{"role":"anon"}', true);
  begin
    select count(*) into n from public.cases;
    perform set_config('t.r', current_setting('t.r') || '비로그인|9 사건 읽기|0 또는 거부|' || n || E'\n', true);
  exception when others then
    get stacked diagnostics e = message_text;
    perform set_config('t.r', current_setting('t.r') || '비로그인|9 사건 읽기|0 또는 거부|거부: ' || e || E'\n', true);
  end;
  select count(*) into n from storage.objects where bucket_id = 'case-evidence';
  perform set_config('t.r', current_setting('t.r') || '비로그인|9 증거파일 조회|0|' || n || E'\n', true);
  perform set_config('role', 'none', true);
end $$;

select split_part(l, '|', 1) as account,
       split_part(l, '|', 2) as test,
       split_part(l, '|', 3) as expected,
       split_part(l, '|', 4) as actual
from regexp_split_to_table(current_setting('t.r'), E'\n') with ordinality as t(l, ord)
where l <> ''
order by ord;
