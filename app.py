import os
import base64
import json
import re
import time
import datetime
import unicodedata
import uuid
import hashlib
import sys
import httpx
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
import chat_assistant as _chat_assistant_module
import precedent_search as _precedent_search_module
import case_workspace as _case_workspace_module
import calendar_ics as _calendar_ics_module

# Streamlit Cloud는 git push 뒤 app.py만 새로 실행하고, 이미 불러온 보조 모듈(case_guide, voice_input 등)은
# 예전 버전을 메모리에 그대로 둘 수 있다. 그러면 새로 추가한 이름을 못 찾아 ImportError로 앱이 멈춘다
# (2026-10-06 실제 발생). 의존 순서대로 다시 불러와 항상 배포된 파일과 같은 내용을 쓰게 한다.
# 배포 직후에는 다른 세션이 실행을 시작하면서 이 모듈들을 sys.modules에서 비울 수 있다(Streamlit 파일 감시).
# 그 사이에 reload하면 ImportError가 나므로(2026-10-12 운영 로그), 비워진 모듈은 건너뛴다.
# 바로 아래의 from-import가 디스크의 새 파일로 다시 불러오므로 예전 버전이 남지 않는다.
for _module in (
    _case_guide_module, _policy_text_module, _billing_module, _auth_ip_module, _voice_input_module,
    _chat_assistant_module, _precedent_search_module, _case_workspace_module, _calendar_ics_module,
):
    try:
        importlib.reload(_module)
    except ImportError as _error:
        # 다른 세션이 이 모듈을 비운 경우만 건너뛴다. 그 사이 또 다른 세션이 다시 불러와 sys.modules에 있을 수도 있으므로
        # (2026-10-12 운영 로그: auth_ip) 모듈이 지금 있는지가 아니라 오류 종류로 판단한다.
        if "not in sys.modules" not in str(_error):
            raise

from voice_input import (
    NEEDS_CHECK, SOURCE_CHAT, TEST_PHRASE_KEYWORDS, VOICE_UPLOAD_TYPES, test_phrase_check, audio_mime_type, clean_transcript,
    extraction_prompt, is_empty_transcript, merge_rows, parse_extraction, transcription_prompt, validate_audio,
)
from chat_assistant import (
    CHAT_INPUT_PLACEHOLDER, CHAT_LIMITS_NOTE, CHAT_MAX_INPUT_CHARS, CHAT_RETENTION_NOTE, CHAT_WELCOME, SPEECH_NOTE, VOICE_CHAT_CSS, VOICE_CHAT_JS,
    DEFAULT_SPEECH_RATE, GEMINI_TTS_DEFAULT_MODEL, SERVER_DEFAULT_VOICE, SERVER_SPEECH_NOTE, SERVER_VOICES, TTS_ENDPOINT, VOICE_PREVIEW_TEXT, TtsError, extract_tts_audio,
    server_speech_text, tts_error_from_response, tts_request, wav_seconds,
    VOICE_MIC_NOTE, VOICE_TURN_MAX_SECONDS, append_exchange, decode_voice_turn, is_duplicate_submit, model_contents,
    normalize_question, question_digest, speech_text, system_instruction as chat_system_instruction, user_statements,
    voice_system_instruction, SUMMARY_FAILED, SUMMARY_MIN_CHARS, clean_summary, summary_prompt, summary_supported,
)
from case_workspace import (
    STYLES, UNKNOWN, build_timeline, bundle_docx, bundle_text, draft_check_items, evidence_items, inconsistencies,
    link_issues, missing_questions, next_action, rewrite_changes, rewrite_prompt, suggestion_for, unknown_items,
)
from calendar_ics import ALARM_CHOICES, KIND_LABELS, build_ics, clean_event, describe
from precedent_search import (
    OFFICIAL_PRECEDENT_SEARCH, OPEN_API_SOURCE, check_query as check_precedent_query, search_precedents, suggest_keywords,
)
from auth_ip import auth_request_settings, client_ip_from, forwarded_chain_summary
from billing import billing_mode, parse_plans, plan_summary, sales_open
from policy_text import (
    CHANGES_V2_TO_V3, PRIVACY_V3_REVISED_DATE, build_privacy_policy_v2, build_privacy_policy_v3,
)
from case_guide import (
    CASE_FIELDS, CASE_FIELD_LABELS, CASE_LONG_TEXT_FIELDS, CASE_FIELD_MAX_CHARS, DAMAGE_TYPES,
    LEGAL_TERMS_SOURCE_LABEL, PROGRESS_ITEMS, PROGRESS_NOTE_MAX_CHARS, PROGRESS_NOTICE, PROGRESS_PLACE_MAX_CHARS, ROUTE_TAGS,
    clean_progress, legal_terms_for, progress_summary, search_legal_terms, type_faq, with_draft_disclaimer,
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
    "list_draft_", "evidence_", "confirm_delete_", "voice_", "chat_", "prog_", "progress_", "prec_", "ws_",
)

# 비밀번호 재설정은 Supabase에 직접 메일 서버(SMTP)와 재설정 메일 템플릿을 설정한 뒤 켠다.
# 기본 메일 설정에서는 재설정 링크가 이 앱의 token_hash 처리 방식과 맞지 않아 동작하지 않는다.
PASSWORD_RESET_ENABLED = os.environ.get("PASSWORD_RESET_ENABLED") == "true"
# 이메일 가입. Supabase 기본 메일은 프로젝트 팀원 주소로만 보내므로(공식 문서), 메일 서버(SMTP)를 연결하기 전에는
# 일반 이용자가 인증 메일을 받지 못한다. 운영자가 "false"로 두면 이메일 가입만 잠시 닫고 네이버·Google 가입을 안내한다.
# 설정이 없으면 지금처럼 열어 둔다.
EMAIL_SIGNUP_ENABLED = os.environ.get("EMAIL_SIGNUP_ENABLED", "true") != "false"
EMAIL_SEND_FAILED_MESSAGE = (
    "지금은 인증 메일을 보낼 수 없어 이메일 가입을 마칠 수 없어요(메일 발송 준비 중). 네이버 또는 Google로 가입해 주세요."
)
# 이용자 IP를 Supabase Auth에 전달해 IP별 제한을 이용자마다 따로 적용(auth_ip.py). 기본은 꺼짐.
AUTH_IP_FORWARDING_ENABLED = os.environ.get("AUTH_IP_FORWARDING_ENABLED") == "true"

UNSUPPORTED_ASSISTANT_MESSAGE = "죄송합니다. 현재 고소미는 일정 관리·메모 기억 기능을 지원하지 않습니다. 법률 상담이나 사건 정리를 도와드릴게요."

PRIVACY_POLICY_ENABLED = os.environ.get("PRIVACY_POLICY_ENABLED") == "true"
PRIVACY_OPERATOR_NAME = os.environ.get("PRIVACY_OPERATOR_NAME")
PRIVACY_CONTACT_EMAIL = os.environ.get("PRIVACY_CONTACT_EMAIL")
PRIVACY_POLICY_EFFECTIVE_DATE = (os.environ.get("PRIVACY_POLICY_EFFECTIVE_DATE") or "").strip() or None
# 이용 연령. 기본은 만 14세(지금 시행 중인 약관). 만 18세 기준(Gemini API 이용 조건)은 운영자가
# MIN_USER_AGE="18"과 새 약관 시행일 TERMS_EFFECTIVE_DATE_V2를 함께 설정할 때만 적용한다(수정안, 2026-10-12 준비).
# 새 약관 버전을 기록하려면 DB 함수 record_consent도 바꿔야 한다(supabase/proposals/age18/, 미적용).
_MIN_AGE_SETTING = os.environ.get("MIN_USER_AGE", "14").strip()
_TERMS_V2_DATE = (os.environ.get("TERMS_EFFECTIVE_DATE_V2") or "").strip()
MIN_USER_AGE = 18 if _MIN_AGE_SETTING == "18" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", _TERMS_V2_DATE) else 14
if _MIN_AGE_SETTING == "18" and MIN_USER_AGE != 18:
    print("[config] MIN_USER_AGE=18 needs TERMS_EFFECTIVE_DATE_V2 (YYYY-MM-DD); keeping 14", flush=True)
TERMS_EFFECTIVE_DATE = _TERMS_V2_DATE if MIN_USER_AGE == 18 else "2026-09-15"


def age_text(text: str) -> str:
    """화면 문구의 '만 14세'를 지금 이용 연령으로 바꾼다(기본 14세면 그대로)."""
    return text.replace("만 14세", f"만 {MIN_USER_AGE}세") if MIN_USER_AGE != 14 else text
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
# 새 AI 대화(이어지는 대화·사건 정보 정리)와 음성 대화. v3 방침에 적은 처리라 시행 전에는 관리자 시험 모드에서만 보인다.
AI_CHAT_V2_ENABLED = os.environ.get("AI_CHAT_V2_ENABLED") == "true"
VOICE_CHAT_ENABLED = os.environ.get("VOICE_CHAT_ENABLED") == "true"
# 답변을 Gemini 음성 생성(TTS)으로 읽는 '고소미 서버 목소리'. 답변 글이 음성용으로 한 번 더 Google에 가므로,
# 이를 적은 방침(v3) 시행 전에는 관리자에게만 보인다. 기본은 꺼짐(비용 발생).
VOICE_SERVER_TTS_ENABLED = os.environ.get("VOICE_SERVER_TTS_ENABLED") == "true"
GEMINI_TTS_MODEL = os.environ.get("GEMINI_TTS_MODEL") or GEMINI_TTS_DEFAULT_MODEL
GEMINI_TTS_TIMEOUT_MS = 60_000
# 용어 풀이·유형별 Q&A 보강·진행 체크리스트(미리보기)·핵심만 보기·판례 찾기. 켜기 전에는 관리자에게만 보인다.
ASSIST_V2_ENABLED = os.environ.get("ASSIST_V2_ENABLED") == "true"
# 사건 작업 공간 저장·저장한 사건 수정. DB 변경(20261012000000_case_workspace_and_case_edit) 적용 후에 켠다.
# 켜면 관리자는 v3 시행 전에도 시험할 수 있고, 일반 이용자는 v3 시행 후에만 쓸 수 있다.
CASE_WORKSPACE_SAVE_ENABLED = os.environ.get("CASE_WORKSPACE_SAVE_ENABLED") == "true"
# 법제처 국가법령정보 Open API 사용자 ID(OC). 사용 신청·서버 IP 등록 전에는 비워 둔다. 값은 화면·로그에 내지 않는다.
LAW_OPEN_API_OC = (os.environ.get("LAW_OPEN_API_OC") or "").strip()
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
SOCIAL_LOGIN_NOTICE = age_text(
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
    "- 이 서비스의 이름은 \"고소미\"이다(이전 이름: 고소음이).\n"
    "- \"고소미\"는 같은 이름의 과자 등 다른 상품·회사와 관계없다. 그런 상품이나 소음 관련 서비스로 오해하거나 "
    "그렇게 답변하지 마라.\n"
    "- \"고소미\"는 범죄·피해 상황을 사용자가 정리하고, 고소장 참고용 초안을 작성하도록 돕는 "
    "AI 법률정보 도우미이다.\n"
    "- \"고소미\"는 변호사의 법률자문을 대체하지 않는다.\n"
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
        if code in ("email_address_not_authorized", "email_provider_disabled") or (
            status == 500 and "sending" in str(getattr(error, "message", "") or error).lower()
        ):
            return EMAIL_SEND_FAILED_MESSAGE
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
    # 서버 업로드 저장소의 녹음(사건 음성 입력)은 위젯 상태를 지우기 전에 파일 ID로 지운다.
    # 음성 대화의 녹음은 업로드 저장소를 거치지 않고, 처리한 요청 안에서만 쓴다.
    purge_session_audio()
    # 이전 계정이 입력하던 사건 내용·초안·다운로드 준비 파일이 다음 계정 화면에 남지 않게 지운다.
    for key in list(st.session_state.keys()):
        if key.startswith(CASE_UI_STATE_PREFIXES):
            del st.session_state[key]


def purge_session_audio() -> None:
    for key in list(st.session_state.keys()):
        if key.startswith(("voice_record_", "voice_upload_")):
            uploaded = st.session_state.get(key)
            if uploaded is not None:
                purge_uploaded_audio(uploaded)


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
    # 네이버 로그인은 이름을 받지 않으므로 이메일을, 이메일이 없으면 고소미 사용자 ID를 보여준다.
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
    return DRAFT_SAVE_ENABLED and (privacy_v3_in_effect() or is_admin())


def workspace_save_available() -> bool:
    """사건 작업 공간 저장과 저장한 사건 수정. 플래그가 꺼져 있으면 누구에게도 저장하지 않는 미리보기로 보인다."""
    return CASE_WORKSPACE_SAVE_ENABLED and (privacy_v3_in_effect() or is_admin())


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
            f"초안 저장: **{'켜짐' if draft_save_available() else '꺼짐'}**, "
            f"새 AI 대화 공개: **{'켜짐' if chat_v2_public() else '꺼짐'}**, "
            f"음성 대화 공개: **{'켜짐' if VOICE_CHAT_ENABLED and chat_v2_public() else '꺼짐'}**, "
            f"보조 기능 공개: **{'켜짐' if ASSIST_V2_ENABLED else '꺼짐'}**, "
            f"판례 Open API: **{'설정됨' if LAW_OPEN_API_OC else '없음'}**\n"
            f"- AI 키: **{'설정됨' if os.environ.get('GEMINI_API_KEY') else '없음'}** (값은 표시하지 않음)"
        )
        st.caption(
            "IP 감지가 '안 됨'이면 IP 전달 기능을 켜도 효과가 없습니다. '됨'이어도 Streamlit 앞단 프록시가 "
            "헤더를 덮어쓰는지(이용자가 넣은 값을 지우는지) 공개 자료로 확인되기 전에는 켜지 마세요."
        )


def voice_input_available() -> bool:
    return (VOICE_INPUT_ENABLED and privacy_v3_in_effect()) or voice_admin_test_mode()


def chat_v2_public() -> bool:
    return AI_CHAT_V2_ENABLED and privacy_v3_in_effect()


def chat_v2_available() -> bool:
    return chat_v2_public() or is_admin()


def assist_preview() -> bool:
    return ASSIST_V2_ENABLED or is_admin()


def voice_chat_available() -> bool:
    return (VOICE_CHAT_ENABLED and chat_v2_public()) or is_admin()


def server_tts_available() -> bool:
    return VOICE_SERVER_TTS_ENABLED and voice_chat_available() and (privacy_v3_in_effect() or is_admin())


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


class SaveConflict(RuntimeError):
    """다른 곳(다른 탭·기기)에서 먼저 저장해, 확인 없이 덮어쓰지 않으려고 멈춘 경우."""


def save_case_draft(case_id: str, draft_text: str, expected_at=False) -> str:
    """초안을 저장(이미 있으면 덮어쓰기)하고 서버에 기록된 저장 시각을 돌려준다.

    expected_at을 주면(None 포함) 서버의 마지막 저장 시각이 그 값과 같을 때만 저장하고, 다르면 SaveConflict.
    """
    if expected_at is not False:
        current = load_case_draft(case_id)
        if (current or {}).get("updated_at") != expected_at:
            raise SaveConflict("draft")
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


WORKSPACE_TABLE = "case_workspace"
CASE_EDIT_FIELDS = [key for _, key in CASE_FIELDS]


def load_workspace_row(case_id: str):
    session = st.session_state.get("supabase_session") or {}
    response = (
        get_authed_client().table(WORKSPACE_TABLE).select("*")
        .eq("user_id", session.get("user_id", "")).eq("case_id", str(case_id)).limit(1).execute()
    )
    return (response.data or [None])[0]


def save_workspace_row(case_id: str, payload: dict, version) -> dict:
    """작업 공간 저장. 처음이면 만들고, 아니면 읽어 둔 version과 같을 때만 고친다(아니면 SaveConflict)."""
    session = ensure_active_session()
    if not session or not session.get("user_id"):
        raise RuntimeError("로그인이 만료되었습니다. 다시 로그인해 주세요.")
    table = get_authed_client().table(WORKSPACE_TABLE)
    if version is None:
        try:
            rows = table.insert({**payload, "user_id": session["user_id"], "case_id": str(case_id)}).execute().data
        except Exception as error:
            if "23505" in str(error) or "duplicate" in str(error).lower():
                raise SaveConflict("workspace") from error
            raise
    else:
        rows = (
            table.update(payload).eq("user_id", session["user_id"]).eq("case_id", str(case_id))
            .eq("version", int(version)).execute().data
        )
    if not rows:
        raise SaveConflict("workspace")
    return rows[0]


def update_case_row(case_id: str, values: dict, edit_version) -> dict:
    """저장한 사건의 내용 칸 고치기. 읽어 둔 edit_version과 같을 때만 고친다(아니면 SaveConflict)."""
    session = ensure_active_session()
    if not session or not session.get("user_id"):
        raise RuntimeError("로그인이 만료되었습니다. 다시 로그인해 주세요.")
    clean = {k: (v or "").strip()[: CASE_FIELD_MAX_CHARS[k]] for k, v in values.items() if k in CASE_EDIT_FIELDS}
    rows = (
        get_authed_client().table(CASES_TABLE).update(clean)
        .eq("user_id", session["user_id"]).eq("case_id", str(case_id)).eq("edit_version", int(edit_version or 1))
        .execute().data
    )
    if not rows:
        raise SaveConflict("case")
    return rows[0]


def map_workspace_save_error(error: Exception) -> str:
    text = str(getattr(error, "message", None) or error).lower()
    if "로그인" in str(error) or "jwt" in text:
        return "로그인이 만료되어 저장하지 못했어요. 고친 내용은 화면에 그대로 있어요. 다시 로그인한 뒤 저장해 주세요."
    if "does not exist" in text or "pgrst205" in text or "schema cache" in text:
        return "저장 공간이 아직 준비되지 않았어요(운영자 설정 필요). 고친 내용은 화면에 그대로 있어요."
    return "저장하지 못했어요. 고친 내용은 화면에 그대로 있으니 잠시 후 '작업 저장'을 다시 눌러 주세요." + contact_hint()


GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_TIMEOUT_MS = 90_000
# 유료 API 비용 보호: 계정마다 1시간에 AI 호출(초안 생성·상담)을 이 횟수까지만 허용한다.
AI_CALLS_PER_HOUR = int(os.environ.get("AI_CALLS_PER_HOUR") or 30)
# 전체 이용자를 합친 1시간 호출 상한. 이용자가 많아져도 비용이 이 범위를 넘지 않게 한다.
AI_CALLS_PER_HOUR_TOTAL = int(os.environ.get("AI_CALLS_PER_HOUR_TOTAL") or 300)


class AiUsageLimitError(RuntimeError):
    pass


class AiPausedError(AiUsageLimitError):
    """운영자가 AI 기능을 잠시 멈춘 상태(AI_FEATURES_PAUSED). 사용량 초과와 같은 경로로 화면에 그대로 안내한다."""


# 운영자가 AI 기능(상담·초안·음성 받아쓰기·정리·요약·표현 바꾸기·서버 목소리)을 잠시 멈추는 설정.
# 예: Gemini를 유료 등급으로 바꾸기 전, 방침과 실제 처리가 다른 동안. 사건 저장·내 사건·내려받기는 그대로 쓴다.
AI_FEATURES_PAUSED = os.environ.get("AI_FEATURES_PAUSED") == "true"
AI_PAUSED_MESSAGE = "AI 기능을 점검 중입니다. 저장한 사건은 계속 확인할 수 있습니다."
AI_PAUSED_CASE_NOTE = "사건 내용은 지금도 저장할 수 있어요. 점검이 끝나면 '내 사건'에서 초안을 만들 수 있어요."


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


def reserve_ai_call() -> None:
    """계정별·전체 1시간 호출 한도를 하나 쓴다. 넘으면 AiUsageLimitError. AI를 멈춘 동안에는 AiPausedError."""
    if AI_FEATURES_PAUSED:
        raise AiPausedError(AI_PAUSED_MESSAGE)
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


def call_gemini(contents, system_instruction=None) -> str:
    reserve_ai_call()
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


def synthesize_speech(text: str, voice: str) -> str:
    """답변 글을 Gemini 음성 생성으로 WAV(base64)로 바꾼다. 키는 서버에서만 쓰고, 글·음성은 기록하지 않는다."""
    reserve_ai_call()
    return base64.b64encode(request_tts_audio(text, voice)).decode("ascii")


