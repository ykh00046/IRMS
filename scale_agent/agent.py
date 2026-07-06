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
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Windows 콘솔(cp949)에서 특수문자로 죽지 않도록 출력 인코딩 방어
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass

try:
    import serial  # pyserial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - 테스트 환경엔 pyserial 이 없어도 된다
    serial = None
    list_ports = None

APP_NAME = "IRMS-Scale"
DEFAULT_CONFIG = {
    "port": None,          # null = 자동 탐지
    "protocol": "and",     # "and"(A&D GX 등) | "mt-sics"(Mettler XP 등)
    "baudrate": None,      # null = 프로토콜 기본값 사용
    "bytesize": None,
    "parity": None,
    "stopbits": None,
    "http_port": 8787,
    # 이 프로세스들이 실행 중이면 포트를 양보(자동 해제), 종료되면 자동 재연결.
    # 예: ["LabX.exe", "BalanceLink.exe"] — 같은 저울을 쓰는 기존 프로그램 exe 이름.
    "yield_to": [],
}

# 프로토콜별 통신 기본값 + 질의 명령 (config 에서 개별 항목을 지정하면 그 값이 우선)
PROTOCOL_PRESETS = {
    "and": {"baudrate": 2400, "bytesize": 7, "parity": "E", "stopbits": 1, "query": b"Q\r\n"},
    "mt-sics": {"baudrate": 9600, "bytesize": 8, "parity": "N", "stopbits": 1, "query": b"SI\r\n"},
}


def resolve_comm(config: dict) -> dict:
    """프로토콜 프리셋 + config 오버라이드를 합친 실제 통신 파라미터."""
    preset = PROTOCOL_PRESETS.get(str(config.get("protocol") or "and"), PROTOCOL_PRESETS["and"])
    return {
        "baudrate": config.get("baudrate") or preset["baudrate"],
        "bytesize": config.get("bytesize") or preset["bytesize"],
        "parity": config.get("parity") or preset["parity"],
        "stopbits": config.get("stopbits") or preset["stopbits"],
        "query": preset["query"],
    }


# ── 프레임 파서 (순수 함수 — 단위 테스트 대상) ────────────────────
def _parse_and(text: str) -> dict | None:
    """A&D 표준 포맷: "ST,+0004775.7   g" (ST=안정, US=불안정, OL=과부하)."""
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


def _parse_sics(text: str) -> dict | None:
    """Mettler MT-SICS 포맷: "S S     105.00 g" (S S=안정, S D=동적/불안정,
    S +/- = 과부하/부족, S I = 명령 처리 불가 — 무시).

    PRINT(전송) 키의 인쇄 템플릿 출력도 허용:
      "N      105.00 g" (순중량 줄) / "105.00 g" (값+단위만)
    """
    tokens = text.split()
    if not tokens:
        return None
    if tokens[0] == "S":
        if len(tokens) >= 2 and tokens[1] in ("+", "-"):
            return {"header": "OL", "stable": False, "overload": True, "value": 0.0, "unit": "g"}
        if len(tokens) >= 3 and tokens[1] in ("S", "D"):
            try:
                value = float(tokens[2])
            except ValueError:
                return None
            return {
                "header": "ST" if tokens[1] == "S" else "US",
                "stable": tokens[1] == "S",
                "overload": False,
                "value": value,
                "unit": tokens[3] if len(tokens) > 3 else "g",
            }
        return None
    # 인쇄 템플릿(수신 전용 모드): 마지막이 단위, 그 앞이 값, 나머지 앞부분은
    # 순번/N(순중량) 표기만 허용. 실측 예(XP10002S): " 1    N    -4544.27 g"
    # G(총중량)/T(용기) 줄은 오탐 방지를 위해 계속 무시.
    units = ("g", "kg", "mg")
    if len(tokens) < 2 or tokens[-1] not in units:
        return None
    prefix = tokens[:-2]
    if not all(t == "N" or t.isdigit() for t in prefix):
        return None
    try:
        value = float(tokens[-2])
    except ValueError:
        return None
    unit = tokens[-1]
    if unit == "kg":
        value, unit = value * 1000, "g"
    elif unit == "mg":
        value, unit = value / 1000, "g"
    return {"header": "ST", "stable": True, "overload": False, "value": value, "unit": unit}


