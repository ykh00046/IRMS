"""serve.py 운영 함수 단위 테스트 — 일일 백업·보존 정리·lock 파일 선택.

Plan: docs/01-plan/roadmap-2026H2.md Phase 1 (P1-1, P1-3).
free_port 는 시스템 프로세스를 죽이므로 여기서 실행하지 않는다(존재만 확인).
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_serve():
    spec = importlib.util.spec_from_file_location("serve_mod", ROOT / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["serve_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_backup_db_creates_consistent_copy(tmp_path, monkeypatch):
    serve = _load_serve()
    # ROOT/DATA_DIR 를 임시 경로로 바꿔 실제 저장소를 건드리지 않는다
    monkeypatch.setattr(serve, "ROOT", tmp_path)
    monkeypatch.setattr(serve, "BACKUP_MIRROR", "")
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path / "data"))
    data = tmp_path / "data"
    data.mkdir()
    db = data / "irms.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('hello')")
    conn.commit()
    conn.close()

    serve.backup_db()

    backups = list((tmp_path / "backups").glob("irms_*.db"))
    assert len(backups) == 1
    check = sqlite3.connect(backups[0])
    assert check.execute("SELECT v FROM t").fetchone()[0] == "hello"
    check.close()


def test_backup_mirror_copy(tmp_path, monkeypatch):
    serve = _load_serve()
    monkeypatch.setattr(serve, "ROOT", tmp_path)
    mirror = tmp_path / "mirror-drive"
    monkeypatch.setattr(serve, "BACKUP_MIRROR", str(mirror))
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path / "data"))
    data = tmp_path / "data"
    data.mkdir()
    sqlite3.connect(data / "irms.db").execute("CREATE TABLE t (v)").connection.close()

    serve.backup_db()

    assert len(list(mirror.glob("irms_*.db"))) == 1  # 2차 사본 생성


def test_prune_backups_keeps_recent_and_minimum(tmp_path, monkeypatch):
    serve = _load_serve()
    monkeypatch.setattr(serve, "ROOT", tmp_path)
    monkeypatch.setattr(serve, "BACKUP_KEEP_DAYS", 30)
    monkeypatch.setattr(serve, "BACKUP_KEEP_MIN", 3)
    backups = tmp_path / "backups"
    backups.mkdir()

    old_ts = time.time() - 60 * 60 * 24 * 60  # 60일 전
    for i in range(6):  # 오래된 백업 6개
        f = backups / f"irms_old{i}.db"
        f.write_bytes(b"x")
        os.utime(f, (old_ts, old_ts))
    fresh = backups / "irms_fresh.db"  # 오늘 백업 1개
    fresh.write_bytes(b"x")

    serve.prune_backups()

    remaining = sorted(p.name for p in backups.glob("irms_*.db"))
    # 오늘 것 + 최소 보존(최신 3개에 포함된 오래된 2개) = 3개
    assert fresh.name in remaining
    assert len(remaining) == 3


def test_requirements_file_prefers_lock(tmp_path, monkeypatch):
    serve = _load_serve()
    monkeypatch.setattr(serve, "ROOT", tmp_path)
    assert serve._requirements_file() == "requirements.txt"  # lock 없으면 기본
    (tmp_path / "requirements-lock.txt").write_text("fastapi==0.136.3\n")
    assert serve._requirements_file() == "requirements-lock.txt"


def test_free_port_exists():
    serve = _load_serve()
    assert callable(serve.free_port)
