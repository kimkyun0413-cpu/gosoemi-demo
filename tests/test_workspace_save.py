"""사건 작업 공간 저장·다시 열기·저장한 사건 수정·동시 수정·저장 실패 검사(가짜 Supabase로 실제 app.py 실행).

접근 제한(본인만)·사건 삭제·탈퇴·되돌리기는 실제 Postgres에서 supabase/tests/local_case_workspace_edit_test.py로 검사한다.
실행: python3 -m unittest discover -s tests
"""

import datetime
import os
import sys
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_services as fake  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CID = "55555555-5555-4555-8555-555555555555"
CASE = {
    "case_id": CID, "user_id": fake.ADMIN_ID, "case_type": "중고거래 사기", "reporter_info": "홍길동(시험)",
    "incident_datetime": "9월 2일", "incident_description": "9월 2일 45만 원 송금 → 9월 4일부터 연락 끊김",
    "damage_details": "45만 원", "evidence": "채팅 캡처", "suspect_info": "닉네임 판매왕", "witness": "없음",
    "punishment_intent": "원함", "edit_version": 1, "created_at": "2026-10-01T00:00:00+00:00",
}
ENV = {
    "SUPABASE_URL": "https://fake.invalid", "SUPABASE_PUBLISHABLE_KEY": "fake", "GEMINI_API_KEY": "fake",
    "ADMIN_USER_IDS": fake.ADMIN_ID, "PRIVACY_POLICY_ENABLED": "true", "PRIVACY_OPERATOR_NAME": "운영자",
    "PRIVACY_CONTACT_EMAIL": "contact@example.com", "PRIVACY_POLICY_EFFECTIVE_DATE": "2026-09-27",
    "CASE_WORKSPACE_SAVE_ENABLED": "true", "DRAFT_SAVE_ENABLED": "true", "AI_CALLS_PER_HOUR": "30",
}


