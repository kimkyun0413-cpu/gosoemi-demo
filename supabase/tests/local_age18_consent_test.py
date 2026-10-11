"""로컬 임시 Postgres에서 만 18세 기준 동의 함수 수정안(supabase/proposals/age18/)을 검사한다. 운영 DB에는 접속하지 않는다.

검사: 새 약관 버전 동의 기록, 옛 약관 버전·다른 방침 버전 거부, 비로그인 거부, 기존 동의 기록 유지, 되돌리기.
실행: LOCAL_PG="host=127.0.0.1 port=54329 user=postgres" python3 supabase/tests/local_age18_consent_test.py
"""
import os
import sys
import uuid

import psycopg

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PG = os.environ.get("LOCAL_PG", "host=127.0.0.1 port=54329 user=postgres")
if "supabase.co" in PG or "pooler" in PG:
    sys.exit("운영 DB 주소로는 실행하지 마세요. 로컬 임시 Postgres만 사용합니다.")
TERMS_V2, PRIVACY_V3 = "2026-11-01", "2026-11-01"   # 시험용 날짜(실제 날짜는 운영자가 정함)

admin = psycopg.connect(PG + " dbname=postgres", autocommit=True)
admin.execute("drop database if exists age18_test")
admin.execute("create database age18_test encoding 'UTF8' template template0")
db = psycopg.connect(PG + " dbname=age18_test client_encoding=UTF8", autocommit=True)
x = db.execute
x("""
do $$ begin
  if not exists (select from pg_roles where rolname='anon') then create role anon nologin; end if;
  if not exists (select from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
end $$;
create schema auth;
create table auth.users (id uuid primary key);
create function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claims', true)::json->>'sub', '')::uuid $$;
grant usage on schema auth to anon, authenticated;
grant execute on function auth.uid() to anon, authenticated;
grant usage on schema public to anon, authenticated;
create table public.consent_records (user_id uuid references auth.users(id), terms_version text, privacy_version text,
  created_at timestamptz default now(), unique (user_id, terms_version, privacy_version));
""")
x(open(f"{REPO}/supabase/migrations/20261013000000_update_consent_privacy_version_v3.sql", encoding="utf-8").read())
A = uuid.uuid4()
x("insert into auth.users values (%s)", (A,))
x("insert into public.consent_records (user_id, terms_version, privacy_version) values (%s, '2026-09-15', '2026-09-27')", (A,))

results = []


def check(name, ok):
    results.append(bool(ok))
    print(("통과" if ok else "실패"), name)


def call(uid, terms, privacy):
    try:
        x("begin")
        x("set local role authenticated")
        if uid:
            x("select set_config('request.jwt.claims', %s, true)", (f'{{"sub":"{uid}"}}',))
        x("select public.record_consent(%s, %s)", (terms, privacy))
        x("commit")
        return True
    except Exception:
        x("rollback")
        return False


proposal = open(f"{REPO}/supabase/proposals/age18/record_consent_terms_v2.sql", encoding="utf-8").read()
check("수정안에 날짜 자리표시가 있어 그대로는 적용되지 않음", "__TERMS_V2_DATE__" in proposal)
x(proposal.replace("__TERMS_V2_DATE__", TERMS_V2).replace("__PRIVACY_V3_DATE__", PRIVACY_V3))
check("새 약관 버전 동의 기록", call(A, TERMS_V2, PRIVACY_V3))
check("옛 약관 버전은 거부", not call(A, "2026-09-15", "2026-09-27"))
check("다른 방침 버전은 거부", not call(A, TERMS_V2, "2026-09-27"))
check("비로그인은 거부", not call(None, TERMS_V2, PRIVACY_V3))
rows = x("select terms_version from public.consent_records where user_id=%s order by 1", (A,)).fetchall()
check("기존 동의 기록 유지 + 새 기록 추가", [r[0] for r in rows] == ["2026-09-15", TERMS_V2])
check("anon은 함수 실행 권한 없음", not x(
    "select has_function_privilege('anon', 'public.record_consent(text,text)', 'execute')").fetchone()[0])
x(open(f"{REPO}/supabase/proposals/age18/record_consent_terms_v2_rollback.sql", encoding="utf-8").read())
check("되돌리기 후 옛 약관 버전 다시 받음", call(A, "2026-09-15", "2026-10-13"))
check("되돌리기 후 새 약관 버전 거부", not call(A, TERMS_V2, PRIVACY_V3))
check("되돌려도 기록은 지우지 않음", x("select count(*) from public.consent_records").fetchone()[0] == 3)
db.close()
admin.execute("drop database age18_test")
print(f"\n{sum(results)}/{len(results)} 통과")
sys.exit(0 if all(results) else 1)
