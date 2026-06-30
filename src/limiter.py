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


limiter = Limiter(key_func=get_remote_address, enabled="pytest" not in sys.modules)
