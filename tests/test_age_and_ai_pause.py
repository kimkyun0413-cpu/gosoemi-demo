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


def social_session(user_id, app_metadata):
    """Google·네이버로 처음 로그인한(연령 확인 전) 가짜 세션."""
    import base64
    import json

    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    session = fake.session_for(user_id, "s@example.com")
    session["access_token"] = ".".join([seg({"alg": "none"}), seg({"sub": user_id, "app_metadata": app_metadata}), "sig"])
    return session


class Age18FlowTest(Base):
    """만 18세 기준: 재확인 거부·미충족·동의 저장 실패·소셜 첫 확인·사전 안내·문구 일치."""
    MAIN_KEYS = {"reconsent_submit"}

    def existing_user(self, **env):
        at = self.start(**{**AGE18, **env})
        fake.CONSENT_MISSING = True                      # 새 약관 동의 전(기존 이용자)
        at.session_state["supabase_session"] = fake.session_for(fake.USER_ID, "x@example.com")
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        return at

    def assert_restricted(self, at, title):
        self.assertEqual([e.value for e in at.exception], [])
        text = self.texts(at)
        self.assertIn(title, text)
        self.assertIn("운영자가 임의로 지우지 않습니다", text)
        keys = {b.key for b in at.button}
        self.assertTrue({"export_prepare_under_age", "delete_account_main", "under_age_back", "under_age_logout"} <= keys)
        self.assertNotIn("reconsent_submit", keys)
        self.assertFalse(keys & {"account_btn", "home_go_chat", "home_go_case"})        # 서비스 화면은 열리지 않음
        self.assertEqual(fake.CALLS, [])                                                 # AI 호출 없음
        self.assertEqual([w for w in fake.WRITES if w[1] in ("delete", "remove")], [])   # 아무것도 지우지 않음
        self.assertEqual([c for c in fake.RPC_CALLS if c[0] == "record_consent"], [])

    def test_decline_shows_download_and_withdrawal_only(self):
        at = self.existing_user()
        at.button(key="reconsent_decline").click().run()
        self.assert_restricted(at, "바뀐 약관에 동의해야 이용할 수 있어요")
        at.button(key="export_prepare_under_age").click().run()                 # 본인 자료 내려받기는 됨
        self.assertTrue(at.session_state["_my_export_under_age"]["text_part"])
        at.button(key="under_age_back").click().run()                           # 마음을 바꾸면 동의 화면으로
        self.assertTrue(any(b.key == "reconsent_submit" for b in at.button))

    def test_menu_check_sees_service_screen_when_consented(self):
        at = self.start(**AGE18)                                                         # 위 확인이 헛돌지 않는지(대조군)
        at.session_state["supabase_session"] = fake.session_for(fake.USER_ID, "x@example.com")
        at.run()
        self.assertTrue({"account_btn", "home_go_chat", "home_go_case"} <= {b.key for b in at.button})

    def test_unchecked_age_is_not_saved(self):
        at = self.existing_user()
        at.checkbox(key="reconsent_terms").check()
        at.checkbox(key="reconsent_privacy").check()
        at.button(key="reconsent_submit").click().run()
        self.assertTrue(any("만 18세 이상 확인" in e.value for e in at.error))
        self.assertEqual([c for c in fake.RPC_CALLS if c[0] == "record_consent"], [])
        self.assertTrue(any(b.key == "reconsent_submit" for b in at.button))

    def test_consent_save_failure_keeps_user_on_consent_screen(self):
        at = self.existing_user()
        fake.FAIL_NEXT.append(("rpc:record_consent", "select"))
        for key in ("reconsent_terms", "reconsent_privacy", "reconsent_age"):
            at.checkbox(key=key).check()
        at.button(key="reconsent_submit").click().run()
        self.assertEqual([e.value for e in at.exception], [])
        self.assertTrue(any("동의 내용을 저장하지 못했습니다" in e.value for e in at.error))
        self.assertTrue(any(b.key == "reconsent_submit" for b in at.button))   # 이용 화면으로 넘어가지 않음
        self.assertFalse(any(b.key == "home_go_chat" for b in at.button))
        self.assertEqual(fake.CALLS, [])

    def test_google_first_login_under_18(self):
        at = self.start(**AGE18)
        at.session_state["supabase_session"] = social_session(fake.USER_ID, {"provider": "google"})
        at.run()
        self.assertIn("[필수] 만 18세 이상입니다.", [c.label for c in at.checkbox])
        self.assertNotIn("만 14세", self.texts(at))
        at.button(key="google_under_age").click().run()
        self.assert_restricted(at, "만 18세 이상만 이용할 수 있어요")

    def test_naver_first_login_under_18(self):
        at = self.start(**AGE18)
        at.session_state["supabase_session"] = social_session(fake.USER_ID, {"provider": "email", "naver_linked": True})
        at.run()
        self.assertIn("[필수] 만 18세 이상입니다.", [c.label for c in at.checkbox])
        at.button(key="naver_under_age").click().run()
        self.assert_restricted(at, "만 18세 이상만 이용할 수 있어요")

    def test_restriction_does_not_carry_to_next_account(self):
        at = self.existing_user()
        at.button(key="reconsent_under_age").click().run()
        at.session_state["supabase_session"] = fake.session_for(fake.ADMIN_ID, "other@example.com")
        at.run()
        self.assertTrue(any(b.key == "reconsent_submit" for b in at.button))
        self.assertFalse(any(b.key == "delete_account_main" for b in at.button))

    def test_terms_policy_and_screens_say_18_together(self):
        at = self.existing_user(PRIVACY_POLICY_EFFECTIVE_DATE="2026-11-01")   # 새 약관과 방침 v3를 같은 날 시행
        text = self.texts(at)
        self.assertNotIn("만 14세", text)
        self.assertIn("서비스는 만 18세 이상만 이용할 수 있습니다", text)          # 약관 제4조
        self.assertIn("본 서비스는 만 18세 이상만 이용할 수 있습니다", text)       # 방침 이용 연령
        self.assertIn("내 자료 모두 내려받기", text)                               # 방침 권리 행사
        self.assertIn("[필수] 만 18세 이상입니다.", [c.label for c in at.checkbox])
        at.checkbox(key="reconsent_terms").check()
        at.checkbox(key="reconsent_privacy").check()
        at.checkbox(key="reconsent_age").check()
        at.button(key="reconsent_submit").click().run()
        call = [c for c in fake.RPC_CALLS if c[0] == "record_consent"][-1][1]
        self.assertEqual((call["p_terms_version"], call["p_privacy_version"]), ("2026-11-01", "2026-11-01"))

    def test_default_14_consent_screen_unchanged(self):
        at = self.start()
        fake.CONSENT_MISSING = True
        at.session_state["supabase_session"] = fake.session_for(fake.USER_ID, "x@example.com")
        at.run()
        keys = {b.key for b in at.button}
        self.assertIn("reconsent_logout_btn", keys)
        self.assertFalse(keys & {"reconsent_decline", "reconsent_under_age"})
        self.assertTrue(any(b.label == "동의하지 않고 로그아웃" for b in at.button))

    def test_upcoming_age_notice_before_effective_date(self):
        at = self.start(TERMS_V2_UPCOMING_DATE="2099-01-01")
        labels = [e.label for e in at.expander]
        self.assertTrue(any("2099-01-01부터 고소미는 만 18세 이상만" in label for label in labels), labels)
        self.assertIn("내 자료 모두 내려받기", self.texts(at))
        past = self.start(TERMS_V2_UPCOMING_DATE="2020-01-01")
        self.assertFalse(any("만 18세 이상만" in e.label for e in past.expander))
        live = self.start(TERMS_V2_UPCOMING_DATE="2099-01-01", **AGE18)            # 시행 뒤(18세 설정)에는 사라짐
        self.assertFalse(any("만 18세 이상만" in e.label for e in live.expander))


