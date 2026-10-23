"""운영 반영 리허설: supabase/release/2026-10-24_* 묶음을 Supabase와 비슷한 로컬 임시 Postgres에서 순서대로 실행한다.
운영 DB에는 접속하지 않는다.

순서: 기존 구조(사건·초안·작업 공간)와 예시 자료 → 적용 전 점검(ok 모두 true) → 적용 SQL(슈퍼유저가 아닌 postgres 비슷한 역할)
→ 운영자 지정 → 적용 후 점검(ok 모두 true) → 기존 자료 개수·내용 그대로 → 새 기능 동작 → 되돌리기 → 기존 자료 그대로.
Supabase처럼 public의 새 표·함수에 anon·authenticated 기본 권한이 붙는 상태에서 시험한다.
실행: LOCAL_PG="host=127.0.0.1 port=54329 user=postgres" python3 supabase/tests/local_release_design_a_test.py
"""
import os, psycopg, uuid, sys
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PG = os.environ.get("LOCAL_PG", "host=127.0.0.1 port=54329 user=postgres")
if "supabase.co" in PG or "pooler" in PG:
    sys.exit("운영 DB 주소로는 실행하지 마세요. 로컬 임시 Postgres만 사용합니다.")
read = lambda p: open(os.path.join(REPO, p), encoding="utf-8").read()  # noqa: E731
conn = psycopg.connect(PG + " dbname=postgres", autocommit=True)
conn.execute("drop database if exists release_design_a_test")
conn.execute("create database release_design_a_test encoding 'UTF8' template template0")
db = psycopg.connect(PG + " dbname=release_design_a_test client_encoding=UTF8", autocommit=True)
x = db.execute
results = []
def check(name, ok):
    results.append((name, ok)); print(("PASS " if ok else "FAIL ") + name)

# ── Supabase 비슷한 기본 구성: 역할, auth, 'supa' = SQL Editor의 postgres(슈퍼유저 아님, RLS 건너뜀) ──
x("""
do $$ begin
  if not exists (select from pg_roles where rolname='anon') then create role anon nologin; end if;
  if not exists (select from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
  if not exists (select from pg_roles where rolname='service_role') then create role service_role nologin bypassrls; end if;
  if not exists (select from pg_roles where rolname='supa') then create role supa nologin nosuperuser bypassrls; end if;
end $$;
create schema auth;
create table auth.users (id uuid primary key);
create function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claims', true)::json->>'sub', '')::uuid $$;
grant usage on schema auth to anon, authenticated, service_role, supa;
grant execute on function auth.uid() to anon, authenticated, service_role, supa;
grant references, select on auth.users to supa;
grant usage, create on schema public to supa;
grant usage on schema public to anon, authenticated, service_role;
grant anon, authenticated to supa;
alter default privileges for role supa in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges for role supa in schema public grant all on functions to anon, authenticated, service_role;
""")
def as_supa(sql, params=None):
    with db.transaction():
        x("set local role supa")
        cur = x(sql, params)
        return cur.fetchall() if cur.description else cur.rowcount
as_supa("""
create table public.cases (
  case_id uuid primary key, user_id uuid not null references auth.users(id),
  original_request text, case_type text, reporter_info text, suspect_info text, incident_datetime text,
  incident_location text, incident_description text, damage_details text, evidence text, witness text,
  punishment_intent text, created_at timestamptz);
alter table public.cases enable row level security; alter table public.cases force row level security;
revoke all on public.cases from anon, authenticated;
grant select, insert, delete on public.cases to authenticated;
create policy s on public.cases for select to authenticated using (auth.uid() = user_id);
create policy i on public.cases for insert to authenticated with check (auth.uid() = user_id);
create policy d on public.cases for delete to authenticated using (auth.uid() = user_id);
create table public.consent_records (user_id uuid references auth.users(id), terms_version text, privacy_version text,
  created_at timestamptz default now());
""")
as_supa(read("supabase/migrations/20261006000000_case_drafts.sql"))
as_supa(read("supabase/migrations/20261012000000_case_workspace_and_case_edit.sql"))

# 예시 자료(가상): 이용자 A·B·운영자 OP, 사건·초안·작업 공간
A, B, OP = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
CA, CB = uuid.uuid4(), uuid.uuid4()
x("insert into auth.users values (%s),(%s),(%s)", (A, B, OP))
x("insert into public.cases (case_id, user_id, case_type, incident_description) values (%s,%s,'a','A 경위(가상)'),(%s,%s,'b','B 경위(가상)')",
  (CA, A, CB, B))
x("insert into public.case_drafts (user_id, case_id, draft_text) values (%s,%s,'A 초안(가상)'),(%s,%s,'B 초안(가상)')", (A, str(CA), B, str(CB)))
x("insert into public.case_workspace (user_id, case_id, checklist) values (%s,%s,'{\"story\": true}')", (A, str(CA)))
snapshot_sql = ("select (select string_agg(case_id::text || incident_description, ',' order by case_id) from public.cases),"
                " (select string_agg(case_id || draft_text, ',' order by case_id) from public.case_drafts),"
                " (select string_agg(case_id || checklist::text, ',' order by case_id) from public.case_workspace)")
