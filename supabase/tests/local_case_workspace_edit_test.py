"""로컬 임시 Postgres에서 사건 작업 공간 저장·사건 수정 마이그레이션(20261012000000)을 검사한다. 운영 DB에는 접속하지 않는다.

검사: 본인 것만 조회·수정, 고칠 수 없는 열, 동시 수정(version) 충돌, 사건 삭제·탈퇴 시 삭제, 되돌리기.

필요: 로컬 PostgreSQL 서버(빈 서버, 슈퍼유저 접속)와 psycopg (pip install "psycopg[binary]")
실행: LOCAL_PG="host=127.0.0.1 port=54329 user=postgres" python3 supabase/tests/local_case_workspace_edit_test.py
Supabase의 auth 스키마·역할·cases 정책을 흉내 낸 workspace_edit_test DB를 새로 만들고 시험한다.
"""
import os, psycopg, uuid, sys
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PG = os.environ.get("LOCAL_PG", "host=127.0.0.1 port=54329 user=postgres")
if "supabase.co" in PG or "pooler" in PG:
    sys.exit("운영 DB 주소로는 실행하지 마세요. 로컬 임시 Postgres만 사용합니다.")
conn = psycopg.connect(PG + " dbname=postgres", autocommit=True)
conn.execute("drop database if exists workspace_edit_test")
conn.execute("create database workspace_edit_test encoding 'UTF8' template template0")
db = psycopg.connect(PG + " dbname=workspace_edit_test client_encoding=UTF8", autocommit=True)
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
x(open(f"{REPO}/supabase/migrations/20261006000000_case_drafts.sql").read())
x(open(f"{REPO}/supabase/migrations/20261012000000_case_workspace_and_case_edit.sql").read())
x("grant all on public.case_workspace to service_role")

A, B = uuid.uuid4(), uuid.uuid4()
CA, CB = uuid.uuid4(), uuid.uuid4()
x("insert into auth.users values (%s),(%s)", (A, B))
x("insert into public.cases (case_id, user_id, case_type, incident_description) values (%s,%s,'a','원래 경위'),(%s,%s,'b','B 경위')",
  (CA, A, CB, B))

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

# ── 사건 수정 ──
row = as_user(A, "update public.cases set incident_description = '고친 경위' where case_id = %s and edit_version = 1 returning edit_version, updated_at is not null", (CA,))
check("A가 본인 사건 내용 수정(버전 1→2, 수정 시각 기록)", row == [(2, True)])
check("옛 버전으로 고치면 0건(동시 수정 충돌)", as_user(A, "update public.cases set witness = 'x' where case_id = %s and edit_version = 1", (CA,)) == 0)
check("A가 B 사건 수정 0건", as_user(A, "update public.cases set incident_description = 'hack' where case_id = %s", (CB,)) == 0)
check("B 사건 내용은 그대로", x("select incident_description from public.cases where case_id = %s", (CB,)).fetchone()[0] == "B 경위")
check("user_id는 고칠 수 없음", fails(A, "update public.cases set user_id = %s where case_id = %s", (B, CA)))
check("case_id는 고칠 수 없음", fails(A, "update public.cases set case_id = %s where case_id = %s", (uuid.uuid4(), CA)))
check("created_at은 고칠 수 없음", fails(A, "update public.cases set created_at = now() where case_id = %s", (CA,)))
check("edit_version을 직접 바꿀 수 없음", fails(A, "update public.cases set edit_version = 99 where case_id = %s", (CA,)))
try:
    with db.transaction():
        db.execute("set local role anon")
        db.execute("update public.cases set witness = 'x'")
    check("비로그인 수정 거부", False)
except psycopg.Error:
    check("비로그인 수정 거부", True)

