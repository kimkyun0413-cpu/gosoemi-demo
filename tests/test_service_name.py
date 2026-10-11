"""서비스 이름 '고소미' 검사(2026-10-11 '고소음이'에서 변경).

화면·내려받는 문서·일정 파일에 옛 이름이 남지 않았는지, 기술 식별자(로그인 저장 키·화면 부품 이름·일정 고유 번호)는
그대로인지 확인한다. 실행: python3 -m unittest discover -s tests
"""

import datetime
import os
import re
import sys
import unittest
from unittest import mock

from docx import Document
from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import calendar_ics as cal  # noqa: E402
import case_guide  # noqa: E402
import case_workspace as w  # noqa: E402
import fake_services as fake  # noqa: E402
import policy_text  # noqa: E402

OLD, NEW = "고소음이", "고소미"
USER_FACING = ["app.py", "case_guide.py", "case_workspace.py", "calendar_ics.py", "precedent_search.py",
               "chat_assistant.py", "voice_input.py", "billing.py"]
# 옛 이름은 '이전 이름' 안내에만 남는다.
ALLOWED_OLD = ["이전 이름: 고소음이", "이전 서비스명: 고소음이", "고소음이 → 고소미"]


def strip_allowed(text: str) -> str:
    for phrase in ALLOWED_OLD:
        text = text.replace(phrase, "")
    return text


class SourceTest(unittest.TestCase):
    def test_old_name_only_in_previous_name_notes(self):
        for name in USER_FACING + ["policy_text.py"]:
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                self.assertNotIn(OLD, strip_allowed(f.read()), name)

    def test_technical_identifiers_unchanged(self):
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            src = f.read()
        for ident in ("'gosoemi_login_v1'", "'gosoemi_tab_login_v1'", '"gosoemi_browser_session"', '"gosoemi_scroll_top"',
                      "gosoemi-demo-csapxr3rjt2sjg66te9wrt.streamlit.app"):
            self.assertIn(ident, src)
        event = cal.clean_event({"title": "제출", "date": datetime.date(2026, 10, 20)})
        self.assertTrue(event["uid"].endswith("@gosoemi"))   # 이미 넣은 일정과 겹치지 않게 그대로 둔다

    def test_terms_and_ai_keep_previous_name_note(self):
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('제공하는 "고소미"(이전 서비스명: 고소음이, 이하 "서비스")', src)   # 이용약관(이미 동의받은 문구)
        self.assertIn('이다(이전 이름: 고소음이)', src)                                 # AI에게 알려 주는 소개
        self.assertNotIn("이전 서비스명: 고소미", src)
        self.assertNotIn("이전 이름: 고소미", src)

    def test_ai_is_told_new_name(self):
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            src = f.read()
        identity = src[src.index("SERVICE_IDENTITY_INSTRUCTION = ("):src.index("SERVICE_IDENTITY_INSTRUCTION = (") + 600]
        self.assertIn('이 서비스의 이름은 \\"고소미\\"이다', identity)


class DocumentTest(unittest.TestCase):
    def test_policy_and_terms_show_new_name_and_keep_versions(self):
        v2 = policy_text.build_privacy_policy_v2("운영자", "a@example.com", "2026-09-27")
        v3 = policy_text.build_privacy_policy_v3("운영자", "a@example.com", "2026-10-13")
        self.assertIn('"고소미"(이전 서비스명: 고소음이, 이하 "서비스")', v2)
        self.assertIn('"고소미"(이하 "서비스")', v3)
        self.assertEqual(policy_text.PRIVACY_V3_REVISED_DATE, datetime.date(2026, 10, 13))

    def test_calendar_and_bundle_use_new_name(self):
        ics = cal.build_ics([{"title": "상담", "date": datetime.date(2026, 10, 20)}], "중고거래 사기")
        self.assertIn("SUMMARY:[고소미] 상담", ics)
        self.assertNotIn(OLD, ics)
        text = w.bundle_text("고소장\n본문", [{"when": "", "what": "송금", "evidence": ""}],
                             [{"name": "채팅 캡처", "shows": "", "source": ""}], {"draft": True, "timeline": True, "evidence": True})
        self.assertIn("고소미", text)
        self.assertNotIn(OLD, text)
        for doc in (w.bundle_docx(text), case_guide.build_case_draft_docx("고소장\n본문")):
            body = "\n".join(p.text for p in Document(doc).paragraphs)
            self.assertNotIn(OLD, body)
        self.assertNotIn(OLD, case_guide.with_draft_disclaimer("고소장\n본문"))


class ScreenTest(unittest.TestCase):
    ENV = {
        "SUPABASE_URL": "https://fake.invalid", "SUPABASE_PUBLISHABLE_KEY": "fake", "GEMINI_API_KEY": "fake",
        "ADMIN_USER_IDS": fake.ADMIN_ID, "PRIVACY_POLICY_ENABLED": "true", "PRIVACY_OPERATOR_NAME": "운영자",
        "PRIVACY_CONTACT_EMAIL": "contact@example.com", "PRIVACY_POLICY_EFFECTIVE_DATE": "2026-09-27",
    }

    def setUp(self):
        fake.reset()
        patches = [mock.patch.dict(os.environ, self.ENV), mock.patch("supabase.create_client", fake.fake_create_client),
                   mock.patch("google.genai.Client", fake.FakeGenaiClient)]
        for patch in patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in reversed(patches)])

    def visible_text(self, at) -> str:
        parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + [b.label for b in at.button]
        parts += [e.value for e in at.info] + [e.value for e in at.warning] + [e.value for e in at.error]
        return re.sub(r"<style>.*?</style>", "", " ".join(parts), flags=re.S)

    def test_login_screen_and_pages_show_new_name(self):
        at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
        at.session_state["browser_login_checked"] = True
        at.run()
        text = self.visible_text(at)                       # 로그인 전 화면
        self.assertIn(NEW, text)
        self.assertNotIn(OLD, text)
        at.session_state["supabase_session"] = fake.session_for(fake.USER_ID, "x@example.com")
        at.run()
        for page in ("home", "chat", "case", "cases", "help", "account"):
            at.session_state["nav_page"] = page
            at.run()
            self.assertEqual([e.value for e in at.exception], [])
            text = self.visible_text(at)
            self.assertNotIn(OLD, text, page)
        self.assertTrue(any(b.label == NEW for b in at.button))  # 위쪽 이름 버튼


if __name__ == "__main__":
    unittest.main()