def request_tts_audio(text: str, voice: str) -> bytes:
    """공식 REST 주소로 음성 생성을 요청해 WAV 바이트를 돌려준다. 실패하면 TtsError(로그용 요약만 담음)."""
    if AI_FEATURES_PAUSED:
        raise AiPausedError(AI_PAUSED_MESSAGE)
    key = os.environ["GEMINI_API_KEY"]
    response = httpx.post(
        TTS_ENDPOINT, json=tts_request(text, voice, GEMINI_TTS_MODEL),
        headers={"x-goog-api-key": key, "Content-Type": "application/json"}, timeout=GEMINI_TTS_TIMEOUT_MS / 1000,
    )
    if response.status_code != 200:
        raise tts_error_from_response(response.status_code, response.text, hide=(key, text))
    try:
        return extract_tts_audio(response.json())
    except ValueError as error:
        raise TtsError(200, "NO_AUDIO", str(error)) from error


def log_tts_error(stage: str, error: Exception, voice: str = "") -> None:
    where = f"model={GEMINI_TTS_MODEL} voice={voice if voice in SERVER_VOICES else '-'}"
    if isinstance(error, TtsError):
        print(f"[voice_chat] stage={stage} result=TtsError http={error.http_status} status={error.status} {where} "
              f"detail={error.detail}", flush=True)
    else:
        print(f"[voice_chat] stage={stage} result={type(error).__name__} {where}", flush=True)


def tts_error_message(error: Exception) -> str:
    if isinstance(error, AiUsageLimitError):
        return str(error) + " 기기 목소리로 들을 수 있어요."
    if isinstance(error, TtsError) and error.http_status == 429:
        return tts_quota_message(error) + " 답변은 글로 볼 수 있고, 기기 목소리로 들을 수 있어요."
    return "서버 목소리를 만들지 못했어요. 답변은 글로 볼 수 있어요. 다시 시도하거나 기기 목소리로 들어 주세요."


def tts_quota_message(error: TtsError) -> str:
    """Google 사용량 한도(429). 무료 등급은 프로젝트 전체에 하루 횟수가 정해져 있고 태평양 시간 자정에 다시 채워진다."""
    wait = re.search(r"retry in (?:(\d+)h)?(?:(\d+)m)?", error.detail or "")
    hours = int(wait.group(1) or 0) if wait else None
    when = (f" 약 {hours}시간 뒤에 다시 쓸 수 있어요." if hours else " 1시간 안에 다시 쓸 수 있어요.") if wait else ""
    return "Google 음성 생성 사용량 한도(무료 등급 하루 횟수)를 다 써서 목소리를 만들지 못했어요." + when


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
    return with_draft_disclaimer(text)


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

