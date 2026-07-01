"""IRMS 단일 창 실행 + 자동 업데이트 감시자.

한 콘솔에서 uvicorn 서버를 띄우고(서버 로그가 이 창에 그대로 출력됨), 주기적으로
origin/main 을 확인해 새 커밋이 있으면 DB 백업 → git pull → pip install → 서버
재시작한다. 창 하나로 서버 실행 + 자동 업데이트를 모두 처리한다.

실행:  .venv\\Scripts\\python.exe serve.py   (보통 run_auto.bat 이 대신 실행)
설정(환경변수):
    IRMS_PORT            서버 포트 (기본 9000)
    IRMS_AUTO_INTERVAL   업데이트 확인 주기(초, 기본 600 = 10분)
    IRMS_AUTO_UPDATE     0 이면 업데이트 감시 없이 서버만 실행

참고: serve.py 자체가 업데이트되면 재시작 후 반영된다(무한 로딩 방지를 위해
실행 중에는 옛 serve.py 로 계속 감시). 서버(src/*) 변경은 매 재시작마다 반영.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = os.environ.get("IRMS_PORT", "9000")
INTERVAL = max(30, int(os.environ.get("IRMS_AUTO_INTERVAL", "600")))
AUTO = os.environ.get("IRMS_AUTO_UPDATE", "1") != "0"

_VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON = str(_VENV_PY) if _VENV_PY.exists() else sys.executable


def log(message: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def _git(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=ROOT, text=True,
        capture_output=capture,
    )


def has_update() -> bool:
    """origin/main 이 로컬 HEAD 보다 앞서 있으면 True."""
    try:
        _git("fetch", "origin", "main")
        local = _git("rev-parse", "HEAD", capture=True).stdout.strip()
        remote = _git("rev-parse", "origin/main", capture=True).stdout.strip()
        return bool(local) and bool(remote) and local != remote
    except Exception as exc:  # noqa: BLE001
        log(f"업데이트 확인 실패(무시): {exc}")
        return False


def backup_db() -> None:
    db = ROOT / "data" / "irms.db"
    if not db.exists():
        return
    backups = ROOT / "backups"
    backups.mkdir(exist_ok=True)
    dest = backups / f"irms_{datetime.now():%Y%m%d_%H%M%S}.db"
    try:
        shutil.copy2(db, dest)
        log(f"DB 백업: {dest.name}")
    except Exception as exc:  # noqa: BLE001
        log(f"DB 백업 실패(계속 진행): {exc}")


def apply_update() -> None:
    log("새 업데이트 발견 → 반영 중 (DB 백업 → git pull → pip install)...")
    backup_db()
    _git("pull", "origin", "main")
    subprocess.run(
        [PYTHON, "-m", "pip", "install", "-r", "requirements.txt", "--quiet"],
        cwd=ROOT,
    )
    log("업데이트 반영 완료.")


def start_server() -> subprocess.Popen:
    log(f"서버 시작 (http://0.0.0.0:{PORT}) — 아래는 서버 로그입니다.")
    # stdout/stderr 를 상속 → uvicorn 로그가 이 콘솔에 그대로 출력(창 1개).
    return subprocess.Popen(
        [PYTHON, "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", str(PORT)],
        cwd=ROOT,
    )


def stop_server(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> None:
    log(
        f"IRMS 실행 (자동 업데이트 {'ON' if AUTO else 'OFF'}, "
        f"{INTERVAL}초 주기, 포트 {PORT}). 종료: Ctrl+C"
    )
    proc = start_server()
    try:
        while True:
            time.sleep(INTERVAL)
            if proc.poll() is not None:
                log("서버가 종료되어 다시 시작합니다.")
                proc = start_server()
                continue
            if AUTO and has_update():
                stop_server(proc)
                apply_update()
                proc = start_server()
    except KeyboardInterrupt:
        log("종료 요청 — 서버 정리 중...")
    finally:
        stop_server(proc)
        log("종료되었습니다.")


if __name__ == "__main__":
    main()
