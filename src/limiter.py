"""앱 전역 Rate Limiter 인스턴스.

slowapi의 ``Limiter`` 는 FastAPI 앱당 단일 인스턴스로 ``app.state.limiter`` 에
등록되어야 데코레이터가 정상 작동한다. 라우터별로 새 ``Limiter`` 를 만들면
요청 객체에서 등록된 limiter 를 찾지 못해 제한이 적용되지 않으므로, 모든
라우터는 이 모듈에서 정의한 단일 인스턴스를 임포트해서 사용한다.
"""

from __future__ import annotations

import sys

from slowapi import Limiter
from slowapi.util import get_remote_address


_LOOPBACK = ("127.0.0.1", "::1")


def client_key(request) -> str:
    """요청자 키 — 터널 경유면 Cloudflare 가 넘겨준 실제 접속 IP.

    cloudflared 는 같은 PC 에서 127.0.0.1 로 붙으므로 그대로 두면 외부 요청 전부가 한
    버킷을 나눠 써, 한 사람이 로그인을 5번 틀리면 다른 외부 책임자까지 1분간 막혔다.
    CF 헤더는 직접 연결이 루프백일 때만 믿는다 — 사내망 PC 가 헤더를 꾸며 제한을
    피하지 못하게.
    """
    direct = get_remote_address(request)
    if direct in _LOOPBACK and request.headers.get("cf-ray"):
        forwarded = (request.headers.get("cf-connecting-ip") or "").strip()
        if forwarded:
            return forwarded
    return direct


limiter = Limiter(key_func=client_key, enabled="pytest" not in sys.modules)
