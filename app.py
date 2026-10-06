import os
import base64
import json
import re
import time
import datetime
import unicodedata
import uuid
import hashlib
import html
import secrets
import threading
from io import BytesIO
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import streamlit as st
from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx
from google import genai
from google.genai import types
from supabase import ClientOptions, create_client, Client
from supabase_auth.errors import AuthApiError
import importlib
import auth_ip as _auth_ip_module
import billing as _billing_module
import case_guide as _case_guide_module
import policy_text as _policy_text_module
import voice_input as _voice_input_module

# Streamlit Cloud는 git push 뒤 app.py만 새로 실행하고, 이미 불러온 보조 모듈(case_guide, voice_input 등)은
# 예전 버전을 메모리에 그대로 둘 수 있다. 그러면 새로 추가한 이름을 못 찾아 ImportError로 앱이 멈춘다
# (2026-10-06 실제 발생). 의존 순서대로 다시 불러와 항상 배포된 파일과 같은 내용을 쓰게 한다.
for _module in (_case_guide_module, _policy_text_module, _billing_module, _auth_ip_module, _voice_input_module):
    importlib.reload(_module)

from voice_input import (
    NEEDS_CHECK, TEST_PHRASE_KEYWORDS, VOICE_UPLOAD_TYPES, test_phrase_check, audio_mime_type, clean_transcript, extraction_prompt,
    is_empty_transcript, merge_rows, parse_extraction, transcription_prompt, validate_audio,
)
from auth_ip import auth_request_settings, client_ip_from, forwarded_chain_summary
from billing import billing_mode, parse_plans, plan_summary, sales_open
from policy_text import (
    CHANGES_V2_TO_V3, PRIVACY_V3_REVISED_DATE, build_privacy_policy_v2, build_privacy_policy_v3,
)
from case_guide import (
    CASE_FIELDS, CASE_LONG_TEXT_FIELDS, CASE_FIELD_MAX_CHARS, DRAFT_DISCLAIMER, DAMAGE_TYPES,
    FIELD_GUIDES, LEGAL_TERMS, LEGAL_TERMS_SOURCE, OTHER_TYPE, ROUTES,
    SAFE_DRAFT_INSTRUCTION, SUSPECT_UNKNOWN_GUIDE, build_case_draft_docx, build_draft_prompt,
    compute_case_fingerprint, damage_type_guide, field_label, field_placeholder, find_unsupported_details,
    is_missing, missing_info_items, normalize_case_values, validate_case_values,
)
MEMORY_TRIGGERS = [unicodedata.normalize("NFC", t) for t in ["기억해", "저장해"]]

CANCEL_TRIGGERS = [unicodedata.normalize("NFC", t) for t in ["취소", "삭제"]]
DATE_WORDS = ["오늘", "내일", "모레", "이번주", "이번 주", "다음주", "다음 주"]

CASES_TABLE = "cases"
# 사건 작성·초안·증거 화면의 위젯 상태 키 접두어. 계정이 바뀌면 모두 지운다.
CASE_UI_STATE_PREFIXES = (
    "case_field_", "case_type_", "case_draft_editor", "case_data_consent",
    "list_draft_", "evidence_", "confirm_delete_", "voice_",
)

# 비밀번호 재설정은 Supabase에 직접 메일 서버(SMTP)와 재설정 메일 템플릿을 설정한 뒤 켠다.
# 기본 메일 설정에서는 재설정 링크가 이 앱의 token_hash 처리 방식과 맞지 않아 동작하지 않는다.
PASSWORD_RESET_ENABLED = os.environ.get("PASSWORD_RESET_ENABLED") == "true"
# 이용자 IP를 Supabase Auth에 전달해 IP별 제한을 이용자마다 따로 적용(auth_ip.py). 기본은 꺼짐.
AUTH_IP_FORWARDING_ENABLED = os.environ.get("AUTH_IP_FORWARDING_ENABLED") == "true"

UNSUPPORTED_ASSISTANT_MESSAGE = "죄송합니다. 현재 고소음이는 일정 관리·메모 기억 기능을 지원하지 않습니다. 법률 상담이나 사건 정리를 도와드릴게요."

PRIVACY_POLICY_ENABLED = os.environ.get("PRIVACY_POLICY_ENABLED") == "true"
PRIVACY_OPERATOR_NAME = os.environ.get("PRIVACY_OPERATOR_NAME")
PRIVACY_CONTACT_EMAIL = os.environ.get("PRIVACY_CONTACT_EMAIL")
PRIVACY_POLICY_EFFECTIVE_DATE = (os.environ.get("PRIVACY_POLICY_EFFECTIVE_DATE") or "").strip() or None
TERMS_EFFECTIVE_DATE = "2026-09-15"
# 개정 예정인 개인정보처리방침 시행일(예: "2026-10-13"). 설정하면 시행 전까지 화면 위에 미리 알린다.
PRIVACY_POLICY_UPCOMING_DATE = (os.environ.get("PRIVACY_POLICY_UPCOMING_DATE") or "").strip() or None

EVIDENCE_UPLOAD_ENABLED = os.environ.get("EVIDENCE_UPLOAD_ENABLED") == "true"

# 유료 요금제. 판매 조건이 갖춰지기 전에는 '준비 중'만 보여 준다(billing.py, docs/billing-readiness.md).
BILLING_MODE = billing_mode(os.environ.get("BILLING_MODE"))
BILLING_PLANS = parse_plans(os.environ.get("BILLING_PLANS_JSON"))

# 고소장 초안 저장. supabase/migrations/20261006000000_case_drafts.sql을 적용한 뒤 켠다.
# 초안 저장을 적은 개인정보처리방침(v3) 시행일이 지나야 화면에 나타난다.
DRAFT_SAVE_ENABLED = os.environ.get("DRAFT_SAVE_ENABLED") == "true"
# 음성 입력. 음성을 Gemini로 보내므로 이를 적은 개인정보처리방침(v3) 시행 후에만 나타난다.
VOICE_INPUT_ENABLED = os.environ.get("VOICE_INPUT_ENABLED") == "true"
# 관리자(운영자) 계정의 Supabase 사용자 ID 목록(쉼표로 구분). 공개 기능을 켜지 않고 음성 입력 시험과
# 운영 진단을 이 계정에만 보여 준다. 이메일은 바뀌거나 소셜 로그인 제공자가 정할 수 있어 쓰지 않는다.
# 운영 AI 키는 서버(Streamlit Secrets)에만 있고 화면·로그에 나오지 않는다. 개인정보 없는 시험 음성만 쓴다.
ADMIN_USER_IDS = {
    i.strip().lower() for i in (os.environ.get("ADMIN_USER_IDS") or "").split(",") if i.strip()
}
CASE_DRAFTS_TABLE = "case_drafts"
CASE_DRAFT_MAX_CHARS = 20000
EVIDENCE_BUCKET = "case-evidence"
EVIDENCE_MAX_FILE_BYTES = 10 * 1024 * 1024
EVIDENCE_MAX_FILES_PER_CASE = 20
EVIDENCE_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".pdf", ".mp3", ".m4a", ".wav"}

NAVER_LOGIN_ENABLED = os.environ.get("NAVER_LOGIN_ENABLED") == "true"
NAVER_LOGIN_START_URL = os.environ.get("NAVER_LOGIN_START_URL")
# 개인정보처리방침에 네이버 수집 항목을 반영한 날. 시행일 환경변수가 이 날짜 이상이고, 한국 날짜로 시행일 당일부터 버튼을 공개한다.
NAVER_PRIVACY_TEXT_REVISED_DATE = datetime.date(2026, 9, 24)
NAVER_ERROR_MESSAGES = {
    "disabled": "네이버 로그인은 아직 준비 중입니다. 이메일로 로그인해 주세요.",
    "cancelled": "네이버 로그인을 취소하셨습니다. 다시 시도하려면 네이버 로그인 버튼을 눌러 주세요.",
    "invalid_state": "로그인 요청이 만료되었거나 올바르지 않습니다. 네이버 로그인을 처음부터 다시 시도해 주세요.",
    "email_required": "네이버 로그인으로 가입하려면 네이버에서 이메일 제공에 동의해 주세요.",
    "email_exists": "이미 이메일로 가입된 주소입니다. 이메일과 비밀번호로 로그인해 주세요.",
    "server_error": "네이버 로그인 중 일시적인 문제가 생겼습니다. 이 탭을 새로고침한 뒤 네이버 로그인을 다시 눌러 주세요.",
}

# Google 로그인은 Supabase OAuth(PKCE)로 처리한다. Client ID/Secret은 Supabase 대시보드에만 둔다.
GOOGLE_LOGIN_ENABLED = os.environ.get("GOOGLE_LOGIN_ENABLED", "true") != "false"
GOOGLE_OAUTH_REDIRECT_URL = (
    os.environ.get("GOOGLE_OAUTH_REDIRECT_URL") or "https://gosoemi-demo-csapxr3rjt2sjg66te9wrt.streamlit.app"
).rstrip("/")
# 개인정보처리방침에 Google 수집 항목을 반영한 날. 시행일 환경변수가 이 날짜 이상이고, 한국 날짜로 시행일 당일부터 버튼을 공개한다.
GOOGLE_PRIVACY_TEXT_REVISED_DATE = datetime.date(2026, 9, 27)
GOOGLE_FLOW_TTL_SECONDS = 600
GOOGLE_FLOW_MAX_PENDING = 2000
GOOGLE_ERROR_MESSAGES = {
    "cancelled": "Google 로그인을 취소하셨습니다. 다시 시도하려면 Google 버튼을 눌러 주세요.",
    "invalid_state": "로그인 요청이 만료되었거나 올바르지 않습니다. Google 로그인을 처음부터 다시 시도해 주세요.",
    "server_error": "Google 로그인 중 일시적인 문제가 생겼습니다. 이 탭을 새로고침한 뒤 다시 시도해 주세요.",
}
# 개인정보처리방침 시행일은 한국 날짜 기준으로 판단한다. 서버(Streamlit Cloud)는 UTC로 동작하므로
# date.today()를 쓰면 KST 00:00~09:00 사이에 하루 늦게 활성화된다. 한국은 서머타임이 없어 고정 오프셋을 쓴다.
KST = datetime.timezone(datetime.timedelta(hours=9), "Asia/Seoul")
# 소셜 로그인 사용자의 만 14세 이상 확인 시각을 저장하는 user_metadata 키 (Google 전용, 네이버는 별도 테이블 사용)
SOCIAL_AGE_CONFIRMED_KEY = "age_14_confirmed_at"
SOCIAL_LOGIN_NOTICE = (
    "소셜 계정으로 처음 로그인하시면 만 14세 이상 확인과 이용약관·개인정보 수집·이용 동의를 마친 뒤 "
    "서비스를 이용할 수 있습니다. 제공받는 정보는 개인정보처리방침에서 확인할 수 있습니다."
)


def privacy_config_ready() -> bool:
    return bool(
        PRIVACY_POLICY_ENABLED
        and PRIVACY_OPERATOR_NAME
        and PRIVACY_CONTACT_EMAIL
        and PRIVACY_POLICY_EFFECTIVE_DATE
    )


def terms_config_ready() -> bool:
    return bool(
        PRIVACY_OPERATOR_NAME
        and PRIVACY_CONTACT_EMAIL
        and TERMS_EFFECTIVE_DATE
    )

SERVICE_IDENTITY_INSTRUCTION = (
    "- 이 서비스의 이름은 \"고소음이\"이다.\n"
    "- \"고소음이\"는 소음(noise) 관련 서비스가 아니다. 이름에 \"소음\"이 포함되어 있다고 해서 "
    "소음 측정, 소음 진단, 층간소음 상담 등 소음 관련 서비스로 오해하거나 그렇게 답변하지 마라.\n"
    "- \"고소음이\"는 범죄·피해 상황을 사용자가 정리하고, 고소장 참고용 초안을 작성하도록 돕는 "
    "AI 법률정보 도우미이다.\n"
    "- \"고소음이\"는 변호사의 법률자문을 대체하지 않는다.\n"
    "- 사용자가 서비스의 정체성(무엇을 하는 서비스인지 등)을 물으면 위 내용에 맞게 간결하게 설명하라."
)


def get_supabase_anon_client() -> Client:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY 환경변수가 설정되지 않았습니다.")
    return create_client(supabase_url, supabase_key)


def get_auth_client() -> Client:
    """로그인·가입·토큰 갱신·인증 확인 요청 전용 클라이언트. DB·파일 요청에는 쓰지 않는다.

    AUTH_IP_FORWARDING_ENABLED와 SUPABASE_SECRET_KEY가 있고 이용자의 공인 IP를 알 수 있으면,
    secret 키와 Sb-Forwarded-For 헤더로 요청해 Supabase의 IP별 제한이 이용자마다 따로 적용되게 한다.
    (Supabase 대시보드 Authentication > Rate Limits > IP Address Forwarding도 켜야 한다.)
    조건이 하나라도 없으면 지금처럼 publishable 키로 요청한다.
    """
    supabase_url = os.environ.get("SUPABASE_URL")
    publishable_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
    if not supabase_url or not publishable_key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY 환경변수가 설정되지 않았습니다.")
    user_ip = None
    if AUTH_IP_FORWARDING_ENABLED:
        try:
            user_ip = client_ip_from(st.context.headers.get("X-Forwarded-For"), st.context.ip_address)
        except Exception:
            user_ip = None
    key, headers = auth_request_settings(
        AUTH_IP_FORWARDING_ENABLED, publishable_key, os.environ.get("SUPABASE_SECRET_KEY", ""), user_ip
    )
    if not headers:
        return create_client(supabase_url, publishable_key)
    return create_client(supabase_url, key, options=ClientOptions(headers=headers))


def get_authed_client() -> Client:
    session = st.session_state.get("supabase_session")
    if not session:
        raise RuntimeError("로그인이 필요합니다.")
    client = get_supabase_anon_client()
    client.postgrest.auth(session["access_token"])
    return client


def has_current_consent(user_id: str):
    """현재 버전 동의가 있으면 True, 없으면 False, 확인하지 못하면 None."""
    try:
        client = get_authed_client()
        response = (
            client.table("consent_records")
            .select("terms_version")
            .eq("user_id", user_id)
            .eq("terms_version", TERMS_EFFECTIVE_DATE)
            .eq("privacy_version", PRIVACY_POLICY_EFFECTIVE_DATE)
            .limit(1)
            .execute()
        )
        return bool(response.data)
    except Exception as error:
        log_auth_error(error)
        return None


def get_app_base_url():
    url = os.environ.get("APP_BASE_URL")
    return url.rstrip("/") if url else None


def log_auth_error(error: Exception) -> None:
    if isinstance(error, AuthApiError):
        print(f"[auth_error] code={error.code} status={error.status}")
    else:
        print(f"[auth_error] unexpected={type(error).__name__}")


def map_auth_error(error: Exception) -> str:
    if isinstance(error, AuthApiError):
        code = getattr(error, "code", None)
        status = getattr(error, "status", None)
        if code == "invalid_credentials":
            return "이메일 또는 비밀번호가 올바르지 않습니다."
        if code == "email_not_confirmed":
            return "아직 이메일 인증을 마치지 않으셨습니다. 받은 편지함과 스팸함에서 인증 메일을 찾아 링크를 눌러 주세요."
        if code in ("user_already_exists", "email_exists"):
            return "이미 가입된 이메일입니다. 로그인 탭에서 로그인해 주세요. 네이버·Google로 가입하셨다면 그 버튼으로 로그인해 주세요."
        if code in ("over_email_send_rate_limit", "over_request_rate_limit") or status == 429:
            return "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
        if code == "weak_password":
            return "비밀번호가 너무 쉽습니다. 영문·숫자를 섞어 6자 이상의 다른 비밀번호를 써 주세요."
        if code in ("validation_failed", "email_address_invalid"):
            return "이메일 주소 형식이 맞지 않습니다. 예: name@example.com"
        if code == "otp_expired":
            return "인증 링크가 만료되었거나 이미 사용되었습니다. 인증 메일을 다시 요청해 주세요."
        if code in ("session_not_found", "flow_state_not_found", "flow_state_expired"):
            return "인증 세션이 만료되었습니다. 처음부터 다시 시도해 주세요."
        if code == "same_password":
            return "이전과 동일한 비밀번호로는 변경할 수 없습니다."
        if code == "signup_disabled":
            return "현재 회원가입이 제한되어 있습니다. 잠시 후 다시 시도해 주세요."
    return "일시적인 문제로 처리하지 못했습니다. 인터넷 연결을 확인하고 잠시 후 다시 시도해 주세요."


def set_auth_flash(level, text) -> None:
    st.session_state.auth_flash = {"level": level, "text": text}


def render_auth_flash() -> None:
    flash = st.session_state.get("auth_flash")
    if flash:
        if flash["level"] == "success":
            st.success(flash["text"])
        elif flash["level"] == "info":
            st.info(flash["text"])
        else:
            st.error(flash["text"])
        st.session_state.auth_flash = None


def session_to_dict(session, previous: dict | None = None) -> dict:
    # login_nonce는 "이 탭에서 이번에 로그인한 것"을 구분하는 임의값이다. 토큰 갱신 때는 이전 값을 이어 쓴다.
    return {
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "user_id": session.user.id,
        "email": session.user.email,
        "expires_at": session.expires_at,
        "login_nonce": (previous or {}).get("login_nonce") or secrets.token_hex(16),
    }


def session_from_auth_response(auth_response, previous: dict | None = None) -> dict:
    return session_to_dict(auth_response.session, previous)


