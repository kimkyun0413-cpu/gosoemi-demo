"""로컬 임시 Postgres에서 case_drafts 마이그레이션과 접근 정책을 검사한다. 운영 DB에는 접속하지 않는다.

필요: 로컬 PostgreSQL 서버(빈 서버, 슈퍼유저 접속)와 psycopg (pip install "psycopg[binary]")
실행: LOCAL_PG="host=127.0.0.1 port=54329 user=postgres" python3 supabase/tests/local_case_drafts_test.py
Supabase의 auth 스키마·역할·cases 정책을 흉내 낸 drafts_test DB를 새로 만들고 시험한다.
"""
import os, psycopg, uuid, sys
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PG = os.environ.get("LOCAL_PG", "host=127.0.0.1 port=54329 user=postgres")
if "supabase.co" in PG or "pooler" in PG:
    sys.exit("운영 DB 주소로는 실행하지 마세요. 로컬 임시 Postgres만 사용합니다.")
conn = psycopg.connect(PG + " dbname=postgres", autocommit=True)
conn.execute("drop database if exists drafts_test")
conn.execute("create database drafts_test encoding 'UTF8' template template0")
db = psycopg.connect(PG + " dbname=drafts_test client_encoding=UTF8", autocommit=True)
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
x(open(f"{REPO}/supabase/migrations/20261013000000_update_consent_privacy_version_v3.sql").read())
x("grant all on public.case_drafts to service_role")

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

check("A가 본인 사건 초안 저장", as_user(A, "insert into public.case_drafts (case_id, draft_text) values (%s, '초안 A')", (str(CA),)) == 1)
check("upsert로 본인 초안 수정", as_user(A, "insert into public.case_drafts (user_id, case_id, draft_text) values (%s, %s, '수정본') on conflict (user_id, case_id) do update set draft_text = excluded.draft_text", (A, str(CA))) == 1)
row = as_user(A, "select draft_text, updated_at >= created_at from public.case_drafts where case_id = %s", (str(CA),))
check("수정 내용·수정 시각 기록", row == [("수정본", True)])
check("A가 B의 사건에 초안 생성 차단", fails(A, "insert into public.case_drafts (case_id, draft_text) values (%s, 'x')", (str(CB),)))
check("A가 B 이름으로 초안 생성 차단", fails(A, "insert into public.case_drafts (user_id, case_id, draft_text) values (%s, %s, 'x')", (B, str(CB))))
check("없는 사건에 초안 생성 차단", fails(A, "insert into public.case_drafts (case_id, draft_text) values (%s, 'x')", (str(uuid.uuid4()),)))
as_user(B, "insert into public.case_drafts (case_id, draft_text) values (%s, '초안 B')", (str(CB),))
check("A가 B 초안 읽기 0건", as_user(A, "select count(*) from public.case_drafts where user_id = %s", (B,)) == [(0,)])
check("A가 B 초안 수정 0건", as_user(A, "update public.case_drafts set draft_text = 'hack' where user_id = %s", (B,)) == 0)
check("A가 B 초안 삭제 0건", as_user(A, "delete from public.case_drafts where user_id = %s", (B,)) == 0)
check("A가 본인 초안을 B 사건으로 옮기기 차단", fails(A, "update public.case_drafts set case_id = %s where user_id = %s", (str(CB), A)))
check("빈 초안 차단", fails(A, "update public.case_drafts set draft_text = '' where user_id = %s", (A,)))
check("20000자 초과 차단", fails(A, "update public.case_drafts set draft_text = repeat('가', 20001) where user_id = %s", (A,)))
try:
    with db.transaction():
        db.execute("set local role anon")
        db.execute("select count(*) from public.case_drafts")
    check("비로그인 읽기 거부", False)
except psycopg.Error:
    check("비로그인 읽기 거부", True)
# 사건 삭제 → 초안 삭제 (사용자 권한)
check("A가 본인 사건 삭제", as_user(A, "delete from public.cases where case_id = %s", (CA,)) == 1)
check("사건 삭제 시 초안도 삭제", x("select count(*) from public.case_drafts where user_id = %s", (A,)).fetchone()[0] == 0)
# 회원탈퇴 흐름 (Edge Function과 같은 순서: cases → consent_records → auth.users, service_role)
with db.transaction():
    db.execute("set local role service_role")
    db.execute("delete from public.cases where user_id = %s", (B,))
    db.execute("delete from public.consent_records where user_id = %s", (B,))
db.execute("delete from auth.users where id = %s", (B,))
check("탈퇴 후 B 초안 0건", x("select count(*) from public.case_drafts where user_id = %s", (B,)).fetchone()[0] == 0)
# 외래키 cascade만으로도 삭제되는지 (cases가 남아 있어도 사용자 삭제 시) - 별도 사용자
Cc, Cu = uuid.uuid4(), uuid.uuid4()
x("insert into auth.users values (%s)", (Cu,))
x("insert into public.cases (case_id, user_id) values (%s, %s)", (Cc, Cu))
as_user(Cu, "insert into public.case_drafts (case_id, draft_text) values (%s, 'c')", (str(Cc),))
x("delete from public.cases where user_id = %s", (Cu,)); x("delete from auth.users where id = %s", (Cu,))
check("사용자 삭제 시 초안 cascade", x("select count(*) from public.case_drafts").fetchone()[0] == 0)
# 동의 함수
D = uuid.uuid4(); x("insert into auth.users values (%s)", (D,))
check("동의 새 버전 저장", fails(D, "select public.record_consent('2026-09-15', '2026-10-13')") is False)
check("동의 이전 버전도 저장", fails(D, "select public.record_consent('2026-09-15', '2026-09-27')") is False)
check("동의 잘못된 버전 거부", fails(D, "select public.record_consent('2026-09-15', '2027-01-01')"))
check("동의 기록 2건", x("select count(*) from public.consent_records where user_id = %s", (D,)).fetchone()[0] == 2)
# 되돌리기
x(open(f"{REPO}/supabase/rollbacks/20261006000000_case_drafts_rollback.sql").read())
check("되돌리기 후 테이블·트리거 없음", x("select to_regclass('public.case_drafts') is null and not exists (select from pg_trigger where tgname='cases_delete_case_draft')").fetchone()[0])
check("되돌리기 후 사건 삭제 정상", x("delete from public.cases").rowcount >= 0)
print("\n%d/%d passed" % (sum(ok for _, ok in results), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
