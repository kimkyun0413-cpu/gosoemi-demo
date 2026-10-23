"""이용 후기 검사: 개인정보 거르기, 빈 화면(가짜 후기 없음), 내려받은 뒤 선택 안내, 쓰기·고치기·지우기, 운영자 게시.

가짜 Supabase로 실제 app.py를 실행한다. DB 권한(RLS·운영자 함수)은 supabase/tests/local_service_reviews_test.py에서 검사한다.
실행: python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_services as fake  # noqa: E402
import service_reviews as sr  # noqa: E402

CID = "66666666-6666-4666-8666-666666666666"
ENV = {
    "SUPABASE_URL": "https://fake.invalid", "SUPABASE_PUBLISHABLE_KEY": "fake", "GEMINI_API_KEY": "fake",
    "ADMIN_USER_IDS": fake.ADMIN_ID, "PRIVACY_POLICY_ENABLED": "true", "PRIVACY_OPERATOR_NAME": "운영자",
    "PRIVACY_CONTACT_EMAIL": "contact@example.com", "PRIVACY_POLICY_EFFECTIVE_DATE": "2026-09-27",
    "CASE_WORKSPACE_SAVE_ENABLED": "true", "DRAFT_SAVE_ENABLED": "true", "REVIEWS_ENABLED": "true",
}


class RulesTest(unittest.TestCase):
    def test_flags_contacts_and_case_details(self):
        self.assertEqual(sr.review_flags("좋았어요")["block"], [])
        flags = sr.review_flags("010-1234-5678로 연락, a@b.com, 123-456-7890123, https://x.kr 9월 2일 45만 원")
        self.assertEqual(flags["block"], ["전화번호처럼 보이는 숫자", "이메일 주소", "계좌·번호처럼 보이는 숫자", "인터넷 주소"])
        self.assertEqual(flags["warn"], ["날짜", "금액"])

    def test_problems_block_public_contacts_but_not_operator_only(self):
        self.assertIn("별점을 골라 주세요.", sr.review_problems(sr.clean_review(None, "", "", "public")))
        self.assertIn("어떻게 보낼지 골라 주세요.", sr.review_problems(sr.clean_review(5, "", "", "")))
        public = sr.clean_review(4, "010 1234 5678로 연락 주세요", "", "public")
        self.assertTrue(any("전화번호처럼 보이는 숫자" in p for p in sr.review_problems(public)))
        self.assertEqual(sr.review_problems(sr.clean_review(4, "010 1234 5678로 연락 주세요", "", "operator")), [])
        self.assertEqual(len(sr.clean_review(5, "가" * 900, "", "public")["helpful"]), sr.TEXT_MAX_CHARS)


class ReviewAppTest(unittest.TestCase):
    def setUp(self):
        fake.reset()
        fake.CASES.append({"case_id": CID, "user_id": fake.ADMIN_ID, "case_type": "중고거래 사기", "reporter_info": "홍길동(시험)",
                           "incident_description": "9월 2일 45만 원 송금", "edit_version": 1, "created_at": "2026-10-01T00:00:00+00:00"})
        patches = [mock.patch.dict(os.environ, ENV), mock.patch("supabase.create_client", fake.fake_create_client),
                   mock.patch("google.genai.Client", fake.FakeGenaiClient)]
        for patch in patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in reversed(patches)])

    def start(self, user_id=fake.ADMIN_ID, page="home", **state):
        at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
        at.session_state["supabase_session"] = fake.session_for(user_id, "x@example.com")
        at.session_state["browser_login_checked"] = True
        at.run()
        at.session_state["nav_page"] = page
        for key, value in state.items():
            at.session_state[key] = value
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        return at

    def texts(self, at):
        return " ".join(e.value for e in list(at.markdown) + list(at.caption) + list(at.info) + list(at.success))

    def write(self, at, rating, helpful, visibility):
        at.session_state["review_rating"] = rating - 1
        at.text_area(key="review_helpful").input(helpful).run()
        at.radio(key="review_visibility").set_value(visibility).run()
        at.button(key="review_send").click().run()

    def test_home_shows_empty_state_not_fake_reviews_and_nothing_when_off(self):
        at = self.start()
        self.assertIn("아직 공개된 후기가 없어요", self.texts(at))
        self.assertTrue(any(b.key == "home_reviews_more" for b in at.button))
        at.button(key="home_reviews_more").click().run()
        self.assertEqual(at.session_state["nav_page"], "reviews")
        self.assertIn("아직 공개된 후기가 없어요", self.texts(at))
        os.environ["REVIEWS_ENABLED"] = ""
        off = self.start()
        self.assertNotIn("이용 후기", self.texts(off))

    def test_prompt_only_after_download_and_is_optional(self):
        at = self.start(page="cases", open_case_id=CID, **{f"ws_step_{CID}": "download"})
        fake.TABLES["case_drafts"].append({"user_id": fake.ADMIN_ID, "case_id": CID, "draft_text": "고소장\n범죄 사실\n송금",
                                           "updated_at": "2026-10-20T00:00:00+00:00"})
        at.run()
        self.assertFalse(any(b.key == f"review_prompt_write_{CID}" for b in at.button))   # 내려받기 전에는 묻지 않는다
        at.session_state[f"ws_{CID}"]["downloaded"] = True
        at.run()
        self.assertIn("남기지 않아도 모든 기능을 그대로 쓸 수 있어요", self.texts(at))
        at.button(key=f"review_prompt_later_{CID}").click().run()
        self.assertFalse(any(b.key == f"review_prompt_write_{CID}" for b in at.button))

    def test_write_public_review_blocks_contacts_then_operator_publishes(self):
        at = self.start(page="review_write")
        self.write(at, 5, "정리하기 쉬웠어요. 010-1234-5678", "public")
        self.assertTrue(any("전화번호처럼 보이는 숫자" in e.value for e in at.error))
        self.assertEqual(fake.TABLES["service_reviews"], [])                      # 막히면 저장하지 않는다
        at.text_area(key="review_helpful").input("정리하기 쉬웠어요.").run()
        at.button(key="review_send").click().run()
        self.assertEqual(at.session_state["nav_page"], "account")
        self.assertIn("운영자가 확인하면 공개돼요", self.texts(at))
        row = fake.TABLES["service_reviews"][0]
        self.assertEqual((row["status"], row["rating"], row["visibility"]), ("pending", 5, "public"))
        self.assertIn("확인 중", self.texts(at))
        home = self.start()
        self.assertIn("아직 공개된 후기가 없어요", self.texts(home))               # 확인 전에는 공개되지 않는다
        admin = self.start(page="review_admin")
        self.assertIn("정리하기 쉬웠어요", self.texts(admin))
        admin.button(key=f"admin_pub_{row['review_id']}").click().run()
        self.assertEqual(fake.TABLES["service_reviews"][0]["status"], "published")
        home = self.start()
        self.assertIn("정리하기 쉬웠어요", self.texts(home))
        self.assertNotIn(fake.ADMIN_ID, self.texts(home))                         # 작성자 정보는 보이지 않는다

    def test_editing_published_review_goes_back_to_pending_and_delete_needs_confirm(self):
        fake.TABLES["service_reviews"].append({"review_id": "r1", "user_id": fake.ADMIN_ID, "rating": 4, "helpful": "좋았어요",
                                               "improve": "", "visibility": "public", "status": "published",
                                               "published_at": "2026-10-20T00:00:00+00:00"})
        at = self.start(page="account")
        self.assertIn("게시됨", self.texts(at))
        at.button(key="my_review_edit_r1").click().run()
        self.assertEqual(at.session_state["nav_page"], "review_write")
        self.assertEqual(at.text_area(key="review_helpful").value, "좋았어요")
        self.assertIn("운영자가 다시 확인한 뒤 게시돼요", self.texts(at))
        at.text_area(key="review_helpful").input("많이 좋았어요").run()
        at.button(key="review_send").click().run()
        row = fake.TABLES["service_reviews"][0]
        self.assertEqual((row["helpful"], row["status"], row["published_at"]), ("많이 좋았어요", "pending", None))
        at.button(key="my_review_del_r1").click().run()
        self.assertEqual(len(fake.TABLES["service_reviews"]), 1)                  # 한 번 더 묻는다
        at.button(key="my_review_del_ok_r1").click().run()
        self.assertEqual(fake.TABLES["service_reviews"], [])

    def test_operator_only_review_is_never_public_and_reject_reason_is_shown(self):
        at = self.start(page="review_write")
        self.write(at, 3, "증거 칸이 어려웠어요. 연락 010-1111-2222", "operator")
        self.assertEqual(fake.TABLES["service_reviews"][0]["status"], "private")  # 운영자에게만은 번호가 있어도 보낼 수 있다
        admin = self.start(page="review_admin", review_admin_tab="private")
        rid = fake.TABLES["service_reviews"][0]["review_id"]
        self.assertFalse(any(b.key == f"admin_pub_{rid}" for b in admin.button))  # 게시 버튼 없음
        fake.TABLES["service_reviews"].append({"review_id": "r9", "user_id": fake.ADMIN_ID, "rating": 2, "helpful": "9월 2일 일",
                                               "improve": "", "visibility": "public", "status": "pending"})
        admin = self.start(page="review_admin")
        admin.selectbox(key="admin_reason_r9").select("구체적인 사건 내용이 들어 있음").run()
        admin.button(key="admin_rej_r9").click().run()
        mine = self.start(page="account")
        self.assertIn("이유: 구체적인 사건 내용이 들어 있음", self.texts(mine))

    def test_regular_user_cannot_write_before_policy_v3(self):
        at = self.start(user_id=fake.USER_ID, page="review_write")
        self.assertIn("후기 기능은 준비 중이에요.", self.texts(at))
        self.assertFalse(any(b.key == "review_send" for b in at.button))
        account = self.start(user_id=fake.USER_ID, page="account")
        self.assertNotIn("내 후기", self.texts(account))
        admin = self.start(user_id=fake.USER_ID, page="review_admin")
        self.assertIn("운영자만 볼 수 있어요.", self.texts(admin))


if __name__ == "__main__":
    unittest.main()
