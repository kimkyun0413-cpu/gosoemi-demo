import os
import json
import re
import datetime
import unicodedata
import uuid
import hmac
from io import BytesIO
import streamlit as st
from docx import Document
from docx.shared import Mm, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from google import genai
from google.genai import types
from supabase import create_client, Client
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
DRAFT_DISCLAIMER = "이 문서는 참고용 초안이며 제출 전 법률 전문가의 검토가 필요합니다."

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


def get_supabase_client() -> Client:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SECRET_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SECRET_KEY 환경변수가 설정되지 않았습니다.")
    return create_client(supabase_url, supabase_key)


def save_case(case_entry):
    client = get_supabase_client()
    client.table(CASES_TABLE).insert(case_entry).execute()


def load_cases_safe():
    try:
        client = get_supabase_client()
        response = (
            client.table(CASES_TABLE)
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as error:
        return [], f"사건 데이터를 불러오는 중 문제가 발생했습니다: {error}"
    return response.data or [], None


def delete_case(case_id):
    client = get_supabase_client()
    client.table(CASES_TABLE).delete().eq("case_id", case_id).execute()


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
    memory = load_memory()
    schedules = load_schedules()
    schedule_notice = None

    if is_cancel_request(question) and ("일정" in question or has_date_cue(question)):
        schedule_notice = handle_schedule_cancel(question, schedules)

    elif is_schedule_add_request(question):
        schedule_notice = handle_schedule_add(question, schedules)

    elif (
        ("캘린더" in question)
        or ("구글 일정" in question)
        or (has_date_cue(question) and ("일정" in question or "스케줄" in question))
    ):
        schedule_notice = get_google_calendar_schedule(question)

    elif any(trigger in question for trigger in MEMORY_TRIGGERS):
        memory_text = extract_memory_text(question)
        if memory_text:
            memory.append(memory_text)
            save_memory(memory)
            return f"기억했습니다: {memory_text}"
    elif is_schedule_query(question):
        schedule_notice = handle_schedule_query(question, schedules)

    if schedule_notice:
        return schedule_notice

    history.append({"role": "user", "parts": [{"text": question}]})
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=history,
        config=types.GenerateContentConfig(
            system_instruction=build_system_instruction(memory, schedule_notice)
        ),
    )

    history.append({"role": "model", "parts": [{"text": response.text}]})
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

#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
[data-testid="stToolbar"] {visibility: hidden;}
[data-testid="stAppDeployButton"] {display: none;}
</style>
"""


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


st.set_page_config(
    page_title="고소음이 | AI 법률정보 도우미",
    page_icon="⚖️",
    initial_sidebar_state="collapsed",
)
st.markdown(BRAND_CSS, unsafe_allow_html=True)

APP_PASSWORD = os.environ.get("APP_PASSWORD")

if not APP_PASSWORD:
    st.error("APP_PASSWORD 환경변수가 설정되지 않았습니다.")
    st.stop()

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    render_brand_header()
    password_input = st.text_input("비밀번호", type="password")
    if st.button("로그인"):
        if hmac.compare_digest(password_input, APP_PASSWORD):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

with st.sidebar:
    st.markdown("**고소음이 데모**")
    if st.button("로그아웃"):
        del st.session_state["authenticated"]
        st.rerun()

render_brand_header()

card_col1, card_col2, card_col3 = st.columns(3)
with card_col1:
    st.markdown('<div class="dgo-card">📝<b>피해 내용 정리</b></div>', unsafe_allow_html=True)
with card_col2:
    st.markdown('<div class="dgo-card">📄<b>고소장 초안 생성</b></div>', unsafe_allow_html=True)
with card_col3:
    st.markdown('<div class="dgo-card">🗂️<b>사건 저장 및 관리</b></div>', unsafe_allow_html=True)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "model_history" not in st.session_state:
    st.session_state.model_history = []
if "case_draft" not in st.session_state:
    st.session_state.case_draft = None
if "case_draft_id" not in st.session_state:
    st.session_state.case_draft_id = None

tab_chat, tab_case, tab_case_list = st.tabs(["AI 상담", "고소장 작성", "내 사건"])

with tab_chat:
    st.caption("피해 상황을 편하게 이야기하면 핵심 사실을 정리할 수 있도록 도와드려요.")

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("무엇을 도와드릴까요?"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        try:
            answer = process_question(prompt, st.session_state.model_history)
        except Exception as error:
            answer = f"오류가 발생했습니다: {error}"

        st.session_state.messages.append({"role": "assistant", "content": answer})
        with st.chat_message("assistant"):
            st.markdown(answer)

with tab_case:
    st.caption("사건 정보를 입력하면 고소장 초안을 자동으로 생성합니다.")
    st.warning("입력하신 사건 정보(개인정보 포함 가능)는 Supabase 데이터베이스에 저장됩니다.")

    with st.form("case_form"):
        field_values = {}
        for label, key in CASE_FIELDS:
            if key in CASE_LONG_TEXT_FIELDS:
                field_values[key] = st.text_area(label)
            else:
                field_values[key] = st.text_input(label)

        submitted = st.form_submit_button("사건 저장 및 고소장 초안 생성")

    if submitted:
        missing_labels = [
            label for label, key in CASE_FIELDS
            if not field_values[key].strip()
        ]

        if missing_labels:
            st.error("다음 항목을 입력해 주세요: " + ", ".join(missing_labels))
        else:
            kst = datetime.timezone(datetime.timedelta(hours=9))
            case_entry = {
                "case_id": str(uuid.uuid4()),
                "original_request": "Streamlit 고소장 작성 폼에서 제출됨",
                **{key: field_values[key].strip() for _, key in CASE_FIELDS},
                "created_at": datetime.datetime.now(kst).isoformat(),
            }

            try:
                save_case(case_entry)
            except Exception as error:
                st.error(f"사건 저장 중 오류가 발생했습니다: {error}")
            else:
                try:
                    st.session_state.case_draft = generate_case_draft(case_entry)
                    st.session_state.case_draft_id = case_entry["case_id"]
                except Exception as error:
                    st.error(f"초안 생성 중 오류가 발생했습니다: {error}")

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
                    except Exception as error:
                        st.error(f"사건 삭제 중 오류가 발생했습니다: {error}")
                    else:
                        st.success(f"사건(case_id: {case_id})을 삭제했습니다.")
                        st.rerun()

            st.divider()

render_footer()
