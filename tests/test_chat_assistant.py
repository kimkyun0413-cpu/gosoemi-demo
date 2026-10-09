"""chat_assistant.py 검사. 실행: python3 -m unittest discover -s tests"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chat_assistant as c  # noqa: E402


class HistoryTest(unittest.TestCase):
    def test_contents_keep_order_and_end_with_question(self):
        messages = []
        c.append_exchange(messages, "첫 질문", "첫 답변")
        contents = c.model_contents(messages, "두 번째 질문")
        self.assertEqual([x["role"] for x in contents], ["user", "model", "user"])
        self.assertEqual(contents[-1]["parts"][0]["text"], "두 번째 질문")

    def test_history_is_trimmed_and_starts_with_user(self):
        messages = []
        for i in range(40):
            c.append_exchange(messages, f"질문{i}", f"답변{i}")
        self.assertLessEqual(len(messages), c.CHAT_MAX_MESSAGES)
        contents = c.model_contents(messages, "새 질문")
        self.assertLessEqual(len(contents), c.CHAT_HISTORY_MESSAGES + 1)
        self.assertEqual(contents[0]["role"], "user")

    def test_duplicate_submit_window(self):
        last = {"digest": c.question_digest("같은  질문"), "at": 100.0}
        self.assertTrue(c.is_duplicate_submit(last, "같은 질문", now=105.0))
        self.assertFalse(c.is_duplicate_submit(last, "같은 질문", now=111.0))
        self.assertFalse(c.is_duplicate_submit(last, "다른 질문", now=101.0))
        self.assertFalse(c.is_duplicate_submit(None, "같은 질문"))

    def test_user_statements_exclude_ai_answers(self):
        messages = []
        c.append_exchange(messages, "45만 원을 보냈어요", "보통 3일 안에 신고하셔야 합니다")
        c.append_exchange(messages, "장소는 온라인 중고거래예요", "알겠습니다")
        text = c.user_statements(messages)
        self.assertIn("45만 원을 보냈어요", text)
        self.assertIn("온라인 중고거래", text)
        self.assertNotIn("3일", text)


class InstructionTest(unittest.TestCase):
    def test_legal_answer_rules(self):
        text = c.system_instruction("정체성", "안전")
        for phrase in ["한 번에 하나만", "추측", "단정하지 마라", "판례, 법조문 번호", "인터넷 검색을 하지 않는다", "112"]:
            self.assertIn(phrase, text)

    def test_retention_note_matches_actual_behavior(self):
        for phrase in ["데이터베이스에 저장하지 않고", "약 2분", "로그아웃", "Google"]:
            self.assertIn(phrase, c.CHAT_RETENTION_NOTE)


class SpeechTest(unittest.TestCase):
    def test_speech_text_strips_markdown_and_links(self):
        text = c.speech_text("**중요**: [경찰청](https://www.police.go.kr) 안내를 보세요.\n- 첫째\n- 둘째 https://x.y/z")
        self.assertNotIn("*", text)
        self.assertNotIn("http", text)
        self.assertIn("경찰청", text)

    def test_speech_text_is_limited(self):
        text = c.speech_text("가나다라마바사입니다. " * 400)
        self.assertLessEqual(len(text), c.SPEECH_MAX_CHARS + 40)
        self.assertTrue(text.endswith("화면의 글로 확인해 주세요."))

    def test_speech_component_uses_only_local_korean_voices_and_no_mic(self):
        js = c.SPEECH_COMPONENT_JS
        self.assertIn("v.localService", js)
        self.assertNotIn("getUserMedia", js)
        self.assertIn("synth.cancel()", js)
        self.assertNotIn("fetch(", js)


if __name__ == "__main__":
    unittest.main()
