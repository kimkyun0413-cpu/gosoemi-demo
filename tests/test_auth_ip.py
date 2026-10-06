"""auth_ip.py 검사. 실행: python3 -m unittest discover -s tests"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth_ip as a  # noqa: E402


class AuthIpTest(unittest.TestCase):
    def test_public_ip_only(self):
        self.assertEqual(a.public_ip("8.8.8.8"), "8.8.8.8")
        self.assertEqual(a.public_ip(" 2001:4860:4860::8888 "), "2001:4860:4860::8888")
        for bad in ("10.0.0.1", "192.168.0.2", "127.0.0.1", "::1", "", None, "not-an-ip", "169.254.1.1"):
            self.assertIsNone(a.public_ip(bad), bad)

    def test_rightmost_public_ip_wins_so_spoofed_left_values_are_ignored(self):
        # 이용자가 "1.1.1.1"을 직접 써 넣고, 프록시가 실제 접속 IP 8.8.8.8과 내부 사설 IP를 덧붙인 경우
        self.assertEqual(a.client_ip_from("1.1.1.1, 8.8.8.8, 10.0.0.5", "172.16.0.1"), "8.8.8.8")
        self.assertEqual(a.client_ip_from("1.1.1.1, 8.8.8.8", None), "8.8.8.8")
        self.assertEqual(a.client_ip_from("", "9.9.9.9"), "9.9.9.9")
        self.assertIsNone(a.client_ip_from("garbage", "10.1.1.1"))

    def test_chain_summary_hides_values(self):
        summary = a.forwarded_chain_summary("1.1.1.1, 8.8.8.8, 10.0.0.5")
        self.assertEqual(summary, {"entries": 3, "public": 2})

    def test_settings_fall_back_to_publishable_key(self):
        self.assertEqual(a.auth_request_settings(False, "pub", "sec", "8.8.8.8"), ("pub", {}))
        self.assertEqual(a.auth_request_settings(True, "pub", "", "8.8.8.8"), ("pub", {}))
        self.assertEqual(a.auth_request_settings(True, "pub", "sec", None), ("pub", {}))
        self.assertEqual(a.auth_request_settings(True, "pub", "sec", "8.8.8.8"), ("sec", {"Sb-Forwarded-For": "8.8.8.8"}))


if __name__ == "__main__":
    unittest.main()
