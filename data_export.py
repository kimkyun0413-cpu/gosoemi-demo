"""'내 자료 모두 내려받기': 이용자 본인 자료를 ZIP으로 묶는다. Streamlit·DB에 의존하지 않는다.

- 앱이 이용자 토큰으로 불러온 자료(사건·초안·사건 정리·동의 기록·증거파일)만 받아 묶는다. 여기서는 아무것도 불러오지 않는다.
- 로그인 토큰·인증키·비밀번호·내부 비밀값은 넣지 않는다. 받은 행에도 그런 열이 있으면 빼고(EXCLUDED_KEYS),
  본인(user_id) 것이 아닌 행·경로는 넣지 않는다(다른 이용자 자료가 섞이지 않게 한 번 더 거른다).
- 일부를 불러오지 못했으면 '받지 못한 자료'를 설명 파일과 화면에 그대로 적는다. 다 담았다고 하지 않는다.
- 서버 메모리를 적게 쓰도록 나눈다: 1번 묶음(build_text_part)에는 글 자료와 증거파일 전체 목록만 넣고,
  증거파일은 약 EVIDENCE_PART_MAX_BYTES씩 묶음을 정해 두었다가(plan) 이용자가 그 묶음을 고를 때 하나씩 만든다(build_evidence_part).
  ZIP 안 파일 경로는 처음에 모두 정해 두어, 묶음이 달라도 같은 이름이 겹치지 않는다.
"""

import datetime
import io
import json
import re
import zipfile

from calendar_ics import ALARM_CHOICES, KIND_LABELS
from case_guide import CASE_FIELDS, PROGRESS_ITEMS

# 증거파일 묶음 하나의 최대 크기(목록에 적힌 크기 기준). 한 번에 이 묶음 하나만 메모리에 올린다.
# 증거파일은 하나에 최대 10MB라 묶음마다 1개 이상 들어간다. 크기를 모르는 파일은 10MB로 보고 나눈다.
EVIDENCE_PART_MAX_BYTES = 20 * 1024 * 1024
UNKNOWN_FILE_SIZE = 10 * 1024 * 1024
# 로그인·인증과 관련된 값이나 내부 관리용 값. 자료에 섞여 있어도 내려받는 파일에 넣지 않는다.
EXCLUDED_KEYS = {"access_token", "refresh_token", "token", "password", "api_key", "secret", "login_nonce", "provider_token"}
NOT_INCLUDED_NOTE = (
    "AI 상담(글·음성 대화)은 서버에 저장하지 않아 이 파일에 들어 있지 않습니다. 음성도 저장하지 않습니다.\n"
    "로그인 토큰·비밀번호·인증키 같은 보안 정보는 넣지 않았습니다."
)


def _clean(row: dict) -> dict:
    return {k: v for k, v in (row or {}).items() if k.lower() not in EXCLUDED_KEYS}


def _safe_name(text: str, limit: int = 30) -> str:
    """ZIP 안 폴더·파일 이름에 쓸 수 있게 다듬는다(경로 문자 제거)."""
    text = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", str(text or "")).strip().strip(".")
    text = re.sub(r"\s+", " ", text)
    return (text[:limit].strip() or "이름없음")


def _date(value) -> str:
    return str(value or "")[:10]


def case_text(case: dict) -> str:
    lines = [f"사건 번호: {case.get('case_id', '')}", f"저장한 날: {_date(case.get('created_at'))}", ""]
    for label, key in CASE_FIELDS:
        value = (case.get(key) or "").strip()
        lines += [f"[{label}]", value or "(비어 있음)", ""]
    return "\n".join(lines).rstrip() + "\n"


