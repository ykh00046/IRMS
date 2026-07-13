"""저울 연동 서비스 — 통합 트레이 앱 안에서 켜고 끌 수 있는 로컬 저울 브릿지.

기존 ``scale_agent/agent.py`` 의 검증된 로직(프레임 파서·EventBus·Scale·HTTP 핸들러)을
그대로 재사용한다(코드 중복 방지). 배합 화면(blend.js)이 호출하는 로컬 HTTP 서버
``127.0.0.1:8787`` 를 스레드로 띄우고, 저울 끄기 시 포트·시리얼을 모두 반납한다.

저울 하드웨어 세부 설정(port/protocol/baud/scales[]/yield_to)은 종전대로
``%APPDATA%\\IRMS-Scale\\config.json`` 이 소유한다. 통합 앱의 토글은 이 서비스를
'시작할지 말지'만 결정한다.
"""

from __future__ import annotations

import logging
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

# scale_agent 는 저장소 루트의 형제 패키지다. 개발 실행(tray_client/run.py) 시
# 임포트되도록 루트를 경로에 추가한다. 프리즈(exe) 빌드에서는 spec 이 포함한다.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from scale_agent.agent import (
        EventBus,
        Scale,
        build_handler,
        load_config as load_scale_config,
        log_applied_config,
        scale_entries,
    )

    _SCALE_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # noqa: BLE001 - pyserial 미설치 등은 서비스 비활성으로 처리
    EventBus = Scale = build_handler = load_scale_config = scale_entries = None  # type: ignore
    log_applied_config = None  # type: ignore
    _SCALE_IMPORT_ERROR = exc


class ScaleService:
    """로컬 저울 HTTP 브릿지의 생명주기 관리(start/stop, 스레드 안전 아님 — 트레이 UI 스레드에서 호출)."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._scales: list = []

    @property
    def available(self) -> bool:
        return _SCALE_IMPORT_ERROR is None

    @property
    def running(self) -> bool:
        return self._server is not None

    def status_line(self) -> str:
        if not self.available:
            return "저울: 사용 불가(pyserial 미설치)"
        if not self.running:
            return "저울: 꺼짐"
        connected = [s for s in self._scales if getattr(s, "port", None)]
        if not self._scales:
            return "저울: 켜짐(설정 없음)"
        if not connected:
            return "저울: 켜짐(연결 안 됨)"
        names = ", ".join(f"{s.name}:{s.port}" for s in connected)
        return f"저울: {names}"

    def reconnect(self) -> None:
        """연결 안 된 저울을 다시 연결 시도(백그라운드)."""
        def _all() -> None:
            for s in self._scales:
                if getattr(s, "port", None) is None and not getattr(s, "yielding", False):
                    try:
                        s.connect()
                    except Exception as exc:  # noqa: BLE001
                        self._logger.warning("scale reconnect failed (%s): %s", s.name, exc)

        threading.Thread(target=_all, name="scale-reconnect", daemon=True).start()

    def start(self) -> bool:
        if self.running:
            return True
        if not self.available:
            self._logger.warning("scale service unavailable: %s", _SCALE_IMPORT_ERROR)
            return False
        try:
            config = load_scale_config()
            bus = EventBus()
            taken: set = set()
            entries = scale_entries(config)
            # 실제 적용된 설정(이름/프로토콜/포트/통신값)을 agent.log 에 남긴다 —
            # 설정 파일이 반영됐는지 현장에서 눈으로 확인하기 위한 진단.
            log_applied_config(entries)
            self._scales = [Scale(entry, bus, taken) for entry in entries]
            for s in self._scales:
                port = s.connect()
                self._logger.info(
                    "scale %s %s", s.name, f"connected: {port}" if port else "not found (retry on demand)"
                )
            http_port = int(config.get("http_port") or 8787)
            self._server = ThreadingHTTPServer(
                ("127.0.0.1", http_port), build_handler(self._scales, bus)
            )
            self._thread = threading.Thread(
                target=self._server.serve_forever, name="scale-http", daemon=True
            )
            self._thread.start()
            self._logger.info(
                "scale HTTP server on 127.0.0.1:%d (%d scale(s))", http_port, len(self._scales)
            )
            return True
        except Exception as exc:  # noqa: BLE001 - 포트 점유 등은 조용히 실패, 앱은 계속
            self._logger.warning("scale service start failed: %s", exc)
            self.stop()
            return False

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._server.server_close()
            except Exception:  # noqa: BLE001
                pass
            self._server = None
        self._thread = None
        for s in self._scales:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        self._scales = []