# Streamlit Session State is lost on a full browser reload. Store only the
# Supabase refresh token in this browser (never in the URL or a shared cache).
# The component runs in the app origin, and removes the token on logout.
#
# 여러 탭 보호: 탭마다 sessionStorage에 이번 로그인의 nonce를 적어 둔다. 다른 탭이
# 다른 계정으로 로그인하거나 로그아웃해 localStorage가 바뀌면 storage 이벤트로 바로
# 알아채고, 이 탭의 로그인을 해제해 계정이 섞이지 않게 한다.
_browser_session_component = st.components.v2.component(
    "gosoemi_browser_session",
    js="""
export default function({data, setStateValue}) {
  const key = 'gosoemi_login_v1';
  const tabKey = 'gosoemi_tab_login_v1';
  const ttl = 7 * 24 * 60 * 60 * 1000;
  const readSaved = () => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  };
  try {
    if (data.action === 'clear') {
      localStorage.removeItem(key);
      try { sessionStorage.removeItem(tabKey); } catch (_) {}
      setStateValue('stored', 'cleared');
    } else if (data.action === 'save') {
      const write = () => localStorage.setItem(key, JSON.stringify({
        token: data.token, user: data.user, nonce: data.nonce, expires: Date.now() + ttl}));
      const check = () => {
        const saved = readSaved();
        let tabNonce = null;
        try { tabNonce = sessionStorage.getItem(tabKey); } catch (_) {}
        if (tabNonce !== data.nonce) {
          // 이 탭에서 방금 로그인했다: 이 계정을 브라우저에 저장한다.
          write();
          try { sessionStorage.setItem(tabKey, data.nonce); } catch (_) {}
          return;
        }
        if (!saved || typeof saved.token !== 'string') {
          setStateValue('sync', 'signed_out');
        } else if (saved.nonce !== data.nonce) {
          if (saved.user && saved.user !== data.user) setStateValue('sync', 'other_account');
        } else if (saved.token !== data.token) {
          write();
        }
      };
      check();
      const onStorage = (event) => { if (event.key === key || event.key === null) check(); };
      window.addEventListener('storage', onStorage);
      return () => window.removeEventListener('storage', onStorage);
    } else if (data.action === 'watch') {
      const onStorage = (event) => {
        if (event.key === key && event.newValue) setStateValue('elsewhere', 'logged_in');
      };
      window.addEventListener('storage', onStorage);
      return () => window.removeEventListener('storage', onStorage);
    } else {
      let saved = readSaved();
      if (saved && (typeof saved.token !== 'string' ||
                    !Number.isFinite(saved.expires) || saved.expires < Date.now())) {
        localStorage.removeItem(key);
        saved = null;
      }
      setStateValue('stored', saved ? saved.token : 'empty');
    }
  } catch (_) {
    setStateValue('stored', 'empty');
  }
}
""",
)

BROWSER_SYNC_MESSAGES = {
    "other_account": (
        "다른 탭에서 다른 계정으로 로그인해서, 계정이 섞이지 않도록 이 탭의 로그인을 해제했습니다. "
        "방금 로그인한 탭을 이용해 주세요. 이 탭은 닫으셔도 됩니다."
    ),
    "signed_out": (
        "다른 탭에서 로그아웃(또는 회원탈퇴)해서 이 탭의 로그인도 해제했습니다. "
        "계속 이용하시려면 다시 로그인해 주세요."
    ),
}


def browser_save_component_key(login_nonce: str) -> str:
    """브라우저 세션 컴포넌트 key. Streamlit은 컴포넌트 ID(=key 포함)에 '__'가 있으면 오류를 낸다.

    예전에는 token_urlsafe 값을 그대로 붙여 '_'가 겹치면(약 2%) 운영 앱이 BidiComponentInvalidIdError로
    멈췄다(2026-10-06). 16진수 해시만 쓰면 '_'가 들어가지 않는다.
    """
    return "browser_login_save_" + hashlib.sha256(login_nonce.encode("utf-8")).hexdigest()[:24]


def drop_local_session() -> None:
    """서버 로그아웃이나 브라우저 저장소 삭제 없이 이 탭의 로그인 상태만 지운다."""
    st.session_state.supabase_session = None
    st.session_state.pop("google_login_url", None)
    clear_user_session_data()
    st.session_state.auth_view = "login"


def restore_browser_session() -> None:
    if st.session_state.get("browser_login_clear"):
        result = _browser_session_component(
            key="browser_login_clear_component", data={"action": "clear"}
        )
        if getattr(result, "stored", None) == "cleared":
            st.session_state.browser_login_clear = False
            st.rerun()
        st.info("로그아웃하고 있습니다. 잠시만 기다려 주세요.")
        st.stop()

    session = st.session_state.get("supabase_session")
    if session:
        if not session.get("login_nonce"):
            session["login_nonce"] = secrets.token_hex(16)
        result = _browser_session_component(
            key=browser_save_component_key(session["login_nonce"]),
            data={
                "action": "save",
                "token": session["refresh_token"],
                # 계정 비교에만 쓰므로 사용자 ID 원문 대신 해시 일부만 브라우저에 둔다.
                "user": hashlib.sha256(session["user_id"].encode("utf-8")).hexdigest()[:16],
                "nonce": session["login_nonce"],
            },
        )
        sync = getattr(result, "sync", None)
        if sync in BROWSER_SYNC_MESSAGES:
            drop_local_session()
            st.session_state.browser_login_checked = True
            set_auth_flash("info", BROWSER_SYNC_MESSAGES[sync])
            st.rerun()
        return

    if st.session_state.get("browser_login_checked"):
        watch = _browser_session_component(key="browser_login_watch", data={"action": "watch"})
        if getattr(watch, "elsewhere", None) == "logged_in":
            st.info(
                "다른 탭에서 로그인이 완료되었습니다. 로그인한 탭을 이용해 주시고, 이 탭은 닫으셔도 됩니다."
            )
        return
    result = _browser_session_component(
        key="browser_login_restore_component", data={"action": "restore"}
    )
    token = getattr(result, "stored", None)
    if token is None:
        st.info("로그인 상태를 확인하고 있습니다. 잠시만 기다려 주세요.")
        st.stop()
    st.session_state.browser_login_checked = True
    if token not in ("empty", "cleared"):
        try:
            response = get_auth_client().auth.refresh_session(token)
            if response.session and response.user:
                st.session_state.supabase_session = session_from_auth_response(response)
                st.rerun()
        except Exception as error:
            log_auth_error(error)
        st.session_state.browser_login_clear = True
        st.rerun()


def get_session_authed_client(session: dict) -> Client:
    client = get_supabase_anon_client()
    set_response = client.auth.set_session(session["access_token"], session["refresh_token"])
    if not getattr(set_response, "session", None):
        raise RuntimeError("세션을 설정할 수 없습니다.")
    return client


def clear_user_session_data() -> None:
    st.session_state.case_draft = None
    st.session_state.case_draft_id = None
    st.session_state.last_saved_case_entry = None
    st.session_state.last_saved_fingerprint = None
    st.session_state.last_saved_at = None
    st.session_state.pending_draft_entry = None
    st.session_state.pending_draft_reason = None
    st.session_state.case_review = None
    st.session_state.case_notice = None
    st.session_state.case_draft_case = None
    st.session_state.messages = []
    st.session_state.model_history = []
    st.session_state.case_workflow_user_id = None
    # 이전 계정이 입력하던 사건 내용·초안·다운로드 준비 파일이 다음 계정 화면에 남지 않게 지운다.
    for key in list(st.session_state.keys()):
        if key.startswith(CASE_UI_STATE_PREFIXES):
            del st.session_state[key]


def perform_logout() -> None:
    session = st.session_state.get("supabase_session")
    if session:
        try:
            get_session_authed_client(session).auth.sign_out()
        except Exception as error:
            log_auth_error(error)
    st.session_state.supabase_session = None
    st.session_state.browser_login_clear = True
    st.session_state.pop("browser_login_clear_component", None)
    st.session_state.pop("browser_login_watch", None)
    st.session_state.pop("google_login_url", None)
    clear_user_session_data()
    st.session_state.auth_view = "login"
    set_auth_flash("success", "로그아웃했습니다. 이 브라우저에 저장된 로그인 정보도 지웠습니다.")
    st.rerun()


def delete_current_account() -> None:
    session = ensure_active_session()
    if not session or not session.get("access_token"):
        raise RuntimeError("로그인이 만료되었습니다. 다시 로그인해 주세요.")
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if not supabase_url:
        raise RuntimeError("탈퇴 기능 설정을 확인할 수 없습니다.")
    request = Request(
        f"{supabase_url}/functions/v1/delete-account",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {session['access_token']}",
            "apikey": os.environ.get("SUPABASE_PUBLISHABLE_KEY", ""),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            if not json.load(response).get("deleted"):
                raise RuntimeError("탈퇴 완료를 확인할 수 없습니다.")
    except HTTPError as error:
        if error.code in (401, 403):
            raise RuntimeError("로그인이 만료되었습니다. 다시 로그인해 주세요.") from error
        raise RuntimeError("탈퇴 처리 중 문제가 생겼습니다. 계정 상태를 확인해 주세요.") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError("연결이 끊겼습니다. 다시 로그인해 계정 상태를 확인해 주세요.") from error

    st.session_state.supabase_session = None
    st.session_state.browser_login_clear = True
    st.session_state.pop("browser_login_clear_component", None)
    clear_user_session_data()
    st.session_state.auth_view = "login"
    st.session_state.pop("google_login_url", None)
    st.session_state.account_deleted = True
    st.rerun()


@st.dialog("회원탈퇴")
def render_delete_account_dialog() -> None:
    session = st.session_state.get("supabase_session") or {}
    identity, method = current_account_label(session)
    st.markdown(f"**탈퇴할 계정:** {identity} ({method} 로그인)")

    cases, cases_error = load_cases_safe()
    try:
        evidence_count = count_user_evidence_files(session.get("user_id", ""))
    except Exception:
        evidence_count = None
    case_text = "확인하지 못했습니다" if cases_error else f"{len(cases)}건"
    evidence_text = "확인하지 못했습니다" if evidence_count is None else f"{evidence_count}개"
    summary = f"**삭제될 자료:** 사건 {case_text}, 증거파일 {evidence_text}"
    draft_count = 0
    if draft_save_available():
        try:
            draft_count = count_user_drafts()
            summary += f", 저장된 초안 {draft_count}개"
        except Exception:
            draft_count = None
            summary += ", 저장된 초안 확인하지 못했습니다"
    st.markdown(summary)
    if cases_error or evidence_count is None or draft_count is None:
        st.caption("개수를 확인하지 못해도 탈퇴하면 이 계정의 모든 사건·초안·증거파일이 삭제됩니다.")

    st.warning("탈퇴하면 위 계정과 저장된 사건, 증거자료를 삭제합니다. 삭제한 내용은 되돌릴 수 없습니다. 탈퇴할 계정이 맞는지 먼저 확인해 주세요.")
    confirmed = st.checkbox("위 계정과 모든 저장 자료의 삭제에 동의합니다.", key="confirm_delete_account")
    if st.button("탈퇴하기", type="primary", disabled=not confirmed, key="delete_account_final"):
        try:
            with st.spinner("계정과 저장된 자료를 삭제하고 있습니다. 잠시 기다려 주세요."):
                delete_current_account()
        except Exception as error:
            st.error(str(error))


def ensure_active_session():
    session = st.session_state.get("supabase_session")
    if not session:
        return None
    expires_at = session.get("expires_at")
    if expires_at and time.time() < expires_at - 60:
        return session
    try:
        client = get_auth_client()
        refreshed = client.auth.refresh_session(session["refresh_token"])
        if not getattr(refreshed, "session", None) or not getattr(refreshed, "user", None):
            raise RuntimeError("empty refresh response")
        new_session = session_from_auth_response(refreshed, session)
        st.session_state.supabase_session = new_session
        # A refresh token is single-use; write the rotated token to this
        # browser before continuing with the newly refreshed session.
        st.rerun()
        # 버튼 콜백 안에서는 st.rerun()이 실행을 멈추지 않으므로 새 세션으로 계속한다.
        return new_session
    except Exception as error:
        log_auth_error(error)
        st.session_state.supabase_session = None
        st.session_state.browser_login_clear = True
        clear_user_session_data()
        st.session_state.auth_view = "login"
        set_auth_flash("error", "로그인 유지 시간이 지나 자동으로 로그아웃되었습니다. 다시 로그인해 주세요.")
        return None


def handle_auth_query_params() -> None:
    token_hash = st.query_params.get("token_hash")
    type_ = st.query_params.get("type")
    if not token_hash or not type_:
        return

    st.query_params.clear()

    try:
        auth_client = get_auth_client()
        response = auth_client.auth.verify_otp({"token_hash": token_hash, "type": type_})
        if not getattr(response, "session", None) or not getattr(response, "user", None):
            raise RuntimeError("empty verify_otp response")

        if type_ == "recovery":
            st.session_state.pending_recovery_session = {
                "access_token": response.session.access_token,
                "refresh_token": response.session.refresh_token,
            }
            st.session_state.auth_view = "recovery_set_password"
        else:
            st.session_state.supabase_session = session_from_auth_response(response)
            st.session_state.auth_view = "login"
            set_auth_flash("success", "이메일 인증이 완료되어 자동으로 로그인되었습니다.")
    except Exception as error:
        log_auth_error(error)
        st.session_state.auth_view = "login"
        set_auth_flash("error", map_auth_error(error))

    st.rerun()


def today_kst() -> datetime.date:
    return datetime.datetime.now(KST).date()


def parse_privacy_effective_date():
    try:
        return datetime.date.fromisoformat(PRIVACY_POLICY_EFFECTIVE_DATE or "")
    except ValueError:
        return None


def social_login_gate_checks() -> dict:
    """소셜 로그인 버튼 표시 조건별 통과 여부. 값 자체가 아니라 true/false만 담는다."""
    effective_date = parse_privacy_effective_date()
    return {
        "privacy_enabled": PRIVACY_POLICY_ENABLED,
        "operator_name_present": bool(PRIVACY_OPERATOR_NAME),
        "contact_email_present": bool(PRIVACY_CONTACT_EMAIL),
        "effective_date_valid": effective_date is not None,
        "effective_date_reached_kst": effective_date is not None and today_kst() >= effective_date,
        "naver_policy_date_ok": effective_date is not None and effective_date >= NAVER_PRIVACY_TEXT_REVISED_DATE,
        "naver_enabled": NAVER_LOGIN_ENABLED,
        "naver_start_url_present": bool(NAVER_LOGIN_START_URL),
        "google_policy_date_ok": effective_date is not None and effective_date >= GOOGLE_PRIVACY_TEXT_REVISED_DATE,
        "google_enabled": GOOGLE_LOGIN_ENABLED,
    }


# (조건 키, 실패 사유 코드). 시행일이 없거나 형식이 잘못되면 날짜 관련 사유는
# effective_date_missing / effective_date_invalid 하나로 묶는다.
COMMON_GATE_REASONS = [
    ("privacy_enabled", "privacy_disabled"),
    ("operator_name_present", "operator_name_missing"),
    ("contact_email_present", "contact_email_missing"),
    ("effective_date_valid", "effective_date_invalid"),
    ("effective_date_reached_kst", "effective_date_not_reached_kst"),
]
NAVER_GATE_REASONS = COMMON_GATE_REASONS + [
    ("naver_policy_date_ok", "naver_policy_date_before_revision"),
    ("naver_enabled", "naver_disabled"),
    ("naver_start_url_present", "naver_start_url_missing"),
]
GOOGLE_GATE_REASONS = COMMON_GATE_REASONS + [
    ("google_policy_date_ok", "google_policy_date_before_revision"),
    ("google_enabled", "google_disabled"),
]
DATE_DEPENDENT_GATES = {"effective_date_reached_kst", "naver_policy_date_ok", "google_policy_date_ok"}


def gate_failure_reasons(checks: dict, gates: list) -> list:
    reasons = []
    for key, reason in gates:
        if checks[key]:
            continue
        if key in DATE_DEPENDENT_GATES and not checks["effective_date_valid"]:
            continue
        if reason == "effective_date_invalid" and not PRIVACY_POLICY_EFFECTIVE_DATE:
            reason = "effective_date_missing"
        reasons.append(reason)
    return reasons


def naver_login_available() -> bool:
    return not gate_failure_reasons(social_login_gate_checks(), NAVER_GATE_REASONS)


def google_login_available() -> bool:
    return not gate_failure_reasons(social_login_gate_checks(), GOOGLE_GATE_REASONS)


def format_auth_gate_debug(checks: dict, google_oauth_url_created: bool, google_oauth_attempted: bool) -> str:
    fields = {**checks, "google_oauth_url_created": google_oauth_url_created}
    naver_reasons = gate_failure_reasons(checks, NAVER_GATE_REASONS)
    google_reasons = gate_failure_reasons(checks, GOOGLE_GATE_REASONS)
    if google_oauth_attempted and not google_oauth_url_created:
        google_reasons.append("google_oauth_url_failed")
    parts = [f"{key}={str(value).lower()}" for key, value in fields.items()]
    parts.append(f"naver_visible={str(not naver_reasons).lower()}")
    parts.append(f"naver_hidden_reason={','.join(naver_reasons) or 'none'}")
    parts.append(f"google_visible={str(not google_reasons).lower()}")
    parts.append(f"google_hidden_reason={','.join(google_reasons) or 'none'}")
    return "[auth_gate_debug] " + " ".join(parts)


@st.cache_resource
def get_auth_gate_debug_seen() -> set:
    # 프로세스당 처음 한 번, 그리고 판정 결과가 바뀔 때만 출력해 rerun마다 로그가 쌓이지 않게 한다.
    return set()


def log_auth_gate_debug(google_oauth_url_created: bool, google_oauth_attempted: bool) -> None:
    line = format_auth_gate_debug(social_login_gate_checks(), google_oauth_url_created, google_oauth_attempted)
    seen = get_auth_gate_debug_seen()
    if line in seen:
        return
    seen.add(line)
    print(line, flush=True)


def handle_naver_query_params() -> None:
    naver_token_hash = st.query_params.get("naver_token_hash")
    naver_error = st.query_params.get("naver_error")
    if not naver_token_hash and not naver_error:
        return

    st.query_params.clear()
    st.session_state.auth_view = "login"

    if naver_error:
        set_auth_flash("error", NAVER_ERROR_MESSAGES.get(naver_error, NAVER_ERROR_MESSAGES["server_error"]))
        st.rerun()

    try:
        auth_client = get_auth_client()
        response = auth_client.auth.verify_otp({"token_hash": naver_token_hash, "type": "magiclink"})
        if not getattr(response, "session", None) or not getattr(response, "user", None):
            raise RuntimeError("empty verify_otp response")
        st.session_state.supabase_session = session_from_auth_response(response)
        set_auth_flash("success", "네이버 계정으로 로그인되었습니다.")
    except Exception as error:
        log_auth_error(error)
        if isinstance(error, AuthApiError) and getattr(error, "code", None) == "otp_expired":
            set_auth_flash("error", NAVER_ERROR_MESSAGES["invalid_state"])
        else:
            set_auth_flash("error", map_auth_error(error))

    st.rerun()


def decode_access_token_claims(session: dict) -> dict:
    # 서명 검증 없이 화면 분기용으로만 사용한다. 실제 권한은 RLS와 RPC가 auth.uid()로 판단한다.
    try:
        payload_segment = session["access_token"].split(".")[1]
        padded = payload_segment + "=" * (-len(payload_segment) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return {}


def is_naver_session(session: dict) -> bool:
    claims = decode_access_token_claims(session)
    return (claims.get("app_metadata") or {}).get("naver_linked") is True


def current_account_label(session: dict) -> tuple[str, str]:
    # 네이버 로그인은 이름을 받지 않으므로 이메일을, 이메일이 없으면 고소음이 사용자 ID를 보여준다.
    if is_naver_session(session):
        method = "네이버"
    else:
        provider = (decode_access_token_claims(session).get("app_metadata") or {}).get("provider")
        method = {"google": "Google", "email": "이메일"}.get(provider, "기타")
    identity = session.get("email") or f"사용자 ID {session.get('user_id', '')}"
    # 이메일의 _ 등이 마크다운 서식으로 바뀌지 않도록 이스케이프한다.
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|~<>])", r"\\\1", identity), method


