"""IRMS 단일 창 실행 + 자동 업데이트 감시자.

한 콘솔에서 uvicorn 서버를 띄우고(서버 로그가 이 창에 그대로 출력됨), 주기적으로
origin/main 을 확인해 새 커밋이 있으면 DB 백업 → git pull → pip install → 서버
재시작한다. 창 하나로 서버 실행 + 자동 업데이트를 모두 처리한다.

실행:  .venv\\Scripts\\python.exe serve.py   (보통 run_auto.bat 이 대신 실행)
설정(환경변수):
    IRMS_PORT              서버 포트 (기본 9000)
    IRMS_AUTO_INTERVAL     업데이트 확인 주기(초, 기본 600 = 10분)
    IRMS_AUTO_UPDATE       0 이면 업데이트 감시 없이 서버만 실행
    IRMS_BACKUP_KEEP_DAYS  백업 보존 일수 (기본 30 — 오래된 백업 자동 삭제, 최근 5개는 항상 보존)
    IRMS_BACKUP_MIRROR     백업 2차 사본 폴더 (예: D:\\irms-backup — 미설정 시 로컬 backups/ 만)

백업: 업데이트 반영 직전 + 매일 1회(감시 루프) 자동 백업. SQLite 온라인 백업
API 사용(서버 가동 중에도 일관된 사본). 복구: 서버 중지 → backups/ 의 원하는
irms_*.db 를 data/irms.db 로 복사 → 서버 시작.

참고: serve.py 자체가 업데이트되면 재시작 후 반영된다(무한 로딩 방지를 위해
실행 중에는 옛 serve.py 로 계속 감시). 서버(src/*) 변경은 매 재시작마다 반영.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKUPS_DIR = ROOT / "backups"
PORT = os.environ.get("IRMS_PORT", "9000")
INTERVAL = max(30, int(os.environ.get("IRMS_AUTO_INTERVAL", "600")))
AUTO = os.environ.get("IRMS_AUTO_UPDATE", "1") != "0"
BACKUP_KEEP_DAYS = max(1, int(os.environ.get("IRMS_BACKUP_KEEP_DAYS", "30")))
BACKUP_KEEP_MIN = 5  # 보존일수와 무관하게 항상 남길 최근 백업 수
BACKUP_MIRROR = os.environ.get("IRMS_BACKUP_MIRROR", "").strip()

_VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON = str(_VENV_PY) if _VENV_PY.exists() else sys.executable


def _requirements_file() -> str:
    """운영은 검증된 고정 버전(requirements-lock.txt) 우선 — 무통제 업그레이드 방지.

    lock 갱신은 개발 PC에서: pip install -r requirements.txt 로 올린 뒤 전체
    테스트/smoke 통과 확인 → pip freeze > requirements-lock.txt 커밋.
    """
    lock = ROOT / "requirements-lock.txt"
    return "requirements-lock.txt" if lock.exists() else "requirements.txt"


# 출력이 파일/파이프로 리다이렉트되면 콘솔 코드페이지(cp949)로 떨어져
# 특수문자(—, · 등)에서 UnicodeEncodeError 로 죽을 수 있다 — UTF-8 로 고정.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def log(message: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def set_console_title(text: str) -> None:
    """콘솔 창 제목 — .bat 의 title 명령 대신 여기서 설정한다.

    cmd 는 .bat 파일 바이트를 OEM 코드페이지(cp949)로 읽으므로 UTF-8 로 저장된
    한글 title/echo 가 깨진다. 파이썬은 SetConsoleTitleW·WriteConsoleW 로 유니코드를
    콘솔 API 에 직접 넘겨 코드페이지와 무관하게 정상 표시된다(실측 확인).
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(text)
    except Exception as exc:  # noqa: BLE001 — 제목은 부가 기능, 실패해도 서버는 뜬다
        log(f"콘솔 제목 설정 실패(무시): {exc}")


def _git(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=ROOT, text=True,
        capture_output=capture,
    )


def ensure_runtime() -> str:
    if not _VENV_PY.exists():
        log("가상환경이 없어 생성하고 requirements.txt 를 설치합니다...")
        subprocess.run([sys.executable, "tools/bootstrap_irms.py"], cwd=ROOT, check=True)
    else:
        req = _requirements_file()
        log(f"가상환경 의존성 확인 중... ({req})")
        subprocess.run(
            [str(_VENV_PY), "-m", "pip", "install", "-r", req, "--quiet"],
            cwd=ROOT,
            check=True,
        )
    return str(_VENV_PY)


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


