import os
import json
import re
import time
import datetime
import unicodedata
import uuid
import hashlib
from io import BytesIO
import streamlit as st
from docx import Document
from docx.shared import Mm, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from google import genai
from google.genai import types
from supabase import create_client, Client
from supabase_auth.errors import AuthApiError
from calendar_test import get_calendar_service
MEMORY_FILE = "memory.json"
MEMORY_TRIGGERS = [unicodedata.normalize("NFC", t) for t in ["기억해", "저장해"]]

SCHEDULE_FILE = "schedules.json"
CANCEL_TRIGGERS = [unicodedata.normalize("NFC", t) for t in ["취소", "삭제"]]
DATE_WORDS = ["오늘", "내일", "모레", "이번주", "이번 주", "다음주", "다음 주"]

CASES_TABLE = "cases"
CASE_FIELDS = [
    ("사건 유형", "case_type"),
    ("신고인 정보", "reporter_info"),
    ("상대방 정보", "suspect_info"),
    ("사건 발생 일시", "incident_datetime"),
    ("사건 발생 장소", "incident_location"),
    ("구체적인 사건 경위", "incident_description"),
    ("피해 내용", "damage_details"),
    ("증거 자료", "evidence"),
    ("목격자 유무", "witness"),
    ("처벌 의사", "punishment_intent"),
]
CASE_LONG_TEXT_FIELDS = {"reporter_info", "suspect_info", "incident_description", "damage_details", "evidence"}
CASE_FIELD_MAX_CHARS = {
    "case_type": 100,
    "reporter_info": 1000,
    "suspect_info": 1000,
    "incident_datetime": 100,
    "incident_location": 300,
    "incident_description": 8000,
    "damage_details": 4000,
    "evidence": 4000,
    "witness": 1000,
    "punishment_intent": 500,
}
DRAFT_DISCLAIMER = "이 문서는 참고용 초안이며 제출 전 법률 전문가의 검토가 필요합니다."

PASSWORD_RESET_ENABLED = False  # 기본 이메일 템플릿(커스텀 SMTP 미설정)에서는 재설정 링크가 동작하지 않음

UNSUPPORTED_ASSISTANT_MESSAGE = "죄송합니다. 현재 고소음이는 일정 관리·메모 기억 기능을 지원하지 않습니다. 법률 상담이나 사건 정리를 도와드릴게요."

PRIVACY_POLICY_ENABLED = os.environ.get("PRIVACY_POLICY_ENABLED") == "true"
PRIVACY_OPERATOR_NAME = os.environ.get("PRIVACY_OPERATOR_NAME")
PRIVACY_CONTACT_EMAIL = os.environ.get("PRIVACY_CONTACT_EMAIL")
PRIVACY_POLICY_EFFECTIVE_DATE = os.environ.get("PRIVACY_POLICY_EFFECTIVE_DATE")
TERMS_EFFECTIVE_DATE = "2026-09-15"


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

DOCX_TITLE = "고소장"
DOCX_SECTION_HEADERS = [
    "고소인", "피고소인", "고소 취지", "범죄 사실", "고소 이유",
    "증거자료", "목격자", "처벌 의사", "작성일",
]


def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_memory(memory):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)


def extract_memory_text(question):
    text = question
    for trigger in MEMORY_TRIGGERS:
        text = re.sub(trigger + r"\S*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.!?？")


def load_schedules():
    if os.path.exists(SCHEDULE_FILE):
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_schedules(schedules):
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(schedules, f, ensure_ascii=False, indent=2)


def get_supabase_anon_client() -> Client:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY 환경변수가 설정되지 않았습니다.")
    return create_client(supabase_url, supabase_key)


def get_authed_client() -> Client:
    session = st.session_state.get("supabase_session")
    if not session:
        raise RuntimeError("로그인이 필요합니다.")
    client = get_supabase_anon_client()
    client.postgrest.auth(session["access_token"])
    return client


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
            return "이메일 인증이 완료되지 않았습니다. 받은 편지함과 스팸함에서 인증 메일을 확인해 주세요."
        if code in ("user_already_exists", "email_exists"):
            return "이미 가입된 이메일입니다. 로그인하시거나 비밀번호를 재설정해 주세요."
        if code in ("over_email_send_rate_limit", "over_request_rate_limit") or status == 429:
            return "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
        if code == "weak_password":
            return "비밀번호가 너무 약합니다. 6자 이상의 다른 비밀번호를 사용해 주세요."
        if code in ("validation_failed", "email_address_invalid"):
            return "이메일 형식이 올바르지 않습니다."
        if code == "otp_expired":
            return "인증 링크가 만료되었거나 이미 사용되었습니다. 인증 메일을 다시 요청해 주세요."
        if code in ("session_not_found", "flow_state_not_found", "flow_state_expired"):
            return "인증 세션이 만료되었습니다. 처음부터 다시 시도해 주세요."
        if code == "same_password":
            return "이전과 동일한 비밀번호로는 변경할 수 없습니다."
        if code == "signup_disabled":
            return "현재 회원가입이 제한되어 있습니다. 잠시 후 다시 시도해 주세요."
    return "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."


