"""policy_text.py 검사. 실행: python3 -m unittest discover -s tests"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import policy_text as p  # noqa: E402


class PrivacyPolicyV3Test(unittest.TestCase):
    def setUp(self):
        self.text = p.build_privacy_policy_v3("홍운영", "contact@example.com", "2026-10-13")

    def test_lists_items_added_by_implementation(self):
        for phrase in [
            "고소장 초안 저장 시",
            "동의 및 연령 확인 기록",
            "만 14세 이상 확인 여부와 확인 일시",
            "로그인·인증 기록",
            "AI 상담 이용 시",
            "localStorage",
            "sessionStorage",
        ]:
            self.assertIn(phrase, self.text)

    def test_covers_required_sections_of_article_30(self):
        # 개인정보 보호법 제30조 제1항: 처리 목적, 처리·보유 기간, 파기, 위탁, 권리·행사방법, 보호책임자, 자동수집장치
        for heading in [
            "개인정보의 처리 목적",
            "처리 및 보유 기간",
            "파기 절차 및 방법",
            "처리의 위탁",
            "권리와 행사 방법",
            "개인정보 보호책임자",
            "자동으로 수집하는 장치",
        ]:
            self.assertIn(heading, self.text)

    def test_overseas_transfer_has_article_28_8_items(self):
        # 제28조의8 제2항: 항목, 국가·시기·방법, 받는 자와 연락처, 목적·보유기간, 거부 방법·효과
        for phrase in [
            "이전 항목", "이전 국가", "정보통신망을 통해 수시로", "이용 목적", "보유·이용 기간",
            "privacy@supabase.com", "privacy@snowflake.com", "Google LLC", "거부하시면",
        ]:
            self.assertIn(phrase, self.text)

    def test_states_backup_and_log_retention_found_in_dashboard(self):
        self.assertIn("프로젝트 백업 기능이 없어", self.text)
        # 운영자가 조회할 수 있는 기간과 Supabase 내부 보관 기간을 구분한다
        self.assertIn("조회할 수 있는 기간은 현재 요금제 기준 1일", self.text)
        self.assertIn("Supabase 공개 자료에서 확인되지 않아", self.text)

    def test_states_legal_basis_storage_location_and_subprocessor_link(self):
        self.assertIn("제28조의8 제1항 제3호", self.text)
        self.assertIn("대한민국 서울", self.text)
        self.assertIn("subprocessor-list", self.text)

    def test_change_comparison_covers_each_changed_section(self):
        self.assertGreaterEqual(len(p.CHANGES_V2_TO_V3), 6)
        for section, before, after in p.CHANGES_V2_TO_V3:
            self.assertTrue(section and before and after)

    def test_does_not_guess_google_countries(self):
        self.assertIn("구체적인 국가를 공개하지 않습니다", self.text)

    def test_operator_and_dates_are_filled(self):
        self.assertIn("개인정보 보호책임자: 홍운영", self.text)
        self.assertIn("2026-10-13부터 적용", self.text)
        self.assertIn("2026-09-27부터 적용되었습니다", self.text)

    def test_previous_version_text_is_kept(self):
        v2 = p.build_privacy_policy_v2("홍운영", "contact@example.com", "2026-09-27")
        self.assertIn("구체적인 처리 국가와 계약 법인은 현재 확인되지 않아", v2)
        self.assertNotIn("고소장 초안 저장 시", v2)


if __name__ == "__main__":
    unittest.main()