def _db_path() -> Path:
    """운영 DB 경로 — IRMS_DATA_DIR 환경변수를 따른다(상대경로는 ROOT 기준)."""
    raw = os.environ.get("IRMS_DATA_DIR", "").strip()
    data_dir = (Path(raw) if Path(raw).is_absolute() else ROOT / raw) if raw else ROOT / "data"
    return data_dir / "irms.db"


def backup_db() -> None:
    """SQLite 온라인 백업 — 서버 가동 중에도 트랜잭션 일관된 사본을 만든다."""
    db = _db_path()
    if not db.exists():
        return
    BACKUPS_DIR.mkdir(exist_ok=True)
    dest = BACKUPS_DIR / f"irms_{datetime.now():%Y%m%d_%H%M%S}.db"
    try:
        src = sqlite3.connect(str(db))
        dst = sqlite3.connect(str(dest))
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()
        log(f"DB 백업: {dest.name}")
    except Exception as exc:  # noqa: BLE001
        # 온라인 백업 실패 시 단순 복사 폴백(없는 것보단 낫다)
        try:
            shutil.copy2(db, dest)
            log(f"DB 백업(복사 폴백): {dest.name} — 온라인 백업 실패: {exc}")
        except Exception as exc2:  # noqa: BLE001
            log(f"DB 백업 실패(계속 진행): {exc2}")
            return
    # 생성 직후 검증 — 통과 시 미러·정상 prune 대상, 실패 시 .corrupt 로 격리.
    if _verify_backup(dest):
        _mirror_backup(dest)
    else:
        corrupt = dest.with_name(dest.name + ".corrupt")
        try:
            dest.rename(corrupt)
        except Exception as exc:  # noqa: BLE001
            log(f"손상 백업 격리 실패({dest.name}): {exc}")
        log(f"[경고] 백업 검증 실패 — 오늘 백업을 신뢰하지 마세요: {corrupt.name}")
    prune_backups()


# 검증 대상 핵심 테이블 — 하나라도 없으면 불완전 사본으로 판정 (전부 schema/migrations 확인 완료)
_VERIFY_TABLES = ("recipes", "recipe_items", "blend_records", "blend_details", "workers", "audit_logs")


def _verify_backup(dest: Path) -> bool:
    """백업 사본 무결성 검증 — 읽기 전용(mode=ro)으로 열어 원본·사본 모두 무수정.

    판정: PRAGMA integrity_check == 'ok' AND 핵심 테이블 전부 존재·COUNT 조회 가능.
    실패 사본은 호출부(backup_db)가 .corrupt 로 격리한다.
    """
    try:
        conn = sqlite3.connect(f"file:{dest.as_posix()}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return False
            counts = {}
            for table in _VERIFY_TABLES:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if row is None:
                    log(f"백업 검증: 핵심 테이블 누락 — {table}")
                    return False
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            log(f"백업 검증 OK: {dest.name} (blend_records={counts['blend_records']}, recipes={counts['recipes']})")
            return True
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log(f"백업 검증 중 오류: {exc}")
        return False


def _mirror_backup(dest: Path) -> None:
    """IRMS_BACKUP_MIRROR 가 설정돼 있으면 2차 사본을 복사(실패해도 계속)."""
    if not BACKUP_MIRROR:
        return
    try:
        mirror = Path(BACKUP_MIRROR)
        mirror.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, mirror / dest.name)
        log(f"백업 2차 사본: {mirror / dest.name}")
    except Exception as exc:  # noqa: BLE001
        log(f"백업 2차 사본 실패(계속 진행): {exc}")