def count_user_evidence_files(user_id: str) -> int:
    if not user_id:
        raise RuntimeError("로그인이 필요합니다.")
    bucket = get_storage_authed_client().storage.from_(EVIDENCE_BUCKET)
    total = 0
    for entry in bucket.list(user_id, {"limit": 1000}) or []:
        if evidence_item_value(entry, "id"):
            total += 1
            continue
        folder = f"{user_id}/{evidence_item_value(entry, 'name')}"
        total += sum(1 for item in bucket.list(folder, {"limit": 1000}) or [] if evidence_item_value(item, "id"))
    return total


class GoogleFlowStore:
    """OAuth 시작 시 만든 PKCE code_verifier를 서버 메모리에 잠시 보관한다.

    Google에서 돌아오면 새 Streamlit 세션이 열려 session_state가 비어 있으므로,
    redirect URL에 넣은 일회용 flow ID(해시로만 저장)로 verifier를 찾는다.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._flows = {}

    @staticmethod
    def _key(flow_id: str) -> str:
        return hashlib.sha256(flow_id.encode("utf-8")).hexdigest()

    def put(self, flow_id: str, code_verifier: str) -> None:
        now = time.time()
        with self._lock:
            for key in [k for k, (_, expires_at) in self._flows.items() if expires_at <= now]:
                del self._flows[key]
            while len(self._flows) >= GOOGLE_FLOW_MAX_PENDING:
                del self._flows[min(self._flows, key=lambda k: self._flows[k][1])]
            self._flows[self._key(flow_id)] = (code_verifier, now + GOOGLE_FLOW_TTL_SECONDS)

    def pop(self, flow_id: str):
        with self._lock:
            entry = self._flows.pop(self._key(flow_id), None)
        if not entry or entry[1] <= time.time():
            return None
        return entry[0]


@st.cache_resource
def get_google_flow_store() -> GoogleFlowStore:
    return GoogleFlowStore()


def get_google_login_url():
    # 같은 브라우저 세션에서는 만료 전까지 같은 URL을 재사용해 rerun마다 flow가 쌓이지 않게 한다.
    cached = st.session_state.get("google_login_url")
    if cached and time.time() < cached["expires_at"] - 60:
        return cached["url"]

    try:
        flow_id = secrets.token_urlsafe(32)
        redirect_to = f"{GOOGLE_OAUTH_REDIRECT_URL}/?{urlencode({'google_flow': flow_id})}"
        auth_client = get_supabase_anon_client()
        response = auth_client.auth.sign_in_with_oauth({
            "provider": "google",
            "options": {
                "redirect_to": redirect_to,
                "query_params": {"prompt": "select_account"},
            },
        })
        storage = auth_client.auth._storage
        code_verifier = storage.get_item(f"{auth_client.auth._storage_key}-code-verifier")
        if not code_verifier or not getattr(response, "url", None):
            raise RuntimeError("missing pkce verifier")
    except Exception as error:
        log_auth_error(error)
        return None

    get_google_flow_store().put(flow_id, code_verifier)
    st.session_state.google_login_url = {
        "url": response.url,
        "expires_at": time.time() + GOOGLE_FLOW_TTL_SECONDS,
    }
    return response.url


def handle_google_query_params() -> None:
    flow_id = st.query_params.get("google_flow")
    if not flow_id:
        return

    auth_code = st.query_params.get("code")
    oauth_error = st.query_params.get("error")
    # 코드가 URL에 남아 새로고침 시 재사용되지 않도록 먼저 지운다.
    st.query_params.clear()
    st.session_state.auth_view = "login"
    code_verifier = get_google_flow_store().pop(flow_id)

    if oauth_error or not auth_code:
        print(f"[auth_error] google_oauth error={oauth_error or 'missing_code'}")
        key = "cancelled" if oauth_error == "access_denied" else "server_error"
        set_auth_flash("error", GOOGLE_ERROR_MESSAGES[key])
        st.rerun()

    if not code_verifier:
        set_auth_flash("error", GOOGLE_ERROR_MESSAGES["invalid_state"])
        st.rerun()

    try:
        auth_client = get_auth_client()
        response = auth_client.auth.exchange_code_for_session(
            {"auth_code": auth_code, "code_verifier": code_verifier}
        )
        if not getattr(response, "session", None) or not getattr(response, "user", None):
            raise RuntimeError("empty exchange_code_for_session response")
        st.session_state.supabase_session = session_from_auth_response(response)
        st.session_state.pop("google_login_url", None)
        set_auth_flash("success", "Google 계정으로 로그인되었습니다.")
    except Exception as error:
        log_auth_error(error)
        set_auth_flash("error", map_auth_error(error))

    st.rerun()


def is_google_primary_session(session: dict) -> bool:
    # 이메일로 가입한 뒤 같은 이메일의 Google 계정이 연결된 경우는 provider가 "email"로 남아 제외된다.
    claims = decode_access_token_claims(session)
    return (claims.get("app_metadata") or {}).get("provider") == "google"


def social_age_confirmed(session: dict) -> bool:
    claims = decode_access_token_claims(session)
    return bool((claims.get("user_metadata") or {}).get(SOCIAL_AGE_CONFIRMED_KEY))


def confirm_google_age() -> None:
    client = get_session_authed_client(st.session_state.supabase_session)
    client.auth.update_user({
        "data": {SOCIAL_AGE_CONFIRMED_KEY: datetime.datetime.now(datetime.timezone.utc).isoformat()}
    })
    # 새 access token에 갱신된 user_metadata가 담기도록 세션을 다시 발급받는다.
    refreshed = client.auth.refresh_session()
    if not getattr(refreshed, "session", None) or not getattr(refreshed, "user", None):
        raise RuntimeError("empty refresh response")
    st.session_state.supabase_session = session_from_auth_response(
        refreshed, st.session_state.supabase_session
    )


DUPLICATE_SUBMIT_WINDOW_SECONDS = 10


def case_exists(case_id: str) -> bool:
    session = st.session_state.get("supabase_session") or {}
    response = (
        get_authed_client()
        .table(CASES_TABLE)
        .select("case_id")
        .eq("case_id", case_id)
        .eq("user_id", session.get("user_id", ""))
        .limit(1)
        .execute()
    )
    return bool(response.data)


class CaseSaveGuard:
    """버튼을 연달아 누르면 Streamlit 실행이 겹쳐 같은 사건을 동시에 저장하려 할 수 있다.

    case_id마다 잠금을 두어 한 번에 하나만 저장하고, 저장을 마친 case_id는 잠시 기억한다.
    """

    TTL_SECONDS = 3600

    def __init__(self):
        self._lock = threading.Lock()
        self._locks = {}
        self._saved = {}

    def lock_for(self, case_id: str) -> threading.Lock:
        now = time.time()
        with self._lock:
            for key in [k for k, saved_at in self._saved.items() if saved_at < now - self.TTL_SECONDS]:
                self._saved.pop(key, None)
                self._locks.pop(key, None)
            return self._locks.setdefault(case_id, threading.Lock())

    def is_saved(self, case_id: str) -> bool:
        with self._lock:
            return case_id in self._saved

    def mark_saved(self, case_id: str) -> None:
        with self._lock:
            self._saved[case_id] = time.time()


@st.cache_resource
def get_case_save_guard() -> CaseSaveGuard:
    return CaseSaveGuard()


def save_case(case_entry):
    """같은 case_id로 두 번 저장되지 않게 한다. 버튼을 연달아 눌러 실행이 겹쳐도 한 건만 남는다."""
    case_id = case_entry["case_id"]
    guard = get_case_save_guard()
    with guard.lock_for(case_id):
        if guard.is_saved(case_id) or case_exists(case_id):
            guard.mark_saved(case_id)
            return
        client = get_authed_client()
        try:
            client.table(CASES_TABLE).insert(case_entry).execute()
        except Exception:
            # 저장은 됐는데 응답만 실패한 경우를 확인한다.
            if not case_exists(case_id):
                raise
        guard.mark_saved(case_id)


def load_cases_safe():
    try:
        session = st.session_state.get("supabase_session")
        if not session or not session.get("user_id"):
            raise RuntimeError("로그인이 필요합니다.")
        client = get_authed_client()
        response = (
            client.table(CASES_TABLE)
            .select("*")
            .eq("user_id", session["user_id"])
            .order("created_at", desc=True)
            .execute()
        )
    except Exception:
        return [], "저장된 사건을 불러오지 못했습니다. 화면을 새로고침해 보시고, 계속 안 되면 로그아웃 후 다시 로그인해 주세요."
    return response.data or [], None


def evidence_folder(user_id: str, case_id: str) -> str:
    return f"{user_id}/{case_id}"


def safe_evidence_filename(filename: str) -> str:
    basename = os.path.basename((filename or "").replace("\\", "/"))
    stem, extension = os.path.splitext(basename)
    extension = extension.lower() if extension.lower() in EVIDENCE_ALLOWED_EXTENSIONS else ""
    # Supabase Storage 객체 키는 ASCII 일부 문자만 허용하므로 한글 등은 제거한다.
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return f"{(stem or 'evidence')[:100]}{extension}"


def evidence_item_value(item, key, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def evidence_display_name(object_name: str) -> str:
    if "__" in object_name:
        return object_name.split("__", 1)[1]
    if re.match(r"^[0-9a-f]{32}_", object_name):
        return object_name[33:]
    return object_name


def get_user_data_client(session: dict) -> Client:
    """로그인 사용자 권한으로 DB·파일 저장소를 쓰는 클라이언트. Supabase Auth에 요청을 보내지 않는다.

    auth.set_session()은 만들 때마다 Auth의 /user를 호출한다. 모든 요청이 앱 서버 IP 하나에서
    나가므로 사건마다 이 호출이 쌓이지 않도록, 토큰을 Authorization 헤더로 직접 넣는다.
    권한 판단은 Supabase(RLS·Storage 정책)가 이 토큰으로 한다.
    """
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY 환경변수가 설정되지 않았습니다.")
    return create_client(
        supabase_url,
        supabase_key,
        options=ClientOptions(headers={"Authorization": f"Bearer {session['access_token']}"}),
    )


def get_storage_authed_client() -> Client:
    session = ensure_active_session()
    if not session or not session.get("user_id"):
        raise RuntimeError("로그인이 필요합니다.")
    # 화면 분기용 확인: 토큰의 사용자와 이 탭의 사용자가 같은지 서명 검증 없이 대조한다.
    if decode_access_token_claims(session).get("sub") != session["user_id"]:
        raise RuntimeError("세션 사용자 정보가 일치하지 않습니다.")
    return get_user_data_client(session)


def safe_error_message(error: Exception) -> str:
    message = str(getattr(error, "message", None) or error)
    message = re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "[token]", message)
    message = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "[id]", message)
    message = re.sub(r"[0-9a-fA-F]{32}", "[id]", message)
    message = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", message)
    message = re.sub(r"\[id\]/\S+", "[path]", message)
    return " ".join(message.split())[:200]


def log_evidence_upload_error(stage: str, error: Exception) -> None:
    status = getattr(error, "status", None) or getattr(error, "status_code", None)
    code = getattr(error, "code", None)
    print(
        f"[evidence_upload_error] stage={stage} type={type(error).__name__} "
        f"status={status} code={code} message={safe_error_message(error)}",
        flush=True,
    )


def map_evidence_upload_error(error: Exception) -> str:
    status = str(getattr(error, "status", None) or getattr(error, "status_code", None) or "")
    message = str(getattr(error, "message", None) or error).lower()
    if status in ("401", "403") or "row-level security" in message or "unauthorized" in message or "jwt" in message:
        return "업로드 권한을 확인하지 못했습니다. 로그아웃한 뒤 다시 로그인하고 시도해 주세요."
    if status == "413" or "maximum allowed size" in message or "too large" in message:
        return "파일이 10MB보다 커서 올릴 수 없습니다. 사진은 크기를 줄이고, 긴 녹음은 나누어 올려 주세요."
    if status == "409" or "already exists" in message:
        return "같은 이름의 파일이 이미 올라가 있습니다. 잠시 후 다시 시도해 주세요."
    if "mime" in message or "invalid_mime_type" in message:
        return "올릴 수 없는 파일 형식입니다. 사진(JPG·PNG·WEBP·HEIC), PDF, 녹음(MP3·M4A·WAV)만 올릴 수 있습니다."
    if status == "404" or "bucket not found" in message:
        return "증거자료 저장소에 연결하지 못했습니다. 잠시 후 다시 시도하시고, 계속되면 운영자 문의처로 알려 주세요."
    if "로그인" in str(error) or "세션" in str(error):
        return "로그인이 만료되었습니다. 다시 로그인한 뒤 시도해 주세요."
    return "업로드하지 못했습니다. 인터넷 연결을 확인하고 잠시 후 다시 시도해 주세요."


def list_case_evidence(case_id: str):
    session = st.session_state.get("supabase_session")
    if not session or not session.get("user_id"):
        raise RuntimeError("로그인이 필요합니다.")
    folder = evidence_folder(session["user_id"], case_id)
    client = get_storage_authed_client()
    items = client.storage.from_(EVIDENCE_BUCKET).list(
        folder,
        {"limit": EVIDENCE_MAX_FILES_PER_CASE + 1, "sortBy": {"column": "created_at", "order": "desc"}},
    )
    files = []
    for item in items or []:
        name = evidence_item_value(item, "name")
        item_id = evidence_item_value(item, "id")
        if name and item_id:
            metadata = evidence_item_value(item, "metadata", {}) or {}
            files.append({
                "name": name,
                "display_name": evidence_display_name(name),
                "size": evidence_item_value(metadata, "size", 0) or 0,
                "path": f"{folder}/{name}",
            })
    return files


def validate_evidence_upload(uploaded_file) -> None:
    extension = os.path.splitext(uploaded_file.name or "")[1].lower()
    if extension not in EVIDENCE_ALLOWED_EXTENSIONS:
        raise ValueError("올릴 수 없는 파일 형식입니다. 사진(JPG·PNG·WEBP·HEIC), PDF, 녹음(MP3·M4A·WAV)만 올릴 수 있습니다.")
    if uploaded_file.size > EVIDENCE_MAX_FILE_BYTES:
        raise ValueError("파일 한 개는 10MB까지 올릴 수 있습니다. 크기를 줄이거나 나누어 올려 주세요.")
    if uploaded_file.size <= 0:
        raise ValueError("내용이 없는(0바이트) 파일은 올릴 수 없습니다. 파일을 다시 확인해 주세요.")


def upload_case_evidence(case_id: str, uploaded_file) -> None:
    validate_evidence_upload(uploaded_file)
    existing = list_case_evidence(case_id)
    if len(existing) >= EVIDENCE_MAX_FILES_PER_CASE:
        raise ValueError("사건 하나에는 파일을 20개까지 첨부할 수 있습니다. 필요 없는 파일을 지운 뒤 다시 시도해 주세요.")
    client = get_storage_authed_client()
    user_id = st.session_state.supabase_session["user_id"]
    safe_name = safe_evidence_filename(uploaded_file.name)
    object_path = f"{evidence_folder(user_id, case_id)}/{uuid.uuid4().hex}_{safe_name}"
    client.storage.from_(EVIDENCE_BUCKET).upload(
        object_path,
        uploaded_file.getvalue(),
        {"content-type": uploaded_file.type or "application/octet-stream", "cache-control": "3600", "upsert": "false"},
    )


def download_case_evidence(object_path: str):
    session = st.session_state.get("supabase_session")
    if not session or not session.get("user_id") or not object_path.startswith(f"{session['user_id']}/"):
        raise RuntimeError("접근 권한이 없습니다.")
    return get_storage_authed_client().storage.from_(EVIDENCE_BUCKET).download(object_path)


def delete_case_evidence_file(object_path: str) -> None:
    session = st.session_state.get("supabase_session")
    if not session or not session.get("user_id") or not object_path.startswith(f"{session['user_id']}/"):
        raise RuntimeError("접근 권한이 없습니다.")
    get_storage_authed_client().storage.from_(EVIDENCE_BUCKET).remove([object_path])


def delete_all_case_evidence(case_id: str) -> None:
    if not EVIDENCE_UPLOAD_ENABLED:
        return
    files = list_case_evidence(case_id)
    if files:
        get_storage_authed_client().storage.from_(EVIDENCE_BUCKET).remove([item["path"] for item in files])


def delete_case(case_id):
    session = st.session_state.get("supabase_session")
    if not session or not session.get("user_id"):
        raise RuntimeError("로그인이 필요합니다.")
    delete_all_case_evidence(case_id)
    client = get_authed_client()
    client.table(CASES_TABLE).delete().eq("case_id", case_id).eq("user_id", session["user_id"]).execute()


def privacy_v3_in_effect() -> bool:
    effective_date = parse_privacy_effective_date()
    return bool(
        privacy_config_ready()
        and effective_date
        and effective_date >= PRIVACY_V3_REVISED_DATE
        and today_kst() >= effective_date
    )


def draft_save_available() -> bool:
    return DRAFT_SAVE_ENABLED and privacy_v3_in_effect()


def verified_user_id(session: dict):
    """Supabase Auth에 액세스 토큰을 직접 확인해 받은 사용자 ID. 토큰마다 한 번만 확인하고 결과를 기억한다."""
    token = session.get("access_token")
    if not token:
        return None
    cache = st.session_state.get("_verified_token")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if cache and cache.get("digest") == digest:
        return cache.get("user_id")
    try:
        response = get_supabase_anon_client().auth.get_user(token)
        user_id = str(response.user.id) if response and response.user else None
    except Exception as error:
        log_auth_error(error)
        user_id = None
    st.session_state["_verified_token"] = {"digest": digest, "user_id": user_id}
    return user_id


def is_admin() -> bool:
    """관리자 판별: 목록에 있는 사용자 ID이면서, Supabase가 서버에서 그 토큰의 주인을 같은 ID로 확인해야 한다.

    브라우저가 보낸 값(입력한 이메일, 쿼리 파라미터 등)은 쓰지 않는다. session_state는 서버 메모리에 있고,
    사용자 ID와 토큰은 Supabase 로그인·갱신 응답에서만 채워진다.
    """
    if not ADMIN_USER_IDS:
        return False
    session = st.session_state.get("supabase_session") or {}
    user_id = (session.get("user_id") or "").lower()
    if user_id not in ADMIN_USER_IDS:
        return False
    return (verified_user_id(session) or "").lower() == user_id


def voice_admin_test_mode() -> bool:
    """공개 기능이 꺼져 있어도 관리자 이메일 계정이면 시험용으로 보여 준다."""
    return is_admin()


def render_admin_diagnostics() -> None:
    """운영자에게만 보이는 진단. 비밀값은 보여 주지 않고 '있음/없음'만 표시한다."""
    try:
        forwarded = st.context.headers.get("X-Forwarded-For")
        connection_ip = st.context.ip_address
    except Exception:
        forwarded, connection_ip = None, None
    detected = client_ip_from(forwarded, connection_ip)
    chain = forwarded_chain_summary(forwarded)
    with st.expander("🛠 운영자 진단 (관리자에게만 보임)"):
        st.markdown(
            f"- 이용자 공인 IP 감지: **{'됨' if detected else '안 됨'}** "
            f"(X-Forwarded-For 항목 {chain['entries']}개, 그중 공인 IP {chain['public']}개, "
            f"연결 IP {'공인' if client_ip_from(None, connection_ip) else '사설·없음'})\n"
            f"- 로그인 IP 전달 설정(AUTH_IP_FORWARDING_ENABLED): **{'켜짐' if AUTH_IP_FORWARDING_ENABLED else '꺼짐'}**, "
            f"secret 키: **{'설정됨' if os.environ.get('SUPABASE_SECRET_KEY') else '없음'}**\n"
            f"- 음성 입력 공개: **{'켜짐' if VOICE_INPUT_ENABLED and privacy_v3_in_effect() else '꺼짐'}**, "
            f"초안 저장: **{'켜짐' if draft_save_available() else '꺼짐'}**\n"
            f"- AI 키: **{'설정됨' if os.environ.get('GEMINI_API_KEY') else '없음'}** (값은 표시하지 않음)"
        )
        st.caption(
            "IP 감지가 '안 됨'이면 IP 전달 기능을 켜도 효과가 없습니다. '됨'이어도 Streamlit 앞단 프록시가 "
            "헤더를 덮어쓰는지(이용자가 넣은 값을 지우는지) 공개 자료로 확인되기 전에는 켜지 마세요."
        )


def voice_input_available() -> bool:
    return (VOICE_INPUT_ENABLED and privacy_v3_in_effect()) or voice_admin_test_mode()


def load_draft_index() -> dict:
    """이 계정의 저장된 초안 목록 {case_id: 마지막 저장 시각}. 본문은 열 때 따로 불러온다."""
    session = st.session_state.get("supabase_session") or {}
    response = (
        get_authed_client()
        .table(CASE_DRAFTS_TABLE)
        .select("case_id, updated_at")
        .eq("user_id", session.get("user_id", ""))
        .execute()
    )
    return {row["case_id"]: row.get("updated_at") for row in response.data or []}


def load_case_draft(case_id: str):
    session = st.session_state.get("supabase_session") or {}
    response = (
        get_authed_client()
        .table(CASE_DRAFTS_TABLE)
        .select("draft_text, updated_at")
        .eq("user_id", session.get("user_id", ""))
        .eq("case_id", str(case_id))
        .limit(1)
        .execute()
    )
    return (response.data or [None])[0]


def save_case_draft(case_id: str, draft_text: str) -> str:
    """초안을 저장(이미 있으면 덮어쓰기)하고 서버에 기록된 저장 시각을 돌려준다."""
    session = ensure_active_session()
    if not session or not session.get("user_id"):
        raise RuntimeError("로그인이 만료되었습니다. 다시 로그인해 주세요.")
    text = (draft_text or "").strip()
    if not text:
        raise ValueError("빈 초안은 저장할 수 없습니다. 내용을 적은 뒤 저장해 주세요.")
    if len(text) > CASE_DRAFT_MAX_CHARS:
        raise ValueError(f"초안은 {CASE_DRAFT_MAX_CHARS:,}자까지 저장할 수 있습니다. 조금 줄인 뒤 저장해 주세요.")
    response = (
        get_authed_client()
        .table(CASE_DRAFTS_TABLE)
        .upsert(
            {"user_id": session["user_id"], "case_id": str(case_id), "draft_text": text},
            on_conflict="user_id,case_id",
        )
        .execute()
    )
    rows = response.data or []
    return rows[0].get("updated_at") if rows else datetime.datetime.now(KST).isoformat()


def map_draft_save_error(error: Exception) -> str:
    if isinstance(error, ValueError):
        return str(error)
    message = str(getattr(error, "message", None) or error).lower()
    if "로그인" in str(error) or "jwt" in message:
        return "로그인이 만료되어 저장하지 못했습니다. 고친 내용을 TXT로 내려받아 두신 뒤 다시 로그인해 주세요."
    if "row-level security" in message:
        return "이 사건에 초안을 저장할 권한을 확인하지 못했습니다. 새로고침한 뒤 다시 시도해 주세요."
    return "초안을 저장하지 못했습니다. 고친 내용은 화면에 남아 있으니 잠시 후 '초안 저장'을 다시 눌러 주세요." + contact_hint()


def count_user_drafts() -> int:
    return len(load_draft_index())


GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_TIMEOUT_MS = 90_000
# 유료 API 비용 보호: 계정마다 1시간에 AI 호출(초안 생성·상담)을 이 횟수까지만 허용한다.
AI_CALLS_PER_HOUR = int(os.environ.get("AI_CALLS_PER_HOUR") or 30)
# 전체 이용자를 합친 1시간 호출 상한. 이용자가 많아져도 비용이 이 범위를 넘지 않게 한다.
AI_CALLS_PER_HOUR_TOTAL = int(os.environ.get("AI_CALLS_PER_HOUR_TOTAL") or 300)


class AiUsageLimitError(RuntimeError):
    pass


class AiUsageLimiter:
    """서버 메모리에 계정별 최근 1시간 호출 시각을 둔다. 앱이 다시 시작되면 초기화된다."""

    def __init__(self):
        self._lock = threading.Lock()
        self._calls = {}

    def allow(self, user_id: str, limit: int) -> bool:
        now = time.time()
        with self._lock:
            recent = [t for t in self._calls.get(user_id, []) if t > now - 3600]
            if len(recent) >= limit:
                self._calls[user_id] = recent
                return False
            recent.append(now)
            self._calls[user_id] = recent
            return True

    def refund(self, user_id: str) -> None:
        """전체 상한에 걸려 실제로 호출하지 않은 경우 계정 사용량을 되돌린다."""
        with self._lock:
            if self._calls.get(user_id):
                self._calls[user_id].pop()


@st.cache_resource
def get_ai_usage_limiter() -> AiUsageLimiter:
    return AiUsageLimiter()


def call_gemini(contents, system_instruction=None) -> str:
    session = st.session_state.get("supabase_session") or {}
    limiter = get_ai_usage_limiter()
    if not limiter.allow(session.get("user_id", ""), AI_CALLS_PER_HOUR):
        raise AiUsageLimitError(
            f"AI 기능은 계정마다 1시간에 {AI_CALLS_PER_HOUR}번까지 쓸 수 있습니다. 잠시 후 다시 시도해 주세요."
        )
    if not limiter.allow("__all_users__", AI_CALLS_PER_HOUR_TOTAL):
        limiter.refund(session.get("user_id", ""))
        raise AiUsageLimitError(
            "지금 이용자가 많아 AI 기능을 잠시 쉬고 있습니다. 10~20분 뒤에 다시 시도해 주세요. 저장한 사건은 그대로 있습니다."
        )
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=system_instruction) if system_instruction else None,
    )
    text = (getattr(response, "text", None) or "").strip()
    if not text:
        # 안전 필터 등으로 빈 응답이 오면 'None'이 초안에 들어가지 않도록 실패로 처리한다.
        raise RuntimeError("empty AI response")
    return text


def ai_error_message(error: Exception, default: str) -> str:
    if isinstance(error, AiUsageLimitError):
        return str(error)
    text = f"{type(error).__name__} {error}".lower()
    if "timeout" in text or "timed out" in text:
        return "AI 응답이 90초 넘게 걸려 멈췄습니다. 잠시 후 다시 시도해 주세요. 입력한 내용과 저장한 사건은 그대로 있습니다."
    if "429" in text or "resource_exhausted" in text or "quota" in text:
        return "AI 서비스 사용량이 잠시 몰렸습니다. 몇 분 뒤에 다시 시도해 주세요." + contact_hint()
    return default + contact_hint()


def generate_case_draft(case):
    today_str = datetime.datetime.now(KST).date().isoformat()
    prompt = build_draft_prompt(case, today_str)
    text = call_gemini([{"role": "user", "parts": [{"text": prompt}]}])
    return f"{text}\n\n{DRAFT_DISCLAIMER}"


def has_date_cue(question):
    return (
        any(word in question for word in DATE_WORDS)
        or re.search(r"\d{1,2}월\s*\d{1,2}일", question)
        or re.search(r"\d{1,2}시", question)
    )


def is_cancel_request(question):
    return any(trigger in question for trigger in CANCEL_TRIGGERS)


def is_schedule_add_request(question):
    return any(word in question for word in ("추가", "등록", "저장", "잡아", "만들어", "넣어")) and ("일정" in question or has_date_cue(question))
def is_schedule_query(question):
    return "일정" in question and not is_cancel_request(question)


def build_system_instruction():
    return "\n\n".join([SERVICE_IDENTITY_INSTRUCTION, SAFE_DRAFT_INSTRUCTION])


def process_question(question, history):
    question = unicodedata.normalize("NFC", question)

    if is_cancel_request(question) and ("일정" in question or has_date_cue(question)):
        return UNSUPPORTED_ASSISTANT_MESSAGE

    if is_schedule_add_request(question):
        return UNSUPPORTED_ASSISTANT_MESSAGE

    if (
        ("캘린더" in question)
        or ("구글 일정" in question)
        or (has_date_cue(question) and ("일정" in question or "스케줄" in question))
    ):
        return UNSUPPORTED_ASSISTANT_MESSAGE

    if any(trigger in question for trigger in MEMORY_TRIGGERS):
        return UNSUPPORTED_ASSISTANT_MESSAGE

    if is_schedule_query(question):
        return UNSUPPORTED_ASSISTANT_MESSAGE

    contents = history[-15:] + [{"role": "user", "parts": [{"text": question}]}]
    answer = call_gemini(contents, build_system_instruction())
    # 답변을 받은 뒤에만 대화 기록에 넣어, 실패한 질문이 다음 요청에 남지 않게 한다.
    history.append({"role": "user", "parts": [{"text": question}]})
    history.append({"role": "model", "parts": [{"text": answer}]})
    if len(history) > 16:
        history[:] = history[-16:]
    return answer


BRAND_CSS = """
<style>
:root {
    --dgo-navy: #0B1F3A;
    --dgo-navy-light: #13294B;
    --dgo-blue: #2F80ED;
    --dgo-blue-light: #EAF2FE;
    --dgo-muted: #5B6B82;
}

