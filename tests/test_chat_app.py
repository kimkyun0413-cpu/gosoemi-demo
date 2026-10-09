"""새 AI 대화(글·음성)를 실제 app.py로 실행해 확인한다. Supabase·Gemini는 가짜(tests/fake_services.py)를 쓴다.

실행: python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_services as fake  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app.py")

LIMIT_ADMIN_ID = "00000000-0000-4000-8000-00000000c003"

ENV = {
    "SUPABASE_URL": "https://fake.invalid",
    "SUPABASE_PUBLISHABLE_KEY": "fake-publishable",
    "GEMINI_API_KEY": "fake-gemini",
    "ADMIN_USER_IDS": fake.ADMIN_ID + "," + "00000000-0000-4000-8000-00000000c003",
    "PRIVACY_POLICY_EFFECTIVE_DATE": "2026-09-27",
    "AI_CALLS_PER_HOUR": "30",
    "AI_CHAT_V2_ENABLED": "",
    "VOICE_CHAT_ENABLED": "",
    "VOICE_INPUT_ENABLED": "",
    "DRAFT_SAVE_ENABLED": "",
}


class ChatAppTest(unittest.TestCase):
    def setUp(self):
        fake.reset()
        self.patches = [
            mock.patch.dict(os.environ, ENV),
            mock.patch("supabase.create_client", fake.fake_create_client),
            mock.patch("google.genai.Client", fake.FakeGenaiClient),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])

    def start(self, user_id=fake.ADMIN_ID, email="admin@example.com"):
        at = AppTest.from_file(APP, default_timeout=60)
        at.session_state["supabase_session"] = fake.session_for(user_id, email)
        at.session_state["browser_login_checked"] = True
        at.session_state["nav_page"] = "chat"
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        return at

    def send(self, at, text):
        at.chat_input(key="chat_input_box").set_value(text).run()
        self.assertEqual([e.value for e in at.exception], [])
        return at

    def texts(self, at):
        return [m.markdown[0].value for m in at.chat_message if m.markdown]

    def button(self, at, key):
        return at.button(key=key)

    # ── 화면 노출 ──
    def test_admin_sees_new_chat_and_regular_user_sees_old_one(self):
        at = self.start()
        self.assertTrue(any(b.key == "chat_new_btn" for b in at.button))
        regular = self.start(fake.USER_ID, "user@example.com")
        self.assertFalse(any(b.key == "chat_new_btn" for b in regular.button))
        self.assertFalse(any(b.key == "chat_extract_btn" for b in regular.button))

    # ── 후속 질문 기억 ──
    def test_follow_up_question_includes_previous_turns(self):
        fake.REPLIES[:] = ["언제 돈을 보내셨나요?", "9월 2일이군요. 남아 있는 자료가 있나요?"]
        at = self.start()
        self.send(at, "중고거래로 45만 원을 보냈는데 연락이 끊겼어요")
        self.send(at, "9월 2일이에요")
        self.assertEqual(len(fake.CALLS), 2)
        second = fake.contents_text(fake.CALLS[1])
        self.assertIn("45만 원을 보냈는데", second)
        self.assertIn("언제 돈을 보내셨나요?", second)
        self.assertTrue(second.rstrip().endswith("user:9월 2일이에요"))
        system = fake.CALLS[1]["config"].system_instruction
        self.assertIn("한 번에 하나만 물어보라", system)
        self.assertIn("인터넷 검색을 하지 않는다", system)
        self.assertIn("단정하지 마라", system)
        shown = self.texts(at)
        self.assertIn("9월 2일이군요. 남아 있는 자료가 있나요?", shown)

    def test_same_question_twice_in_a_row_is_sent_once(self):
        at = self.start()
        self.send(at, "증거는 무엇을 모아야 하나요?")
        self.send(at, "증거는 무엇을 모아야 하나요?")
        self.assertEqual(len(fake.CALLS), 1)
        self.assertIn("한 번만 보냈습니다", " ".join(i.value for i in at.info))

    # ── 새 대화·지우기 ──
    def test_new_chat_and_clear_remove_history(self):
        at = self.start()
        self.send(at, "첫 질문입니다")
        self.button(at, "chat_new_btn").click().run()
        self.assertEqual(at.session_state["chat_messages"] if "chat_messages" in at.session_state else [], [])
        self.send(at, "새 대화 질문입니다")
        self.assertNotIn("첫 질문입니다", fake.contents_text(fake.CALLS[-1]))
        self.button(at, "chat_clear_btn").click().run()
        self.assertNotIn("새 대화 질문입니다", " ".join(self.texts(at)))
        self.assertIn("지웠어요", " ".join(i.value for i in at.info))

    # ── 실패·재시도 ──
    def test_failure_keeps_question_and_retry_works(self):
        fake.REPLIES[:] = [RuntimeError("503 unavailable"), "다시 답변드립니다."]
        at = self.start()
        self.send(at, "상대방 이름을 모르면 어떻게 하나요?")
        self.assertTrue(at.error)
        self.assertEqual(at.text_area(key="chat_retry_text").value, "상대방 이름을 모르면 어떻게 하나요?")
        self.assertEqual(at.session_state["chat_messages"], [])
        self.button(at, "chat_retry_btn").click().run()
        self.assertIn("다시 답변드립니다.", self.texts(at))
        self.assertEqual(len(at.session_state["chat_messages"]), 2)

    def test_usage_limit_message_and_question_kept(self):
        # 사용량 기록은 서버 메모리에 계속 남으므로, 이 시험만 쓰는 관리자 계정으로 한다.
        os.environ["AI_CALLS_PER_HOUR"] = "1"
        at = self.start(LIMIT_ADMIN_ID, "limit@example.com")
        self.send(at, "첫 질문")
        self.send(at, "두 번째 질문")
        self.assertIn("1시간에 1번까지", " ".join(e.value for e in at.error))
        self.assertEqual(at.text_area(key="chat_retry_text").value, "두 번째 질문")
        self.assertEqual(len(fake.CALLS), 1)

    # ── 글 채팅 ↔ 음성 대화 전환(같은 대화 이어 가기) ──
    def test_switching_to_voice_and_back_keeps_conversation(self):
        at = self.start()
        self.send(at, "중고거래로 45만 원을 보냈어요")
        self.button(at, "chat_voice_open_btn").click().run()
        self.assertEqual([e.value for e in at.exception], [])
        self.assertEqual(at.session_state["chat_mode"], "voice")
        self.assertFalse(at.chat_input)  # 음성 대화 화면에는 글 입력창이 없다
        self.assertTrue(any(b.key == "chat_back_to_text" for b in at.button))
        self.button(at, "chat_back_to_text").click().run()
        self.assertEqual(at.session_state["chat_mode"], "text")
        self.send(at, "이어서 질문해요")
        self.assertIn("45만 원을 보냈어요", fake.contents_text(fake.CALLS[-1]))

    def test_voice_button_hidden_for_regular_user(self):
        at = self.start(fake.USER_ID, "user@example.com")
        self.assertFalse(any(b.key == "chat_voice_open_btn" for b in at.button))

    # ── 사건 정보 정리 ──
    def test_extract_uses_only_user_words_and_protects_existing_input(self):
        fake.REPLIES[:] = [
            "언제 있었던 일인가요?",
            '{"case_type": "중고거래 사기", "incident_location": "온라인 중고거래", '
            '"damage_details": "45만 원 송금", "suspect_info": "닉네임 판매왕", "witness": ""}',
        ]
        at = self.start()
        self.send(at, "온라인 중고거래에서 판매왕에게 45만 원을 보냈어요")
        at.session_state["case_field_suspect_info"] = "이미 적은 상대방 정보"
        at.run()
        self.button(at, "chat_extract_btn").click().run()
        prompt = fake.contents_text(fake.CALLS[-1])
        self.assertIn("판매왕에게 45만 원을 보냈어요", prompt)
        self.assertNotIn("언제 있었던 일인가요?", prompt)
        self.assertFalse(at.checkbox(key="chat_pick_suspect_info").value)  # 덮어쓰기는 기본 해제
        self.assertTrue(at.checkbox(key="chat_pick_incident_location").value)
        at.text_area(key="chat_edit_damage_details").set_value("45만 원 계좌이체")
        self.button(at, "chat_apply_btn").click().run()
        self.assertEqual(at.session_state["case_field_incident_location"], "온라인 중고거래")
        self.assertEqual(at.session_state["case_field_damage_details"], "45만 원 계좌이체")
        self.assertEqual(at.session_state["case_field_suspect_info"], "이미 적은 상대방 정보")
        self.assertEqual(at.session_state["case_type_choice"], "중고거래 사기")
        self.assertFalse([w for w in fake.WRITES if w[0] == "cases"])  # 자동 저장하지 않음

    # ── 계정 전환·로그아웃 ──
    def test_account_switch_and_logout_clear_chat(self):
        at = self.start()
        self.send(at, "이전 계정의 질문")
        at.session_state["supabase_session"] = fake.session_for(fake.USER_ID, "user@example.com")
        at.run()
        self.assertNotIn("chat_messages", at.session_state)
        at.session_state["supabase_session"] = fake.session_for(fake.ADMIN_ID, "admin@example.com")
        at.run()
        self.send(at, "다시 질문")
        self.button(at, "account_btn").click().run()  # 로그아웃은 '내 계정' 화면에 있다
        self.button(at, "logout_btn_main").click().run()
        self.assertNotIn("chat_messages", at.session_state)
        self.assertIsNone(at.session_state["supabase_session"])


if __name__ == "__main__":
    unittest.main()