def parse_frame(raw: str | bytes, protocol: str = "and") -> dict | None:
    """저울 한 줄 응답 해석. 해석 불가 시 None."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("ascii", errors="ignore")
        except Exception:  # noqa: BLE001
            return None
    text = raw.strip()
    if not text:
        return None
    if protocol == "mt-sics":
        return _parse_sics(text)
    return _parse_and(text)


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
    known = set(DEFAULT_CONFIG) | {"scales"}
    merged.update({k: v for k, v in raw.items() if k in known})
    return merged


def scale_entries(config: dict) -> list[dict]:
    """설정에서 저울 목록을 추출. 여러 대를 동시에 연결할 수 있다.

    - "scales": [{...}, {...}] 형식이면 그대로 (저울별 name/protocol/port/yield_to)
    - 없으면 구 단일 설정(평평한 키)을 저울 1대로 해석 (하위 호환)
    """
    entries = config.get("scales")
    if isinstance(entries, list) and entries:
        out = []
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                continue
            entry = dict(e)
            entry.setdefault("name", entry.get("protocol") or f"저울{i + 1}")
            out.append(entry)
        if out:
            return out
    single = {k: config.get(k) for k in ("port", "protocol", "baudrate", "bytesize", "parity", "stopbits", "yield_to", "passive")}
    single["name"] = str(config.get("protocol") or "저울1")
    return [single]


# ── 이벤트 버스: 여러 저울의 PRINT 푸시를 하나의 스트림으로 ──────
class EventBus:
    # 같은 저울이 같은 값을 이 시간(초) 안에 다시 보내면 중복 전송으로 보고 무시
    # (일부 설정은 PRINT 1회에 같은 줄을 두 번 내보내거나 짧게 반복 출력함)
    DEDUPE_SECONDS = 2.0

    def __init__(self, clock=time.time) -> None:
        self._events: deque = deque(maxlen=100)
        self._seq = 0
        self._lock = threading.Lock()
        self._clock = clock
        self._last: tuple[str, float, float] | None = None  # (source, value, at)

    def push(self, frame: dict, source: str) -> None:
        with self._lock:
            now = self._clock()
            if self._last is not None:
                last_source, last_value, last_at = self._last
                if (
                    last_source == source
                    and last_value == frame.get("value")
                    and now - last_at < self.DEDUPE_SECONDS
                ):
                    self._last = (source, last_value, now)
                    return
            self._last = (source, float(frame.get("value") or 0.0), now)
            self._seq += 1
            self._events.append({**frame, "id": self._seq, "source": source})

    def after(self, after_id: int) -> tuple[list[dict], int]:
        with self._lock:
            return [e for e in self._events if e["id"] > after_id], self._seq


# ── 저울 통신 ─────────────────────────────────────────────────────
# 상시 수신 구조: 저울마다 리더 스레드가 포트를 계속 읽는다. 프레임은
# 두 갈래 — (a) 질의(/weight) 응답, (b) 저울 PRINT 키 푸시. 푸시된
# 안정값은 공용 EventBus 에 쌓이고, 배합 화면이 /events 를 폴링해
# 실제량 칸에 자동 입력한다. 여러 저울(A&D + Mettler 등)을 동시에
# 연결할 수 있고, 어느 저울에서 PRINT 를 눌러도 같은 흐름으로 들어간다.
class Scale:
    def __init__(self, entry: dict, bus: EventBus, taken_ports: set) -> None:
        self._config = entry
        self._bus = bus
        self._taken = taken_ports  # 다른 저울이 점유한 포트(자동탐지 중복 방지)
        self._comm = resolve_comm(entry)
        self._protocol = str(entry.get("protocol") or "and")
        self.name = str(entry.get("name") or self._protocol)
        self._write_lock = threading.Lock()
        self._serial = None  # serial.Serial | None
        self.port: str | None = None
        # 기존 프로그램(yield_to)에 포트를 양보 중인가
        self.yielding = False
        # 최초 연결 실패 시 1회 수신 감청(포맷 판독용) 수행 여부
        self._sniffed = False
        # 리더가 해석 못 한 수신 라인 로깅 횟수(세션당 상한)
        self._unparsed_logged = 0
        # 질의 대기자
        self._expect_q = False
        self._q_result: dict | None = None
        self._q_waiter = threading.Event()
        self._stop = threading.Event()
        self._reader = threading.Thread(
            target=self._reader_loop, name=f"scale-reader-{self.name}", daemon=True
        )
        self._reader.start()
        if entry.get("yield_to"):
            threading.Thread(
                target=self._yield_watcher, name=f"scale-yield-{self.name}", daemon=True
            ).start()

    def _open(self, port: str):
        return serial.Serial(
            port=port,
            baudrate=int(self._comm["baudrate"]),
            bytesize=int(self._comm["bytesize"]),
            parity=str(self._comm["parity"]),
            stopbits=int(self._comm["stopbits"]),
            timeout=0.5,
            write_timeout=1.2,
        )

    def _comm_candidates(self) -> list[tuple[int, int, str]]:
        """시도할 (속도, 데이터비트, 패리티) 조합. config 에 명시한 항목은 고정,
        나머지는 프리셋 우선 + 흔한 값들을 자동 시도(저울 쪽 설정 상이 대비)."""
        if self._config.get("baudrate"):
            bauds = [int(self._config["baudrate"])]
        else:
            preset = int(self._comm["baudrate"])
            common = [9600, 19200, 4800, 2400, 38400]
            bauds = [preset] + [b for b in common if b != preset]
        if self._config.get("bytesize") or self._config.get("parity"):
            frames = [(int(self._comm["bytesize"]), str(self._comm["parity"]))]
        else:
            preset_frame = (int(self._comm["bytesize"]), str(self._comm["parity"]))
            frames = [preset_frame] + [
                f for f in [(8, "N"), (7, "E"), (8, "E")] if f != preset_frame
            ]
        return [(b, bits, par) for b in bauds for (bits, par) in frames]

    def _probe(
        self, port: str, baudrate: int, bytesize: int, parity: str,
        raw_sink: list | None = None,
    ) -> "serial.Serial | None":
        """지정 조합으로 열고 질의 → 유효 프레임이 오면 연결 유지, 아니면 닫음.

        raw_sink 를 주면 해석 실패한 수신 원본(bytes)을 모아준다(포맷 판독용).
        """
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=bytesize,
            parity=parity,
            stopbits=int(self._comm["stopbits"]),
            timeout=0.5,
            write_timeout=1.2,
        )
        try:
            # USB-시리얼 어댑터는 포트를 여는 순간 잡음 바이트가 낄 수 있어
            # (저울에 '?SI' 처럼 도착 → MT-SICS 'ES' 오류 응답) 안정화 후
            # 빈 줄로 저울 쪽 명령 버퍼를 비우고 질의를 최대 3회 재시도한다.
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write(b"\r\n")
            time.sleep(0.15)
            ser.reset_input_buffer()
            # 질의 3회: 표준(CRLF) 2회 + CR 단독 종결 1회(터미네이터 상이 대비)
            query = self._comm["query"]
            attempts = [query, query, query.replace(b"\r\n", b"\r")]
            for attempt_query in attempts:
                ser.write(attempt_query)
                deadline = time.time() + 1.2
                while time.time() < deadline:
                    line = ser.readline()
                    if not line:
                        continue
                    if parse_frame(line, self._protocol) is not None:
                        return ser
                    if raw_sink is not None and len(raw_sink) < 8:
                        raw_sink.append((f"{baudrate}/{bytesize}{parity}", bytes(line)))
                    if line.strip().upper() in (b"ES", b"EL", b"ET"):
                        break  # 저울이 응답함(명령만 오염) → 같은 조합에서 재질의
        except Exception:  # noqa: BLE001
            pass
        try:
            ser.close()
        except Exception:  # noqa: BLE001
            pass
        return None

    def _sniff(self, port: str, seconds: float = 10.0) -> bytes:
        """수신 전용 감청 — 질의 없이 저울이 스스로 보내는 데이터를 모은다.

        (연속 전송 모드이거나, 사용자가 이 사이 PRINT 를 누르면 잡힌다.)
        프리셋 통신값으로 연다. 최초 연결 실패 시 1회만 수행.
        """
        try:
            ser = serial.Serial(
                port=port,
                baudrate=int(self._comm["baudrate"]),
                bytesize=int(self._comm["bytesize"]),
                parity=str(self._comm["parity"]),
                stopbits=int(self._comm["stopbits"]),
                timeout=1.0,
            )
        except Exception:  # noqa: BLE001
            return b""
        chunks: list[bytes] = []
        deadline = time.time() + seconds
        try:
            while time.time() < deadline and sum(len(c) for c in chunks) < 300:
                data = ser.read(64)
                if data:
                    chunks.append(data)
        except Exception:  # noqa: BLE001
            pass
        try:
            ser.close()
        except Exception:  # noqa: BLE001
            pass
        return b"".join(chunks)

    def connect(self) -> str | None:
        """설정 포트 또는 자동 탐지로 저울 연결. 성공 시 포트명.

        탐지(probe)는 self._serial 배정 전에 로컬 객체로 직접 읽으므로
        리더 스레드와 충돌하지 않는다. 양보 중에는 연결하지 않는다.
        실패 원인(포트 점유 vs 응답 없음)을 agent.log 에 구분해 남긴다.
        """
        if serial is None or self.yielding:
            return None
        fixed_port = bool(self._config.get("port"))
        # 수신 전용(passive): 질의 없이 포트만 열고 PRINT 푸시를 기다린다.
        # 질의(SI)를 거부(ES)하는 저울도 PRINT 출력은 정상 수신 가능.
        if self._config.get("passive"):
            if not fixed_port:
                log(f"[{self.name}] passive 모드는 port 를 고정 지정해야 합니다.")
                return None
            port = self._config["port"]
            if port in self._taken:
                return None
            try:
                ser = serial.Serial(
                    port=port,
                    baudrate=int(self._comm["baudrate"]),
                    bytesize=int(self._comm["bytesize"]),
                    parity=str(self._comm["parity"]),
                    stopbits=int(self._comm["stopbits"]),
                    timeout=0.5,
                )
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if "denied" in msg.lower() or "액세스" in msg:
                    log(f"[{self.name}] {port} 열기 실패 — 다른 프로그램이 포트 사용 중입니다.")
                else:
                    log(f"[{self.name}] {port} 열기 실패: {msg[:120]}")
                return None
            self._serial = ser
            self.port = port
            self._taken.add(port)
            log(f"[{self.name}] {port} 수신 전용으로 연결 — 저울에서 PRINT(전송) 키를 누르면 입력됩니다.")
            return port
        candidates = (
            [self._config["port"]]
            if fixed_port
            else [p.device for p in list_ports.comports()]
        )
        # 다른 저울이 이미 잡은 포트는 건너뜀(자동 탐지 충돌 방지)
        candidates = [p for p in candidates if p not in self._taken]
        combos = self._comm_candidates()
        for port in candidates:
            tried: list[str] = []
            raw_sink: list = []
            for baud, bits, parity in combos:
                try:
                    ser = self._probe(port, baud, bits, parity, raw_sink)
                except Exception as exc:  # noqa: BLE001 - 포트 자체를 못 연 경우
                    msg = str(exc)
                    if "denied" in msg.lower() or "액세스" in msg or "PermissionError" in msg:
                        log(f"[{self.name}] {port} 열기 실패 — 다른 프로그램이 포트 사용 중입니다.")
                    elif fixed_port:
                        log(f"[{self.name}] {port} 열기 실패: {msg[:120]}")
                    break  # 이 포트는 못 여니 다른 조합 시도 무의미
                if ser is not None:
                    if (baud, bits, parity) != combos[0]:
                        log(f"[{self.name}] {port} {baud}bps/{bits}bit/{parity} 로 연결됨 — config 에 "
                            f"\"baudrate\": {baud}, \"bytesize\": {bits}, \"parity\": \"{parity}\" "
                            "를 넣어두면 다음부터 바로 붙습니다.")
                    self._serial = ser
                    self.port = port
                    self._taken.add(port)
                    return port
                tried.append(f"{baud}/{bits}{parity}")
            if tried and fixed_port:
                if raw_sink:
                    # 수신은 있었으나 해석 불가 → 원본을 로그에 남겨 포맷 판독 근거로.
                    samples = " | ".join(f"({c}) {d!r}" for c, d in raw_sink[:5])
                    log(f"[{self.name}] {port} 수신 데이터가 있으나 해석하지 못했습니다. "
                        f"원본 샘플: {samples} — 이 로그를 개발자에게 전달하세요.")
                elif not self._sniffed:
                    self._sniffed = True
                    log(f"[{self.name}] {port} 응답 없음 → 10초간 수신 감청을 시작합니다. "
                        "지금 저울의 PRINT(전송) 키를 몇 번 눌러 보세요...")
                    raw = self._sniff(port)
                    if raw:
                        log(f"[{self.name}] 감청 수신: {raw[:200]!r} — 이 로그를 개발자에게 전달하세요.")
                    else:
                        log(f"[{self.name}] 감청에도 수신 없음 — 저울이 이 포트로 아무것도 보내지 않습니다. "
                            "저울 설정에서 주변기기=Host 인지 확인하세요. (시도: "
                            + ", ".join(tried) + ")")
                else:
                    log(f"[{self.name}] {port} 열림, 그러나 응답 없음 (시도: {', '.join(tried)}).")
        return None

    # ── 기존 프로그램 공존: 프로세스 감지 → 포트 자동 양보/복귀 ──
    def _yield_watcher(self) -> None:
        names = [str(n).lower() for n in (self._config.get("yield_to") or []) if str(n).strip()]
        if not names:
            return
        import subprocess
        while not self._stop.is_set():
            try:
                out = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                ).stdout.lower()
                running = any(name in out for name in names)
            except Exception:  # noqa: BLE001
                running = False
            if running and not self.yielding:
                self.yielding = True
                self._drop_connection()
                log(f"[{self.name}] 기존 저울 프로그램 감지 → 포트 양보(연결 해제)")
            elif not running and self.yielding:
                self.yielding = False
                port = self.connect()
                log(f"[{self.name}] 기존 프로그램 종료 → 재연결{'됨: ' + port if port else ' 시도(저울 응답 없음)'}")
            # 1초 주기: 기존 프로그램이 켜지자마자 포트를 열기 전에 양보가 끝나도록.
            time.sleep(1)

    def _drop_connection(self) -> None:
        ser, self._serial = self._serial, None
        if self.port:
            self._taken.discard(self.port)
        self.port = None
        if ser is not None:
            try:
                ser.close()
            except Exception:  # noqa: BLE001
                pass

    def _handle_frame(self, frame: dict) -> None:
        """수신 프레임 분배 — 질의 응답이면 대기자에게, 아니면 PRINT 푸시 이벤트로."""
        if self._expect_q:
            self._expect_q = False
            self._q_result = frame
            self._q_waiter.set()
            return
        if frame.get("stable") and not frame.get("overload"):
            self._bus.push(frame, self.name)

    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            ser = self._serial
            if ser is None:
                time.sleep(2)
                continue
            try:
                line = ser.readline()
            except Exception:  # noqa: BLE001 - 케이블 분리 등
                self._drop_connection()
                continue
            if not line:
                continue
            frame = parse_frame(line, self._protocol)
            if frame:
                self._handle_frame(frame)
            elif self._unparsed_logged < 6 and line.strip():
                # 해석 못 한 수신(인쇄 템플릿 등) 원본을 남겨 파서 보강 근거로.
                self._unparsed_logged += 1
                log(f"[{self.name}] 수신(해석 불가): {bytes(line)!r}")

    def close(self) -> None:
        """리더/양보 스레드를 멈추고 포트를 반납한다(통합 앱에서 저울 끄기 시 사용)."""
        self._stop.set()
        self._drop_connection()

    def read(self) -> dict | None:
        """현재 무게 1건(질의) — 진단용(/weight)."""
        if self.yielding:
            return None
        if self._serial is None and self.connect() is None:
            return None
        with self._write_lock:
            self._q_waiter.clear()
            self._q_result = None
            self._expect_q = True
            try:
                self._serial.write(self._comm["query"])
            except Exception:  # noqa: BLE001
                self._expect_q = False
                self._drop_connection()
                return None
        if self._q_waiter.wait(timeout=2.0):
            return self._q_result
        self._expect_q = False
        return None


# ── 로컬 HTTP 서버 ────────────────────────────────────────────────
def build_handler(scales: list, bus: EventBus):
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
                self._send(200, {
                    "ok": any(s.port is not None for s in scales),
                    "scales": [
                        {"name": s.name, "port": s.port, "yielding": s.yielding}
                        for s in scales
                    ],
                })
                return
            if self.path.startswith("/weight"):
                # 진단용: 연결된 저울들에 차례로 질의해 첫 응답 반환.
                for s in scales:
                    if s.port is None:
                        continue
                    frame = s.read()
                    if frame is not None:
                        self._send(200, {**frame, "source": s.name})
                        return
                self._send(503, {"error": "SCALE_NOT_CONNECTED"})
                return
            if self.path.startswith("/events"):
                # 저울 PRINT 키 푸시 이벤트(모든 저울 공용). ?after=<id> 이후만.
                from urllib.parse import parse_qs, urlparse
                params = parse_qs(urlparse(self.path).query)
                try:
                    after = int(params.get("after", ["0"])[0])
                except ValueError:
                    after = 0
                items, last_id = bus.after(after)
                self._send(200, {"last_id": last_id, "items": items})
                return
            self._send(404, {"error": "NOT_FOUND"})

        def log_message(self, fmt, *args):  # 콘솔 소음 줄이기
            pass

    return Handler


def log(message: str) -> None:
    """파일 로그(+가능하면 콘솔). 트레이(창 없는) 모드에선 파일이 유일한 기록."""
    line = f"{__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S} {message}"
    try:
        with open(config_path().parent / "agent.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001 - 창 없는 exe 에선 stdout 이 없을 수 있음
        pass


# ── Windows 부팅 시 자동 실행 (HKCU Run 레지스트리) ────────────────
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "IRMS-Scale"


def _autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{Path(__file__).resolve()}"'


def autostart_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_NAME)
        return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except OSError:
                pass
    log(f"부팅 시 자동 실행: {'켜짐' if enabled else '꺼짐'}")


# ── 트레이 아이콘 (pystray) — 콘솔 없이 작업표시줄 상주 ────────────
def _tray_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, 60, 60], radius=12, fill=(27, 64, 121, 255))  # 브랜드 네이비
    d.rectangle([16, 40, 48, 46], fill=(255, 255, 255, 255))                 # 저울 받침
    d.polygon([(32, 16), (20, 40), (44, 40)], fill=(244, 124, 38, 255))      # 저울 접시(오렌지)
    return img


def run_tray(scales: list, server: ThreadingHTTPServer) -> None:
    import pystray
    from pystray import Menu, MenuItem

    def status_text(scale):
        def _text(_item) -> str:
            if scale.yielding:
                return f"{scale.name}: 기존 프로그램에 양보 중"
            return f"{scale.name}: {scale.port} 연결됨" if scale.port else f"{scale.name}: 연결 안 됨"
        return _text

    def reconnect(_icon, _item) -> None:
        def _all():
            for s in scales:
                if s.port is None and not s.yielding:
                    s.connect()
        threading.Thread(target=_all, daemon=True).start()

    def toggle_autostart(icon, _item) -> None:
        set_autostart(not autostart_enabled())
        icon.update_menu()

    def open_folder(_icon, _item) -> None:
        os.startfile(str(config_path().parent))  # noqa: S606

    def quit_app(icon, _item) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()
        icon.stop()

    status_items = [MenuItem(status_text(s), None, enabled=False) for s in scales]
    icon = pystray.Icon(
        "irms_scale",
        icon=_tray_image(),
        title="IRMS 저울 에이전트",
        menu=Menu(
            *status_items,
            MenuItem("다시 연결", reconnect),
            Menu.SEPARATOR,
            MenuItem("부팅 시 자동 실행", toggle_autostart,
                     checked=lambda _item: autostart_enabled()),
            MenuItem("로그·설정 폴더 열기", open_folder),
            Menu.SEPARATOR,
            MenuItem("종료", quit_app),
        ),
    )
    log("트레이 모드 시작 (작업표시줄 아이콘)")
    icon.run()


def main() -> None:
    config = load_config()
    log(f"설정: {config_path()}")
    bus = EventBus()
    taken_ports: set = set()
    scales = [Scale(entry, bus, taken_ports) for entry in scale_entries(config)]
    for s in scales:
        port = s.connect()
        log(f"[{s.name}] 연결됨: {port}" if port else f"[{s.name}] 저울을 찾지 못했습니다. 케이블/전원 확인. (요청 시 재시도)")

    http_port = int(config["http_port"])
    server = ThreadingHTTPServer(("127.0.0.1", http_port), build_handler(scales, bus))
    log(f"http://127.0.0.1:{http_port} 대기 중 (저울 {len(scales)}대 설정)")

    # 최초 실행 시 부팅 자동 실행을 기본으로 켠다(트레이 메뉴에서 끌 수 있음).
    try:
        if not autostart_enabled():
            set_autostart(True)
    except Exception:  # noqa: BLE001 - 레지스트리 접근 불가 환경은 무시
        pass

    # 트레이 모드(기본): 콘솔 없이 상주. pystray 가 없거나 --console 이면 콘솔 모드.
    tray_available = False
    if "--console" not in sys.argv:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
            tray_available = True
        except ImportError:
            log("pystray/Pillow 미설치 - 콘솔 모드로 동작")

    if tray_available:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        run_tray(scales, server)
        log("종료")
        return
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("종료")


if __name__ == "__main__":
    main()
