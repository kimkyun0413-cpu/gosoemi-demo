// 네이버 간편로그인 중계 Edge Function
//
//   GET /naver-auth/start     → state 발급(쿠키 + DB 해시) → 네이버 인증 화면으로 302
//   GET /naver-auth/callback  → state 검증·1회 소비 → code 교환 → 프로필 조회
//                              → Supabase 사용자 조회/생성 → magiclink hashed_token
//                              → APP_BASE_URL?naver_token_hash=... 로 302
//
// 배포: supabase functions deploy naver-auth --no-verify-jwt
//   (브라우저 리다이렉트에는 Authorization 헤더가 없으므로 JWT 검증을 끈다)
// Edge Secret NAVER_LOGIN_ENABLED="true"일 때만 동작한다.
//
// 비밀값은 모두 Edge Function Secrets(Deno.env)에서만 읽는다.
// 로그에는 단계 이름만 남기고 code·토큰·state·이메일·네이버 id는 남기지 않는다.

import { createClient, type SupabaseClient } from "npm:@supabase/supabase-js@2";

const NAVER_AUTHORIZE_URL = "https://nid.naver.com/oauth2.0/authorize";
const NAVER_TOKEN_URL = "https://nid.naver.com/oauth2.0/token";
const NAVER_PROFILE_URL = "https://openapi.naver.com/v1/nid/me";

const STATE_COOKIE = "__Host-naver_state";
const STATE_TTL_SECONDS = 600;
const CLEAR_STATE_COOKIE = `${STATE_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`;

type NaverErrorCode =
  | "disabled"
  | "cancelled"
  | "invalid_state"
  | "email_required"
  | "email_exists"
  | "server_error";

class StageError extends Error {
  constructor(readonly stage: string) {
    super(stage);
  }
}

class LoginRejected extends Error {
  constructor(readonly code: NaverErrorCode) {
    super(code);
  }
}

function requireEnv(name: string): string {
  const value = Deno.env.get(name);
  if (!value) throw new StageError(`missing_env_${name}`);
  return value;
}

function adminClient(): SupabaseClient {
  return createClient(requireEnv("SUPABASE_URL"), requireEnv("SUPABASE_SERVICE_ROLE_KEY"), {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

function timingSafeEqual(a: string, b: string): boolean {
  const left = new TextEncoder().encode(a);
  const right = new TextEncoder().encode(b);
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i++) diff |= left[i] ^ right[i];
  return diff === 0;
}

function readCookie(req: Request, name: string): string | null {
  const header = req.headers.get("cookie") ?? "";
  for (const part of header.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return rest.join("=");
  }
  return null;
}

function redirect(location: string, setCookie: string): Response {
  const headers = new Headers({
    Location: location,
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
  });
  headers.append("Set-Cookie", setCookie);
  return new Response(null, { status: 302, headers });
}

function appRedirect(appBaseUrl: string, params: Record<string, string>): Response {
  const url = new URL(appBaseUrl);
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  return redirect(url.toString(), CLEAR_STATE_COOKIE);
}

async function handleStart(): Promise<Response> {
  const clientId = requireEnv("NAVER_CLIENT_ID");
  const callbackUrl = requireEnv("NAVER_CALLBACK_URL");

  const state = base64Url(crypto.getRandomValues(new Uint8Array(32)));
  const { error } = await adminClient().rpc("naver_create_oauth_state", {
    p_state_hash: await sha256Hex(state),
  });
  if (error) throw new StageError("state_store");

  const authorizeUrl = new URL(NAVER_AUTHORIZE_URL);
  authorizeUrl.searchParams.set("response_type", "code");
  authorizeUrl.searchParams.set("client_id", clientId);
  authorizeUrl.searchParams.set("redirect_uri", callbackUrl);
  authorizeUrl.searchParams.set("state", state);

  return redirect(
    authorizeUrl.toString(),
    `${STATE_COOKIE}=${state}; Path=/; Max-Age=${STATE_TTL_SECONDS}; HttpOnly; Secure; SameSite=Lax`,
  );
}

async function exchangeCode(code: string, state: string): Promise<string> {
  const response = await fetch(NAVER_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: requireEnv("NAVER_CLIENT_ID"),
      client_secret: requireEnv("NAVER_CLIENT_SECRET"),
      code,
      state,
    }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok || typeof body?.access_token !== "string") throw new StageError("token_exchange");
  return body.access_token;
}

async function fetchProfile(accessToken: string): Promise<{ naverId: string; email: string | null }> {
  const response = await fetch(NAVER_PROFILE_URL, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  const body = await response.json().catch(() => null);
  const profile = body?.response;
  if (!response.ok || body?.resultcode !== "00" || typeof profile?.id !== "string" || !profile.id) {
    throw new StageError("profile");
  }
  const email = typeof profile.email === "string" && profile.email.trim()
    ? profile.email.trim().toLowerCase()
    : null;
  return { naverId: profile.id, email };
}

// 네이버 접근 토큰은 프로필 확인에만 쓰고 저장하지 않으며 즉시 폐기한다.
async function revokeNaverToken(accessToken: string): Promise<void> {
  try {
    await fetch(NAVER_TOKEN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "delete",
        client_id: requireEnv("NAVER_CLIENT_ID"),
        client_secret: requireEnv("NAVER_CLIENT_SECRET"),
        access_token: accessToken,
        service_provider: "NAVER",
      }),
    });
  } catch {
    console.error("[naver-auth] stage=token_revoke");
  }
}

