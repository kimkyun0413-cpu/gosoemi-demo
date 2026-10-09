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

    def test_first_screen_texts(self):
        self.assertEqual(c.CHAT_WELCOME, "무엇이 궁금하신가요?")
        self.assertEqual(c.CHAT_INPUT_PLACEHOLDER, "질문을 입력해 주세요")
        self.assertIn(f"idle: '{c.VOICE_WELCOME}'", c.VOICE_CHAT_JS)
        for old in ("어떤 일이 있으셨는지", "하나씩 여쭤"):
            self.assertNotIn(old, c.CHAT_WELCOME + c.VOICE_CHAT_JS)

    def test_voice_component_rules(self):
        js = c.VOICE_CHAT_JS
        self.assertIn("v.localService", js)                       # 기기 안 음성만
        self.assertEqual(js.count("getUserMedia("), 1)              # 마이크 요청은 start() 한 곳뿐
        start = js.index("async function start(")
        self.assertGreater(js.index("getUserMedia("), start)
        self.assertIn("S.el.mic.onclick", js)                       # start()는 마이크 버튼에서만 부른다
        self.assertEqual(js.count(" start(S);"), 1)
        speak = js[js.index("function speak("):js.index("function downsample(")]
        self.assertIn("micEnabled(S, false)", speak)                # 답변 중에는 마이크 입력을 끈다
        finish = js[js.index("function finishTurn("):js.index("function onFrame(")]
        self.assertIn("micEnabled(S, false)", finish)
        end = js[js.index("function endAll("):js.index("function build(")]
        for call in ("stopSpeech(S)", "stopStream(S)"):
            self.assertIn(call, end)                                # 종료하면 마이크·읽기 모두 멈춤
        self.assertIn("t.stop()", js)
        self.assertIn("echoCancellation: true", js)
        self.assertNotIn("fetch(", js)
        self.assertNotIn("innerHTML = data", js)


class VoiceTurnTest(unittest.TestCase):
    def wav(self, n=4000):
        import struct
        body = b"\x00\x00" * n
        return b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVEfmt " + b"\x00" * 20 + b"data" + struct.pack("<I", len(body)) + body

    def payload(self, **kw):
        import base64
        data = {"sid": "abc123", "seq": 1, "audio": base64.b64encode(self.wav()).decode()}
        data.update(kw)
        return data

    def test_valid_turn(self):
        sid, seq, data = c.decode_voice_turn(self.payload())
        self.assertEqual((sid, seq), ("abc123", 1))
        self.assertTrue(data.startswith(b"RIFF"))

    def test_rejects_bad_input(self):
        import base64
        bad = [
            None, "x", self.payload(sid="../x"), self.payload(seq="1"), self.payload(seq=0),
            self.payload(audio="!!!"), self.payload(audio=base64.b64encode(b"x" * 100).decode()),
            self.payload(audio=base64.b64encode(b"ID3" + b"0" * 5000).decode()),
            self.payload(audio="A" * (c.VOICE_TURN_MAX_BYTES * 2)),
        ]
        for item in bad:
            with self.assertRaises(ValueError):
                c.decode_voice_turn(item)

    def test_voice_instruction_is_short_and_keeps_legal_rules(self):
        text = c.voice_system_instruction("정체성", "안전")
        self.assertIn("2~4문장", text)
        self.assertIn("단정하지 마라", text)


if __name__ == "__main__":
    unittest.main()
