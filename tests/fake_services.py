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


TABLES = {"cases": CASES, "case_workspace": [], "case_drafts": []}
FAIL_NEXT = []  # 다음 요청을 실패시킬 (표, 동작) 목록. 예: [("case_workspace", "update")]
# 가짜 consent_records에 있는 열. 운영처럼 '동의한 때' 열이 없다고 두고, 없는 열을 고르면 PostgREST처럼 42703 오류를 낸다.
CONSENT_COLUMNS = ("user_id", "terms_version", "privacy_version")
CONSENT_STAR_DENIED = [False]  # True면 select("*")를 열 권한 오류(42501)로 막는다


class FakeAPIError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.message, self.code = message, code
_CLOCK = [0]


def _now_iso() -> str:
    _CLOCK[0] += 1
    return f"2026-10-11T00:00:{_CLOCK[0] % 60:02d}.{_CLOCK[0]:06d}+00:00"


class FakeQuery:
    """supabase-py 표 요청 흉내. 저장 기능이 있는 표(cases·case_workspace·case_drafts)는 메모리에 행을 둔다.
    RLS(본인만)는 로컬 Postgres 시험에서 검사하고, 여기서는 앱이 user_id로 거르는지만 본다."""

    def __init__(self, table):
        self.table_name = table
        self.op = "select"
        self.filters = {}
        self.payload = None

    def __getattr__(self, name):
        def method(*args, **kwargs):
            if name in ("insert", "upsert", "update", "delete"):
                self.op = name
                self.payload = args[0] if args else None
                WRITES.append((self.table_name, name, args))
            if name == "eq" and len(args) == 2:
                self.filters[args[0]] = str(args[1])
            if name == "select":
                self.columns = [c.strip() for c in (args[0] if args else "*").split(",")]
            return self
        return method

    def _match(self, row):
        return all(str(row.get(k)) == v for k, v in self.filters.items())

    def execute(self):
        if (self.table_name, self.op) in FAIL_NEXT:
            FAIL_NEXT.remove((self.table_name, self.op))
            raise RuntimeError("fake network error")
        if self.table_name == "consent_records" and self.op == "select":
            columns = getattr(self, "columns", ["*"])
            if columns == ["*"] and CONSENT_STAR_DENIED[0]:
                raise FakeAPIError("permission denied for table consent_records", "42501")
            for column in columns:
                if column != "*" and column not in CONSENT_COLUMNS:
                    raise FakeAPIError(f"column consent_records.{column} does not exist", "42703")
            row = {"user_id": self.filters.get("user_id", USER_ID), "terms_version": "x", "privacy_version": "2026-09-27"}
            return SimpleNamespace(data=[] if CONSENT_MISSING else [row])
        rows = TABLES.get(self.table_name)
        if rows is None:
            return SimpleNamespace(data=[])
        if self.op == "select":
            return SimpleNamespace(data=[dict(r) for r in rows if self._match(r)])
        if self.op == "delete":
            gone = [r for r in rows if self._match(r)]
            rows[:] = [r for r in rows if not self._match(r)]
            if self.table_name == "cases":  # 사건 삭제 트리거 흉내
                for r in gone:
                    for other in ("case_workspace", "case_drafts"):
                        TABLES[other][:] = [x for x in TABLES[other] if str(x.get("case_id")) != str(r.get("case_id"))]
            return SimpleNamespace(data=gone)
        if self.op == "insert":
            row = dict(self.payload)
            if self.table_name == "case_workspace":
                if any(r["user_id"] == row["user_id"] and r["case_id"] == row["case_id"] for r in rows):
                    raise RuntimeError('duplicate key value violates unique constraint "case_workspace_pkey" (23505)')
                row.setdefault("version", 1)
                row["updated_at"] = _now_iso()
            rows.append(row)
            return SimpleNamespace(data=[dict(row)])
        if self.op == "upsert":
            row = dict(self.payload)
            row["updated_at"] = _now_iso()
            for r in rows:
                if r["user_id"] == row["user_id"] and str(r["case_id"]) == str(row["case_id"]):
                    r.update(row)
                    return SimpleNamespace(data=[dict(r)])
            rows.append(row)
            return SimpleNamespace(data=[dict(row)])
        if self.op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload)
                    if self.table_name == "case_workspace":
                        r["version"] = int(r.get("version") or 1) + 1
                        r["updated_at"] = _now_iso()
                    if self.table_name == "cases":
                        r["edit_version"] = int(r.get("edit_version") or 1) + 1
                    out.append(dict(r))
            return SimpleNamespace(data=out)
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


