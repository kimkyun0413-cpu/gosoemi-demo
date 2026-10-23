"""로컬 임시 Postgres에서 이전 초안 보관 마이그레이션(20261023000000)을 검사한다. 운영 DB에는 접속하지 않는다.

검사: 본인 사건에만 보관·조회·수정·삭제, 고칠 수 있는 열(draft_text만), 보관 시각은 서버가 정함, 앱 저장 방식(고치고 없으면 넣기),
사건 삭제·탈퇴 시 삭제, 되돌리기.
필요: 로컬 PostgreSQL 서버(빈 서버, 슈퍼유저 접속)와 psycopg (pip install "psycopg[binary]")
실행: LOCAL_PG="host=127.0.0.1 port=54329 user=postgres" python3 supabase/tests/local_case_draft_archive_test.py
"""
import os, psycopg, uuid, sys
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PG = os.environ.get("LOCAL_PG", "host=127.0.0.1 port=54329 user=postgres")
if "supabase.co" in PG or "pooler" in PG:
    sys.exit("운영 DB 주소로는 실행하지 마세요. 로컬 임시 Postgres만 사용합니다.")
conn = psycopg.connect(PG + " dbname=postgres", autocommit=True)
conn.execute("drop database if exists draft_archive_test")
conn.execute("create database draft_archive_test encoding 'UTF8' template template0")
db = psycopg.connect(PG + " dbname=draft_archive_test client_encoding=UTF8", autocommit=True)
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
""")
x(open(f"{REPO}/supabase/migrations/20261006000000_case_drafts.sql").read())
x(open(f"{REPO}/supabase/migrations/20261023000000_case_draft_archive.sql").read())

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

# 앱의 저장 방식: 먼저 고치고(0건이면) 새로 넣는다
check("처음엔 고칠 행이 없어 0건", as_user(A, "update public.case_draft_archive set draft_text = '첫 보관' where user_id = %s and case_id = %s", (A, str(CA))) == 0)
row = as_user(A, "insert into public.case_draft_archive (user_id, case_id, draft_text) values (%s, %s, '첫 보관') returning archived_at is not null", (A, str(CA)))
check("A가 본인 사건에 이전 초안 보관", row == [(True,)])
first_at = x("select archived_at from public.case_draft_archive where case_id = %s", (str(CA),)).fetchone()[0]
row = as_user(A, "update public.case_draft_archive set draft_text = '두 번째 보관' where user_id = %s and case_id = %s returning archived_at", (A, str(CA)))
check("다시 보관하면 고쳐지고 보관 시각이 새로 기록", len(row) == 1 and row[0][0] >= first_at)
check("같은 사건에 두 개 만들 수 없음(사건당 1개)", fails(A, "insert into public.case_draft_archive (case_id, draft_text) values (%s, 'x')", (str(CA),)))
check("보관 시각을 직접 바꿀 수 없음", fails(A, "update public.case_draft_archive set archived_at = '2000-01-01' where case_id = %s", (str(CA),)))
check("case_id를 바꿀 수 없음", fails(A, "update public.case_draft_archive set case_id = %s where case_id = %s", (str(CB), str(CA))))
check("A가 B 사건에 보관 차단", fails(A, "insert into public.case_draft_archive (case_id, draft_text) values (%s, 'x')", (str(CB),)))
check("A가 B 이름으로 보관 차단", fails(A, "insert into public.case_draft_archive (user_id, case_id, draft_text) values (%s, %s, 'x')", (B, str(CB))))
as_user(B, "insert into public.case_draft_archive (case_id, draft_text) values (%s, 'B 보관')", (str(CB),))
check("A가 B 보관 초안 읽기 0건", as_user(A, "select count(*) from public.case_draft_archive where user_id = %s", (B,)) == [(0,)])
check("A가 B 보관 초안 수정 0건", as_user(A, "update public.case_draft_archive set draft_text = 'hack' where user_id = %s", (B,)) == 0)
check("A가 B 보관 초안 삭제 0건", as_user(A, "delete from public.case_draft_archive where user_id = %s", (B,)) == 0)
check("빈 글 보관 차단", fails(A, "update public.case_draft_archive set draft_text = '' where case_id = %s", (str(CA),)))
check("20000자 초과 차단", fails(A, "update public.case_draft_archive set draft_text = repeat('가', 20001) where case_id = %s", (str(CA),)))
try:
    with db.transaction():
        db.execute("set local role anon")
        db.execute("select count(*) from public.case_draft_archive")
    check("비로그인 읽기 거부", False)
except psycopg.Error:
    check("비로그인 읽기 거부", True)

# 사건 삭제·탈퇴
check("A가 본인 사건 삭제", as_user(A, "delete from public.cases where case_id = %s", (CA,)) == 1)
check("사건 삭제 시 보관 초안도 삭제", x("select count(*) from public.case_draft_archive where user_id = %s", (A,)).fetchone()[0] == 0)
x("delete from public.cases where user_id = %s", (B,)); x("delete from auth.users where id = %s", (B,))
check("탈퇴 후 B 보관 초안 0건", x("select count(*) from public.case_draft_archive where user_id = %s", (B,)).fetchone()[0] == 0)

# 되돌리기
x(open(f"{REPO}/supabase/rollbacks/20261023000000_case_draft_archive_rollback.sql").read())
check("되돌리기 후 표 없음", x("select to_regclass('public.case_draft_archive')").fetchone()[0] is None)
check("되돌리기 후 기존 초안 표는 그대로", x("select to_regclass('public.case_drafts')").fetchone()[0] is not None)

db.close(); conn.execute("drop database draft_archive_test"); conn.close()
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
sys.exit(1 if failed else 0)
