// 실행: deno test supabase/proposals/billing/functions/billing-webhook/handler_test.ts
import { handle, mockAdapter, sha256Hex, type VerifiedEvent } from "./handler.ts";

const SECRET = "test-only-secret";
const ORDER = "11111111-2222-4333-8444-555555555555";

async function sign(body: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function request(body: string, signature: string, method = "POST"): Request {
  return new Request("http://localhost/billing-webhook", {
    method, body: method === "POST" ? body : undefined, headers: { "x-billing-signature": signature },
  });
}

// DB 함수 흉내: 이벤트 중복과 금액 불일치를 DB처럼 판단한다.
function fakeDb(orderAmount: number) {
  const seen = new Set<string>();
  const calls: Array<[VerifiedEvent, string]> = [];
  return {
    calls,
    apply: async (event: VerifiedEvent, hash: string) => {
      calls.push([event, hash]);
      if (seen.has(event.eventId)) return "duplicate";
      seen.add(event.eventId);
      return event.amount === orderAmount ? "applied" : "amount_mismatch";
    },
  };
}

function assertEquals(actual: unknown, expected: unknown) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)} but got ${JSON.stringify(actual)}`);
  }
}

const success = JSON.stringify({ event_id: "evt-1", kind: "payment_succeeded", order_id: ORDER, payment_id: "pay-1", amount: 1000 });

Deno.test("서명이 맞으면 반영하고 원문 해시를 넘긴다", async () => {
  const db = fakeDb(1000);
  const res = await handle(request(success, await sign(success)), mockAdapter(SECRET), db.apply);
  assertEquals(res.status, 200);
  assertEquals(await res.json(), { result: "applied" });
  assertEquals(db.calls[0][1], await sha256Hex(success));
});

Deno.test("서명이 틀리면 401이고 DB를 부르지 않는다", async () => {
  const db = fakeDb(1000);
  const res = await handle(request(success, "0".repeat(64)), mockAdapter(SECRET), db.apply);
  assertEquals(res.status, 401);
  assertEquals(db.calls.length, 0);
});

Deno.test("같은 이벤트를 다시 받으면 duplicate로 200", async () => {
  const db = fakeDb(1000);
  const sig = await sign(success);
  await handle(request(success, sig), mockAdapter(SECRET), db.apply);
  const res = await handle(request(success, sig), mockAdapter(SECRET), db.apply);
  assertEquals(await res.json(), { result: "duplicate" });
});

Deno.test("금액이 다르면 amount_mismatch", async () => {
  const db = fakeDb(5000);
  const res = await handle(request(success, await sign(success)), mockAdapter(SECRET), db.apply);
  assertEquals(await res.json(), { result: "amount_mismatch" });
});

Deno.test("형식이 잘못된 이벤트는 400", async () => {
  const bad = JSON.stringify({ event_id: "e", kind: "free_money", order_id: ORDER, payment_id: "p", amount: 1 });
  const res = await handle(request(bad, await sign(bad)), mockAdapter(SECRET), fakeDb(1).apply);
  assertEquals(res.status, 400);
});

Deno.test("결제가 꺼져 있으면 503", async () => {
  const res = await handle(request(success, await sign(success)), null, fakeDb(1000).apply);
  assertEquals(res.status, 503);
});

Deno.test("DB 오류면 500으로 재전송 유도", async () => {
  const res = await handle(request(success, await sign(success)), mockAdapter(SECRET), () => Promise.reject(new Error("db")));
  assertEquals(res.status, 500);
});

Deno.test("POST가 아니면 405", async () => {
  const res = await handle(request("", "", "GET"), mockAdapter(SECRET), fakeDb(1).apply);
  assertEquals(res.status, 405);
});
