"""app.get_user_data_client()가 기대하는 supabase-py 동작 확인.

토큰을 Authorization 헤더로 넘겨 만든 클라이언트는 Supabase Auth에 요청하지 않고,
DB(postgrest)·파일 저장소(storage) 요청에 그 토큰과 apikey를 싣는다.
supabase 패키지를 올리면 이 테스트를 먼저 돌린다. 실행: python3 -m unittest discover -s tests
"""

import unittest
from unittest import mock

import httpx
from supabase import ClientOptions, create_client


class UserDataClientTest(unittest.TestCase):
    def test_header_client_makes_no_network_calls_and_carries_token(self):
        sent = []

        def block(self, request, *args, **kwargs):
            sent.append(str(request.url))
            raise RuntimeError("network blocked in test")

        with mock.patch.object(httpx.Client, "send", block):
            client = create_client(
                "https://example.supabase.co",
                "sb_publishable_test",
                options=ClientOptions(headers={"Authorization": "Bearer USER_TOKEN"}),
            )
            storage_headers = dict(client.storage._client.headers)
            postgrest_headers = dict(client.postgrest.session.headers)

        self.assertEqual(sent, [])
        self.assertEqual(storage_headers.get("authorization"), "Bearer USER_TOKEN")
        self.assertEqual(postgrest_headers.get("authorization"), "Bearer USER_TOKEN")
        self.assertEqual(storage_headers.get("apikey"), "sb_publishable_test")


if __name__ == "__main__":
    unittest.main()
