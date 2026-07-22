"""serve.py 운영 보강 헬퍼 단위 테스트 — .env 로드·업데이트 상태·백업 게이트·pull 복구.

docs/ops-infra-flows.md §9 (1·2·3·4번) 갭 수정에 대응한다. 서버 루프를 돌리지 않고
헬퍼만 임시 폴더/모킹으로 검증한다(실제 git pull/서버 기동 없음).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_serve():
    spec = importlib.util.spec_from_file_location("serve_helpers_mod", ROOT / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["serve_helpers_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


def _cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr=stderr)


# ── Fix 1: .env 로드(실제 환경변수가 .env 보다 우선) ──────────────────────────
def test_load_env_reads_dotenv(tmp_path, monkeypatch):
    serve = _load_serve()
    monkeypatch.setattr(serve, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("IRMS_FROM_DOTENV=hello\n", encoding="utf-8")
    monkeypatch.delenv("IRMS_FROM_DOTENV", raising=False)

    serve.load_env()

    import os
    assert os.environ.get("IRMS_FROM_DOTENV") == "hello"


def test_load_env_real_env_wins_over_dotenv(tmp_path, monkeypatch):
    """config.py 와 동일 우선순위: 이미 설정된 실제 환경변수가 .env 를 덮지 않는다."""
    serve = _load_serve()
    monkeypatch.setattr(serve, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("IRMS_PRECEDENCE=from_dotenv\n", encoding="utf-8")
    monkeypatch.setenv("IRMS_PRECEDENCE", "from_realenv")

    serve.load_env()

    import os
    assert os.environ.get("IRMS_PRECEDENCE") == "from_realenv"


# ── Fix 2/3: 업데이트 상태 파일 ────────────────────────────────────────────────
def test_write_update_status_writes_json(tmp_path, monkeypatch):
    serve = _load_serve()
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path / "data"))

    serve.write_update_status(False, "git_pull_failed: boom")

    status = json.loads((tmp_path / "data" / serve.UPDATE_STATUS_FILE).read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert status["last_error"] == "git_pull_failed: boom"
    assert status["at"]  # ISO 타임스탬프 존재


# ── Fix 2: git pull 실패 분류 ─────────────────────────────────────────────────
def test_is_local_changes_failure_detects_tracked_and_untracked():
    serve = _load_serve()
    tracked = "error: Your local changes to the following files would be overwritten by merge:\n\tdocs/x"
    untracked = "error: The following untracked working tree files would be overwritten by merge:\n\tnew.txt"
    network = "fatal: unable to access 'https://github.com/...': Could not resolve host"
    assert serve._is_local_changes_failure(tracked) is True
    assert serve._is_local_changes_failure(untracked) is True
    assert serve._is_local_changes_failure(network) is False


def test_recover_pull_stashes_and_retries_success(tmp_path, monkeypatch):
    """로컬 변경으로 pull 이 막히면 stash → 재시도, 성공 시 stash 는 drop 하지 않는다."""
    serve = _load_serve()
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path / "data"))
    calls = []

    def fake_git(*args, capture=False):
        calls.append(args)
        if args[0] == "stash":
            return _cp(0)
        if args[0] == "pull":
            return _cp(0)  # 재시도 pull 성공
        return _cp(0)

    monkeypatch.setattr(serve, "_git", fake_git)
    failed = _cp(1, stderr="Your local changes to the following files would be overwritten by merge")

    assert serve._recover_and_retry_pull(failed) is True
    verbs = [c[0] for c in calls]
    assert "stash" in verbs and "pull" in verbs
    assert "drop" not in [a for c in calls for a in c]  # 절대 drop 안 함
    status = json.loads((tmp_path / "data" / serve.UPDATE_STATUS_FILE).read_text(encoding="utf-8"))
    assert status["ok"] is True
    assert "recovered_via_stash" in status["last_error"]


def test_recover_pull_non_local_failure_is_critical(tmp_path, monkeypatch):
    """로컬 변경이 아닌 실패(네트워크 등)는 복구하지 않고 실패 상태를 기록한다."""
    serve = _load_serve()
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(serve, "_git", lambda *a, capture=False: _cp(0))
    failed = _cp(1, stderr="fatal: unable to access remote: Could not resolve host")

    assert serve._recover_and_retry_pull(failed) is False
    status = json.loads((tmp_path / "data" / serve.UPDATE_STATUS_FILE).read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert status["last_error"].startswith("git_pull_failed")


def test_recover_pull_still_failing_after_stash_is_critical(tmp_path, monkeypatch):
    serve = _load_serve()
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path / "data"))

    def fake_git(*args, capture=False):
        if args[0] == "stash":
            return _cp(0)
        if args[0] == "pull":
            return _cp(1, stderr="CONFLICT")  # 재시도도 실패
        return _cp(0)

    monkeypatch.setattr(serve, "_git", fake_git)
    failed = _cp(1, stderr="Please commit your changes or stash them before you merge")

    assert serve._recover_and_retry_pull(failed) is False
    status = json.loads((tmp_path / "data" / serve.UPDATE_STATUS_FILE).read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert "after_stash" in status["last_error"]


# ── Fix 3: 백업 게이트 (backup_db 결과에 따라 업데이트 보류) ─────────────────────
def test_backup_db_missing_returns_skipped(tmp_path, monkeypatch):
    serve = _load_serve()
    monkeypatch.setattr(serve, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path / "data"))  # DB 파일 없음

    assert serve.backup_db() == serve.BACKUP_SKIPPED_MISSING


def test_apply_update_aborts_when_backup_failed(tmp_path, monkeypatch):
    """백업이 실패/격리되면 git pull 로 넘어가지 않고 보류한다."""
    serve = _load_serve()
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(serve, "backup_db", lambda: serve.BACKUP_FAILED)
    pull_called = {"n": 0}

    def fake_git(*args, capture=False):
        if args and args[0] == "pull":
            pull_called["n"] += 1
        return _cp(0)

    monkeypatch.setattr(serve, "_git", fake_git)

    assert serve.apply_update() is False
    assert pull_called["n"] == 0  # pull 로 진행하지 않음
    status = json.loads((tmp_path / "data" / serve.UPDATE_STATUS_FILE).read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert status["last_error"] == "backup_failed"


def test_apply_update_aborts_when_backup_corrupt(tmp_path, monkeypatch):
    serve = _load_serve()
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(serve, "backup_db", lambda: serve.BACKUP_CORRUPT)
    monkeypatch.setattr(serve, "_git", lambda *a, capture=False: _cp(0))

    assert serve.apply_update() is False
    status = json.loads((tmp_path / "data" / serve.UPDATE_STATUS_FILE).read_text(encoding="utf-8"))
    assert status["last_error"] == "backup_corrupt"


def test_apply_update_proceeds_when_backup_ok(tmp_path, monkeypatch):
    """백업 OK + pull/pip 성공이면 업데이트 반영(True) + 상태 ok."""
    serve = _load_serve()
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(serve, "backup_db", lambda: serve.BACKUP_OK)
    monkeypatch.setattr(serve, "_git", lambda *a, capture=False: _cp(0))
    monkeypatch.setattr(serve.subprocess, "run", lambda *a, **k: _cp(0))

    assert serve.apply_update() is True
    status = json.loads((tmp_path / "data" / serve.UPDATE_STATUS_FILE).read_text(encoding="utf-8"))
    assert status["ok"] is True


def test_apply_update_proceeds_when_db_missing(tmp_path, monkeypatch):
    """DB 미존재(SKIPPED)는 게이트를 막지 않는다(신규 설치 등) — 경고만 하고 진행."""
    serve = _load_serve()
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(serve, "backup_db", lambda: serve.BACKUP_SKIPPED_MISSING)
    monkeypatch.setattr(serve, "_git", lambda *a, capture=False: _cp(0))
    monkeypatch.setattr(serve.subprocess, "run", lambda *a, **k: _cp(0))

    assert serve.apply_update() is True


# ── Fix 4: IRMS_ENV 경고 (동작 변경 없음, 경고만) ──────────────────────────────
def test_warn_if_not_production_warns_when_unset(monkeypatch, capsys):
    serve = _load_serve()
    monkeypatch.delenv("IRMS_ENV", raising=False)
    serve.warn_if_not_production()
    out = capsys.readouterr().out
    assert "개발 모드로 기동 중" in out


def test_warn_if_not_production_silent_in_production(monkeypatch, capsys):
    serve = _load_serve()
    monkeypatch.setenv("IRMS_ENV", "production")
    serve.warn_if_not_production()
    assert "개발 모드로 기동 중" not in capsys.readouterr().out