def set_auth_flash(level, text) -> None:
    st.session_state.auth_flash = {"level": level, "text": text}


def render_auth_flash() -> None:
    flash = st.session_state.get("auth_flash")
    if flash:
        if flash["level"] == "success":
            st.success(flash["text"])
        else:
            st.error(flash["text"])
        st.session_state.auth_flash = None


def session_to_dict(session) -> dict:
    return {
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "user_id": session.user.id,
        "email": session.user.email,
        "expires_at": session.expires_at,
    }


def session_from_auth_response(auth_response) -> dict:
    return session_to_dict(auth_response.session)


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
    st.session_state.messages = []
    st.session_state.model_history = []
    st.session_state.case_workflow_user_id = None


def perform_logout() -> None:
    session = st.session_state.get("supabase_session")
    if session:
        try:
            get_session_authed_client(session).auth.sign_out()
        except Exception as error:
            log_auth_error(error)
    st.session_state.supabase_session = None
    clear_user_session_data()
    st.session_state.auth_view = "login"
    st.rerun()


def ensure_active_session():
    session = st.session_state.get("supabase_session")
    if not session:
        return None
    expires_at = session.get("expires_at")
    if expires_at and time.time() < expires_at - 60:
        return session
    try:
        client = get_supabase_anon_client()
        refreshed = client.auth.refresh_session(session["refresh_token"])
        if not getattr(refreshed, "session", None) or not getattr(refreshed, "user", None):
            raise RuntimeError("empty refresh response")
        new_session = session_from_auth_response(refreshed)
        st.session_state.supabase_session = new_session
        return new_session
    except Exception as error:
        log_auth_error(error)
        st.session_state.supabase_session = None
        clear_user_session_data()
        st.session_state.auth_view = "login"
        set_auth_flash("error", "세션이 만료되었습니다. 다시 로그인해 주세요.")
        return None


def handle_auth_query_params() -> None:
    token_hash = st.query_params.get("token_hash")
    type_ = st.query_params.get("type")
    if not token_hash or not type_:
        return

    st.query_params.clear()

    try:
        auth_client = get_supabase_anon_client()
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


DUPLICATE_SUBMIT_WINDOW_SECONDS = 10


def compute_case_fingerprint(user_id: str, field_values: dict) -> str:
    normalized = {key: (field_values.get(key) or "").strip() for _, key in CASE_FIELDS}
    payload = json.dumps({"user_id": user_id, "fields": normalized}, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def save_case(case_entry):
    client = get_authed_client()
    client.table(CASES_TABLE).insert(case_entry).execute()


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
        return [], "사건 데이터를 불러오는 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."
    return response.data or [], None


def delete_case(case_id):
    session = st.session_state.get("supabase_session")
    if not session or not session.get("user_id"):
        raise RuntimeError("로그인이 필요합니다.")
    client = get_authed_client()
    client.table(CASES_TABLE).delete().eq("case_id", case_id).eq("user_id", session["user_id"]).execute()


def build_draft_prompt(case, today_str):
    fields_text = "\n".join(
        f"- {label}: {case.get(key) or '(입력 안 됨)'}"
        for label, key in CASE_FIELDS
    )

    return (
        "아래는 사용자가 실제로 입력한 사건 정보입니다. 이 정보만 근거로 고소장 초안을 작성하세요.\n"
        "규칙:\n"
        "1. 사용자가 입력한 객관적인 사실과 사용자의 주장만 작성하세요. 사용자가 제공하지 않은 내용을 추측하거나 새로 만들어내지 마세요.\n"
        "2. 확인되지 않은 범죄 고의, 기망 의도, 불법영득 의사, 범죄 성립 여부를 확정적으로 표현하지 마세요.\n"
        "3. 필요한 경우 \"고소인은 ~라고 판단합니다\", \"~한 것으로 의심됩니다\", \"수사를 통해 확인해 주시기 바랍니다\"와 같이 고소인의 주장·의심임을 분명히 하는 표현을 사용하세요.\n"
        "4. 사용자가 직접 말하지 않은 법조문, 범행 동기, 고의, 추가 피해 내용을 만들어내지 마세요.\n"
        "5. 특정 항목에 필요한 정보가 제공되지 않았거나 불충분하면 반드시 '확인 필요'라고만 표시하세요.\n"
        "6. \"고소 취지\"와 \"범죄 사실\" 항목은 법원의 확정판결처럼 단정하지 말고, 고소인의 주장 형식으로 작성하세요.\n"
        "7. 다음 10개 항목을 정확히 이 순서로, 각 항목을 소제목으로 구분해 작성하세요:\n"
        "   문서 제목 / 고소인 / 피고소인 / 고소 취지 / 범죄 사실 / 고소 이유 / 증거자료 / 목격자 / 처벌 의사 / 작성일\n"
        f"8. 작성일은 반드시 {today_str}로 표기하세요.\n\n"
        f"[입력된 사건 정보]\n{fields_text}"
    )


def generate_case_draft(case):
    kst = datetime.timezone(datetime.timedelta(hours=9))
    today_str = datetime.datetime.now(kst).date().isoformat()

    prompt = build_draft_prompt(case, today_str)
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
    )

    return f"{response.text}\n\n{DRAFT_DISCLAIMER}"


