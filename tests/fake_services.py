"""app.py를 외부 서비스 없이 실행하기 위한 가짜 Supabase·Gemini. 운영 DB·AI에 연결하지 않는다.

- 가짜 Supabase: 동의 기록은 있다고 답하고, 다른 표는 빈 목록을 돌려준다. 쓰기 요청(insert/upsert/update/delete)은 기록만 한다.
- 가짜 Gemini: 받은 요청을 CALLS에 쌓고, REPLIES 순서대로 답한다(Exception이면 그 오류를 낸다).
"""

import base64
import json
from types import SimpleNamespace

ADMIN_ID = "00000000-0000-4000-8000-00000000a001"
USER_ID = "00000000-0000-4000-8000-00000000b002"

CALLS = []
REPLIES = []
WRITES = []
CASES = []  # 'cases' 표를 읽을 때 돌려줄 가짜 사건


def fake_token(user_id: str) -> str:
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return ".".join([seg({"alg": "none"}), seg({"sub": user_id, "app_metadata": {"provider": "email"}}), "sig"])


def session_for(user_id: str, email: str) -> dict:
    return {
        "access_token": fake_token(user_id), "refresh_token": "refresh-" + user_id, "user_id": user_id,
        "email": email, "expires_at": 4_102_444_800, "login_nonce": "a1b2c3d4" + user_id[-4:],
    }


class FakeQuery:
    def __init__(self, table):
        self.table_name = table
        self.op = "select"

    def __getattr__(self, name):
        def method(*args, **kwargs):
            if name in ("insert", "upsert", "update", "delete"):
                self.op = name
                WRITES.append((self.table_name, name, args))
            return self
        return method

    def execute(self):
        if self.table_name == "consent_records" and self.op == "select":
            return SimpleNamespace(data=[{"terms_version": "x"}])
        if self.table_name == "cases" and self.op == "select":
            return SimpleNamespace(data=[dict(c) for c in CASES])
        return SimpleNamespace(data=[])


class FakeAuth:
    def get_user(self, token):
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return SimpleNamespace(user=SimpleNamespace(id=claims["sub"]))

    def sign_out(self):
        return None

    def set_session(self, access, refresh):
        return SimpleNamespace(session=object())


class FakeSupabase:
    def __init__(self, *args, **kwargs):
        self.auth = FakeAuth()
        self.postgrest = SimpleNamespace(auth=lambda token: None)

    def table(self, name):
        return FakeQuery(name)

    def rpc(self, name, params=None):
        return FakeQuery("rpc:" + name)


def fake_create_client(*args, **kwargs):
    return FakeSupabase()


class _Models:
    def generate_content(self, model, contents, config=None):
        CALLS.append({"model": model, "contents": contents, "config": config})
        reply = REPLIES.pop(0) if REPLIES else "가짜 답변입니다."
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(text=reply)


class FakeGenaiClient:
    def __init__(self, *args, **kwargs):
        self.models = _Models()


def reset():
    CALLS.clear()
    REPLIES.clear()
    WRITES.clear()
    CASES.clear()


def contents_text(call) -> str:
    """요청 내용을 글로 펼친다(검사용)."""
    out = []
    for item in call["contents"]:
        if isinstance(item, dict):
            out.append(item["role"] + ":" + "".join(p.get("text", "") for p in item["parts"]))
        else:
            out.append(str(getattr(item, "text", "") or "[binary]"))
    return "\n".join(out)
