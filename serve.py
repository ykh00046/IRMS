"""IRMS 단일 창 실행 + 자동 업데이트 감시자.

한 콘솔에서 uvicorn 서버를 띄우고(서버 로그가 이 창에 그대로 출력됨), 주기적으로
origin/main 을 확인해 새 커밋이 있으면 DB 백업 → git pull → pip install → 서버
재시작한다. 창 하나로 서버 실행 + 자동 업데이트를 모두 처리한다.

실행:  .venv\\Scripts\\python.exe serve.py   (보통 run_auto.bat 이 대신 실행)
설정(환경변수): 프로젝트 루트 .env 를 먼저 로드한다(src/config.py 와 동일 우선순위 —
실제 환경변수가 .env 보다 우선). 따라서 IRMS_DATA_DIR·IRMS_PORT·IRMS_BACKUP_* 를 .env
로만 지정해도 serve.py(부모)와 서버(자식)가 같은 값을 본다.
    IRMS_PORT              서버 포트 (기본 9000)
    IRMS_AUTO_INTERVAL     업데이트 확인 주기(초, 기본 600 = 10분)
    IRMS_AUTO_UPDATE       0 이면 업데이트 감시 없이 서버만 실행
    IRMS_BACKUP_KEEP_DAYS  백업 보존 일수 (기본 30 — 오래된 백업 자동 삭제, 최근 5개는 항상 보존)
    IRMS_BACKUP_MIRROR     백업 2차 사본 폴더 (예: D:\\irms-backup — 미설정 시 로컬 backups/ 만)

자동 업데이트 상태는 <IRMS_DATA_DIR>/update-status.json 에 기록된다({ok,last_error,at}) —
콘솔을 못 봐도 마지막 업데이트 성패를 파일로 확인할 수 있다. git pull 이 로컬 변경으로
막히면 stash 로 안전 보관 후 재시도하고, 그래도 실패하면 매 주기 CRITICAL 경고를 낸다.

백업: 업데이트 반영 직전 + 매일 1회(감시 루프) 자동 백업. SQLite 온라인 백업
API 사용(서버 가동 중에도 일관된 사본). 복구: 서버 중지 → backups/ 의 원하는
irms_*.db 를 data/irms.db 로 복사 → 서버 시작.

참고: serve.py 자체가 업데이트되면 재시작 후 반영된다(무한 로딩 방지를 위해
실행 중에는 옛 serve.py 로 계속 감시). 서버(src/*) 변경은 매 재시작마다 반영.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env() -> None:
    """프로젝트 루트 .env 를 로드 — src/config.py 와 동일 우선순위.

    python-dotenv 의 기본 override=False 라 **이미 설정된 os.environ 값(실제 환경변수)이
    .env 보다 우선**한다(config.py:5~8 과 동일 규칙). 이걸로 IRMS_DATA_DIR·IRMS_PORT·
    IRMS_BACKUP_* 를 .env 로만 줘도 serve.py(부모)가 서버 자식과 같은 값을 읽어,
    "백업 대상 DB 경로/포트가 실제 서버와 어긋나는" 정합성 사고를 막는다.

    아직 의존성 설치 전(부트스트랩 직전)이라 python-dotenv 가 없으면 조용히 건너뛴다
    (그 경우 os.environ 만 사용 — 종전 동작과 동일).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


load_env()

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


def _data_dir() -> Path:
    """운영 데이터 폴더 — IRMS_DATA_DIR 환경변수를 따른다(상대경로는 ROOT 기준)."""
    raw = os.environ.get("IRMS_DATA_DIR", "").strip()
    return (Path(raw) if Path(raw).is_absolute() else ROOT / raw) if raw else ROOT / "data"


def _db_path() -> Path:
    """운영 DB 경로 — IRMS_DATA_DIR 환경변수를 따른다(상대경로는 ROOT 기준)."""
    return _data_dir() / "irms.db"


# 자동 업데이트 상태 파일(운영자가 콘솔을 못 봐도 마지막 성패를 파일로 점검).
UPDATE_STATUS_FILE = "update-status.json"


