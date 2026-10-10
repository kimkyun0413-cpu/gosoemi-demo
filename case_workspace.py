"""사건 하나를 끝까지 정리하는 작업 공간의 규칙. Streamlit에 의존하지 않는다.

- 빠진 정보·서로 다른 값 점검: 입력한 글만 보고 규칙으로 찾는다. 점수·확률·범죄 성립 판단은 만들지 않는다.
- 사건 경위(타임라인): 사용자가 적은 경위를 나눠 보여 줄 뿐, 날짜나 사실을 만들지 않는다. 날짜가 안 보이면 비워 둔다.
- 증거 목록: 사용자가 적은 자료 이름과 첨부한 파일 이름만 쓴다. 파일 내용은 분석하지 않는다.
- 초안 표현 바꾸기: AI가 표현만 바꾸고, 바뀐 뒤 숫자(금액·날짜·번호)가 늘거나 빠지면 경고한다.
"""

import re
from io import BytesIO

from docx import Document
from docx.shared import Pt

from case_guide import (
    CASE_FIELD_LABELS, DRAFT_DISCLAIMER, UNKNOWN_HINTS, _amounts, _dates, is_missing, with_draft_disclaimer,
)

UNKNOWN = "모름"

# 꼭 필요한 순서대로 하나씩 묻는다(필수 입력인 신고인 정보·경위는 이미 확인을 거쳤다).
QUESTIONS = [
    ("incident_datetime", "언제 있었던 일인가요?", "정확하지 않으면 '9월 초쯤'처럼 기억나는 범위만 적어도 돼요."),
    ("suspect_info", "상대방에 대해 아는 것이 있나요?", "이름을 모르면 닉네임·전화번호·계좌번호 같은 단서만 적어도 돼요."),
    ("incident_location", "어디에서 있었던 일인가요?", "온라인이면 앱·사이트 이름을 적어 주세요."),
    ("damage_details", "어떤 피해를 입었나요?", "돈이라면 금액과 보낸 날, 다쳤다면 진료 기록 등을 적어 주세요."),
    ("evidence", "가지고 있는 자료가 있나요?", "대화 캡처, 이체 내역, 사진처럼 이미 가진 자료 이름을 적어 주세요."),
    ("witness", "본 사람이 있나요?", "없으면 '없음'이라고 적어 주세요."),
    ("punishment_intent", "상대방의 처벌을 원하시나요?", "아직 정하지 못했다면 그렇게 적어도 돼요."),
]

_SPLIT_RE = re.compile(r"\s*(?:→|->|\n|;|(?<=[.!?。])\s+)\s*")
_DATE_TOKEN_RE = re.compile(
    r"\d{4}\s*(?:년|[.\-/])\s*\d{1,2}\s*(?:월|[.\-/])\s*\d{1,2}\s*일?|\d{1,2}\s*월\s*\d{1,2}\s*일"
    r"|\d{1,2}\s*월\s*(?:초|중순|말)쯤?|어제|오늘|그저께|지난\s*주"
)
_TIME_TOKEN_RE = re.compile(r"(?:오전|오후|새벽|밤|저녁|아침)?\s*\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?(?:\s*쯤)?")


def is_unknown(value: str) -> bool:
    text = (value or "").strip()
    return any(hint in text for hint in UNKNOWN_HINTS)


def missing_questions(case: dict) -> list:
    """비어 있는 항목의 질문(필요한 순서대로). '모름'이라고 적은 항목은 묻지 않는다."""
    return [(key, q, hint) for key, q, hint in QUESTIONS if is_missing(case.get(key)) and not is_unknown(case.get(key))]


def suggestion_for(case: dict, key: str) -> str:
    """빠진 칸에 넣을 수 있는, 이미 다른 칸에 적힌 글자(지금은 사건 일시 ← 경위의 첫 날짜). 없으면 빈 문자열."""
    if key == "incident_datetime":
        for row in build_timeline(case):
            if row["when"]:
                return row["when"]
    return ""


def unknown_items(case: dict) -> list:
    """'모름' 등으로 진행한 항목 이름. 확인 완료로 보지 않고 초안에 [확인 필요]로 남는다."""
    return [CASE_FIELD_LABELS[key] for key, _, _ in QUESTIONS if not is_missing(case.get(key)) and is_unknown(case.get(key))]