class AiPauseTest(Base):
    MSG = "AI 기능을 점검 중입니다. 저장한 사건은 계속 확인할 수 있습니다."

    def test_chat_is_closed_with_notice_and_no_gemini_call(self):
        at = self.start(user_id=fake.USER_ID, AI_FEATURES_PAUSED="true")
        at.session_state["nav_page"] = "chat"
        at.run()
        self.assertTrue(any(i.value == self.MSG for i in at.info))
        self.assertTrue(at.chat_input[0].disabled)
        self.assertEqual(fake.CALLS, [])

    def test_admin_chat_voice_and_ai_buttons_closed(self):
        at = self.start(user_id=fake.ADMIN_ID, AI_FEATURES_PAUSED="true", ADMIN_USER_IDS=fake.ADMIN_ID)
        at.session_state["nav_page"] = "chat"
        at.session_state["chat_mode"] = "voice"           # 음성 대화를 열어 둔 상태였어도
        at.run()
        self.assertFalse(any(b.key == "chat_voice_open_btn" for b in at.button))
        self.assertFalse(at.get("bidi_component") and any("voice_chat" in str(e.proto) for e in at.get("bidi_component")))
        self.assertTrue(at.chat_input[0].disabled)
        at.session_state["nav_page"] = "case"
        at.run()
        self.assertFalse(any(b.key in ("case_start_text", "case_start_voice") for b in at.button))

    def test_case_can_be_saved_viewed_and_downloaded_without_ai(self):
        at = self.start(user_id=fake.USER_ID, AI_FEATURES_PAUSED="true")
        at.session_state["nav_page"] = "case"
        at.run()
        self.assertTrue(any(i.value == self.MSG for i in at.info))
        at.selectbox(key="case_type_choice").select("중고거래 사기").run()
        at.text_area(key="case_field_reporter_info").input("홍길동(시험)").run()
        at.text_area(key="case_field_incident_description").input("9월 2일 45만 원 송금 후 연락 끊김").run()
        at.checkbox(key="case_data_consent").check().run()
        at.button(key="case_form_submit").click().run()
        confirm = at.button(key="case_review_confirm")
        self.assertEqual(confirm.label, "사건 저장하기")
        confirm.click().run()
        self.assertEqual([e.value for e in at.exception], [])
        self.assertTrue([w for w in fake.WRITES if w[0] == "cases" and w[1] == "insert"])   # 사건은 저장
        self.assertEqual(fake.CALLS, [])                                                  # AI는 부르지 않음
        self.assertTrue(any("사건을 저장했어요" in m.value for m in at.success))
        self.assertFalse(any(b.key == "retry_draft_button" for b in at.button))    # 누를 수 없는 버튼은 숨김
        self.assertTrue(any(b.key == "pending_open_case" for b in at.button))
        # 내 사건에서 저장한 사건 보기·초안 버튼 닫힘
        saved = [w for w in fake.WRITES if w[0] == "cases" and w[1] == "insert"][0][2][0]
        fake.CASES.append(dict(saved))
        at.session_state["nav_page"] = "cases"
        at.session_state["open_case_id"] = saved["case_id"]
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        self.assertTrue(any("9월 2일 45만 원" in m.value for m in at.markdown) or
                        any("9월 2일 45만 원" in str(t.value) for t in at.text_area))
        self.assertTrue(at.button(key=f"list_draft_btn_{saved['case_id']}").disabled)
        at.session_state["nav_page"] = "account"
        at.run()
        self.assertTrue(any(b.key == "delete_account_main" for b in at.button))       # 회원탈퇴는 그대로

    def test_default_not_paused(self):
        at = self.start(user_id=fake.USER_ID)
        at.session_state["nav_page"] = "chat"
        at.run()
        self.assertFalse(any(i.value == self.MSG for i in at.info))
        self.assertFalse(at.chat_input[0].disabled)


if __name__ == "__main__":
    unittest.main()
