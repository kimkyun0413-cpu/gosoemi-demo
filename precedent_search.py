"""판례·법령 찾기: 공식 판례 검색 연결과 검색어 제안, 법제처 국가법령정보 공동활용 Open API 조회. Streamlit에 의존하지 않는다.

- 고소미는 판례·법령을 직접 고르거나 만들어 보여 주지 않는다. 공식 서비스에서 실제로 조회된 것만 출처와 함께 보여 준다.
- 기본: 국가법령정보센터 판례 검색 페이지로 연결하고, 붙여 넣을 검색어를 제안한다.
  (확인 2026-10-10: 검색 페이지 주소에 검색어를 붙여도 검색되지 않아 페이지만 연결한다.)
- LAW_OPEN_API_OC(법제처 공동활용에서 승인받은 인증값)를 설정하면 판례 목록(lawSearch.do?target=prec)과
  법령 목록(target=law)을 조회한다. 호출 서버의 IP·도메인을 공동활용 누리집에 등록해야 한다.
- 확인 2026-10-22(실제 호출): 인증에 실패해도 HTTP 200으로 {"result": "사용자 정보 검증에 실패하였습니다.", ...}를
  돌려준다. 이것을 '결과 없음'으로 보여 주면 안 되므로 LawApiError로 구분한다. 결과가 1건이면 목록 대신 객체가 온다.
  상세 링크(판례상세링크·법령상세링크)에는 인증값이 들어 있어 쓰지 않고, 누리집 공개 주소를 만든다.
- 법제처 법령정보를 제공할 때는 출처(법제처 국가법령정보센터)를 표시해야 한다(공동활용 이용안내).
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
PRECEDENT_PAGE_URL = "https://www.law.go.kr/LSW/precInfoP.do?precSeq="
LAW_PAGE_URL = "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq="
QUERY_MAX_CHARS = 40
PRECEDENT_SOURCE_FILTER = "대법원"  # 데이터 출처: 법원 판례만(국세법령정보시스템 자료 제외)


class LawApiError(RuntimeError):
    """Open API가 목록 대신 오류(인증 실패·IP 미등록·형식 변경 등)를 돌려줌. '조회 결과 없음'과 다르다."""

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


def build_search_url(query: str, oc: str, display: int = 10, search: int = 1) -> str:
    """판례 목록 조회. search=1 사건명, 2 본문."""
    params = {"OC": oc, "target": "prec", "type": "JSON", "query": query.strip(), "display": display, "search": search,
              "datSrcNm": PRECEDENT_SOURCE_FILTER}
    return f"{OPEN_API_URL}?{urlencode(params)}"


def build_law_search_url(query: str, oc: str, display: int = 30) -> str:
    params = {"OC": oc, "target": "law", "type": "JSON", "query": query.strip(), "display": display}
    return f"{OPEN_API_URL}?{urlencode(params)}"


def _root(raw: str, key: str) -> dict:
    """응답에서 목록 묶음(PrecSearch·LawSearch)을 꺼낸다. 오류 응답이나 다른 형식이면 LawApiError."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise LawApiError("응답 형식을 읽을 수 없음") from None
    if not isinstance(data, dict) or not isinstance(data.get(key), dict):
        message = str(data.get("result") or "").strip() if isinstance(data, dict) else ""
        raise LawApiError(message or "응답 형식이 바뀜")
    return data[key]


def _items(node) -> list:
    if isinstance(node, list):
        return [x for x in node if isinstance(x, dict)]
    return [node] if isinstance(node, dict) else []


def _total(root: dict) -> int:
    try:
        return int(str(root.get("totalCnt") or "0").strip() or 0)
    except ValueError:
        return 0


