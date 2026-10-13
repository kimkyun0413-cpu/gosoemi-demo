"""AI 사용량 기록용 전용 DB 계정 연결 점검(읽기만, 아무것도 쓰지 않음). 비밀번호·주소는 출력하지 않는다.

서버에서: docker compose --env-file .env -f deploy/compose.yaml exec app python scripts/check_ai_usage_db.py
확인하는 것:
 1. 주소 형식: 전용 계정 gosomi_ai_usage.<프로젝트ref>, sslmode=verify-full, sslrootcert(Supabase CA)
 2. 비밀번호 로그인 성공 + 암호화 연결(TLS 버전) + 인증서·호스트 확인(verify-full로 연결됨)
 3. 틀린 비밀번호는 거부되는지
 4. 암호화하지 않은 연결(sslmode=disable)이 거부되는지(대시보드 'Enforce SSL'이 켜져 있으면 거부)
 5. 권한: 예약·완료만 가능, 정리·보고·본인 기록 함수 불가, 사건·사용량 표 읽기 불가, RLS 우회·다른 역할 불가
결과: 모두 통과면 종료 코드 0.
"""
import os
import secrets
import sys
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_runtime import usage_dsn_problems  # noqa: E402

RESERVE = "public.reserve_ai_usage(uuid,uuid,text,text,integer,integer,integer,integer)"
FINISH = "public.finish_ai_usage(uuid,text,bigint,bigint,bigint,bigint)"
results = []


def check(name, ok, note=""):
    results.append(bool(ok))
    print(("통과" if ok else "실패"), name, f"— {note}" if note else "")


def with_changes(dsn, password=None, **query):
    url = urlparse(dsn)
    netloc = url.netloc
    if password is not None:
        user, host = netloc.rsplit("@", 1)
        netloc = f"{user.split(':', 1)[0]}:{password}@{host}"
    q = dict(parse_qsl(url.query))
    q.update(query)
    return urlunparse(url._replace(netloc=netloc, query=urlencode(q)))


def connect(dsn):
    import psycopg
    return psycopg.connect(dsn, autocommit=True, connect_timeout=8, prepare_threshold=None,
                           application_name="gosomi-ai-usage-check")


def refused(dsn):
    try:
        connect(dsn).close()
        return False
    except Exception:
        return True


def denied(conn, sql):
    try:
        conn.execute(sql).fetchone()
        return False
    except Exception:
        return True


def main():
    dsn = os.environ.get("AI_USAGE_DB_URL", "")
    problems = usage_dsn_problems(dsn)
    check("주소 형식(전용 계정·verify-full·CA 인증서)", not problems, ", ".join(problems))
    if problems:
        return 1
    if not os.path.exists(dict(parse_qsl(urlparse(dsn).query))["sslrootcert"]):
        check("CA 인증서 파일 있음", False, "sslrootcert 경로에 파일이 없음")
        return 1
    try:
        conn = connect(dsn)
    except Exception as error:
        check("비밀번호 로그인 + 암호화 연결", False, type(error).__name__)
        return 1
    # 이 프로그램 쪽에서 본 암호화 여부(가장 확실한 값). verify-full로 연결됐으므로 인증서·호스트 확인도 통과한 것.
    # 서버가 본 TLS 버전은 참고용(Supabase 풀러를 거치면 풀러→DB 구간 값일 수 있음).
    try:
        version = conn.execute("select version from pg_stat_ssl where pid = pg_backend_pid()").fetchone()
        version = version[0] if version else "?"
    except Exception:
        version = "?"
    check("비밀번호 로그인 + 암호화 연결", conn.pgconn.ssl_in_use, f"인증서·호스트 확인(verify-full) 통과, 서버 TLS {version}")
    user = conn.execute("select current_user").fetchone()[0]
    check("전용 계정으로 접속", user == "gosomi_ai_usage", user)
    check("틀린 비밀번호 거부", refused(with_changes(dsn, password=secrets.token_hex(16))))
    plain_refused = refused(with_changes(dsn, sslmode="disable"))
    check("암호화 안 한 연결 거부", plain_refused,
          "" if plain_refused else "대시보드 Database → SSL Configuration → 'Enforce SSL on incoming connections'를 켜 주세요")
    priv = lambda f: conn.execute("select has_function_privilege(current_user, %s, 'execute')", (f,)).fetchone()[0]
    check("예약 함수 실행 가능", priv(RESERVE))
    check("완료 함수 실행 가능", priv(FINISH))
    for fn in ("public.prune_ai_usage()", "public.ai_usage_report()", "public.my_ai_usage()"):
        check(f"{fn} 실행 불가", not priv(fn))
    check("사용량 표 직접 읽기 불가", denied(conn, "select 1 from ai_private.requests limit 1"))
    check("사건 표 읽기 불가", denied(conn, "select 1 from public.cases limit 1"))
    row = conn.execute("select rolbypassrls, rolsuper, rolcreaterole, rolconnlimit from pg_roles where rolname = current_user").fetchone()
    check("RLS 우회·관리자·역할 생성 권한 없음", row[:3] == (False, False, False), f"동시 접속 제한 {row[3]}")
    check("service_role로 바꾸기 불가", denied(conn, "set role service_role"))
    conn.close()
    print(f"\n{sum(results)}/{len(results)} 통과")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
