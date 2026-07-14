"""로그인 엔드포인트의 교차 출처(cross-site) POST 차단 — 로그인 CSRF 방어 (감사 F-10).

로그인 3개 엔드포인트는 CSRF 토큰 검사에서 면제돼 있다(토큰을 아직 못 받은 상태에서
로그인해야 하므로). 그래서 악성 페이지가 **공격자 자신의 자격증명으로** 피해자를 강제
로그인시킬 수 있다 — 피해자의 쿠키가 필요 없는 공격이라 SameSite=strict 로도 못 막고,
이후 피해자의 작업이 공격자 계정으로 기록된다.

방어: 브라우저는 교차 출처 POST 에 반드시 ``Origin`` 헤더를 붙인다. Origin 이 있는데
우리 호스트와 다르면 거부한다. 정상 로그인(같은 출처)은 Origin 이 자기 자신이라 통과하고,
Origin 이 아예 없는 요청(비브라우저 클라이언트·테스트)은 CSRF 대상이 아니므로 통과시킨다.

토큰 면제를 제거하는 대안(감사 원안)은 프론트 3곳 + 테스트 다수를 함께 고쳐야 하고,
토큰 부착이 한 곳이라도 어긋나면 **운영 로그인이 통째로 막힌다**. 같은 위협을 훨씬 낮은
위험으로 막을 수 있어 Origin 검사를 택했다.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# 토큰 면제 상태로 남는 로그인 경로들 (src/main.py 의 exempt_urls 와 짝을 이룬다)
LOGIN_PATHS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^/api/auth/management-login$"),
    re.compile(r"^/api/attendance/login$"),
    re.compile(r"^/api/blend/session/login$"),
)


def _host_of(value: str) -> str:
    """Origin/Host 문자열에서 host:port 만 뽑는다 (스킴 차이는 무시).

    운영은 Cloudflare Tunnel 뒤라 외부는 https, 내부는 http 로 들어올 수 있다 —
    스킴까지 비교하면 정상 로그인을 막을 수 있어 호스트만 본다.
    """
    if not value:
        return ""
    if "://" in value:
        return urlsplit(value).netloc.lower()
    return value.strip().lower()


def _trusted_origins() -> set[str]:
    """IRMS_TRUSTED_ORIGINS (쉼표 구분) 로 지정한 추가 허용 호스트.

    탈출구다. 리버스 프록시가 Host 헤더를 내부 주소로 바꿔 전달하면 Origin(공개 도메인)과
    Host 가 달라져 **정상 로그인이 막힌다**. Cloudflare Tunnel 예시 설정은
    httpHostHeader 로 공개 호스트명을 그대로 넘기므로 보통은 필요 없지만, 운영 config.yml
    은 저장소에 없어 확인이 안 된다 — 그때 코드 수정 없이 도메인을 넣어 복구할 수 있게 한다.
    """
    raw = os.environ.get("IRMS_TRUSTED_ORIGINS", "")
    return {_host_of(token) for token in raw.split(",") if token.strip()}


class LoginOriginMiddleware(BaseHTTPMiddleware):
    """로그인 POST 에 교차 출처 Origin 이 붙어 있으면 403."""

    def __init__(self, app, login_paths: Iterable[re.Pattern[str]] = LOGIN_PATHS) -> None:
        super().__init__(app)
        self._paths = tuple(login_paths)

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and any(p.match(request.url.path) for p in self._paths):
            origin = _host_of(request.headers.get("origin", ""))
            # Origin 이 없으면(비브라우저·일부 구형 클라이언트) 교차 출처 공격이 아니다.
            if origin:
                host = _host_of(request.headers.get("host", "")) or _host_of(
                    str(request.base_url)
                )
                if origin != host and origin not in _trusted_origins():
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "CROSS_ORIGIN_LOGIN_BLOCKED"},
                    )
        return await call_next(request)
