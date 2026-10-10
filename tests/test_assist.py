"""용어 풀이·유형별 Q&A·진행 체크리스트·핵심만 보기·판례 찾기·초안 안내 검사.

순수 함수는 바로, 화면은 가짜 Supabase·Gemini로 실제 app.py를 실행해 확인한다. 실행: python3 -m unittest discover -s tests
"""

import json
import os
import sys
import unittest
from io import BytesIO
from unittest import mock

from docx import Document
from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import case_guide as g  # noqa: E402
import chat_assistant as c  # noqa: E402
import fake_services as fake  # noqa: E402
import precedent_search as p  # noqa: E402


class LegalTermsTest(unittest.TestCase):
    def test_every_term_has_plain_text_and_example(self):
        for term, plain, example in g.LEGAL_TERM_GUIDE:
            self.assertTrue(term and plain and example, term)
            self.assertNotRegex(plain + example, r"\d+\s*일\s*(이내|안에)")  # 기한은 적지 않는다

    def test_original_terms_kept_and_screen_terms_exist(self):
        names = {t[0] for t in g.LEGAL_TERM_GUIDE}
        self.assertTrue({t for t, _ in g.LEGAL_TERMS} <= names)
        for screen, terms in g.SCREEN_TERMS.items():
            self.assertEqual(len(g.legal_terms_for(screen)), len(terms), screen)
        self.assertEqual([t[0] for t in g.search_legal_terms("송치")][:2], ["송치", "불송치"])
        self.assertTrue(g.LEGAL_TERMS_SOURCE.startswith("https://www.spo.go.kr/"))


class TypeFaqTest(unittest.TestCase):
    def test_every_type_covers_evidence_unknown_suspect_and_procedure(self):
        self.assertEqual(len(g.DAMAGE_TYPES), 14)
        for case_type in g.DAMAGE_TYPES:
            faq = g.type_faq(case_type)
            text = " ".join(q for q, _ in faq)
            self.assertIn("자료", text, case_type)
            self.assertIn("모르", text, case_type)
            self.assertIn("절차", text, case_type)
            self.assertGreaterEqual(len(faq), len(g.DAMAGE_TYPES[case_type]["qa"]))
            procedure = faq[-1][1]
            self.assertIn("모든 피해가 고소 대상인 것은 아니", procedure)
            for label, url in g.DAMAGE_TYPES[case_type]["sources"]:
                self.assertIn(url, procedure)

    def test_route_tags_cover_routes(self):
        self.assertEqual(set(g.ROUTE_TAGS), set(g.ROUTES))


class ProgressTest(unittest.TestCase):
    def test_clean_and_summary(self):
        record = g.clean_progress({"story": True, "draft": "yes", "unknown": True, "submitted_to": "가" * 100,
                                   "submitted_on": "2026/10/10", "next_note": "나" * 999})
        self.assertTrue(record["story"])
        self.assertFalse(record["draft"])
        self.assertNotIn("unknown", record)
        self.assertEqual(len(record["submitted_to"]), g.PROGRESS_PLACE_MAX_CHARS)
        self.assertEqual(record["submitted_on"], "")
        self.assertEqual(len(record["next_note"]), g.PROGRESS_NOTE_MAX_CHARS)
        self.assertEqual(g.progress_summary(record), (1, len(g.PROGRESS_ITEMS)))
        self.assertIn("조회한 것이 아니", g.PROGRESS_NOTICE)


class SummaryTest(unittest.TestCase):
    def test_rejects_numbers_not_in_answer(self):
        answer = "먼저 112에 신고하고, 9월 2일 이체 내역 45만 원을 보관하세요."
        self.assertTrue(c.summary_supported("핵심: 112 신고, 45만 원 이체 내역 보관", answer))
        self.assertFalse(c.summary_supported("핵심: 1332에 문의", answer))
        self.assertFalse(c.summary_supported("3개월 안에 고소", answer))

    def test_prompt_forbids_new_facts(self):
        prompt = c.summary_prompt("답변")
        for phrase in ["[답변]만 근거로", "보태지 마세요", "단정하지 마세요", "다음에 할 일"]:
            self.assertIn(phrase, prompt)


