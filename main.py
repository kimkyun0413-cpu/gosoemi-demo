import os
import json
import re
import datetime
import unicodedata
import uuid
from google import genai
from google.genai import types
from calendar_test import get_calendar_service
MEMORY_FILE = "memory.json"
MEMORY_TRIGGERS = [unicodedata.normalize("NFC", t) for t in ["기억해", "저장해"]]

SCHEDULE_FILE = "schedules.json"
CANCEL_TRIGGERS = [unicodedata.normalize("NFC", t) for t in ["취소", "삭제"]]
DATE_WORDS = ["오늘", "내일", "모레", "이번주", "이번 주", "다음주", "다음 주"]

CASE_FILE = "cases.json"
CASE_TRIGGERS = [unicodedata.normalize("NFC", t) for t in ["고소장", "고소하고", "사건 접수", "사건접수"]]
DRAFT_TRIGGERS = [unicodedata.normalize("NFC", t) for t in ["고소장 초안", "초안 만들어", "초안 작성"]]
DRAFT_DISCLAIMER = "이 문서는 참고용 초안이며 제출 전 법률 전문가의 검토가 필요합니다."


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


def load_cases():
    if os.path.exists(CASE_FILE):
        with open(CASE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_cases(cases):
    with open(CASE_FILE, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)

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
    # 한글 조합과 보이지 않는 문자를 정리한 뒤 시간 숫자를 먼저 찾는다.
    # 이렇게 하면 "오후 3시", "오후 3시에", "오후 3시 30분"을 모두 처리한다.
    clean_question = unicodedata.normalize("NFC", question).replace("\u200b", "")
    m = re.search(r"([0-9]{1,2})\s*시(?:\s*([0-9]{1,2})\s*분)?", clean_question)
    if not m:
        return None

    period_match = re.search(r"(오전|오후)\s*$", clean_question[:m.start()])
    period = period_match.group(1) if period_match else None
    hour = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0

    if hour > 23 or minute > 59:
        return None
    if period and not 1 <= hour <= 12:
        return None

    if period == "오후" and hour != 12:
        hour += 12
    if period == "오전" and hour == 12:
        hour = 0

    return f"{hour:02d}:{minute:02d}"    


SCHEDULE_ACTION_WORDS = ["추가", "등록", "저장", "잡아", "만들어", "넣어"]

def strip_common_words(text):
    for word in DATE_WORDS:
        text = text.replace(word, "")
    text = re.sub(r"\d{1,2}월\s*\d{1,2}일", "", text)
    text = re.sub(r"(오전|오후)?\s*\d{1,2}시(\s*\d{1,2}분)?", "", text)
    text = text.replace("일정", "")
    for word in SCHEDULE_ACTION_WORDS:
        text = re.sub(word + r"\S*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.!?에는를을이가")



def extract_task_text(question):
    text = question
    for trigger in MEMORY_TRIGGERS:
        text = re.sub(trigger + r"\S*", "", text)
    text = re.sub(
        r"(?:일정|스케줄)?\s*(?:추가|등록|저장|잡아|만들어|넣어)"
        r"(?:해\s*줘|해주세요|해줘|해|줘)?\s*$",
        "",
        text,
    )
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


def is_case_report_intent(question):
    return any(trigger in question for trigger in CASE_TRIGGERS)


def is_draft_request(question):
    return any(trigger in question for trigger in DRAFT_TRIGGERS)


def format_schedule(item):
    time_part = f" {item['time']}" if item["time"] else ""
    return f"{item['date']}{time_part} {item['task']}"


def handle_schedule_add(question, schedules):
    date = resolve_date(question)
    time = resolve_time(question)
    task = extract_task_text(question)
    print(f"[진단] date={date!r}, time={time!r}, task={task!r}, question={question!r}")    
    if not date or not task:
        print("[일정 저장 실패] 날짜나 내용을 정확히 알 수 없습니다.")
        return "사용자가 일정을 저장하려 했지만 날짜 또는 내용을 인식하지 못했습니다."

    entry = {
        "date": date.isoformat(),
        "time": time,
        "task": task
    }

    if any(
        item.get("date") == entry["date"]
        and item.get("time") == entry["time"]
        and item.get("task") == entry["task"]
        for item in schedules
    ):
        return f"이미 저장된 일정입니다: {format_schedule(entry)}"

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


CASE_FIELDS = [
    ("사건 유형", "case_type"),
    ("신고인 정보", "reporter_info"),
    ("상대방 정보(모르면 '모름' 입력 가능)", "suspect_info"),
    ("사건 발생 일시", "incident_datetime"),
    ("사건 발생 장소", "incident_location"),
    ("구체적인 사건 경위", "incident_description"),
    ("피해 내용", "damage_details"),
    ("증거 자료", "evidence"),
    ("목격자 유무", "witness"),
    ("상대방에 대한 처벌 의사", "punishment_intent"),
]


def handle_case_report(question, cases):
    print("고소장 작성을 위해 몇 가지를 순서대로 여쭤볼게요. (중단하려면 '중단' 입력)")

    entry = {
        "case_id": str(uuid.uuid4()),
        "original_request": question,
    }

    for label, key in CASE_FIELDS:
        answer = input(f"- {label}: ").strip()
        if answer == "중단":
            return "고소장 작성을 중단했습니다."
        entry[key] = answer

    kst = datetime.timezone(datetime.timedelta(hours=9))
    entry["created_at"] = datetime.datetime.now(kst).isoformat()

    cases.append(entry)
    save_cases(cases)

    return "사건 내용을 저장했습니다. 필요하면 이 내용을 바탕으로 고소장 초안 작성을 요청해 주세요."


def get_latest_case(cases):
    return max(cases, key=lambda c: c.get("created_at", ""))


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
        f"[입력된 사건 정보]\n{fields_text}\n\n"
        f"[사용자 최초 요청 원문]\n{case.get('original_request', '(없음)')}"
    )


