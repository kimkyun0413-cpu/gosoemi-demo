"""화면 구성 검사: 처음 화면의 세 가지 길, 화면 이동, 화면을 옮겨도 적던 내용 유지, 고소장 작성 단계.

가짜 Supabase·Gemini로 실제 app.py를 실행한다. 실행: python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_services as fake  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = {
    "SUPABASE_URL": "https://fake.invalid", "SUPABASE_PUBLISHABLE_KEY": "fake", "GEMINI_API_KEY": "fake",
    "ADMIN_USER_IDS": "", "PRIVACY_POLICY_ENABLED": "true", "PRIVACY_OPERATOR_NAME": "운영자",
    "PRIVACY_CONTACT_EMAIL": "contact@example.com", "PRIVACY_POLICY_EFFECTIVE_DATE": "2026-09-27",
}


class NavigationTest(unittest.TestCase):
    def setUp(self):
        fake.reset()
        patches = [mock.patch.dict(os.environ, ENV), mock.patch("supabase.create_client", fake.fake_create_client),
                   mock.patch("google.genai.Client", fake.FakeGenaiClient)]
        for patch in patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in reversed(patches)])
        self.at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
        self.at.session_state["supabase_session"] = fake.session_for(fake.USER_ID, "user@example.com")
        self.at.session_state["browser_login_checked"] = True
        self.at.run()
        self.assertEqual([e.value for e in self.at.exception], [])

    def click(self, key):
        self.at.button(key=key).click().run()
        self.assertEqual([e.value for e in self.at.exception], [])

    def test_home_shows_three_main_choices_and_moves_between_pages(self):
        keys = {b.key for b in self.at.button}
        self.assertTrue({"home_go_chat", "home_go_case", "home_go_cases", "home_go_help"} <= keys)
        self.click("home_go_case")
        self.assertEqual(self.at.session_state["nav_page"], "case")
        self.assertTrue(any("고소장 작성 단계" in m.value for m in self.at.markdown))
        self.assertFalse(self.at.chat_input)  # 한 번에 한 화면만 그린다
        self.click("brand_home_btn")
        self.assertEqual(self.at.session_state["nav_page"], "home")
        self.click("home_go_chat")
        self.assertTrue(self.at.chat_input)

    def test_typed_case_text_survives_page_switch(self):
        self.click("home_go_case")
        self.at.text_area(key="case_field_incident_description").input("9월 2일 45만 원 송금").run()
        self.at.text_area(key="case_field_reporter_info").input("홍길동").run()
        self.click("brand_home_btn")
        self.click("home_go_case")
        self.assertEqual(self.at.text_area(key="case_field_incident_description").value, "9월 2일 45만 원 송금")
        self.assertEqual(self.at.text_area(key="case_field_reporter_info").value, "홍길동")

    def test_case_flow_steps_review_and_back_keeps_text(self):
        self.click("home_go_case")
        self.at.selectbox(key="case_type_choice").select("중고거래 사기").run()
        self.at.text_area(key="case_field_reporter_info").input("홍길동").run()
        self.at.text_area(key="case_field_incident_description").input("9월 2일 45만 원 송금").run()
        self.click("case_form_submit")
        self.assertIn("저장 동의", " ".join(e.value for e in self.at.error))
        self.at.checkbox(key="case_data_consent").check().run()
        self.click("case_form_submit")
        self.assertIsNotNone(self.at.session_state["case_review"])
        self.assertTrue(any('aria-current="step"><span>2' in m.value for m in self.at.markdown))
        self.click("case_review_cancel")
        self.assertEqual(self.at.text_area(key="case_field_incident_description").value, "9월 2일 45만 원 송금")

    def test_cases_page_empty_state_offers_writing(self):
        self.at.session_state["nav_page"] = "cases"
        self.at.run()
        self.assertTrue(any(b.key == "cases_go_case" for b in self.at.button))

    def test_open_case_shows_sections_and_delete_needs_confirmation(self):
        case_id = "22222222-2222-4222-8222-222222222222"
        fake.CASES.append({"case_id": case_id, "user_id": fake.USER_ID, "case_type": "투자 사기",
                           "incident_description": "300만 원 입금", "created_at": "2026-10-02T00:00:00+00:00"})
        self.at.session_state["nav_page"] = "cases"
        self.at.run()
        self.click(f"open_case_{case_id}")
        labels = [e.label for e in self.at.expander]
        self.assertTrue({"고소장 초안", "입력한 내용", "이 사건 삭제"} <= set(labels))
        self.assertTrue(self.at.button(key=f"delete_button_{case_id}").disabled)
        self.at.checkbox(key=f"confirm_delete_{case_id}").check().run()
        self.click(f"delete_button_{case_id}")
        self.assertIn("사건을 삭제했어요.", [s.value for s in self.at.success])
        self.assertTrue(any(b.key == "cases_go_case" for b in self.at.button))

    def test_account_page_has_logout_policy_and_delete(self):
        self.click("account_btn")
        self.assertEqual(self.at.session_state["nav_page"], "account")
        keys = {b.key for b in self.at.button}
        self.assertTrue({"logout_btn_main", "privacy_policy_view_btn_main", "terms_of_service_view_btn_main",
                         "delete_account_main"} <= keys)
        self.click("tab_nav_chat")  # 아래 메뉴로 다른 화면 고르기
        self.assertEqual(self.at.session_state["nav_page"], "chat")

    def test_public_chat_stays_disabled_until_launch_is_ready(self):
        fake.REPLIES[:] = ["가짜 답변입니다."]
        self.click("home_go_chat")
        self.assertEqual(self.at.chat_input(key="public_chat_input").proto.placeholder, "지금은 질문을 보낼 수 없어요")
        self.assertTrue(self.at.chat_input(key="public_chat_input").disabled)
        self.assertFalse(any(b.key == "chat_new_btn" for b in self.at.button))  # 새 기능은 켜지 않는다
        self.assertEqual(fake.CALLS, [])

    def scroll_marks(self):
        return [e for e in self.at.get("bidi_component") if "gosoemi_scroll_top" in str(e.proto)]

    def test_scroll_to_top_only_when_view_changes(self):
        self.click("home_go_case")
        self.assertTrue(self.scroll_marks())        # 화면이 바뀐 실행에서만 맨 위로 올리는 부품을 그린다
        self.at.text_area(key="case_field_reporter_info").input("홍길동").run()
        self.assertFalse(self.scroll_marks())       # 같은 화면에서 입력할 때는 올리지 않는다

    def nav_keys(self):
        return {b.key for b in self.at.button if b.key and b.key.startswith(("side_nav_", "tab_nav_"))}

    def test_menu_is_on_every_page_and_home_cards_use_short_texts(self):
        # A안: 홈에서도 메뉴(넓은 화면 왼쪽·휴대폰 아래)가 보인다. 아래 메뉴는 다섯 칸.
        self.assertEqual({k for k in self.nav_keys() if k.startswith("tab_nav_")},
                         {"tab_nav_home", "tab_nav_cases", "tab_nav_case", "tab_nav_chat", "tab_nav_search"})
        self.assertIn("side_nav_help", self.nav_keys())
        texts = " ".join(m.value for m in list(self.at.markdown) + list(self.at.caption))
        self.assertIn("AI 기능을 점검 중입니다.", texts)  # 일반 공개 준비 전
        self.assertIn("사건 내용을 정리해 저장해요. (초안 만들기는 점검 중)", texts)
        self.assertIn("저장한 사건을 확인하세요.", texts)
        self.assertNotIn("초안을 다시", texts)
        self.click("tab_nav_search")
        self.assertEqual(self.at.session_state["nav_page"], "search")
        self.assertTrue(any("고소미 안에서 바로 찾아보는 기능은 준비 중" in i.value for i in self.at.info))
        links = [b.proto.url for b in self.at.get("link_button")]
        self.assertTrue(any("precSc.do" in u for u in links) and any("lsSc.do" in u for u in links))
        self.click("side_nav_cases")
        self.assertEqual(self.at.session_state["nav_page"], "cases")

    def test_continue_card_opens_latest_case(self):
        case_id = "44444444-4444-4444-8444-444444444444"
        fake.CASES.append({"case_id": case_id, "user_id": fake.USER_ID, "case_type": "투자 사기",
                           "incident_description": "300만 원 입금", "created_at": "2026-10-02T00:00:00+00:00"})
        self.click("tab_nav_cases")
        self.click("tab_nav_home")
        self.click("home_continue_btn")
        self.assertEqual(self.at.session_state["nav_page"], "cases")
        self.assertEqual(self.at.session_state["open_case_id"], case_id)

    def test_cases_caption_does_not_promise_saved_drafts_when_saving_is_off(self):
        fake.CASES.append({"case_id": "33333333-3333-4333-8333-333333333333", "user_id": fake.USER_ID,
                           "case_type": "투자 사기", "created_at": "2026-10-02T00:00:00+00:00"})
        self.click("home_go_cases")
        captions = " ".join(c.value for c in self.at.caption)
        self.assertIn("새 초안 만들기", captions)
        self.assertNotIn("저장한 초안", captions)
        self.assertFalse(any(b.key and b.key.startswith("list_draft_open_") for b in self.at.button))


if __name__ == "__main__":
    unittest.main()

