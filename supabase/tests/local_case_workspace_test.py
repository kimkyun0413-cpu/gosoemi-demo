"""로컬 임시 Postgres에서 사건 작업 공간 제안(supabase/proposals/case_workspace)의 접근 제한·삭제를 검사한다.
운영 DB에는 접속하지 않는다.

필요: 로컬 PostgreSQL 서버(빈 서버, 슈퍼유저 접속)와 psycopg (pip install "psycopg[binary]")
실행: LOCAL_PG="host=127.0.0.1 port=54329 user=postgres" python3 supabase/tests/local_case_workspace_test.py
Supabase의 auth 스키마·역할·cases 정책을 흉내 낸 workspace_test DB를 새로 만들고 시험한다.
"""
import os, psycopg, uuid, sys
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PG = os.environ.get("LOCAL_PG", "host=127.0.0.1 port=54329 user=postgres")
if "supabase.co" in PG or "pooler" in PG:
    sys.exit("운영 DB 주소로는 실행하지 마세요. 로컬 임시 Postgres만 사용합니다.")
conn = psycopg.connect(PG + " dbname=postgres", autocommit=True)
conn.execute("drop database if exists workspace_test")
conn.execute("create database workspace_test encoding 'UTF8' template template0")
db = psycopg.connect(PG + " dbname=workspace_test client_encoding=UTF8", autocommit=True)
x = db.execute
# --- Supabase 비슷한 기본 구성 ---
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
-- 기본 권한: Supabase는 public 테이블에 anon/authenticated 권한을 기본 부여한다
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
create table public.cases (
  case_id uuid primary key, user_id uuid not null references auth.users(id),
  original_request text, case_type text, reporter_info text, suspect_info text, incident_datetime text,
  incident_location text, incident_description text, damage_details text, evidence text, witness text,
  punishment_intent text, created_at timestamptz);
alter table public.cases enable row level security; alter table public.cases force row level security;
revoke all on public.cases from anon, authenticated;
grant select, insert, delete on public.cases to authenticated;
grant all on public.cases to service_role;
create policy s on public.cases for select to authenticated using (auth.uid() = user_id);
create policy i on public.cases for insert to authenticated with check (auth.uid() = user_id);
create policy d on public.cases for delete to authenticated using (auth.uid() = user_id);
create table public.consent_records (user_id uuid references auth.users(id), terms_version text, privacy_version text,
  created_at timestamptz default now(), unique (user_id, terms_version, privacy_version));
""")
x(open(f"{REPO}/supabase/proposals/case_workspace/20261012000000_case_workspace.sql").read())
x("grant all on public.case_workspace to service_role")

A, B = uuid.uuid4(), uuid.uuid4()
CA, CB = uuid.uuid4(), uuid.uuid4()
x("insert into auth.users values (%s),(%s)", (A, B))
x("insert into public.cases (case_id, user_id, case_type) values (%s,%s,'a'),(%s,%s,'b')", (CA, A, CB, B))

results = []
def check(name, ok):
    results.append((name, ok)); print(("PASS " if ok else "FAIL ") + name)

def as_user(uid, sql, params=None):
    with db.transaction():
        db.execute("set local role authenticated")
        db.execute("select set_config('request.jwt.claims', %s, true)", (f'{{"sub":"{uid}","role":"authenticated"}}',))
        cur = db.execute(sql, params)
        return cur.fetchall() if cur.description else cur.rowcount

def fails(uid, sql, params=None):
    try:
        as_user(uid, sql, params); return False
    except psycopg.Error:
        return True

check("A가 본인 사건 체크리스트 저장", as_user(A, "insert into public.case_workspace (case_id, timeline) values (%s, '[{\"when\": \"9월 2일\", \"what\": \"송금\"}]')", (str(CA),)) == 1)
check("본인 체크리스트 수정", as_user(A, "update public.case_workspace set schedules = '[{\"title\": \"제출\"}]' where case_id = %s", (str(CA),)) == 1)
check("A가 B 사건에 만들기 차단", fails(A, "insert into public.case_workspace (case_id) values (%s)", (str(CB),)))
check("A가 B 이름으로 만들기 차단", fails(A, "insert into public.case_workspace (user_id, case_id) values (%s, %s)", (B, str(CB))))
check("없는 사건 차단", fails(A, "insert into public.case_workspace (case_id) values (%s)", (str(uuid.uuid4()),)))
as_user(B, "insert into public.case_workspace (case_id) values (%s)", (str(CB),))
check("A가 B 기록 읽기 0건", as_user(A, "select count(*) from public.case_workspace where user_id = %s", (B,)) == [(0,)])
check("A가 B 기록 수정 0건", as_user(A, "update public.case_workspace set answers = '{}' where user_id = %s", (B,)) == 0)
check("A가 B 기록 삭제 0건", as_user(A, "delete from public.case_workspace where user_id = %s", (B,)) == 0)
check("timeline이 배열이 아니면 차단", fails(A, "update public.case_workspace set timeline = '{}' where user_id = %s", (A,)))
check("checklist가 객체가 아니면 차단", fails(A, "update public.case_workspace set checklist = '[1]' where user_id = %s", (A,)))
check("timeline 32KB 초과 차단", fails(A, "update public.case_workspace set timeline = jsonb_build_array(repeat('가', 40000)) where user_id = %s", (A,)))
try:
    with db.transaction():
        db.execute("set local role anon")
        db.execute("select count(*) from public.case_workspace")
    check("비로그인 읽기 거부", False)
except psycopg.Error:
    check("비로그인 읽기 거부", True)
check("A가 본인 사건 삭제", as_user(A, "delete from public.cases where case_id = %s", (CA,)) == 1)
check("사건 삭제 시 체크리스트도 삭제", x("select count(*) from public.case_workspace where user_id = %s", (A,)).fetchone()[0] == 0)
with db.transaction():
    db.execute("set local role service_role")
    db.execute("delete from public.cases where user_id = %s", (B,))
    db.execute("delete from public.consent_records where user_id = %s", (B,))
db.execute("delete from auth.users where id = %s", (B,))
check("탈퇴 후 B 기록 0건", x("select count(*) from public.case_workspace where user_id = %s", (B,)).fetchone()[0] == 0)
x(open(f"{REPO}/supabase/proposals/case_workspace/20261012000000_case_workspace_rollback.sql").read())
check("되돌리기 후 테이블·트리거 없음", x("select to_regclass('public.case_workspace') is null and not exists (select from pg_trigger where tgname='cases_delete_case_workspace')").fetchone()[0])
print("\n%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
