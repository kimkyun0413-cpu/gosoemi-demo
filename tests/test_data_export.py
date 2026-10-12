"""'내 자료 모두 내려받기' 검사(가상 자료).

- ZIP 내용: 사건별 폴더, 사건 내용·저장된 초안·사건 정리·증거파일, 계정_동의기록, 읽어보세요, 전체자료.json
- 보안 정보 제외, 다른 이용자 자료가 섞이지 않음, 누락 표시(다 담았다고 하지 않음), 큰 자료 나눠 받기
- 실제 app.py(가짜 Supabase)로 '내 계정' 버튼 → 자료 모으기 → 내려받기 버튼, 실패한 파일 표시, 18세 미만 안내 화면 연결
실행: python3 -m unittest discover -s tests
"""

import datetime
import io
import json
import os
import sys
import unittest
import zipfile
from unittest import mock

from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_export as dx  # noqa: E402
import fake_services as fake  # noqa: E402

ME, OTHER = "11111111-aaaa-4aaa-8aaa-111111111111", "22222222-bbbb-4bbb-8bbb-222222222222"
C1, C2, CX = "c1000000-0000-4000-8000-000000000001", "c2000000-0000-4000-8000-000000000002", "cx000000-0000-4000-8000-00000000000x"
NOW = datetime.datetime(2026, 10, 12, 21, 30)


def cases():
    return [
        {"case_id": C1, "user_id": ME, "case_type": "중고거래 사기", "reporter_info": "홍길동(가상)",
         "incident_description": "9월 2일 45만 원 송금(가상)", "created_at": "2026-10-01T00:00:00+00:00", "access_token": "SECRET-TOKEN"},
        {"case_id": C2, "user_id": ME, "case_type": "명예훼손/모욕", "incident_description": "댓글(가상)",
         "created_at": "2026-10-05T00:00:00+00:00"},
        {"case_id": CX, "user_id": OTHER, "case_type": "다른 사람 사건", "incident_description": "섞이면 안 됨",
         "created_at": "2026-10-03T00:00:00+00:00"},
    ]


def zip_names(data: bytes) -> list:
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


def zip_read(data: bytes, suffix: str) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    name = [n for n in z.namelist() if n.endswith(suffix)][0]
    return z.read(name).decode("utf-8")


