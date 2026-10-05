// [제안 · 미배포] 배포: supabase functions deploy billing-webhook --no-verify-jwt
// (결제사 서버가 보내는 요청에는 Supabase 로그인 토큰이 없으므로 JWT 검증을 끄고, 결제사 서명으로 검증한다.)
// 필요한 Edge Function Secrets: BILLING_MODE(off|test|live), BILLING_PROVIDER, BILLING_WEBHOOK_SECRET,
// SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY. 비밀값은 코드나 저장소에 두지 않는다.
import { createClient } from "npm:@supabase/supabase-js@2";
import { handle, mockAdapter, type ProviderAdapter, type VerifiedEvent } from "./handler.ts";

function selectAdapter(): ProviderAdapter | null {
  const mode = Deno.env.get("BILLING_MODE") ?? "off";
  const provider = Deno.env.get("BILLING_PROVIDER") ?? "";
  if (mode === "off") return null;
  if (provider === "mock" && mode === "test") return mockAdapter(Deno.env.get("BILLING_WEBHOOK_SECRET") ?? "");
  // 실제 결제사 어댑터는 결제사를 정한 뒤 추가한다.
  return null;
}

const admin = createClient(Deno.env.get("SUPABASE_URL") ?? "", Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "", {
  auth: { persistSession: false, autoRefreshToken: false },
});

async function applyEvent(event: VerifiedEvent, payloadSha256: string): Promise<string> {
  const { data, error } = await admin.rpc("billing_apply_event", {
    p_provider: event.provider,
    p_event_id: event.eventId,
    p_payload_sha256: payloadSha256,
    p_kind: event.kind,
    p_order_id: event.orderId,
    p_provider_payment_id: event.providerPaymentId,
    p_amount: event.amount,
    p_reason: event.reason ?? null,
  });
  if (error) throw error;
  return String(data);
}

Deno.serve((req) => handle(req, selectAdapter(), applyEvent));
