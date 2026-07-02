"""IRMS 저울 로컬 에이전트 — A&D 저울(GX-10202M 등)을 웹 화면에 연결.

현장 PC에서 실행하면 RS-232C(또는 USB-시리얼)로 저울을 읽고, 로컬 HTTP
(127.0.0.1:8787)로 현재 무게를 내어준다. 배합 화면(blend.js)이 이 주소를
호출해 실제량 칸을 자동으로 채운다. 브라우저 페이지는 서버(192.168.x)에
있어도 fetch 대상이 같은 PC 의 127.0.0.1 이므로 저울은 로컬에서 읽힌다.

- GET /health  → {"ok": true, "port": "COM3"}
- GET /weight  → {"stable": true, "value": 4775.7, "unit": "g", "header": "ST"}

A&D 표준 포맷: "ST,+0004775.7   g" (ST=안정, US=불안정, OL=과부하, QT=개수).
질의는 'Q' 명령(즉시 1건 응답). 공장 기본 통신값 2400bps/7bit/EVEN/1stop —
저울 설정(bASFnc)을 바꿨다면 config.json 에서 맞춘다.

설정: %APPDATA%\\IRMS-Scale\\config.json (첫 실행 시 자동 생성)
    {"port": null, "baudrate": 2400, "bytesize": 7, "parity": "E",
     "stopbits": 1, "http_port": 8787}
    port 가 null 이면 COM 포트를 훑어 응답하는 저울을 자동 탐지한다.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import serial  # pyserial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - 테스트 환경엔 pyserial 이 없어도 된다
    serial = None
    list_ports = None

APP_NAME = "IRMS-Scale"
DEFAULT_CONFIG = {
    "port": None,          # null = 자동 탐지
    "baudrate": 2400,      # A&D 공장 기본
    "bytesize": 7,
    "parity": "E",
    "stopbits": 1,
    "http_port": 8787,
}


# ── A&D 프레임 파서 (순수 함수 — 단위 테스트 대상) ────────────────
def parse_frame(raw: str | bytes) -> dict | None:
    """A&D 표준 포맷 한 줄을 해석. 해석 불가 시 None.

    예: "ST,+0004775.7   g" → {header: ST, stable: True, value: 4775.7, unit: g}
        "US,-0000012.3   g" → 불안정(측정 중)
        "OL,+9999999.9   g" → 과부하
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("ascii", errors="ignore")
        except Exception:  # noqa: BLE001
            return None
    text = raw.strip()
    if len(text) < 4 or text[2] != ",":
        return None
    header = text[:2].upper()
    if header not in ("ST", "US", "OL", "QT", "WT"):
        return None
    body = text[3:].strip()
    # 값과 단위 분리: "+0004775.7   g" / "4775.7g"
    unit = ""
    number = body
    for i in range(len(body) - 1, -1, -1):
        ch = body[i]
        if ch.isdigit() or ch in "+-.":
            number = body[: i + 1].strip()
            unit = body[i + 1:].strip()
            break
    try:
        value = float(number)
    except ValueError:
        return None
    return {
        "header": header,
        "stable": header in ("ST", "QT", "WT"),
        "overload": header == "OL",
        "value": value,
        "unit": unit or "g",
    }


# ── 설정 ─────────────────────────────────────────────────────────
def config_path() -> Path:
    base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    directory = Path(base) / APP_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "config.json"


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        return dict(DEFAULT_CONFIG)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in raw.items() if k in DEFAULT_CONFIG})
    return merged


# ── 저울 통신 ─────────────────────────────────────────────────────
class Scale:
    def __init__(self, config: dict) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._serial: "serial.Serial | None" = None
        self.port: str | None = None

    def _open(self, port: str) -> "serial.Serial":
        return serial.Serial(
            port=port,
            baudrate=int(self._config["baudrate"]),
            bytesize=int(self._config["bytesize"]),
            parity=str(self._config["parity"]),
            stopbits=int(self._config["stopbits"]),
            timeout=1.2,
            write_timeout=1.2,
        )

    def _query(self, ser: "serial.Serial") -> dict | None:
        ser.reset_input_buffer()
        ser.write(b"Q\r\n")
        line = ser.readline()
        return parse_frame(line) if line else None

    def connect(self) -> str | None:
        """설정 포트 또는 자동 탐지로 저울 연결. 성공 시 포트명."""
        if serial is None:
            return None
        candidates = (
            [self._config["port"]]
            if self._config["port"]
            else [p.device for p in list_ports.comports()]
        )
        for port in candidates:
            try:
                ser = self._open(port)
                if self._query(ser) is not None:
                    self._serial = ser
                    self.port = port
                    return port
                ser.close()
            except Exception:  # noqa: BLE001 - 다음 포트 시도
                continue
        return None

    def read(self) -> dict | None:
        """현재 무게 1건. 연결이 끊겼으면 재연결 시도."""
        with self._lock:
            if self._serial is None and self.connect() is None:
                return None
            try:
                return self._query(self._serial)
            except Exception:  # noqa: BLE001 - 케이블 분리 등 → 재연결 1회
                try:
                    self._serial.close()
                except Exception:  # noqa: BLE001
                    pass
                self._serial = None
                if self.connect() is None:
                    return None
                try:
                    return self._query(self._serial)
                except Exception:  # noqa: BLE001
                    return None


# ── 로컬 HTTP 서버 ────────────────────────────────────────────────
def build_handler(scale: Scale):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            # 웹 페이지(서버 origin)에서 127.0.0.1 로 호출하므로 CORS 허용 필요
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            # Chrome Private Network Access 프리플라이트 대응
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send(204, {})

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/health"):
                self._send(200, {"ok": scale.port is not None, "port": scale.port})
                return
            if self.path.startswith("/weight"):
                frame = scale.read()
                if frame is None:
                    self._send(503, {"error": "SCALE_NOT_CONNECTED", "port": scale.port})
                    return
                self._send(200, frame)
                return
            self._send(404, {"error": "NOT_FOUND"})

        def log_message(self, fmt, *args):  # 콘솔 소음 줄이기
            pass

    return Handler


def main() -> None:
    config = load_config()
    scale = Scale(config)
    port = scale.connect()
    print(f"[IRMS-Scale] 설정: {config_path()}")
    if port:
        print(f"[IRMS-Scale] 저울 연결됨: {port}")
    else:
        print("[IRMS-Scale] 저울을 찾지 못했습니다 — 케이블/전원 확인. (요청 시 재시도)")
    http_port = int(config["http_port"])
    server = ThreadingHTTPServer(("127.0.0.1", http_port), build_handler(scale))
    print(f"[IRMS-Scale] http://127.0.0.1:{http_port} 대기 중 (Ctrl+C 종료)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[IRMS-Scale] 종료")


if __name__ == "__main__":
    main()