.dgo-hero {
    text-align: center;
    padding: 0.5rem 0 1.25rem 0;
}

.dgo-hero-title {
    font-size: 2.1rem;
    font-weight: 800;
    color: var(--dgo-navy);
    letter-spacing: -0.02em;
}

.dgo-hero-subtitle {
    margin-top: 0.4rem;
    font-size: 1.02rem;
    color: var(--dgo-muted);
}

.dgo-card {
    background-color: var(--dgo-blue-light);
    border: 1px solid #D3E4FB;
    border-radius: 14px;
    padding: 1.1rem 0.8rem;
    text-align: center;
    font-size: 1.4rem;
    color: var(--dgo-navy);
    margin-bottom: 0.75rem;
}

.dgo-card b {
    display: block;
    margin-top: 0.35rem;
    font-size: 0.95rem;
    font-weight: 700;
}

.dgo-footer {
    margin-top: 2rem;
    padding-top: 1rem;
    border-top: 1px solid #E3E8EF;
    text-align: center;
    font-size: 0.82rem;
    color: var(--dgo-muted);
}

[data-testid="stButton"] button,
[data-testid="stFormSubmitButton"] button,
[data-testid="stDownloadButton"] button {
    background-color: var(--dgo-blue);
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    font-weight: 600;
}

[data-testid="stButton"] button:disabled {
    background-color: #E3E8EF;
    color: var(--dgo-muted);
}

[data-testid="stButton"] button:hover,
[data-testid="stFormSubmitButton"] button:hover,
[data-testid="stDownloadButton"] button:hover {
    background-color: var(--dgo-navy-light);
    color: #FFFFFF;
}

@media (max-width: 640px) {
    .dgo-hero-title {
        font-size: 1.6rem;
    }
    .dgo-hero-subtitle {
        font-size: 0.92rem;
    }
    .dgo-card {
        font-size: 1.2rem;
    }
}

@media (max-width: 768px) {
    html, body {
        -webkit-text-size-adjust: 100%;
        text-size-adjust: 100%;
    }

    .dgo-card {
        padding: 0.6rem 0.7rem;
        margin-bottom: 0.4rem;
    }

    [data-testid="stMarkdownContainer"] p,
    [data-testid="stMarkdownContainer"] li {
        font-size: 0.9rem;
        line-height: 1.5;
    }

    [data-testid="stTextArea"] textarea {
        height: 120px !important;
        min-height: 120px !important;
        resize: vertical;
    }

    [data-testid="stTextInput"] input,
    [data-testid="stButton"] button,
    [data-testid="stFormSubmitButton"] button,
    [data-testid="stDownloadButton"] button {
        min-height: 44px;
    }

    /* 휴대폰에서 버튼을 손가락으로 누르기 쉽게 가로 전체로 넓힌다. */
    [data-testid="stElementContainer"]:has([data-testid="stButton"], [data-testid="stFormSubmitButton"], [data-testid="stDownloadButton"]),
    [data-testid="stElementContainer"]:has([data-testid="stButton"], [data-testid="stFormSubmitButton"], [data-testid="stDownloadButton"]) > div,
    [data-testid="stButton"],
    [data-testid="stFormSubmitButton"],
    [data-testid="stDownloadButton"],
    [data-testid="stButton"] button,
    [data-testid="stFormSubmitButton"] button,
    [data-testid="stDownloadButton"] button {
        width: 100% !important;
    }

    /* iOS는 16px보다 작은 입력칸에 초점이 가면 화면을 확대하므로 글자 크기를 맞춘다. */
    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea,
    [data-testid="stChatInput"] textarea {
        font-size: 16px !important;
    }

    /* 초안 편집칸은 긴 문서이므로 위의 120px 제한보다 크게 보여 준다. */
    [class*="st-key-case_draft_editor"] textarea,
    [class*="st-key-list_draft_editor_"] textarea {
        height: 360px !important;
        min-height: 360px !important;
    }

    /* 탭이 많아도 가로로 밀어서 볼 수 있게 한다. */
    [data-baseweb="tab-list"] {
        overflow-x: auto;
        scrollbar-width: none;
    }
    [data-baseweb="tab"] {
        flex-shrink: 0;
        padding-left: 0.6rem;
        padding-right: 0.6rem;
    }

    /* 휴대폰에서는 단계 카드를 숨겨 입력칸이 먼저 보이게 한다. 같은 단계는 탭 안의 안내 문구로 보여 준다. */
    .dgo-card,
    [data-testid="stHorizontalBlock"]:has(.dgo-card) {
        display: none;
    }
}

.dgo-google-btn,
.dgo-google-btn:link,
.dgo-google-btn:visited {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    min-height: 40px;
    padding: 0 16px;
    margin: 0.25rem 0 0.5rem 0;
    background-color: #FFFFFF;
    border: 1px solid #747775;
    border-radius: 8px;
    color: #1F1F1F !important;
    font-family: "Roboto", Arial, sans-serif;
    font-size: 14px;
    font-weight: 500;
    text-decoration: none !important;
}

.dgo-google-btn:hover {
    background-color: #F8F9FA;
    box-shadow: 0 1px 2px rgba(60, 64, 67, 0.3);
}

.dgo-google-btn:focus-visible {
    outline: 2px solid var(--dgo-blue);
    outline-offset: 2px;
}

@media (max-width: 768px) {
    .dgo-google-btn {
        min-height: 44px;
    }
}

#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
[data-testid="stToolbar"] {visibility: hidden;}
[data-testid="stAppDeployButton"] {display: none;}
</style>
"""


def build_privacy_policy_text():
    effective_date = parse_privacy_effective_date()
    args = (PRIVACY_OPERATOR_NAME, PRIVACY_CONTACT_EMAIL, PRIVACY_POLICY_EFFECTIVE_DATE)
    if effective_date and effective_date >= PRIVACY_V3_REVISED_DATE:
        return build_privacy_policy_v3(*args)
    return build_privacy_policy_v2(*args)


@st.dialog("개인정보처리방침", width="large")
def render_privacy_policy_dialog():
    if not privacy_config_ready():
        st.info("개인정보처리방침을 준비 중입니다. 잠시 후 다시 확인해 주세요.")
    else:
        st.markdown(build_privacy_policy_text())


def build_terms_of_service_text():
    return f"""
### 이용약관

이 약관은 {PRIVACY_OPERATOR_NAME}(이하 "운영자")가 개인 자격으로 제공하는 "고소음이"(이하 "서비스")의 이용에 관한 조건을 정합니다.
이 약관은 회원가입 과정에서 이용약관 필수 동의 항목에 체크하고 가입을 완료한 이용자에게 적용됩니다.

**제1조 (목적)**
이 약관은 운영자가 제공하는 서비스의 이용조건 및 절차, 운영자와 이용자의 권리·의무 및 책임사항을 규정함을 목적으로 합니다.

**제2조 (서비스의 내용과 법률자문 비대체)**
1. 서비스는 이용자가 입력한 피해 상황을 정리하고, 고소장 등 문서의 참고용 초안을 작성하도록 돕는 법률정보 제공 및 문서작성 보조 서비스입니다.
2. 서비스는 변호사가 아니며, 서비스가 제공하는 정보와 문서는 변호사의 법률자문이나 소송대리를 대체하지 않습니다. 서비스 이용만으로 특정 법적 결과(고소 성립, 수사 개시, 처벌 등)가 보장되지 않습니다.
3. 구체적인 법적 판단이 필요한 경우 이용자는 반드시 변호사 등 관련 분야 전문가와 별도로 상담해야 합니다.

