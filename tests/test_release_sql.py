"""운영 반영 SQL 묶음이 검사한 마이그레이션·되돌리기와 글자 하나까지 같은지 확인한다. 실행: python3 -m unittest discover -s tests"""

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
read = lambda path: open(os.path.join(ROOT, path), encoding="utf-8").read()  # noqa: E731


class ReleaseSqlTest(unittest.TestCase):
    def test_apply_is_both_migrations_in_one_transaction(self):
        apply = read("supabase/release/2026-10-24_design_a_apply.sql")
        body = apply.split("begin;\n\n", 1)[1]
        self.assertEqual(body, read("supabase/migrations/20261023000000_case_draft_archive.sql") + "\n"
                         + read("supabase/migrations/20261023000001_service_reviews.sql") + "\ncommit;\n")
        self.assertEqual(apply.count("begin;"), 1)

    def test_rollback_is_both_rollbacks_newest_first(self):
        body = read("supabase/release/2026-10-24_design_a_rollback.sql").split("begin;\n\n", 1)[1]
        self.assertEqual(body, read("supabase/rollbacks/20261023000001_service_reviews_rollback.sql") + "\n"
                         + read("supabase/rollbacks/20261023000000_case_draft_archive_rollback.sql") + "\ncommit;\n")

    def test_admin_sql_needs_a_real_id(self):
        sql = read("supabase/release/2026-10-24_review_admin.sql")
        self.assertIn("'<운영자 user id>'::uuid", sql)   # 바꾸지 않으면 형식 오류로 멈춘다
        self.assertNotIn("delete", sql.lower())


if __name__ == "__main__":
    unittest.main()