def workspace_text(row: dict) -> str:
    """사건 정리 기록: 경위, 증거 설명·연결, 일정, 준비 체크리스트."""
    out = ["[사건 경위]"]
    timeline = row.get("timeline") or []
    if not timeline:
        out.append("(적은 경위 없음)")
    for i, item in enumerate(timeline, 1):
        when = item.get("when") or "날짜 모름"
        link = item.get("evidence") or "연결 안 함"
        out.append(f"{i}. {when} — {item.get('what', '')} (관련 자료: {link})")
    out += ["", "[증거 설명]"]
    notes = row.get("evidence_notes") or []
    if not notes:
        out.append("(적은 증거 설명 없음)")
    for item in notes:
        out.append(f"- {item.get('name', '')}: {item.get('shows') or '(설명 없음)'}")
    out += ["", "[일정]"]
    schedules = row.get("schedules") or []
    if not schedules:
        out.append("(적은 일정 없음)")
    for item in schedules:
        when = item.get("date", "") + (f" {item['time']}" if item.get("time") else " (하루 종일)")
        alarm = ALARM_CHOICES.get(int(item.get("alarm") or 0), "")
        kind = KIND_LABELS.get(item.get("kind"), "기타")
        memo = f" · 메모: {item['memo']}" if item.get("memo") else ""
        out.append(f"- {when} · {kind} · {item.get('title', '')} · {alarm}{memo}")
    out += ["", "[준비 체크리스트]"]
    checklist = row.get("checklist") or {}
    for key, label in PROGRESS_ITEMS:
        out.append(f"- [{'v' if checklist.get(key) else ' '}] {label}")
    for key, label in (("submitted_to", "제출한 곳"), ("submitted_on", "제출한 날"), ("next_note", "다음에 할 일 메모")):
        if checklist.get(key):
            out.append(f"- {label}: {checklist[key]}")
    if row.get("updated_at"):
        out += ["", f"마지막 저장: {row['updated_at']}"]
    return "\n".join(out) + "\n"


def consent_text(account: dict, consents: list) -> str:
    out = ["[계정]", f"이메일: {account.get('email') or '(없음)'}", f"로그인 방법: {account.get('method') or ''}", "",
           "[동의 기록] (이용약관 버전 / 개인정보처리방침 버전 / 동의한 때)"]
    if not consents:
        out.append("(기록 없음)")
    for row in consents:
        out.append(f"- {row.get('terms_version', '')} / {row.get('privacy_version', '')} / "
                   f"{row.get('created_at') or '(동의한 때는 기록에 없음)'}")
    return "\n".join(out) + "\n"


def evidence_summary_text(summary: dict) -> str:
    """증거파일 개수 문구. 목록을 확인하지 못한 사건이 있으면 0개라고 하지 않는다."""
    files, checked, failed = summary["files"], summary.get("evidence_checked"), summary.get("evidence_failed", 0)
    if failed:
        return (f"증거파일 {files}개(사건 {failed}건은 목록을 불러오지 못해 파일이 있는지 확인하지 못했어요)" if files or checked
                else f"증거파일: 확인하지 못함(사건 {failed}건 목록을 불러오지 못함)")
    if files == 0 and checked:
        return f"증거파일 0개(사건 {checked}건 모두 목록을 확인했고, 첨부한 파일이 없어요)"
    return f"증거파일 {files}개"


def _mb(n: int) -> str:
    return f"{max(0.1, n / (1024 * 1024)):.1f}MB"


def readme_text(summary: dict, missing: list, now: datetime.datetime, parts: list) -> str:
    """1번 묶음의 설명 파일: 들어 있는 것, 증거파일이 몇 번 묶음에 있는지, 받지 못한 자료."""
    count = 1 + len(parts)
    lines = [
        "고소미 — 내 자료 모두 내려받기",
        f"만든 때: {now:%Y-%m-%d %H:%M} (한국 시간)",
        f"묶음: 1 / {count}" + (" — 증거파일은 2번부터의 묶음에 나눠 담습니다. 모든 묶음을 받아 같은 폴더에 풀어 주세요." if parts else ""),
        "",
        "[들어 있는 것]",
        f"- 사건 {summary['cases']}건: 사건마다 폴더가 있고, 그 안에 사건 내용·저장된 초안·사건 정리(경위·증거 설명·일정·체크리스트)가 있습니다.",
        f"- 저장된 초안 {summary['drafts']}개, 사건 정리 기록 {summary['workspaces']}개, {evidence_summary_text(summary)}",
        "- 계정_동의기록.txt: 가입한 이메일, 로그인 방법, 이용약관·개인정보처리방침 동의 기록",
        "- 전체자료.json: 위 내용을 컴퓨터가 읽기 쉬운 형식으로 한 번 더 담은 파일",
        "",
        "[들어 있지 않은 것]",
        NOT_INCLUDED_NOTE,
        "",
    ]
    if parts:
        lines.append("[증거파일 목록] (묶음 번호 · ZIP 안 위치 · 크기)")
        for no, part in enumerate(parts, 2):
            for item in part:
                lines.append(f"- {no}번 묶음 · {item['zip_path']} · {_mb(int(item.get('size') or 0))}")
        lines.append("")
    if missing:
        lines += ["[받지 못한 자료] — 일부 자료를 담지 못했습니다. 잠시 후 다시 내려받거나 문의해 주세요."]
        lines += [f"- {item}" for item in missing]
    else:
        lines += ["[받지 못한 자료] 글 자료는 모두 담았습니다." + (" 증거파일은 각 묶음의 설명 파일에서 확인해 주세요." if parts else "")]
    lines += ["", "이 파일에는 개인정보가 들어 있습니다. 다른 사람과 함께 쓰는 컴퓨터나 공유 폴더에 두지 마세요."]
    return "\n".join(lines) + "\n"