**제3조 (AI 생성 결과의 한계와 전문가 검토 안내)**
1. 서비스가 AI를 통해 생성하는 상담 응답 및 고소장 초안(이하 "AI 생성 결과")은 이용자가 입력한 정보를 바탕으로 자동 생성된 참고 자료이며, 그 정확성·완전성·최신 법령 반영 여부를 보장하지 않습니다.
2. 이용자는 AI 생성 결과를 실제 문서로 제출하거나 법적 판단의 근거로 사용하기 전에 반드시 그 내용을 직접 확인하고, 필요한 경우 변호사 등 전문가의 검토를 받아야 합니다.
3. AI 생성 결과를 검토 없이 그대로 사용함에 따라 발생하는 불이익에 대해 운영자는 제9조(책임의 제한)에서 정하는 범위를 초과하여 책임지지 않습니다.

**제4조 (이용 자격)**
서비스는 만 14세 이상만 이용할 수 있습니다. 만 14세 미만인 경우 회원가입 및 서비스 이용이 제한됩니다.

**제5조 (계정 관리)**
1. 이용자는 회원가입 시 정확한 정보를 제공해야 하며, 이메일과 비밀번호 등 계정 정보를 스스로의 책임으로 관리해야 합니다.
2. 이용자는 자신의 계정을 제3자에게 공유·양도·대여할 수 없습니다.
3. 계정 정보의 도용이나 무단 사용을 인지한 경우 이용자는 지체 없이 운영자에게 통지해야 하며, 통지 전까지 발생한 불이익에 대해 운영자는 책임지지 않습니다. 다만 운영자의 고의 또는 중과실이 있는 경우는 제외합니다.

**제6조 (이용자의 의무와 금지행위)**
1. 이용자는 사건 정보를 입력할 때 사실에 부합하는 내용을 입력해야 하며, 상대방 등 제3자의 개인정보는 이용자가 적법하게 제공할 수 있는 범위 내에서 필요한 최소한만 입력해야 합니다.
2. 이용자는 다음 각 호의 행위를 해서는 안 됩니다.
   - 허위임을 알면서 사건 정보를 입력하거나 무고·협박 등 불법적인 목적으로 이용하는 행위
   - 타인을 사칭하거나 타인의 명예를 훼손·비방할 목적으로 서비스를 이용하는 행위
   - 법령을 위반하거나 공서양속에 반하는 목적으로 서비스를 이용하는 행위
   - 서비스를 무단으로 복제·역설계하거나 자동화된 수단(크롤링 등)으로 접근하는 행위
   - 서비스의 정상적인 운영을 방해하는 행위
3. 이용자가 위 의무를 위반해 발생한 결과에 대한 책임은 이용자 본인에게 있습니다.

**제7조 (서비스의 변경 및 중단)**
1. 서비스는 개인이 운영하는 베타 성격의 서비스로, 운영자는 서비스의 전부 또는 일부를 변경하거나 중단할 수 있습니다.
2. 운영자는 서비스 내용을 변경하거나 중단하는 경우 사전에 서비스 내 공지 등 합리적인 방법으로 이용자에게 알립니다. 다만 긴급한 장애 대응 등 부득이한 사유가 있는 경우 사후에 알릴 수 있습니다.

**제8조 (지식재산권)**
1. 이용자는 자신이 입력하는 정보에 대해 서비스에 제공할 적법한 권한을 보유해야 합니다.
2. 운영자는 서비스 제공(저장, AI 처리, 화면 표시 등) 목적의 범위 내에서만 이용자가 입력한 정보를 사용하며, 이를 초과하는 방식으로 이용하지 않습니다.
3. 서비스의 명칭, 디자인, 소프트웨어 등 서비스 자체에 관한 권리는 운영자 또는 정당한 권리자에게 있으며, 이용자는 운영자의 사전 동의 없이 이를 복제·배포·상업적으로 이용할 수 없습니다.

**제9조 (책임의 제한)**
1. 운영자는 천재지변, 불가항력, 이용자의 귀책사유 등 운영자가 통제할 수 없는 사유로 발생한 손해에 대해 책임지지 않습니다.
2. 운영자는 AI 생성 결과의 내용을 이용자가 검증 없이 그대로 사용함으로써 발생한 손해에 대해, 운영자에게 고의 또는 중과실이 없는 한 책임지지 않습니다.
3. 이 조는 운영자의 고의 또는 중과실로 인한 책임이나 관련 법령상 제한할 수 없는 책임까지 면제하는 것은 아닙니다.

**제10조 (약관의 변경)**
1. 운영자는 관련 법령을 위반하지 않는 범위에서 이 약관을 변경할 수 있습니다.
2. 약관이 변경되는 경우 운영자는 변경 내용, 시행일 및 변경 사유를 서비스 내 공지 등의 방법으로 시행일 이전에 안내합니다.
3. 관련 법령상 이용자의 별도 동의가 필요한 변경의 경우에는 이용자의 동의를 받습니다.

**제11조 (개인정보 보호)**
운영자는 관련 법령에 따라 이용자의 개인정보를 보호합니다. 개인정보의 수집 항목, 이용 목적, 보유기간, 제3자 제공 및 국외 이전에 관한 사항은 이 약관이 아닌 별도의 "개인정보처리방침"에 따릅니다.

**제12조 (준거법 및 분쟁 해결)**
1. 이 약관 및 서비스 이용과 관련하여 운영자와 이용자 사이에 발생한 분쟁에는 대한민국 법을 적용합니다.
2. 분쟁에 관한 소송이 제기되는 경우 관련 법령에 따른 관할법원에 제기합니다.