def handle_case_draft(cases):
    if not cases:
        return "저장된 사건이 없습니다. 먼저 고소장 작성을 진행해 주세요."

    case = get_latest_case(cases)

    kst = datetime.timezone(datetime.timedelta(hours=9))
    today_str = datetime.datetime.now(kst).date().isoformat()

    prompt = build_draft_prompt(case, today_str)
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
    )

    draft_text = f"{response.text}\n\n{DRAFT_DISCLAIMER}"

    filename = f"case_draft_{case['case_id']}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(draft_text)

    return draft_text


def build_system_instruction(memory, schedule_notice):
    parts = []
    if memory:
        lines = "\n".join(f"- {item}" for item in memory)
        parts.append(
            "다음은 사용자가 이전에 기억해달라고 요청한 장기 기억입니다. "
            "관련된 질문에 답할 때 반드시 참고하세요:\n" + lines
        )
    if schedule_notice:
        parts.append(schedule_notice)
    if not parts:
        return None
    return "\n\n".join(parts)


print("[main.py 시작됨 | 기능: 대화 맥락 + 장기기억(memory.json) + 일정관리(schedules.json)]")

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
history = []
memory = load_memory()
schedules = load_schedules()
cases = load_cases()

while True:
    question = input("질문 : ")
    question = unicodedata.normalize("NFC", question)

    if question.lower() == "exit" or question == "종료":
        break

    schedule_notice = None

    if is_draft_request(question):
        schedule_notice = handle_case_draft(cases)

    elif is_case_report_intent(question):
        schedule_notice = handle_case_report(question, cases)

    elif is_cancel_request(question) and ("일정" in question or has_date_cue(question)):
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
            print(f"[장기기억 저장됨] {memory_text}")


    elif is_schedule_query(question):
        schedule_notice = handle_schedule_query(question, schedules)
    if schedule_notice:
        print(f"\nAI : {schedule_notice}")
        continue

    history.append({"role": "user", "parts": [{"text": question}]})
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=history,
        config=types.GenerateContentConfig(
            system_instruction=build_system_instruction(memory, schedule_notice)
        ),
    )

    history.append({"role": "model", "parts": [{"text": response.text}]})

    print("\nAI :", response.text)
