"""실제 Gemini로 음성 입력을 시험한다(받아쓰기 → 입력칸 정리). 개인정보 없는 시험 음성만 쓴다.

키는 화면에 나오지 않게 입력하고, 이 스크립트는 키를 출력·저장하지 않는다.

    read -s GEMINI_API_KEY && export GEMINI_API_KEY   # 붙여 넣어도 화면에 보이지 않음
    python3 scripts/voice_smoke_test.py               # 기본: tests/fixtures/voice_test_ko.wav
    unset GEMINI_API_KEY

운영 키를 이 컴퓨터에 두고 싶지 않다면 이 스크립트 대신 앱의 관리자 시험 모드(VOICE_ADMIN_EMAILS)를 쓴다.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from case_guide import DAMAGE_TYPES  # noqa: E402
from voice_input import (  # noqa: E402
    TEST_PHRASE_KEYWORDS, audio_mime_type, clean_transcript, extraction_prompt, parse_extraction, test_phrase_check,
    transcription_prompt, validate_audio,
)

MODEL = "gemini-3.5-flash-lite"


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY가 없습니다. 위 안내대로 read -s로 입력해 주세요.")
        return 2
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "voice_test_ko.wav")
    data = open(path, "rb").read()
    problem = validate_audio(path, len(data))
    if problem:
        print(problem)
        return 2

    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=90_000))
    response = client.models.generate_content(
        model=MODEL,
        contents=[types.Part.from_text(text=transcription_prompt()),
                  types.Part.from_bytes(data=data, mime_type=audio_mime_type(path))],
    )
    transcript = clean_transcript(response.text or "")
    print("[받아쓰기]\n" + transcript + "\n")

    raw = client.models.generate_content(
        model=MODEL,
        contents=[{"role": "user", "parts": [{"text": extraction_prompt(transcript, list(DAMAGE_TYPES))}]}],
    ).text
    fields = parse_extraction(raw, transcript, list(DAMAGE_TYPES))
    print("[입력칸 정리]\n" + json.dumps(fields, ensure_ascii=False, indent=2) + "\n")

    found = test_phrase_check(transcript)
    print(f"[확인] 시험 문장의 핵심어 {len(found)}/{len(TEST_PHRASE_KEYWORDS)}개 인식: {found}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
