"""음성 입력: 음성 → 글 → 사건 입력칸 정리에 쓰는 규칙과 검사. Streamlit에 의존하지 않는다.

- 음성 인식은 기존 Gemini API(gemini-3.5-flash-lite, 오디오 입력 지원)로 한다. 새 서비스는 쓰지 않는다.
- 원본 음성은 저장하지 않는다. 앱은 요청을 보내는 동안만 메모리에 두고 위젯 상태를 지운다.
- AI가 정리한 값에서 사용자가 말하지 않은 금액·날짜·번호가 보이면 '(확인 필요)'를 붙인다.
"""

import json
import os
import re

from case_guide import CASE_FIELD_LABELS, CASE_FIELD_MAX_CHARS, CASE_FIELDS, _amounts, _compact, _dates

# Gemini 오디오 문서(2026-09-23)의 지원 형식 중 휴대폰·PC에서 흔한 것만 받는다.
VOICE_MIME_BY_EXTENSION = {
    ".wav": "audio/wav",
    ".mp3": "audio/mp3",
    ".m4a": "audio/m4a",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/opus",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
}
VOICE_UPLOAD_TYPES = [ext.lstrip(".") for ext in VOICE_MIME_BY_EXTENSION]
# Gemini 인라인 요청 한도는 프롬프트 포함 20MB. 여유를 두고 음성은 10MB까지 받는다.
VOICE_MAX_BYTES = 10 * 1024 * 1024
VOICE_MIN_BYTES = 1024
TRANSCRIPT_MAX_CHARS = 8000
UNCLEAR_MARK = "[잘 안 들림]"
NEEDS_CHECK = "(확인 필요)"
NO_SPEECH = "[음성 없음]"

# 관리자 시험용 합성 음성(tests/fixtures/voice_test_ko.wav)에 들어 있는 핵심어. 받아쓰기 점검에만 쓴다.
TEST_PHRASE_KEYWORDS = ["9월 2일", "45만", "판매왕", "채팅", "이체"]


def test_phrase_check(transcript: str) -> list:
    """시험 문장 핵심어 중 받아쓰기에 들어 있는 것."""
    compact = re.sub(r"\s", "", transcript or "")
    return [word for word in TEST_PHRASE_KEYWORDS if re.sub(r"\s", "", word) in compact]


# 음성에서 정리해 넣을 수 있는 입력칸. 사건 유형은 목록에서 고르는 값이라 따로 다룬다.
EXTRACT_FIELDS = [key for _, key in CASE_FIELDS if key != "case_type"]


def audio_mime_type(filename: str, declared: str = "") -> str:
    extension = os.path.splitext((filename or "").lower())[1]
    if extension in VOICE_MIME_BY_EXTENSION:
        return VOICE_MIME_BY_EXTENSION[extension]
    if declared in VOICE_MIME_BY_EXTENSION.values():
        return declared
    return ""


def validate_audio(filename: str, size: int, declared_type: str = "") -> str:
    """문제가 있으면 이용자에게 보여 줄 안내를, 없으면 빈 문자열을 돌려준다."""
    if not audio_mime_type(filename, declared_type):
        return "이 형식의 음성파일은 올릴 수 없습니다. WAV, MP3, M4A, AAC, OGG, OPUS, FLAC, WEBM 파일을 올려 주세요."
    if size < VOICE_MIN_BYTES:
        return "녹음된 내용이 거의 없습니다. 마이크가 켜져 있는지 확인하고 다시 녹음해 주세요."
    if size > VOICE_MAX_BYTES:
        return "음성파일이 10MB보다 큽니다. 5분 안쪽으로 나눠 녹음하거나 파일을 나눠 올려 주세요."
    return ""


def transcription_prompt() -> str:
    return (
        "첨부한 음성을 한국어 글로 그대로 받아 적으세요.\n"
        "규칙:\n"
        "1. 들린 말만 적고, 요약하거나 순서를 바꾸거나 내용을 보태지 마세요.\n"
        f"2. 잘 들리지 않는 부분은 추측하지 말고 {UNCLEAR_MARK}이라고 적으세요.\n"
        "3. 숫자·날짜·금액·이름·전화번호는 들린 그대로 적고, 확실하지 않으면 뒤에 '(확인 필요)'를 붙이세요.\n"
        "4. 말소리가 없으면 '" + NO_SPEECH + "'만 적으세요.\n"
        "5. 설명이나 인사말 없이 받아 적은 글만 출력하세요."
    )