class PrecedentTest(unittest.TestCase):
    SAMPLE = json.dumps({"PrecSearch": {"totalCnt": "2", "prec": [
        {"판례일련번호": "1", "사건명": "사기", "사건번호": "2020도0000", "선고일자": "20200101", "법원명": "대법원"},
        {"판례일련번호": "2", "사건명": "", "사건번호": "", "선고일자": "", "법원명": ""},
    ]}}, ensure_ascii=False)

    def test_only_real_rows_with_source(self):
        rows = p.parse_precedent_list(self.SAMPLE)
        self.assertEqual(rows, [{"name": "사기", "case_no": "2020도0000", "date": "2020.01.01", "court": "대법원",
                                 "source": p.OPEN_API_SOURCE}])

    def test_search_requires_key_and_blocks_personal_numbers(self):
        with self.assertRaises(RuntimeError):
            p.search_precedents("사기", "", lambda u: self.SAMPLE)
        with self.assertRaises(ValueError):
            p.search_precedents("010-1234-5678", "oc", lambda u: self.SAMPLE)
        seen = []
        rows = p.search_precedents("중고거래 사기", "oc", lambda u: seen.append(u) or self.SAMPLE)
        self.assertEqual(len(rows), 1)
        self.assertIn("target=prec", seen[0])
        self.assertTrue(seen[0].startswith("https://www.law.go.kr/DRF/lawSearch.do"))

    def test_links_and_keywords(self):
        self.assertTrue(p.OFFICIAL_PRECEDENT_SEARCH[1].startswith("https://www.law.go.kr/"))
        self.assertEqual(set(p.PRECEDENT_KEYWORDS), set(g.DAMAGE_TYPES))


class DraftNoticeTest(unittest.TestCase):
    def test_notice_added_once(self):
        self.assertIn("법률 전문가의 검토", g.DRAFT_DISCLAIMER)
        self.assertIn("사실관계", g.DRAFT_DISCLAIMER)
        once = g.with_draft_disclaimer("본문")
        self.assertEqual(g.with_draft_disclaimer(once), once)
        old = "본문\n\n" + g.OLD_DRAFT_DISCLAIMERS[0]
        self.assertEqual(g.with_draft_disclaimer(old), old)

    def test_docx_has_notice_even_if_user_deleted_it(self):
        doc = Document(BytesIO(g.build_case_draft_docx("고소장\n## 고소인\n홍길동").getvalue()))
        texts = [para.text for para in doc.paragraphs]
        self.assertEqual(texts.count(g.DRAFT_DISCLAIMER), 1)


ENV = {
    "SUPABASE_URL": "https://fake.invalid", "SUPABASE_PUBLISHABLE_KEY": "fake", "GEMINI_API_KEY": "fake",
    "ADMIN_USER_IDS": fake.ADMIN_ID, "PRIVACY_POLICY_EFFECTIVE_DATE": "2026-09-27", "AI_CALLS_PER_HOUR": "30",
    "ASSIST_V2_ENABLED": "", "AI_CHAT_V2_ENABLED": "", "LAW_OPEN_API_OC": "",
}
CASE_ID = "11111111-1111-4111-8111-111111111111"
LONG_ANSWER = ("먼저 거래 내용을 시간 순서대로 정리해 보세요. " * 12) + "112에 신고할 수 있어요. 45만 원 이체 내역을 보관하세요."


