"""Supabase Auth 요청에 이용자 IP를 실어 보내는 설정(Sb-Forwarded-For). Streamlit에 의존하지 않는다.

배경: 로그인·가입·토큰 갱신 요청은 모두 Streamlit 서버에서 나가므로, Supabase의 IP별 제한
(로그인·가입 5분 30회, 토큰 갱신 5분 150회 등)을 모든 이용자가 서버 IP 하나로 나눠 쓴다.
Supabase는 secret 키로 요청하면서 Sb-Forwarded-For 헤더에 이용자 IP를 넣으면 그 IP 기준으로 제한한다
(공식 문서: Auth > Rate limits > IP address forwarding, 대시보드에서 기능을 켜야 함).

이 모듈은 '언제 secret 키 + 헤더를 쓸지'만 정한다. secret 키는 RLS를 우회하므로
여기서 만든 설정은 Auth 요청(로그인·가입·갱신·인증 확인)에만 쓰고 DB·파일 요청에는 절대 쓰지 않는다.
"""

import ipaddress


def public_ip(value):
    """공인 IP면 정규화한 문자열, 아니면 None."""
    try:
        ip = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return None
    return str(ip)


def client_ip_from(forwarded_for, connection_ip):
    """X-Forwarded-For에서 오른쪽(가장 마지막에 프록시가 붙인 쪽)부터 첫 공인 IP, 없으면 연결 IP(공인일 때만).

    왼쪽 값은 이용자가 직접 써 넣을 수 있어 믿을 수 없다. 프록시는 받은 연결의 IP를 오른쪽에 덧붙이므로,
    우리 앞의 프록시들이 모두 사설 IP라면 오른쪽에서 처음 나오는 공인 IP가 실제 접속 IP다.
    단, Streamlit Community Cloud 앞단 프록시 구성은 공개되어 있지 않아 이 가정이 맞는지 운영에서 확인해야 한다.
    """
    for part in reversed((forwarded_for or "").split(",")):
        ip = public_ip(part)
        if ip:
            return ip
    return public_ip(connection_ip)


def forwarded_chain_summary(forwarded_for) -> dict:
    """진단용: 헤더에 IP가 몇 개 있고 그중 공인 IP가 몇 개인지(값은 돌려주지 않는다)."""
    parts = [p.strip() for p in (forwarded_for or "").split(",") if p.strip()]
    return {"entries": len(parts), "public": sum(1 for p in parts if public_ip(p))}


def auth_request_settings(enabled: bool, publishable_key: str, secret_key: str, user_ip):
    """Auth 요청에 쓸 (키, 추가 헤더). 조건이 하나라도 빠지면 지금처럼 publishable 키만 쓴다."""
    if enabled and secret_key and user_ip:
        return secret_key, {"Sb-Forwarded-For": user_ip}
    return publishable_key, {}