def plan_parts(items: list, limit: int = None) -> list:
    """증거파일 목록을 묶음으로 나눈다(목록의 크기 기준, 아직 내려받지 않음). 각 묶음은 파일 목록."""
    limit = limit or EVIDENCE_PART_MAX_BYTES
    parts, current, size = [], [], 0
    for item in items:
        n = int(item.get("size") or 0) or UNKNOWN_FILE_SIZE
        if current and size + n > limit:
            parts.append(current)
            current, size = [], 0
        current.append(item)
        size += n
    if current:
        parts.append(current)
    return parts


def build_text_part(user_id: str, account: dict, cases: list, drafts: dict, workspaces: dict, consents: list,
                    evidence: list, missing: list, now: datetime.datetime, limit: int = None,
                    evidence_status: dict = None) -> dict:
    """1번 묶음(글 자료 + 증거파일 목록)을 만들고, 증거파일 묶음 계획을 돌려준다. 증거파일 내용은 받지 않는다.

    evidence: [{"case_id", "path", "name", "size"}] (목록에서 읽은 것)
    돌려주는 값: {"root", "text_part": bytes, "parts": [[item...]...], "missing", "complete", "summary", "skipped_foreign"}
    complete는 글 자료 기준이다. 증거파일 묶음이 남아 있으면 앱이 묶음을 모두 만들 때까지 다 담았다고 하지 않는다.
    """
    missing = list(missing)
    skipped = 0
    own_cases = []
    for case in cases:
        if str(case.get("user_id")) != str(user_id):
            skipped += 1          # 본인 것이 아니면 넣지 않는다(RLS가 막지만 한 번 더 확인)
            continue
        own_cases.append(_clean(case))
    case_ids = {str(c.get("case_id")) for c in own_cases}
    drafts = {str(k): _clean(v) for k, v in drafts.items()
              if str(k) in case_ids and str((v or {}).get("user_id", user_id)) == str(user_id)}
    workspaces = {str(k): _clean(v) for k, v in workspaces.items()
                  if str(k) in case_ids and str((v or {}).get("user_id", user_id)) == str(user_id)}
    consents = [_clean(r) for r in consents if str(r.get("user_id", user_id)) == str(user_id)]

    folders = {}
    for i, case in enumerate(sorted(own_cases, key=lambda c: str(c.get("created_at") or "")), 1):
        folders[str(case["case_id"])] = f"사건{i:02d}_{_safe_name(case.get('case_type'))}_{_date(case.get('created_at'))}"
    root = f"고소미_내자료_{now:%Y%m%d-%H%M}"

    own_evidence, used = [], set()
    for item in evidence:
        if not str(item.get("path", "")).startswith(f"{user_id}/") or str(item.get("case_id")) not in case_ids:
            skipped += 1
            continue
        folder = f"{root}/{folders[str(item['case_id'])]}/증거파일"
        name = _safe_name(item["name"], 80)
        path, k = f"{folder}/{name}", 2
        while path in used:                       # 같은 이름이 있으면 번호를 붙인다(묶음이 달라도 겹치지 않게 미리 정함)
            path, k = f"{folder}/{k}_{name}", k + 1
        used.add(path)
        own_evidence.append({"case_id": str(item["case_id"]), "path": item["path"], "name": item["name"],
                             "size": int(item.get("size") or 0), "zip_path": path})
    parts = plan_parts(own_evidence, limit)
    summary = {"cases": len(own_cases), "drafts": len(drafts), "workspaces": len(workspaces), "files": len(own_evidence)}
    if evidence_status is not None:   # 증거파일 목록을 확인한 사건 수·확인하지 못한 사건 수
        summary["evidence_checked"] = int(evidence_status.get("checked", 0))
        summary["evidence_failed"] = int(evidence_status.get("failed", 0))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{root}/계정_동의기록.txt", consent_text(account, consents))
        for case in own_cases:
            cid = str(case["case_id"])
            folder = f"{root}/{folders[cid]}"
            z.writestr(f"{folder}/사건내용.txt", case_text(case))
            if cid in drafts:
                z.writestr(f"{folder}/저장된초안.txt",
                           (drafts[cid].get("draft_text") or "") + f"\n\n(마지막 저장: {drafts[cid].get('updated_at', '')})\n")
            if cid in workspaces:
                z.writestr(f"{folder}/사건정리_경위_증거_일정.txt", workspace_text(workspaces[cid]))
        data = {
            "account": {"email": account.get("email"), "method": account.get("method")},
            "consents": consents, "cases": own_cases, "drafts": drafts, "workspaces": workspaces,
            "evidence_files": [{"case_id": f["case_id"], "name": f["name"], "bytes": f["size"], "zip_path": f["zip_path"],
                                "part": no} for no, part in enumerate(parts, 2) for f in part],
            "missing": missing, "exported_at": now.isoformat(),
        }
        if "ai_usage" in account:
            # RPC derives auth.uid(); still filter the returned account id defensively.
            data["ai_usage"] = [_clean(r) for r in account["ai_usage"] if str(r.get("user_id")) == str(user_id)]
            z.writestr(f"{root}/AI사용량기록.json",
                       json.dumps(data["ai_usage"], ensure_ascii=False, indent=1, default=str))
        z.writestr(f"{root}/전체자료.json", json.dumps(data, ensure_ascii=False, indent=1, default=str))
        z.writestr(f"{root}/읽어보세요.txt", readme_text(summary, missing, now, parts))
    return {"root": root, "text_part": buf.getvalue(), "parts": parts, "missing": missing, "complete": not missing,
            "summary": summary, "skipped_foreign": skipped}