def _fmt_amounts(values: set) -> str:
    return ", ".join(sorted(v.replace("만", "만 ").replace("천", "천 ").replace("억", "억 ").strip() + " 원" for v in values))


def _fmt_dates(values: set) -> str:
    return ", ".join(f"{m}월 {d}일" for m, d in sorted(values))


def inconsistencies(case: dict) -> list:
    """서로 다른 칸에 적힌 금액·날짜가 겹치지 않으면 확인을 부탁한다. 무엇이 맞는지는 판단하지 않는다."""
    notes = []
    story = case.get("incident_description") or ""
    damage = case.get("damage_details") or ""
    when = case.get("incident_datetime") or ""
    story_amounts, damage_amounts = _amounts(story), _amounts(damage)
    if story_amounts and damage_amounts and not (story_amounts & damage_amounts):
        notes.append(
            f"사건 경위에는 {_fmt_amounts(story_amounts)}, 피해 내용에는 {_fmt_amounts(damage_amounts)}이 적혀 있어요. "
            "같은 돈인지, 둘 다 맞는지 확인해 주세요."
        )
    when_dates, story_dates = _dates(when), _dates(story)
    if when_dates and story_dates and not (when_dates & story_dates):
        notes.append(
            f"사건 일시에는 {_fmt_dates(when_dates)}, 사건 경위에는 {_fmt_dates(story_dates)}이 적혀 있어요. 날짜를 확인해 주세요."
        )
    return notes


def build_timeline(case: dict) -> list:
    """사건 경위를 문장·화살표 단위로 나눈다. 날짜·시각은 그 문장에 적힌 글자만 그대로 옮긴다."""
    rows = []
    for part in _SPLIT_RE.split((case.get("incident_description") or "").strip()):
        part = part.strip(" .")
        if not part:
            continue
        date = _DATE_TOKEN_RE.search(part)
        time = _TIME_TOKEN_RE.search(part)
        when = " ".join(x.group(0).strip() for x in (date, time) if x)
        rows.append({"when": when, "what": part, "evidence": ""})
    return rows


def evidence_items(case: dict, file_names=None) -> list:
    """증거 칸에 적은 자료와 첨부한 파일 이름. '무엇을 보여 주는지'는 사용자가 적는다(파일 내용은 보지 않는다)."""
    items = []
    text = case.get("evidence") or ""
    if not is_missing(text) and not is_unknown(text):
        for name in re.split(r"\s*(?:,|/|·|\n|및)\s*", text):
            name = name.strip(" .")
            if name and name not in ("없음", "없어요"):
                items.append({"name": name[:60], "shows": "", "source": "적은 자료"})
    for name in file_names or []:
        items.append({"name": str(name)[:60], "shows": "", "source": "첨부 파일"})
    seen, unique = set(), []
    for item in items:
        if item["name"] not in seen:
            seen.add(item["name"])
            unique.append(item)
    return unique


def link_issues(timeline: list, evidence: list) -> dict:
    """연결되지 않은 경위·자료. 사실 여부를 판단하는 것이 아니라 확인할 항목을 알려 준다."""
    names = {e["name"] for e in evidence}
    linked = {row.get("evidence") for row in timeline if row.get("evidence")}
    return {
        "events_without_evidence": [row["what"] for row in timeline if not row.get("evidence") and row.get("what")],
        "evidence_not_used": [e["name"] for e in evidence if e["name"] not in linked],
        "evidence_without_note": [e["name"] for e in evidence if not (e.get("shows") or "").strip()],
        "unknown_links": sorted(x for x in linked if x not in names),
    }


def next_action(case: dict, has_draft: bool, progress: dict) -> str:
    """내 사건에서 먼저 보여 줄 다음 행동. 실제 경찰·법원 진행을 뜻하지 않는다."""
    if missing_questions(case) or inconsistencies(case):
        return "빠진 정보 확인하기"
    if not has_draft:
        return "초안 만들기"
    if not (progress or {}).get("review"):
        return "초안 내용 직접 확인하기"
    if not (progress or {}).get("submitted"):
        return "제출할지 정하고 표시하기"
    return "다음 일정 확인하기"


