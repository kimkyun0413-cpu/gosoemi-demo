// [제안 · 미배포] 결제 결과 수신 처리
//
// 결제사(PG)는 아직 정하지 않았다. 결제사마다 다른 부분(서명 검증, 결제 승인 API 재확인, 이벤트 형식)은
// ProviderAdapter로 분리하고, 지금은 시험용 mock 어댑터만 있다. 실제 결제사를 정하면 어댑터를 추가한다.
//
// 처리 순서
// 1. 원문(body)을 그대로 받아 결제사 서명을 검증한다. 실패하면 401.
// 2. 어댑터가 결제사 API로 결제 상태·금액을 다시 확인한 결과(VerifiedEvent)를 만든다.
//    클라이언트가 보낸 금액이나 성공 여부는 믿지 않는다.
// 3. DB 함수 billing_apply_event()에 넘긴다. 같은 이벤트 재수신, 금액 불일치, 이미 처리된 주문은 DB가 막는다.
// 로그에는 단계와 결과 코드만 남기고 결제 원문·카드 정보·이메일은 남기지 않는다.

export type EventKind =
  | "payment_succeeded" | "payment_failed" | "payment_cancelled" | "refunded"
  | "renewal_succeeded" | "renewal_failed" | "subscription_cancelled" | "subscription_expired";

export interface VerifiedEvent {
  provider: string;
  eventId: string;
  kind: EventKind;
  orderId: string;
  providerPaymentId: string;
  amount: number;
  reason?: string;
}

export interface ProviderAdapter {
  name: string;
  verify(req: Request, body: string): Promise<VerifiedEvent | null>;
}

export type ApplyEvent = (event: VerifiedEvent, payloadSha256: string) => Promise<string>;

const KINDS = new Set<EventKind>([
  "payment_succeeded", "payment_failed", "payment_cancelled", "refunded",
  "renewal_succeeded", "renewal_failed", "subscription_cancelled", "subscription_expired",
]);
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function hmacHex(secret: string, text: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(text));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// 시험 전용. 운영(BILLING_MODE=live)에서는 쓸 수 없다.
export function mockAdapter(secret: string): ProviderAdapter {
  return {
    name: "mock",
    async verify(req, body) {
      const signature = req.headers.get("x-billing-signature") ?? "";
      if (!secret || !timingSafeEqual(signature, await hmacHex(secret, body))) return null;
      const data = JSON.parse(body);
      if (!KINDS.has(data.kind) || !UUID_RE.test(String(data.order_id)) || !Number.isInteger(data.amount)) {
        throw new Error("invalid_event");
      }
      return {
        provider: "mock",
        eventId: String(data.event_id),
        kind: data.kind,
        orderId: String(data.order_id),
        providerPaymentId: String(data.payment_id),
        amount: data.amount,
        reason: data.reason ? String(data.reason).slice(0, 200) : undefined,
      };
    },
  };
}

export async function handle(
  req: Request,
  adapter: ProviderAdapter | null,
  applyEvent: ApplyEvent,
): Promise<Response> {
  if (req.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
  if (!adapter) return new Response("Billing disabled", { status: 503 });
  const body = await req.text();
  if (body.length > 64 * 1024) return new Response("Payload Too Large", { status: 413 });

  let event: VerifiedEvent | null;
  try {
    event = await adapter.verify(req, body);
  } catch {
    console.error("[billing-webhook] stage=verify result=invalid_event");
    return new Response("Bad Request", { status: 400 });
  }
  if (!event) {
    console.error("[billing-webhook] stage=verify result=bad_signature");
    return new Response("Unauthorized", { status: 401 });
  }

  try {
    const result = await applyEvent(event, await sha256Hex(body));
    console.log(`[billing-webhook] stage=apply kind=${event.kind} result=${result}`);
    // duplicate·ignored도 200으로 답해 결제사가 같은 이벤트를 계속 다시 보내지 않게 한다.
    return new Response(JSON.stringify({ result }), {
      status: 200,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    console.error("[billing-webhook] stage=apply result=error");
    // 500이면 결제사가 다시 보내고, DB의 이벤트 중복 방지로 한 번만 반영된다.
    return new Response("Retry later", { status: 500 });
  }
}
