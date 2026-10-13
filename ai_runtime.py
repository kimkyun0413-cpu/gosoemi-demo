"""AI request controls. No prompts, responses, audio or credentials enter usage records.

Usage records are written with a dedicated least-privilege Postgres login (gosomi_ai_usage) that can only
execute reserve/finish; the app never holds the Supabase service_role key."""
from collections.abc import Mapping

FEATURE_LIMITS = {
    "chat": (2048, "minimal"), "voice_chat": (1024, "minimal"),
    "draft": (8192, "low"), "rewrite": (8192, "low"),
    "transcribe": (4096, "minimal"), "extract": (4096, "minimal"), "summary": (1536, "minimal"),
}


def generation_settings(feature):
    maximum, thinking = FEATURE_LIMITS[feature]
    return {"max_output_tokens": maximum, "thinking_config": {"thinking_level": thinking}}


def usage_counts(metadata):
    """Missing usage is unknown, never zero. total includes thinking; do not add twice."""
    def value(name):
        raw = metadata.get(name) if isinstance(metadata, Mapping) else getattr(metadata, name, None)
        return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else None
    return {name: value(name) for name in (
        "prompt_token_count", "candidates_token_count", "thoughts_token_count", "total_token_count",
    )}


def public_ai_blockers(*, paid, hosting, age, privacy_v3, durable):
    problems = []
    if not paid:
        problems.append("AI 유료 등급 확인")
    if not hosting:
        problems.append("개인정보 처리에 적합한 서버 확인")
    if age != 18:
        problems.append("만 18세 기준 적용")
    if not privacy_v3:
        problems.append("개인정보 안내 개정 적용")
    if not durable:
        problems.append("재시작 후에도 유지되는 사용량 관리")
    return problems


class UsageStoreUnavailable(RuntimeError):
    pass


# 사용량 DB 연결은 반드시 암호화 + 인증서·호스트 확인(verify-full). 그보다 약하면 연결하지 않는다.
REQUIRED_SSLMODES = {"verify-full"}


def usage_dsn_problems(dsn):
    """AI_USAGE_DB_URL 점검. 문제 목록(빈 목록이면 사용 가능). 비밀번호는 돌려주지 않는다."""
    from urllib.parse import parse_qs, urlparse
    problems = []
    if not dsn:
        return ["AI_USAGE_DB_URL 없음"]
    url = urlparse(dsn)
    query = {k: v[-1] for k, v in parse_qs(url.query).items()}
    if url.scheme not in ("postgresql", "postgres"):
        problems.append("postgresql:// 주소가 아님")
    if not (url.username or "").startswith("gosomi_ai_usage"):
        problems.append("전용 계정(gosomi_ai_usage.<프로젝트ref>)이 아님")
    if query.get("sslmode") not in REQUIRED_SSLMODES:
        problems.append("sslmode=verify-full 아님(암호화·인증서 확인 필요)")
    if not query.get("sslrootcert"):
        problems.append("sslrootcert(Supabase CA 인증서 파일 경로) 없음")
    return problems


class PostgresUsageStore:
    """전용 최소 권한 DB 계정(gosomi_ai_usage)으로 예약·완료 두 함수만 부른다. 사건·파일·계정 자료는 다루지 않는다.

    connect: 테스트에서 바꿔 끼우는 연결 함수(기본 psycopg.connect). 연결은 요청마다 열고 닫는다(풀러 transaction 모드).
    """
    def __init__(self, dsn, connect=None):
        problems = usage_dsn_problems(dsn)
        if problems:
            raise UsageStoreUnavailable("usage DB settings invalid: " + ", ".join(problems))
        self.dsn = dsn
        self._connect = connect

    def _call(self, sql, params):
        connect = self._connect
        if connect is None:
            import psycopg   # 사용량 DB를 켤 때만 필요하다
            connect = psycopg.connect
        try:
            with connect(self.dsn, autocommit=True, connect_timeout=5, prepare_threshold=None,
                         application_name="gosomi-ai-usage") as conn:
                row = conn.execute(sql, params).fetchone()
        except Exception:
            # 오류 내용에 연결 정보가 섞일 수 있어 그대로 올리지 않는다.
            raise UsageStoreUnavailable("usage DB call failed") from None
        return row[0] if row else None

    def reserve(self, user_id, request_id, feature, model, limits):
        result = self._call(
            "select public.reserve_ai_usage(%s, %s, %s, %s, %s, %s, %s, %s)",
            (user_id, request_id, feature, model, limits["user_hour"], limits["total_hour"],
             limits["user_day"], limits["total_day"]))
        if not isinstance(result, str) or result not in {"reserved", "user_hour", "total_hour", "user_day", "total_day"}:
            raise UsageStoreUnavailable("invalid reservation result")
        return result

    def finish(self, request_id, status, metadata=None):
        counts = usage_counts(metadata)
        result = self._call(
            "select public.finish_ai_usage(%s, %s, %s, %s, %s, %s)",
            (request_id, status, counts["prompt_token_count"], counts["candidates_token_count"],
             counts["thoughts_token_count"], counts["total_token_count"]))
        if result is not True:
            raise UsageStoreUnavailable("usage completion not recorded")
