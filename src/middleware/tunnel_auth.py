"""터널(외부 인터넷)로 들어온 요청에만 책임자 로그인을 요구한다.

현장 화면은 일부러 무로그인이다 — 작업자가 이름만 넣고 계량에 들어가는 게 정책이고,
사내망 안에서는 그게 맞다. 문제는 cloudflared 가 앱 전체(`http://127.0.0.1:9000`)를
그대로 인터넷에 게시한다는 점이다. Cloudflare Access 를 앞단에 걸지 않으면 주소만
아는 사람이:

  * `GET /api/blend/records/export-all` 로 전 배합 기록 엑셀을 받고,
  * `GET /api/blend/records/dhr-zip` 으로 배합일지 전량을 받고,
  * `POST /api/workers` → `POST /api/blend/session/login` → `POST /api/blend/records`
    로 **가짜 생산기록을 넣을 수** 있다.

Access 를 거는 것이 정공법이고(코드 0줄), 이 미들웨어는 그게 걸리기 전까지 —
그리고 걸린 뒤에도 — 원본 쪽에서 한 겹 더 막는 안전망이다.

## 터널 경유를 어떻게 아는가

Cloudflare 엣지가 붙이는 `CF-Ray`/`CF-Connecting-IP` 헤더로 판별한다. 엣지는 클라이언트가
보낸 동명 헤더를 **덮어쓰므로**, 인터넷 쪽에서 이 헤더를 지워 사내망인 척할 수 없다.
반대로 사내망에서 이 헤더를 위조하면 더 엄격해질 뿐이라(로그인 요구) 위험이 없다.

원본이 127.0.0.1 로만 노출돼 있어 터널을 우회해 직접 닿을 방법이 없다는 점이 이 판별의
전제다. 원본 포트를 인터넷에 직접 열면 이 방어는 무의미해진다 — 열지 말 것.

## 무엇을 통과시키는가

로그인 자체와 그 화면을 그릴 정적 파일, 그리고 헬스체크만. 나머지는 전부 책임자
세션을 요구한다(HTML 은 로그인 화면으로, API 는 403). 사내망 요청은 이 미들웨어를
그대로 지나가므로 **현장 동작은 하나도 바뀌지 않는다**.

끄려면 `IRMS_TUNNEL_REQUIRE_LOGIN=0`. 터널을 안 쓰면 헤더가 없어 아무 영향이 없으므로
기본값은 켬이다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

# Cloudflare 엣지가 붙이는 헤더 — 하나라도 있으면 터널 경유로 본다.
_CLOUDFLARE_HEADERS = ("cf-ray", "cf-connecting-ip")

# 로그인 전에도 반드시 닿아야 하는 경로.
_ALWAYS_ALLOWED = (
    re.compile(r"^/static/"),
    re.compile(r"^/favicon\.ico$"),
    re.compile(r"^/health$"),
    re.compile(r"^/api/health$"),
    re.compile(r"^/management/login$"),
    re.compile(r"^/api/auth/management-login$"),
    re.compile(r"^/api/auth/logout$"),
    re.compile(r"^/api/auth/me$"),
)


def is_via_cloudflare(request: Request) -> bool:
    return any(h in request.headers for h in _CLOUDFLARE_HEADERS)


class TunnelAuthMiddleware(BaseHTTPMiddleware):
    """터널로 들어온 요청은 책임자 세션이 있어야 통과시킨다.

    SessionMiddleware 보다 **안쪽**에 놓여야 한다(= create_app 에서 가장 먼저
    add_middleware). 바깥에 놓으면 request.session 이 아직 없어 항상 미인증으로
    보이고, 로그인해도 계속 튕긴다.
    """

    def __init__(self, app, *, enabled: bool = True, allowed: Iterable[re.Pattern] = ()) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.allowed = (*_ALWAYS_ALLOWED, *allowed)

    def _is_allowed(self, path: str) -> bool:
        return any(p.match(path) for p in self.allowed)

    def _has_manager_session(self, request: Request) -> bool:
        # auth.get_current_user 를 직접 부르면 DB 조회 + 유휴 타이머 갱신까지 일어난다.
        # 여기서는 '로그인 흔적이 있는가'만 보고, 실제 권한 검사는 각 라우트의
        # require_access_level 이 그대로 수행한다(이 미들웨어는 문지기일 뿐 권한 판정자가 아니다).
        try:
            session = request.session
        except Exception:  # noqa: BLE001 — SessionMiddleware 바깥이면 세션이 없다
            return False
        return bool(session.get("mgr_worker_id") or session.get("user_id"))

    async def dispatch(self, request: Request, call_next):
        if not self.enabled or not is_via_cloudflare(request):
            return await call_next(request)

        path = request.url.path
        if self._is_allowed(path) or self._has_manager_session(request):
            return await call_next(request)

        if path.startswith("/api/"):
            return JSONResponse(
                {"detail": "외부 접속은 책임자 로그인이 필요합니다."},
                status_code=403,
            )
        # 화면 요청은 로그인으로 보내고, 로그인 후 원래 가려던 곳으로 돌려보낸다.
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(f"/management/login?next={target}", status_code=303)
