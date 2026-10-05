-- 고소장 초안(public.case_drafts) 계정 간 접근 차단 테스트
-- 20261006000000_case_drafts.sql을 적용한 뒤, cross_account_access_test.sql과 같은 테스트 계정 두 개로 실행한다.
-- __A__ / __B__ 자리에 테스트 계정 ID를 넣는다. 각 계정에 사건이 1건 이상 있어야 한다.
-- 모든 쓰기 시도는 하위 트랜잭션 안에서 실행한 뒤 일부러 예외를 일으켜 항상 되돌린다. DB에 남는 변경이 없다.

select set_config('t.r', '', true);

do $$
declare
  pairs uuid[][] := array[array['__A__'::uuid, '__B__'::uuid], array['__B__'::uuid, '__A__'::uuid]];
  me uuid; other uuid; who text; n bigint; e text; d text; my_case text; other_case text; i int;
begin
  for i in 1..2 loop
    me := pairs[i][1]; other := pairs[i][2];
    who := case when i = 1 then 'A가 B에게' else 'B가 A에게' end;
    select case_id::text into other_case from public.cases where user_id = other limit 1;

    perform set_config('role', 'authenticated', true);
    perform set_config('request.jwt.claim.sub', me::text, true);
    perform set_config('request.jwt.claims', json_build_object('sub', me, 'role', 'authenticated')::text, true);

    select case_id::text into my_case from public.cases where user_id = me limit 1;

    -- 0. 대조군: 본인 사건에는 초안을 만들 수 있어야 한다 (되돌림)
    begin
      insert into public.case_drafts (case_id, draft_text) values (my_case, '접근 테스트')
      on conflict (user_id, case_id) do update set draft_text = excluded.draft_text;
      get diagnostics n = row_count;
      raise exception using message = '__probe__', detail = n::text;
    exception when others then
      get stacked diagnostics e = message_text, d = pg_exception_detail;
      perform set_config('t.r', current_setting('t.r') || who || '|0 본인 사건 초안 저장(대조)|1건|' ||
        case when e = '__probe__' then d || '건' else '거부: ' || e end || E'\n', true);
    end;

    -- 1·4·5. 다른 사용자 초안 읽기·수정·삭제
    -- 상대 계정에 초안이 없으면 0건이 당연하므로, 관리자 권한으로 상대 초안을 임시로 만든 뒤 시도하고 모두 되돌린다.
    begin
      perform set_config('role', 'none', true);
      insert into public.case_drafts (user_id, case_id, draft_text)
      values (other, other_case, '접근 테스트 대상')
      on conflict (user_id, case_id) do nothing;
      perform set_config('role', 'authenticated', true);
      select count(*) into n from public.case_drafts where user_id = other;
      d := n::text;
      update public.case_drafts set draft_text = '변경 시도' where user_id = other;
      get diagnostics n = row_count;
      d := d || ',' || n;
      delete from public.case_drafts where user_id = other;
      get diagnostics n = row_count;
      d := d || ',' || n;
      raise exception using message = '__probe__', detail = d;
    exception when others then
      get stacked diagnostics e = message_text, d = pg_exception_detail;
      if e = '__probe__' then
        perform set_config('t.r', current_setting('t.r') || who || '|1 다른 사용자 초안 읽기|0|' || split_part(d, ',', 1) || E'\n', true);
        perform set_config('t.r', current_setting('t.r') || who || '|4 다른 사용자 초안 수정|0건|' || split_part(d, ',', 2) || '건' || E'\n', true);
        perform set_config('t.r', current_setting('t.r') || who || '|5 다른 사용자 초안 삭제|0건|' || split_part(d, ',', 3) || '건' || E'\n', true);
      else
        perform set_config('t.r', current_setting('t.r') || who || '|1·4·5 다른 사용자 초안 읽기·수정·삭제|0건|오류: ' || e || E'\n', true);
      end if;
    end;
    perform set_config('role', 'authenticated', true);

    -- 2. 다른 사용자 사건에 내 초안 만들기
    begin
      insert into public.case_drafts (case_id, draft_text) values (other_case, '접근 테스트');
      raise exception using message = '__probe__', detail = '1';
    exception when others then
      get stacked diagnostics e = message_text, d = pg_exception_detail;
      perform set_config('t.r', current_setting('t.r') || who || '|2 다른 사용자 사건에 초안 생성|거부|' ||
        case when e = '__probe__' then '허용됨(' || d || '건)' else '거부: ' || e end || E'\n', true);
    end;

    -- 3. 다른 사용자 이름으로 초안 만들기
    begin
      insert into public.case_drafts (user_id, case_id, draft_text) values (other, other_case, '접근 테스트');
      raise exception using message = '__probe__', detail = '1';
    exception when others then
      get stacked diagnostics e = message_text, d = pg_exception_detail;
      perform set_config('t.r', current_setting('t.r') || who || '|3 다른 사용자 이름으로 초안 생성|거부|' ||
        case when e = '__probe__' then '허용됨(' || d || '건)' else '거부: ' || e end || E'\n', true);
    end;

    perform set_config('role', 'none', true);
  end loop;

  -- 6. 비로그인(anon)
  perform set_config('role', 'anon', true);
  perform set_config('request.jwt.claim.sub', '', true);
  perform set_config('request.jwt.claims', '{"role":"anon"}', true);
  begin
    select count(*) into n from public.case_drafts;
    perform set_config('t.r', current_setting('t.r') || '비로그인|6 초안 읽기|거부|' || n || '건 보임' || E'\n', true);
  exception when others then
    get stacked diagnostics e = message_text;
    perform set_config('t.r', current_setting('t.r') || '비로그인|6 초안 읽기|거부|거부: ' || e || E'\n', true);
  end;
  perform set_config('role', 'none', true);
end $$;

select split_part(l, '|', 1) as account,
       split_part(l, '|', 2) as test,
       split_part(l, '|', 3) as expected,
       split_part(l, '|', 4) as actual
from regexp_split_to_table(current_setting('t.r'), E'\n') with ordinality as t(l, ord)
where l <> ''
order by ord;