# ── 초안 표현 바꾸기 ────────────────────────────────────────────
STYLES = {
    "concise": ("간결하게", "각 소제목 아래 문장을 짧고 분명하게 줄이세요. 같은 말의 반복을 없애세요."),
    "detailed": ("자세하게", "'범죄 사실'은 일어난 순서대로 문장을 나눠 쓰세요. 이미 적힌 사실만 풀어 쓰세요."),
}


def rewrite_prompt(draft: str, style: str) -> str:
    label, how = STYLES[style]
    return (
        f"아래 [초안]의 표현만 '{label}' 바꾸세요. {how}\n"
        "규칙:\n"
        "1. 날짜·시간·금액·이름·연락처·계좌·장소·사건 내용을 바꾸거나 새로 넣거나 빼지 마세요.\n"
        "2. '[확인 필요]' 표시는 그대로 두세요.\n"
        "3. 주장의 강도를 바꾸지 마세요. 추측은 추측으로, 요청은 요청으로 두고 범죄가 된다고 단정하지 마세요.\n"
        "4. 소제목(고소인, 피고소인, 고소 취지, 범죄 사실, 고소 이유, 증거자료, 목격자, 처벌 의사, 작성일)은 그대로 두세요.\n"
        "5. 마크다운 기호 없이, 설명 없이 고친 초안만 출력하세요.\n\n"
        f"[초안]\n{draft}"
    )


def _digits(text: str) -> set:
    return set(re.findall(r"\d+", re.sub(r"(?<=\d),(?=\d)", "", text or "")))


def rewrite_changes(before: str, after: str) -> list:
    """표현을 바꾼 뒤 사라지거나 새로 생긴 숫자와 [확인 필요] 표시 수 변화를 알려 준다."""
    notes = []
    added, removed = _digits(after) - _digits(before), _digits(before) - _digits(after)
    if added:
        notes.append("원래 초안에 없던 숫자가 들어갔어요: " + ", ".join(sorted(added)))
    if removed:
        notes.append("원래 초안에 있던 숫자가 빠졌어요: " + ", ".join(sorted(removed)))
    if after.count("[확인 필요]") != before.count("[확인 필요]"):
        notes.append(f"'[확인 필요]' 표시 수가 바뀌었어요({before.count('[확인 필요]')}개 → {after.count('[확인 필요]')}개).")
    return notes


def draft_check_items(draft: str) -> list:
    """초안에서 직접 확인할 줄([확인 필요]가 있는 줄)."""
    return [line.strip() for line in (draft or "").splitlines() if "[확인 필요]" in line]


# ── 함께 내려받기 ───────────────────────────────────────────────
def bundle_text(draft: str, timeline: list, evidence: list, include: dict) -> str:
    parts = []
    if include.get("draft") and draft:
        parts.append(with_draft_disclaimer(draft))
    if include.get("timeline") and timeline:
        lines = ["사건 경위 (직접 정리한 순서)"]
        for i, row in enumerate(timeline, 1):
            when = row.get("when") or "날짜 확인 필요"
            link = f" — 관련 자료: {row['evidence']}" if row.get("evidence") else ""
            lines.append(f"{i}. [{when}] {row.get('what', '')}{link}")
        parts.append("\n".join(lines))
    if include.get("evidence") and evidence:
        lines = ["증거 목록 (파일 내용은 고소음이가 확인하지 않았습니다)"]
        for i, item in enumerate(evidence, 1):
            shows = f" — {item['shows']}" if (item.get("shows") or "").strip() else " — 무엇을 보여 주는지 확인 필요"
            lines.append(f"{i}. {item['name']} ({item.get('source', '')}){shows}")
        parts.append("\n".join(lines))
    if parts and not include.get("draft"):
        parts.append(DRAFT_DISCLAIMER)
    return "\n\n".join(parts)


def bundle_docx(text: str) -> BytesIO:
    document = Document()
    document.styles["Normal"].font.size = Pt(11)
    for line in (text or "").splitlines():
        if line.strip():
            document.add_paragraph(line.strip().replace("**", "").replace("## ", ""))
    buffer = BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return buffer
