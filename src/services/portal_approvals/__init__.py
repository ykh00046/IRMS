"""포털 근태허가원 수집 — 클라이언트(requests) + 파서(순수 함수).

BRM 이 직접 포털에 로그인해 부서공개함의 근태허가원을 읽는다. 별도 수집기 앱이
BRM 운영 서버와 다른 PC 에서 돌아 파일·HTTP 로 넘길 길이 없었기 때문이다
(docs/attendance-approvals.md §1.2). 브라우저는 쓰지 않는다.

적재는 이 패키지가 하지 않는다 — 항목만 만들어 주고
``services/attendance_approvals.collect_from_portal`` 이 표에 넣는다.
"""

from . import client, collector, parser
from .client import (
    PortalClient,
    PortalError,
    PortalLoginFailed,
    PortalPermissionDenied,
)
from .collector import collect

__all__ = [
    "client",
    "collector",
    "parser",
    "PortalClient",
    "PortalError",
    "PortalLoginFailed",
    "PortalPermissionDenied",
    "collect",
]