def _date(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return f"{digits[:4]}.{digits[4:6]}.{digits[6:8]}" if len(digits) == 8 else str(value or "")


def _digits(value) -> str:
    text = str(value or "").strip()
    return text if text.isdigit() else ""


def parse_precedent_list(raw: str) -> dict:
    """판례 목록 응답에서 실제로 받은 항목만 꺼낸다. {"total": 전체 건수, "rows": [...]}. 오류 응답이면 LawApiError."""
    root = _root(raw, "PrecSearch")
    rows = []
    for row in _items(root.get("prec")):
        case_no = str(row.get("사건번호") or "").strip()
        name = str(row.get("사건명") or "").strip()
        if not case_no or not name:
            continue
        seq = _digits(row.get("판례일련번호"))
        rows.append({
            "name": name,
            "case_no": case_no,
            "date": _date(row.get("선고일자")),
            "court": str(row.get("법원명") or "").strip(),
            "kind": str(row.get("사건종류명") or "").strip(),
            "link": PRECEDENT_PAGE_URL + seq if seq else "",
            "source": OPEN_API_SOURCE,
        })
    total = _total(root)
    if total and not rows:
        raise LawApiError("전체 건수는 있는데 항목을 읽지 못함")
    return {"total": total, "rows": rows}


_LAW_KIND_ORDER = {"법률": 0, "대통령령": 1}


def parse_law_list(raw: str, query: str = "") -> dict:
    """법령 목록 응답에서 실제로 받은 항목만 꺼낸다. 이름이 검색어와 같은 법령, 법률, 짧은 이름 순으로 앞에 둔다."""
    root = _root(raw, "LawSearch")
    q = re.sub(r"\s", "", query or "")
    rows = []
    for row in _items(root.get("law")):
        name = str(row.get("법령명한글") or "").strip()
        if not name:
            continue
        seq = _digits(row.get("법령일련번호"))
        rows.append({
            "name": name,
            "short": str(row.get("법령약칭명") or "").strip(),
            "kind": str(row.get("법령구분명") or "").strip(),
            "ministry": str(row.get("소관부처명") or "").strip(),
            "effective": _date(row.get("시행일자")),
            "promulgated": _date(row.get("공포일자")),
            "status": str(row.get("현행연혁코드") or "").strip(),
            "link": LAW_PAGE_URL + seq if seq else "",
            "source": OPEN_API_SOURCE,
        })
    exact = lambda r: q and q in (re.sub(r"\s", "", r["name"]), re.sub(r"\s", "", r["short"]))  # noqa: E731
    rows.sort(key=lambda r: (not exact(r), _LAW_KIND_ORDER.get(r["kind"], 2), len(r["name"])))
    total = _total(root)
    if total and not rows:
        raise LawApiError("전체 건수는 있는데 항목을 읽지 못함")
    return {"total": total, "rows": rows}


def search_precedents(query: str, oc: str, fetch, display: int = 10) -> dict:
    """fetch(url) -> 응답 글(str). 실제 조회된 판례만 돌려준다: {"total", "rows", "scope": "사건명"|"본문"}.

    사건명에서 찾지 못하면 본문에서 한 번 더 찾는다. 키(OC)가 없으면 RuntimeError, 검색어 문제는 ValueError,
    API 오류는 LawApiError(이때 결과를 만들어 내지 않는다).
    """
    if not oc:
        raise RuntimeError("open api not configured")
    problem = check_query(query)
    if problem:
        raise ValueError(problem)
    result = parse_precedent_list(fetch(build_search_url(query, oc, display, search=1)))
    if result["total"]:
        return {**result, "scope": "사건명"}
    result = parse_precedent_list(fetch(build_search_url(query, oc, display, search=2)))
    return {**result, "scope": "본문"}


def search_laws(query: str, oc: str, fetch, display: int = 30) -> dict:
    """실제 조회된 법령만 돌려준다: {"total", "rows"}. 오류 규칙은 search_precedents와 같다."""
    if not oc:
        raise RuntimeError("open api not configured")
    problem = check_query(query)
    if problem:
        raise ValueError(problem)
    return parse_law_list(fetch(build_law_search_url(query, oc, display)), query)