def build_evidence_part(user_id: str, root: str, part: list, part_no: int, part_count: int, fetch,
                        now: datetime.datetime) -> dict:
    """증거파일 묶음 하나를 만든다. fetch(path) -> bytes 로 이 묶음의 파일만 받아 바로 ZIP에 쓰고 원본은 들고 있지 않는다.
    받지 못한 파일은 이름을 '받지 못한 자료'로 적는다. 돌려주는 값: {"data": bytes, "missing": [...], "files": n}"""
    missing, written = [], []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for item in part:
            if not str(item.get("path", "")).startswith(f"{user_id}/"):
                missing.append(f"'{item.get('name')}' — 본인 파일이 아니라서 넣지 않았습니다")
                continue
            try:
                z.writestr(item["zip_path"], fetch(item["path"]))
                written.append(item["zip_path"])
            except Exception:
                missing.append(f"증거파일 '{item['name']}'을(를) 받지 못했습니다")
        lines = ["고소미 — 내 자료 모두 내려받기 (증거파일 묶음)", f"만든 때: {now:%Y-%m-%d %H:%M} (한국 시간)",
                 f"묶음: {part_no} / {part_count} — 1번 묶음과 같은 폴더에 풀어 주세요.", "", f"[이 묶음의 증거파일 {len(written)}개]"]
        lines += [f"- {path}" for path in written]
        lines += [""] + (["[받지 못한 자료] — 이 묶음을 다시 만들어 보거나 사건 화면에서 하나씩 받아 주세요."] + [f"- {m}" for m in missing]
                         if missing else ["[받지 못한 자료] 없음 — 이 묶음의 파일을 모두 담았습니다."])
        z.writestr(f"{root}/읽어보세요_{part_no}번묶음.txt", "\n".join(lines) + "\n")
    return {"data": buf.getvalue(), "missing": missing, "files": len(written)}