class BuildTest(unittest.TestCase):
    def build(self, files=None, missing=(), limit=dx.EXPORT_PART_MAX_BYTES):
        drafts = {C1: {"case_id": C1, "user_id": ME, "draft_text": "고소장(가상 초안)", "updated_at": "2026-10-06"},
                  CX: {"case_id": CX, "user_id": OTHER, "draft_text": "다른 사람 초안"}}
        workspaces = {C1: {"case_id": C1, "user_id": ME, "timeline": [{"when": "9월 2일", "what": "송금", "evidence": "이체 내역"}],
                           "evidence_notes": [{"name": "이체 내역", "shows": "45만 원 송금 기록"}],
                           "schedules": [{"kind": "submit", "title": "경찰서 제출", "date": "2026-10-20", "time": None, "alarm": 1440}],
                           "checklist": {"story": True, "submitted_to": "○○경찰서"}, "updated_at": "2026-10-07"}}
        consents = [{"user_id": ME, "terms_version": "2026-09-15", "privacy_version": "2026-09-27", "created_at": "2026-10-01"}]
        files = files if files is not None else [
            {"case_id": C1, "path": f"{ME}/{C1}/a.png", "name": "이체내역.png", "data": b"PNG-1"},
            {"case_id": C1, "path": f"{ME}/{C1}/b.png", "name": "이체내역.png", "data": b"PNG-2"},       # 같은 이름
            {"case_id": CX, "path": f"{OTHER}/{CX}/x.png", "name": "남의파일.png", "data": b"NO"},       # 다른 사람
        ]
        return dx.build_export(ME, {"email": "me@example.invalid", "method": "이메일", "access_token": "SECRET"}, cases(), drafts,
                               workspaces, consents, files, list(missing), NOW, limit)

    def test_zip_has_case_folders_and_readable_files(self):
        result = self.build()
        self.assertEqual(len(result["parts"]), 1)
        names = zip_names(result["parts"][0])
        root = "고소미_내자료_20261012-2130"
        self.assertIn(f"{root}/읽어보세요.txt", names)
        self.assertIn(f"{root}/계정_동의기록.txt", names)
        self.assertIn(f"{root}/전체자료.json", names)
        self.assertIn(f"{root}/사건01_중고거래 사기_2026-10-01/사건내용.txt", names)
        self.assertIn(f"{root}/사건01_중고거래 사기_2026-10-01/저장된초안.txt", names)
        self.assertIn(f"{root}/사건01_중고거래 사기_2026-10-01/사건정리_경위_증거_일정.txt", names)
        self.assertIn(f"{root}/사건01_중고거래 사기_2026-10-01/증거파일/이체내역.png", names)
        self.assertIn(f"{root}/사건01_중고거래 사기_2026-10-01/증거파일/2_이체내역.png", names)   # 같은 이름은 번호
        self.assertTrue(any(n.startswith(f"{root}/사건02_명예훼손 모욕_2026-10-05/") for n in names))  # '/'는 폴더로 쓰지 않음
        ws = zip_read(result["parts"][0], "사건정리_경위_증거_일정.txt")
        self.assertIn("9월 2일 — 송금 (관련 자료: 이체 내역)", ws)
        self.assertIn("이체 내역: 45만 원 송금 기록", ws)
        self.assertIn("2026-10-20 (하루 종일) · 제출 · 경찰서 제출 · 하루 전", ws)
        self.assertIn("제출한 곳: ○○경찰서", ws)
        readme = zip_read(result["parts"][0], "읽어보세요.txt")
        self.assertIn("AI 상담(글·음성 대화)은 서버에 저장하지 않아", readme)
        self.assertIn("받지 못한 자료] 없음", readme)
        self.assertTrue(result["complete"])

    def test_no_secrets_and_no_other_users_data(self):
        result = self.build()
        blob = b"".join(zipfile.ZipFile(io.BytesIO(p)).read(n) for p in result["parts"] for n in zip_names(p))
        for bad in (b"SECRET", "다른 사람 사건".encode(), "다른 사람 초안".encode(), "섞이면 안 됨".encode(), "남의파일".encode(), b"NO", OTHER.encode()):
            self.assertNotIn(bad, blob)
        self.assertEqual(result["summary"]["cases"], 2)
        self.assertEqual(result["summary"]["files"], 2)
        self.assertEqual(result["skipped_foreign"], 2)          # 다른 사람 사건 1건 + 파일 1개
        data = json.loads(zip_read(result["parts"][0], "전체자료.json"))
        self.assertEqual({c["case_id"] for c in data["cases"]}, {C1, C2})
        self.assertNotIn("access_token", data["cases"][0])

    def test_missing_items_are_listed_and_not_called_complete(self):
        result = self.build(missing=["사건 '중고거래 사기'의 증거파일 '사진.png'을(를) 받지 못했습니다"])
        self.assertFalse(result["complete"])
        readme = zip_read(result["parts"][0], "읽어보세요.txt")
        self.assertIn("일부 자료를 담지 못했습니다", readme)
        self.assertIn("사진.png", readme)
        self.assertNotIn("모두 담았습니다", readme)

    def test_large_files_are_split_and_text_stays_in_part_one(self):
        big = [{"case_id": C1, "path": f"{ME}/{C1}/f{i}.bin", "name": f"파일{i}.bin", "data": bytes([i]) * 700_000} for i in range(5)]
        result = self.build(files=big, limit=1_500_000)
        self.assertGreater(len(result["parts"]), 1)
        for part in result["parts"]:
            total = sum(i.file_size for i in zipfile.ZipFile(io.BytesIO(part)).infolist())
            self.assertLessEqual(total, 1_500_000 + 1024 * 1024)     # 1번 묶음은 글 자료 몫 여유 포함
        self.assertTrue(any(n.endswith("사건내용.txt") for n in zip_names(result["parts"][0])))
        self.assertFalse(any(n.endswith("사건내용.txt") for n in zip_names(result["parts"][1])))
        all_files = [n for p in result["parts"] for n in zip_names(p) if "/증거파일/" in n]
        self.assertEqual(len(all_files), 5)                        # 나눠도 빠지는 파일 없음
        readme2 = zip_read(result["parts"][1], "읽어보세요.txt")
        self.assertIn(f"묶음: 2 / {len(result['parts'])}", readme2)