class WorkspaceSaveTest(unittest.TestCase):
    def setUp(self):
        fake.reset()
        fake.CASES.append(dict(CASE))
        patches = [mock.patch.dict(os.environ, ENV), mock.patch("supabase.create_client", fake.fake_create_client),
                   mock.patch("google.genai.Client", fake.FakeGenaiClient)]
        for patch in patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in reversed(patches)])

    def open(self, step=None, user_id=fake.ADMIN_ID):
        at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
        at.session_state["supabase_session"] = fake.session_for(user_id, "x@example.com")
        at.session_state["browser_login_checked"] = True
        at.run()
        at.session_state["nav_page"] = "cases"
        at.session_state["open_case_id"] = CID
        if step:
            at.session_state[f"ws_step_{CID}"] = step
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        return at

    def save_button(self, at):
        return at.button(key=f"ws_save_{CID}")

    def texts(self, at):
        return " ".join(m.value for m in at.markdown)

    def workspace_rows(self):
        return fake.TABLES["case_workspace"]

    def test_save_bar_starts_clean_then_shows_unsaved_change(self):
        at = self.open(step="check")
        self.assertIn("아직 저장하지 않았어요.", self.texts(at))
        self.assertTrue(self.save_button(at).disabled)
        at.text_input(key=f"ws_edit_{CID}_incident_location").input("○○마켓 앱 채팅").run()
        self.assertIn("저장하지 않은 변경이 있어요.", self.texts(at))
        self.assertFalse(self.save_button(at).disabled)

    def test_case_edit_and_workspace_are_saved_and_reopened(self):
        at = self.open(step="check")
        at.text_input(key=f"ws_edit_{CID}_incident_location").input("○○마켓 앱 채팅").run()
        at.session_state[f"ws_step_{CID}"] = "next"
        at.run()
        at.checkbox(key=f"prog_{CID}_story").check().run()
        p = f"ws_sch_{CID}_"
        at.text_input(key=p + "title").input("경찰서 제출").run()
        at.date_input(key=p + "date").set_value(datetime.date(2026, 10, 20)).run()
        at.button(key=p + "add").click().run()
        self.save_button(at).click().run()
        self.assertEqual([e.value for e in at.exception], [])
        self.assertIn("저장했어요", self.texts(at))
        self.assertEqual(fake.CASES[0]["incident_location"], "○○마켓 앱 채팅")
        self.assertEqual(fake.CASES[0]["edit_version"], 2)
        row = self.workspace_rows()[0]
        self.assertEqual((row["user_id"], row["case_id"], row["version"]), (fake.ADMIN_ID, CID, 1))
        self.assertTrue(row["checklist"]["story"])
        self.assertEqual(row["schedules"][0]["date"], "2026-10-20")
        # 새로 열면(다른 세션) 저장한 내용이 그대로 보인다
        again = self.open(step="next")
        self.assertTrue(again.checkbox(key=f"prog_{CID}_story").value)
        self.assertEqual(again.session_state[f"ws_{CID}"]["schedules"][0]["title"], "경찰서 제출")
        self.assertIn("마지막 저장", self.texts(again))
        self.assertTrue(self.save_button(again).disabled)

    def test_save_failure_keeps_input_and_retry_works(self):
        at = self.open(step="check")
        at.text_input(key=f"ws_edit_{CID}_witness").input("친구 김○○").run()
        fake.FAIL_NEXT.append(("cases", "update"))
        self.save_button(at).click().run()
        self.assertTrue(any("저장하지 못했어요" in e.value for e in at.error))
        self.assertEqual(at.text_input(key=f"ws_edit_{CID}_witness").value, "친구 김○○")
        self.assertEqual(at.session_state[f"ws_{CID}"]["answers"], {"witness": "친구 김○○"})
        self.assertEqual(fake.CASES[0]["witness"], "없음")
        self.save_button(at).click().run()
        self.assertEqual(fake.CASES[0]["witness"], "친구 김○○")

    def test_workspace_conflict_is_not_overwritten_without_choice(self):
        at = self.open(step="next")
        at.checkbox(key=f"prog_{CID}_story").check().run()
        self.save_button(at).click().run()
        other = self.open(step="next")                    # 다른 탭
        other.checkbox(key=f"prog_{CID}_draft").check().run()
        self.save_button(other).click().run()             # 다른 탭이 먼저 저장(version 2)
        at.checkbox(key=f"prog_{CID}_evidence").check().run()
        self.save_button(at).click().run()                # 옛 version(1)로 저장 시도
        self.assertTrue(any("먼저 저장했어요" in w.value for w in at.warning))
        row = self.workspace_rows()[0]
        self.assertEqual(row["version"], 2)
        self.assertTrue(row["checklist"]["draft"])        # 다른 탭 내용이 그대로
        self.assertFalse(row["checklist"]["evidence"])
        at.button(key=f"ws_take_{CID}").click().run()     # 먼저 저장된 내용 불러오기
        self.assertTrue(at.checkbox(key=f"prog_{CID}_draft").value)
        self.assertFalse(at.checkbox(key=f"prog_{CID}_evidence").value)

    def test_conflict_overwrite_after_confirmation(self):
        at = self.open(step="next")
        at.checkbox(key=f"prog_{CID}_story").check().run()
        self.save_button(at).click().run()
        fake.TABLES["case_workspace"][0]["version"] = 5  # 다른 곳에서 저장됨
        at.checkbox(key=f"prog_{CID}_review").check().run()
        self.save_button(at).click().run()
        self.assertTrue(any(b.key == f"ws_mine_{CID}" for b in at.button))
        at.button(key=f"ws_mine_{CID}").click().run()
        row = self.workspace_rows()[0]
        self.assertTrue(row["checklist"]["review"])
        self.assertEqual(row["version"], 6)

    def test_case_edit_conflict(self):
        at = self.open(step="check")
        fake.CASES[0]["edit_version"] = 3                  # 다른 곳에서 사건을 고침
        fake.CASES[0]["damage_details"] = "50만 원"
        at.text_input(key=f"ws_edit_{CID}_witness").input("친구").run()
        self.save_button(at).click().run()
        self.assertTrue(any("사건 내용을 먼저 고쳤어요" in w.value for w in at.warning))
        self.assertEqual(fake.CASES[0]["witness"], "없음")
        at.button(key=f"ws_take_{CID}").click().run()
        self.assertEqual(at.session_state[f"ws_{CID}"]["answers"], {})

    def test_draft_saved_through_workspace_and_conflict_checked(self):
        fake.REPLIES[:] = ["고소장\n범죄 사실\n9월 2일 45만 원 송금"]
        at = self.open(step="draft")
        at.button(key=f"ws_make_{CID}").click().run()
        self.assertFalse(any(b.key == f"list_draft_editor_{CID}_save" for b in at.button))  # 저장 버튼은 위쪽 하나
        self.save_button(at).click().run()
        self.assertEqual(len(fake.TABLES["case_drafts"]), 1)
        at.text_area(key=f"list_draft_editor_{CID}").input("고소장\n범죄 사실\n고친 내용").run()
        fake.TABLES["case_drafts"][0]["updated_at"] = "2026-10-11T09:00:00+00:00"  # 다른 곳에서 초안 저장
        self.save_button(at).click().run()
        self.assertTrue(any("초안을 먼저 저장했어요" in w.value for w in at.warning))
        self.assertNotIn("고친 내용", fake.TABLES["case_drafts"][0]["draft_text"])

    def test_partial_save_case_ok_workspace_failed_then_retry_only_rest(self):
        at = self.open(step="check")
        at.text_input(key=f"ws_edit_{CID}_witness").input("친구").run()
        at.session_state[f"ws_step_{CID}"] = "next"
        at.run()
        at.checkbox(key=f"prog_{CID}_story").check().run()
        fake.FAIL_NEXT.append(("case_workspace", "insert"))
        self.save_button(at).click().run()
        error = " ".join(e.value for e in at.error)
        self.assertIn("저장한 것: 사건 정보", error)
        self.assertIn("아직 저장 못 한 것: 정리 내용(경위·증거·일정·체크)", error)
        self.assertNotIn("저장했어요 ·", self.texts(at))
        self.assertEqual(fake.CASES[0]["witness"], "친구")
        self.assertEqual(self.workspace_rows(), [])
        self.assertTrue(at.checkbox(key=f"prog_{CID}_story").value)  # 입력 그대로
        self.save_button(at).click().run()                           # 다시 누르면 남은 것만
        self.assertEqual(fake.CASES[0]["edit_version"], 2)            # 사건은 다시 저장하지 않음
        self.assertTrue(self.workspace_rows()[0]["checklist"]["story"])
        self.assertIn("저장했어요", self.texts(at))

    def test_partial_save_draft_failed_is_not_reported_as_saved(self):
        fake.REPLIES[:] = ["고소장\n범죄 사실\n9월 2일 45만 원 송금"]
        at = self.open(step="draft")
        at.button(key=f"ws_make_{CID}").click().run()
        at.session_state[f"ws_step_{CID}"] = "next"
        at.run()
        at.checkbox(key=f"prog_{CID}_draft").check().run()
        fake.FAIL_NEXT.append(("case_drafts", "upsert"))
        self.save_button(at).click().run()
        error = " ".join(e.value for e in at.error)
        self.assertIn("아직 저장 못 한 것: 초안", error)
        self.assertIn("저장한 것: 정리 내용", error)
        self.assertNotIn("저장했어요 ·", self.texts(at))
        self.assertEqual(len(self.workspace_rows()), 1)
        self.assertEqual(fake.TABLES["case_drafts"], [])
        self.save_button(at).click().run()
        self.assertEqual(len(fake.TABLES["case_drafts"]), 1)
        self.assertEqual(self.workspace_rows()[0]["version"], 1)      # 정리 내용은 다시 저장하지 않음
        self.assertIn("저장했어요", self.texts(at))

    def test_saving_requires_admin_before_policy_v3_even_with_flag_on(self):
        fake.CASES[0]["user_id"] = fake.USER_ID
        os.environ["ASSIST_V2_ENABLED"] = "true"          # 일반 이용자에게 작업 공간을 보여도
        at = self.open(user_id=fake.USER_ID, step="check")
        self.assertFalse(any(b.key == f"ws_save_{CID}" for b in at.button))   # v3 시행 전에는 저장 버튼 없음
        self.assertTrue(any("저장되지 않아요" in c.value for c in at.caption))

    def test_regular_user_gets_no_draft_or_workspace_saving_before_v3(self):
        fake.CASES[0]["user_id"] = fake.USER_ID
        fake.REPLIES[:] = ["고소장\n범죄 사실\n9월 2일"]
        at = self.open(user_id=fake.USER_ID)                       # 일반 이용자: 기존 사건 화면
        at.button(key=f"list_draft_btn_{CID}").click().run()
        keys = {b.key for b in at.button}
        self.assertNotIn(f"list_draft_editor_{CID}_save", keys)    # 초안 저장 버튼 없음
        self.assertNotIn(f"ws_save_{CID}", keys)                   # 작업 저장 버튼 없음
        self.assertEqual(fake.TABLES["case_drafts"], [])
        self.assertEqual(fake.TABLES["case_workspace"], [])

    def test_preview_note_when_saving_is_off(self):
        os.environ["CASE_WORKSPACE_SAVE_ENABLED"] = ""
        at = self.open(step="check")
        self.assertFalse(any(b.key == f"ws_save_{CID}" for b in at.button))
        self.assertTrue(any("저장되지 않아요" in c.value for c in at.caption))


if __name__ == "__main__":
    unittest.main()