SOURCE_VOICE = "피해자가 직접 말한 내용을 글로 바꾼 것입니다"
# AI 대화에서 정리할 때: AI 답변은 빼고 이용자가 쓰거나 말한 내용만 보낸다.
SOURCE_CHAT = "피해자가 AI 상담 대화에서 직접 쓰거나 말한 내용만 순서대로 모은 것입니다(AI의 답변은 들어 있지 않습니다)"


def extraction_prompt(transcript: str, case_types: list, source: str = SOURCE_VOICE) -> str:
    labels = "\n".join(f"- {key}: {CASE_FIELD_LABELS[key]}" for key in EXTRACT_FIELDS)
    return (
        f"아래 [변환된 글]은 {source}. 이 글만 근거로 사건 입력칸을 채울 JSON을 만드세요.\n"
        "규칙:\n"
        "1. 글에 없는 사실(날짜, 시간, 금액, 이름, 장소, 연락처, 증거, 목격자, 처벌 의사)을 절대 만들지 마세요.\n"
        "2. 말하지 않은 항목은 빈 문자열 \"\"로 두세요.\n"
        f"3. 말했지만 불확실하거나 {UNCLEAR_MARK}이 섞인 내용은 값 뒤에 '{NEEDS_CHECK}'를 붙이세요.\n"
        "4. 피해자의 말투를 바꾸지 말고 필요한 부분만 옮기세요. 범죄가 된다거나 처벌받아야 한다고 판단해 넣지 마세요.\n"
        "5. incident_description에는 일어난 일을 말한 순서대로 정리하세요.\n"
        "6. damage_details에는 피해 금액과 피해 내용을, evidence에는 피해자가 가지고 있다고 말한 자료만 넣으세요.\n"
        "7. incident_location에는 피해자가 말한 장소를 말한 그대로 넣으세요. 온라인에서 일어났다고 말했다면"
        " 그것도 장소입니다(예: '온라인 중고거래', '○○마켓 앱 채팅'). 말한 범위만 옮기고,"
        " 말하지 않은 앱·사이트 이름이나 주소는 덧붙이지 마세요. 사건 유형이나 경위에 같은 말이 들어가도 장소 칸을 비우지 마세요.\n"
        f"8. case_type은 다음 중 글 내용에 가장 맞는 하나를 고르고, 판단하기 어려우면 \"\"로 두세요: {', '.join(case_types)}\n"
        "9. 설명 없이 JSON 객체 하나만 출력하세요. 키는 case_type과 아래 키만 쓰세요.\n"
        f"{labels}\n\n"
        f"[변환된 글]\n{transcript}"
    )


def clean_transcript(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text).strip()
    return text[:TRANSCRIPT_MAX_CHARS]


def is_empty_transcript(text: str) -> bool:
    stripped = (text or "").strip()
    return not stripped or stripped == NO_SPEECH


def _numbers(text: str) -> set:
    return {_compact(n) for n in re.findall(r"\d{2,6}(?:-\d{2,6}){2,}", text or "")}


def unsupported_in(value: str, transcript: str) -> bool:
    """값에 들어 있는 금액·날짜·전화번호가 변환된 글에 없으면 True."""
    return bool(
        (_amounts(value) - _amounts(transcript))
        or (_dates(value) - _dates(transcript))
        or (_numbers(value) - _numbers(transcript))
    )


def parse_extraction(raw: str, transcript: str, case_types: list) -> dict:
    """AI가 돌려준 JSON을 검사해 {입력칸 키: 값}으로 만든다. 형식이 틀리면 ValueError."""
    text = re.sub(r"^```[a-z]*\n?|\n?```$", "", (raw or "").strip()).strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("no json object")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("not an object")

    result = {}
    for key in EXTRACT_FIELDS:
        value = data.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value:
            continue
        if unsupported_in(value, transcript) and NEEDS_CHECK not in value:
            value = f"{value} {NEEDS_CHECK}"
        if UNCLEAR_MARK in value and NEEDS_CHECK not in value:
            value = f"{value} {NEEDS_CHECK}"
        result[key] = value[: CASE_FIELD_MAX_CHARS[key]]

    case_type = data.get("case_type")
    if isinstance(case_type, str) and case_type.strip() in case_types:
        result["case_type"] = case_type.strip()
    return result


def merge_rows(current: dict, proposed: dict) -> list:
    """입력칸별 비교: [(키, 이름, 지금 값, 제안 값, 'fill'|'overwrite'|'same')]."""
    rows = []
    for key in ["case_type"] + EXTRACT_FIELDS:
        if key not in proposed:
            continue
        now = (current.get(key) or "").strip()
        new = proposed[key].strip()
        if now == new:
            action = "same"
        elif now:
            action = "overwrite"
        else:
            action = "fill"
        rows.append((key, CASE_FIELD_LABELS[key], now, new, action))
    return rows