/* ── 글자·색: 읽기 쉬운 크기와 대비 ── */
[data-testid="stMainBlockContainer"] { padding-top: 3rem !important; }
[data-testid="stHeaderActionElements"] { display: none !important; }
textarea::placeholder, input::placeholder { color: #66758B !important; opacity: 1 !important; }
html, body, [data-testid="stAppViewContainer"] { font-size: 17px; }
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li { line-height: 1.6; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color: #3E4C61 !important; font-size: 0.95rem !important; }
h2 { font-size: 1.55rem !important; color: var(--dgo-navy); padding-top: 0.6rem !important; }
h3 { font-size: 1.2rem !important; color: var(--dgo-navy); padding-top: 0.8rem !important; }
[data-testid="stWidgetLabel"] p { font-size: 1rem !important; font-weight: 600; color: var(--dgo-navy); }
[data-testid="stExpander"] summary p { font-size: 1rem; font-weight: 600; }
:focus-visible { outline: 3px solid var(--dgo-blue) !important; outline-offset: 2px; }

/* ── 버튼: 주 버튼(파랑), 보조 버튼(흰 바탕+테두리), 글자 버튼 ── */
[data-testid="stBaseButton-primary"] {
    background-color: var(--dgo-blue); color: #FFFFFF; border: 1px solid var(--dgo-blue);
    border-radius: 10px; font-weight: 700; min-height: 46px;
}
[data-testid="stBaseButton-primary"]:hover {
    background-color: var(--dgo-navy-light); border-color: var(--dgo-navy-light); color: #FFFFFF;
}
[data-testid="stBaseButton-secondary"] {
    background-color: #FFFFFF; color: var(--dgo-navy); border: 1px solid #9FB3D1;
    border-radius: 10px; font-weight: 600; min-height: 46px;
}
[data-testid="stBaseButton-secondary"]:hover { border-color: var(--dgo-blue); color: var(--dgo-blue); }
[data-testid="stBaseButton-tertiary"] { color: var(--dgo-navy); font-weight: 600; min-height: 40px; }
[data-testid="stBaseButton-primary"]:disabled, [data-testid="stBaseButton-secondary"]:disabled {
    background-color: #E3E8EF; color: #5B6B82; border-color: #E3E8EF;
}

/* ── 위쪽 막대: 이름(처음으로)·내 계정, 화면 이동 ── */
.st-key-top_bar [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; }
.st-key-top_bar [data-testid="stColumn"] { min-width: 0 !important; }
.st-key-top_bar [data-testid="stColumn"]:last-child { display: flex; justify-content: flex-end; }
.st-key-top_bar [data-testid="stColumn"]:last-child [data-testid="stVerticalBlock"] { align-items: flex-end; }
.st-key-account_btn { display: flex; justify-content: flex-end; width: 100%; }
.st-key-account_btn [data-testid="stButton"], .st-key-account_btn button { width: auto !important; }
/* 처음 화면 카드 세 개의 높이를 맞춘다(PC). */
[data-testid="stHorizontalBlock"]:has([class*="st-key-home_card_"]) { align-items: stretch; }
[data-testid="stHorizontalBlock"]:has([class*="st-key-home_card_"]) [data-testid="stColumn"] > [data-testid="stVerticalBlock"],
[data-testid="stLayoutWrapper"]:has(> [class*="st-key-home_card_"]),
[class*="st-key-home_card_"] { height: 100%; }
[class*="st-key-home_card_"] { justify-content: space-between; }
.st-key-brand_home_btn button { font-size: 1.35rem !important; font-weight: 800 !important; color: var(--dgo-navy) !important; padding-left: 0; }
.st-key-brand_home_btn button p { font-size: 1.35rem !important; font-weight: 800 !important; }
/* 화면 이동 막대·도움말 종류: 고른 칸은 파란 바탕+흰 글자로 분명하게 */
.st-key-nav_choice [data-testid="stButtonGroup"] > div, .st-key-help_section [data-testid="stButtonGroup"] > div,
[class*="st-key-ws_nav_"] [data-testid="stButtonGroup"] > div { gap: 6px; }
[class*="st-key-ws_nav_"] button { min-height: 44px; margin: 0 !important; border-radius: 10px !important; border: 1px solid #9FB3D1 !important; background: #FFFFFF; }
[class*="st-key-ws_nav_"] button[aria-checked="true"] { background: var(--dgo-navy) !important; border-color: var(--dgo-navy) !important; }
[class*="st-key-ws_nav_"] button[aria-checked="true"] p { color: #FFFFFF !important; font-weight: 700; }
.st-key-nav_choice button, .st-key-help_section button {
    min-height: 46px; margin: 0 !important; border-radius: 10px !important; border: 1px solid #9FB3D1 !important;
    background: #FFFFFF; color: var(--dgo-navy); font-size: 1rem;
}
.st-key-nav_choice button[aria-checked="true"], .st-key-help_section button[aria-checked="true"] {
    background: var(--dgo-blue) !important; border-color: var(--dgo-blue) !important; color: #FFFFFF !important;
}
.st-key-nav_choice button[aria-checked="true"] p, .st-key-help_section button[aria-checked="true"] p { color: #FFFFFF !important; font-weight: 700; }
@media (max-width: 640px) {
    /* 휴대폰: 세 칸씩 같은 너비로 두 줄에 배치 */
    .st-key-nav_choice [data-testid="stButtonGroup"] > div,
    [class*="st-key-ws_nav_"] [data-testid="stButtonGroup"] > div { display: grid !important; grid-template-columns: repeat(3, 1fr); }
    [class*="st-key-ws_nav_"] button { width: 100%; padding: 0 4px; font-size: 0.95rem; }
    .st-key-nav_choice button { width: 100%; padding: 0 4px; font-size: 0.95rem; }
}

/* ── 사건 정리: 화면 아래에 붙는 저장 영역, 항목 카드, 지우기 버튼 ── */
/* Streamlit이 컨테이너를 감싸는 칸(stLayoutWrapper)에 붙여야 화면 아래에 고정된다. */
[data-testid="stLayoutWrapper"]:has(> [class*="st-key-ws_savebar_"]) { position: sticky; bottom: 0; z-index: 60; }
[class*="st-key-ws_savebar_"] {
    background: #FFFFFF;
    border-top: 2px solid #9FB3D1; box-shadow: 0 -6px 14px rgba(11, 31, 58, 0.08);
    padding: 10px 12px 12px 12px; margin-top: 0.5rem;
}
[class*="st-key-ws_savebar_"] [data-testid="stMarkdownContainer"] p { margin: 0; }
/* Streamlit 글 상자의 아래쪽 음수 여백 때문에 휴대폰에서 상태 글이 버튼에 가리지 않게 한다. */
[class*="st-key-ws_savebar_"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
[class*="st-key-ws_item_"] { background: #FAFBFD; }
[class*="st-key-ws_form_"] { background: #F3F7FE; border-color: #9FB3D1 !important; }
[class*="st-key-ws_del_"] button, .st-key-delete_account_main button {
    color: #B42318 !important; border-color: #E8A39B !important; background: #FFFFFF;
}
[class*="st-key-ws_del_"] button p, .st-key-delete_account_main button p { color: #B42318 !important; }
[class*="st-key-ws_del_"] button:hover, .st-key-delete_account_main button:hover { background: #FEF3F2 !important; }
@media (max-width: 640px) {
    /* 휴대폰: 저장 영역은 상태 한 줄 + 버튼, 항목 버튼은 한 줄에 나란히 */
    [class*="st-key-ws_savebar_"] [data-testid="stHorizontalBlock"] { gap: 0.4rem; }
    [class*="st-key-ws_btns_"] [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.4rem; }
    [class*="st-key-ws_btns_"] [data-testid="stColumn"] { min-width: 0 !important; width: auto !important; flex: 1 1 0 !important; }
    [class*="st-key-ws_btns_"] button { padding: 0 6px; }
}

/* ── 처음 화면 카드 ── */
[class*="st-key-home_card_"] { background: #F6F9FE; border-radius: 14px; }
[class*="st-key-home_card_"] h3 { padding-top: 0 !important; }

/* ── 고소장 작성 단계 표시 ── */
.dgo-steps { display: flex; gap: 6px; list-style: none; padding: 0; margin: 0.2rem 0 1rem 0; }
.dgo-step { flex: 1; display: flex; align-items: center; gap: 6px; padding: 8px 10px; border-radius: 10px;
    background: #F1F4F9; color: #5B6B82; font-weight: 600; font-size: 0.95rem; }
.dgo-step span { display: inline-flex; width: 24px; height: 24px; border-radius: 50%; align-items: center; justify-content: center;
    background: #C9D3E3; color: #FFFFFF; font-size: 0.85rem; flex-shrink: 0; }
.dgo-step.now { background: var(--dgo-blue-light); color: var(--dgo-navy); outline: 2px solid var(--dgo-blue); }
.dgo-step.now span, .dgo-step.done span { background: var(--dgo-blue); }
.dgo-step.done { color: var(--dgo-navy); }

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
        font-size: 1rem;
        line-height: 1.55;
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

    /* 휴대폰에서 주요 버튼은 가로 전체로 넓혀 누르기 쉽게 한다(아이콘 버튼·글자 버튼 제외). */
    [data-testid="stElementContainer"]:has([data-testid="stBaseButton-primary"], [data-testid="stDownloadButton"]) [data-testid="stButton"],
    [data-testid="stElementContainer"]:has([data-testid="stBaseButton-primary"], [data-testid="stDownloadButton"]) [data-testid="stDownloadButton"],
    [data-testid="stBaseButton-primary"], [data-testid="stDownloadButton"] button {
        width: 100% !important;
    }
    .st-key-chat_voice_open_btn [data-testid="stButton"], .st-key-chat_voice_open_btn button { width: 52px !important; }
    .dgo-step { font-size: 0.85rem; padding: 6px 6px; gap: 4px; }

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
        return build_privacy_policy_v3(*args, min_age=MIN_USER_AGE)
    return build_privacy_policy_v2(*args)


@st.dialog("개인정보처리방침", width="large")
def render_privacy_policy_dialog():
    if not privacy_config_ready():
        st.info("개인정보처리방침을 준비 중입니다. 잠시 후 다시 확인해 주세요.")
    else:
        st.markdown(build_privacy_policy_text())


TERMS_AGE18_CLAUSE = (
    "서비스는 만 18세 이상만 이용할 수 있습니다. 서비스의 AI 기능에 쓰는 Google Gemini API의 이용 조건(18세 이상, "
    "18세 미만이 이용할 서비스에서 사용 금지)에 따른 것입니다. 만 18세 미만인 경우 회원가입 및 서비스 이용이 제한됩니다.\n"
    "이 기준을 시행하기 전에 가입한 이용자는 다음 이용 때 만 18세 이상인지 다시 확인합니다. 확인하지 않으면 서비스를 이용할 수 "
    "없으며, 이때에도 운영자는 계정과 저장한 자료를 임의로 삭제하지 않고, 이용자는 언제든지 직접 탈퇴해 모두 삭제할 수 있습니다."
)


def build_terms_of_service_text():
    """이용약관. 기본(만 14세)은 2026-09-15 시행본과 똑같은 문구이고, 만 18세 기준이면 연령 조항만 바꾼 새 버전이다."""
    text = _terms_of_service_v1()
    if MIN_USER_AGE == 18:
        text = text.replace("서비스는 만 14세 이상만 이용할 수 있습니다. 만 14세 미만인 경우 회원가입 및 서비스 이용이 제한됩니다.",
                            TERMS_AGE18_CLAUSE)
    return text


def _terms_of_service_v1():
    return f"""
### 이용약관

이 약관은 {PRIVACY_OPERATOR_NAME}(이하 "운영자")가 개인 자격으로 제공하는 "고소미"(이전 서비스명: 고소음이, 이하 "서비스")의 이용에 관한 조건을 정합니다.
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
        '<div class="dgo-hero-title">⚖️ 고소미</div>'
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
        st.markdown(build_privacy_policy_v3(PRIVACY_OPERATOR_NAME, PRIVACY_CONTACT_EMAIL, upcoming.isoformat(),
                                            min_age=MIN_USER_AGE))


def render_footer():
    contact = ""
    if PRIVACY_CONTACT_EMAIL:
        email = html.escape(PRIVACY_CONTACT_EMAIL, quote=True)
        contact = f'<br>문의: <a href="mailto:{email}">{email}</a>'
    st.markdown(
        '<div class="dgo-footer">'
        '고소미는 법률정보와 문서 작성을 돕는 서비스이며, 변호사의 법률자문을 대체하지 않습니다.'
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
    preview = assist_preview()
    if preview:
        # 유형별 Q&A에 준비 자료·상대방 정보·절차 질문을 보탠다(이미 확인한 안내로만 만든다).
        guide = {**guide, "qa": type_faq(case_type)}
    route = ROUTES.get(guide.get("route"))
    if guide.get("urgent"):
        st.error("🚨 " + guide["urgent"])
    if route:
        tag = f"[{ROUTE_TAGS[guide['route']]}] " if preview and guide.get("route") in ROUTE_TAGS else ""
        message = f"**{tag}{route['label']}**  \n{route['note']}"
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


def render_legal_terms(screen: str, title: str = "📖 어려운 말 풀이") -> None:
    """화면에 나오는 법률 용어를 쉬운 말과 예시로 펼쳐 보기. 기본 화면에는 접힌 제목만 보인다."""
    terms = legal_terms_for(screen)
    with st.expander(title):
        for term, plain, example in terms:
            st.markdown(f"**{term}** — {plain}  \n:gray[예: {example}]")
        st.caption(f"뜻 출처: [{LEGAL_TERMS_SOURCE_LABEL}]({LEGAL_TERMS_SOURCE}) · 예시는 이해를 돕기 위한 가상의 상황이에요.")


def render_legal_terms_search(key: str) -> None:
    query = st.text_input("용어 찾기", placeholder="예: 송치, 불송치, 합의", key=key)
    found = search_legal_terms(query)
    if not found:
        st.caption("찾는 용어가 없어요. 다른 말로 찾아보세요.")
    for term, plain, example in found:
        st.markdown(f"**{term}** — {plain}  \n:gray[예: {example}]")
    st.caption(f"뜻 출처: [{LEGAL_TERMS_SOURCE_LABEL}]({LEGAL_TERMS_SOURCE}) · 예시는 가상의 상황이에요.")


def render_precedent_finder(case_type: str) -> None:
    """공식 판례 검색으로 연결한다. 앱이 판례를 고르거나 만들어 보여 주지 않는다."""
    label, url = OFFICIAL_PRECEDENT_SEARCH
    with st.expander("🔎 판례 찾아보기 (외부 공식 사이트)"):
        st.caption(
            "고소미는 판례를 직접 검색하지 않아요. 아래 버튼을 누르면 법제처 국가법령정보센터의 판례 검색 화면이 새 창으로 열려요. "
            "검색어를 복사해 그 화면의 검색창에 붙여 넣어 주세요. 판례는 사건마다 사실관계가 달라 내 사건 결과를 뜻하지 않아요."
        )
        st.link_button(f"{label} 열기 ↗", url, width="stretch")
        keywords = suggest_keywords(case_type)
        if keywords:
            st.caption("검색어 예시 (오른쪽 위 아이콘으로 복사)")
            for word in keywords:
                st.code(word, language=None)
        st.caption("검색창에 이름·전화번호·계좌번호 같은 개인정보는 넣지 마세요.")
        if LAW_OPEN_API_OC:
            render_precedent_api_search(case_type)


def render_precedent_api_search(case_type: str) -> None:
    """법제처 Open API가 설정된 경우에만: 실제로 조회된 판례 목록만 출처와 함께 보여 준다."""
    query = st.text_input("국가법령정보센터에서 판례 목록 조회", key=f"prec_query_{case_type}", max_chars=40)
    if st.button("목록 조회", key=f"prec_search_{case_type}"):
        problem = check_precedent_query(query)
        if problem:
            st.warning(problem)
            return
        try:
            with st.spinner("국가법령정보센터에서 조회하고 있어요."):
                rows = search_precedents(
                    query, LAW_OPEN_API_OC,
                    lambda u: urlopen(Request(u, headers={"User-Agent": "gosoemi"}), timeout=15).read().decode("utf-8"),
                )
        except Exception as error:
            print(f"[precedent] result={type(error).__name__}", flush=True)
            st.error("판례 목록을 받지 못했어요. 위 버튼으로 공식 사이트에서 직접 찾아 주세요.")
            return
        if not rows:
            st.info("조회된 판례가 없어요. 다른 검색어로 찾아보세요.")
        for row in rows:
            st.markdown(f"**{md_escape(row['name'])}**  \n{md_escape(row['court'])} · {md_escape(row['case_no'])} · 선고 {md_escape(row['date'])}")
        if rows:
            st.caption(f"출처: {OPEN_API_SOURCE}. 전문은 위 공식 검색 화면에서 사건번호로 찾아 주세요.")


def render_case_progress(case_id: str) -> None:
    """사건 진행 체크리스트(미리보기). 이용자가 직접 표시하는 준비 기록이며, 지금은 이 탭에서만 기억하고 저장하지 않는다."""
    prefix = f"prog_{case_id}_"
    # 위젯 값은 위젯 키에만 둔다(기본값을 매번 넘기면 Streamlit이 새 위젯으로 보고 체크를 되돌린다).
    for key, _ in PROGRESS_ITEMS:
        st.session_state.setdefault(prefix + key, False)
    st.session_state.setdefault(prefix + "to", "")
    st.session_state.setdefault(prefix + "on", None)
    st.session_state.setdefault(prefix + "note", "")
    on = st.session_state.get(prefix + "on")
    record = clean_progress({
        **{key: st.session_state[prefix + key] for key, _ in PROGRESS_ITEMS},
        "submitted_to": st.session_state[prefix + "to"],
        "submitted_on": on.isoformat() if on else "",
        "next_note": st.session_state[prefix + "note"],
    })
    done, total = progress_summary(record)
    # 제목을 바꾸면 Streamlit이 새 펼침 상자로 보고 접어 버리므로 제목은 고정하고 개수는 안에 적는다.
    with st.expander("✅ 진행 체크리스트"):
        st.markdown(f"**{done}/{total}개 표시함**")
        st.caption(PROGRESS_NOTICE)
        if workspace_save_available():
            st.caption("표시한 내용은 화면 아래 '작업 저장'을 누르면 저장돼요.")
        else:
            st.caption("🧪 미리보기: 표시한 내용은 저장되지 않고, 새로고침하거나 로그아웃하면 사라져요.")
        for key, label in PROGRESS_ITEMS:
            st.checkbox(label, key=prefix + key)
        if st.session_state[prefix + "submitted"]:
            st.text_input(
                "제출한 곳 (선택)", key=prefix + "to", max_chars=PROGRESS_PLACE_MAX_CHARS,
                placeholder="예: ○○경찰서, 경찰청 사이버범죄 신고시스템",
            )
            st.date_input("제출한 날 (선택)", key=prefix + "on", format="YYYY-MM-DD")
        st.text_area(
            "다음에 할 일 메모 (선택)", key=prefix + "note", max_chars=PROGRESS_NOTE_MAX_CHARS, height=70,
            placeholder="예: 담당 수사관 연락 기다리기, 추가 자료 내기",
        )

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
    st.session_state.review_skipped = []


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


def render_draft_panel(editor_key: str, original: str, case: dict, case_id: str, show_save: bool = True,
                       show_download: bool = True) -> None:
    # 사건 작업 공간에서는 화면 아래 '작업 저장' 버튼 하나로 초안까지 저장하므로 이 패널의 저장 버튼은 숨긴다.
    can_save = draft_save_available() and show_save
    saved = st.session_state.get(f"{editor_key}_saved")
    st.markdown("#### 고소장 초안")
    st.caption(
        "AI가 입력하신 내용만으로 만든 참고용 초안이에요. 제출 전 사실관계와 내용을 직접 확인하고, 법률 전문가의 검토를 받아 주세요."
    )
    if assist_preview():
        render_legal_terms("draft", "📖 초안에 나오는 말 풀이")
    if editor_key not in st.session_state:
        st.session_state[editor_key] = saved["text"] if saved else original

    missing = missing_info_items(case)
    if missing:
        st.warning(
            "제출 전에 확인할 항목입니다. 초안의 '[확인 필요]' 부분을 채우거나 지워 주세요.\n\n"
            + "\n".join(f"- {item}" for item in missing)
        )

    edited = st.text_area(
        "초안 내용 (직접 고칠 수 있어요)", key=editor_key, height=420, max_chars=CASE_DRAFT_MAX_CHARS,
        help="고친 뒤 칸 바깥을 한 번 누르거나 Ctrl+Enter(맥은 ⌘+Enter)를 누르면 저장·내려받기에 반영돼요. 내려받는 문서 끝에는 참고용 초안 안내가 한 번 붙어요.",
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
    if show_download:
        # Word가 주로 쓰는 형식이라 앞에 크게, 글자만 필요한 경우를 위해 TXT를 옆에 둔다.
        col_docx_download, col_txt_download = st.columns(2)
        with col_docx_download:
            st.download_button(
                "Word로 내려받기",
                data=build_case_draft_docx(edited),
                file_name=f"{file_stem}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"{editor_key}_docx",
                width="stretch",
                type="primary",
                icon=":material/download:",
            )
        with col_txt_download:
            st.download_button(
                "TXT로 내려받기",
                data=with_draft_disclaimer(edited),
                file_name=f"{file_stem}.txt",
                mime="text/plain",
                key=f"{editor_key}_txt",
                width="stretch",
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
    if not can_save and show_download:
        st.caption(
            "초안은 서버에 저장되지 않아요. 필요하면 내려받아 보관하세요. "
            "'내 사건'에서 언제든 다시 만들 수 있어요."
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
    title = "🎤 말로 입력칸 채우기 (선택)" + (" · 관리자 시험" if admin_test else "")
    with st.expander(title):
        if admin_test:
            st.caption("관리자 시험 중이에요. 개인정보가 없는 시험 음성만 사용해 주세요.")
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


# ── AI 대화(글 채팅·음성 대화) ─────────────────────────────────────
_voice_chat_component = st.components.v2.component("gosoemi_voice_chat", js=VOICE_CHAT_JS, css=VOICE_CHAT_CSS)

CHAT_CSS = """
<style>
.st-key-chat_input_row [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.5rem; align-items: flex-end; }
.st-key-chat_input_row [data-testid="stColumn"] { min-width: 0 !important; }
.st-key-chat_input_row [data-testid="stColumn"]:first-child { flex: 1 1 auto !important; width: auto !important; }
.st-key-chat_input_row [data-testid="stColumn"]:last-child { flex: 0 0 52px !important; width: 52px !important; }
.st-key-chat_voice_open_btn button { width: 52px; height: 52px; border-radius: 50%; padding: 0; }
.st-key-chat_voice_open_btn button span[data-testid="stIconMaterial"] { font-size: 26px; }
.st-key-chat_voice_open_btn button [data-testid="stMarkdownContainer"] { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.st-key-chat_menu_row [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; align-items: center; }
.st-key-chat_menu_row [data-testid="stColumn"]:first-child { flex: 1 1 auto !important; width: auto !important; min-width: 0 !important; }
.st-key-chat_menu_row [data-testid="stColumn"]:last-child { flex: 0 0 auto !important; width: auto !important; min-width: 0 !important; }
</style>
"""


def chat_messages() -> list:
    return st.session_state.setdefault("chat_messages", [])


def reset_chat(notice: str) -> None:
    """대화·대기 중 질문·실패한 질문·정리 제안을 모두 지운다(버튼 콜백). 글·음성 대화가 같은 기록을 쓴다."""
    for key in list(st.session_state.keys()):
        if key.startswith(("chat_pick_", "chat_edit_")) or key in (
            "chat_messages", "chat_pending", "chat_failed", "chat_last_sent", "chat_proposal",
            "chat_retry_text", "chat_voice_reply", "chat_voice_notice", "chat_voice_last", "chat_voice_preview",
        ):
            del st.session_state[key]
    st.session_state.chat_notice = notice


def open_voice_mode() -> None:
    st.session_state.chat_mode = "voice"
    st.session_state.pop("chat_voice_reply", None)
    st.session_state.pop("chat_voice_notice", None)


def close_voice_mode() -> None:
    # 음성 대화 부품이 화면에서 사라지면 브라우저에서 마이크와 읽기를 멈춘다(부품의 정리 함수).
    st.session_state.chat_mode = "text"
    st.session_state.pop("chat_voice_reply", None)   # 서버 목소리 음성도 함께 지운다
    st.session_state.pop("chat_voice_notice", None)
    st.session_state.pop("chat_voice_preview", None)


def queue_chat_question(text: str, via_voice: bool) -> str:
    """질문을 보낼 차례에 올린다. 문제가 있으면 안내 문구를 돌려준다."""
    question = normalize_question(text)
    if not question:
        return "보낼 내용이 없습니다. 질문을 적어 주세요."
    if st.session_state.get("chat_pending"):
        return "앞의 질문에 답변을 만들고 있습니다. 답변이 나온 뒤에 보내 주세요."
    if is_duplicate_submit(st.session_state.get("chat_last_sent"), question):
        return "방금 보낸 질문과 같아 한 번만 보냈습니다."
    st.session_state.chat_pending = {"text": question, "voice": via_voice}
    st.session_state.pop("chat_failed", None)
    return ""


def retry_chat_question() -> None:
    failed = st.session_state.get("chat_failed") or {}
    problem = queue_chat_question(st.session_state.get("chat_retry_text", ""), via_voice=failed.get("voice", False))
    if problem:
        st.session_state.chat_notice = problem
    else:
        st.session_state.pop("chat_retry_text", None)


def drop_failed_question() -> None:
    st.session_state.pop("chat_failed", None)
    st.session_state.pop("chat_retry_text", None)


def remember_failed_question(text: str, via_voice: bool, error: Exception) -> None:
    st.session_state.chat_failed = {
        "text": text, "voice": via_voice,
        "error": ai_error_message(error, "답변을 만들지 못했습니다. 적은 내용은 아래에 남겨 두었으니 잠시 후 다시 보내 주세요."),
    }
    st.session_state.chat_retry_text = text


def answer_chat_question() -> None:
    """대기 중인 질문에 답한다. 결과는 다른 Streamlit 호출보다 먼저 세션에 적어, 화면이 다시 그려져도 두 번 호출하지 않게 한다."""
    pending = st.session_state.get("chat_pending")
    if not pending:
        return
    messages = chat_messages()
    try:
        answer = call_gemini(
            model_contents(messages, pending["text"]),
            chat_system_instruction(SERVICE_IDENTITY_INSTRUCTION, SAFE_DRAFT_INSTRUCTION),
        )
    except Exception as error:
        remember_failed_question(pending["text"], pending.get("voice", False), error)
        st.session_state.chat_pending = None
        print(f"[chat] stage=answer result={type(error).__name__}", flush=True)
        return
    append_exchange(messages, pending["text"], answer, via_voice=pending.get("voice", False))
    st.session_state.chat_last_sent = {"digest": question_digest(pending["text"]), "at": time.time()}
    st.session_state.chat_pending = None


def set_voice_notice(sid: str, seq: int, kind: str, text: str) -> None:
    st.session_state.chat_voice_reply = None
    st.session_state.chat_voice_notice = {"sid": sid, "id": seq, "kind": kind, "text": text}


def handle_voice_turn(payload) -> None:
    """음성 대화 부품이 보낸 말 한 마디를 글로 바꾸고 답한다. 원본 녹음은 이 함수 안에서만 쓰고 남기지 않는다."""
    sid = payload.get("sid") if isinstance(payload, dict) else None
    seq = payload.get("seq") if isinstance(payload, dict) else None
    try:
        sid, seq, wav = decode_voice_turn(payload)
    except ValueError as error:
        print(f"[voice_chat] stage=decode result={error}", flush=True)
        if isinstance(sid, str) and isinstance(seq, int):
            set_voice_notice(sid, seq, "retry", "말씀을 받지 못했어요. 다시 말씀해 주세요.")
        return
    if st.session_state.get("chat_voice_last") == [sid, seq]:
        return  # 같은 말을 두 번 처리하지 않는다
    st.session_state.chat_voice_last = [sid, seq]
    try:
        transcript = call_gemini([
            types.Part.from_text(text=transcription_prompt()),
            types.Part.from_bytes(data=wav, mime_type="audio/wav"),
        ])
    except Exception as error:
        del wav
        print(f"[voice_chat] stage=transcribe result={type(error).__name__}", flush=True)
        stop = isinstance(error, AiUsageLimitError)
        set_voice_notice(sid, seq, "stop" if stop else "retry", (
            str(error) + " 글 채팅은 사용량이 풀린 뒤 이용할 수 있어요." if stop
            else "말씀을 글로 바꾸지 못했어요. 다시 말씀해 주시거나 '글 채팅으로'를 눌러 글로 적어 주세요."
        ))
        return
    del wav
    transcript = clean_transcript(transcript)[:CHAT_MAX_INPUT_CHARS]
    if is_empty_transcript(transcript):
        set_voice_notice(sid, seq, "retry", "잘 못 알아들었어요. 마이크 가까이에서 다시 말씀해 주세요.")
        return
    messages = chat_messages()
    try:
        answer = call_gemini(
            model_contents(messages, transcript),
            voice_system_instruction(SERVICE_IDENTITY_INSTRUCTION, SAFE_DRAFT_INSTRUCTION),
        )
    except Exception as error:
        print(f"[voice_chat] stage=answer result={type(error).__name__}", flush=True)
        remember_failed_question(transcript, True, error)
        stop = isinstance(error, AiUsageLimitError)
        set_voice_notice(sid, seq, "stop" if stop else "retry", (
            str(error) if stop else
            "답변을 만들지 못했어요. 다시 말씀하시거나, 글 채팅에서 방금 말씀을 고쳐 다시 보낼 수 있어요."
        ))
        return
    append_exchange(messages, transcript, answer, via_voice=True)
    st.session_state.chat_last_sent = {"digest": question_digest(transcript), "at": time.time()}
    st.session_state.pop("chat_failed", None)
    st.session_state.chat_voice_notice = None
    reply = {"sid": sid, "id": seq, "text": speech_text(answer)}
    tts = payload.get("tts") if isinstance(payload, dict) else None
    voice = tts.get("voice") if isinstance(tts, dict) else None
    if voice in SERVER_VOICES and server_tts_available():
        add_server_audio(reply, voice)
    st.session_state.chat_voice_reply = reply


def add_server_audio(reply: dict, voice: str) -> None:
    """답변에 서버 목소리를 붙인다. 실패하면 글 답변은 그대로 두고 다시 시도·기기 목소리 선택을 알린다."""
    reply.pop("audio", None)
    reply.pop("tts_error", None)
    try:
        reply["audio"] = synthesize_speech(server_speech_text(reply["text"]), voice)
    except Exception as error:
        log_tts_error("tts", error, voice)
        reply["tts_error"] = tts_error_message(error)


def handle_tts_retry(payload) -> None:
    """'서버 목소리 다시 시도': 지금 답변 글로 음성을 다시 만든다(질문·답변은 다시 보내지 않는다)."""
    reply = st.session_state.get("chat_voice_reply")
    if not isinstance(payload, dict) or not reply or payload.get("sid") != reply.get("sid") or payload.get("id") != reply.get("id"):
        return
    voice = payload.get("voice")
    if voice not in SERVER_VOICES or not server_tts_available():
        reply.pop("audio", None)
        reply["tts_error"] = "서버 목소리를 쓸 수 없어요. 기기 목소리로 들어 주세요."
    else:
        add_server_audio(reply, voice)
    reply["try"] = payload.get("n")
    st.session_state.chat_voice_reply = dict(reply)


@st.cache_resource
def preview_audio_cache() -> dict:
    """{(모델, 목소리): 미리듣기 음성(base64)}. 고정 예시 문장이라 개인정보가 없고, 같은 목소리를 다시 들어도 한도를 쓰지 않는다."""
    return {}


def handle_voice_preview(payload) -> None:
    """목소리 설정의 '미리듣기'(서버 목소리): 정해 둔 예시 문장만 음성으로 바꾼다."""
    if (not isinstance(payload, dict) or payload.get("voice") not in SERVER_VOICES or not server_tts_available()
            or not isinstance(payload.get("sid"), str) or not isinstance(payload.get("n"), int)):
        return
    preview = {"sid": payload["sid"][:32], "n": payload["n"]}
    cache, key = preview_audio_cache(), (GEMINI_TTS_MODEL, payload["voice"])
    if key in cache:
        preview["audio"] = cache[key]
        print(f"[voice_chat] stage=preview result=cached model={GEMINI_TTS_MODEL} voice={payload['voice']}", flush=True)
        st.session_state.chat_voice_preview = preview
        return
    try:
        preview["audio"] = cache[key] = synthesize_speech(VOICE_PREVIEW_TEXT, payload["voice"])
        print(f"[voice_chat] stage=preview result=ok model={GEMINI_TTS_MODEL} voice={payload['voice']} "
              f"seconds={wav_seconds(base64.b64decode(preview['audio']))}", flush=True)
    except Exception as error:
        log_tts_error("preview", error, payload["voice"])
        if isinstance(error, AiUsageLimitError):
            preview["error"] = str(error)
        elif isinstance(error, TtsError) and error.http_status == 429:
            preview["error"] = tts_quota_message(error) + " 기기 목소리는 계속 미리들을 수 있어요."
        else:
            preview["error"] = "미리듣기 목소리를 만들지 못했어요. 잠시 후 다시 눌러 주세요."
    st.session_state.chat_voice_preview = preview


def apply_chat_proposal() -> None:
    proposal = st.session_state.get("chat_proposal") or {}
    applied = 0
    for key in proposal:
        if not st.session_state.get(f"chat_pick_{key}"):
            continue
        value = (st.session_state.get(f"chat_edit_{key}", proposal[key]) or "").strip()
        if key == "case_type":
            if value not in DAMAGE_TYPES:
                continue
            st.session_state.case_type_choice = value
        else:
            if not value:
                continue
            st.session_state[f"case_field_{key}"] = value[: CASE_FIELD_MAX_CHARS[key]]
        applied += 1
    cancel_chat_proposal()
    if applied:
        # 넣은 내용을 바로 확인할 수 있게 고소장 작성 화면으로 옮긴다.
        st.session_state.case_notice = (
            f"대화에서 {applied}개 항목을 넣었어요. 아직 저장하지 않았어요. 내용을 확인·수정한 뒤 저장해 주세요. "
            f"'{NEEDS_CHECK}' 표시는 다시 확인이 필요한 부분이에요."
        )
        st.session_state.case_review = None
        st.session_state.nav_page = "case"
    else:
        st.session_state.chat_notice = "선택한 항목이 없어 입력칸을 바꾸지 않았어요."


def cancel_chat_proposal() -> None:
    st.session_state.chat_proposal = None
    for key in list(st.session_state.keys()):
        if key.startswith(("chat_pick_", "chat_edit_")):
            del st.session_state[key]


def run_chat_extraction() -> None:
    transcript = user_statements(chat_messages())
    try:
        with st.spinner("대화에서 말씀하신 내용을 항목별로 나누고 있습니다."):
            raw = call_gemini([{"role": "user", "parts": [{"text": extraction_prompt(transcript, list(DAMAGE_TYPES), SOURCE_CHAT)}]}])
            proposal = parse_extraction(raw, transcript, list(DAMAGE_TYPES))
    except ValueError:
        st.error("정리 결과를 읽지 못했어요. '이 대화로 사건 정리하기'를 한 번 더 눌러 주세요.")
        return
    except Exception as error:
        st.error(ai_error_message(error, "사건 정보를 정리하지 못했습니다. 잠시 후 다시 시도해 주세요."))
        return
    cancel_chat_proposal()
    if not proposal:
        st.warning("대화에서 입력칸에 넣을 내용을 찾지 못했습니다. 상황을 조금 더 말씀하시거나 작성 화면에 직접 적어 주세요.")
    st.session_state.chat_proposal = proposal or None


def render_chat_proposal() -> None:
    proposal = st.session_state.get("chat_proposal")
    if not proposal:
        return
    with st.container(border=True):
        st.markdown("**이 대화로 사건 정리하기** — 넣을 항목만 체크하고, 필요하면 고쳐 주세요.")
        st.caption(f"대화에서 직접 말씀하신 내용만 정리했어요. 말하지 않은 항목은 비워 두었고, 확인이 필요한 곳에는 '{NEEDS_CHECK}'를 붙였어요.")
        overwrites = 0
        for key, label, now, new, action in merge_rows(current_case_values(), proposal):
            overwrites += action == "overwrite"
            st.checkbox(
                f"{label}" + {"fill": "", "same": " — 지금과 같음", "overwrite": " — ⚠️ 이미 적은 내용을 바꿉니다"}[action],
                value=action == "fill", key=f"chat_pick_{key}", disabled=action == "same",
            )
            if key == "case_type":
                st.caption(f"제안: {new}")
            else:
                st.text_area(
                    f"{label} 제안 내용", value=new, key=f"chat_edit_{key}", label_visibility="collapsed",
                    height=68 if len(new) < 80 else 140, max_chars=CASE_FIELD_MAX_CHARS[key],
                )
            if action == "overwrite":
                st.caption(f"지금 작성 화면의 내용: {now[:150]}")
        missing = [CASE_FIELD_LABELS[key] for _, key in CASE_FIELDS if key not in proposal]
        if missing:
            st.caption("대화에서 찾지 못해 비워 둔 항목: " + ", ".join(missing))
        if overwrites:
            st.warning(f"이미 적은 항목 {overwrites}개는 기본으로 체크하지 않았어요. 바꾸려면 직접 체크해 주세요.")
        col_apply, col_cancel = st.columns(2)
        with col_apply:
            st.button("작성 화면에 넣기", key="chat_apply_btn", type="primary", on_click=apply_chat_proposal, width="stretch")
        with col_cancel:
            st.button("넣지 않기", key="chat_cancel_proposal_btn", on_click=cancel_chat_proposal, width="stretch")


def render_answer_summary(message: dict, index: int, busy: bool) -> None:
    """긴 답변의 '핵심만 보기'. 원래 답변만 근거로 요약하고, 답변에 없는 숫자가 섞이면 보여 주지 않는다."""
    summary = message.get("summary")
    if summary:
        with st.container(border=True):
            st.markdown("**핵심만 보기**  \n" + md_escape(summary).replace("\n", "  \n"))
            st.caption("AI가 위 답변만 줄여 쓴 것이에요. 자세한 내용은 위 답변을 확인해 주세요.")
        return
    if st.button("핵심만 보기 (짧게 요약)", key=f"chat_sum_{index}", icon=":material/summarize:", type="secondary",
                 disabled=busy or AI_FEATURES_PAUSED,
                 help="긴 답변을 핵심과 다음에 할 일로 짧게 줄여 보여 드려요."):
        try:
            with st.spinner("답변을 줄이고 있어요."):
                raw = call_gemini([{"role": "user", "parts": [{"text": summary_prompt(message["content"])}]}])
        except Exception as error:
            st.error(ai_error_message(error, "요약하지 못했어요. 잠시 후 다시 눌러 주세요."))
            return
        text = clean_summary(raw)
        if not text or not summary_supported(text, message["content"]):
            st.warning(SUMMARY_FAILED)
            return
        message["summary"] = text
        st.rerun()


def render_chat_details() -> None:
    st.markdown(f"- {CHAT_LIMITS_NOTE}\n- {CHAT_RETENTION_NOTE}\n- {VOICE_MIC_NOTE}\n- {SPEECH_NOTE}"
                + (f"\n- {SERVER_SPEECH_NOTE}" if server_tts_available() else ""))


def render_chat_text(voice_ok: bool) -> None:
    messages = chat_messages()
    busy = bool(st.session_state.get("chat_pending"))
    has_user_text = any(m["role"] == "user" for m in messages)
    extract = False
    with st.container(key="chat_menu_row"):
        col_note, col_menu = st.columns([6, 1])
        with col_note:
            badge = "" if chat_v2_public() else "🧪 관리자 시험 · "
            st.caption(badge + "일반 법률 정보를 안내해요. 법률 자문은 아니에요.")
        with col_menu:
            with st.popover("메뉴", icon=":material/more_horiz:"):
                st.button("새 대화", key="chat_new_btn", icon=":material/add_comment:", on_click=reset_chat,
                          args=("새 대화를 시작했어요.",), disabled=busy, width="stretch")
                st.button("대화 지우기", key="chat_clear_btn", icon=":material/delete:", on_click=reset_chat,
                          args=("대화를 지웠어요.",), disabled=busy or not (messages or st.session_state.get("chat_failed")),
                          width="stretch")
                if assist_preview():
                    with st.expander("용어 풀이"):
                        render_legal_terms_search("chat_terms_query")
                with st.expander("안내 자세히"):
                    render_chat_details()

    notice = st.session_state.pop("chat_notice", None)
    if notice:
        st.info(notice)

    history_box = st.container()
    with history_box:
        if not AI_FEATURES_PAUSED:
            with st.chat_message("assistant"):
                st.markdown(CHAT_WELCOME)
        for index, message in enumerate(messages):
            with st.chat_message(message["role"]):
                st.markdown(("🎤 " if message.get("voice") else "") + message["content"])
                if message["role"] == "assistant" and assist_preview() and len(message["content"]) >= SUMMARY_MIN_CHARS:
                    render_answer_summary(message, index, busy)

    failed = st.session_state.get("chat_failed")
    if failed and not busy:
        with st.container(border=True):
            st.error(failed["error"])
            st.text_area("보내지 못한 질문", key="chat_retry_text", max_chars=CHAT_MAX_INPUT_CHARS, height=80)
            col_r, col_x = st.columns(2)
            with col_r:
                st.button("다시 보내기", key="chat_retry_btn", type="primary", on_click=retry_chat_question, width="stretch")
            with col_x:
                st.button("보내지 않기", key="chat_drop_failed_btn", on_click=drop_failed_question, width="stretch")

    if has_user_text and not busy and not st.session_state.get("chat_proposal") and not AI_FEATURES_PAUSED:
        extract = st.button("이 대화로 사건 정리하기", key="chat_extract_btn", icon=":material/assignment:", type="secondary",
                            help="대화에서 직접 말씀하신 내용만 모아 고소장 작성 칸에 넣을 제안을 만들어요. 확인 전에는 아무것도 바뀌지 않아요.")
    if extract:
        run_chat_extraction()
    render_chat_proposal()

    with st.container(key="chat_input_row"):
        if voice_ok:
            col_input, col_mic = st.columns([12, 1])
        else:
            col_input, col_mic = st.container(), None
        with col_input:
            prompt = st.chat_input(
                "지금은 질문을 보낼 수 없어요" if AI_FEATURES_PAUSED else "답변을 만들고 있어요…" if busy else CHAT_INPUT_PLACEHOLDER,
                max_chars=CHAT_MAX_INPUT_CHARS, key="chat_input_box", disabled=busy or AI_FEATURES_PAUSED,
            )
        if col_mic is not None:
            with col_mic:
                st.button("음성 대화", key="chat_voice_open_btn", icon=":material/graphic_eq:", type="primary",
                          on_click=open_voice_mode, disabled=busy, help="음성으로 대화하기")

    if prompt and not busy:
        problem = queue_chat_question(prompt, via_voice=False)
        if problem:
            st.session_state.chat_notice = problem
        st.rerun()

    if busy:
        pending = st.session_state.chat_pending
        with history_box:
            with st.chat_message("user"):
                st.markdown(("🎤 " if pending.get("voice") else "") + pending["text"])
            with st.chat_message("assistant"):
                with st.spinner("답변을 만들고 있어요…"):
                    answer_chat_question()
        st.rerun()


def render_chat_voice() -> None:
    with st.container(key="chat_menu_row"):
        col_title, col_back = st.columns([3, 2])
        with col_title:
            st.markdown("**음성으로 대화하기**" + ("" if chat_v2_public() else "  \n:gray[🧪 관리자 시험]"))
        with col_back:
            st.button("글 채팅으로", key="chat_back_to_text", icon=":material/keyboard:", on_click=close_voice_mode, width="stretch")

    result = _voice_chat_component(
        key="chat_voice_panel",
        data={
            "reply": st.session_state.get("chat_voice_reply"),
            "notice": st.session_state.get("chat_voice_notice"),
            "preview": st.session_state.get("chat_voice_preview"),
            "max_seconds": VOICE_TURN_MAX_SECONDS,
            "preview_text": VOICE_PREVIEW_TEXT,
            "server_voices": [{"id": k, "label": v} for k, v in SERVER_VOICES.items()] if server_tts_available() else [],
            "default_voice": SERVER_DEFAULT_VOICE if server_tts_available() else "",
            "default_rate": DEFAULT_SPEECH_RATE,
        },
        on_utterance_change=lambda: None,
        on_end_change=lambda: None,
        on_tts_change=lambda: None,
        on_preview_change=lambda: None,
    )
    if getattr(result, "end", None):
        close_voice_mode()
        st.rerun()
    payload = getattr(result, "utterance", None)
    if payload:
        handle_voice_turn(payload)
        del payload
        st.rerun()
    if getattr(result, "tts", None):
        handle_tts_retry(result.tts)
        st.rerun()
    if getattr(result, "preview", None):
        handle_voice_preview(result.preview)
        st.rerun()

    messages = chat_messages()
    with st.expander(f"인식된 글과 답변 보기 ({len(messages) // 2})"):
        if not messages:
            st.caption("아직 대화가 없어요.")
        for message in messages:
            who = "나" if message["role"] == "user" else "AI"
            st.markdown(f"**{who}** {'🎤 ' if message.get('voice') else ''}{message['content']}")
    if st.session_state.get("chat_failed"):
        st.caption("답변을 못 만든 말씀은 '글 채팅으로'에서 고쳐 다시 보낼 수 있어요.")
    with st.expander("마이크와 음성은 어떻게 처리되나요?"):
        render_chat_details()


def render_chat_v2() -> None:
    st.markdown(CHAT_CSS, unsafe_allow_html=True)
    voice_ok = voice_chat_available() and not AI_FEATURES_PAUSED   # AI 점검 중에는 음성 대화를 열지 않는다
    if voice_ok and st.session_state.get("chat_mode") == "voice":
        render_chat_voice()
    else:
        render_chat_text(voice_ok)


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
            "고소미에서 로그아웃해도 네이버 로그인은 유지되므로, 다른 네이버 계정을 쓰려면 "
            "'다른 네이버 계정으로 로그인'을 눌러 주세요."
        )


def render_google_age_gate():
    st.markdown(
        age_text("**만 14세 이상 확인**\n\n"
                 "고소미는 만 14세 이상만 이용할 수 있습니다. "
                 "Google 계정으로 처음 이용하시는 경우 아래 확인이 필요합니다.")
    )
    google_age_consent = st.checkbox(age_text("[필수] 만 14세 이상입니다."), key="google_age_consent")
    if st.button("확인하고 계속하기", key="google_age_submit"):
        if not google_age_consent:
            st.error(age_text("만 14세 이상인 경우에만 서비스를 이용할 수 있습니다."))
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
            age_text("**만 14세 이상 확인**\n\n"
                     "고소미는 만 14세 이상만 이용할 수 있습니다. "
                     "네이버 계정으로 처음 이용하시는 경우 아래 확인이 필요합니다.")
        )
        naver_age_consent = st.checkbox(age_text("[필수] 만 14세 이상입니다."), key="naver_age_consent")
        if st.button("확인하고 계속하기", key="naver_age_submit"):
            if not naver_age_consent:
                st.error(age_text("만 14세 이상인 경우에만 서비스를 이용할 수 있습니다."))
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
    page_title="고소미 | AI 법률정보 도우미",
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
        login_tab, signup_tab = st.tabs(["로그인", "회원가입"])

        with login_tab:
            st.caption("가입할 때 쓴 방법(이메일·네이버·Google)으로 로그인해 주세요. 로그인하면 이 브라우저에서 최대 7일 동안 로그인이 유지됩니다.")
            with st.form("login_form"):
                login_email = st.text_input("이메일", key="login_email")
                login_password = st.text_input("비밀번호", type="password", key="login_password")
                login_submitted = st.form_submit_button("로그인", type="primary", width="stretch")

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
            if not EMAIL_SIGNUP_ENABLED:
                st.info("이메일 가입은 인증 메일 준비 중이라 잠시 쉬고 있어요. 아래 네이버 또는 Google로 가입해 주세요. "
                        "이미 이메일로 가입하신 분은 로그인 탭에서 그대로 로그인할 수 있어요.")
                render_social_login_section("signup")
            else:
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
                    signup_age_consent = st.checkbox(age_text("[필수] 만 14세 이상입니다."), key="signup_age_consent")
                    signup_submitted = st.form_submit_button("회원가입", type="primary", width="stretch")

                if signup_submitted:
                    if not privacy_config_ready() or not terms_config_ready():
                        st.error(
                            "현재 서비스 운영 정보 설정이 진행 중이라 잠시 회원가입을 "
                            "이용하실 수 없습니다. 잠시 후 다시 시도해 주세요."
                        )
                    elif not (signup_privacy_consent and signup_terms_consent and signup_age_consent):
                        st.error(age_text("회원가입을 진행하려면 이용약관 동의, 개인정보 수집·이용 동의, 만 14세 이상 확인에 모두 체크해 주세요."))
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

        col_privacy, col_terms = st.columns(2)
        with col_privacy:
            if st.button("개인정보처리방침 보기", key="privacy_policy_view_btn_login", type="tertiary"):
                render_privacy_policy_dialog()
        with col_terms:
            if st.button("이용약관 보기", key="terms_of_service_view_btn_login", type="tertiary"):
                render_terms_of_service_dialog()

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
if not consent_status and MIN_USER_AGE == 18 and st.session_state.get("under_age_declared"):
    # 만 18세 미만이라고 한 경우: 서비스는 쓸 수 없지만 계정·자료는 지우지 않는다. 직접 탈퇴만 안내한다.
    render_brand_header()
    st.markdown("### 만 18세 이상만 이용할 수 있어요")
    st.markdown(
        "고소미의 AI 기능에 쓰는 Google Gemini의 이용 조건 때문에 만 18세 미만은 이용할 수 없게 되었습니다.\n\n"
        "- 계정과 저장한 사건·자료는 **운영자가 임의로 지우지 않습니다.**\n"
        "- 모두 지우려면 아래 '회원탈퇴'를 눌러 주세요. 지울 내용을 한 번 더 보여 드려요.\n"
        "- 잘못 누르셨다면 '다시 확인하기'를 눌러 주세요."
        + (f"\n- 문의: {md_escape(PRIVACY_CONTACT_EMAIL)}" if PRIVACY_CONTACT_EMAIL else "")
    )
    col_back, col_out = st.columns(2)
    with col_back:
        if st.button("다시 확인하기", key="under_age_back", width="stretch"):
            st.session_state.under_age_declared = False
            st.rerun()
    with col_out:
        if st.button("로그아웃", key="under_age_logout", width="stretch"):
            perform_logout()
    if st.button("회원탈퇴", key="delete_account_main", type="secondary", icon=":material/person_remove:"):
        render_delete_account_dialog()
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
    if MIN_USER_AGE == 18:
        # 이 기준 시행 전에 가입한 이용자도 여기서 만 18세 이상인지 다시 확인한다(새 약관 동의와 함께 기록).
        reconsent_age = st.checkbox("[필수] 만 18세 이상입니다.", key="reconsent_age")
    else:
        reconsent_age = True
    if st.button("동의하고 계속하기", key="reconsent_submit"):
        if reconsent_terms and reconsent_privacy and reconsent_age:
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
            st.error("계속하려면 이용약관과 개인정보 수집·이용 두 곳에 모두 체크해 주세요."
                     if MIN_USER_AGE != 18 else "계속하려면 이용약관, 개인정보 수집·이용, 만 18세 이상 확인에 모두 체크해 주세요.")
    if MIN_USER_AGE == 18 and st.button("만 18세 미만입니다", key="reconsent_under_age"):
        st.session_state.under_age_declared = True
        st.rerun()
    if st.button("동의하지 않고 로그아웃", key="reconsent_logout_btn"):
        perform_logout()
    render_footer()
    st.stop()

# ── 화면 구성: 처음 · AI에게 질문 · 고소장 작성 · 내 사건 · 도움말 ─────────────────
# 탭 대신 한 번에 한 화면만 그린다. 다른 화면으로 옮겨도 적던 내용이 사라지지 않게 입력칸 값은 keep_widget_values()로 붙잡아 둔다.
_scroll_top_component = st.components.v2.component(
    "gosoemi_scroll_top",
    js="""
export default function() {
  const top = () => {
    const main = document.querySelector('[data-testid="stMain"]');
    if (main) main.scrollTo({top: 0});
    window.scrollTo(0, 0);
  };
  top();
  requestAnimationFrame(top);
}
""",
)
PAGES = {"home": "처음", "chat": "AI에게 질문", "case": "고소장 작성", "cases": "내 사건", "help": "도움말"}


def apply_nav(action: dict) -> None:
    if action.get("close_case"):
        st.session_state.open_case_id = None
    if action.get("reset_case"):
        reset_case_form()
    if action.get("page"):
        st.session_state.nav_page = action["page"]


def request_nav(action: dict) -> None:
    """다른 화면으로 옮긴다. 바로 전 화면에 저장하지 않은 작업이 있으면 옮기지 않고 어떻게 할지 묻는다."""
    unsaved = st.session_state.get("_unsaved")
    if unsaved:
        st.session_state._pending_nav = {"action": action, "ctx": unsaved}
        return
    apply_nav(action)


def go_to(page: str) -> None:
    request_nav({"page": page})


def on_nav_change() -> None:
    choice = st.session_state.get("nav_choice")
    if choice:  # 이미 고른 칸을 다시 눌러 선택이 풀린 경우에는 화면을 그대로 둔다
        request_nav({"page": choice})


@st.dialog("저장하지 않은 내용이 있어요", dismissible=False)
def render_leave_dialog() -> None:
    pending = st.session_state.get("_pending_nav") or {}
    ctx = pending.get("ctx") or {}
    st.markdown("이 사건에서 고친 내용을 아직 저장하지 않았어요. 어떻게 할까요?")
    pending_kind = ws_form_pending(ctx["case_id"], ws_state(ctx["case_id"])) if ctx else None
    if pending_kind:
        st.caption(f"작성 중인 {ITEM_NAMES[pending_kind]} 항목은 '저장하고 이동'을 누르면 목록에 넣고 함께 저장해요.")
    if st.button("저장하고 이동", key="leave_save", type="primary", width="stretch", icon=":material/save:"):
        with st.spinner("저장하고 있어요…"):
            ws_save(ctx["case"], ctx["case_id"], ctx["draft_key"], ctx["editor_key"])
        ws = ws_state(ctx["case_id"])
        st.session_state._pending_nav = None
        if ws["save_error"] or ws["conflict"] or ws.get("pending_error"):
            # 저장하지 못했으면 옮기지 않는다. 화면 아래 저장 영역에 이유와 다시 하는 방법이 보인다.
            ws["save_error"] = ws["save_error"] and "저장하지 못해 이동하지 않았어요. " + ws["save_error"]
        else:
            apply_nav(pending["action"])
        st.rerun()
    if st.button("저장하지 않고 이동", key="leave_discard", width="stretch"):
        st.session_state._pending_nav = None
        apply_nav(pending["action"])
        st.rerun()
    if st.button("계속 수정", key="leave_stay", type="tertiary", width="stretch"):
        st.session_state._pending_nav = None
        st.rerun()
    st.caption("저장하지 않고 이동해도 고친 내용은 이 탭에 잠시 남아 있어 다시 열면 이어서 고칠 수 있어요. "
               "하지만 새로고침하거나 탭을 닫거나 로그아웃하면 사라져요.")


_scroll_to_component = st.components.v2.component(
    "gosoemi_scroll_to",
    js="""
export default function({data}) {
  const go = () => {
    const el = document.querySelector(data && data.selector);
    if (!el) return;
    el.scrollIntoView({block: 'center'});
    const fields = [...el.querySelectorAll('input[type="text"], textarea')];
    const field = fields.find(f => !f.value) || fields[0];  // 비어 있는 칸(빠진 칸)부터
    if (field) field.focus({preventScroll: true});
  };
  setTimeout(go, 350);
}
""",
)


# 저장하지 않은 작업이 있을 때 새로고침·탭 닫기를 하면 브라우저가 확인 창을 띄우게 한다.
# 확인 창의 문구는 브라우저가 정하며, 휴대폰(특히 iPhone Safari)에서 앱 전환·탭 정리로 닫힐 때는 뜨지 않을 수 있다.
_unsaved_guard_component = st.components.v2.component(
    "gosoemi_unsaved_guard",
    js="""
export default function({data}) {
  window.__gosoemiUnsaved = !!(data && data.dirty);
  if (!window.__gosoemiGuard) {
    window.__gosoemiGuard = (event) => {
      if (window.__gosoemiUnsaved) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', window.__gosoemiGuard);
  }
}
""",
)


def keep_widget_values() -> None:
    """화면에 없는 입력칸의 값은 Streamlit이 지우므로, 적던 사건 내용·초안 편집·체크리스트 값을 다시 적어 둔다."""
    for key in list(st.session_state.keys()):
        if (
            key.startswith(("case_field_", "prog_", "ws_f_"))
            or key in ("case_type_choice", "case_type_custom", "case_data_consent", "case_draft_editor", "chat_retry_text")
            or re.fullmatch(r"list_draft_editor_[0-9A-Za-z-]+", key)
        ):
            st.session_state[key] = st.session_state[key]


def render_account_page(identity: str, method: str) -> None:
    """내 계정: 로그아웃·방침·약관·문의·탈퇴. 확인 창이 다른 메뉴에 가리지 않도록 별도 화면으로 둔다."""
    st.markdown("## 내 계정")
    st.markdown(f"{identity} ({method} 로그인)")
    if st.button("로그아웃", key="logout_btn_main", type="primary", icon=":material/logout:"):
        perform_logout()
    st.caption("공용 기기라면 사용 후 꼭 로그아웃해 주세요. 다른 계정을 쓰려면 로그아웃한 뒤 다시 로그인해 주세요.")
    st.markdown("### 약관과 개인정보")
    col_privacy, col_terms = st.columns(2)
    with col_privacy:
        if st.button("개인정보처리방침 보기", key="privacy_policy_view_btn_main", width="stretch", type="secondary"):
            render_privacy_policy_dialog()
    with col_terms:
        if st.button("이용약관 보기", key="terms_of_service_view_btn_main", width="stretch", type="secondary"):
            render_terms_of_service_dialog()
    if PRIVACY_CONTACT_EMAIL:
        st.markdown(f"문의: {md_escape(PRIVACY_CONTACT_EMAIL)} (서비스 이용·개인정보)")
    if not sales_open(BILLING_MODE, BILLING_PLANS):
        st.caption("지금은 모든 기능이 무료예요. 결제를 요청하지 않아요.")
    st.markdown("### 회원탈퇴")
    st.markdown("탈퇴하면 계정과 저장한 사건" + (", 초안" if draft_save_available() else "")
                + (", 증거파일" if EVIDENCE_UPLOAD_ENABLED else "") + "이 모두 삭제돼요. 누르면 삭제할 내용을 한 번 더 보여 드려요.")
    if st.button("회원탈퇴", key="delete_account_main", type="secondary", icon=":material/person_remove:"):
        render_delete_account_dialog()
    if is_admin():
        st.markdown("### 운영자")
        render_admin_diagnostics()


def render_top_bar(identity: str, method: str) -> str:
    with st.container(key="top_bar"):
        col_logo, col_account = st.columns([3, 2], vertical_alignment="center")
        with col_logo:
            st.button("고소미", key="brand_home_btn", icon=":material/balance:", type="tertiary", on_click=go_to, args=("home",),
                      help="처음 화면으로 가기")
        with col_account:
            st.button("내 계정", key="account_btn", icon=":material/account_circle:", type="secondary",
                      on_click=go_to, args=("account",))
    # 지금 화면은 nav_page(일반 값)에 두고, 위쪽 이동 막대(nav_choice)는 그 값을 보여 주기만 한다.
    page = st.session_state.get("nav_page") or "home"
    if page not in PAGES and page != "account":
        page = "home"
    st.session_state.nav_page = page
    if page == "home":
        return page  # 처음 화면에서는 이동 막대 없이 카드 세 개로 고른다
    st.session_state.nav_choice = page if page in PAGES else None
    st.segmented_control(
        "화면 이동", list(PAGES), format_func=PAGES.get, key="nav_choice", label_visibility="collapsed",
        width="stretch", on_change=on_nav_change,
    )
    return page


def render_home_page() -> None:
    st.markdown("## 무엇을 도와드릴까요?")
    choices = [
        ("chat", "AI에게 질문하기", "AI 기능을 점검 중입니다." if AI_FEATURES_PAUSED
         else "궁금한 점을 글이나 말로 물어보세요." if voice_chat_available() else "궁금한 점을 글로 물어보세요.",
         ":material/chat:"),
        ("case", "고소장 작성하기", "사건 내용을 정리해 저장해요. (초안 만들기는 점검 중)" if AI_FEATURES_PAUSED
         else "사건 내용을 바탕으로 초안을 만들어요.", ":material/edit_document:"),
        ("cases", "내 사건 보기", "저장한 사건을 확인하세요.", ":material/folder_open:"),
    ]
    columns = st.columns(3)
    for column, (page, title, desc, icon) in zip(columns, choices):
        with column:
            with st.container(border=True, key=f"home_card_{page}"):
                st.markdown(f"### {title}")
                st.markdown(desc)
                st.button(title, key=f"home_go_{page}", icon=icon, type="primary", width="stretch",
                          on_click=go_to, args=(page,))
    st.button("법률 용어·피해별 안내 보기", key="home_go_help", icon=":material/menu_book:", type="secondary",
              on_click=go_to, args=("help",))


def render_steps(current: int) -> None:
    names = ["내용 적기", "확인하기", "저장" if AI_FEATURES_PAUSED else "정리·초안" if assist_preview() else "초안 받기"]
    items = []
    for i, name in enumerate(names, 1):
        state = "now" if i == current else ("done" if i < current else "")
        current_attr = ' aria-current="step"' if i == current else ""
        items.append(f'<div class="dgo-step {state}" role="listitem"{current_attr}><span>{i}</span>{name}</div>')
    items = "".join(items)
    st.markdown(f'<div class="dgo-steps" role="list" aria-label="고소장 작성 단계">{items}</div>', unsafe_allow_html=True)


def render_case_type_help(case_type: str) -> None:
    """사건 작성 화면용 짧은 유형 안내. 자세한 내용은 펼쳐 보기 안에 둔다."""
    guide = damage_type_guide(case_type)
    preview = assist_preview()
    if guide.get("urgent"):
        st.error(guide["urgent"])
    route = ROUTES.get(guide.get("route"))
    if route:
        tag = f"[{ROUTE_TAGS[guide['route']]}] " if preview and guide.get("route") in ROUTE_TAGS else ""
        st.info(f"**{tag}{route['label']}**  \n{route['note']}")
    with st.expander("이 유형 작성 도움말 (준비할 자료·자주 묻는 질문)"):
        if guide.get("route_detail"):
            st.markdown(guide["route_detail"])
        st.markdown("**사건 경위에 함께 적으면 좋은 내용**\n" + "\n".join(f"- {item}" for item in guide["checklist"]))
        st.markdown("**모아 두면 좋은 자료**\n" + "\n".join(f"- {item}" for item in guide["evidence_tips"]))
        qa = type_faq(case_type) if preview else guide.get("qa", [])
        if qa:
            st.markdown("**자주 묻는 질문**")
            for question, answer in qa:
                st.markdown(f"**Q. {question}**\n\n{answer}")
        if guide.get("sources"):
            st.markdown("공식 안내: " + " · ".join(f"[{label}]({url})" for label, url in guide["sources"]))
    if preview:
        render_legal_terms("case")


def start_from_chat(voice: bool) -> None:
    st.session_state.nav_page = "chat"
    st.session_state.chat_mode = "voice" if voice else "text"


def render_case_form() -> None:
    if assist_preview() and not AI_FEATURES_PAUSED:
        with st.container(key="case_start_choice"):
            st.markdown("**어떻게 시작할까요?** 이야기로 시작하면 대화 내용을 이 칸에 옮길 수 있어요(옮기기 전에 확인해요).")
            options = [("AI와 글로 이야기하기", ":material/chat:", False)]
            if voice_chat_available():
                options.append(("AI와 말로 이야기하기", ":material/graphic_eq:", True))
            cols = st.columns(len(options))
            for col, (label, icon, voice) in zip(cols, options):
                with col:
                    st.button(label, key=f"case_start_{'voice' if voice else 'text'}", icon=icon, width="stretch",
                              on_click=start_from_chat, args=(voice,))
            st.caption("직접 적으려면 아래 칸을 채워 주세요.")
    if voice_input_available() and not AI_FEATURES_PAUSED:
        render_voice_input()
    st.markdown("### 1. 어떤 피해인가요?")
    type_options = list(DAMAGE_TYPES) + [OTHER_TYPE]
    case_type_choice = st.selectbox(
        "피해 유형 (필수)", type_options, index=None, placeholder="눌러서 고르기", key="case_type_choice",
        help="고르면 그 유형에 맞는 작성 예시가 입력칸에 나타나요. 맞는 유형이 없으면 '기타'를 고르세요.",
    )
    case_type_custom = ""
    if case_type_choice == OTHER_TYPE:
        case_type_custom = st.text_input(
            "피해 유형 직접 적기 (필수)", max_chars=CASE_FIELD_MAX_CHARS["case_type"],
            placeholder="예: 렌터카 보증금 미반환", key="case_type_custom",
        )
    if case_type_choice:
        render_case_type_help(case_type_choice)

    guide_type = case_type_choice or ""
    field_values = {}

    def field(key: str, required: bool) -> None:
        label = field_label(key) + (" (필수)" if required else "")
        widget = st.text_area if key in CASE_LONG_TEXT_FIELDS else st.text_input
        field_values[key] = widget(
            label, max_chars=CASE_FIELD_MAX_CHARS[key], placeholder=field_placeholder(key, guide_type),
            help=FIELD_GUIDES[key]["help"], key=f"case_field_{key}",
        )
        if key == "suspect_info":
            with st.expander("상대방 이름이나 연락처를 모르면?"):
                st.markdown(SUSPECT_UNKNOWN_GUIDE)

    # 폼(st.form)을 쓰지 않는다: 폼 안에 적은 글은 제출 전까지 앱이 볼 수 없어,
    # 음성 입력이 기존 내용을 덮어쓰는지 확인할 수 없기 때문이다. 각 칸은 칸을 벗어날 때 저장된다.
    st.markdown("### 2. 꼭 적어 주세요")
    for key in ("reporter_info", "incident_description"):
        field(key, True)
    st.markdown("### 3. 아는 만큼 적어 주세요 (선택)")
    st.caption("모르는 칸은 비워 두셔도 돼요. 초안에 '[확인 필요]'로 표시돼요.")
    for _, key in CASE_FIELDS:
        if key not in ("case_type", "reporter_info", "incident_description"):
            field(key, False)

    st.markdown("### 4. 저장 동의")
    st.markdown(
        "입력한 내용은 내 계정에 비공개로 저장되고, 초안을 만들 때 Google Gemini로 전송돼요. "
        "주민등록번호·계좌 비밀번호·카드번호·인증번호는 적지 마세요."
    )
    with st.expander("저장되는 정보 자세히 보기"):
        st.markdown(
            "- 저장 항목: 사건 유형, 신고인 정보, 상대방 정보, 사건 발생 일시, 사건 발생 장소, "
            "사건 경위, 피해 내용, 증거자료, 목격자 유무, 처벌 의사 (총 10개 항목)\n"
            "- 목적: 사건 저장·관리, AI 상담 및 고소장 초안 생성\n"
            "- 보관기간: 이용자가 직접 삭제하거나 회원탈퇴할 때까지\n"
            "- 동의를 거부할 수 있으나, 거부 시 사건 저장과 고소장 초안 생성이 제한됩니다.\n"
            "- 사건 정보(신고인·상대방 정보 등 개인정보 포함 가능)는 Supabase 데이터베이스에 저장되고, "
            "고소장 초안을 만들 때 Google Gemini API로 전송되어 처리됩니다. 상대방 등 제3자의 정보는 필요한 최소한만 적어 주세요."
            + (
                "\n- 고소장 초안: 처음 만든 초안과 '초안 저장'으로 저장한 초안은 사건과 함께 보관되며, "
                "사건을 삭제하거나 회원탈퇴하면 함께 삭제됩니다."
                if draft_save_available() else ""
            )
        )
    case_data_consent = st.checkbox("[필수] 위 내용을 확인했고, 사건 정보 저장·전송에 동의합니다.", key="case_data_consent")
    if any((st.session_state.get(f"case_field_{key}") or "").strip() for _, key in CASE_FIELDS):
        # 적던 내용은 다른 화면에 다녀와도 남지만, 새로고침·탭 닫기 때는 사라지므로 브라우저 확인 창을 띄운다.
        st.session_state["_unsaved_form"] = True
        st.caption("적은 내용은 '입력한 내용 확인하기' → '"
                   + ("사건 저장하기" if AI_FEATURES_PAUSED else "저장하고 초안 만들기")
                   + "'를 눌러야 저장돼요. 새로고침하거나 탭을 닫으면 사라져요.")
    submitted = st.button("입력한 내용 확인하기", type="primary", key="case_form_submit", width="stretch",
                          icon=":material/fact_check:")

    if submitted:
        field_values["case_type"] = case_type_custom.strip() if case_type_choice == OTHER_TYPE else (case_type_choice or "")
        errors = []
        if not case_type_choice:
            errors.append("1번에서 피해 유형을 골라 주세요.")
        elif case_type_choice == OTHER_TYPE and not field_values["case_type"]:
            errors.append("'피해 유형 직접 적기' 칸에 피해 유형을 적어 주세요.")
        # 피해 유형 누락은 위에서 더 구체적으로 안내했으므로 나머지 항목만 검사한다.
        errors += validate_case_values({**field_values, "case_type": field_values["case_type"] or "-"})
        if not privacy_config_ready():
            st.error("지금은 서비스 운영 정보를 설정하는 중이라 사건을 저장할 수 없습니다. 잠시 후 다시 시도해 주세요." + contact_hint())
        elif not case_data_consent:
            st.error("저장하려면 '저장 동의'에 체크해 주세요.")
        elif errors:
            for message in dict.fromkeys(errors):
                st.error(message)
        else:
            st.session_state.case_review = {"case_id": str(uuid.uuid4()), "values": normalize_case_values(field_values)}
            st.session_state.case_notice = None
            st.session_state.review_skipped = []
            st.rerun()


def review_answer(key: str, unknown: bool, value: str = "") -> None:
    """저장 전 확인 단계에서 빠진 칸을 채운다. 입력칸(case_field_)과 확인 내용을 같이 바꾼다."""
    review = st.session_state.get("case_review")
    value = UNKNOWN if unknown else (value or st.session_state.get(f"review_ans_{key}") or "").strip()
    if not review or not value:
        st.session_state.review_notice = "칸에 적거나 '모름으로 진행'을 눌러 주세요."
        return
    value = value[: CASE_FIELD_MAX_CHARS[key]]
    st.session_state[f"case_field_{key}"] = value
    review["values"][key] = value
    st.session_state.pop(f"review_ans_{key}", None)
    st.session_state.pop("review_notice", None)


def review_skip(key: str) -> None:
    st.session_state.setdefault("review_skipped", []).append(key)


def render_review_questions(review: dict) -> None:
    values = review["values"]
    for note in inconsistencies(values):
        st.warning(note)
    skipped = st.session_state.get("review_skipped") or []
    questions = [q for q in missing_questions(values) if q[0] not in skipped]
    if not questions:
        return
    key, question, hint = questions[0]
    with st.container(border=True, key="review_question_box"):
        st.markdown(f"**빠진 정보 {len(questions)}개 · {question}**")
        st.caption(hint)
        notice = st.session_state.pop("review_notice", None)
        if notice:
            st.warning(notice)
        suggestion = suggestion_for(values, key)
        if suggestion:
            st.button(f"사건 경위에 적은 '{suggestion}' 넣기", key=f"review_suggest_{key}", type="secondary",
                      on_click=review_answer, args=(key, False, suggestion))
        st.text_input(question, key=f"review_ans_{key}", label_visibility="collapsed", max_chars=CASE_FIELD_MAX_CHARS[key])
        cols = st.columns(3)
        with cols[0]:
            st.button("넣기", key=f"review_put_{key}", type="primary", width="stretch", on_click=review_answer, args=(key, False))
        with cols[1]:
            st.button("모름으로 진행", key=f"review_unknown_{key}", width="stretch", on_click=review_answer, args=(key, True))
        with cols[2]:
            st.button("나중에", key=f"review_skip_{key}", width="stretch", type="tertiary", on_click=review_skip, args=(key,))


def render_case_review(review: dict) -> None:
    st.session_state["_unsaved_form"] = True  # 아직 저장 전(새로고침 경고)
    st.markdown("### 입력한 내용을 확인해 주세요")
    if assist_preview():
        render_review_questions(review)
    st.caption(("이대로 사건을 저장해요(초안 만들기는 AI 점검이 끝난 뒤). " if AI_FEATURES_PAUSED else "이대로 저장하고 초안을 만들어요. ")
               + "고칠 곳이 있으면 '고치러 가기'를 눌러 주세요. 적은 내용은 그대로 남아 있어요.")
    with st.container(border=True):
        for label, key in CASE_FIELDS:
            value = review["values"][key]
            shown = "_비어 있음 — 초안에 [확인 필요]로 표시_" if is_missing(value) else md_escape(value)
            st.markdown(f"**{label}**  \n{shown}")
    col_confirm, col_back = st.columns(2)
    with col_confirm:
        confirm_clicked = st.button("사건 저장하기" if AI_FEATURES_PAUSED else "저장하고 초안 만들기", key="case_review_confirm",
                                    type="primary", width="stretch",
                                    icon=":material/auto_awesome:")
    with col_back:
        if st.button("고치러 가기", key="case_review_cancel", width="stretch", type="secondary", icon=":material/edit:"):
            st.session_state.case_review = None
            st.rerun()
    if not confirm_clicked:
        return

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
        st.session_state.case_notice = "방금 저장한 사건과 같은 내용이라 다시 저장하지 않았어요. 아래 초안을 확인해 주세요."
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
            with st.spinner("사건을 저장하고 있어요."):
                save_case(new_entry)
        except Exception:
            st.error(
                "사건을 저장하지 못했어요. 적은 내용은 그대로 있으니 인터넷 연결을 확인하고 "
                "'저장하고 초안 만들기'를 다시 눌러 주세요. 다시 눌러도 같은 사건이 두 번 저장되지는 않아요."
                + contact_hint()
            )
        else:
            st.session_state.last_saved_case_entry = new_entry
            st.session_state.last_saved_fingerprint = fingerprint
            st.session_state.last_saved_at = time.time()
            case_entry = new_entry

    if case_entry is None:
        return
    st.session_state.case_review = None
    already_drafted = st.session_state.case_draft_id == case_entry["case_id"] and st.session_state.case_draft
    if not already_drafted:
        try:
            with st.spinner("사건을 저장했어요. 고소장 초안을 만들고 있어요. 보통 10~30초 걸려요."):
                draft_text = generate_case_draft(case_entry)
        except Exception as error:
            st.session_state.pending_draft_entry = case_entry
            st.session_state.pending_draft_reason = ai_error_message(error, "초안을 만드는 중 문제가 생겼습니다.")
        else:
            show_new_draft(case_entry, draft_text)
            st.session_state.case_notice = (
                "사건을 저장했어요. 아래 '다음 할 일'부터 차례로 정리해 보세요."
                if assist_preview()
                else "사건과 초안을 저장했어요. 초안을 읽고 고친 뒤 '초안 저장'을 눌러 주세요."
                if draft_save_available()
                else "사건을 저장했어요. 초안을 읽고 고친 뒤 내려받아 주세요. 사건은 '내 사건'에서 다시 볼 수 있어요."
            )
    st.rerun()


def open_saved_pending_case() -> None:
    """AI 점검 중 사건을 저장한 뒤 '내 사건에서 보기': 방금 저장한 사건을 연다."""
    entry = st.session_state.get("pending_draft_entry") or {}
    st.session_state.pending_draft_entry = None
    reset_case_form()
    st.session_state.nav_page = "cases"
    st.session_state.open_case_id = entry.get("case_id")


def start_new_case_after_pending() -> None:
    st.session_state.pending_draft_entry = None
    reset_case_form()


def render_pending_draft() -> None:
    if AI_FEATURES_PAUSED:
        # AI 점검 중: 사건은 저장됐다. 누를 수 없는 '초안 다시 만들기' 대신 다음에 할 일만 보여 준다.
        st.success("사건을 저장했어요. AI 기능 점검이 끝나면 '내 사건'에서 초안을 만들 수 있어요.")
        col_open, col_new = st.columns(2)
        with col_open:
            st.button("내 사건에서 보기", key="pending_open_case", type="primary", width="stretch",
                      icon=":material/folder_open:", on_click=open_saved_pending_case)
        with col_new:
            st.button("새 사건 작성하기", key="pending_new_case", width="stretch", icon=":material/add:",
                      on_click=start_new_case_after_pending)
        return
    st.warning("사건은 저장했지만 초안을 만들지 못했어요. 잠시 후 '초안 다시 만들기'를 눌러 주세요.")
    if st.session_state.get("pending_draft_reason"):
        st.caption(st.session_state.pending_draft_reason)
    col_retry, col_cancel = st.columns(2)
    with col_retry:
        if st.button("초안 다시 만들기", key="retry_draft_button", type="primary", width="stretch", disabled=AI_FEATURES_PAUSED):
            entry = st.session_state.pending_draft_entry
            try:
                with st.spinner("고소장 초안을 만들고 있어요. 보통 10~30초 걸려요."):
                    draft_text = generate_case_draft(entry)
            except Exception as error:
                st.error(ai_error_message(error, "초안을 또 만들지 못했어요. 1~2분 뒤에 다시 눌러 주세요."))
            else:
                show_new_draft(entry, draft_text)
                st.session_state.pending_draft_entry = None
                st.rerun()
    with col_cancel:
        if st.button("나중에 하기 (내 사건에서 다시 만들 수 있어요)", key="cancel_pending_draft_button", width="stretch",
                     type="secondary"):
            st.session_state.pending_draft_entry = None
            st.rerun()


def render_case_page() -> None:
    st.markdown("## 고소장 작성하기")
    if AI_FEATURES_PAUSED:
        st.info(AI_PAUSED_MESSAGE)
        st.caption(AI_PAUSED_CASE_NOTE)
    review = st.session_state.get("case_review")
    pending = st.session_state.pending_draft_entry
    has_draft = bool(st.session_state.case_draft) and not pending
    render_steps(3 if (has_draft or pending) else (2 if review else 1))
    case_notice = st.session_state.get("case_notice")
    if case_notice:
        st.success(case_notice)
    if pending:
        render_pending_draft()
    elif has_draft:
        if assist_preview():
            render_case_workspace(st.session_state.get("case_draft_case") or {}, st.session_state.case_draft_id,
                                  "case_draft", "case_draft_editor")
        else:
            render_draft_panel(
                "case_draft_editor", st.session_state.case_draft,
                st.session_state.get("case_draft_case") or {}, st.session_state.case_draft_id,
            )
        col_new, col_list = st.columns(2)
        with col_new:
            st.button("새 사건 작성하기", key="case_new_button", on_click=request_nav, args=({"reset_case": True},), width="stretch",
                      type="secondary", icon=":material/add:")
        with col_list:
            st.button("내 사건 목록 보기", key="case_go_list_button", on_click=go_to, args=("cases",), width="stretch",
                      type="secondary", icon=":material/folder_open:")
    elif review:
        render_case_review(review)
    else:
        render_case_form()


def open_case(case_id: str) -> None:
    st.session_state.open_case_id = case_id


def close_case() -> None:
    request_nav({"close_case": True})


# ── 사건 작업 공간(관리자 미리보기): 빠진 정보 → 경위·증거 → 초안 → 내려받기 → 다음 할 일 ──────────
# CASE_WORKSPACE_SAVE_ENABLED가 켜져 있으면 '작업 저장'으로 case_workspace·cases·case_drafts에 저장한다
# (migrations/20261012000000, 운영 미적용). 꺼져 있으면 이 탭의 서버 메모리에만 있는 미리보기다.
WS_STEPS = {"check": "빠진 정보", "story": "경위·증거", "draft": "초안", "download": "내려받기", "next": "다음 할 일"}


def ws_state(case_id: str) -> dict:
    return st.session_state.setdefault(f"ws_{case_id}", {
        "answers": {}, "timeline": None, "evidence": None, "file_names": [],
        "history": [], "schedules": [], "notice": None,
        # 경위·증거·일정 목록 편집: editing(열린 입력칸)·confirm_del(지울지 묻는 항목)·item_notice·item_error
        "editing": None, "confirm_del": None, "item_notice": None, "item_error": None,
        # 저장: loaded(불러왔는지)·version(작업 공간)·case_version(사건)·snapshot(마지막 저장 내용)·saved_at·conflict·save_error
        "loaded": False, "version": None, "case_version": None, "snapshot": None, "saved_at": None,
        "conflict": None, "save_error": None, "saved_ok": None,
    })


def ws_case(case: dict, ws: dict) -> dict:
    """이 화면에서 채운 답을 더한 사건 내용(저장된 사건은 바꾸지 않는다)."""
    return {**case, **ws["answers"]}


def ws_go(case_id: str, step: str) -> None:
    st.session_state[f"ws_step_{case_id}"] = step


def ws_on_nav(case_id: str) -> None:
    choice = st.session_state.get(f"ws_nav_{case_id}")
    if choice:
        st.session_state[f"ws_step_{case_id}"] = choice


def ws_answer(case_id: str, key: str, unknown: bool, value: str = "") -> None:
    ws = ws_state(case_id)
    value = UNKNOWN if unknown else (value or st.session_state.get(f"ws_ans_{case_id}_{key}") or "").strip()
    if not value:
        ws["notice"] = "칸에 적거나 '모름으로 진행'을 눌러 주세요."
        return
    ws["answers"][key] = value[: CASE_FIELD_MAX_CHARS[key]]
    ws["notice"] = None
    st.session_state.pop(f"ws_ans_{case_id}_{key}", None)
    st.session_state[f"ws_edit_{case_id}_{key}"] = ws["answers"][key]


def render_ws_check(case_id: str, case: dict, ws: dict) -> None:
    issues = inconsistencies(case)
    questions = missing_questions(case)
    if ws.get("notice"):
        st.warning(ws["notice"])
    for note in issues:
        st.warning(note)
    if questions:
        key, question, hint = questions[0]
        st.markdown(f"#### {question}")
        st.caption(hint + f" (남은 질문 {len(questions)}개)")
        suggestion = suggestion_for(case, key)
        if suggestion:
            st.button(f"사건 경위에 적은 '{suggestion}' 넣기", key=f"ws_suggest_{case_id}_{key}", type="secondary",
                      on_click=ws_answer, args=(case_id, key, False, suggestion))
        st.text_input(question, key=f"ws_ans_{case_id}_{key}", label_visibility="collapsed",
                      max_chars=CASE_FIELD_MAX_CHARS[key], placeholder=field_placeholder(key, case.get("case_type") or ""))
        col_put, col_unknown = st.columns(2)
        with col_put:
            st.button("넣기", key=f"ws_put_{case_id}_{key}", type="primary", width="stretch", on_click=ws_answer,
                      args=(case_id, key, False))
        with col_unknown:
            st.button("모름으로 진행", key=f"ws_unknown_{case_id}_{key}", width="stretch", on_click=ws_answer,
                      args=(case_id, key, True))
    elif not issues:
        st.success("비어 있는 칸은 없어요. 그래도 내용이 맞는지는 직접 확인해 주세요.")
        st.button("다음: 경위·증거 정리", key=f"ws_to_story_{case_id}", type="primary", on_click=ws_go, args=(case_id, "story"))
    unknown = unknown_items(case)
    if unknown:
        st.caption("'모름'으로 진행한 항목: " + ", ".join(unknown) + " — 초안에 [확인 필요]로 남아요.")
    if ws["answers"]:
        with st.expander(f"여기서 채운 내용 {len(ws['answers'])}개"):
            for key, value in ws["answers"].items():
                st.markdown(f"**{CASE_FIELD_LABELS[key]}**: {md_escape(value)}")
            st.caption("'작업 저장'을 누르면 저장한 사건 내용에 반영돼요." if workspace_save_available()
                       else "여기서 채운 내용은 이 화면의 초안·내려받기 자료에만 쓰여요. 저장한 사건 내용은 바뀌지 않아요.")


NO_LINK = "연결 안 함"  # 관련 자료를 고르지 않았을 때 보이는 값(저장할 때는 빈 값)
ITEM_NAMES = {"timeline": "사건 경위", "evidence": "증거 설명", "schedules": "일정"}


def ws_items(case: dict, ws: dict, kind: str) -> list:
    """경위·증거 목록. 아직 고친 적이 없으면 적은 사건 내용과 첨부 파일 이름으로 처음 목록을 만든다."""
    if ws.get(kind) is None:
        ws[kind] = build_timeline(case) if kind == "timeline" else evidence_items(case, ws.get("file_names"))
    return ws[kind]


def ws_saved_hint() -> str:
    return ("화면 아래 '작업 저장'을 눌러야 저장돼요." if workspace_save_available()
            else "미리보기라 저장되지 않아요. 새로고침하면 사라져요.")


def ws_form_key(case_id: str, kind: str, field: str) -> str:
    return f"ws_f_{case_id}_{kind}_{field}"


def ws_item_open(case_id: str, kind: str, index) -> None:
    """추가(index=None) 또는 수정할 항목의 입력칸을 연다. 수정이면 지금 값을 채워 둔다."""
    ws = ws_state(case_id)
    ws["editing"] = {"kind": kind, "index": index}
    ws["item_error"] = ws["item_notice"] = ws["confirm_del"] = None
    item = ws[kind][index] if index is not None else {}
    k = lambda field: ws_form_key(case_id, kind, field)  # noqa: E731
    if kind == "timeline":
        st.session_state[k("when")] = item.get("when", "")
        st.session_state[k("what")] = item.get("what", "")
        st.session_state[k("evidence")] = item.get("evidence") or NO_LINK
    elif kind == "evidence":
        st.session_state[k("name")] = item.get("name", "")
        st.session_state[k("shows")] = item.get("shows", "")
    else:
        st.session_state[k("kind")] = item.get("kind", "submit")
        st.session_state[k("title")] = item.get("title", "")
        st.session_state[k("date")] = item.get("date") or datetime.date.today()
        st.session_state[k("has_time")] = bool(item.get("time"))
        st.session_state[k("time")] = item.get("time") or datetime.time(10, 0)
        st.session_state[k("alarm")] = item.get("alarm", 0)
        st.session_state[k("memo")] = item.get("memo", "")
    prefix = f"ws_f_{case_id}_{kind}_"
    ws["form_base"] = {key: value for key, value in st.session_state.items() if key.startswith(prefix)}


ITEM_STEPS = {"timeline": "story", "evidence": "story", "schedules": "next"}


def ws_form_pending(case_id: str, ws: dict):
    """입력칸을 열어 적었지만 '목록에 추가'(또는 '고친 내용 넣기')를 누르지 않은 항목의 종류. 없으면 None."""
    editing = ws.get("editing")
    if not editing:
        return None
    base = ws.get("form_base") or {}
    prefix = f"ws_f_{case_id}_{editing['kind']}_"
    for key, value in st.session_state.items():
        if key.startswith(prefix) and key in base and value != base[key]:
            return editing["kind"]
    return None


def ws_commit_pending(case_id: str) -> bool:
    """저장·이동 전에 작성 중인 항목을 검사해 목록에 넣는다. 칸이 모자라면 넣지 않고 False."""
    ws = ws_state(case_id)
    ws["pending_error"] = ws["pending_added"] = None
    kind = ws_form_pending(case_id, ws)
    if not kind:
        return True
    if not ws_item_submit(case_id):
        ws["pending_error"] = {"kind": kind, "text": f"작성 중인 {ITEM_NAMES[kind]} 항목을 먼저 추가해 주세요. {ws.get('item_error') or ''}".strip()}
        ws["focus_form"] = (ws.get("focus_form") or 0) + 1  # 그 입력칸이 보이는 단계라면 바로 그 칸까지 내려 준다
        return False
    ws["pending_added"] = ITEM_NAMES[kind]
    return True


def ws_go_to_form(case_id: str) -> None:
    """'작성 중인 칸으로 가기': 입력칸이 있는 단계로 옮기고 그 칸까지 내려 준다."""
    ws = ws_state(case_id)
    kind = (ws.get("editing") or {}).get("kind")
    if kind:
        st.session_state[f"ws_step_{case_id}"] = ITEM_STEPS[kind]
        ws["focus_form"] = (ws.get("focus_form") or 0) + 1


def ws_item_close(case_id: str) -> None:
    ws = ws_state(case_id)
    kind = (ws.get("editing") or {}).get("kind")
    ws["editing"] = ws["item_error"] = ws["form_base"] = ws["pending_error"] = None
    for key in list(st.session_state.keys()):
        if kind and key.startswith(f"ws_f_{case_id}_{kind}_"):
            del st.session_state[key]


def ws_item_submit(case_id: str) -> bool:
    """입력칸의 내용을 목록에 넣는다(저장은 '작업 저장'에서). 입력이 모자라면 칸을 그대로 두고 알려 준다."""
    ws = ws_state(case_id)
    editing = ws.get("editing") or {}
    kind, index = editing.get("kind"), editing.get("index")
    get = lambda field: st.session_state.get(ws_form_key(case_id, kind, field))  # noqa: E731
    items = ws[kind]
    if kind == "timeline":
        what = (get("what") or "").strip()
        if not what:
            ws["item_error"] = "'무슨 일이 있었나요' 칸을 적어 주세요."
            return False
        link = get("evidence")
        item = {"when": (get("when") or "").strip()[:40], "what": what[:300], "evidence": "" if link in (None, NO_LINK) else link}
        label = what[:30]
    elif kind == "evidence":
        name = (get("name") or "").strip()[:60]
        if not name:
            ws["item_error"] = "'자료 이름' 칸을 적어 주세요."
            return False
        if any(e["name"] == name for i, e in enumerate(items) if i != index):
            ws["item_error"] = "같은 이름의 자료가 이미 있어요. 이름을 조금 다르게 적어 주세요."
            return False
        old = items[index] if index is not None else {}
        item = {"name": name, "shows": (get("shows") or "").strip()[:200], "source": old.get("source") or "직접 추가"}
        if old and old["name"] != name:  # 이름을 바꾸면 경위의 연결도 따라 바꾼다
            for row in ws.get("timeline") or []:
                if row.get("evidence") == old["name"]:
                    row["evidence"] = name
        label = name
    else:
        old = items[index] if index is not None else {}
        try:
            item = clean_event({
                "kind": get("kind"), "title": get("title"), "date": get("date"),
                "time": get("time") if get("has_time") else None, "alarm": get("alarm") or 0, "memo": get("memo"),
                "uid": old.get("uid"),
            })
        except ValueError:
            ws["item_error"] = "날짜를 골라 주세요."
            return False
        label = item["title"]
    if index is None:
        items.append(item)
        verb = "목록에 추가했어요"
    else:
        items[index] = item
        verb = "고쳤어요"
    if kind == "schedules":
        items.sort(key=lambda e: (e["date"], e["time"] or datetime.time(0, 0)))
    ws_item_close(case_id)
    ws["item_notice"] = {"kind": kind, "text": f"'{label}' {verb}. {ws_saved_hint()}"}
    ws["pending_error"] = None
    return True


def ws_item_delete(case_id: str, kind: str, index: int, confirmed: bool = False) -> None:
    ws = ws_state(case_id)
    ws["item_notice"] = None
    if not confirmed:
        ws["confirm_del"] = {"kind": kind, "index": index}
        return
    ws["confirm_del"] = None
    removed = ws[kind].pop(index)
    if kind == "evidence":  # 지운 자료를 가리키던 경위는 '연결 안 함'으로 둔다
        for row in ws.get("timeline") or []:
            if row.get("evidence") == removed["name"]:
                row["evidence"] = ""
    label = removed.get("what") or removed.get("name") or removed.get("title") or ""
    ws["item_notice"] = {"kind": kind, "text": f"'{label[:30]}' 지웠어요. {ws_saved_hint()}"}


def ws_item_move_up(case_id: str, kind: str, index: int) -> None:
    items = ws_state(case_id)[kind]
    if 0 < index < len(items):
        items[index - 1], items[index] = items[index], items[index - 1]


def render_item_form(case_id: str, ws: dict, kind: str, evidence_names=()) -> None:
    """이름 붙은 입력칸으로 항목 하나를 적는다. '목록에 추가'(또는 '고친 내용 넣기')를 눌러야 목록에 들어간다."""
    index = ws["editing"]["index"]
    k = lambda field: ws_form_key(case_id, kind, field)  # noqa: E731
    with st.container(border=True, key=f"ws_form_{case_id}_{kind}"):
        st.markdown(f"**{ITEM_NAMES[kind]} {'추가' if index is None else '고치기'}**")
        if ws.get("focus_form"):
            # '작성 중인 칸으로 가기'를 눌렀을 때만 이 입력칸까지 내려 준다(화면 맨 위로 올리는 동작보다 뒤에 실행).
            _scroll_to_component(key=f"ws_focus_{case_id}_{ws['focus_form']}", height=0,
                                 data={"selector": f".st-key-ws_form_{case_id}_{kind}"})
            ws["focus_form"] = None
        if kind == "timeline":
            st.text_input("언제 (모르면 비워 두세요)", key=k("when"), max_chars=40, placeholder="예: 9월 2일 오후 3시")
            st.text_area("무슨 일이 있었나요", key=k("what"), max_chars=300, height=90,
                         placeholder="예: 판매자 계좌로 45만 원을 보냈어요.")
            options = [NO_LINK] + list(evidence_names)
            if st.session_state.get(k("evidence")) not in options:
                st.session_state[k("evidence")] = NO_LINK
            st.selectbox("이 일을 보여 주는 자료 (선택)", options, key=k("evidence"),
                         help="증거 목록에 있는 자료와 첨부한 파일 이름 중에서 골라요.")
        elif kind == "evidence":
            st.text_input("자료 이름", key=k("name"), max_chars=60, placeholder="예: 채팅 캡처, 이체 내역")
            st.text_area("이 자료가 보여 주는 것 (선택)", key=k("shows"), max_chars=200, height=80,
                         placeholder="예: 판매자가 물건을 보내겠다고 한 대화")
        else:
            col_kind, col_title = st.columns([1, 2])
            with col_kind:
                st.selectbox("종류", list(KIND_LABELS), format_func=KIND_LABELS.get, key=k("kind"))
            with col_title:
                st.text_input("할 일", key=k("title"), max_chars=60, placeholder="예: ○○경찰서에 고소장 제출")
            col_date, col_time = st.columns(2)
            with col_date:
                st.date_input("날짜", key=k("date"), format="YYYY-MM-DD",
                              help="달력에서 고르거나 2026-10-20처럼 적고 Enter를 누르세요.")
            with col_time:
                st.checkbox("시간도 정하기", key=k("has_time"))
                if st.session_state.get(k("has_time")):
                    st.time_input("시간", key=k("time"), step=600)
            st.selectbox("알림 (휴대폰·PC 일정 앱이 보내요)", list(ALARM_CHOICES), format_func=ALARM_CHOICES.get,
                         key=k("alarm"))
            with st.expander("메모 (선택)", expanded=bool(st.session_state.get(k("memo")))):
                st.text_area("메모", key=k("memo"), max_chars=300, height=70, label_visibility="collapsed")
        if ws.get("item_error"):
            st.error(ws["item_error"])
        col_ok, col_cancel = st.columns(2)
        with col_ok:
            st.button("목록에 추가" if index is None else "고친 내용 넣기", key=f"ws_fok_{case_id}_{kind}", type="primary",
                      width="stretch", on_click=ws_item_submit, args=(case_id,))
        with col_cancel:
            st.button("취소", key=f"ws_fcancel_{case_id}_{kind}", width="stretch", on_click=ws_item_close, args=(case_id,))
        st.caption("목록에 넣은 뒤 " + ws_saved_hint())


def render_item_list(case_id: str, ws: dict, kind: str, items: list, describe_item, empty_text: str,
                     evidence_names=(), add_label: str = "") -> None:
    """항목마다 읽기 쉬운 카드와 수정·삭제 버튼. 표를 더블클릭하지 않아도 된다."""
    editing = ws.get("editing") or {}
    confirm = ws.get("confirm_del") or {}
    if not items and not (editing.get("kind") == kind and editing.get("index") is None):
        st.caption(empty_text)
    for index, item in enumerate(items):
        if editing.get("kind") == kind and editing.get("index") == index:
            render_item_form(case_id, ws, kind, evidence_names)
            continue
        with st.container(border=True, key=f"ws_item_{case_id}_{kind}_{index}"):
            title, detail = describe_item(index, item)
            st.markdown(title)
            if detail:
                st.caption(detail)
            if confirm.get("kind") == kind and confirm.get("index") == index:
                st.markdown("**이 항목을 지울까요?**")
                with st.container(key=f"ws_btns_{case_id}_{kind}_c{index}"):
                    col_yes, col_no = st.columns(2)
                    with col_yes:
                        st.button("지우기", key=f"ws_del_{case_id}_{kind}_ok_{index}", width="stretch",
                                  on_click=ws_item_delete, args=(case_id, kind, index, True))
                    with col_no:
                        st.button("취소", key=f"ws_keep_{case_id}_{kind}_{index}", width="stretch",
                                  on_click=lambda: ws.__setitem__("confirm_del", None))
                continue
            with st.container(key=f"ws_btns_{case_id}_{kind}_{index}"):
                cols = st.columns(3 if kind == "timeline" else 2)
                with cols[0]:
                    st.button("수정", key=f"ws_mod_{case_id}_{kind}_{index}", icon=":material/edit:", width="stretch",
                              on_click=ws_item_open, args=(case_id, kind, index))
                with cols[1]:
                    st.button("삭제", key=f"ws_del_{case_id}_{kind}_{index}", icon=":material/delete:", width="stretch",
                              on_click=ws_item_delete, args=(case_id, kind, index))
                if kind == "timeline":
                    with cols[2]:
                        st.button("위로", key=f"ws_up_{case_id}_{kind}_{index}", icon=":material/arrow_upward:",
                                  width="stretch", disabled=index == 0, on_click=ws_item_move_up, args=(case_id, kind, index))
    notice = ws.get("item_notice") or {}
    if notice.get("kind") == kind:
        st.success(notice["text"])
    if editing.get("kind") == kind and editing.get("index") is None:
        render_item_form(case_id, ws, kind, evidence_names)
    elif not editing:
        st.button(add_label or f"{ITEM_NAMES[kind]} 추가", key=f"ws_add_{case_id}_{kind}", icon=":material/add:",
                  type="secondary", on_click=ws_item_open, args=(case_id, kind, None))


def ws_add_file(case_id: str, name: str) -> None:
    ws = ws_state(case_id)
    if not any(e["name"] == name for e in ws["evidence"]):
        ws["evidence"].append({"name": name[:60], "shows": "", "source": "첨부 파일"})
    ws["item_notice"] = {"kind": "evidence", "text": f"'{name[:30]}' 목록에 넣었어요. '수정'을 눌러 무엇을 보여 주는지 적어 주세요. {ws_saved_hint()}"}


def render_ws_story(case_id: str, case: dict, ws: dict, file_names: list) -> None:
    evidence = ws_items(case, ws, "evidence")
    timeline = ws_items(case, ws, "timeline")
    names = [e["name"] for e in evidence]
    st.markdown("#### 사건 경위")
    st.caption("적은 사건 내용을 일 단위로 나눠 두었어요. 날짜는 적힌 글자만 옮겼어요. 고치거나 더하고 지울 수 있어요.")
    render_item_list(
        case_id, ws, "timeline", timeline,
        lambda i, r: (f"**{i + 1}. {md_escape(r.get('when') or '날짜 모름')}** — {md_escape(r.get('what') or '')}",
                      "관련 자료: " + (r.get("evidence") or NO_LINK)),
        "아직 적은 경위가 없어요. '사건 경위 추가'를 눌러 한 가지씩 적어 주세요.", names,
    )

    st.markdown("#### 증거 자료")
    st.caption("자료마다 무엇을 보여 주는지 적어 주세요. 고소미는 파일 내용을 열어 보지 않아요.")
    render_item_list(
        case_id, ws, "evidence", evidence,
        lambda i, e: (f"**{md_escape(e['name'])}**" + (f" · {e['source']}" if e.get("source") else ""),
                      e.get("shows") or "무엇을 보여 주는지 아직 적지 않았어요."),
        "아직 적은 자료가 없어요. 자료가 없어도 괜찮아요.",
    )
    unlisted = [n for n in file_names if str(n)[:60] not in names]
    if unlisted:
        st.caption("첨부한 파일 중 목록에 없는 것")
        for i, name in enumerate(unlisted):
            st.button(f"'{name}' 목록에 넣기", key=f"ws_addfile_{case_id}_{i}", type="tertiary", icon=":material/attach_file:",
                      on_click=ws_add_file, args=(case_id, str(name)))

    issues = link_issues(timeline, evidence)
    todo = (
        [f"자료가 연결되지 않은 일: {t[:40]}" for t in issues["events_without_evidence"]]
        + [f"경위에 연결되지 않은 자료: {n}" for n in issues["evidence_not_used"]]
        + [f"설명이 없는 자료: {n}" for n in issues["evidence_without_note"]]
    )
    if todo:
        with st.expander(f"확인할 항목 {len(todo)}개"):
            st.markdown("\n".join(f"- {md_escape(item)}" for item in todo))
            st.caption("자료가 없어도 괜찮아요. 확인할 곳을 알려 드리는 것이며, 사실 여부를 판단한 것은 아니에요.")
    st.button("다음: 초안", key=f"ws_to_draft_{case_id}", type="primary", on_click=ws_go, args=(case_id, "draft"))


def render_ws_draft(case_id: str, case: dict, ws: dict, draft_key: str, editor_key: str) -> None:
    draft = st.session_state.get(draft_key)
    if not draft:
        st.markdown("아직 초안이 없어요. 채운 내용으로 참고용 초안을 만들어요.")
        if AI_FEATURES_PAUSED:
            st.caption(AI_PAUSED_MESSAGE)
        if st.button("초안 만들기", key=f"ws_make_{case_id}", type="primary", icon=":material/auto_awesome:",
                     disabled=AI_FEATURES_PAUSED):
            try:
                with st.spinner("고소장 초안을 만들고 있어요. 보통 10~30초 걸려요."):
                    st.session_state[draft_key] = generate_case_draft(case)
                st.session_state.pop(editor_key, None)
                st.rerun()
            except Exception as error:
                st.error(ai_error_message(error, "초안을 만들지 못했어요. 적은 내용은 그대로 있어요. 1~2분 뒤에 다시 눌러 주세요."))
        return
    current = st.session_state.get(editor_key) or draft
    check_lines = draft_check_items(current)
    if check_lines:
        with st.expander(f"직접 채울 곳 {len(check_lines)}군데 ([확인 필요])"):
            st.markdown("\n".join(f"- {md_escape(line[:120])}" for line in check_lines))
    st.markdown("**표현 바꾸기** — 사실·금액·날짜는 그대로 두고 문장만 바꿔요.")
    cols = st.columns(3)
    for col, (style, (label, _)) in zip(cols, STYLES.items()):
        with col:
            if st.button(label, key=f"ws_style_{case_id}_{style}", width="stretch", disabled=AI_FEATURES_PAUSED):
                try:
                    with st.spinner(f"{label} 바꾸고 있어요."):
                        after = clean_transcript(call_gemini([{"role": "user", "parts": [{"text": rewrite_prompt(current, style)}]}]))
                except Exception as error:
                    st.error(ai_error_message(error, "표현을 바꾸지 못했어요. 지금 초안은 그대로 있어요."))
                else:
                    ws["history"].append(current)
                    st.session_state[editor_key] = with_draft_disclaimer(after)
                    ws["rewrite_notes"] = rewrite_changes(current, after)
                    st.rerun()
    with cols[2]:
        st.button("바로 전 초안으로", key=f"ws_undo_{case_id}", width="stretch", disabled=not ws["history"],
                  on_click=lambda: (st.session_state.__setitem__(editor_key, ws["history"].pop()), ws.pop("rewrite_notes", None)))
    for note in ws.get("rewrite_notes") or []:
        st.warning(note + " 바뀐 부분을 꼭 확인하거나 '바로 전 초안으로'를 눌러 주세요.")
    render_draft_panel(editor_key, draft, case, case_id, show_save=not workspace_save_available(), show_download=False)
    st.button("다음: 내려받기", key=f"ws_to_download_{case_id}", type="primary", icon=":material/arrow_forward:",
              on_click=ws_go, args=(case_id, "download"))


def render_ws_download(case_id: str, ws: dict, editor_key: str, draft_key: str, case: dict) -> None:
    draft = st.session_state.get(editor_key) or st.session_state.get(draft_key) or ""
    timeline = ws["timeline"] if ws["timeline"] is not None else build_timeline(case)
    evidence = ws["evidence"] if ws["evidence"] is not None else evidence_items(case, ws.get("file_names"))
    st.markdown("#### 내려받을 자료")
    st.caption("함께 내려받을 자료를 고르세요. 고소미는 기관에 자동으로 보내지 않아요.")
    include = {
        "draft": st.checkbox("고소장 초안" + ("" if draft else " (아직 없음)"), value=bool(draft), key=f"ws_inc_draft_{case_id}",
                             disabled=not draft),
        "timeline": st.checkbox(f"사건 경위 ({len(timeline)}줄)", value=bool(timeline), key=f"ws_inc_tl_{case_id}"),
        "evidence": st.checkbox(f"증거 목록 ({len(evidence)}개)", value=bool(evidence), key=f"ws_inc_ev_{case_id}"),
    }
    text = bundle_text(draft, timeline, evidence, include)
    if not text:
        st.info("고른 자료가 없어요.")
        return
    with st.expander("내려받을 내용 미리 보기"):
        st.text(text[:4000] + ("…" if len(text) > 4000 else ""))
    st.caption("내려받은 파일을 제출하기 전에 사실관계를 직접 확인하고, 법률 전문가의 검토를 받아 주세요.")
    col_doc, col_txt = st.columns(2)
    with col_doc:
        st.download_button("Word로 내려받기", data=bundle_docx(text), file_name=f"case_{case_id}_bundle.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           key=f"ws_dl_doc_{case_id}", width="stretch", type="primary", icon=":material/download:")
    with col_txt:
        st.download_button("TXT로 내려받기", data=text, file_name=f"case_{case_id}_bundle.txt", mime="text/plain",
                           key=f"ws_dl_txt_{case_id}", width="stretch")
    st.button("다음: 다음 할 일", key=f"ws_to_next_{case_id}", icon=":material/arrow_forward:",
              on_click=ws_go, args=(case_id, "next"))


def render_ws_next(case_id: str, case: dict, ws: dict) -> None:
    render_case_progress(case_id)
    st.markdown("#### 일정")
    st.caption("제출·상담·후속 확인처럼 직접 정한 일정을 적어 두는 곳이에요. "
               "법률상 기한은 대신 계산하지 않으니 담당 기관에서 확인해 주세요.")
    render_item_list(
        case_id, ws, "schedules", ws["schedules"],
        lambda i, e: (f"**{md_escape(e['title'])}**", md_escape(describe(e)) + (f" · 메모: {md_escape(e['memo'])}" if e.get("memo") else "")),
        "아직 적은 일정이 없어요.",
    )
    with st.expander("앱에 저장하기와 휴대폰 일정에 넣기는 달라요"):
        st.markdown(
            ("- **앱에 저장**: 화면 아래 '작업 저장'을 누르면 이 사건에 일정이 저장돼 다음에 열어도 보여요.\n"
             if workspace_save_available() else "- **앱에 저장**: 지금은 미리보기라 일정이 저장되지 않아요.\n")
            + "- **휴대폰·PC 일정에 넣기**: 아래 .ics 파일을 받아 열면 일정 앱에 추가돼요. 알림은 그 일정 앱이 보내요.\n"
            "- 고소미는 알림을 직접 보내지 않아요."
        )
    if ws["schedules"]:
        st.download_button(
            "휴대폰·PC 일정 앱에 넣을 파일 받기 (.ics)", data=build_ics(ws["schedules"], case.get("case_type") or ""),
            file_name=f"gosomi_schedule_{case_id[:8]}.ics", mime="text/calendar", key=f"ws_sch_{case_id}_ics", width="stretch",
        )
        st.caption("받은 파일을 연 뒤 일정 앱에서 시간과 알림을 확인해 주세요. 앱마다 알림 방식이 다를 수 있어요.")


def ws_payload(case_id: str, case: dict, ws: dict) -> dict:
    """저장할 작업 공간 내용(사건 경위·증거 설명·일정·준비 체크). 초안 본문은 case_drafts에 따로 저장한다."""
    timeline = ws["timeline"] if ws["timeline"] is not None else build_timeline(case)
    evidence = ws["evidence"] if ws["evidence"] is not None else evidence_items(case, ws.get("file_names"))
    schedules = []
    for event in ws["schedules"]:
        schedules.append({
            "kind": event["kind"], "title": event["title"], "date": event["date"].isoformat(),
            "time": event["time"].strftime("%H:%M") if event["time"] else None,
            "alarm": event["alarm"], "memo": event["memo"], "uid": event["uid"],
        })
    p = f"prog_{case_id}_"
    on = st.session_state.get(p + "on")
    checklist = {key: bool(st.session_state.get(p + key)) for key, _ in PROGRESS_ITEMS}
    checklist.update({
        "submitted_to": (st.session_state.get(p + "to") or "")[:PROGRESS_PLACE_MAX_CHARS],
        "submitted_on": on.isoformat() if isinstance(on, datetime.date) else "",
        "next_note": (st.session_state.get(p + "note") or "")[:PROGRESS_NOTE_MAX_CHARS],
    })
    return {
        "timeline": [{"when": r.get("when", ""), "what": r.get("what", ""), "evidence": r.get("evidence") or ""} for r in timeline],
        "evidence_notes": [{"name": e["name"], "shows": e.get("shows", ""), "source": e.get("source", "")} for e in evidence],
        "schedules": schedules,
        "checklist": checklist,
    }


def ws_snapshot(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def ws_apply_row(case_id: str, ws: dict, row: dict) -> None:
    """저장된 작업 공간을 화면 상태로 불러온다(위젯을 그리기 전에만 부른다)."""
    ws["timeline"] = [dict(r) for r in row.get("timeline") or []]
    ws["evidence"] = [dict(r) for r in row.get("evidence_notes") or []]
    ws["editing"] = ws["confirm_del"] = ws["item_notice"] = None
    events = []
    for event in row.get("schedules") or []:
        try:
            events.append(clean_event({
                **event, "date": datetime.date.fromisoformat(event["date"]),
                "time": datetime.time.fromisoformat(event["time"]) if event.get("time") else None,
            }))
        except (KeyError, ValueError, TypeError):
            continue
    ws["schedules"] = events
    checklist = row.get("checklist") or {}
    p = f"prog_{case_id}_"
    for key, _ in PROGRESS_ITEMS:
        st.session_state[p + key] = bool(checklist.get(key))
    st.session_state[p + "to"] = checklist.get("submitted_to") or ""
    try:
        st.session_state[p + "on"] = datetime.date.fromisoformat(checklist["submitted_on"]) if checklist.get("submitted_on") else None
    except ValueError:
        st.session_state[p + "on"] = None
    st.session_state[p + "note"] = checklist.get("next_note") or ""
    ws["version"] = row.get("version")
    ws["saved_at"] = row.get("updated_at")


def ws_load(case: dict, case_id: str, ws: dict, draft_key: str, editor_key: str) -> None:
    ws["loaded"] = True
    ws["case_version"] = case.get("edit_version") or 1
    try:
        row = load_workspace_row(case_id)
    except Exception as error:
        log_auth_error(error)
        ws["save_error"] = map_workspace_save_error(error)
        return
    if row:
        ws_apply_row(case_id, ws, row)
    ws["snapshot"] = ws_snapshot(ws_payload(case_id, case, ws))
    if draft_save_available() and not st.session_state.get(draft_key):
        try:
            draft_row = load_case_draft(case_id)
        except Exception as error:
            log_auth_error(error)
            draft_row = None
        if draft_row:
            st.session_state[draft_key] = draft_row["draft_text"]
            st.session_state[editor_key] = draft_row["draft_text"]
            st.session_state[f"{editor_key}_saved"] = {"text": draft_row["draft_text"].strip(), "at": draft_row.get("updated_at")}


def ws_draft_dirty(draft_key: str, editor_key: str) -> bool:
    if not draft_save_available():
        return False
    text = (st.session_state.get(editor_key) or st.session_state.get(draft_key) or "").strip()
    saved = st.session_state.get(f"{editor_key}_saved")
    return bool(text) and (not saved or saved["text"] != text)


def ws_dirty(case_id: str, case: dict, ws: dict, draft_key: str, editor_key: str) -> bool:
    return bool(ws["answers"]) or ws_snapshot(ws_payload(case_id, case, ws)) != ws["snapshot"] or ws_draft_dirty(draft_key, editor_key)


SAVE_PART_LABELS = {"case": "사건 정보", "workspace": "정리 내용(경위·증거·일정·체크)", "draft": "초안"}


def ws_partial_note(done: list, failed: str) -> str:
    """일부만 저장됐을 때 무엇이 저장됐고 무엇이 남았는지 알려 준다."""
    if not done:
        return ""
    return (" 저장한 것: " + ", ".join(SAVE_PART_LABELS[p] for p in done)
            + " / 아직 저장 못 한 것: " + SAVE_PART_LABELS[failed] + ". 다시 누르면 남은 것만 저장해요.")


def ws_save(case: dict, case_id: str, draft_key: str, editor_key: str, overwrite: str = "") -> None:
    """사건 정보 → 정리 내용 → 초안 순서로, 바뀐 것만 저장한다.

    하나라도 실패하면 '저장했어요'를 띄우지 않고, 이미 저장한 부분과 남은 부분을 알려 준다. 저장한 부분은
    저장된 상태로 표시해 다시 누르면 남은 것만 저장한다. 화면의 입력은 지우지 않는다.
    """
    ws = ws_state(case_id)
    ws["save_error"] = ws["saved_ok"] = ws["conflict"] = None
    ws["save_partial"] = False
    if not ws_commit_pending(case_id):
        return  # 저장한 것처럼 보이지 않게 아무것도 저장하지 않고, 작성 중인 칸으로 안내한다
    done, step = [], "case"
    try:
        if ws["answers"]:
            if overwrite == "case":
                ws["case_version"] = case.get("edit_version") or ws["case_version"]
            row = update_case_row(case_id, ws["answers"], ws["case_version"])
            ws["case_version"] = row.get("edit_version")
            if st.session_state.get("case_draft_id") == case_id and st.session_state.get("case_draft_case"):
                st.session_state.case_draft_case = {**st.session_state.case_draft_case, **ws["answers"]}
            ws["case_saved_values"] = {**(ws.get("case_saved_values") or {}), **ws["answers"]}
            ws["answers"] = {}
            done.append("case")
        step = "workspace"
        payload = ws_payload(case_id, case, ws)
        if overwrite == "workspace" or ws_snapshot(payload) != ws["snapshot"]:
            if overwrite == "workspace":
                remote = load_workspace_row(case_id)
                ws["version"] = remote.get("version") if remote else None
            row = save_workspace_row(case_id, payload, ws["version"])
            ws["version"], ws["saved_at"] = row.get("version"), row.get("updated_at")
            ws["snapshot"] = ws_snapshot(payload)
            done.append("workspace")
        step = "draft"
        if ws_draft_dirty(draft_key, editor_key):
            text = (st.session_state.get(editor_key) or st.session_state.get(draft_key) or "").strip()
            saved = st.session_state.get(f"{editor_key}_saved")
            expected = False if overwrite == "draft" else (saved or {}).get("at")
            at = save_case_draft(case_id, text, expected_at=expected)
            st.session_state[f"{editor_key}_saved"] = {"text": text, "at": at}
            ws["saved_at"] = at or ws["saved_at"]
            done.append("draft")
    except SaveConflict as conflict:
        ws["conflict"] = str(conflict)
        ws["conflict_note"] = ws_partial_note(done, step)
        return
    except Exception as error:
        log_auth_error(error)
        message = map_draft_save_error(error) if isinstance(error, ValueError) else map_workspace_save_error(error)
        ws["save_error"] = message + ws_partial_note(done, step)
        ws["save_partial"] = bool(done)
        return
    ws["conflict_note"] = ""
    ws["saved_ok"] = format_created_at(ws["saved_at"]) if ws["saved_at"] else "방금"


def ws_take_remote(case: dict, case_id: str, draft_key: str, editor_key: str) -> None:
    """먼저 저장된 내용 불러오기(여기서 고친 내용은 버린다)."""
    ws = ws_state(case_id)
    kind, ws["conflict"] = ws["conflict"], None
    if kind == "case":
        ws["answers"] = {}
        ws["case_version"] = case.get("edit_version") or ws["case_version"]
        for key in list(st.session_state.keys()):
            if key.startswith(f"ws_edit_{case_id}_"):
                del st.session_state[key]
    elif kind == "workspace":
        row = load_workspace_row(case_id)
        if row:
            ws_apply_row(case_id, ws, row)
        ws["snapshot"] = ws_snapshot(ws_payload(case_id, case, ws))
    elif kind == "draft":
        row = load_case_draft(case_id)
        if row:
            st.session_state[draft_key] = row["draft_text"]
            st.session_state[editor_key] = row["draft_text"]
            st.session_state[f"{editor_key}_saved"] = {"text": row["draft_text"].strip(), "at": row.get("updated_at")}


CONFLICT_TEXT = {
    "case": "다른 탭이나 기기에서 이 사건 내용을 먼저 고쳤어요.",
    "workspace": "다른 탭이나 기기에서 이 사건의 정리 내용을 먼저 저장했어요.",
    "draft": "다른 탭이나 기기에서 이 사건의 초안을 먼저 저장했어요.",
}


def render_ws_save_bar(case: dict, case_id: str, ws: dict, draft_key: str, editor_key: str) -> bool:
    """화면 아래에 붙어 있는 저장 영역. 저장하지 않은 변경이 있으면 True."""
    with st.container(key=f"ws_savebar_{case_id}"):
        if ws["conflict"]:
            st.warning(CONFLICT_TEXT.get(ws["conflict"], "먼저 저장된 내용이 있어요.") + " 어떻게 할까요? 확인 없이 덮어쓰지 않았어요."
                       + (ws.get("conflict_note") or ""))
            col_take, col_mine = st.columns(2)
            with col_take:
                st.button("먼저 저장된 내용 불러오기", key=f"ws_take_{case_id}", width="stretch", on_click=ws_take_remote,
                          args=(case, case_id, draft_key, editor_key), help="여기서 고친 내용은 버려요.")
            with col_mine:
                st.button("내 내용으로 덮어쓰기", key=f"ws_mine_{case_id}", width="stretch", type="primary", on_click=ws_save,
                          args=(case, case_id, draft_key, editor_key, ws["conflict"]))
            return True
        pending = ws_form_pending(case_id, ws)
        pending_error = ws.get("pending_error") if ws.get("editing") else None
        dirty = ws_dirty(case_id, case, ws, draft_key, editor_key) or bool(pending)
        col_status, col_button = st.columns([3, 2], vertical_alignment="center")
        with col_status:
            if pending_error:
                st.warning(pending_error["text"])
            elif pending:
                st.markdown(f"**작성 중인 {ITEM_NAMES[pending]} 항목이 있어요.** '작업 저장'을 누르면 목록에 넣고 함께 저장해요.")
            elif ws["save_error"] and ws.get("save_partial"):
                st.warning("**일부만 저장됐어요.** " + ws["save_error"])
            elif ws["save_error"]:
                st.error(ws["save_error"])
            elif dirty:
                st.markdown("**수정한 내용이 아직 저장되지 않았습니다.**")
            elif ws.pop("nothing_to_save", False):
                st.markdown("바뀐 내용이 없어 저장할 것이 없어요.")
            elif ws["saved_ok"]:
                st.markdown(f"✓ 저장했어요 · {ws['saved_ok']}"
                            + (f" (작성 중이던 {ws['pending_added']} 항목도 넣었어요)" if ws.get("pending_added") else ""))
            elif ws["saved_at"]:
                st.markdown(f"마지막 저장 · {format_created_at(ws['saved_at'])}")
            else:
                st.markdown("아직 저장하지 않았어요.")
        with col_button:
            form_kind = (ws.get("editing") or {}).get("kind")
            if pending_error and st.session_state.get(f"ws_step_{case_id}") != ITEM_STEPS.get(form_kind):
                # 입력칸이 지금 단계에 없으면 그 칸으로 데려간다. 같은 단계면 칸을 고친 뒤 바로 다시 저장할 수 있다.
                st.button("작성 중인 칸으로 가기", key=f"ws_goform_{case_id}", type="primary", width="stretch",
                          icon=":material/edit:", on_click=ws_go_to_form, args=(case_id,))
                clicked = False
            else:
                # 항상 누를 수 있게 둔다: 입력칸에 적는 중에는 칸을 벗어나기 전까지 변경이 반영되지 않아,
                # 버튼을 막아 두면 첫 번째 누름이 '적은 내용 확정'에만 쓰이고 두 번 눌러야 저장됐다(2026-10-12 확인).
                clicked = st.button("작업 저장", key=f"ws_save_{case_id}", type="primary", width="stretch", icon=":material/save:")
        if clicked:
            if dirty or ws["save_error"]:
                with st.spinner("저장하고 있어요…"):
                    ws_save(case, case_id, draft_key, editor_key)
            else:
                ws["nothing_to_save"] = True
            st.rerun()
    return dirty or bool(ws["save_error"])


def ws_edit_field(case_id: str, key: str, original: str) -> None:
    ws = ws_state(case_id)
    value = (st.session_state.get(f"ws_edit_{case_id}_{key}") or "").strip()
    if value == (original or "").strip():
        ws["answers"].pop(key, None)
    else:
        ws["answers"][key] = value


def render_case_edit(case_id: str, case: dict, ws: dict) -> None:
    """저장한 사건 내용 고치기. 고친 칸은 '작업 저장'을 눌러야 저장된다."""
    with st.expander("저장한 사건 내용 고치기" + (f" (고친 칸 {len(ws['answers'])}개)" if ws["answers"] else "")):
        st.caption("고친 칸은 화면 아래 '작업 저장'을 눌러야 저장돼요.")
        for label, key in CASE_FIELDS:
            original = case.get(key) or ""
            widget_key = f"ws_edit_{case_id}_{key}"
            st.session_state.setdefault(widget_key, ws["answers"].get(key, original))
            widget = st.text_area if key in CASE_LONG_TEXT_FIELDS else st.text_input
            widget(label, key=widget_key, max_chars=CASE_FIELD_MAX_CHARS[key], on_change=ws_edit_field,
                   args=(case_id, key, original))


def render_case_workspace(case: dict, case_id: str, draft_key: str, editor_key: str, file_names=None) -> None:
    ws = ws_state(case_id)
    ws["file_names"] = [str(n) for n in file_names or []]
    can_save = workspace_save_available()
    if can_save and not ws["loaded"]:
        ws_load(case, case_id, ws, draft_key, editor_key)
    effective = ws_case(case, ws)
    has_draft = bool(st.session_state.get(draft_key))
    progress = {key: st.session_state.get(f"prog_{case_id}_{key}") for key, _ in PROGRESS_ITEMS}
    action = next_action(effective, has_draft, progress)
    default_step = {"빠진 정보 확인하기": "check", "초안 만들기": "draft"}.get(action, "draft" if has_draft else "check")
    step = st.session_state.setdefault(f"ws_step_{case_id}", default_step)
    with st.container(border=True, key=f"ws_head_{case_id}"):
        st.markdown(f"**다음 할 일: {action}**")
        if can_save:
            st.caption("고친 내용은 화면 아래 '작업 저장'을 눌러야 저장돼요. 단계를 옮겨도 고친 내용은 그대로 있어요.")
        else:
            st.caption("🧪 미리보기: 이 화면에서 정리한 경위·증거 설명·일정·체크는 저장되지 않아요. 새로고침하거나 로그아웃하면 사라져요.")
    st.session_state[f"ws_nav_{case_id}"] = step
    st.segmented_control("정리 단계", list(WS_STEPS), format_func=WS_STEPS.get, key=f"ws_nav_{case_id}",
                         label_visibility="collapsed", width="stretch", on_change=ws_on_nav, args=(case_id,))
    if step == "check":
        render_ws_check(case_id, effective, ws)
        if can_save:
            render_case_edit(case_id, case, ws)
    elif step == "story":
        render_ws_story(case_id, effective, ws, ws["file_names"])
    elif step == "draft":
        render_ws_draft(case_id, effective, ws, draft_key, editor_key)
    elif step == "download":
        render_ws_download(case_id, ws, editor_key, draft_key, effective)
    else:
        render_ws_next(case_id, effective, ws)
    # 저장 영역은 아래 화면(목록 편집 등)을 그린 뒤에 그려야 방금 고친 내용이 상태에 반영된다. CSS로 화면 아래에 붙여 둔다.
    if can_save and render_ws_save_bar(case, case_id, ws, draft_key, editor_key):
        st.session_state["_unsaved"] = {"case": case, "case_id": case_id, "draft_key": draft_key, "editor_key": editor_key}


def render_case_detail(case: dict, saved_at, can_save_drafts: bool) -> None:
    case_id = case.get("case_id", "")
    st.button("사건 목록으로", key="case_back_to_list", icon=":material/arrow_back:", type="tertiary", on_click=close_case)
    st.markdown(f"### {md_escape(case.get('case_type') or '유형 미입력')}")
    st.caption(f"{format_created_at(case.get('created_at'))} 저장" + (" · 저장된 초안 있음" if saved_at else ""))

    if assist_preview():
        if saved_at and not st.session_state.get(f"list_draft_{case_id}"):
            st.button("저장된 초안 열기", key=f"list_draft_open_{case_id}", type="secondary", on_click=open_saved_draft,
                      args=(case_id,))
        file_names = []
        if EVIDENCE_UPLOAD_ENABLED:
            try:
                file_names = [f["display_name"] for f in list_case_evidence(case_id)]
            except Exception:
                file_names = []
        render_case_workspace(case, case_id, f"list_draft_{case_id}", f"list_draft_editor_{case_id}", file_names)
    else:
        with st.expander("고소장 초안", expanded=True):
            list_draft_key = f"list_draft_{case_id}"
            list_editor_key = f"list_draft_editor_{case_id}"
            if saved_at:
                st.caption(f"저장된 초안이 있어요 (마지막 저장 {format_created_at(saved_at)}).")
                st.button("저장된 초안 열기", key=f"list_draft_open_{case_id}", type="primary", on_click=open_saved_draft,
                          args=(case_id,))
            elif can_save_drafts:
                st.caption("아직 저장된 초안이 없어요. 초안을 만든 뒤 '초안 저장'을 눌러 주세요.")
            else:
                st.caption("저장한 내용으로 초안을 새로 만들어요. 초안은 서버에 저장되지 않으니 필요하면 내려받아 주세요.")
            if AI_FEATURES_PAUSED:
                st.caption(AI_PAUSED_MESSAGE)
            if st.button("AI로 새 초안 만들기", key=f"list_draft_btn_{case_id}", type="secondary" if saved_at else "primary",
                         icon=":material/auto_awesome:", disabled=AI_FEATURES_PAUSED):
                try:
                    with st.spinner("고소장 초안을 만들고 있어요. 보통 10~30초 걸려요."):
                        st.session_state[list_draft_key] = generate_case_draft(case)
                    st.session_state.pop(list_editor_key, None)
                    if saved_at:
                        st.info("새 초안을 만들었어요. '초안 저장'을 누르기 전까지 저장된 초안은 바뀌지 않아요.")
                except Exception as error:
                    st.error(ai_error_message(error, "초안을 만들지 못했어요. 1~2분 뒤에 다시 눌러 주세요."))
            load_error = st.session_state.pop(f"{list_editor_key}_error", None) if not st.session_state.get(list_draft_key) else None
            if load_error:
                st.error(load_error)
            if st.session_state.get(list_draft_key):
                render_draft_panel(list_editor_key, st.session_state[list_draft_key], case, case_id)

        with st.expander("입력한 내용"):
            for label, key in CASE_FIELDS:
                value = case.get(key)
                shown = "_비어 있음_" if is_missing(value) else md_escape(value)
                st.markdown(f"**{label}**  \n{shown}")
            st.caption(f"사건 번호: {case_id}")

    if EVIDENCE_UPLOAD_ENABLED:
        render_case_evidence_manager(case_id)

    with st.expander("이 사건 삭제"):
        st.markdown("이 사건의 입력 내용" + (", 저장된 초안" if can_save_drafts else "") +
                    (", 첨부한 증거파일" if EVIDENCE_UPLOAD_ENABLED else "") + "이 모두 삭제돼요. 되돌릴 수 없어요.")
        confirm_delete = st.checkbox("삭제할 사건이 맞는지 확인했어요", key=f"confirm_delete_{case_id}")
        if st.button("이 사건 삭제하기", key=f"delete_button_{case_id}", disabled=not confirm_delete, type="secondary",
                     icon=":material/delete:"):
            try:
                with st.spinner("사건과 첨부파일을 삭제하고 있어요."):
                    delete_case(case_id)
            except Exception:
                st.error(
                    "사건을 삭제하지 못했어요. 새로고침한 뒤 다시 시도해 주세요. "
                    "첨부파일 일부가 먼저 지워졌을 수 있으니 목록을 확인해 주세요." + contact_hint()
                )
            else:
                st.session_state.case_list_notice = "사건을 삭제했어요."
                st.session_state.open_case_id = None
                st.rerun()


def render_cases_page() -> None:
    st.markdown("## 내 사건")
    list_notice = st.session_state.pop("case_list_notice", None)
    if list_notice:
        st.success(list_notice)
    cases, load_error = load_cases_safe()
    if load_error:
        st.error(load_error + contact_hint())
        if st.button("다시 불러오기", key="reload_cases_button"):
            st.rerun()
        return
    if not cases:
        st.info("아직 저장한 사건이 없어요.")
        st.button("고소장 작성하기", key="cases_go_case", type="primary", on_click=go_to, args=("case",),
                  icon=":material/edit_document:")
        return
    can_save_drafts = draft_save_available()
    draft_index = {}
    if can_save_drafts:
        try:
            draft_index = load_draft_index()
        except Exception as error:
            log_auth_error(error)
            st.warning("저장된 초안 목록을 불러오지 못했어요. 새로고침해 주세요. 사건 내용은 그대로 볼 수 있어요.")
    unique = []
    seen = set()
    for case in cases:
        if case.get("case_id", "") not in seen:
            seen.add(case.get("case_id", ""))
            unique.append(case)
    opened = next((c for c in unique if str(c.get("case_id")) == str(st.session_state.get("open_case_id"))), None)
    if opened:
        render_case_detail(opened, draft_index.get(str(opened.get("case_id"))), can_save_drafts)
        return
    opened_items = ["저장한 초안" if can_save_drafts else "새 초안 만들기"] + (["증거파일"] if EVIDENCE_UPLOAD_ENABLED else [])
    st.caption(f"저장한 사건 {len(unique)}건 · 사건을 열면 " + "·".join(opened_items) + "을 이용할 수 있어요.")
    for case in unique:
        case_id = case.get("case_id", "")
        with st.container(border=True):
            col_text, col_open = st.columns([4, 1], vertical_alignment="center")
            with col_text:
                st.markdown(f"**{md_escape(case.get('case_type') or '유형 미입력')}**")
                meta = f"{format_created_at(case.get('created_at'))} 저장"
                if draft_index.get(str(case_id)):
                    meta += " · 초안 저장됨"
                summary = (case.get("incident_description") or "").strip()
                if summary and not is_missing(summary):
                    meta += f"  \n{md_escape(summary[:60] + ('…' if len(summary) > 60 else ''))}"
                st.markdown(meta)
                if assist_preview():
                    ws = st.session_state.get(f"ws_{case_id}") or {"answers": {}}
                    progress = {key: st.session_state.get(f"prog_{case_id}_{key}") for key, _ in PROGRESS_ITEMS}
                    has_draft = bool(st.session_state.get(f"list_draft_{case_id}") or draft_index.get(str(case_id)))
                    gaps = len(missing_questions({**case, **ws["answers"]}))
                    st.markdown(f"**다음 할 일: {next_action({**case, **ws['answers']}, has_draft, progress)}**"
                                + (f"  \n:gray[빠진 정보 {gaps}개]" if gaps else ""))
            with col_open:
                st.button("열기", key=f"open_case_{case_id}", on_click=open_case, args=(case_id,), width="stretch",
                          type="secondary")


def render_help_page() -> None:
    st.markdown("## 도움말")
    st.segmented_control("도움말 종류", ["피해별 안내", "법률 용어"], key="help_section", default="피해별 안내",
                         required=True, label_visibility="collapsed")
    if st.session_state.get("help_section") == "법률 용어":
        render_terms_section()
    else:
        render_guide_section()


def render_guide_section() -> None:
    st.caption("피해 유형을 고르면 준비할 자료, 자주 묻는 질문, 공식 안내를 보여 드려요. 일반 정보이며 실제 절차는 담당 기관에서 확인해 주세요.")
    qa_options = list(DAMAGE_TYPES)
    default_type = st.session_state.get("case_type_choice")
    selected_qa_type = st.selectbox(
        "피해 유형", qa_options, index=qa_options.index(default_type) if default_type in qa_options else 0,
        key="victim_qa_type",
    )
    render_damage_type_guide(selected_qa_type, show_qa=True)
    if assist_preview():
        render_precedent_finder(selected_qa_type)


def render_terms_section() -> None:
    if assist_preview():
        st.caption("자주 보이는 말을 쉬운 말과 예시로 풀었어요. 내 사건의 결과를 판단하는 내용은 아니에요.")
        render_legal_terms_search("legal_terms_query_v2")
    else:
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


def render_public_chat() -> None:
    """일반 이용자용 AI 상담(기존 기능). 새 대화 화면과 같은 첫 문구·입력창 문구·모양으로 보여 준다. 새 기능은 넣지 않는다."""
    st.caption("일반 법률 정보를 안내해요. 법률 자문은 아니에요.")
    history_box = st.container()
    with history_box:
        if not AI_FEATURES_PAUSED:
            with st.chat_message("assistant"):
                st.markdown(CHAT_WELCOME)
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
    with st.container(key="public_chat_input_row"):  # 입력창을 대화 바로 아래에 둔다(화면 맨 아래 고정이 아니라)
        prompt = st.chat_input("지금은 질문을 보낼 수 없어요" if AI_FEATURES_PAUSED else CHAT_INPUT_PLACEHOLDER, max_chars=3000,
                               key="public_chat_input", disabled=AI_FEATURES_PAUSED)
    with st.expander("대화는 어떻게 처리되나요?"):
        st.markdown(
            "- 대화 내용은 이 서비스에 저장하지 않아요. 새로고침하거나 로그아웃하면 사라져요.\n"
            "- 답변을 만들기 위해 질문이 Google Gemini로 전송돼요. Google은 오남용 방지를 위해 입력 기록을 일정 기간 보관할 수 있어요.\n"
            "- AI 답변은 일반 정보예요. 인터넷 검색을 하지 않아 최신 법·제도와 다를 수 있고, 범죄가 되는지나 결과는 수사기관·법원이 판단해요."
        )
    if prompt and not AI_FEATURES_PAUSED:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with history_box:
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                try:
                    with st.spinner("답변을 만들고 있어요…"):
                        answer = process_question(prompt, st.session_state.model_history)
                except Exception as error:
                    answer = ai_error_message(error, "답변을 만들지 못했어요. 잠시 후 다시 질문해 주세요.")
        st.session_state.messages.append({"role": "assistant", "content": answer})
        st.rerun()


def render_chat_page() -> None:
    st.markdown("## AI에게 질문하기")
    if AI_FEATURES_PAUSED:
        st.info(AI_PAUSED_MESSAGE)
    if chat_v2_available():
        render_chat_v2()
    else:
        render_public_chat()


keep_widget_values()
st.session_state["_unsaved"] = None  # 이번 화면을 그리면서 다시 채운다(이동 버튼의 콜백은 바로 전 화면 기준으로 본다)
st.session_state["_unsaved_form"] = False  # 고소장 작성 칸에 적던 내용(새로고침 경고에만 쓴다)
render_upcoming_privacy_notice()
account_identity, account_method = current_account_label(st.session_state.supabase_session)
current_page = render_top_bar(account_identity, account_method)
if current_page == "account":
    render_account_page(account_identity, account_method)
else:
    {
        "home": render_home_page, "chat": render_chat_page, "case": render_case_page,
        "cases": render_cases_page, "help": render_help_page,
    }[current_page]()
if st.session_state.get("_pending_nav"):
    render_leave_dialog()
_unsaved_guard_component(key="unsaved_guard", data={"dirty": bool(st.session_state.get("_unsaved") or st.session_state.get("_unsaved_form"))}, height=0)
render_footer()
# 다른 화면(또는 고소장 작성 단계·사건 열기·음성 대화)으로 바뀌었을 때만 새 화면의 맨 위로 올린다.
current_view = "|".join(str(x) for x in (
    current_page, bool(st.session_state.get("case_review")), bool(st.session_state.get("case_draft")),
    bool(st.session_state.get("pending_draft_entry")), st.session_state.get("open_case_id"),
    st.session_state.get("chat_mode"),
    st.session_state.get(f"ws_step_{st.session_state.get('open_case_id') or st.session_state.get('case_draft_id')}"),
))
if st.session_state.get("_last_view") != current_view:
    st.session_state._last_view = current_view
    _scroll_top_component(key="scroll_top", data={"view": current_view}, height=0)
