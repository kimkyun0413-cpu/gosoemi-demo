"""로컬 임시 Postgres에서 AI 사용량 기록의 최소 권한 수정안을 검사한다. 운영 DB에는 접속하지 않는다.

PR 원안(20261014000000_ai_usage.sql) 위에 supabase/proposals/ai_usage_least_privilege/least_privilege.sql을 적용하고,
전용 역할 gosomi_ai_usage가 예약·완료만 할 수 있고 다른 자료·함수에는 닿지 못하는지, 이용자는 여전히 조작할 수 없는지,
되돌리기가 되는지 확인한다.
실행: LOCAL_PG="host=127.0.0.1 port=54329 user=postgres" python3 supabase/tests/local_ai_usage_least_privilege_test.py
"""
import os
import sys
import uuid

import psycopg

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PG = os.environ.get("LOCAL_PG", "host=127.0.0.1 port=54329 user=postgres")
if "supabase.co" in PG or "pooler" in PG:
    sys.exit("운영 DB 주소로는 실행하지 마세요. 로컬 임시 Postgres만 사용합니다.")
PROPOSAL = f"{REPO}/supabase/proposals/ai_usage_least_privilege"
TEST_PASSWORD = "local-test-" + uuid.uuid4().hex   # 로컬 시험용, 실행마다 새로 만듦

admin = psycopg.connect(PG + " dbname=postgres", autocommit=True)
admin.execute("drop database if exists ai_usage_lp_test")
admin.execute("drop role if exists gosomi_ai_usage")
admin.execute("create database ai_usage_lp_test encoding 'UTF8' template template0")
db = psycopg.connect(PG + " dbname=ai_usage_lp_test client_encoding=UTF8", autocommit=True)
x = db.execute
x("""
do $$ begin
  if not exists (select from pg_roles where rolname='anon') then create role anon nologin; end if;
  if not exists (select from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
  if not exists (select from pg_roles where rolname='service_role') then create role service_role nologin bypassrls; end if;
end $$;
create schema auth;
create table auth.users (id uuid primary key);
create function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claims', true)::json->>'sub', '')::uuid $$;
grant usage on schema auth to anon, authenticated, service_role;
grant execute on function auth.uid() to anon, authenticated, service_role;
grant usage on schema public to anon, authenticated, service_role;
-- Supabase 비슷한 기존 자료 표와 함수
create table public.cases (case_id uuid primary key, user_id uuid references auth.users(id), incident_description text);
alter table public.cases enable row level security;
create policy own on public.cases for select to authenticated using (user_id = auth.uid());
grant select, insert, update, delete on public.cases to authenticated, service_role;
create function public.record_consent(t text, p text) returns void language plpgsql security definer set search_path='' as $$
begin if auth.uid() is null then raise exception 'unauthenticated'; end if; end $$;
revoke execute on function public.record_consent(text,text) from public, anon;
grant execute on function public.record_consent(text,text) to authenticated;
""")
x(open(f"{REPO}/supabase/migrations/20261014000000_ai_usage.sql", encoding="utf-8").read())
A, B = uuid.uuid4(), uuid.uuid4()
x("insert into auth.users values (%s), (%s)", (A, B))
x("insert into public.cases values (%s, %s, '가상 사건 내용')", (uuid.uuid4(), A))

results = []


def check(name, ok):
    results.append(bool(ok))
    print(("통과" if ok else "실패"), name)


def as_role(role, sql, params=(), uid=None):
    """역할을 바꿔 실행(SET ROLE). 결과 또는 'ERROR'."""
    try:
        x("begin")
        x(f"set local role {role}")
        if uid:
            x("select set_config('request.jwt.claims', %s, true)", (f'{{"sub":"{uid}"}}',))
        row = x(sql, params).fetchone()
        x("commit")
        return row[0] if row else None
    except Exception:
        x("rollback")
        return "ERROR"


LIMITS = (30, 300, 100, 2000)
RESERVE = "select public.reserve_ai_usage(%s, %s, 'chat', 'gemini-test', %s, %s, %s, %s)"
FINISH = "select public.finish_ai_usage(%s, 'succeeded', 10, 20, 0, 30)"

# 원안: service_role만 예약 가능, 이용자는 불가
check("원안: service_role 예약 가능", as_role("service_role", RESERVE, (A, uuid.uuid4(), *LIMITS)) == "reserved")
check("원안: 이용자(authenticated)는 예약 불가", as_role("authenticated", RESERVE, (A, uuid.uuid4(), *LIMITS), uid=A) == "ERROR")

