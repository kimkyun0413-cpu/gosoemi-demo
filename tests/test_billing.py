"""billing.py 검사. 실행: python3 -m unittest discover -s tests"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import billing as b  # noqa: E402

PLANS = json.dumps([
    {"plan_id": "basic", "name": "기본 1회권", "kind": "one_time", "amount": 1000, "period_days": 30},
    {"plan_id": "monthly", "name": "월간", "kind": "subscription", "amount": 3000, "period_days": 30},
])


class BillingConfigTest(unittest.TestCase):
    def test_mode_defaults_to_off(self):
        self.assertEqual(b.billing_mode(None), "off")
        self.assertEqual(b.billing_mode("LIVE"), "live")
        self.assertEqual(b.billing_mode("free-money"), "off")

    def test_parse_valid_plans(self):
        plans = b.parse_plans(PLANS)
        self.assertEqual([p.plan_id for p in plans], ["basic", "monthly"])

    def test_any_invalid_plan_hides_all(self):
        bad = json.loads(PLANS) + [{"plan_id": "x", "name": "x", "kind": "weekly", "amount": 1, "period_days": 7}]
        self.assertEqual(b.parse_plans(json.dumps(bad)), [])
        self.assertEqual(b.parse_plans('[{"plan_id": "a", "name": "a", "kind": "one_time", "amount": 0, "period_days": 30}]'), [])
        self.assertEqual(b.parse_plans("not json"), [])

    def test_prices_hidden_unless_test_mode(self):
        plan = b.parse_plans(PLANS)[0]
        self.assertNotIn("1,000", b.plan_summary(plan, "off"))
        self.assertNotIn("1,000", b.plan_summary(plan, "live"))
        self.assertIn("실제 결제 아님", b.plan_summary(plan, "test"))

    def test_sales_never_open_without_provider_integration(self):
        for mode in b.BILLING_MODES:
            self.assertFalse(b.sales_open(mode, b.parse_plans(PLANS)))

    def test_idempotency_keys_are_unique(self):
        self.assertNotEqual(b.new_idempotency_key(), b.new_idempotency_key())


if __name__ == "__main__":
    unittest.main()