FILES = {}  # 가짜 파일 저장소 {경로: 바이트}
DOWNLOAD_FAIL = set()  # 내려받기에 실패할 파일 경로
LIST_FAIL = set()  # 목록 조회에 실패할 폴더(사용자ID/사건ID)


class FakeBucket:
    def list(self, folder, *args, **kwargs):
        if folder.rstrip("/") in LIST_FAIL:
            raise RuntimeError("fake storage list error")
        prefix = folder.rstrip("/") + "/"
        return [{"name": path[len(prefix):], "id": path, "metadata": {"size": len(data)}} for path, data in FILES.items()
                if path.startswith(prefix)]

    def upload(self, path, data, *args, **kwargs):
        FILES[path] = bytes(data)
        WRITES.append(("storage", "upload", (path,)))

    def remove(self, paths):
        for path in paths:
            FILES.pop(path, None)
        WRITES.append(("storage", "remove", tuple(paths)))
        return []

    def download(self, path):
        if path in DOWNLOAD_FAIL:
            raise RuntimeError("fake storage error")
        return FILES.get(path, b"")


class FakeStorage:
    def from_(self, bucket):
        return FakeBucket()


class FakeSupabase:
    def __init__(self, *args, **kwargs):
        self.auth = FakeAuth()
        self.storage = FakeStorage()
        self.postgrest = SimpleNamespace(auth=lambda token: None)

    def table(self, name):
        return FakeQuery(name)

    def rpc(self, name, params=None):
        RPC_CALLS.append((name, params))
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


def fake_wav(seconds: float = 0.2, rate: int = 24000) -> bytes:
    import struct
    pcm = b"\x00\x00" * int(seconds * rate)
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


TTS_CALLS = []    # 음성 생성 요청 본문(REST)
TTS_REPLIES = []  # 다음 음성 생성 응답: WAV 바이트, 또는 (HTTP 상태, 오류 본문)


def fake_tts_post(url, json=None, headers=None, timeout=None):
    """httpx.post 대신 쓰는 가짜 음성 생성. 공식 문서의 REST 응답 모양(steps[].content[].data)으로 돌려준다."""
    import base64
    import json as _json
    import httpx
    TTS_CALLS.append({"url": url, "body": json, "has_key": bool((headers or {}).get("x-goog-api-key"))})
    reply = TTS_REPLIES.pop(0) if TTS_REPLIES else None
    request = httpx.Request("POST", url)
    if isinstance(reply, tuple):
        status, body = reply
        return httpx.Response(status, text=_json.dumps(body), request=request)
    data = base64.b64encode(reply or fake_wav()).decode()
    return httpx.Response(200, json={"steps": [{"type": "model_output", "content": [{"type": "audio", "data": data}]}]},
                          request=request)


class FakeGenaiClient:
    def __init__(self, *args, **kwargs):
        self.models = _Models()


CONSENT_MISSING = False   # True면 현재 버전 동의가 없는 것처럼(동의 화면 시험)
RPC_CALLS = []


def reset():
    global CONSENT_MISSING
    CONSENT_MISSING = False
    RPC_CALLS.clear()
    DOWNLOAD_FAIL.clear()
    LIST_FAIL.clear()
    CONSENT_STAR_DENIED[0] = False
    TTS_CALLS.clear()
    TTS_REPLIES.clear()
    CALLS.clear()
    REPLIES.clear()
    WRITES.clear()
    CASES.clear()
    FILES.clear()
    TABLES["case_workspace"].clear()
    TABLES["case_drafts"].clear()
    FAIL_NEXT.clear()


def contents_text(call) -> str:
    """요청 내용을 글로 펼친다(검사용)."""
    out = []
    for item in call["contents"]:
        if isinstance(item, dict):
            out.append(item["role"] + ":" + "".join(p.get("text", "") for p in item["parts"]))
        else:
            out.append(str(getattr(item, "text", "") or "[binary]"))
    return "\n".join(out)