def write_update_status(ok: bool, last_error: str | None = None) -> None:
    """자동 업데이트 상태를 <IRMS_DATA_DIR>/update-status.json 에 기록.

    {ok, last_error, at} — 콘솔 로그를 놓쳐도 자동 업데이트가 살아있는지/멈췄는지
    파일 하나로 확인할 수 있다. 기록 실패는 부가 기능이므로 무시(운영 중단 없음).
    """
    payload = {
        "ok": bool(ok),
        "last_error": last_error,
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        target = _data_dir()
        target.mkdir(parents=True, exist_ok=True)
        (target / UPDATE_STATUS_FILE).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001 — 상태 파일 기록 실패는 치명적 아님
        log(f"업데이트 상태 파일 기록 실패(무시): {exc}")


def _loud(lines: list[str]) -> None:
    """여러 줄 경고 블록 — 콘솔에서 눈에 띄게(ASCII 배너 + 각 줄 접두)."""
    bar = "!" * 68
    log(bar)
    for line in lines:
        log(f"!! {line}")
    log(bar)


# backup_db() 결과 코드 — apply_update 의 게이트 판정에 쓴다.
BACKUP_OK = "ok"
BACKUP_CORRUPT = "corrupt"
BACKUP_FAILED = "failed"
BACKUP_SKIPPED_MISSING = "skipped_missing"


def backup_db() -> str:
    """SQLite 온라인 백업 — 서버 가동 중에도 트랜잭션 일관된 사본을 만든다.

    반환값(apply_update 게이트용):
      BACKUP_OK              생성·검증 통과(미러/보존 처리 완료)
      BACKUP_CORRUPT         생성됐으나 검증 실패 → .corrupt 격리
      BACKUP_FAILED          온라인·복사 폴백 모두 실패(사본 없음)
      BACKUP_SKIPPED_MISSING DB 파일이 없어 백업 대상 없음(경로 오설정 가능)
    """
    db = _db_path()
    if not db.exists():
        # 조용히 return 하지 않는다 — IRMS_DATA_DIR 오설정 시 "매일 백업하는 줄 알았는데
        # 한 건도 없던" 무음 사고를 막기 위해 경고로 남긴다.
        log(f"[경고] 백업 대상 DB 가 없습니다: {db} — IRMS_DATA_DIR 설정을 확인하세요(오설정이면 백업이 계속 스킵됨).")
        return BACKUP_SKIPPED_MISSING
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
            return BACKUP_FAILED
    # 생성 직후 검증 — 통과 시 미러·정상 prune 대상, 실패 시 .corrupt 로 격리.
    result = BACKUP_OK
    if _verify_backup(dest):
        _mirror_backup(dest)
    else:
        result = BACKUP_CORRUPT
        corrupt = dest.with_name(dest.name + ".corrupt")
        try:
            dest.rename(corrupt)
        except Exception as exc:  # noqa: BLE001
            log(f"손상 백업 격리 실패({dest.name}): {exc}")
        log(f"[경고] 백업 검증 실패 — 오늘 백업을 신뢰하지 마세요: {corrupt.name}")
    prune_backups()
    return result


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


def _git_output(cp: subprocess.CompletedProcess) -> str:
    """CompletedProcess 의 stdout+stderr 를 합쳐 정리(로그용)."""
    return ((cp.stdout or "") + (cp.stderr or "")).strip()


def _is_local_changes_failure(output: str) -> bool:
    """git pull 실패가 '로컬(추적/미추적) 파일 변경'으로 인한 것인지 판정.

    이 경우에 한해 stash 로 안전 보관 후 재시도한다(다른 사유 — 충돌·네트워크·
    자격증명 — 는 자동 복구 대상 아님).
    """
    markers = (
        "your local changes to the following files would be overwritten",
        "please commit your changes or stash them",
        "commit your changes or stash",
        "would be overwritten by merge",
        "would be overwritten by checkout",
        "the following untracked working tree files would be overwritten",
    )
    low = output.lower()
    return any(m in low for m in markers)


def _recover_and_retry_pull(failed: subprocess.CompletedProcess) -> bool:
    """git pull 실패 복구 — 로컬 변경이 원인이면 stash 후 1회 재시도.

    반환: 복구(재시도 pull 성공) 시 True, 아니면 False.
    - 로컬 변경이 원인이 아니면(네트워크·충돌 등) 복구하지 않고 CRITICAL.
    - stash 는 재시도 성공 여부와 무관하게 **절대 drop 하지 않는다**(운영자 데이터 보존).
    - 실패가 지속되면 apply_update 가 매 주기 다시 호출되므로 CRITICAL 이 매 주기 반복된다.
    """
    output = _git_output(failed)
    _loud([
        "git pull 실패 — 자동 업데이트가 진행되지 못했습니다.",
        "git 출력:",
        *(output.splitlines() or ["(출력 없음)"]),
    ])
    if not _is_local_changes_failure(output):
        _loud([
            "[CRITICAL] 자동 업데이트가 멈춰 있습니다.",
            "로컬 변경이 원인이 아니라 자동 복구 대상이 아닙니다(네트워크·자격증명·충돌 확인 필요).",
            "옛 버전으로 계속 서비스합니다.",
        ])
        write_update_status(False, f"git_pull_failed: {output[:500]}")
        return False

    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    stash_name = f"serve-auto-{stamp}"
    log(f"로컬 변경이 pull 을 막고 있습니다 → 안전 보관(stash) 후 재시도: {stash_name}")
    stash = _git("stash", "push", "--include-untracked", "-m", stash_name, capture=True)
    if stash.returncode != 0:
        _loud([
            "[CRITICAL] 자동 업데이트가 멈춰 있습니다.",
            f"stash 실패로 자동 복구 불가: {_git_output(stash)[:300]}",
            "운영자가 직접 작업트리를 정리해야 합니다. 옛 버전으로 계속 서비스합니다.",
        ])
        write_update_status(False, "git_stash_failed")
        return False

    retry = _git("pull", "origin", "main", capture=True)
    if retry.returncode == 0:
        # 성공 — stash 는 남겨둔다(drop 금지). 운영자가 나중에 apply 로 복원할 수 있게.
        log(f"stash 후 pull 성공 — 보관한 로컬 변경은 stash '{stash_name}' 로 남겨둡니다(자동 삭제 안 함).")
        log("확인: git stash list   /   복원: git stash apply stash@{0}")
        write_update_status(True, f"recovered_via_stash: {stash_name}")
        return True

    _loud([
        "[CRITICAL] 자동 업데이트가 멈춰 있습니다.",
        "로컬 변경을 stash 로 치웠는데도 pull 이 실패했습니다(충돌·네트워크 가능).",
        f"보관된 변경: stash '{stash_name}' (git stash list 로 확인, 자동 삭제 안 함).",
        f"git 출력: {_git_output(retry)[:300]}",
        "옛 버전으로 계속 서비스합니다.",
    ])
    write_update_status(False, f"git_pull_failed_after_stash: {stash_name}")
    return False


def apply_update() -> bool:
    """DB 백업 → git pull → pip install. 전부 성공하면 True.

    실패 시 False 를 돌려주고 호출부가 재시작을 건너뛴다(기존 서버는 메모리의
    옛 코드로 계속 동작하므로 무중단). 다음 주기에 자동 재시도된다.

    - 백업 게이트: 신뢰할 백업을 못 만들었으면(BACKUP_FAILED/BACKUP_CORRUPT) 갱신을
      **보류**한다("그날 신뢰할 백업 없이 코드만 갱신"되는 위험 차단).
    - git pull 이 로컬 변경으로 막히면 _recover_and_retry_pull 로 stash 후 재시도.
    """
    log("새 업데이트 발견 → 반영 중 (DB 백업 → git pull → pip install)...")
    backup = backup_db()
    if backup in (BACKUP_FAILED, BACKUP_CORRUPT):
        reason = "백업 생성 실패" if backup == BACKUP_FAILED else "백업 검증 실패(.corrupt 격리)"
        _loud([
            "자동 업데이트 보류 — 신뢰할 백업을 만들지 못했습니다.",
            f"사유: {reason}. 코드 갱신 없이 기존(옛) 서버로 계속 운영합니다.",
            "backups\\ 폴더와 IRMS_DATA_DIR 을 점검해 원인을 제거하세요.",
        ])
        write_update_status(False, f"backup_{backup}")
        return False
    # BACKUP_OK 또는 BACKUP_SKIPPED_MISSING(경고는 backup_db 가 이미 출력) → 진행

    pull = _git("pull", "origin", "main", capture=True)
    if pull.returncode != 0 and not _recover_and_retry_pull(pull):
        return False

    pip = subprocess.run(
        [PYTHON, "-m", "pip", "install", "-r", _requirements_file(), "--quiet"],
        cwd=ROOT,
    )
    if pip.returncode != 0:
        log("pip install 실패 — 서버 재시작을 건너뜁니다(다음 주기에 재시도).")
        write_update_status(False, "pip_install_failed")
        return False
    log("업데이트 반영 완료.")
    write_update_status(True, None)
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


def warn_if_not_production() -> None:
    """운영 기동인데 IRMS_ENV 가 production 이 아니면 눈에 띄는 경고(동작 변경 없음).

    누락/오설정 시 개발 모드로 조용히 떨어져 HSTS·Secure 쿠키·strict SameSite 가 꺼지고
    세션 시크릿이 랜덤, 데모 시드가 기본 ON 이 되는 위험을 콘솔에서 인지시키기 위함.
    """
    env = os.environ.get("IRMS_ENV", "").strip().lower()
    if env == "production":
        return
    _loud([
        "개발 모드로 기동 중 — 운영 보안 기능이 꺼져 있습니다.",
        f"IRMS_ENV = {env or '(미설정)'} (production 아님).",
        "HSTS·Secure 쿠키·strict SameSite 비활성 · 세션 시크릿 랜덤 · 데모 시드 기본 ON.",
        "운영 PC 라면 .env 의 IRMS_ENV=production 이 실제로 들어갔는지 확인하세요.",
    ])


def main() -> None:
    global PYTHON
    set_console_title(f"IRMS 서버 + 자동 업데이트 (포트 {PORT})")
    warn_if_not_production()
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
