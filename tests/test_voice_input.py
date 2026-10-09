"""voice_input.py 검사. 실행: python3 -m unittest discover -s tests"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voice_input as v  # noqa: E402
from case_guide import DAMAGE_TYPES  # noqa: E402

TYPES = list(DAMAGE_TYPES)
TRANSCRIPT = (
    "9월 2일 오후 3시쯤 중고마켓에서 태블릿을 사려고 45만 원을 보냈어요. "
    "판매자 닉네임은 판매왕이고 전화번호는 010-1234-5678이었어요. "
    "채팅 캡처랑 이체 내역이 있어요. [잘 안 들림] 이후로 연락이 안 돼요."
)


class AudioValidationTest(unittest.TestCase):
    def test_mime_types(self):
        self.assertEqual(v.audio_mime_type("녹음.M4A"), "audio/m4a")
        self.assertEqual(v.audio_mime_type("audio_input", "audio/wav"), "audio/wav")
        self.assertEqual(v.audio_mime_type("movie.mp4"), "")

    def test_validate(self):
        self.assertEqual(v.validate_audio("a.wav", 50_000), "")
        self.assertIn("형식", v.validate_audio("a.mp4", 50_000))
        self.assertIn("거의 없습니다", v.validate_audio("a.wav", 100))
        self.assertIn("10MB", v.validate_audio("a.wav", v.VOICE_MAX_BYTES + 1))

    def test_upload_types_match_mime_table(self):
        for ext in v.VOICE_UPLOAD_TYPES:
            self.assertTrue(v.audio_mime_type("x." + ext))


class PromptTest(unittest.TestCase):
    def test_transcription_prompt_forbids_guessing(self):
        prompt = v.transcription_prompt()
        self.assertIn(v.UNCLEAR_MARK, prompt)
        self.assertIn("보태지 마세요", prompt)

    def test_extraction_prompt_contains_transcript_and_rules(self):
        prompt = v.extraction_prompt(TRANSCRIPT, TYPES)
        self.assertIn(TRANSCRIPT, prompt)
        self.assertIn("만들지 마세요", prompt)
        self.assertIn(v.NEEDS_CHECK, prompt)
        for key in v.EXTRACT_FIELDS:
            self.assertIn(key, prompt)

    def test_extraction_prompt_treats_stated_online_place_as_location(self):
        # 운영 시험(2026-10-10)에서 말한 '온라인 중고거래'가 장소 칸에 들어가지 않았다.
        prompt = v.extraction_prompt(TRANSCRIPT, TYPES)
        self.assertIn("incident_location에는 피해자가 말한 장소", prompt)
        self.assertIn("온라인 중고거래", prompt)
        self.assertIn("덧붙이지 마세요", prompt)

    def test_stated_online_location_is_kept(self):
        transcript = "온라인 중고거래로 태블릿을 사려고 45만 원을 보냈어요."
        result = v.parse_extraction(
            json.dumps({"incident_location": "온라인 중고거래"}, ensure_ascii=False), transcript, TYPES
        )
        self.assertEqual(result["incident_location"], "온라인 중고거래")

    def test_empty_transcript(self):
        self.assertTrue(v.is_empty_transcript(" [음성 없음] "))
        self.assertTrue(v.is_empty_transcript(""))
        self.assertFalse(v.is_empty_transcript("돈을 보냈어요"))


class ExtractionTest(unittest.TestCase):
    def parse(self, data, transcript=TRANSCRIPT):
        return v.parse_extraction("```json\n" + json.dumps(data, ensure_ascii=False) + "\n```", transcript, TYPES)

    def test_keeps_stated_facts(self):
        result = self.parse({
            "case_type": "중고거래 사기",
            "incident_datetime": "9월 2일 오후 3시쯤",
            "suspect_info": "닉네임 판매왕, 010-1234-5678",
            "damage_details": "45만 원 송금",
            "evidence": "채팅 캡처, 이체 내역",
            "witness": "",
            "unknown_key": "무시",
        })
        self.assertEqual(result["case_type"], "중고거래 사기")
        self.assertEqual(result["damage_details"], "45만 원 송금")
        self.assertEqual(result["suspect_info"], "닉네임 판매왕, 010-1234-5678")
        self.assertNotIn("witness", result)
        self.assertNotIn("unknown_key", result)

    def test_flags_invented_amount_date_and_number(self):
        result = self.parse({
            "damage_details": "100만 원 송금",
            "incident_datetime": "9월 10일",
            "suspect_info": "010-9999-0000",
        })
        for key in ("damage_details", "incident_datetime", "suspect_info"):
            self.assertTrue(result[key].endswith(v.NEEDS_CHECK), key)

    def test_flags_unclear_parts(self):
        result = self.parse({"incident_description": "[잘 안 들림] 이후로 연락이 안 됨"})
        self.assertTrue(result["incident_description"].endswith(v.NEEDS_CHECK))

    def test_rejects_unknown_case_type_and_bad_json(self):
        self.assertNotIn("case_type", self.parse({"case_type": "살인 사건"}))
        with self.assertRaises(ValueError):
            v.parse_extraction("죄송합니다", TRANSCRIPT, TYPES)

    def test_truncates_to_field_limit(self):
        result = self.parse({"witness": "가" * 2000}, transcript="가" * 2000)
        self.assertLessEqual(len(result["witness"]), 1000)


class TestPhraseTest(unittest.TestCase):
    def test_counts_keywords_ignoring_spaces(self):
        self.assertEqual(v.test_phrase_check(TRANSCRIPT), ["9월 2일", "45만", "판매왕", "채팅", "이체"])
        self.assertEqual(v.test_phrase_check("구월 이일에 돈을 보냈어요"), [])


class MergeTest(unittest.TestCase):
    def test_marks_fill_overwrite_same(self):
        rows = v.merge_rows(
            {"incident_location": "집 앞", "evidence": "", "witness": "없음"},
            {"incident_location": "회사 앞", "evidence": "사진", "witness": "없음"},
        )
        actions = {key: action for key, _, _, _, action in rows}
        self.assertEqual(actions, {"incident_location": "overwrite", "evidence": "fill", "witness": "same"})


if __name__ == "__main__":
    unittest.main()