class AppExportTest(unittest.TestCase):
    ENV = {"SUPABASE_URL": "https://fake.invalid", "SUPABASE_PUBLISHABLE_KEY": "fake", "GEMINI_API_KEY": "fake",
           "PRIVACY_POLICY_ENABLED": "true", "PRIVACY_OPERATOR_NAME": "운영자", "PRIVACY_CONTACT_EMAIL": "c@example.com",
           "PRIVACY_POLICY_EFFECTIVE_DATE": "2026-09-27", "EVIDENCE_UPLOAD_ENABLED": "true", "AI_FEATURES_PAUSED": "true"}

    def setUp(self):
        fake.reset()
        fake.CASES.extend([
            {"case_id": C1, "user_id": fake.USER_ID, "case_type": "중고거래 사기", "incident_description": "가상 경위",
             "created_at": "2026-10-01T00:00:00+00:00"},
            {"case_id": CX, "user_id": OTHER, "case_type": "다른 사람 사건", "incident_description": "섞이면 안 됨",
             "created_at": "2026-10-02T00:00:00+00:00"},
        ])
        fake.TABLES["case_drafts"].append({"case_id": C1, "user_id": fake.USER_ID, "draft_text": "가상 초안", "updated_at": "2026-10-06"})
        fake.FILES[f"{fake.USER_ID}/{C1}/1_photo.png"] = b"PNG-OK"
        fake.FILES[f"{fake.USER_ID}/{C1}/2_broken.png"] = b"PNG-BAD"
        fake.FILES[f"{OTHER}/{CX}/9_other.png"] = b"OTHER"
        patches = [mock.patch.dict(os.environ, self.ENV), mock.patch("supabase.create_client", fake.fake_create_client),
                   mock.patch("google.genai.Client", fake.FakeGenaiClient)]
        for patch in patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in reversed(patches)])

    def start(self):
        at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
        at.session_state["supabase_session"] = fake.session_for(fake.USER_ID, "me@example.invalid")
        at.session_state["browser_login_checked"] = True
        at.session_state["nav_page"] = "account"
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        return at

    def download_bytes(self, at):
        # AppTest는 내려받기 버튼의 실제 파일을 주지 않으므로, 같은 세션에 준비해 둔 결과를 읽는다.
        return at.session_state["_my_export_account"]["parts"]

    def test_account_export_with_one_failed_file(self):
        fake.DOWNLOAD_FAIL.add(f"{fake.USER_ID}/{C1}/2_broken.png")
        at = self.start()
        self.assertTrue(any("AI 상담(글·음성 대화)은 서버에 저장하지 않아" in c.value for c in at.caption))
        at.button(key="export_prepare_account").click().run()
        self.assertEqual([e.value for e in at.exception], [])
        self.assertTrue(any("일부 자료를 담지 못했어요" in w.value and "broken" in w.value for w in at.warning))
        self.assertFalse(at.success)                                       # 다 담았다고 하지 않는다
        parts = self.download_bytes(at)
        names = zip_names(parts[0])
        self.assertTrue(any("/증거파일/" in n and "photo" in n for n in names))
        self.assertFalse(any("broken" in n for n in names))
        blob = b"".join(zipfile.ZipFile(io.BytesIO(p)).read(n) for p in parts for n in zip_names(p))
        self.assertNotIn("섞이면 안 됨".encode(), blob)
        self.assertNotIn(b"OTHER", blob)
        self.assertIn("가상 초안", zip_read(parts[0], "저장된초안.txt"))

    def test_account_export_complete(self):
        at = self.start()
        at.button(key="export_prepare_account").click().run()
        self.assertTrue(any("내려받을 준비가 됐어요" in s.value for s in at.success))
        self.assertTrue(any(b.key == "export_clear_account" for b in at.button))
        at.button(key="export_clear_account").click().run()
        self.assertNotIn("_my_export_account", at.session_state)

    def test_total_size_cap_lists_skipped_files(self):
        fake.FILES[f"{fake.USER_ID}/{C1}/3_big.bin"] = b"x" * 3000
        with mock.patch("data_export.EXPORT_TOTAL_MAX_BYTES", 2500):
            at = self.start()
            at.button(key="export_prepare_account").click().run()
        result = at.session_state["_my_export_account"]
        self.assertFalse(result["complete"])
        self.assertTrue(any("big" in m and "용량" in m for m in result["missing"]))
        names = [n for p in result["parts"] for n in zip_names(p)]
        self.assertFalse(any("big" in n for n in names))
        self.assertTrue(any("photo" in n for n in names))

    def prepare(self):
        at = self.start()
        at.button(key="export_prepare_account").click().run()
        self.assertEqual([e.value for e in at.exception], [])
        return at, at.session_state["_my_export_account"]

    def test_consent_records_without_time_column(self):
        # 운영 오류 재현: consent_records에 created_at 열이 없으면 예전 코드는 '동의 기록을 불러오지 못했습니다'였다.
        at, result = self.prepare()
        self.assertNotIn("동의 기록을(를) 불러오지 못했습니다", result["missing"])
        self.assertTrue(result["complete"], result["missing"])
        text = zip_read(result["parts"][0], "계정_동의기록.txt")
        self.assertIn("2026-09-27", text)
        self.assertIn("동의한 때는 기록에 없음", text)

    def test_consent_records_fallback_when_star_is_denied(self):
        fake.CONSENT_STAR_DENIED[0] = True          # 열 단위 권한이면 동의 확인과 같은 세 열만 다시 받는다
        _, result = self.prepare()
        self.assertTrue(result["complete"], result["missing"])
        self.assertIn("2026-09-27", zip_read(result["parts"][0], "계정_동의기록.txt"))

    def test_consent_records_failure_is_reported(self):
        # 내려받기 때의 두 조회(*, 세 열)만 실패시키고, 화면마다 하는 동의 확인(terms_version)은 그대로 둔다.
        fake.CONSENT_STAR_DENIED[0] = True
        with mock.patch.object(fake, "CONSENT_COLUMNS", ("terms_version",)):
            at, result = self.prepare()
        self.assertIn("동의 기록을(를) 불러오지 못했습니다", result["missing"])
        self.assertFalse(at.success)

    def test_missing_column_in_drafts_is_not_hidden_as_zero(self):
        # 테이블이 없을 때만 0건으로 넘기고, 열 오류는 '받지 못한 자료'로 알린다.
        app_globals = {}
        exec(open(os.path.join(ROOT, "app.py")).read().split("def _table_missing")[1].split("def _error_code")[0]
             .replace("(error: Exception) -> bool:", "def _table_missing(error):", 1), app_globals)
        missing = app_globals["_table_missing"]
        self.assertTrue(missing(fake.FakeAPIError("Could not find the table 'public.case_drafts'", "PGRST205")))
        self.assertTrue(missing(fake.FakeAPIError('relation "public.case_drafts" does not exist', "42P01")))
        self.assertFalse(missing(fake.FakeAPIError("column case_drafts.draft_text does not exist", "42703")))

    def test_no_evidence_files_is_shown_as_checked_zero(self):
        fake.FILES.clear()
        at, result = self.prepare()
        self.assertTrue(result["complete"])
        self.assertTrue(any("증거파일 0개(사건 1건 모두 목록을 확인했고, 첨부한 파일이 없어요)" in s.value for s in at.success))

    def test_evidence_list_failure_is_not_shown_as_zero(self):
        fake.FILES.clear()
        fake.LIST_FAIL.add(f"{fake.USER_ID}/{C1}")
        at, result = self.prepare()
        self.assertFalse(result["complete"])
        self.assertTrue(any("증거파일 목록을 불러오지 못했습니다" in m for m in result["missing"]))
        warning = " ".join(w.value for w in at.warning)
        self.assertIn("증거파일: 확인하지 못함", warning)
        self.assertNotIn("증거파일 0개", warning)
        self.assertIn("증거파일: 확인하지 못함", zip_read(result["parts"][0], "읽어보세요.txt"))

    def test_under_age_screen_offers_download_and_withdrawal(self):
        fake.CONSENT_MISSING = True
        patch = mock.patch.dict(os.environ, {"MIN_USER_AGE": "18", "TERMS_EFFECTIVE_DATE_V2": "2026-11-01"})
        patch.start()
        self.addCleanup(patch.stop)
        at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
        at.session_state["supabase_session"] = fake.session_for(fake.USER_ID, "me@example.invalid")
        at.session_state["browser_login_checked"] = True
        at.run()                                       # 로그인 직후 한 번 그린 뒤(계정별 상태 초기화 후) '18세 미만'을 고른 상태
        at.session_state["under_age_declared"] = True
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        keys = {b.key for b in at.button}
        self.assertIn("export_prepare_under_age", keys)
        self.assertIn("delete_account_main", keys)
        at.button(key="export_prepare_under_age").click().run()
        self.assertTrue(at.session_state["_my_export_under_age"]["parts"])
        self.assertEqual([w for w in fake.WRITES if w[1] in ("delete", "remove")], [])   # 아무것도 지우지 않음


if __name__ == "__main__":
    unittest.main()
