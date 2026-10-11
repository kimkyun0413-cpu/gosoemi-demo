"""로컬 임시 Postgres에서 백업·복구 스크립트를 실제로 돌려 본다. 운영 DB에는 접속하지 않는다.

1) Supabase 비슷한 원본 DB(backup_src)에 앱 마이그레이션과 가짜 자료를 넣는다.
2) scripts/backup_supabase.sh로 암호화 백업을 만든다.
3) '새 프로젝트'처럼 auth 스키마·역할만 있는 빈 DB(backup_dst)에 scripts/restore_supabase.sh로 복구한다.
4) 표마다 행 수, 계정·사건 연결, 본인만 보는 정책(RLS)이 그대로인지 확인한다.

필요: 로컬 PostgreSQL 서버(빈 서버, 슈퍼유저 접속), 같은 버전 이상의 pg_dump·psql이 PATH에 있을 것, psycopg
실행: LOCAL_PG="host=127.0.0.1 port=54329 user=postgres" python3 supabase/tests/local_backup_restore_test.py
"""
import os
import subprocess
import sys
import tempfile
import uuid

import psycopg

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PG = os.environ.get("LOCAL_PG", "host=127.0.0.1 port=54329 user=postgres")
if "supabase.co" in PG or "pooler" in PG:
    sys.exit("운영 DB 주소로는 실행하지 마세요. 로컬 임시 Postgres만 사용합니다.")
params = dict(part.split("=", 1) for part in PG.split())
URL = f"postgresql://{params.get('user', 'postgres')}@{params.get('host', '127.0.0.1')}:{params.get('port', '5432')}"

BASE = """
do $$ begin
  if not exists (select from pg_roles where rolname='anon') then create role anon nologin; end if;
  if not exists (select from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
  if not exists (select from pg_roles where rolname='service_role') then create role service_role nologin bypassrls; end if;
end $$;
create schema auth;
create table auth.users (id uuid primary key, email text);
create table auth.identities (id uuid primary key, user_id uuid not null references auth.users(id) on delete cascade, provider text);
create function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claims', true)::json->>'sub', '')::uuid $$;
grant usage on schema auth to anon, authenticated, service_role;
grant execute on function auth.uid() to anon, authenticated, service_role;
grant usage on schema public to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
"""

APP = """
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
"""

results = []


def check(name, ok):
    results.append((name, bool(ok)))
    print(("통과" if ok else "실패"), name)


def fresh(name):
    admin = psycopg.connect(PG + " dbname=postgres", autocommit=True)
    admin.execute(f"drop database if exists {name}")
    admin.execute(f"create database {name} encoding 'UTF8' template template0")
    admin.close()
    db = psycopg.connect(PG + f" dbname={name} client_encoding=UTF8", autocommit=True)
    db.execute(BASE)
    return db


src = fresh("backup_src")
src.execute(APP)
for migration in ("20260924000000_naver_login.sql", "20261006000000_case_drafts.sql",
                  "20261012000000_case_workspace_and_case_edit.sql"):
    src.execute(open(f"{REPO}/supabase/migrations/{migration}", encoding="utf-8").read())

A, B = uuid.uuid4(), uuid.uuid4()
src.execute("insert into auth.users values (%s, 'a@example.invalid'), (%s, 'b@example.invalid')", (A, B))
src.execute("insert into auth.identities values (%s, %s, 'email'), (%s, %s, 'google')", (uuid.uuid4(), A, uuid.uuid4(), B))
cases = [uuid.uuid4() for _ in range(3)]
src.execute("insert into public.cases (case_id, user_id, case_type, incident_description, created_at) values "
            "(%s,%s,'시험','가짜 경위 1',now()),(%s,%s,'시험','가짜 경위 2',now()),(%s,%s,'시험','가짜 경위 3',now())",
            (cases[0], A, cases[1], A, cases[2], B))
