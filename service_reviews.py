"""이용 후기 규칙. Streamlit에 의존하지 않는다.

- 후기는 별점(1~5), 도움이 된 점, 개선할 점, 공개 방식(운영자에게만 / 익명으로 공개)으로 이뤄진다. 후기를 쓰지 않아도 이용에 제한이 없다.
- 익명 공개를 고르면 운영자가 확인한 뒤에만 게시된다(DB 트리거·함수: supabase/migrations/20261023000001_service_reviews.sql).
- 공개 후기에 연락처·계좌·이메일 같은 개인정보가 보이면 보내기 전에 막고, 날짜·금액처럼 사건이 드러날 수 있는 부분은 알린다.
  사람 이름은 글자만으로 가려낼 수 없어 안내 문구와 운영자 확인으로 막는다.
- 실제 후기가 없으면 빈 화면을 보여 준다. 예시·가짜 후기를 만들지 않는다.
"""

import re

TEXT_MAX_CHARS = 500
VISIBILITY = {"operator": "운영자에게만 보내기", "public": "익명으로 공개하기"}
VISIBILITY_HELP = {
    "operator": "공개되지 않아요. 서비스 개선에만 써요.",
    "public": "운영자가 확인한 뒤 이름 없이 게시해요. 별점과 두 칸의 글만 보여요.",
}
STATUS_LABELS = {"pending": "확인 중", "published": "게시됨", "rejected": "공개되지 않음", "private": "운영자에게만"}
REJECT_REASONS = ["개인정보가 들어 있음", "구체적인 사건 내용이 들어 있음", "특정인을 비방하거나 결과를 보장하는 내용", "기타"]
DO_NOT_WRITE = ("내 이름·상대방 이름·연락처·계좌번호, 구체적인 사건 내용(날짜·금액·장소), 증거파일 내용은 적지 마세요. "
                "후기에는 파일을 붙일 수 없어요.")
REVIEW_NOTICE = "후기는 이용자 개인의 경험이에요. 사건 결과나 효과를 보장하지 않아요."

_PHONE_RE = re.compile(r"01[016789][\s.\-]?\d{3,4}[\s.\-]?\d{4}|0\d{1,2}[\s.\-]\d{3,4}[\s.\-]\d{4}")
_ACCOUNT_RE = re.compile(r"\d{2,6}-\d{2,6}-\d{2,8}|\d{10,}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL_RE = re.compile(r"https?://|www\.", re.I)
_DATE_RE = re.compile(r"\d{1,4}\s*(?:년|월|일)|\d{1,2}[./]\d{1,2}")
_AMOUNT_RE = re.compile(r"\d[\d,]*\s*(?:만|천|억)?\s*원")


def review_flags(text: str) -> dict:
    """후기 글에서 공개하면 안 될 수 있는 부분. {'block': [...], 'warn': [...]} (사람이 읽는 이름)."""
    text = text or ""
    block, warn = [], []
    if _PHONE_RE.search(text):
        block.append("전화번호처럼 보이는 숫자")
    if _EMAIL_RE.search(text):
        block.append("이메일 주소")
    if _ACCOUNT_RE.search(_PHONE_RE.sub(" ", text)):  # 전화번호를 뺀 뒤에도 남는 긴 번호
        block.append("계좌·번호처럼 보이는 숫자")
    if _URL_RE.search(text):
        block.append("인터넷 주소")
    if _DATE_RE.search(text):
        warn.append("날짜")
    if _AMOUNT_RE.search(text):
        warn.append("금액")
    return {"block": block, "warn": warn}


def clean_review(rating, helpful: str, improve: str, visibility: str) -> dict:
    return {
        "rating": rating,
        "helpful": (helpful or "").strip()[:TEXT_MAX_CHARS],
        "improve": (improve or "").strip()[:TEXT_MAX_CHARS],
        "visibility": visibility if visibility in VISIBILITY else "",
    }


def review_problems(review: dict) -> list:
    """보내기 전에 막을 문제(사람이 읽는 문장). 없으면 빈 목록."""
    problems = []
    if not isinstance(review.get("rating"), int) or not 1 <= review["rating"] <= 5:
        problems.append("별점을 골라 주세요.")
    if review.get("visibility") not in VISIBILITY:
        problems.append("어떻게 보낼지 골라 주세요.")
    if review.get("visibility") == "public":
        flags = review_flags(review.get("helpful", "") + "\n" + review.get("improve", ""))
        if flags["block"]:
            problems.append("공개 후기에 " + ", ".join(flags["block"]) + "이(가) 보여요. 지우고 보내 주세요.")
    return problems
