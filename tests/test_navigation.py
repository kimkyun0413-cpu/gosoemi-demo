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
        self.at.session_state["nav_choice"] = "chat"  # 위쪽 막대로 다른 화면 고르기
        self.at.run()
        self.assertEqual([e.value for e in self.at.exception], [])


if __name__ == "__main__":
    unittest.main()