proposal = open(f"{PROPOSAL}/least_privilege.sql", encoding="utf-8").read()
check("수정안에 비밀번호가 들어 있지 않음", "password '" not in proposal.split("-- 비밀번호는")[0] and "alter role gosomi_ai_usage password '<" in proposal)
x(proposal)
x(f"alter role gosomi_ai_usage password '{TEST_PASSWORD}'")

# 전용 역할로 실제 로그인해서 확인(SET ROLE이 아니라 진짜 접속)
host = dict(p.split("=") for p in PG.split())
w = psycopg.connect(host=host.get("host"), port=host.get("port"), dbname="ai_usage_lp_test",
                    user="gosomi_ai_usage", password=TEST_PASSWORD, autocommit=True)


def as_writer(sql, params=()):
    try:
        row = w.execute(sql, params).fetchone()
        return row[0] if row else None
    except Exception:
        w.rollback() if not w.autocommit else None
        return "ERROR"


rid = uuid.uuid4()
check("전용 역할: 예약 가능", as_writer(RESERVE, (B, rid, *LIMITS)) == "reserved")
check("전용 역할: 완료 기록 가능", as_writer(FINISH, (rid,)) is True)
check("전용 역할: 같은 요청 두 번 완료 불가(조작 방지)", as_writer(FINISH, (rid,)) is False)
check("전용 역할: 사용량 표 직접 읽기 불가", as_writer("select count(*) from ai_private.requests") == "ERROR")
check("전용 역할: 사건 표 읽기 불가", as_writer("select count(*) from public.cases") == "ERROR")
check("전용 역할: 이용자 표 읽기 불가", as_writer("select count(*) from auth.users") == "ERROR")
check("전용 역할: 30일 정리 실행 불가", as_writer("select public.prune_ai_usage()") == "ERROR")
check("전용 역할: 사용량 보고 실행 불가", as_writer("select public.ai_usage_report()") == "ERROR")
check("전용 역할: 본인 기록 함수 실행 불가", as_writer("select public.my_ai_usage()") == "ERROR")
check("전용 역할: 동의 기록 함수 실행 불가", as_writer("select public.record_consent('a','b')") == "ERROR")
check("전용 역할: service_role로 바꾸기 불가", as_writer("set role service_role") == "ERROR")
check("전용 역할: 표 만들기 불가", as_writer("create table public.x (id int)") == "ERROR")
check("전용 역할: RLS 우회 권한 없음", not x("select rolbypassrls from pg_roles where rolname='gosomi_ai_usage'").fetchone()[0])
check("전용 역할: 동시 접속 5개 제한", x("select rolconnlimit from pg_roles where rolname='gosomi_ai_usage'").fetchone()[0] == 5)

# 한도는 그대로 지켜짐(1시간 2회로 줄여 확인)
small = (2, 300, 100, 2000)
# A는 원안 시험에서 이미 1건 → 한도 2면 한 번 더는 되고, 그다음은 막혀야 한다
second = as_writer(RESERVE, (A, uuid.uuid4(), *small))
third = as_writer(RESERVE, (A, uuid.uuid4(), *small))
check("전용 역할로도 계정 1시간 한도가 지켜짐(2건까지, 3번째 거부)", (second, third) == ("reserved", "user_hour"))
w.close()

# 이용자는 여전히 조작 불가, service_role 키는 더 이상 필요 없음
check("이용자(authenticated): 예약 불가", as_role("authenticated", RESERVE, (A, uuid.uuid4(), *LIMITS), uid=A) == "ERROR")
check("이용자(authenticated): 완료 기록 불가", as_role("authenticated", FINISH, (rid,), uid=A) == "ERROR")
check("비로그인(anon): 예약 불가", as_role("anon", RESERVE, (A, uuid.uuid4(), *LIMITS)) == "ERROR")
check("service_role: 예약 권한 회수됨", as_role("service_role", RESERVE, (A, uuid.uuid4(), *LIMITS)) == "ERROR")
check("이용자: 본인 기록 보기는 그대로", isinstance(as_role("authenticated", "select public.my_ai_usage()", uid=B), list))

# 되돌리기
x(open(f"{PROPOSAL}/least_privilege_rollback.sql", encoding="utf-8").read())
check("되돌리기 후 전용 역할 없음", x("select count(*) from pg_roles where rolname='gosomi_ai_usage'").fetchone()[0] == 0)
check("되돌리기 후 service_role 예약 다시 가능", as_role("service_role", RESERVE, (B, uuid.uuid4(), *LIMITS)) == "reserved")
check("되돌려도 사용량 기록 유지", x("select count(*) from ai_private.requests").fetchone()[0] >= 3)

db.close()
admin.execute("drop database ai_usage_lp_test")
print(f"\n{sum(results)}/{len(results)} 통과")
sys.exit(0 if all(results) else 1)
