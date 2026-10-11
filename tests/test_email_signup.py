"""이메일 가입: 메일 서버(SMTP) 연결 전 안내와 운영자가 이메일 가입만 잠시 닫는 설정(EMAIL_SIGNUP_ENABLED) 검사.

Supabase 기본 메일은 프로젝트 팀원 주소로만 보낸다(공식 문서). 일반 이용자가 이메일로 가입하면 인증 메일을 받지 못한다.
실행: python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest
from supabase_auth.errors import AuthApiError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_services as fake  # noqa: E402

ENV = {"SUPABASE_URL": "https://fake.invalid", "SUPABASE_PUBLISHABLE_KEY": "fake", "GEMINI_API_KEY": "fake",
       "PRIVACY_POLICY_ENABLED": "true", "PRIVACY_OPERATOR_NAME": "운영자", "PRIVACY_CONTACT_EMAIL": "c@example.com",
       "PRIVACY_POLICY_EFFECTIVE_DATE": "2026-09-27", "APP_BASE_URL": "https://example.invalid",
       "GOOGLE_LOGIN_ENABLED": "true"}

class EmailSignupTest(unittest.TestCase):
    def start(self, **env):
        patches = [mock.patch.dict(os.environ, {**ENV, **env}), mock.patch("supabase.create_client", fake.fake_create_client)]
        for patch in patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in reversed(patches)])
        at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
        at.session_state["browser_login_checked"] = True
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        return at

    def test_default_keeps_email_signup_form(self):
        at = self.start()
        self.assertTrue(any(t.key == "signup_email" for t in at.text_input))

    def test_operator_can_pause_email_signup_only(self):
        at = self.start(EMAIL_SIGNUP_ENABLED="false")
        self.assertFalse(any(t.key == "signup_email" for t in at.text_input))
        self.assertTrue(any("이메일 가입은 인증 메일 준비 중" in i.value for i in at.info))
        self.assertTrue(any(t.key == "login_email" for t in at.text_input))   # 이미 가입한 이메일 로그인은 그대로

    def test_send_failure_message_points_to_social_login(self):
        source = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
        start = source.index("def map_auth_error(")
        end = source.index("\ndef ", start + 10)
        scope = {"AuthApiError": AuthApiError}
        import re
        exec(re.search(r"EMAIL_SEND_FAILED_MESSAGE = \(\n.*?\n\)", source, re.S).group(0), scope)
        exec(source[start:end], scope)
        mapper = scope["map_auth_error"]
        msg = mapper(AuthApiError("Email address not authorized", 400, "email_address_not_authorized"))
        self.assertIn("네이버 또는 Google로 가입", msg)
        msg = mapper(AuthApiError("Error sending confirmation email", 500, "unexpected_failure"))
        self.assertIn("네이버 또는 Google로 가입", msg)
        self.assertIn("이메일 또는 비밀번호", mapper(AuthApiError("x", 400, "invalid_credentials")))


if __name__ == "__main__":
    unittest.main()
