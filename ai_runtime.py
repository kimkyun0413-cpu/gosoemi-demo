"""AI request controls. No prompts, responses, audio or credentials enter usage records."""
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


class SupabaseUsageStore:
    """A server-only client used exclusively for two usage RPCs; never for case data."""
    def __init__(self, client):
        self.client = client

    def reserve(self, user_id, request_id, feature, model, limits):
        result = self.client.rpc("reserve_ai_usage", {
            "p_user_id": user_id, "p_request_id": request_id, "p_feature": feature,
            "p_model": model, "p_user_hour": limits["user_hour"],
            "p_total_hour": limits["total_hour"], "p_user_day": limits["user_day"],
            "p_total_day": limits["total_day"],
        }).execute().data
        if not isinstance(result, str) or result not in {"reserved", "user_hour", "total_hour", "user_day", "total_day"}:
            raise UsageStoreUnavailable("invalid reservation result")
        return result

    def finish(self, request_id, status, metadata=None):
        counts = usage_counts(metadata)
        result = self.client.rpc("finish_ai_usage", {
            "p_request_id": request_id, "p_status": status,
            "p_input": counts["prompt_token_count"], "p_output": counts["candidates_token_count"],
            "p_thinking": counts["thoughts_token_count"], "p_total": counts["total_token_count"],
        }).execute().data
        if result is not True:
            raise UsageStoreUnavailable("usage completion not recorded")
