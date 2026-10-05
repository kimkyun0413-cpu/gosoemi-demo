"""유료 요금제 설정과 화면 표시 규칙. 결제사 연동 코드는 없다(supabase/proposals/billing 참고).

판매 조건(사업자등록, 통신판매업 신고, 결제사 계약, 환불 규정 확정)이 갖춰지기 전에는
가격을 보여 주거나 결제 버튼을 만들지 않는다. 설정값이 잘못되면 '준비 중'으로 처리한다.
"""

import json
import uuid
from dataclasses import dataclass

BILLING_MODES = ("off", "test", "live")
PLAN_KINDS = ("one_time", "subscription")


@dataclass(frozen=True)
class Plan:
    plan_id: str
    name: str
    kind: str
    amount: int
    period_days: int


def billing_mode(value) -> str:
    value = (value or "off").strip().lower()
    return value if value in BILLING_MODES else "off"


def parse_plans(json_text) -> list:
    """BILLING_PLANS_JSON 설정을 읽는다. 하나라도 잘못되면 빈 목록을 돌려준다(부분 표시 금지)."""
    if not json_text:
        return []
    try:
        raw = json.loads(json_text)
        plans = []
        for item in raw:
            plan = Plan(
                plan_id=str(item["plan_id"]),
                name=str(item["name"]),
                kind=str(item["kind"]),
                amount=int(item["amount"]),
                period_days=int(item["period_days"]),
            )
            if (
                plan.kind not in PLAN_KINDS
                or plan.amount <= 0
                or not 1 <= plan.period_days <= 366
                or not plan.name.strip()
                or not plan.plan_id.replace("_", "").replace("-", "").isalnum()
            ):
                return []
            plans.append(plan)
        return plans
    except (ValueError, TypeError, KeyError):
        return []


def sales_open(mode: str, plans: list) -> bool:
    """실제 판매 여부. 현재 코드는 결제사 연동이 없으므로 live여도 판매하지 않는다."""
    return False


def plan_summary(plan: Plan, mode: str) -> str:
    kind = "정기결제" if plan.kind == "subscription" else "1회 결제"
    if mode == "test":
        return f"{plan.name} · {kind} · {plan.period_days}일 · 테스트 금액 {plan.amount:,}원 (실제 결제 아님)"
    # off·live에서는 확정되지 않은 가격을 보여 주지 않는다.
    return f"{plan.name} · {kind} (가격 미정)"


def new_idempotency_key() -> str:
    """결제 버튼을 누를 때마다가 아니라 '결제 시작 화면'마다 하나씩 만든다. 두 번 눌러도 같은 주문이 된다."""
    return uuid.uuid4().hex