class AssistAppTest(unittest.TestCase):
    def setUp(self):
        fake.reset()
        fake.CASES.append({"case_id": "11111111-1111-4111-8111-111111111111", "user_id": fake.ADMIN_ID,
                           "case_type": "중고거래 사기", "incident_description": "9월 2일 45만 원 송금",
                           "created_at": "2026-10-01T00:00:00+00:00"})
        patches = [mock.patch.dict(os.environ, ENV), mock.patch("supabase.create_client", fake.fake_create_client),
                   mock.patch("google.genai.Client", fake.FakeGenaiClient)]
        for patch in patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in reversed(patches)])

    def start(self, user_id=fake.ADMIN_ID, page="chat", **state):
        at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
        at.session_state["supabase_session"] = fake.session_for(user_id, "x@example.com")
        at.session_state["browser_login_checked"] = True
        at.session_state["nav_page"] = page
        at.run()
        if state:  # 첫 실행은 계정이 바뀐 것으로 보고 화면 상태를 비우므로, 그 뒤에 넣는다.
            for key, value in state.items():
                at.session_state[key] = value
            at.run()
        self.assertEqual([e.value for e in at.exception], [])
        return at

    def labels(self, at):
        return [e.label for e in at.expander]

    def pages(self, user_id):
        """사건 작성·도움말·내 사건(사건 열기) 화면의 펼쳐 보기 제목과 링크 버튼을 모은다."""
        labels, links = [], []
        case = self.start(user_id, "case", case_type_choice="중고거래 사기")
        labels += self.labels(case)
        help_page = self.start(user_id, "help")
        labels += self.labels(help_page)
        links += [b.proto.url for b in help_page.get("link_button")]
        cases = self.start(user_id, "cases", open_case_id=CASE_ID)
        labels += self.labels(cases)
        return labels, links

    def test_admin_sees_new_helpers_and_regular_user_does_not(self):
        labels, links = self.pages(fake.ADMIN_ID)
        self.assertIn("📖 어려운 말 풀이", labels)
        self.assertIn("🔎 판례 찾아보기 (외부 공식 사이트)", labels)
        self.assertIn("✅ 진행 체크리스트", labels)
        self.assertTrue(any("상대방 이름이나 연락처를 모르면" in l for l in labels))
        self.assertTrue(any(u.startswith("https://www.law.go.kr/") for u in links))
        fake.CASES[0]["user_id"] = fake.USER_ID
        ulabels, _ = self.pages(fake.USER_ID)
        for hidden in ("📖 어려운 말 풀이", "🔎 판례 찾아보기 (외부 공식 사이트)", "✅ 진행 체크리스트"):
            self.assertNotIn(hidden, ulabels)

    def test_admin_diagnostics_only_on_account_page(self):
        home = self.start(page="home")
        self.assertNotIn("🛠 운영자 진단 (관리자에게만 보임)", self.labels(home))
        account = self.start(page="account")
        self.assertIn("🛠 운영자 진단 (관리자에게만 보임)", self.labels(account))
        self.assertIn("궁금한 점을 글이나 말로 물어보세요.", " ".join(m.value for m in home.markdown))

    def test_checklist_is_not_saved(self):
        at = self.start(page="cases", open_case_id=CASE_ID)
        at.checkbox(key=f"prog_{CASE_ID}_story").check().run()
        self.assertIn(f"**1/{len(g.PROGRESS_ITEMS)}개 표시함**", [m.value for m in at.markdown])
        self.assertIn("✅ 진행 체크리스트", self.labels(at))
        self.assertFalse(fake.WRITES)

    def test_summary_button_flow(self):
        fake.REPLIES[:] = [LONG_ANSWER, "핵심: 거래 내용을 정리하고 112 신고를 검토하세요.\n다음에 할 일: 45만 원 이체 내역 보관"]
        at = self.start()
        at.chat_input(key="chat_input_box").set_value("중고거래 사기를 당했어요").run()
        at.button(key="chat_sum_1").click().run()
        self.assertEqual([e.value for e in at.exception], [])
        self.assertIn("112 신고를 검토", at.session_state["chat_messages"][1]["summary"])
        self.assertIn("[답변]만 근거로", fake.contents_text(fake.CALLS[-1]))

    def test_summary_with_new_number_is_hidden(self):
        fake.REPLIES[:] = [LONG_ANSWER, "핵심: 1332에 문의하세요."]
        at = self.start()
        at.chat_input(key="chat_input_box").set_value("질문").run()
        at.button(key="chat_sum_1").click().run()
        self.assertNotIn("summary", at.session_state["chat_messages"][1])
        self.assertIn(c.SUMMARY_FAILED, " ".join(w.value for w in at.warning))


if __name__ == "__main__":
    unittest.main()