src.execute("insert into public.case_drafts (case_id, user_id, draft_text) values (%s,%s,'가짜 초안')", (cases[0], A))
src.execute("insert into public.case_workspace (case_id, user_id, timeline) values (%s,%s,'[]'::jsonb)", (cases[1], A))
src.execute("insert into public.consent_records (user_id, terms_version, privacy_version) values (%s,'2026-09-15','2026-09-27')", (A,))
src.execute("insert into naver_private.naver_identities (naver_id, user_id) values ('fake-naver-id', %s)", (B,))


def counts(db):
    tables = ["auth.users", "auth.identities", "public.cases", "public.case_drafts", "public.case_workspace",
              "public.consent_records", "naver_private.naver_identities"]
    return {t: db.execute(f"select count(*) from {t}").fetchone()[0] for t in tables}


before = counts(src)
src.close()
dst = fresh("backup_dst")   # 새 프로젝트: auth·역할만 있고 앱 표는 없음
dst.close()

env = {**os.environ, "BACKUP_PASSPHRASE": "local-test-only-" + uuid.uuid4().hex}
with tempfile.TemporaryDirectory() as out:
    env["BACKUP_DIR"] = out
    env["SUPABASE_DB_URL"] = URL + "/backup_src"
    run = subprocess.run(["bash", f"{REPO}/scripts/backup_supabase.sh"], env=env, capture_output=True, text=True)
    check("백업 스크립트 성공", run.returncode == 0)
    if run.returncode != 0:
        print(run.stderr[-2000:])
    files = [f for f in os.listdir(out) if f.endswith(".enc")]
    check("암호화된 백업 파일 1개", len(files) == 1)
    raw = open(os.path.join(out, files[0]), "rb").read() if files else b""
    check("백업 파일에 자료가 평문으로 보이지 않음", b"\xea\xb0\x80\xec\xa7\x9c" not in raw and b"example.invalid" not in raw)

    env["TARGET_DB_URL"] = URL + "/backup_dst"
    run = subprocess.run(["bash", f"{REPO}/scripts/restore_supabase.sh", os.path.join(out, files[0])],
                         env=env, capture_output=True, text=True)
    check("복구 스크립트 성공", run.returncode == 0)
    if run.returncode != 0:
        print(run.stderr[-3000:])

    wrong = {**env, "BACKUP_PASSPHRASE": "wrong"}
    run = subprocess.run(["bash", f"{REPO}/scripts/restore_supabase.sh", os.path.join(out, files[0])],
                         env={**wrong, "TARGET_DB_URL": URL + "/backup_src"}, capture_output=True, text=True)
    check("암호가 틀리면 복구하지 않음", run.returncode != 0)
    run = subprocess.run(["bash", f"{REPO}/scripts/restore_supabase.sh", os.path.join(out, files[0])],
                         env={**env, "TARGET_DB_URL": "postgresql://x@db.hnaobxrfapsuiygggdgx.supabase.co/postgres"},
                         capture_output=True, text=True)
    check("운영 프로젝트 주소로는 복구하지 않음", run.returncode != 0 and "운영 프로젝트" in run.stderr)

dst = psycopg.connect(PG + " dbname=backup_dst client_encoding=UTF8", autocommit=True)
after = counts(dst)
for table, n in before.items():
    check(f"행 수 같음: {table} ({n})", after.get(table) == n)
check("사건-계정 연결 유지", dst.execute(
    "select count(*) from public.cases c join auth.users u on u.id = c.user_id").fetchone()[0] == before["public.cases"])
check("cases RLS 강제 유지", dst.execute(
    "select relrowsecurity and relforcerowsecurity from pg_class where oid='public.cases'::regclass").fetchone()[0])
check("case_workspace 정책 4개 유지", dst.execute(
    "select count(*) from pg_policies where tablename='case_workspace'").fetchone()[0] == 4)
dst.execute("set role authenticated")
dst.execute("select set_config('request.jwt.claims', %s, false)", (f'{{"sub":"{B}"}}',))
check("복구 후에도 B는 자기 사건 1건만 보임", dst.execute("select count(*) from public.cases").fetchone()[0] == 1)
dst.execute("reset role")
dst.close()

failed = [name for name, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
sys.exit(1 if failed else 0)