async function getLinkedUserId(admin: SupabaseClient, naverId: string): Promise<string | null> {
  const { data, error } = await admin.rpc("naver_get_user_id", { p_naver_id: naverId });
  if (error) throw new StageError("identity_lookup");
  return typeof data === "string" ? data : null;
}

// 네이버 고유 id로만 계정을 찾는다. 같은 이메일의 기존 계정에 자동 연결하지 않는다.
async function resolveUserId(admin: SupabaseClient, naverId: string, email: string | null): Promise<string> {
  const linkedUserId = await getLinkedUserId(admin, naverId);
  if (linkedUserId) return linkedUserId;

  if (!email) throw new LoginRejected("email_required");

  const { data, error } = await admin.auth.admin.createUser({
    email,
    email_confirm: true,
    app_metadata: { naver_linked: true },
  });
  if (error) {
    if (error.code === "email_exists" || error.code === "user_already_exists") {
      const racedUserId = await getLinkedUserId(admin, naverId);
      if (racedUserId) return racedUserId;
      throw new LoginRejected("email_exists");
    }
    throw new StageError("create_user");
  }

  const newUserId = data.user.id;
  const { error: linkError } = await admin.rpc("naver_link_identity", {
    p_naver_id: naverId,
    p_user_id: newUserId,
  });
  if (linkError) {
    const { error: deleteError } = await admin.auth.admin.deleteUser(newUserId);
    if (deleteError) console.error("[naver-auth] stage=rollback_user");
    const racedUserId = await getLinkedUserId(admin, naverId);
    if (racedUserId) return racedUserId;
    throw new StageError("link_identity");
  }
  return newUserId;
}

async function handleCallback(req: Request, appBaseUrl: string): Promise<Response> {
  const params = new URL(req.url).searchParams;
  const queryState = params.get("state");
  const cookieState = readCookie(req, STATE_COOKIE);
  if (!queryState || !cookieState || !timingSafeEqual(queryState, cookieState)) {
    return appRedirect(appBaseUrl, { naver_error: "invalid_state" });
  }

  const admin = adminClient();
  const { data: consumed, error: consumeError } = await admin.rpc("naver_consume_oauth_state", {
    p_state_hash: await sha256Hex(queryState),
  });
  if (consumeError) throw new StageError("state_consume");
  if (consumed !== true) return appRedirect(appBaseUrl, { naver_error: "invalid_state" });

  if (params.get("error")) return appRedirect(appBaseUrl, { naver_error: "cancelled" });

  const code = params.get("code");
  if (!code) return appRedirect(appBaseUrl, { naver_error: "invalid_state" });

  const naverAccessToken = await exchangeCode(code, queryState);
  let profile: { naverId: string; email: string | null };
  try {
    profile = await fetchProfile(naverAccessToken);
  } finally {
    await revokeNaverToken(naverAccessToken);
  }

  let userId: string;
  try {
    userId = await resolveUserId(admin, profile.naverId, profile.email);
  } catch (error) {
    if (error instanceof LoginRejected) return appRedirect(appBaseUrl, { naver_error: error.code });
    throw error;
  }

  const { data: userData, error: userError } = await admin.auth.admin.getUserById(userId);
  const accountEmail = userData?.user?.email;
  if (userError || !accountEmail) throw new StageError("get_user");

  const { data: linkData, error: linkError } = await admin.auth.admin.generateLink({
    type: "magiclink",
    email: accountEmail,
  });
  const hashedToken = linkData?.properties?.hashed_token;
  if (linkError || !hashedToken) throw new StageError("generate_link");

  return appRedirect(appBaseUrl, { naver_token_hash: hashedToken });
}

Deno.serve(async (req) => {
  if (req.method !== "GET") {
    return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET" } });
  }

  const appBaseUrl = Deno.env.get("APP_BASE_URL");
  if (!appBaseUrl) {
    console.error("[naver-auth] stage=missing_env_APP_BASE_URL");
    return new Response("Service Unavailable", { status: 503 });
  }

  // 긴급 차단 스위치: Edge Secret NAVER_LOGIN_ENABLED가 "true"가 아니면 start·callback 모두 거부한다.
  if (Deno.env.get("NAVER_LOGIN_ENABLED") !== "true") {
    return appRedirect(appBaseUrl, { naver_error: "disabled" });
  }

  const path = new URL(req.url).pathname;
  try {
    if (path.endsWith("/start")) return await handleStart();
    if (path.endsWith("/callback")) return await handleCallback(req, appBaseUrl);
    return new Response("Not Found", { status: 404 });
  } catch (error) {
    const stage = error instanceof StageError ? error.stage : "unexpected";
    console.error(`[naver-auth] stage=${stage}`);
    return appRedirect(appBaseUrl, { naver_error: "server_error" });
  }
});