def build_case_draft_docx(draft_text):
    document = Document()

    section = document.sections[0]
    section.page_height = Mm(297)
    section.page_width = Mm(210)
    section.top_margin = Mm(20)
    section.bottom_margin = Mm(20)
    section.left_margin = Mm(20)
    section.right_margin = Mm(20)

    normal_style = document.styles["Normal"]
    normal_style.font.size = Pt(11)
    normal_style.paragraph_format.line_spacing = 1.5

    title_paragraph = document.add_paragraph()
    title_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title_paragraph.add_run(DOCX_TITLE)
    title_run.bold = True
    title_run.font.size = Pt(20)

    document.add_paragraph()

    skip_next_line = False
    for raw_line in draft_text.splitlines():
        line = raw_line.strip()

        if not line:
            continue
        if line == "문서 제목":
            skip_next_line = True
            continue
        if skip_next_line:
            skip_next_line = False
            continue

        if line in DOCX_SECTION_HEADERS:
            heading_paragraph = document.add_paragraph()
            heading_run = heading_paragraph.add_run(line)
            heading_run.bold = True
            heading_run.font.size = Pt(13)
        else:
            document.add_paragraph(line)

    buffer = BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return buffer


def get_google_calendar_schedule(question):
    target_date = resolve_date(question)

    if target_date is None:
        target_date = datetime.date.today()

    kst = datetime.timezone(datetime.timedelta(hours=9))

    start_time = datetime.datetime.combine(
        target_date,
        datetime.time.min,
        tzinfo=kst
    )

    end_time = start_time + datetime.timedelta(days=1)

    service = get_calendar_service()

    result = service.events().list(
        calendarId="primary",
        timeMin=start_time.isoformat(),
        timeMax=end_time.isoformat(),
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    events = result.get("items", [])

    if not events:
        return f"{target_date} 일정이 없습니다."

    schedule_text = []

    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        title = event.get("summary", "제목 없음")
        schedule_text.append(f"- {start} | {title}")

    return "\n".join(schedule_text)
def has_date_cue(question):
    return (
        any(word in question for word in DATE_WORDS)
        or re.search(r"\d{1,2}월\s*\d{1,2}일", question)
        or re.search(r"\d{1,2}시", question)
    )


def resolve_date(question, today=None):
    today = today or datetime.date.today()
    if "모레" in question:
        return today + datetime.timedelta(days=2)
    if "내일" in question:
        return today + datetime.timedelta(days=1)
    if "오늘" in question:
        return today
    m = re.search(r"(\d{1,2})월\s*(\d{1,2})일", question)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        candidate = datetime.date(today.year, month, day)
        if candidate < today:
            candidate = datetime.date(today.year + 1, month, day)
        return candidate
    return None


def resolve_date_range(question, today=None):
    today = today or datetime.date.today()
    if "다음주" in question or "다음 주" in question:
        this_monday = today - datetime.timedelta(days=today.weekday())
        next_monday = this_monday + datetime.timedelta(days=7)
        return next_monday, next_monday + datetime.timedelta(days=6)
    if "이번주" in question or "이번 주" in question:
        this_monday = today - datetime.timedelta(days=today.weekday())
        return this_monday, this_monday + datetime.timedelta(days=6)
    single_date = resolve_date(question, today)
    if single_date:
        return single_date, single_date
    return None, None


def resolve_time(question):
    m = re.search(r"(오전|오후)?\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?", question)    
    if not m:
        return None

    period = m.group(1)
    hour = int(m.group(2))
    minute = int(m.group(3)) if m.group(3) else 0

    if period == "오후" and hour != 12:
        hour += 12
    if period == "오전" and hour == 12:
        hour = 0

    return f"{hour:02d}:{minute:02d}"    


def strip_common_words(text):
    for word in DATE_WORDS:
        text = text.replace(word, "")
    text = re.sub(r"\d{1,2}월\s*\d{1,2}일", "", text)
    text = re.sub(r"(오전|오후)?\s*\d{1,2}시(\s*\d{1,2}분)?", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.!?에는를을이가")


def extract_task_text(question):
    text = question
    for trigger in MEMORY_TRIGGERS:
        text = re.sub(trigger + r"\S*", "", text)
    return strip_common_words(text)


def extract_cancel_keyword(question):
    text = question
    for trigger in CANCEL_TRIGGERS:
        text = re.sub(trigger + r"\S*", "", text)
    text = text.replace("일정", "")
    return strip_common_words(text)


def is_cancel_request(question):
    return any(trigger in question for trigger in CANCEL_TRIGGERS)


def is_schedule_add_request(question):
    return any(word in question for word in ("추가", "등록", "저장", "잡아", "만들어", "넣어")) and ("일정" in question or has_date_cue(question))
def is_schedule_query(question):
    return "일정" in question and not is_cancel_request(question)


def format_schedule(item):
    time_part = f" {item['time']}" if item["time"] else ""
    return f"{item['date']}{time_part} {item['task']}"


def handle_schedule_add(question, schedules):
    date = resolve_date(question)
    time = resolve_time(question)
    task = extract_task_text(question)
    print(f"[확인] date={date}, time={time}, task={task!r}")
    if not date or not task:
        print("[일정 저장 실패] 날짜나 내용을 정확히 알 수 없습니다.")
        return "사용자가 일정을 저장하려 했지만 날짜 또는 내용을 인식하지 못했습니다."

    entry = {
        "date": date.isoformat(),
        "time": time,
        "task": task
    }

    schedules.append(entry)
    save_schedules(schedules)

    try:
        service = get_calendar_service()

        if time:
            hour, minute = map(int, time.split(":")[:2])

            start_dt = datetime.datetime(
                date.year, date.month, date.day,
                hour, minute,
                tzinfo=datetime.timezone(datetime.timedelta(hours=9))
            )

            end_dt = start_dt + datetime.timedelta(hours=1)

            event = {
                "summary": task,
                "start": {
                    "dateTime": start_dt.isoformat(),
                    "timeZone": "Asia/Seoul"
                },
                "end": {
                    "dateTime": end_dt.isoformat(),
                    "timeZone": "Asia/Seoul"
                }
            }

        else:
            event = {
                "summary": task,
                "start": {
                    "date": date.isoformat()
                },
                "end": {
                    "date": (date + datetime.timedelta(days=1)).isoformat()
                }
            }

        service.events().insert(
            calendarId="primary",
            body=event
        ).execute()

        return f"일정을 저장했고 구글 캘린더에도 등록했습니다: {format_schedule(entry)}"

    except Exception as e:
        print(f"[구글 캘린더 저장 실패] {e}")
        return f"일정은 저장했지만 구글 캘린더 등록은 실패했습니다: {format_schedule(entry)}"

def handle_schedule_cancel(question, schedules):
    date = resolve_date(question)
    keyword = extract_cancel_keyword(question)

    if not keyword:
        return "어떤 일정을 취소할지 일정 이름을 말씀해 주세요."

    local_matches = [
        s for s in schedules
        if (date is None or s["date"] == date.isoformat())
        and keyword in s["task"]
    ]

    for item in local_matches:
        schedules.remove(item)

    if local_matches:
        save_schedules(schedules)

    kst = datetime.timezone(datetime.timedelta(hours=9))
    target_date = date or datetime.datetime.now(kst).date()

    start_time = datetime.datetime.combine(
        target_date,
        datetime.time.min,
        tzinfo=kst
    )

    if date is None:
        end_time = start_time + datetime.timedelta(days=365)
    else:
        end_time = start_time + datetime.timedelta(days=1)

    google_matches = []

    try:
        service = get_calendar_service()
        result = service.events().list(
            calendarId="primary",
            timeMin=start_time.isoformat(),
            timeMax=end_time.isoformat(),
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        for event in result.get("items", []):
            title = event.get("summary", "")
            if keyword in title:
                service.events().delete(
                    calendarId="primary",
                    eventId=event["id"]
                ).execute()
                google_matches.append(title)

    except Exception as e:
        print(f"[구글 캘린더 삭제 실패] {e}")

    removed_items = [format_schedule(item) for item in local_matches]
    removed_items.extend(google_matches)

    if not removed_items:
        return "조건에 맞는 일정을 찾지 못했습니다. 어떤 일정인지 다시 말씀해 주세요."

    removed_text = ", ".join(dict.fromkeys(removed_items))
    print(f"[일정 삭제됨] {removed_text}")
    return f"요청하신 일정을 삭제했습니다: {removed_text}"


def handle_schedule_query(question, schedules):
    start, end = resolve_date_range(question)
    if start and end:
        matches = [s for s in schedules if start.isoformat() <= s["date"] <= end.isoformat()]
    else:
        today_str = datetime.date.today().isoformat()
        matches = [s for s in schedules if s["date"] >= today_str]
    matches.sort(key=lambda s: (s["date"], s["time"] or ""))
    if not matches:
        print("[조회된 일정 없음]")
        return "요청한 기간에 해당하는 저장된 일정이 없습니다. 없다고 자연스럽게 답변하세요."
    listing = "\n".join(f"- {format_schedule(m)}" for m in matches)
    print(f"[조회된 일정]\n{listing}")
    return f"다음은 요청한 기간에 해당하는 저장된 일정입니다. 이 내용만 근거로 답변하세요:\n{listing}"


def build_system_instruction(memory, schedule_notice):
    parts = [SERVICE_IDENTITY_INSTRUCTION]
    if memory:
        lines = "\n".join(f"- {item}" for item in memory)
        parts.append(
            "다음은 사용자가 이전에 기억해달라고 요청한 장기 기억입니다. "
            "관련된 질문에 답할 때 반드시 참고하세요:\n" + lines
        )
    if schedule_notice:
        parts.append(schedule_notice)
    return "\n\n".join(parts)


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

    history.append({"role": "user", "parts": [{"text": question}]})
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=history[-16:],
        config=types.GenerateContentConfig(
            system_instruction=build_system_instruction([], None)
        ),
    )

    history.append({"role": "model", "parts": [{"text": response.text}]})
    if len(history) > 16:
        history[:] = history[-16:]
    return response.text


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
}

#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
[data-testid="stToolbar"] {visibility: hidden;}
[data-testid="stAppDeployButton"] {display: none;}
</style>
"""


# 법률 검토 전 초안 — 실제 배포 전 검토 필요
def build_privacy_policy_text():
    return f"""
### 개인정보처리방침

{PRIVACY_OPERATOR_NAME}(이하 "운영자")는 개인이 운영하는 서비스(사업자등록 전)로서 "고소음이"(이하 "서비스")를 제공하며,
이용자의 개인정보를 아래와 같이 처리합니다.

**1. 수집하는 개인정보 항목**
- 회원가입 시: 이메일 주소, 인증을 위한 비밀번호
- 사건 작성 시: 사건 유형, 신고인 정보, 상대방 정보, 사건 발생 일시, 사건 발생 장소, 사건 경위, 피해 내용, 증거자료, 목격자 유무, 처벌 의사 (총 10개 항목)
  - 이 중 "상대방 정보"는 이용자 본인이 아닌 제3자에 관한 정보가 포함될 수 있습니다.

**2. 개인정보의 수집·이용 목적**
- 회원가입, 로그인, 계정 관리
- 이용자가 입력한 사건 정보의 저장·조회·삭제
- AI를 이용한 상담 응답 제공 및 고소장 초안 생성

**3. 개인정보의 보유·이용 기간**
- 회원 정보(이메일, 비밀번호): 회원탈퇴 시까지 보유하며, 탈퇴 확인 후 삭제합니다.
- 사건 정보: 이용자가 직접 삭제하거나 회원탈퇴를 요청할 때까지 보유합니다.

**4. 개인정보 처리 위탁 및 정보 전송**
- Supabase — 회원 인증 및 사건 정보 저장. 데이터베이스와 인증 정보는 AWS 서울 리전에 저장되어 주로 처리됩니다.
  다만 로그·모니터링·이메일 발송 등 부가 처리 과정에서 미국·싱가포르 소재 하위처리자를 거칠 수 있습니다.
  비밀번호는 Supabase Auth에서 암호화된 인증정보로 처리되며 운영자는 원문을 열람할 수 없습니다.
- Google Gemini API(유료 API) — 고소장 초안 생성 및 AI 상담 응답 생성을 위해 입력하신 정보가 처리 목적으로 전송됩니다.
  유료 API 이용 시 입력·응답 내용은 제품 개선(모델 학습 포함)에 사용되지 않으며, 오남용 방지 등을 위한 로그가 기본 최대 55일간 보관될 수 있습니다.

**5. 개인정보의 국외 이전**
- Supabase의 로그·모니터링·이메일 발송 등 부가 처리는 미국·싱가포르 소재 하위처리자를 거칠 수 있습니다.
- Google Gemini API로 전송되는 정보는 Google 또는 그 대리인이 시설을 운영하는 국가에서 일시 저장·캐시될 수 있습니다. 구체적인 처리 국가와 계약 법인은 현재 확인되지 않아 확인되는 대로 갱신하겠습니다.

**6. 이용자의 권리**
- 저장된 사건의 열람·삭제: 서비스 내 "내 사건" 화면에서 직접 삭제할 수 있습니다.
- 회원탈퇴 및 전체 정보 삭제: 아래 문의처로 이메일을 보내 요청하시면, 확인 후 계정과 저장된 모든 사건 정보를 삭제합니다. 현재는 이메일 요청을 받아 순차적으로 처리하고 있습니다.

**7. 이용 연령 제한**
본 서비스는 만 14세 이상만 이용할 수 있습니다.

**8. 안전한 정보 입력 안내**
상대방 등 제3자의 정보는 이용자가 적법하게 제공할 수 있는 범위에서 필요한 최소한만 입력해 주세요.
주민등록번호, 계좌 비밀번호, 카드번호, 인증번호(OTP), 불필요한 주소·연락처 등은 입력하지 마세요.

**9. 문의처**
개인정보 관련 문의, 열람·정정·삭제 요청은 아래로 연락해 주세요.
- 운영자: {PRIVACY_OPERATOR_NAME}
- 이메일: {PRIVACY_CONTACT_EMAIL}

**10. 시행일**
이 개인정보처리방침은 {PRIVACY_POLICY_EFFECTIVE_DATE}부터 적용됩니다.
"""


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


def render_brand_header():
    st.markdown(
        '<div class="dgo-hero">'
        '<div class="dgo-hero-title">⚖️ 고소음이</div>'
        '<div class="dgo-hero-subtitle">어려운 피해 상황을 정리하고, 고소장 초안 작성을 돕는 AI 법률정보 도우미</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def render_footer():
    st.markdown(
        '<div class="dgo-footer">'
        '고소음이는 법률정보와 문서 작성을 돕는 서비스이며, 변호사의 법률자문을 대체하지 않습니다.'
        '</div>',
        unsafe_allow_html=True,
    )


def render_signup_guide():
    st.markdown(
        "**가입 절차**\n\n"
        "① 이메일 입력 → ② 인증메일 확인(스팸함 포함) → ③ 앱으로 돌아와 로그인"
    )


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
                st.error("앱 주소 설정이 필요합니다. 관리자에게 문의해 주세요.")
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
        auth_client = get_supabase_anon_client()
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
        auth_client = get_supabase_anon_client()
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
            st.error("앱 주소 설정이 필요합니다. 관리자에게 문의해 주세요.")
        else:
            try:
                auth_client = get_supabase_anon_client()
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

if not st.session_state.supabase_session:
    render_brand_header()
    render_auth_flash()

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
            with st.form("login_form"):
                login_email = st.text_input("이메일", key="login_email")
                login_password = st.text_input("비밀번호", type="password", key="login_password")
                login_submitted = st.form_submit_button("로그인")

            if login_submitted:
                try:
                    auth_client = get_supabase_anon_client()
                    auth_response = auth_client.auth.sign_in_with_password(
                        {"email": login_email, "password": login_password}
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
                        st.error("앱 주소 설정이 필요합니다. 관리자에게 문의해 주세요.")
                    else:
                        try:
                            auth_client = get_supabase_anon_client()
                            auth_client.auth.sign_up({
                                "email": signup_email,
                                "password": signup_password,
                                "options": {"email_redirect_to": base_url},
                            })
                            st.session_state.signup_sent_email = signup_email
                            st.session_state.signup_resend_last_sent_at = None
                            st.session_state.auth_view = "signup_sent"
                            st.rerun()
                        except Exception as error:
                            log_auth_error(error)
                            st.error(map_auth_error(error))

    render_footer()
    st.stop()

if ensure_active_session() is None:
    st.rerun()
render_auth_flash()

current_user_id = st.session_state.supabase_session["user_id"]
if st.session_state.get("case_workflow_user_id") != current_user_id:
    clear_user_session_data()
    st.session_state.case_workflow_user_id = current_user_id

with st.sidebar:
    st.markdown("**고소음이 데모**")
    st.caption(st.session_state.supabase_session.get("email") or "")
    if st.button("로그아웃", key="logout_btn_sidebar"):
        perform_logout()

    if st.button("개인정보처리방침 보기", key="privacy_policy_view_btn_sidebar"):
        render_privacy_policy_dialog()

    if st.button("이용약관 보기", key="terms_of_service_view_btn_sidebar"):
        render_terms_of_service_dialog()

    if PRIVACY_CONTACT_EMAIL:
        st.caption(f"회원탈퇴를 원하시면 {PRIVACY_CONTACT_EMAIL}로 가입하신 이메일을 포함해 요청해 주세요. 확인 후 계정과 저장된 사건을 삭제해 드립니다.")
    else:
        st.caption("회원탈퇴 절차를 준비 중입니다.")

render_brand_header()

with st.expander("계정 및 개인정보 메뉴"):
    if st.button("개인정보처리방침 보기", key="privacy_policy_view_btn_main"):
        render_privacy_policy_dialog()

    if st.button("이용약관 보기", key="terms_of_service_view_btn_main"):
        render_terms_of_service_dialog()

    if PRIVACY_CONTACT_EMAIL:
        st.caption(f"회원탈퇴를 원하시면 {PRIVACY_CONTACT_EMAIL}로 가입하신 이메일을 포함해 요청해 주세요. 확인 후 계정과 저장된 사건을 삭제해 드립니다.")
    else:
        st.caption("회원탈퇴 절차를 준비 중입니다.")

    if st.button("로그아웃", key="logout_btn_main"):
        perform_logout()

card_col1, card_col2, card_col3 = st.columns(3)
with card_col1:
    st.markdown('<div class="dgo-card">📝<b>피해 내용 정리</b></div>', unsafe_allow_html=True)
with card_col2:
    st.markdown('<div class="dgo-card">📄<b>고소장 초안 생성</b></div>', unsafe_allow_html=True)
with card_col3:
    st.markdown('<div class="dgo-card">🗂️<b>사건 저장 및 관리</b></div>', unsafe_allow_html=True)

tab_chat, tab_case, tab_case_list = st.tabs(["AI 상담", "고소장 작성", "내 사건"])

with tab_chat:
    st.caption("피해 상황을 편하게 이야기하면 핵심 사실을 정리할 수 있도록 도와드려요.")

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("무엇을 도와드릴까요?", max_chars=3000):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        try:
            answer = process_question(prompt, st.session_state.model_history)
        except Exception:
            answer = "답변 생성 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."

        st.session_state.messages.append({"role": "assistant", "content": answer})
        with st.chat_message("assistant"):
            st.markdown(answer)

with tab_case:
    st.caption("사건 정보를 입력하면 고소장 초안을 자동으로 생성합니다.")
    st.markdown(
        "**사건 저장 시 수집하는 개인정보**\n"
        "- 저장 항목: 사건 유형, 신고인 정보, 상대방 정보, 사건 발생 일시, 사건 발생 장소, "
        "사건 경위, 피해 내용, 증거자료, 목격자 유무, 처벌 의사 (총 10개 항목)\n"
        "- 목적: 사건 저장·관리, AI 상담 및 고소장 초안 생성\n"
        "- 보관기간: 이용자가 직접 삭제하거나 회원탈퇴할 때까지\n"
        "- 동의를 거부할 수 있으나, 거부 시 사건 저장과 고소장 초안 생성이 제한됩니다."
    )
    st.warning(
        "입력하신 사건 정보(신고인·상대방 정보 등 개인정보 포함 가능)는 Supabase 데이터베이스에 저장되고, "
        "고소장 초안 생성 시 Google Gemini API로 전송되어 처리됩니다."
    )
    st.info(
        "상대방 등 제3자의 정보는 이용자가 적법하게 제공할 수 있는 범위에서 필요한 최소한만 입력해 주세요. "
        "주민등록번호, 계좌 비밀번호, 카드번호, 인증번호(OTP), 불필요한 주소·연락처는 입력하지 마세요."
    )

    if st.session_state.pending_draft_entry:
        st.warning(
            "사건은 저장되었습니다. 다만 고소장 초안 생성에 실패했습니다. "
            "새 사건을 제출하기 전에 초안을 다시 생성하거나 이 사건을 보류해 주세요."
        )
        col_retry, col_cancel = st.columns(2)
        with col_retry:
            if st.button("초안만 다시 생성", key="retry_draft_button"):
                entry = st.session_state.pending_draft_entry
                try:
                    st.session_state.case_draft = generate_case_draft(entry)
                    st.session_state.case_draft_id = entry["case_id"]
                    st.session_state.pending_draft_entry = None
                    st.rerun()
                except Exception:
                    st.error("고소장 초안 생성에 다시 실패했습니다. 잠시 후 다시 시도해 주세요.")
        with col_cancel:
            if st.button("이 사건 보류하고 새로 시작", key="cancel_pending_draft_button"):
                st.session_state.pending_draft_entry = None
                st.rerun()
    else:
        with st.form("case_form"):
            field_values = {}
            for label, key in CASE_FIELDS:
                if key in CASE_LONG_TEXT_FIELDS:
                    field_values[key] = st.text_area(label, max_chars=CASE_FIELD_MAX_CHARS[key])
                else:
                    field_values[key] = st.text_input(label, max_chars=CASE_FIELD_MAX_CHARS[key])

            case_data_consent = st.checkbox(
                "[필수] 위 안내를 확인했으며 사건 정보 저장·전송에 동의합니다.",
                key="case_data_consent",
            )
            submitted = st.form_submit_button("사건 저장 및 고소장 초안 생성")

        if submitted:
            if not privacy_config_ready():
                st.error("현재 서비스 운영 정보 설정이 진행 중이라 잠시 사건을 저장할 수 없습니다. 잠시 후 다시 시도해 주세요.")
            elif not case_data_consent:
                st.error("사건을 저장하려면 데이터 저장·전송 안내에 동의해 주세요.")
            else:
                missing_labels = [
                    label for label, key in CASE_FIELDS
                    if not field_values[key].strip()
                ]
                over_limit_errors = [
                    f"{label}은 최대 {CASE_FIELD_MAX_CHARS[key]}자까지 입력할 수 있습니다."
                    for label, key in CASE_FIELDS
                    if len(field_values[key]) > CASE_FIELD_MAX_CHARS[key]
                ]

                if missing_labels:
                    st.error("다음 항목을 입력해 주세요: " + ", ".join(missing_labels))
                elif over_limit_errors:
                    for message in over_limit_errors:
                        st.error(message)
                else:
                    user_id = st.session_state.supabase_session["user_id"]
                    fingerprint = compute_case_fingerprint(user_id, field_values)

                    is_duplicate = (
                        st.session_state.last_saved_fingerprint == fingerprint
                        and st.session_state.last_saved_at is not None
                        and (time.time() - st.session_state.last_saved_at) < DUPLICATE_SUBMIT_WINDOW_SECONDS
                    )

                    case_entry = None
                    if is_duplicate:
                        case_entry = st.session_state.last_saved_case_entry
                        st.info("방금 제출한 동일한 사건이 이미 저장되어 있어 다시 저장하지 않았습니다.")
                    else:
                        kst = datetime.timezone(datetime.timedelta(hours=9))
                        new_entry = {
                            "case_id": str(uuid.uuid4()),
                            "user_id": user_id,
                            "original_request": "Streamlit 고소장 작성 폼에서 제출됨",
                            **{key: field_values[key].strip() for _, key in CASE_FIELDS},
                            "created_at": datetime.datetime.now(kst).isoformat(),
                        }

                        try:
                            save_case(new_entry)
                        except Exception:
                            st.error("사건 저장 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.")
                        else:
                            st.session_state.last_saved_case_entry = new_entry
                            st.session_state.last_saved_fingerprint = fingerprint
                            st.session_state.last_saved_at = time.time()
                            st.session_state.case_draft = None
                            st.session_state.case_draft_id = None
                            case_entry = new_entry

                    if case_entry is not None:
                        already_drafted = (
                            st.session_state.case_draft_id == case_entry["case_id"]
                            and st.session_state.case_draft
                        )
                        if not already_drafted:
                            try:
                                st.session_state.case_draft = generate_case_draft(case_entry)
                                st.session_state.case_draft_id = case_entry["case_id"]
                            except Exception:
                                st.session_state.pending_draft_entry = case_entry
                                st.rerun()

    if st.session_state.case_draft:
        st.subheader("생성된 고소장 초안")
        st.text_area("초안 내용", st.session_state.case_draft, height=400)

        col_txt_download, col_docx_download = st.columns(2)

        with col_txt_download:
            st.download_button(
                "TXT 다운로드",
                data=st.session_state.case_draft,
                file_name=f"case_draft_{st.session_state.case_draft_id}.txt",
                mime="text/plain",
            )

        with col_docx_download:
            docx_buffer = build_case_draft_docx(st.session_state.case_draft)
            st.download_button(
                "Word 다운로드",
                data=docx_buffer,
                file_name=f"case_draft_{st.session_state.case_draft_id}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

with tab_case_list:
    st.caption("저장된 사건을 조회하고 삭제할 수 있습니다.")
    st.caption("계정과 사건을 모두 삭제(회원탈퇴)하려면 사이드바의 안내를 참고해 주세요.")

    cases, load_error = load_cases_safe()

    if load_error:
        st.error(load_error)
    elif not cases:
        st.info("저장된 사건이 없습니다.")
    else:
        for case in cases:
            case_id = case.get("case_id", "")

            st.markdown(
                f"**사건 유형**: {case.get('case_type', '확인 필요')} | "
                f"**신고인**: {case.get('reporter_info', '확인 필요')} | "
                f"**상대방**: {case.get('suspect_info', '확인 필요')} | "
                f"**작성일시**: {case.get('created_at', '확인 필요')} | "
                f"**case_id**: {case_id}"
            )

            with st.expander("자세히 보기"):
                for label, key in CASE_FIELDS:
                    st.markdown(f"**{label}**: {case.get(key, '확인 필요')}")
                st.markdown(f"**작성일시**: {case.get('created_at', '확인 필요')}")
                st.markdown(f"**case_id**: {case_id}")

            confirm_delete = st.checkbox("이 사건 삭제", key=f"confirm_delete_{case_id}")

            if st.button("삭제하기", key=f"delete_button_{case_id}"):
                if not confirm_delete:
                    st.warning("삭제 확인에 체크해 주세요.")
                else:
                    try:
                        delete_case(case_id)
                    except Exception:
                        st.error("사건 삭제 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.")
                    else:
                        st.success(f"사건(case_id: {case_id})을 삭제했습니다.")
                        st.rerun()

            st.divider()

render_footer()