def prune_backups() -> None:
    """보존일수를 넘긴 백업 삭제 — 단 최근 BACKUP_KEEP_MIN 개는 항상 보존.

    검증 실패로 격리된 `.corrupt` 사본은 원인 분석용으로 최근 2개만 보존한다.
    """
    if not BACKUPS_DIR.exists():
        return
    files = sorted(BACKUPS_DIR.glob("irms_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    cutoff = (datetime.now() - timedelta(days=BACKUP_KEEP_DAYS)).timestamp()
    removed = 0
    for old in files[BACKUP_KEEP_MIN:]:
        if old.stat().st_mtime < cutoff:
            try:
                old.unlink()
                removed += 1
            except Exception as exc:  # noqa: BLE001
                log(f"백업 정리 실패({old.name}): {exc}")
    # 격리 사본은 보존일수와 무관하게 최근 2개만 유지 — 검증 실패 원인 분석용.
    corrupt = sorted(BACKUPS_DIR.glob("irms_*.db.corrupt"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in corrupt[2:]:
        try:
            old.unlink()
            removed += 1
        except Exception as exc:  # noqa: BLE001
            log(f"손상 백업 정리 실패({old.name}): {exc}")
    if removed:
        log(f"백업 정리: {removed}개 삭제 (보존 {BACKUP_KEEP_DAYS}일, 최소 {BACKUP_KEEP_MIN}개 유지)")


def free_port() -> None:
    """PORT 를 점유한 프로세스를 정리 — 비정상 종료 잔존 서버로 인한 크래시 루프 방지."""
    try:
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f"Get-NetTCPConnection -LocalPort {PORT} -State Listen -ErrorAction SilentlyContinue "
                "| Select-Object -ExpandProperty OwningProcess -Unique",
            ],
            capture_output=True, text=True, timeout=20,
        ).stdout.split()
        me = os.getpid()
        for pid in out:
            if pid.isdigit() and int(pid) not in (me, 0):
                subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
                log(f"포트 {PORT} 점유 프로세스 종료: PID {pid}")
    except Exception as exc:  # noqa: BLE001
        log(f"포트 정리 실패(계속 진행): {exc}")


def apply_update() -> bool:
    """DB 백업 → git pull → pip install. 전부 성공하면 True.

    실패 시 False 를 돌려주고 호출부가 재시작을 건너뛴다(기존 서버는 메모리의
    옛 코드로 계속 동작하므로 무중단). 다음 주기에 자동 재시도된다.
    """
    log("새 업데이트 발견 → 반영 중 (DB 백업 → git pull → pip install)...")
    backup_db()
    if _git("pull", "origin", "main").returncode != 0:
        log("git pull 실패 — 이번 주기는 건너뛰고 다음에 재시도합니다.")
        return False
    pip = subprocess.run(
        [PYTHON, "-m", "pip", "install", "-r", _requirements_file(), "--quiet"],
        cwd=ROOT,
    )
    if pip.returncode != 0:
        log("pip install 실패 — 서버 재시작을 건너뜁니다(다음 주기에 재시도).")
        return False
    log("업데이트 반영 완료.")
    return True


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


def _ensure_runtime_self_healing() -> str:
    """의존성 설치 — 실패하면 원격 최신을 당겨(pull) 한 번 더 시도한다.

    lock 파일에 잘못된 핀(예: 이 파이썬에서 설치 불가한 버전)이 커밋되면
    ensure_runtime() 이 죽어 서버가 아예 뜨지 못하고, git pull 단계까지
    도달하지 못해 **원격에 수정본이 올라와도 자동 복구되지 않는다**
    (2026-07-14 numpy==2.5.0 / Python 3.11 로 운영 중단). 그래서 설치 실패 시
    먼저 최신 코드를 당겨보고 재시도한다.
    """
    try:
        return ensure_runtime()
    except subprocess.CalledProcessError:
        log("의존성 설치 실패 — 원격 최신을 받아 재시도합니다 (수정본이 올라왔을 수 있음).")
        if _git("pull", "origin", "main").returncode != 0:
            log("git pull 도 실패했습니다. 네트워크·자격증명 또는 requirements-lock.txt 를 확인하세요.")
            raise
        return ensure_runtime()  # 재시도도 실패하면 그대로 예외(기동 중단 — fail-loud)


def main() -> None:
    global PYTHON
    set_console_title(f"IRMS 서버 + 자동 업데이트 (포트 {PORT})")
    PYTHON = _ensure_runtime_self_healing()
    log(
        f"IRMS 실행 (자동 업데이트 {'ON' if AUTO else 'OFF'}, "
        f"{INTERVAL}초 주기, 포트 {PORT}, 백업 보존 {BACKUP_KEEP_DAYS}일"
        f"{' + 미러 ' + BACKUP_MIRROR if BACKUP_MIRROR else ''}). 종료: Ctrl+C"
    )
    free_port()  # 비정상 종료로 남은 옛 서버가 포트를 물고 있으면 정리
    last_daily_backup: date | None = None
    proc = start_server()
    try:
        while True:
            time.sleep(INTERVAL)
            if proc.poll() is not None:
                log("서버가 종료되어 다시 시작합니다.")
                free_port()
                proc = start_server()
                continue
            # 일일 자동 백업(감시 주기마다 날짜 확인 — 하루 1회)
            today = date.today()
            if last_daily_backup != today:
                backup_db()
                last_daily_backup = today
            if AUTO and has_update():
                # pull/pip 가 전부 성공했을 때만 재시작 — 실패하면 기존 서버가
                # (메모리에 올라간 옛 코드로) 계속 돌아 무중단.
                if apply_update():
                    stop_server(proc)
                    proc = start_server()
    except KeyboardInterrupt:
        log("종료 요청 — 서버 정리 중...")
    finally:
        stop_server(proc)
        log("종료되었습니다.")


if __name__ == "__main__":
    main()