**제13조 (운영자·문의처·시행일)**
- 운영자: {PRIVACY_OPERATOR_NAME}
- 문의 이메일: {PRIVACY_CONTACT_EMAIL}
- 이 약관은 {TERMS_EFFECTIVE_DATE}부터 적용됩니다.
"""


@st.dialog("이용약관", width="large")
def render_terms_of_service_dialog():
    if not terms_config_ready():
        st.info("이용약관을 준비 중입니다. 잠시 후 다시 확인해 주세요.")
    else:
        st.markdown(build_terms_of_service_text())


def format_file_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}MB"
    return f"{max(1, round(size / 1024))}KB"


def render_case_evidence_manager(case_id: str) -> None:
    with st.expander("증거파일 첨부·관리"):
        st.caption(
            "올릴 수 있는 파일: 사진(JPG·PNG·WEBP·HEIC), PDF, 녹음(MP3·M4A·WAV) · "
            "파일 한 개 10MB까지 · 사건 하나에 20개까지 · 나만 볼 수 있게 비공개로 저장됩니다."
        )
        st.warning(
            "주민등록번호, 계좌 비밀번호, 카드번호, 인증번호처럼 사건과 관계없는 민감한 정보는 "
            "가린 뒤 올려 주세요."
        )
        uploads = st.file_uploader(
            "올릴 파일 고르기",
            type=["jpg", "jpeg", "png", "webp", "heic", "heif", "pdf", "mp3", "m4a", "wav"],
            accept_multiple_files=True,
            key=f"evidence_upload_{case_id}",
            help="여러 개를 한 번에 고를 수 있습니다. 10MB보다 큰 파일은 크기를 줄이거나 나누어 올려 주세요.",
        )
        consent = st.checkbox(
            "[필수] 고른 파일을 비공개로 저장하고 사건 관리에 쓰는 데 동의합니다.",
            key=f"evidence_consent_{case_id}",
        )
        if st.button("고른 파일 올리기", key=f"evidence_upload_btn_{case_id}"):
            if not uploads:
                st.warning("먼저 위에서 올릴 파일을 골라 주세요.")
            elif not consent:
                st.warning("파일을 올리려면 저장·이용 동의에 체크해 주세요.")
            else:
                uploaded_count = 0
                errors = []
                with st.spinner("파일을 올리고 있습니다. 창을 닫지 말고 기다려 주세요."):
                    for uploaded_file in uploads:
                        try:
                            upload_case_evidence(case_id, uploaded_file)
                            uploaded_count += 1
                        except ValueError as exc:
                            errors.append(f"{uploaded_file.name}: {exc}")
                        except Exception as exc:
                            log_evidence_upload_error("upload", exc)
                            errors.append(f"{uploaded_file.name}: {map_evidence_upload_error(exc)}")
                if uploaded_count:
                    st.session_state[f"evidence_notice_{case_id}"] = f"파일 {uploaded_count}개를 올렸습니다."
                for error in errors:
                    st.error(error)
                if errors and uploaded_count:
                    st.info(f"나머지 파일 {uploaded_count}개는 정상적으로 올라갔습니다. 위 파일만 다시 확인해 주세요.")
                if uploaded_count and not errors:
                    st.rerun()

        notice = st.session_state.pop(f"evidence_notice_{case_id}", None)
        if notice:
            st.success(notice)

        try:
            files = list_case_evidence(case_id)
        except Exception as exc:
            log_evidence_upload_error("list", exc)
            st.error("첨부한 파일 목록을 불러오지 못했습니다. 새로고침한 뒤 다시 열어 주세요.")
            return

        if not files:
            st.caption("아직 첨부한 파일이 없습니다. 위에서 파일을 골라 올려 보세요.")
            return

        st.caption(f"첨부한 파일 {len(files)}개 / 최대 {EVIDENCE_MAX_FILES_PER_CASE}개")
        selected = st.selectbox(
            "첨부한 파일",
            files,
            format_func=lambda item: f"{item['display_name']} ({format_file_size(item['size'])})",
            key=f"evidence_select_{case_id}",
        )
        download_key = f"evidence_download_{case_id}"
        col_prepare, col_remove = st.columns(2)
        with col_prepare:
            if st.button("내려받기 준비", key=f"evidence_prepare_{case_id}"):
                try:
                    with st.spinner("파일을 불러오고 있습니다."):
                        st.session_state[download_key] = {
                            "path": selected["path"],
                            "name": selected["display_name"],
                            "data": download_case_evidence(selected["path"]),
                        }
                except Exception as exc:
                    log_evidence_upload_error("download", exc)
                    st.error("파일을 불러오지 못했습니다. 새로고침한 뒤 다시 시도해 주세요.")
        with col_remove:
            confirm_remove = st.checkbox("이 파일 삭제 확인", key=f"evidence_delete_confirm_{case_id}")
            if st.button("고른 파일 삭제", key=f"evidence_delete_{case_id}", disabled=not confirm_remove):
                try:
                    delete_case_evidence_file(selected["path"])
                except Exception as exc:
                    log_evidence_upload_error("delete", exc)
                    st.error("파일을 삭제하지 못했습니다. 새로고침한 뒤 다시 시도해 주세요.")
                else:
                    st.session_state.pop(download_key, None)
                    st.session_state.pop(f"evidence_delete_confirm_{case_id}", None)
                    st.session_state[f"evidence_notice_{case_id}"] = "파일을 삭제했습니다."
                    st.rerun()

        prepared = st.session_state.get(download_key)
        if prepared and prepared.get("path") == selected["path"]:
            st.download_button(
                "파일 내려받기",
                data=prepared["data"],
                file_name=prepared["name"],
                key=f"evidence_download_btn_{case_id}",
            )


def render_brand_header():
    st.markdown(
        '<div class="dgo-hero">'
        '<div class="dgo-hero-title">⚖️ 고소음이</div>'
        '<div class="dgo-hero-subtitle">어려운 피해 상황을 정리하고, 고소장 초안 작성을 돕는 AI 법률정보 도우미</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def contact_hint() -> str:
    """운영자가 설정한 문의 이메일이 있을 때만 안내 문구를 만든다. 연락처를 임의로 만들지 않는다."""
    if PRIVACY_CONTACT_EMAIL:
        return f" 문제가 계속되면 {PRIVACY_CONTACT_EMAIL} 로 알려 주세요."
    return ""


def render_upcoming_privacy_notice() -> None:
    """개정 방침 시행 전에 바뀌는 내용을 미리 볼 수 있게 한다. 시행일이 지나면 자동으로 사라진다."""
    try:
        upcoming = datetime.date.fromisoformat(PRIVACY_POLICY_UPCOMING_DATE or "")
    except ValueError:
        return
    current = parse_privacy_effective_date()
    if not privacy_config_ready() or upcoming < PRIVACY_V3_REVISED_DATE or today_kst() >= upcoming:
        return
    if current and current >= upcoming:
        return
    with st.expander(f"📢 개인정보처리방침이 {upcoming.isoformat()}부터 바뀝니다 (눌러서 보기)"):
        rows = "\n".join(f"| {section} | {before} | {after} |" for section, before, after in CHANGES_V2_TO_V3)
        st.markdown(
            "**변경 전·후 비교**\n\n| 항목 | 변경 전 | 변경 후 |\n|---|---|---|\n" + rows + "\n\n"
            "시행일 이후 처음 이용하실 때 바뀐 방침에 다시 동의해 주셔야 합니다. 동의하지 않으시면 회원탈퇴할 수 있습니다."
        )
        st.markdown("**바뀐 개인정보처리방침 전문**")
        st.markdown(build_privacy_policy_v3(PRIVACY_OPERATOR_NAME, PRIVACY_CONTACT_EMAIL, upcoming.isoformat()))


def render_footer():
    contact = ""
    if PRIVACY_CONTACT_EMAIL:
        email = html.escape(PRIVACY_CONTACT_EMAIL, quote=True)
        contact = f'<br>문의: <a href="mailto:{email}">{email}</a>'
    st.markdown(
        '<div class="dgo-footer">'
        '고소음이는 법률정보와 문서 작성을 돕는 서비스이며, 변호사의 법률자문을 대체하지 않습니다.'
        '<br>지금 위험하거나 급한 상황이라면 112에 먼저 신고하세요.'
        f'{contact}'
        '</div>',
        unsafe_allow_html=True,
    )


def md_escape(text) -> str:
    # 사용자가 적은 *, _, # 등이 마크다운 서식으로 바뀌지 않게 하고 줄바꿈은 유지한다.
    escaped = re.sub(r"([\\`*_{}\[\]()#+\-.!|~<>])", r"\\\1", str(text or ""))
    return escaped.replace("\n", "  \n")


def format_created_at(value) -> str:
    try:
        created = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "날짜 확인 필요"
    if created.tzinfo:
        created = created.astimezone(KST)
    return created.strftime("%Y-%m-%d %H:%M")


def render_damage_type_guide(case_type: str, show_qa: bool) -> None:
    guide = damage_type_guide(case_type)
    route = ROUTES.get(guide.get("route"))
    if guide.get("urgent"):
        st.error("🚨 " + guide["urgent"])
    if route:
        message = f"**{route['label']}**  \n{route['note']}"
        if guide.get("route_detail"):
            message += f"  \n{guide['route_detail']}"
        if guide["route"] == "criminal":
            st.info(message)
        else:
            st.warning(message)
    if guide.get("summary"):
        st.caption(f"이런 경우: {guide['summary']}")

    with st.expander("이 유형에서 함께 적으면 좋은 내용 · 준비할 자료", expanded=show_qa):
        st.markdown("**사건 경위에 함께 적어 주세요**\n" + "\n".join(f"- {item}" for item in guide["checklist"]))
        st.markdown("**모아 두면 좋은 자료**\n" + "\n".join(f"- {item}" for item in guide["evidence_tips"]))
        st.caption("자료는 원본을 지우지 말고 보관하세요. 자료를 얻으려고 위험한 행동을 하지는 마세요.")

    if guide.get("qa"):
        if show_qa:
            st.markdown("**자주 묻는 질문**")
            for question, answer in guide["qa"]:
                with st.expander(question):
                    st.markdown(answer)
        else:
            with st.expander(f"이 유형의 자주 묻는 질문 ({len(guide['qa'])}개)"):
                for question, answer in guide["qa"]:
                    st.markdown(f"**Q. {question}**\n\n{answer}")

    if guide.get("sources"):
        links = " · ".join(f"[{label}]({url})" for label, url in guide["sources"])
        st.markdown(f"공식 안내: {links}")


def show_new_draft(case_entry: dict, draft_text: str) -> None:
    st.session_state.case_draft = draft_text
    st.session_state.case_draft_id = case_entry["case_id"]
    st.session_state.case_draft_case = case_entry
    st.session_state.pop("case_draft_editor", None)
    st.session_state.pop("case_draft_editor_saved", None)
    if draft_save_available():
        # 처음 만든 초안은 바로 저장해 두어, 창을 닫아도 '내 사건'에서 다시 열 수 있게 한다.
        try:
            saved_at = save_case_draft(case_entry["case_id"], draft_text)
        except Exception as error:
            log_auth_error(error)
            st.session_state.case_draft_editor_error = (
                "초안은 만들었지만 자동 저장하지 못했습니다. 아래에서 '초안 저장'을 눌러 주세요."
            )
        else:
            st.session_state.case_draft_editor_saved = {"text": draft_text.strip(), "at": saved_at}


def reset_case_form() -> None:
    for key in list(st.session_state.keys()):
        if key.startswith(("case_field_", "case_type_", "case_draft_editor", "case_data_consent")):
            del st.session_state[key]
    st.session_state.case_draft = None
    st.session_state.case_draft_id = None
    st.session_state.case_draft_case = None
    st.session_state.case_review = None
    st.session_state.case_notice = None


def restore_original_draft(editor_key: str, original: str) -> None:
    st.session_state[editor_key] = original


def save_draft_from_editor(editor_key: str, case_id: str) -> None:
    text = st.session_state.get(editor_key, "")
    try:
        saved_at = save_case_draft(case_id, text)
    except Exception as error:
        if not isinstance(error, ValueError):
            log_auth_error(error)
        st.session_state[f"{editor_key}_error"] = map_draft_save_error(error)
    else:
        st.session_state[f"{editor_key}_saved"] = {"text": text.strip(), "at": saved_at}
        st.session_state[f"{editor_key}_notice"] = "초안을 저장했습니다. '내 사건'에서 언제든 다시 열어 이어서 고칠 수 있습니다."
        st.session_state.pop(f"{editor_key}_error", None)


def render_draft_panel(editor_key: str, original: str, case: dict, case_id: str) -> None:
    can_save = draft_save_available()
    saved = st.session_state.get(f"{editor_key}_saved")
    st.markdown("#### 고소장 초안")
    st.caption(
        "AI가 입력하신 내용만으로 만든 참고용 초안입니다. 꼭 직접 읽고 고쳐 주세요. "
        "고친 내용은 칸 바깥을 한 번 누르거나 Ctrl+Enter(맥은 ⌘+Enter)를 누르면 저장·내려받기에 반영됩니다."
    )
    if editor_key not in st.session_state:
        st.session_state[editor_key] = saved["text"] if saved else original

    missing = missing_info_items(case)
    if missing:
        st.warning(
            "제출 전에 확인할 항목입니다. 초안의 '[확인 필요]' 부분을 채우거나 지워 주세요.\n\n"
            + "\n".join(f"- {item}" for item in missing)
        )

    edited = st.text_area(
        "초안 내용 (직접 고칠 수 있어요)", key=editor_key, height=420, max_chars=CASE_DRAFT_MAX_CHARS
    )

    unsupported = find_unsupported_details(edited, case, datetime.datetime.now(KST).date().isoformat())
    if unsupported:
        st.warning(
            "입력하신 내용에 없던 정보가 초안에 들어갔을 수 있습니다. 아래 부분을 꼭 확인해 주세요.\n\n"
            + "\n".join(f"- {item}" for item in unsupported)
        )

    notice = st.session_state.pop(f"{editor_key}_notice", None)
    if notice:
        st.success(notice)
    error = st.session_state.pop(f"{editor_key}_error", None)
    if error:
        st.error(error)

    if can_save:
        unsaved = not saved or edited.strip() != saved["text"]
        if saved:
            st.caption(f"마지막 저장: {format_created_at(saved['at'])}")
        if unsaved:
            st.caption("⚠️ 저장하지 않은 수정 내용이 있습니다. 창을 닫거나 새로고침하기 전에 '초안 저장'을 눌러 주세요.")
        st.button(
            "초안 저장" if unsaved else "✓ 저장됨",
            key=f"{editor_key}_save",
            type="primary",
            disabled=not unsaved,
            on_click=save_draft_from_editor,
            args=(editor_key, case_id),
        )
    elif edited != original:
        st.caption("✏️ 직접 고친 내용이 TXT·Word 내려받기에 그대로 반영됩니다.")

    file_stem = f"case_draft_{case_id}"
    col_txt_download, col_docx_download = st.columns(2)
    with col_txt_download:
        st.download_button(
            "TXT로 내려받기",
            data=edited,
            file_name=f"{file_stem}.txt",
            mime="text/plain",
            key=f"{editor_key}_txt",
        )
    with col_docx_download:
        st.download_button(
            "Word로 내려받기",
            data=build_case_draft_docx(edited),
            file_name=f"{file_stem}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key=f"{editor_key}_docx",
        )
    baseline, baseline_label = (saved["text"], "마지막으로 저장한 초안으로 되돌리기") if saved else (
        original, "처음 만든 초안으로 되돌리기"
    )
    if edited.strip() != baseline.strip():
        st.button(
            baseline_label,
            key=f"{editor_key}_restore",
            on_click=restore_original_draft,
            args=(editor_key, baseline),
        )
    if not can_save:
        st.caption(
            "초안은 서버에 저장되지 않습니다. 필요하면 내려받아 보관하세요. "
            "저장한 사건 내용으로 '내 사건' 탭에서 언제든 다시 만들 수 있습니다."
        )


def open_saved_draft(case_id: str) -> None:
    editor_key = f"list_draft_editor_{case_id}"
    try:
        row = load_case_draft(case_id)
    except Exception as error:
        log_auth_error(error)
        st.session_state[f"{editor_key}_error"] = "저장된 초안을 불러오지 못했습니다. 새로고침한 뒤 다시 눌러 주세요." + contact_hint()
        return
    if not row:
        st.session_state[f"{editor_key}_error"] = "저장된 초안을 찾지 못했습니다. 다른 탭에서 사건을 삭제했는지 확인해 주세요."
        return
    st.session_state[f"list_draft_{case_id}"] = row["draft_text"]
    st.session_state[f"{editor_key}_saved"] = {"text": row["draft_text"].strip(), "at": row.get("updated_at")}
    st.session_state[editor_key] = row["draft_text"]


def voice_widget_suffix() -> int:
    return st.session_state.setdefault("voice_widget_n", 0)


def purge_uploaded_audio(uploaded) -> bool:
    """서버 메모리의 업로드 저장소에서 음성파일을 지운다.

    녹음·업로드한 음성은 Streamlit 업로드 저장소(서버 메모리)에 세션이 끝날 때까지 남는다.
    위젯 상태만 지워서는 이 저장소가 비지 않으므로, 파일 ID로 직접 지운다.
    (Streamlit 1.61의 ScriptRunContext.uploaded_file_mgr를 쓴다. 버전을 올리면 tests의 확인을 다시 돌린다.)
    """
    file_id = getattr(uploaded, "file_id", None)
    ctx = get_script_run_ctx()
    if not file_id or ctx is None or ctx.uploaded_file_mgr is None:
        return False
    try:
        ctx.uploaded_file_mgr.remove_file(session_id=ctx.session_id, file_id=file_id)
    except Exception as error:
        print(f"[voice] stage=purge result={type(error).__name__}", flush=True)
        return False
    return True


def discard_voice_audio() -> None:
    """녹음·업로드 위젯과 서버 업로드 저장소에서 음성을 지운다. 위젯 키를 바꿔 화면의 녹음도 비운다."""
    n = voice_widget_suffix()
    for key in (f"voice_record_{n}", f"voice_upload_{n}"):
        uploaded = st.session_state.get(key)
        if uploaded is not None:
            purge_uploaded_audio(uploaded)
            try:
                uploaded.close()
            except Exception:
                pass
        st.session_state.pop(key, None)
    st.session_state.voice_widget_n = n + 1


def current_case_values() -> dict:
    values = {key: st.session_state.get(f"case_field_{key}", "") or "" for _, key in CASE_FIELDS if key != "case_type"}
    choice = st.session_state.get("case_type_choice")
    values["case_type"] = choice if choice in DAMAGE_TYPES else ""
    return values


def apply_voice_proposal() -> None:
    proposal = st.session_state.get("voice_proposal") or {}
    applied = 0
    for key, value in proposal.items():
        if not st.session_state.get(f"voice_pick_{key}"):
            continue
        if key == "case_type":
            st.session_state.case_type_choice = value
        else:
            st.session_state[f"case_field_{key}"] = value
        applied += 1
    for key in list(st.session_state.keys()):
        if key.startswith("voice_pick_"):
            del st.session_state[key]
    st.session_state.voice_proposal = None
    st.session_state.voice_notice = (
        f"{applied}개 항목을 입력칸에 넣었습니다. 아래 입력칸에서 내용을 확인하고 고쳐 주세요. "
        f"'{NEEDS_CHECK}' 표시는 다시 확인이 필요한 부분입니다."
        if applied else "선택한 항목이 없어 입력칸을 바꾸지 않았습니다."
    )


def cancel_voice_proposal() -> None:
    st.session_state.voice_proposal = None
    for key in list(st.session_state.keys()):
        if key.startswith("voice_pick_"):
            del st.session_state[key]


def render_voice_input() -> None:
    admin_test = voice_admin_test_mode() and not (VOICE_INPUT_ENABLED and privacy_v3_in_effect())
    title = "🎤 말로 설명하고 입력칸 채우기 (선택)" + (" · 🧪 관리자 시험 모드" if admin_test else "")
    with st.expander(title, expanded=admin_test):
        if admin_test:
            st.warning(
                "관리자 시험 모드입니다. 일반 이용자에게는 이 기능이 보이지 않습니다. "
                "개인정보가 없는 시험 음성(예: docs/voice-test 안내의 시험 문장)만 사용해 주세요."
            )
        st.caption(
            "말로 피해 내용을 설명하면 글로 바꾸고, 확인을 거쳐 아래 입력칸에 나눠 넣어 드립니다. "
            "음성은 글로 바꾸는 데만 쓰고 이 서비스에 저장하지 않습니다. 글로 바꾸려면 Google Gemini로 전송되며, "
            "Google은 오남용 방지를 위해 AI 입력 기록을 일정 기간 보관할 수 있습니다."
        )
        notice = st.session_state.pop("voice_notice", None)
        if notice:
            st.success(notice)

        n = voice_widget_suffix()
        record_tab, upload_tab = st.tabs(["녹음하기", "음성파일 올리기"])
        with record_tab:
            recorded = st.audio_input("마이크 버튼을 누르고 말한 뒤 다시 눌러 멈추세요", key=f"voice_record_{n}")
            st.caption(
                "언제·어디서·누가·무엇을 했는지, 피해 금액, 가지고 있는 자료를 차례로 말해 주세요. 5분 안쪽을 권장합니다. "
                "마이크 권한을 거부했다면 브라우저 주소창의 자물쇠(또는 설정)에서 마이크를 허용하거나, "
                "휴대폰 녹음 앱으로 녹음한 뒤 '음성파일 올리기'를 이용해 주세요."
            )
        with upload_tab:
            uploaded = st.file_uploader(
                "음성파일 고르기 (10MB까지)", type=VOICE_UPLOAD_TYPES, key=f"voice_upload_{n}",
                help="WAV, MP3, M4A, AAC, OGG, OPUS, FLAC, WEBM 파일을 올릴 수 있습니다.",
            )

        audio = recorded or uploaded
        if audio is not None:
            st.button(
                "녹음·파일 지우기", key="voice_discard_btn", on_click=discard_voice_audio,
                help="글로 바꾸지 않고 서버 메모리에서 바로 지웁니다.",
            )
        if st.button("글로 바꾸기", key="voice_transcribe_btn", type="primary", disabled=audio is None):
            name = getattr(audio, "name", "") or "recording.wav"
            mime = audio_mime_type(name, getattr(audio, "type", "") or "audio/wav")
            problem = validate_audio(name, getattr(audio, "size", 0) or 0, getattr(audio, "type", "") or "audio/wav")
            if problem:
                st.error(problem)
            else:
                try:
                    with st.spinner("음성을 글로 바꾸고 있습니다. 길이에 따라 30초 정도 걸릴 수 있습니다."):
                        text = call_gemini([
                            types.Part.from_text(text=transcription_prompt()),
                            types.Part.from_bytes(data=audio.getvalue(), mime_type=mime or "audio/wav"),
                        ])
                except Exception as error:
                    st.error(ai_error_message(error, "음성을 글로 바꾸지 못했습니다. 조용한 곳에서 다시 녹음하거나 잠시 후 시도해 주세요."))
                    st.caption("다시 시도할 수 있도록 음성을 잠시 남겨 두었습니다. 그만두려면 '녹음·파일 지우기'를 눌러 주세요. 탭을 닫아도 지워집니다.")
                else:
                    text = clean_transcript(text)
                    # 여기서 st.rerun()을 하지 않는다. 아래 사건 입력칸이 그려지기 전에 다시 실행하면
                    # Streamlit이 그 입력칸에 적어 둔 내용을 지워 버린다.
                    discard_voice_audio()
                    if is_empty_transcript(text):
                        st.session_state.voice_error = "말소리를 찾지 못했습니다. 마이크 가까이에서 다시 녹음해 주세요."
                    else:
                        st.session_state.voice_transcript_editor = text
                        st.session_state.voice_proposal = None
                        st.success("글로 바꿨습니다. 이 서비스에 있던 원본 음성은 지웠습니다. 아래 글을 확인하고 고쳐 주세요.")

        error = st.session_state.pop("voice_error", None)
        if error:
            st.error(error)

        if st.session_state.get("voice_transcript_editor"):
            transcript = st.text_area(
                "바뀐 글 (말한 내용과 다르면 직접 고쳐 주세요)", key="voice_transcript_editor", height=180,
                max_chars=8000,
            )
            if admin_test:
                found = test_phrase_check(transcript)
                st.info(
                    f"🧪 시험 문장 인식 점검: 핵심어 {len(found)}/{len(TEST_PHRASE_KEYWORDS)}개 "
                    f"({', '.join(found) or '없음'}). 시험 음성 파일을 쓴 경우에만 의미가 있습니다."
                )
            st.caption(
                "[잘 안 들림]으로 표시된 부분은 직접 고쳐 주세요. 이 서비스에 있던 원본 음성은 지웠습니다. "
                "다만 Google이 오남용 방지를 위해 AI 입력 기록을 일정 기간 보관할 수 있습니다(개인정보처리방침 참고)."
            )
            if st.button("입력칸으로 정리하기", key="voice_extract_btn"):
                try:
                    with st.spinner("말한 내용을 입력칸별로 나누고 있습니다."):
                        raw = call_gemini([{"role": "user", "parts": [{"text": extraction_prompt(transcript, list(DAMAGE_TYPES))}]}])
                        proposal = parse_extraction(raw, transcript, list(DAMAGE_TYPES))
                except ValueError:
                    st.error("정리 결과를 읽지 못했습니다. '입력칸으로 정리하기'를 한 번 더 눌러 주세요.")
                except Exception as error:
                    st.error(ai_error_message(error, "입력칸으로 정리하지 못했습니다. 잠시 후 다시 시도해 주세요."))
                else:
                    if not proposal:
                        st.warning("글에서 입력칸에 넣을 내용을 찾지 못했습니다. 글을 고치거나 입력칸에 직접 적어 주세요.")
                    st.session_state.voice_proposal = proposal or None

        proposal = st.session_state.get("voice_proposal")
        if proposal:
            rows = merge_rows(current_case_values(), proposal)
            st.markdown("**입력칸에 넣을 내용 확인** — 넣을 항목만 체크해 주세요.")
            overwrites = 0
            for key, label, now, new, action in rows:
                default = action == "fill"
                caption = {"fill": "빈 칸에 넣습니다", "same": "지금 내용과 같습니다", "overwrite": "⚠️ 이미 적은 내용을 바꿉니다"}[action]
                overwrites += action == "overwrite"
                st.checkbox(f"{label}: {new}", value=default, key=f"voice_pick_{key}", disabled=action == "same")
                if action == "overwrite":
                    st.caption(f"{caption} — 지금 내용: {now[:120]}")
                else:
                    st.caption(caption)
            if overwrites:
                st.warning(f"이미 적은 내용이 있는 항목 {overwrites}개는 기본으로 체크하지 않았습니다. 바꾸려면 직접 체크해 주세요.")
            col_apply, col_cancel = st.columns(2)
            with col_apply:
                st.button("선택한 항목을 입력칸에 넣기", key="voice_apply_btn", type="primary", on_click=apply_voice_proposal)
            with col_cancel:
                st.button("넣지 않기", key="voice_cancel_btn", on_click=cancel_voice_proposal)


def render_signup_guide():
    st.markdown(
        "**가입 절차**\n\n"
        "① 이메일 입력 → ② 인증메일 확인(스팸함 포함) → ③ 앱으로 돌아와 로그인"
    )


GOOGLE_G_LOGO_SVG = (
    '<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">'
    '<path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 '
    '14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>'
    '<path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 '
    '5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>'
    '<path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19'
    'C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>'
    '<path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 '
    '2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>'
    '</svg>'
)

NAVER_N_LOGO_SVG = (
    '<svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">'
    '<rect width="18" height="18" rx="2" fill="#03C75A"/>'
    '<path fill="#FFFFFF" d="M5 4h3l2.9 4.2V4H14v10h-3l-2.9-4.2V14H5V4z"/>'
    '</svg>'
)


def render_google_login_button(url: str) -> None:
    st.markdown(
        f'<a class="dgo-google-btn" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
        f'{GOOGLE_G_LOGO_SVG}<span>Google로 계속하기</span></a>',
        unsafe_allow_html=True,
    )


def render_naver_login_button(url: str) -> None:
    st.markdown(
        f'<a class="dgo-google-btn" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
        f'{NAVER_N_LOGO_SVG}<span>네이버로 로그인</span></a>',
        unsafe_allow_html=True,
    )


def render_social_login_section(key_suffix: str) -> None:
    google_attempted = google_login_available()
    google_url = get_google_login_url() if google_attempted else None
    show_naver = naver_login_available()
    log_auth_gate_debug(bool(google_url), google_attempted)
    if not google_url and not show_naver:
        return

    st.divider()
    st.caption(SOCIAL_LOGIN_NOTICE)
    if google_url:
        render_google_login_button(google_url)
    if show_naver:
        render_naver_login_button(NAVER_LOGIN_START_URL)
        separator = "&" if "?" in NAVER_LOGIN_START_URL else "?"
        switch_url = f"{NAVER_LOGIN_START_URL}{separator}switch=1"
        st.markdown(
            f'<a href="{html.escape(switch_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
            '다른 네이버 계정으로 로그인</a>',
            unsafe_allow_html=True,
        )
        st.caption(
            "네이버 로그인은 새 탭에서 열립니다. 로그인한 뒤에는 이전 탭을 닫아 주세요. "
            "고소음이에서 로그아웃해도 네이버 로그인은 유지되므로, 다른 네이버 계정을 쓰려면 "
            "'다른 네이버 계정으로 로그인'을 눌러 주세요."
        )


def render_google_age_gate():
    st.markdown(
        "**만 14세 이상 확인**\n\n"
        "고소음이는 만 14세 이상만 이용할 수 있습니다. "
        "Google 계정으로 처음 이용하시는 경우 아래 확인이 필요합니다."
    )
    google_age_consent = st.checkbox("[필수] 만 14세 이상입니다.", key="google_age_consent")
    if st.button("확인하고 계속하기", key="google_age_submit"):
        if not google_age_consent:
            st.error("만 14세 이상인 경우에만 서비스를 이용할 수 있습니다.")
        else:
            try:
                confirm_google_age()
                confirmed = True
            except Exception as error:
                log_auth_error(error)
                confirmed = False
            if confirmed:
                st.rerun()
            st.error("확인 내용을 저장하지 못했습니다. 잠시 후 다시 눌러 주세요." + contact_hint())

    if st.button("로그아웃", key="google_age_logout_btn"):
        perform_logout()


def render_naver_age_gate(status_error: bool):
    if status_error:
        st.error("계정 정보를 확인하지 못했습니다. 새로고침해 보시고, 계속되면 로그아웃 후 다시 로그인해 주세요.")
    else:
        st.markdown(
            "**만 14세 이상 확인**\n\n"
            "고소음이는 만 14세 이상만 이용할 수 있습니다. "
            "네이버 계정으로 처음 이용하시는 경우 아래 확인이 필요합니다."
        )
        naver_age_consent = st.checkbox("[필수] 만 14세 이상입니다.", key="naver_age_consent")
        if st.button("확인하고 계속하기", key="naver_age_submit"):
            if not naver_age_consent:
                st.error("만 14세 이상인 경우에만 서비스를 이용할 수 있습니다.")
            else:
                try:
                    confirmed = get_authed_client().rpc("naver_confirm_age").execute().data is True
                except Exception:
                    confirmed = False
                if confirmed:
                    st.rerun()
                st.error("확인 내용을 저장하지 못했습니다. 잠시 후 다시 눌러 주세요." + contact_hint())

    if st.button("로그아웃", key="naver_age_logout_btn"):
        perform_logout()


def render_pending_email_screen(email, resend_key, on_resend, message):
    st.success(message)
    st.info("받은 편지함에 메일이 보이지 않으면 **스팸함(정크메일함)**도 확인해 주세요.")

    elapsed = time.time() - (st.session_state.get(resend_key) or 0)
    remaining = max(0, 60 - int(elapsed))

    if remaining > 0:
        st.caption(f"{remaining}초 후 다시 보낼 수 있습니다. (화면을 조작하면 남은 시간이 갱신됩니다)")
        st.button("메일 다시 보내기", disabled=True, key=f"{resend_key}_disabled_btn")
    else:
        if st.button("메일 다시 보내기", key=f"{resend_key}_btn"):
            base_url = get_app_base_url()
            if not base_url:
                st.error("서비스 주소 설정이 아직 끝나지 않아 메일을 보낼 수 없습니다. 잠시 후 다시 시도해 주세요." + contact_hint())
            else:
                try:
                    on_resend(email, base_url)
                    st.session_state[resend_key] = time.time()
                    st.success("메일을 다시 보냈습니다.")
                except Exception as error:
                    log_auth_error(error)
                    st.error(map_auth_error(error))

    if st.button("로그인 화면으로", key=f"{resend_key}_back_btn"):
        st.session_state.auth_view = "login"
        st.rerun()


def render_check_email_screen(email, resend_key):
    def _resend(target_email, base_url):
        auth_client = get_auth_client()
        auth_client.auth.resend({
            "type": "signup",
            "email": target_email,
            "options": {"email_redirect_to": base_url},
        })

    render_pending_email_screen(
        email, resend_key, _resend,
        f"**{email}** 주소로 인증 메일을 보냈습니다. 메일을 확인해 주세요!",
    )
    st.info("인증 메일의 링크를 누른 다음, 이 앱으로 돌아와 로그인해 주세요.")


def render_reset_sent_screen():
    email = st.session_state.get("reset_sent_email") or ""

    def _resend(target_email, base_url):
        auth_client = get_auth_client()
        auth_client.auth.reset_password_for_email(target_email, {"redirect_to": base_url})

    render_pending_email_screen(
        email, "reset_resend_last_sent_at", _resend,
        f"**{email}** 주소로 비밀번호 재설정 메일을 보냈습니다.",
    )


def render_forgot_password_form():
    st.markdown("가입하신 이메일로 비밀번호 재설정 메일을 보내드립니다.")

    with st.form("forgot_password_form"):
        forgot_email = st.text_input("이메일", key="forgot_password_email")
        forgot_submitted = st.form_submit_button("재설정 메일 보내기")

    if forgot_submitted:
        base_url = get_app_base_url()
        if not base_url:
            st.error("서비스 주소 설정이 아직 끝나지 않아 메일을 보낼 수 없습니다. 잠시 후 다시 시도해 주세요." + contact_hint())
        else:
            try:
                auth_client = get_auth_client()
                auth_client.auth.reset_password_for_email(forgot_email, {"redirect_to": base_url})
                st.session_state.reset_sent_email = forgot_email
                st.session_state.reset_resend_last_sent_at = None
                st.session_state.auth_view = "reset_sent"
                st.rerun()
            except Exception as error:
                log_auth_error(error)
                st.error(map_auth_error(error))

    if st.button("로그인으로 돌아가기", key="forgot_password_back_btn"):
        st.session_state.auth_view = "login"
        st.rerun()


def render_recovery_set_password_form():
    st.markdown("새 비밀번호를 설정해 주세요.")

    with st.form("recovery_set_password_form"):
        new_password = st.text_input("새 비밀번호 (6자 이상)", type="password", key="recovery_new_password")
        new_password_confirm = st.text_input("새 비밀번호 확인", type="password", key="recovery_new_password_confirm")
        recovery_submitted = st.form_submit_button("비밀번호 변경")

    if recovery_submitted:
        if new_password != new_password_confirm:
            st.error("비밀번호가 일치하지 않습니다.")
        else:
            pending = st.session_state.get("pending_recovery_session")
            if not pending:
                st.session_state.auth_view = "login"
                set_auth_flash("error", "인증 세션이 만료되었습니다. 처음부터 다시 시도해 주세요.")
                st.rerun()
            try:
                client = get_session_authed_client(pending)
                client.auth.update_user({"password": new_password})
                refreshed = client.auth.get_session()
                if not refreshed:
                    raise RuntimeError("password update 이후 세션을 확인할 수 없습니다.")
                new_session = session_to_dict(refreshed)
                st.session_state.supabase_session = new_session
                st.session_state.pending_recovery_session = None
                st.session_state.auth_view = "login"
                set_auth_flash("success", "비밀번호가 변경되었습니다.")
                st.rerun()
            except Exception as error:
                log_auth_error(error)
                st.session_state.pending_recovery_session = None
                st.session_state.auth_view = "login"
                set_auth_flash("error", map_auth_error(error))
                st.rerun()

    if st.button("취소", key="recovery_cancel_btn"):
        st.session_state.pending_recovery_session = None
        st.session_state.auth_view = "login"
        st.rerun()


st.set_page_config(
    page_title="고소음이 | AI 법률정보 도우미",
    page_icon="⚖️",
    initial_sidebar_state="collapsed",
)
st.markdown(BRAND_CSS, unsafe_allow_html=True)

if "supabase_session" not in st.session_state:
    st.session_state.supabase_session = None
if "auth_view" not in st.session_state:
    st.session_state.auth_view = "login"
if "auth_flash" not in st.session_state:
    st.session_state.auth_flash = None
if "signup_sent_email" not in st.session_state:
    st.session_state.signup_sent_email = ""
if "signup_resend_last_sent_at" not in st.session_state:
    st.session_state.signup_resend_last_sent_at = None
if "reset_sent_email" not in st.session_state:
    st.session_state.reset_sent_email = ""
if "reset_resend_last_sent_at" not in st.session_state:
    st.session_state.reset_resend_last_sent_at = None
if "pending_recovery_session" not in st.session_state:
    st.session_state.pending_recovery_session = None

handle_auth_query_params()
handle_naver_query_params()
handle_google_query_params()
restore_browser_session()

if not st.session_state.supabase_session:
    render_brand_header()
    if st.session_state.pop("account_deleted", False):
        st.success("회원탈퇴가 완료되었습니다. 계정과 저장된 자료가 삭제되었습니다.")
    render_auth_flash()
    render_upcoming_privacy_notice()

    if st.session_state.auth_view == "recovery_set_password":
        render_recovery_set_password_form()
    elif st.session_state.auth_view == "signup_sent":
        render_check_email_screen(st.session_state.signup_sent_email, "signup_resend_last_sent_at")
    elif PASSWORD_RESET_ENABLED and st.session_state.auth_view == "reset_sent":
        render_reset_sent_screen()
    elif PASSWORD_RESET_ENABLED and st.session_state.auth_view == "forgot_password":
        render_forgot_password_form()
    else:
        if st.button("개인정보처리방침 보기", key="privacy_policy_view_btn_login"):
            render_privacy_policy_dialog()

        if st.button("이용약관 보기", key="terms_of_service_view_btn_login"):
            render_terms_of_service_dialog()

        login_tab, signup_tab = st.tabs(["로그인", "회원가입"])

        with login_tab:
            st.caption("가입할 때 쓴 방법(이메일·네이버·Google)으로 로그인해 주세요. 로그인하면 이 브라우저에서 최대 7일 동안 로그인이 유지됩니다.")
            with st.form("login_form"):
                login_email = st.text_input("이메일", key="login_email")
                login_password = st.text_input("비밀번호", type="password", key="login_password")
                login_submitted = st.form_submit_button("로그인")

            if login_submitted and not (login_email.strip() and login_password):
                st.error("이메일과 비밀번호를 모두 입력해 주세요.")
            elif login_submitted:
                try:
                    auth_client = get_auth_client()
                    auth_response = auth_client.auth.sign_in_with_password(
                        {"email": login_email.strip(), "password": login_password}
                    )
                    if not getattr(auth_response, "session", None) or not getattr(auth_response, "user", None):
                        raise RuntimeError("empty sign_in response")
                    st.session_state.supabase_session = session_from_auth_response(auth_response)
                    st.rerun()
                except Exception as error:
                    log_auth_error(error)
                    st.error(map_auth_error(error))

            if PASSWORD_RESET_ENABLED:
                if st.button("비밀번호를 잊으셨나요?", key="forgot_password_link_btn"):
                    st.session_state.auth_view = "forgot_password"
                    st.rerun()

            render_social_login_section("login")

        with signup_tab:
            render_signup_guide()
            st.markdown(
                "**회원가입 시 수집하는 개인정보**\n"
                "- 수집 항목: 이메일, 인증을 위한 비밀번호\n"
                "- 목적: 회원가입·로그인·계정관리\n"
                "- 보관기간: 회원탈퇴 시까지\n"
                "- 동의를 거부할 수 있으나, 거부 시 회원가입이 제한됩니다.\n"
                "- 비밀번호는 Supabase Auth에서 암호화된 인증정보로 처리되며 운영자는 원문을 열람할 수 없습니다."
            )

            with st.form("signup_form"):
                signup_email = st.text_input("이메일", key="signup_email", max_chars=254)
                signup_password = st.text_input("비밀번호 (6자 이상)", type="password", key="signup_password", max_chars=128)
                signup_password_confirm = st.text_input("비밀번호 확인", type="password", key="signup_password_confirm", max_chars=128)
                signup_privacy_consent = st.checkbox(
                    "[필수] 위 안내를 확인했으며 개인정보 수집·이용에 동의합니다.",
                    key="signup_privacy_consent",
                )
                signup_terms_consent = st.checkbox(
                    "[필수] 이용약관에 동의합니다.",
                    key="signup_terms_consent",
                )
                signup_age_consent = st.checkbox("[필수] 만 14세 이상입니다.", key="signup_age_consent")
                signup_submitted = st.form_submit_button("회원가입")

            if signup_submitted:
                if not privacy_config_ready() or not terms_config_ready():
                    st.error(
                        "현재 서비스 운영 정보 설정이 진행 중이라 잠시 회원가입을 "
                        "이용하실 수 없습니다. 잠시 후 다시 시도해 주세요."
                    )
                elif not (signup_privacy_consent and signup_terms_consent and signup_age_consent):
                    st.error("회원가입을 진행하려면 이용약관 동의, 개인정보 수집·이용 동의, 만 14세 이상 확인에 모두 체크해 주세요.")
                elif len(signup_password) < 6:
                    st.error("비밀번호는 6자 이상 입력해 주세요.")
                elif signup_password != signup_password_confirm:
                    st.error("비밀번호가 일치하지 않습니다.")
                else:
                    base_url = get_app_base_url()
                    if not base_url:
                        st.error("서비스 주소 설정이 아직 끝나지 않아 메일을 보낼 수 없습니다. 잠시 후 다시 시도해 주세요." + contact_hint())
                    else:
                        try:
                            auth_client = get_auth_client()
                            auth_client.auth.sign_up({
                                "email": signup_email.strip(),
                                "password": signup_password,
                                "options": {
                                    "email_redirect_to": base_url,
                                    "data": {
                                        "terms_version": TERMS_EFFECTIVE_DATE,
                                        "privacy_version": PRIVACY_POLICY_EFFECTIVE_DATE,
                                        "terms_consent": True,
                                        "privacy_consent": True,
                                    },
                                },
                            })
                            st.session_state.signup_sent_email = signup_email.strip()
                            st.session_state.signup_resend_last_sent_at = None
                            st.session_state.auth_view = "signup_sent"
                            st.rerun()
                        except Exception as error:
                            log_auth_error(error)
                            st.error(map_auth_error(error))

            render_social_login_section("signup")

    render_footer()
    st.stop()

if ensure_active_session() is None:
    st.rerun()
render_auth_flash()

current_user_id = st.session_state.supabase_session["user_id"]
if st.session_state.get("case_workflow_user_id") != current_user_id:
    clear_user_session_data()
    st.session_state.case_workflow_user_id = current_user_id

if is_naver_session(st.session_state.supabase_session):
    try:
        naver_age_rows = get_authed_client().rpc("naver_age_status").execute().data or []
        naver_age_confirmed = bool(naver_age_rows and naver_age_rows[0].get("age_confirmed"))
    except Exception:
        naver_age_confirmed = None
    if not naver_age_confirmed:
        render_brand_header()
        render_naver_age_gate(status_error=naver_age_confirmed is None)
        render_footer()
        st.stop()
elif is_google_primary_session(st.session_state.supabase_session):
    if not social_age_confirmed(st.session_state.supabase_session):
        render_brand_header()
        render_google_age_gate()
        render_footer()
        st.stop()

consent_status = has_current_consent(current_user_id)
if consent_status is None:
    render_brand_header()
    st.error("동의 상태를 확인하지 못했습니다. 인터넷 연결을 확인하고 다시 시도해 주세요." + contact_hint())
    if st.button("다시 시도", key="consent_retry_btn", type="primary"):
        st.rerun()
    if st.button("로그아웃", key="consent_error_logout_btn"):
        perform_logout()
    render_footer()
    st.stop()
if not consent_status:
    render_brand_header()
    consent_identity, consent_method = current_account_label(st.session_state.supabase_session)
    st.info(
        f"{consent_identity} ({consent_method} 로그인) 계정으로 이용하시려면 아래 약관과 개인정보처리방침을 "
        "읽고 동의해 주세요. 처음 이용하시거나 내용이 바뀐 경우에 한 번 표시됩니다."
    )
    st.markdown(build_terms_of_service_text())
    st.markdown(build_privacy_policy_text())
    reconsent_terms = st.checkbox("[필수] 이용약관에 동의합니다.", key="reconsent_terms")
    reconsent_privacy = st.checkbox("[필수] 개인정보 수집·이용에 동의합니다.", key="reconsent_privacy")
    if st.button("동의하고 계속하기", key="reconsent_submit"):
        if reconsent_terms and reconsent_privacy:
            try:
                get_authed_client().rpc(
                    "record_consent",
                    {
                        "p_terms_version": TERMS_EFFECTIVE_DATE,
                        "p_privacy_version": PRIVACY_POLICY_EFFECTIVE_DATE,
                    },
                ).execute()
                st.rerun()
            except Exception as error:
                error_code = re.sub(
                    r"[^A-Za-z0-9_.-]",
                    "",
                    str(getattr(error, "code", "") or type(error).__name__),
                )[:80] or "unknown"
                error_status = getattr(error, "status", None)
                print(f"[consent_error] code={error_code} status={error_status}")
                st.error(
                    "동의 내용을 저장하지 못했습니다. 잠시 후 다시 눌러 주세요. "
                    f"계속되면 운영자에게 이 오류 코드를 알려 주세요: {error_code}" + contact_hint()
                )
        else:
            st.error("계속하려면 이용약관과 개인정보 수집·이용 두 곳에 모두 체크해 주세요.")
    if st.button("동의하지 않고 로그아웃", key="reconsent_logout_btn"):
        perform_logout()
    render_footer()
    st.stop()

with st.sidebar:
    st.markdown("**고소음이**")
    sidebar_identity, sidebar_method = current_account_label(st.session_state.supabase_session)
    st.caption(f"{sidebar_method} 로그인")
    st.markdown(sidebar_identity)
    if st.button("로그아웃", key="logout_btn_sidebar"):
        perform_logout()

    if st.button("개인정보처리방침 보기", key="privacy_policy_view_btn_sidebar"):
        render_privacy_policy_dialog()

    if st.button("이용약관 보기", key="terms_of_service_view_btn_sidebar"):
        render_terms_of_service_dialog()

    if st.button("회원탈퇴", key="delete_account_sidebar"):
        render_delete_account_dialog()

render_brand_header()

account_identity, account_method = current_account_label(st.session_state.supabase_session)
st.caption(f"로그인한 계정: {account_identity} ({account_method})")
render_upcoming_privacy_notice()
if is_admin():
    render_admin_diagnostics()

with st.expander("계정 및 개인정보 메뉴"):
    st.markdown(f"**현재 로그인 계정:** {account_identity} ({account_method} 로그인)")
    st.caption(
        "다른 계정으로 바꾸려면 로그아웃한 뒤 다시 로그인해 주세요. 네이버는 고소음이에서 로그아웃해도 "
        "네이버 로그인이 유지되므로, 로그인 화면의 '다른 네이버 계정으로 로그인'을 눌러 주세요. "
        "공용 기기라면 사용 후 꼭 로그아웃해 주세요."
    )
    if PRIVACY_CONTACT_EMAIL:
        st.markdown(f"**문의:** {md_escape(PRIVACY_CONTACT_EMAIL)} (서비스 이용·개인정보 관련)")

    st.markdown("**요금제**")
    if sales_open(BILLING_MODE, BILLING_PLANS):
        st.caption("결제 기능을 준비하고 있습니다.")
    else:
        st.caption("유료 요금제는 준비 중입니다. 지금은 모든 기능을 무료로 이용할 수 있으며, 결제를 요청하지 않습니다.")
        if BILLING_MODE == "test" and BILLING_PLANS:
            for plan in BILLING_PLANS:
                st.caption("🧪 " + plan_summary(plan, BILLING_MODE))

    if st.button("개인정보처리방침 보기", key="privacy_policy_view_btn_main"):
        render_privacy_policy_dialog()

    if st.button("이용약관 보기", key="terms_of_service_view_btn_main"):
        render_terms_of_service_dialog()

    if st.button("회원탈퇴", key="delete_account_main"):
        render_delete_account_dialog()

    if st.button("로그아웃", key="logout_btn_main"):
        perform_logout()

card_col1, card_col2, card_col3 = st.columns(3)
with card_col1:
    st.markdown('<div class="dgo-card">📝<b>① 피해 내용 적기</b></div>', unsafe_allow_html=True)
with card_col2:
    st.markdown('<div class="dgo-card">📄<b>② 초안 확인·수정</b></div>', unsafe_allow_html=True)
with card_col3:
    st.markdown('<div class="dgo-card">🗂️<b>③ 저장·내려받기</b></div>', unsafe_allow_html=True)

tab_case, tab_case_list, tab_qa, tab_terms, tab_chat = st.tabs(
    ["고소장 작성", "내 사건", "피해 유형별 안내", "법률 용어", "AI 상담"]
)

with tab_case:
    st.caption("① 피해 유형 고르기 → ② 아는 내용 적기 → ③ 입력 내용 확인 → ④ 초안 확인·수정 → ⑤ 내려받기")
    with st.container(border=True):
        st.markdown(
            "**사건 저장 시 수집하는 개인정보**\n"
            "- 저장 항목: 사건 유형, 신고인 정보, 상대방 정보, 사건 발생 일시, 사건 발생 장소, "
            "사건 경위, 피해 내용, 증거자료, 목격자 유무, 처벌 의사 (총 10개 항목)\n"
            "- 목적: 사건 저장·관리, AI 상담 및 고소장 초안 생성\n"
            "- 보관기간: 이용자가 직접 삭제하거나 회원탈퇴할 때까지\n"
            "- 동의를 거부할 수 있으나, 거부 시 사건 저장과 고소장 초안 생성이 제한됩니다."
        )
        if draft_save_available():
            st.markdown(
                "- 고소장 초안: 처음 만든 초안과 '초안 저장'으로 저장한 초안은 사건과 함께 보관되며, "
                "사건을 삭제하거나 회원탈퇴하면 함께 삭제됩니다."
            )
        st.caption(
            "입력하신 사건 정보(신고인·상대방 정보 등 개인정보 포함 가능)는 Supabase 데이터베이스에 저장되고, "
            "고소장 초안을 만들 때 Google Gemini API로 전송되어 처리됩니다. "
            "상대방 등 제3자의 정보는 필요한 최소한만 적어 주시고, 주민등록번호·계좌 비밀번호·카드번호·"
            "인증번호(OTP)는 적지 마세요."
        )

    case_notice = st.session_state.get("case_notice")
    if case_notice:
        st.success(case_notice)

    if st.session_state.pending_draft_entry:
        st.warning(
            "사건은 저장했지만 고소장 초안을 만들지 못했습니다. 잠시 후 '초안 다시 만들기'를 눌러 주세요. "
            "계속 안 되면 '내 사건' 탭에서 같은 사건으로 초안을 다시 만들 수도 있습니다."
        )
        if st.session_state.get("pending_draft_reason"):
            st.info(st.session_state.pending_draft_reason)
        col_retry, col_cancel = st.columns(2)
        with col_retry:
            if st.button("초안 다시 만들기", key="retry_draft_button", type="primary"):
                entry = st.session_state.pending_draft_entry
                try:
                    with st.spinner("고소장 초안을 만들고 있습니다. 보통 10~30초 걸립니다."):
                        draft_text = generate_case_draft(entry)
                except Exception as error:
                    st.error(ai_error_message(error, "초안을 또 만들지 못했습니다. 1~2분 뒤에 다시 시도해 주세요."))
                else:
                    show_new_draft(entry, draft_text)
                    st.session_state.pending_draft_entry = None
                    st.rerun()
        with col_cancel:
            if st.button("나중에 하고 새 사건 쓰기", key="cancel_pending_draft_button"):
                st.session_state.pending_draft_entry = None
                st.rerun()
    else:
        if voice_input_available():
            render_voice_input()
        type_options = list(DAMAGE_TYPES) + [OTHER_TYPE]
        case_type_choice = st.selectbox(
            "① 어떤 피해를 입으셨나요?",
            type_options,
            index=None,
            placeholder="피해 유형을 골라 주세요",
            key="case_type_choice",
            help="고르시면 그 유형에 맞는 작성 예시와 준비할 자료를 보여 드립니다. 맞는 유형이 없으면 '기타'를 골라 주세요.",
        )
        case_type_custom = ""
        if case_type_choice == OTHER_TYPE:
            case_type_custom = st.text_input(
                "피해 유형 직접 적기",
                max_chars=CASE_FIELD_MAX_CHARS["case_type"],
                placeholder="예: 렌터카 보증금 미반환",
                key="case_type_custom",
            )
        if case_type_choice:
            render_damage_type_guide(case_type_choice, show_qa=False)
        else:
            st.info("먼저 피해 유형을 골라 주세요. 유형에 맞는 작성 예시가 아래 입력칸에 나타납니다.")

        guide_type = case_type_choice or ""
        # 폼(st.form)을 쓰지 않는다: 폼 안에 적은 글은 제출 전까지 앱이 볼 수 없어,
        # 음성 입력이 기존 내용을 덮어쓰는지 확인할 수 없기 때문이다. 각 칸은 칸을 벗어날 때 저장된다.
        with st.container(border=True):
            st.markdown("**② 아는 내용을 적어 주세요** (* 표시는 꼭 적어야 하는 칸, 나머지는 모르면 비워 두셔도 됩니다)")
            field_values = {}
            for _, key in CASE_FIELDS:
                if key == "case_type":
                    continue
                label = field_label(key) + (" *" if key in ("reporter_info", "incident_description") else "")
                widget = st.text_area if key in CASE_LONG_TEXT_FIELDS else st.text_input
                field_values[key] = widget(
                    label,
                    max_chars=CASE_FIELD_MAX_CHARS[key],
                    placeholder=field_placeholder(key, guide_type),
                    help=FIELD_GUIDES[key]["help"],
                    key=f"case_field_{key}",
                )
                if key == "suspect_info":
                    with st.expander("상대방 이름이나 주소를 모르면?"):
                        st.markdown(SUSPECT_UNKNOWN_GUIDE)

            case_data_consent = st.checkbox(
                "[필수] 위 안내를 확인했으며 사건 정보 저장·전송에 동의합니다.",
                key="case_data_consent",
            )
            submitted = st.button("③ 입력 내용 확인하기", type="primary", key="case_form_submit")

        if submitted:
            field_values["case_type"] = (
                case_type_custom.strip() if case_type_choice == OTHER_TYPE else (case_type_choice or "")
            )
            errors = []
            if not case_type_choice:
                errors.append("맨 위에서 피해 유형을 골라 주세요.")
            elif case_type_choice == OTHER_TYPE and not field_values["case_type"]:
                errors.append("'피해 유형 직접 적기' 칸에 피해 유형을 적어 주세요.")
            # 피해 유형 누락은 위에서 더 구체적으로 안내했으므로 나머지 항목만 검사한다.
            errors += validate_case_values({**field_values, "case_type": field_values["case_type"] or "-"})
            if not privacy_config_ready():
                st.error("지금은 서비스 운영 정보를 설정하는 중이라 사건을 저장할 수 없습니다. 잠시 후 다시 시도해 주세요." + contact_hint())
            elif not case_data_consent:
                st.error("사건을 저장하려면 입력칸 아래의 저장·전송 동의에 체크해 주세요.")
            elif errors:
                for message in dict.fromkeys(errors):
                    st.error(message)
            else:
                st.session_state.case_review = {
                    "case_id": str(uuid.uuid4()),
                    "values": normalize_case_values(field_values),
                }
                st.session_state.case_notice = None

        review = st.session_state.get("case_review")
        if review:
            with st.container(border=True):
                st.markdown("**③ 입력 내용 확인**")
                st.caption(
                    "아래 내용으로 사건을 저장하고 초안을 만듭니다. 고칠 부분이 있으면 위 입력칸을 고친 뒤 "
                    "'입력 내용 확인하기'를 다시 눌러 주세요."
                )
                for label, key in CASE_FIELDS:
                    value = review["values"][key]
                    shown = "_확인 필요 (입력하지 않음)_" if is_missing(value) else md_escape(value)
                    st.markdown(f"**{label}**  \n{shown}")
                review_missing = missing_info_items(review["values"])
                if review_missing:
                    st.warning(
                        "아래 항목은 초안에 '[확인 필요]'로 표시됩니다. 지금 채우거나, 초안을 받은 뒤 직접 고쳐도 됩니다.\n\n"
                        + "\n".join(f"- {item}" for item in review_missing)
                    )
                col_confirm, col_back = st.columns(2)
                with col_confirm:
                    confirm_clicked = st.button("④ 이대로 저장하고 초안 만들기", key="case_review_confirm", type="primary")
                with col_back:
                    if st.button("취소하고 다시 고치기", key="case_review_cancel"):
                        st.session_state.case_review = None
                        st.rerun()

            if confirm_clicked:
                user_id = st.session_state.supabase_session["user_id"]
                values = review["values"]
                fingerprint = compute_case_fingerprint(user_id, values)
                last_entry = st.session_state.last_saved_case_entry
                is_duplicate = (
                    st.session_state.last_saved_fingerprint == fingerprint
                    and st.session_state.last_saved_at is not None
                    and (time.time() - st.session_state.last_saved_at) < DUPLICATE_SUBMIT_WINDOW_SECONDS
                ) or bool(last_entry and last_entry.get("case_id") == review["case_id"])

                case_entry = None
                if is_duplicate and last_entry:
                    case_entry = last_entry
                    st.session_state.case_notice = "방금 저장한 사건과 같은 내용이라 다시 저장하지 않았습니다. 아래 초안을 확인해 주세요."
                else:
                    kst = datetime.timezone(datetime.timedelta(hours=9))
                    new_entry = {
                        "case_id": review["case_id"],
                        "user_id": user_id,
                        "original_request": "Streamlit 고소장 작성 폼에서 제출됨",
                        **values,
                        "created_at": datetime.datetime.now(kst).isoformat(),
                    }
                    try:
                        with st.spinner("사건을 저장하고 있습니다."):
                            save_case(new_entry)
                    except Exception:
                        st.error(
                            "사건을 저장하지 못했습니다. 입력한 내용은 그대로 남아 있으니, 인터넷 연결을 확인하고 "
                            "'이대로 저장하고 초안 만들기'를 다시 눌러 주세요. 다시 눌러도 같은 사건이 두 번 저장되지는 않습니다."
                            + contact_hint()
                        )
                    else:
                        st.session_state.last_saved_case_entry = new_entry
                        st.session_state.last_saved_fingerprint = fingerprint
                        st.session_state.last_saved_at = time.time()
                        case_entry = new_entry

                if case_entry is not None:
                    st.session_state.case_review = None
                    already_drafted = (
                        st.session_state.case_draft_id == case_entry["case_id"] and st.session_state.case_draft
                    )
                    if not already_drafted:
                        try:
                            with st.spinner("사건을 저장했습니다. 고소장 초안을 만들고 있습니다. 보통 10~30초 걸립니다."):
                                draft_text = generate_case_draft(case_entry)
                        except Exception as error:
                            st.session_state.pending_draft_entry = case_entry
                            st.session_state.pending_draft_reason = ai_error_message(
                                error, "초안을 만드는 중 문제가 생겼습니다."
                            )
                        else:
                            show_new_draft(case_entry, draft_text)
                            st.session_state.case_notice = (
                                "사건과 초안을 저장했습니다. 아래 초안을 읽고 고친 뒤 '초안 저장'을 눌러 주세요. '내 사건' 탭에서 다시 열 수 있습니다."
                                if draft_save_available()
                                else "사건을 저장했습니다. 아래 초안을 읽고 필요한 부분을 고쳐 주세요. '내 사건' 탭에서 다시 볼 수 있습니다."
                            )
                    st.rerun()

    if st.session_state.case_draft and not st.session_state.pending_draft_entry:
        render_draft_panel(
            "case_draft_editor",
            st.session_state.case_draft,
            st.session_state.get("case_draft_case") or {},
            st.session_state.case_draft_id,
        )
        st.button("새 사건 작성하기", key="case_new_button", on_click=reset_case_form)

with tab_case_list:
    st.caption("저장한 사건을 다시 보고, 초안을 다시 만들거나 증거파일을 첨부·삭제할 수 있습니다.")

    list_notice = st.session_state.pop("case_list_notice", None)
    if list_notice:
        st.success(list_notice)

    cases, load_error = load_cases_safe()

    if load_error:
        st.error(load_error + contact_hint())
        if st.button("다시 불러오기", key="reload_cases_button"):
            st.rerun()
    elif not cases:
        st.info(
            "아직 저장한 사건이 없습니다. '고소장 작성' 탭에서 피해 내용을 적고 저장하면 여기에 모입니다."
        )
    else:
        st.caption(f"저장한 사건 {len(cases)}건 · 회원탈퇴는 화면 위쪽 '계정 및 개인정보 메뉴'에서 할 수 있습니다.")
        can_save_drafts = draft_save_available()
        draft_index = {}
        if can_save_drafts:
            try:
                draft_index = load_draft_index()
            except Exception as error:
                log_auth_error(error)
                st.warning("저장된 초안 목록을 불러오지 못했습니다. 새로고침해 주세요. 사건 내용은 아래에서 그대로 볼 수 있습니다.")
        shown_case_ids = set()
        for case in cases:
            case_id = case.get("case_id", "")
            if case_id in shown_case_ids:
                continue
            shown_case_ids.add(case_id)
            with st.container(border=True):
                st.markdown(
                    f"**{md_escape(case.get('case_type') or '유형 미입력')}** · "
                    f"{format_created_at(case.get('created_at'))} 저장"
                )
                if draft_index.get(str(case_id)):
                    st.caption("📝 저장된 초안 있음")
                summary = (case.get("incident_description") or "").strip()
                if summary and not is_missing(summary):
                    st.caption(md_escape(summary[:80] + ("…" if len(summary) > 80 else "")))

                with st.expander("입력한 내용 보기"):
                    for label, key in CASE_FIELDS:
                        value = case.get(key)
                        shown = "_확인 필요 (입력하지 않음)_" if is_missing(value) else md_escape(value)
                        st.markdown(f"**{label}**  \n{shown}")
                    st.caption(f"사건 번호: {case_id}")

                saved_at = draft_index.get(str(case_id)) if draft_index else None
                with st.expander("고소장 초안 열기·고치기"):
                    list_draft_key = f"list_draft_{case_id}"
                    list_editor_key = f"list_draft_editor_{case_id}"
                    if saved_at:
                        st.caption(f"저장된 초안이 있습니다 (마지막 저장 {format_created_at(saved_at)}).")
                        st.button(
                            "저장된 초안 열기",
                            key=f"list_draft_open_{case_id}",
                            type="primary",
                            on_click=open_saved_draft,
                            args=(case_id,),
                        )
                    elif can_save_drafts:
                        st.caption("아직 저장된 초안이 없습니다. 아래에서 초안을 만든 뒤 '초안 저장'을 눌러 주세요.")
                    else:
                        st.caption("저장한 내용으로 초안을 새로 만듭니다. 초안은 서버에 저장되지 않으니 필요하면 내려받아 주세요.")
                    if st.button("AI로 새 초안 만들기", key=f"list_draft_btn_{case_id}"):
                        try:
                            with st.spinner("고소장 초안을 만들고 있습니다. 보통 10~30초 걸립니다."):
                                st.session_state[list_draft_key] = generate_case_draft(case)
                            st.session_state.pop(list_editor_key, None)
                            if saved_at:
                                st.info("새 초안을 만들었습니다. '초안 저장'을 누르기 전까지 저장된 초안은 바뀌지 않습니다.")
                        except Exception as error:
                            st.error(ai_error_message(error, "초안을 만들지 못했습니다. 1~2분 뒤에 다시 눌러 주세요."))
                    load_error = st.session_state.pop(f"{list_editor_key}_error", None) if not st.session_state.get(list_draft_key) else None
                    if load_error:
                        st.error(load_error)
                    if st.session_state.get(list_draft_key):
                        render_draft_panel(list_editor_key, st.session_state[list_draft_key], case, case_id)

                if EVIDENCE_UPLOAD_ENABLED:
                    render_case_evidence_manager(case_id)

                confirm_delete = st.checkbox(
                    "이 사건과 첨부한 증거파일을 모두 삭제합니다 (되돌릴 수 없음)",
                    key=f"confirm_delete_{case_id}",
                )
                if st.button("사건 삭제", key=f"delete_button_{case_id}", disabled=not confirm_delete):
                    try:
                        with st.spinner("사건과 첨부파일을 삭제하고 있습니다."):
                            delete_case(case_id)
                    except Exception:
                        st.error(
                            "사건을 삭제하지 못했습니다. 새로고침한 뒤 다시 시도해 주세요. "
                            "첨부파일 일부가 먼저 지워졌을 수 있으니 목록을 확인해 주세요." + contact_hint()
                        )
                    else:
                        st.session_state.case_list_notice = "사건과 첨부파일을 삭제했습니다."
                        st.rerun()

with tab_qa:
    st.subheader("내 상황에 맞는 안내 보기")
    st.caption(
        "피해 유형을 고르면 먼저 해 볼 일, 준비할 자료, 자주 묻는 질문, 공식 안내 링크를 보여 드립니다. "
        "여기서 고르거나 읽은 내용은 저장되지 않습니다."
    )
    qa_options = list(DAMAGE_TYPES)
    default_type = st.session_state.get("case_type_choice")
    selected_qa_type = st.selectbox(
        "피해 유형",
        qa_options,
        index=qa_options.index(default_type) if default_type in qa_options else 0,
        key="victim_qa_type",
    )
    render_damage_type_guide(selected_qa_type, show_qa=True)
    st.caption("위 내용은 일반 정보입니다. 실제 신고 방법과 보호조치는 담당 기관의 안내를 확인해 주세요.")

with tab_terms:
    st.subheader("어려운 법률 용어, 쉽게 보기")
    st.caption("형사 사건에서 자주 보이는 말의 일반적인 뜻을 설명합니다. 개별 사건의 결과를 판단하는 내용은 아닙니다.")
    term_query = st.text_input("용어 검색", placeholder="예: 고소, 피고소인, 기소", key="legal_terms_query").strip().casefold()
    matching_terms = [
        (term, explanation) for term, explanation in LEGAL_TERMS
        if term_query in term.casefold() or term_query in explanation.casefold()
    ]
    if not matching_terms:
        st.info("찾는 용어가 없습니다. 다른 단어로 검색하시거나 검색칸을 비우면 전체 목록을 볼 수 있습니다.")
    for term, explanation in matching_terms:
        with st.expander(term):
            st.write(explanation)
    st.markdown(f"[설명 근거: 대검찰청 사건처리절차 안내]({LEGAL_TERMS_SOURCE})")

with tab_chat:
    st.caption(
        "피해 상황을 편하게 이야기하면 핵심 사실을 정리하도록 도와드려요. 대화 내용은 저장되지 않고, "
        "새로고침하거나 로그아웃하면 사라집니다. 답변은 일반 정보이며 법률 자문이 아닙니다."
    )

    if not st.session_state.messages:
        st.info(
            "이렇게 물어보세요:\n\n"
            "- 중고거래로 돈을 보냈는데 연락이 끊겼어요. 무엇부터 정리할까요?\n"
            "- 고소장에 상대방 이름을 모르면 어떻게 쓰나요?\n"
            "- 증거로 어떤 자료를 모아 두면 좋을까요?"
        )

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("궁금한 점이나 상황을 적어 주세요", max_chars=3000):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        try:
            with st.spinner("답변을 만들고 있습니다."):
                answer = process_question(prompt, st.session_state.model_history)
        except Exception as error:
            answer = ai_error_message(error, "답변을 만들지 못했습니다. 잠시 후 다시 질문해 주세요.")

        st.session_state.messages.append({"role": "assistant", "content": answer})
        with st.chat_message("assistant"):
            st.markdown(answer)

render_footer()
