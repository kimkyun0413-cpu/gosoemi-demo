"""로컬 임시 Postgres에서 이용 후기 마이그레이션(20261023000001)을 검사한다. 운영 DB에는 접속하지 않는다.

검사: 본인 후기만 보고·고치고·지움, 상태·게시 시각을 이용자가 못 정함, 공개 후기를 고치면 다시 확인 대기,
공개 목록은 게시된 것만·작성자 정보 없이, 운영자(review_admins)만 검토·게시, 운영자에게만 보낸 의견은 게시 불가,
계정당 20개, 탈퇴 시 삭제, 되돌리기.
필요: 로컬 PostgreSQL 서버(빈 서버, 슈퍼유저 접속)와 psycopg (pip install "psycopg[binary]")
실행: LOCAL_PG="host=127.0.0.1 port=54329 user=postgres" python3 supabase/tests/local_service_reviews_test.py
"""
import os, psycopg, uuid, sys
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PG = os.environ.get("LOCAL_PG", "host=127.0.0.1 port=54329 user=postgres")
if "supabase.co" in PG or "pooler" in PG:
    sys.exit("운영 DB 주소로는 실행하지 마세요. 로컬 임시 Postgres만 사용합니다.")
conn = psycopg.connect(PG + " dbname=postgres", autocommit=True)
conn.execute("drop database if exists service_reviews_test")
conn.execute("create database service_reviews_test encoding 'UTF8' template template0")
db = psycopg.connect(PG + " dbname=service_reviews_test client_encoding=UTF8", autocommit=True)
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
""")
x(open(f"{REPO}/supabase/migrations/20261023000001_service_reviews.sql").read())

A, B, OP = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
x("insert into auth.users values (%s),(%s),(%s)", (A, B, OP))
x("insert into public.review_admins values (%s)", (OP,))

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

# 쓰기: 상태는 공개 방식으로만 정해진다
rid = as_user(A, "insert into public.service_reviews (user_id, rating, helpful, visibility, status, published_at) "
              "values (%s, 5, '좋았어요', 'public', 'published', now()) returning review_id, status, published_at", (A,))
check("공개 후기를 '게시됨'으로 넣으려 해도 확인 대기로 저장", rid[0][1] == "pending" and rid[0][2] is None)
RA = rid[0][0]
op_row = as_user(A, "insert into public.service_reviews (rating, improve, visibility) values (3, '어려웠어요', 'operator') returning status", None)
check("운영자에게만 보내기는 'private'", op_row == [("private",)])
check("별점 0 차단", fails(A, "insert into public.service_reviews (rating, visibility) values (0, 'public')"))
check("501자 차단", fails(A, "insert into public.service_reviews (rating, helpful, visibility) values (5, repeat('가', 501), 'public')"))
check("다른 사람 이름으로 쓰기 차단", fails(A, "insert into public.service_reviews (user_id, rating, visibility) values (%s, 5, 'public')", (B,)))
check("상태를 직접 바꿀 수 없음", fails(A, "update public.service_reviews set status = 'published' where review_id = %s", (RA,)))
check("게시 시각을 직접 바꿀 수 없음", fails(A, "update public.service_reviews set published_at = now() where review_id = %s", (RA,)))

# 공개 목록·운영자
check("확인 전에는 공개 목록에 없음", as_user(B, "select count(*) from public.published_reviews()") == [(0,)])
check("운영자가 아니면 검토 목록 0건", as_user(B, "select count(*) from public.reviews_for_moderation('pending')") == [(0,)])
check("운영자가 아니면 게시 거부", fails(B, "select public.moderate_review(%s, 'publish')", (RA,)))
check("운영자는 검토 목록에서 봄", as_user(OP, "select count(*) from public.reviews_for_moderation('pending')") == [(1,)])
cols = as_user(OP, "select * from public.reviews_for_moderation('pending')")
check("검토 목록에 작성자 정보 없음", all(str(A) not in [str(v) for v in r] for r in cols))
check("운영자 게시", as_user(OP, "select public.moderate_review(%s, 'publish')", (RA,)) == [(1,)])
pub = as_user(B, "select * from public.published_reviews()")
check("게시 뒤 다른 이용자도 공개 목록에서 봄(작성자 정보 없이)", len(pub) == 1 and str(A) not in [str(v) for v in pub[0]])
check("다른 이용자는 원본 표에서 남의 후기 0건", as_user(B, "select count(*) from public.service_reviews where user_id = %s", (A,)) == [(0,)])
priv = x("select review_id from public.service_reviews where visibility = 'operator'").fetchone()[0]
check("운영자에게만 보낸 의견은 게시 0건", as_user(OP, "select public.moderate_review(%s, 'publish')", (priv,)) == [(0,)])

# 고치면 다시 확인 대기
upd = as_user(A, "update public.service_reviews set helpful = '많이 좋았어요' where review_id = %s returning status, published_at", (RA,))
check("게시된 후기를 고치면 다시 확인 대기·공개 내려감", upd == [("pending", None)])
check("고친 뒤 공개 목록에서 빠짐", as_user(B, "select count(*) from public.published_reviews()") == [(0,)])
as_user(OP, "select public.moderate_review(%s, 'reject', '개인정보가 들어 있음')", (RA,))
check("반려 이유가 작성자에게 보임", as_user(A, "select status, reject_reason from public.service_reviews where review_id = %s", (RA,))
      == [("rejected", "개인정보가 들어 있음")])
check("남의 후기 고치기 0건", as_user(B, "update public.service_reviews set helpful = 'x' where review_id = %s", (RA,)) == 0)
check("남의 후기 지우기 0건", as_user(B, "delete from public.service_reviews where review_id = %s", (RA,)) == 0)
check("이용자는 운영자 목록을 볼 수 없음", fails(A, "select count(*) from public.review_admins"))
try:
    with db.transaction():
        db.execute("set local role anon")
        db.execute("select count(*) from public.published_reviews()")
    check("비로그인은 공개 목록 함수도 못 부름", False)
except psycopg.Error:
    check("비로그인은 공개 목록 함수도 못 부름", True)

# 계정당 20개
for _ in range(18):
    as_user(A, "insert into public.service_reviews (rating, visibility) values (4, 'operator')")
check("21번째 후기 차단", fails(A, "insert into public.service_reviews (rating, visibility) values (4, 'operator')"))
check("본인 후기 지우기", as_user(A, "delete from public.service_reviews where review_id = %s", (RA,)) == 1)

# 탈퇴
x("delete from auth.users where id = %s", (A,))
check("탈퇴 시 후기 모두 삭제", x("select count(*) from public.service_reviews where user_id = %s", (A,)).fetchone()[0] == 0)

# 되돌리기
x(open(f"{REPO}/supabase/rollbacks/20261023000001_service_reviews_rollback.sql").read())
check("되돌리기 후 표·함수 없음", x("select to_regclass('public.service_reviews'), to_regclass('public.review_admins'), "
                              "to_regprocedure('public.published_reviews(integer,integer)')").fetchone() == (None, None, None))

db.close(); conn.execute("drop database service_reviews_test"); conn.close()
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
sys.exit(1 if failed else 0)
