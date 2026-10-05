"""로컬 임시 Postgres에서 결제 구조 제안(supabase/proposals/billing)을 검사한다. 운영 DB에는 접속하지 않는다.

실행: LOCAL_PG="host=127.0.0.1 port=54329 user=postgres" python3 supabase/tests/local_billing_test.py
"""
import os, sys, uuid
import psycopg

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PG = os.environ.get("LOCAL_PG", "host=127.0.0.1 port=54329 user=postgres")
if "supabase.co" in PG or "pooler" in PG:
    sys.exit("운영 DB 주소로는 실행하지 마세요. 로컬 임시 Postgres만 사용합니다.")

admin = psycopg.connect(PG + " dbname=postgres", autocommit=True)
admin.execute("drop database if exists billing_test")
admin.execute("create database billing_test encoding 'UTF8' template template0")
db = psycopg.connect(PG + " dbname=billing_test client_encoding=UTF8", autocommit=True)
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
grant usage on schema public to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
""")
x(open(f"{REPO}/supabase/proposals/billing/20261007000000_billing.sql").read())
x("insert into public.billing_plans values ('basic', '테스트 1회권', 'one_time', 1000, 'KRW', 30, true), "
  "('monthly', '테스트 월간', 'subscription', 3000, 'KRW', 30, true), ('hidden', '비공개', 'one_time', 500, 'KRW', 30, false)")

A, B = uuid.uuid4(), uuid.uuid4()
x("insert into auth.users values (%s), (%s)", (A, B))
results = []


def check(name, ok):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name)


def as_role(role, uid, sql, params=None):
    with db.transaction():
        db.execute(f"set local role {role}")
        db.execute("select set_config('request.jwt.claims', %s, true)", (f'{{"sub":"{uid or ""}"}}',))
        cur = db.execute(sql, params)
        return cur.fetchall() if cur.description else cur.rowcount


def fails(role, uid, sql, params=None):
    try:
        as_role(role, uid, sql, params)
        return False
    except psycopg.Error:
        return True


def event(kind, order_id, amount, event_id=None, payment_id=None):
    return as_role("service_role", None,
                   "select public.billing_apply_event('mock', %s, %s, %s, %s, %s, %s)",
                   (event_id or str(uuid.uuid4()), "0" * 64, kind, order_id, payment_id or str(uuid.uuid4()), amount))[0][0]


order = as_role("authenticated", A, "select order_id, amount, status from public.billing_create_order('basic', 'key-0001')")[0]
check("주문 생성: 금액은 요금제 표에서 (1000원, pending)", order[1] == 1000 and order[2] == "pending")
again = as_role("authenticated", A, "select order_id from public.billing_create_order('basic', 'key-0001')")[0]
check("같은 멱등 키로 다시 생성하면 같은 주문", again[0] == order[0])
check("같은 키로 다른 요금제 주문 거부", fails("authenticated", A, "select public.billing_create_order('monthly', 'key-0001')"))
check("비공개 요금제 주문 거부", fails("authenticated", A, "select public.billing_create_order('hidden', 'key-0002')"))
check("비로그인 주문 거부", fails("anon", None, "select public.billing_create_order('basic', 'key-0003')"))
check("이용자가 직접 주문 상태 변경 불가", fails("authenticated", A, "update public.billing_orders set status = 'paid'"))
check("이용자가 결제 반영 함수 호출 불가",
      fails("authenticated", A, "select public.billing_apply_event('mock','e','" + "0" * 64 + "','payment_succeeded',%s,'p',1000)", (order[0],)))
check("결제 전 이용권 없음", as_role("authenticated", A, "select public.billing_has_entitlement('basic')") == [(False,)])
check("금액이 다르면 거부", event("payment_succeeded", order[0], 999) == "amount_mismatch")
check("금액 불일치 주문은 failed", x("select status from public.billing_orders where order_id=%s", (order[0],)).fetchone()[0] == "failed")

order2 = as_role("authenticated", A, "select order_id from public.billing_create_order('basic', 'key-0004')")[0][0]
check("결제 성공 반영", event("payment_succeeded", order2, 1000, event_id="evt-1") == "applied")
check("같은 이벤트 재수신은 중복 처리", event("payment_succeeded", order2, 1000, event_id="evt-1") == "duplicate")
check("다른 이벤트로 같은 주문 재성공은 무시", event("payment_succeeded", order2, 1000) == "ignored")
check("결제 후 이용권 있음", as_role("authenticated", A, "select public.billing_has_entitlement('basic')") == [(True,)])
check("B는 A의 주문을 볼 수 없음", as_role("authenticated", B, "select count(*) from public.billing_orders") == [(0,)])
check("B에게는 이용권 없음", as_role("authenticated", B, "select public.billing_has_entitlement('basic')") == [(False,)])

order3 = as_role("authenticated", B, "select order_id from public.billing_create_order('basic', 'key-0005')")[0][0]
check("결제 실패 반영", event("payment_failed", order3, 1000) == "applied")
order4 = as_role("authenticated", B, "select order_id from public.billing_create_order('basic', 'key-0006')")[0][0]
check("결제 취소 반영", event("payment_cancelled", order4, 1000) == "applied")
check("실패·취소 후 이용권 없음", as_role("authenticated", B, "select public.billing_has_entitlement('basic')") == [(False,)])

check("환불 반영", event("refunded", order2, 1000) == "applied")
check("환불 후 이용권 종료", as_role("authenticated", A, "select public.billing_has_entitlement('basic')") == [(False,)])

sub = as_role("authenticated", B, "select order_id from public.billing_create_order('monthly', 'key-0007')")[0][0]
event("payment_succeeded", sub, 3000)
check("구독 시작 active", x("select status from public.billing_subscriptions where user_id=%s", (B,)).fetchone()[0] == "active")
check("갱신 실패 → past_due, 이용권은 유지",
      event("renewal_failed", sub, 3000) == "applied"
      and x("select status from public.billing_subscriptions where user_id=%s", (B,)).fetchone()[0] == "past_due"
      and as_role("authenticated", B, "select public.billing_has_entitlement('monthly')") == [(True,)])
check("해지 → 기간 끝까지 이용, 다음 갱신 안 함",
      event("subscription_cancelled", sub, 3000) == "applied"
      and x("select cancel_at_period_end from public.billing_subscriptions where user_id=%s", (B,)).fetchone()[0] is True)
check("만료 → 이용권 종료",
      event("subscription_expired", sub, 3000) == "applied"
      and as_role("authenticated", B, "select public.billing_has_entitlement('monthly')") == [(False,)])

kept = as_role("service_role", None, "select public.billing_retain_orders_for_user(%s)", (B,))[0][0]
x("delete from auth.users where id = %s", (B,))
check("탈퇴 전 결제 기록 별도 보존(결제·환불 건)", kept == 1)
check("탈퇴 후 주문은 공개 표에서 삭제, 보존 기록은 남음",
      x("select count(*) from public.billing_orders where user_id=%s", (B,)).fetchone()[0] == 0
      and x("select count(*) from billing_private.retained_orders").fetchone()[0] == 1)
check("보존 기한 5년", x("select bool_and(retain_until > now() + interval '4 years 11 months') from billing_private.retained_orders").fetchone()[0])
check("이용자는 보존 기록에 접근 불가", fails("authenticated", A, "select count(*) from billing_private.retained_orders"))

x(open(f"{REPO}/supabase/proposals/billing/20261007000000_billing_rollback.sql").read())
check("되돌리기 후 결제 표 없음", x("select to_regclass('public.billing_orders') is null").fetchone()[0])
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