# ── 작업 공간 ──
ins = as_user(A, "insert into public.case_workspace (case_id, timeline, schedules, checklist) values (%s, '[{\"when\": \"9월 2일\", \"what\": \"송금\"}]', '[{\"title\": \"제출\", \"date\": \"2026-10-20\"}]', '{\"story\": true}') returning version", (str(CA),))
check("A가 본인 사건 작업 공간 저장(버전 1)", ins == [(1,)])
upd = as_user(A, "update public.case_workspace set checklist = '{\"story\": true, \"draft\": true}' where case_id = %s and version = 1 returning version", (str(CA),))
check("같은 버전으로 저장하면 버전 2", upd == [(2,)])
check("옛 버전(1)으로 저장하면 0건(다른 곳에서 먼저 저장)", as_user(A, "update public.case_workspace set checklist = '{}' where case_id = %s and version = 1", (str(CA),)) == 0)
check("version을 직접 바꿀 수 없음", fails(A, "update public.case_workspace set version = 1 where case_id = %s", (str(CA),)))
check("같은 사건에 두 번 만들 수 없음", fails(A, "insert into public.case_workspace (case_id) values (%s)", (str(CA),)))
check("A가 B 사건에 작업 공간 만들기 차단", fails(A, "insert into public.case_workspace (case_id) values (%s)", (str(CB),)))
check("A가 B 이름으로 만들기 차단", fails(A, "insert into public.case_workspace (user_id, case_id) values (%s, %s)", (B, str(CB))))
as_user(B, "insert into public.case_workspace (case_id) values (%s)", (str(CB),))
check("A가 B 작업 공간 읽기 0건", as_user(A, "select count(*) from public.case_workspace where user_id = %s", (B,)) == [(0,)])
check("A가 B 작업 공간 수정 0건", as_user(A, "update public.case_workspace set checklist = '{}' where user_id = %s", (B,)) == 0)
check("A가 B 작업 공간 삭제 0건", as_user(A, "delete from public.case_workspace where user_id = %s", (B,)) == 0)
check("timeline이 배열이 아니면 차단", fails(A, "update public.case_workspace set timeline = '{}' where case_id = %s", (str(CA),)))
check("timeline 32KB 초과 차단", fails(A, "update public.case_workspace set timeline = jsonb_build_array(repeat('가', 40000)) where case_id = %s", (str(CA),)))
try:
    with db.transaction():
        db.execute("set local role anon")
        db.execute("select count(*) from public.case_workspace")
    check("비로그인 읽기 거부", False)
except psycopg.Error:
    check("비로그인 읽기 거부", True)

# ── 사건 삭제·탈퇴 ──
as_user(A, "insert into public.case_drafts (case_id, draft_text) values (%s, '초안')", (str(CA),))
check("A가 본인 사건 삭제", as_user(A, "delete from public.cases where case_id = %s", (CA,)) == 1)
check("사건 삭제 시 작업 공간도 삭제", x("select count(*) from public.case_workspace where user_id = %s", (A,)).fetchone()[0] == 0)
check("사건 삭제 시 초안도 삭제(기존 트리거 유지)", x("select count(*) from public.case_drafts where user_id = %s", (A,)).fetchone()[0] == 0)
with db.transaction():
    db.execute("set local role service_role")
    db.execute("delete from public.cases where user_id = %s", (B,))
    db.execute("delete from public.consent_records where user_id = %s", (B,))
db.execute("delete from auth.users where id = %s", (B,))
check("탈퇴 후 B 작업 공간 0건", x("select count(*) from public.case_workspace where user_id = %s", (B,)).fetchone()[0] == 0)
C, CC = uuid.uuid4(), uuid.uuid4()
x("insert into auth.users values (%s)", (C,))
x("insert into public.cases (case_id, user_id) values (%s, %s)", (CC, C))
as_user(C, "insert into public.case_workspace (case_id) values (%s)", (str(CC),))
x("delete from public.cases where user_id = %s", (C,)); x("delete from auth.users where id = %s", (C,))
check("사용자 삭제 시 작업 공간 cascade", x("select count(*) from public.case_workspace").fetchone()[0] == 0)

# ── 되돌리기 ──
D, CD = uuid.uuid4(), uuid.uuid4()
x("insert into auth.users values (%s)", (D,))
x("insert into public.cases (case_id, user_id, witness) values (%s, %s, '없음')", (CD, D))
x(open(f"{REPO}/supabase/rollbacks/20261012000000_case_workspace_and_case_edit_rollback.sql").read())
check("되돌리기 후 작업 공간 표 없음", x("select to_regclass('public.case_workspace') is null").fetchone()[0])
check("되돌리기 후 사건 수정 다시 막힘", fails(D, "update public.cases set witness = 'x' where case_id = %s", (CD,)))
check("되돌리기 후 추가 열 없음", x("select count(*) from information_schema.columns where table_name='cases' and column_name in ('edit_version','updated_at')").fetchone()[0] == 0)
check("되돌리기 후 사건 내용 유지", x("select witness from public.cases where case_id = %s", (CD,)).fetchone()[0] == "없음")
check("되돌리기 후 사건 삭제 정상", as_user(D, "delete from public.cases where case_id = %s", (CD,)) == 1)
# 다시 적용해도 되는지
x(open(f"{REPO}/supabase/migrations/20261012000000_case_workspace_and_case_edit.sql").read())
check("되돌린 뒤 다시 적용 가능", x("select to_regclass('public.case_workspace') is not null").fetchone()[0])
print("\n%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