before = x(snapshot_sql).fetchone()

def run_checks(path):
    with db.transaction():
        x("set local role supa")
        cur = db.cursor()
        cur.execute(read(path))
        first = cur.fetchall()
        cur.nextset()
        counts = cur.fetchone()
    return first, counts

pre, pre_counts = run_checks("supabase/checks/preflight_design_a.sql")
check(f"적용 전 점검 {sum(r[4] for r in pre)}/{len(pre)} ok", all(r[4] for r in pre))
for r in pre:
    if not r[4]: print("   ", r)

as_supa(read("supabase/release/2026-10-24_design_a_apply.sql").replace("begin;", "", 1).replace("\ncommit;\n", "\n"))
check("적용 SQL 실행", x("select to_regclass('public.service_reviews') is not null").fetchone()[0])
admin_sql = read("supabase/release/2026-10-24_review_admin.sql").replace("<운영자 user id>", str(OP))
check("운영자 지정 결과 1줄", as_supa(admin_sql) == [(OP,)])
try:
    as_supa(read("supabase/release/2026-10-24_review_admin.sql"))
    check("ID를 바꾸지 않은 운영자 지정은 멈춤", False)
except psycopg.Error:
    check("ID를 바꾸지 않은 운영자 지정은 멈춤", True)

post, post_counts = run_checks("supabase/checks/postapply_design_a.sql")
check(f"적용 후 점검 {sum(r[4] for r in post)}/{len(post)} ok", all(r[4] for r in post))
for r in post:
    if not r[4]: print("   ", r)
check("사건·초안·작업 공간·계정 개수 그대로", tuple(post_counts[:4]) == tuple(pre_counts) and post_counts[4:] == (0, 0))
check("기존 자료 내용 그대로", x(snapshot_sql).fetchone() == before)

def as_user(uid, sql, params=None):
    with db.transaction():
        x("set local role authenticated")
        x("select set_config('request.jwt.claims', %s, true)", (f'{{"sub":"{uid}","role":"authenticated"}}',))
        cur = x(sql, params)
        return cur.fetchall() if cur.description else cur.rowcount

# 새 기능이 기본 권한(Supabase 기본값) 아래에서도 의도대로 막히는지
as_user(A, "insert into public.case_draft_archive (case_id, draft_text) values (%s, 'A 이전 초안(가상)')", (str(CA),))
check("B는 A의 이전 초안 0건", as_user(B, "select count(*) from public.case_draft_archive") == [(0,)])
rid = as_user(A, "insert into public.service_reviews (rating, helpful, visibility) values (5, '좋았어요', 'public') returning review_id")[0][0]
check("B는 A의 후기 0건", as_user(B, "select count(*) from public.service_reviews") == [(0,)])
try:
    as_user(B, "select public.moderate_review(%s, 'publish')", (rid,)); check("일반 이용자 게시 승인 거부", False)
except psycopg.Error:
    check("일반 이용자 게시 승인 거부", True)
check("운영자 게시", as_user(OP, "select public.moderate_review(%s, 'publish')", (rid,)) == [(1,)])
check("다른 이용자에게 공개(작성자 정보 없이)", as_user(B, "select rating, helpful from public.published_reviews()") == [(5, "좋았어요")])
try:
    with db.transaction():
        x("set local role anon")
        x("select * from public.published_reviews()")
    check("비로그인은 후기 함수 못 부름", False)
except psycopg.Error:
    check("비로그인은 후기 함수 못 부름", True)
check("A가 사건 삭제 → 이전 초안 정리", as_user(A, "delete from public.cases where case_id = %s", (CA,)) == 1
      and x("select count(*) from public.case_draft_archive").fetchone()[0] == 0)
x("insert into public.cases (case_id, user_id, case_type, incident_description) values (%s,%s,'a','A 경위(가상)')", (CA, A))
x("insert into public.case_drafts (user_id, case_id, draft_text) values (%s,%s,'A 초안(가상)')", (A, str(CA)))
x("insert into public.case_workspace (user_id, case_id, checklist) values (%s,%s,'{\"story\": true}')", (A, str(CA)))

# 되돌리기
as_supa(read("supabase/release/2026-10-24_design_a_rollback.sql").replace("begin;", "", 1).replace("\ncommit;\n", "\n"))
gone = x("select to_regclass('public.case_draft_archive'), to_regclass('public.service_reviews'), to_regclass('public.review_admins')").fetchone()
check("되돌리기 후 새 표 없음", gone == (None, None, None))
check("되돌리기 후 기존 자료 내용 그대로", x(snapshot_sql).fetchone() == before)
check("되돌리기 후 기존 사건 삭제 트리거 2개 그대로",
      x("select count(*) from pg_trigger where tgname in ('cases_delete_case_draft','cases_delete_case_workspace')").fetchone()[0] == 2)

db.close(); conn.execute("drop database release_design_a_test"); conn.close()
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
sys.exit(1 if failed else 0)
