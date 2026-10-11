"""이용 연령 만 18세 수정안(기본은 14세 그대로)과 AI 기능 잠시 멈춤(AI_FEATURES_PAUSED) 검사.

- 기본 설정: 지금 시행 중인 약관·화면 문구(만 14세)가 바뀌지 않는다.
- MIN_USER_AGE=18 + TERMS_EFFECTIVE_DATE_V2: 가입·소셜 확인·동의 화면이 만 18세 기준, 기존 이용자는 동의 화면에서 다시 확인,
  만 18세 미만이라고 하면 이용을 막되 계정·자료를 지우지 않는다.
- AI 멈춤: Gemini를 부르지 않고 안내하며, 사건 저장은 그대로 된다.
실행: python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_services as fake  # noqa: E402

ENV = {"SUPABASE_URL": "https://fake.invalid", "SUPABASE_PUBLISHABLE_KEY": "fake", "GEMINI_API_KEY": "fake",
       "PRIVACY_POLICY_ENABLED": "true", "PRIVACY_OPERATOR_NAME": "운영자", "PRIVACY_CONTACT_EMAIL": "c@example.com",
       "PRIVACY_POLICY_EFFECTIVE_DATE": "2026-09-27", "APP_BASE_URL": "https://example.invalid", "AI_CALLS_PER_HOUR": "30"}
AGE18 = {"MIN_USER_AGE": "18", "TERMS_EFFECTIVE_DATE_V2": "2026-11-01"}


class Base(unittest.TestCase):
    def start(self, user_id=None, **env):
        fake.reset()
        patches = [mock.patch.dict(os.environ, {**ENV, **env}), mock.patch("supabase.create_client", fake.fake_create_client),
                   mock.patch("google.genai.Client", fake.FakeGenaiClient)]
        for patch in patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in reversed(patches)])
        at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
        at.session_state["browser_login_checked"] = True
        if user_id:
            at.session_state["supabase_session"] = fake.session_for(user_id, "x@example.com")
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        return at

    def texts(self, at):
        return " ".join([m.value for m in at.markdown] + [c.value for c in at.checkbox and [] or []] +
                        [c.label for c in at.checkbox] + [i.value for i in at.info] + [e.value for e in at.error])


class AgeTest(Base):
    def test_default_stays_14_and_terms_unchanged(self):
        at = self.start()
        labels = [c.label for c in at.checkbox]
        self.assertIn("[필수] 만 14세 이상입니다.", labels)
        self.assertNotIn("만 18세", self.texts(at))

    def test_18_without_terms_date_keeps_14(self):
        at = self.start(MIN_USER_AGE="18")
        self.assertIn("[필수] 만 14세 이상입니다.", [c.label for c in at.checkbox])

    def test_18_signup_uses_18(self):
        at = self.start(**AGE18)
        self.assertIn("[필수] 만 18세 이상입니다.", [c.label for c in at.checkbox])
        self.assertNotIn("만 14세", " ".join(c.label for c in at.checkbox))

    def test_existing_user_reconfirms_age_on_new_terms(self):
        at = self.start(**AGE18)
        fake.CONSENT_MISSING = True                      # 새 약관 버전 동의가 아직 없음(기존 이용자)
        at.session_state["supabase_session"] = fake.session_for(fake.USER_ID, "x@example.com")
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        text = self.texts(at)
        self.assertIn("만 18세 이상만 이용할 수 있습니다", text)      # 새 약관 조항
        at.checkbox(key="reconsent_terms").check()
        at.checkbox(key="reconsent_privacy").check()
        at.button(key="reconsent_submit").click().run()
        self.assertTrue(any("만 18세 이상 확인" in e.value for e in at.error))   # 나이 확인 없이는 못 넘어감
        self.assertFalse([c for c in fake.RPC_CALLS if c[0] == "record_consent"])
        at.checkbox(key="reconsent_age").check()
        at.button(key="reconsent_submit").click().run()
        call = [c for c in fake.RPC_CALLS if c[0] == "record_consent"][-1]
        self.assertEqual(call[1]["p_terms_version"], "2026-11-01")

    def test_under_18_is_blocked_without_deleting(self):
        at = self.start(**AGE18)
        fake.CONSENT_MISSING = True
        at.session_state["supabase_session"] = fake.session_for(fake.USER_ID, "x@example.com")
        at.run()
        at.button(key="reconsent_under_age").click().run()
        text = self.texts(at)
        self.assertIn("만 18세 이상만 이용할 수 있어요", text)
        self.assertIn("운영자가 임의로 지우지 않습니다", text)
        self.assertEqual([w for w in fake.WRITES if w[1] == "delete"], [])   # 아무것도 지우지 않음
        self.assertEqual([c for c in fake.RPC_CALLS if c[0] == "record_consent"], [])
        self.assertTrue(any(b.key == "delete_account_main" for b in at.button))  # 직접 탈퇴는 가능
        at.button(key="under_age_back").click().run()
        self.assertTrue(any(c.key == "reconsent_age" for c in at.checkbox))


class AiPauseTest(Base):
    def test_paused_ai_is_not_called_and_explained(self):
        at = self.start(user_id=fake.USER_ID, AI_FEATURES_PAUSED="true")
        at.session_state["nav_page"] = "chat"
        at.run()
        self.assertTrue(any("AI 기능(상담·초안 만들기·음성)을 잠시 멈췄어요" in i.value for i in at.info))
        at.session_state["nav_page"] = "case"
        at.run()
        self.assertTrue(any("잠시 멈췄어요" in i.value for i in at.info))
        self.assertEqual(fake.CALLS, [])

    def test_paused_chat_question_does_not_reach_gemini(self):
        at = self.start(user_id=fake.USER_ID, AI_FEATURES_PAUSED="true")
        at.session_state["nav_page"] = "chat"
        at.run()
        at.chat_input[0].set_value("상대방 이름을 모르면 어떻게 하나요?").run()
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        self.assertEqual(fake.CALLS, [])
        shown = " ".join(m.value for m in at.markdown) + " ".join(e.value for e in at.error)
        self.assertIn("잠시 멈췄어요", shown)

    def test_default_not_paused(self):
        at = self.start(user_id=fake.USER_ID)
        at.session_state["nav_page"] = "chat"
        at.run()
        self.assertFalse(any("잠시 멈췄어요" in i.value for i in at.info))


if __name__ == "__main__":
    unittest.main()
