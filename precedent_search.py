"""판례 찾기: 공식 판례 검색 연결과 검색어 제안, 법제처 Open API 연동 준비. Streamlit에 의존하지 않는다.

- 고소미는 판례를 직접 고르거나 만들어 보여 주지 않는다. 판례는 공식 서비스에서 실제로 조회된 것만 보여 준다.
- 기본: 국가법령정보센터 판례 검색 페이지로 연결하고, 붙여 넣을 검색어를 제안한다.
  (확인 2026-10-10: 검색 페이지 주소에 검색어를 붙여도 검색되지 않아 페이지만 연결한다.)
- 법제처 국가법령정보 공동활용 Open API(판례 목록 조회, lawSearch.do?target=prec)는 회원가입·사용 신청·서버 IP 등록이
  필요하다. Streamlit Community Cloud는 고정 IP가 없어 지금은 연결하지 않는다. LAW_OPEN_API_OC를 설정하면
  search_precedents()로 목록을 받아 사건명·사건번호·선고일자·법원명과 출처를 보여 준다.
"""

import json
import re
from urllib.parse import urlencode

OFFICIAL_PRECEDENT_SEARCH = (
    "국가법령정보센터 판례 검색",
    "https://www.law.go.kr/LSW/precSc.do?menuId=7&subMenuId=47&tabMenuId=213",
)
OPEN_API_SOURCE = "국가법령정보센터(법제처) Open API"
OPEN_API_URL = "https://www.law.go.kr/DRF/lawSearch.do"
QUERY_MAX_CHARS = 40

# 공식 검색창에 붙여 넣을 검색어 예시. 판례 내용이나 결과를 뜻하지 않는다.
PRECEDENT_KEYWORDS = {
    "중고거래 사기": ["중고거래 사기", "인터넷 물품 거래 사기"],
    "온라인 쇼핑 사기": ["인터넷 쇼핑몰 사기", "물품 대금 사기"],
    "투자 사기": ["투자 사기", "리딩방 사기"],
    "보이스피싱·문자 사기": ["보이스피싱", "전기통신금융사기"],
    "명예훼손·모욕": ["정보통신망 명예훼손", "모욕"],
    "협박·폭행": ["협박", "폭행"],
    "스토킹": ["스토킹범죄"],
    "교제·가정폭력": ["가정폭력", "교제폭력"],
    "성범죄·디지털 성범죄": ["카메라등이용촬영", "디지털 성범죄"],
    "학교폭력": ["학교폭력"],
    "직장 내 괴롭힘": ["직장 내 괴롭힘"],
    "개인정보 침해·명의도용": ["개인정보 유출", "명의도용"],
    "임금·퇴직금 미지급": ["임금 체불", "퇴직금 미지급"],
    "빌려준 돈·계약 분쟁": ["대여금", "차용금 사기"],
}

_PERSONAL_RE = re.compile(r"\d{2,6}-\d{2,6}-\d{2,6}|\d{6}-?\d{7}|\d{9,}")


def suggest_keywords(case_type: str) -> list:
    return PRECEDENT_KEYWORDS.get(case_type, [])


def check_query(query: str) -> str:
    """문제가 있으면 안내 문구, 없으면 빈 문자열. 검색어는 외부(법제처)로 가므로 개인정보를 막는다."""
    q = (query or "").strip()
    if not q:
        return "검색어를 적어 주세요."
    if len(q) > QUERY_MAX_CHARS:
        return f"검색어는 {QUERY_MAX_CHARS}자 이내로 짧게 적어 주세요."
    if _PERSONAL_RE.search(q):
        return "전화번호·계좌번호·주민등록번호 같은 숫자는 검색어에 넣지 마세요."
    return ""


def build_search_url(query: str, oc: str, display: int = 10) -> str:
    params = {"OC": oc, "target": "prec", "type": "JSON", "query": query.strip(), "display": display, "search": 1}
    return f"{OPEN_API_URL}?{urlencode(params)}"


def _rows(node):
    if isinstance(node, dict):
        if "사건번호" in node:
            yield node
        for value in node.values():
            yield from _rows(value)
    elif isinstance(node, list):
        for item in node:
            yield from _rows(item)


def _date(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return f"{digits[:4]}.{digits[4:6]}.{digits[6:8]}" if len(digits) == 8 else str(value or "")


def parse_precedent_list(raw: str) -> list:
    """Open API 판례 목록 응답(JSON)에서 실제로 받은 항목만 꺼낸다. 형식이 틀리면 ValueError."""
    data = json.loads(raw)
    results = []
    for row in _rows(data):
        case_no = str(row.get("사건번호") or "").strip()
        name = str(row.get("사건명") or "").strip()
        if not case_no or not name:
            continue
        results.append({
            "name": name,
            "case_no": case_no,
            "date": _date(row.get("선고일자")),
            "court": str(row.get("법원명") or "").strip(),
            "source": OPEN_API_SOURCE,
        })
    return results


def search_precedents(query: str, oc: str, fetch) -> list:
    """fetch(url) -> 응답 글(str). 실제 조회된 판례만 돌려준다. 키(OC)가 없으면 RuntimeError."""
    if not oc:
        raise RuntimeError("open api not configured")
    problem = check_query(query)
    if problem:
        raise ValueError(problem)
    return parse_precedent_list(fetch(build_search_url(query, oc)))
