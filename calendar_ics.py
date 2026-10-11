"""사용자가 정한 일정을 캘린더 파일(.ics, RFC 5545)로 만든다. Streamlit에 의존하지 않는다.

- 고소미는 알림을 보내지 않는다. 파일을 휴대폰·PC 일정 앱에 추가하면 그 앱이 알림을 보낸다(앱마다 다를 수 있음).
- 법률 기한을 계산하거나 추정하지 않는다. 날짜와 시간은 사용자가 고른 값만 쓴다.
- 시간은 한국 시간(Asia/Seoul, UTC+9, 일광절약시간 없음)으로 적는다.
"""

import datetime
import uuid

KIND_LABELS = {"submit": "제출", "consult": "상담", "follow_up": "후속 확인", "other": "기타"}
ALARM_CHOICES = {0: "알림 없음", 30: "30분 전", 60: "1시간 전", 1440: "하루 전"}
TITLE_MAX = 60
MEMO_MAX = 300

_VTIMEZONE = [
    "BEGIN:VTIMEZONE", "TZID:Asia/Seoul", "BEGIN:STANDARD", "DTSTART:19700101T000000",
    "TZOFFSETFROM:+0900", "TZOFFSETTO:+0900", "TZNAME:KST", "END:STANDARD", "END:VTIMEZONE",
]


def _escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _fold(line: str) -> list:
    """75바이트를 넘는 줄은 접는다(한글이 깨지지 않게 글자 단위로)."""
    out, cur = [], ""
    for ch in line:
        limit = 75 if not out else 74
        if len((cur + ch).encode("utf-8")) > limit:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return [out[0]] + [" " + part for part in out[1:]]


def clean_event(event: dict) -> dict:
    """입력값 검사. 날짜가 없으면 ValueError."""
    date = event.get("date")
    if not isinstance(date, datetime.date):
        raise ValueError("date")
    time = event.get("time")
    if time is not None and not isinstance(time, datetime.time):
        raise ValueError("time")
    alarm = int(event.get("alarm") or 0)
    if alarm not in ALARM_CHOICES:
        raise ValueError("alarm")
    kind = event.get("kind") if event.get("kind") in KIND_LABELS else "other"
    title = (event.get("title") or "").strip()[:TITLE_MAX] or KIND_LABELS[kind]
    return {"kind": kind, "title": title, "date": date, "time": time, "alarm": alarm,
            "memo": (event.get("memo") or "").strip()[:MEMO_MAX], "uid": event.get("uid") or f"{uuid.uuid4()}@gosoemi"}


def build_ics(events: list, case_title: str = "", now: datetime.datetime = None) -> str:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = now.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//gosoemi//case schedule//KO", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", *_VTIMEZONE]
    for raw in events:
        event = clean_event(raw)
        summary = f"[고소미] {event['title']}" + (f" · {case_title}" if case_title else "")
        lines += ["BEGIN:VEVENT", f"UID:{event['uid']}", f"DTSTAMP:{stamp}"]
        if event["time"] is None:
            day = event["date"]
            lines += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + datetime.timedelta(days=1):%Y%m%d}"]
        else:
            start = datetime.datetime.combine(event["date"], event["time"])
            end = start + datetime.timedelta(hours=1)
            lines += [f"DTSTART;TZID=Asia/Seoul:{start:%Y%m%dT%H%M%S}", f"DTEND;TZID=Asia/Seoul:{end:%Y%m%dT%H%M%S}"]
        lines.append(f"SUMMARY:{_escape(summary)}")
        description = (event["memo"] + "\n" if event["memo"] else "") + "사용자가 직접 정한 일정입니다. 실제 기관 일정·기한은 담당 기관에서 확인해 주세요."
        lines.append(f"DESCRIPTION:{_escape(description)}")
        if event["alarm"]:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(summary)}",
                      f"TRIGGER:-PT{event['alarm']}M", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    folded = []
    for line in lines:
        folded += _fold(line)
    return "\r\n".join(folded) + "\r\n"


def describe(event: dict) -> str:
    event = clean_event(event)
    when = f"{event['date']:%Y-%m-%d}" + (f" {event['time']:%H:%M}" if event["time"] else " (하루 종일)")
    alarm = "" if not event["alarm"] else f" · 알림 {ALARM_CHOICES[event['alarm']]}"
    return f"{when} · {KIND_LABELS[event['kind']]} · {event['title']}{alarm}"
