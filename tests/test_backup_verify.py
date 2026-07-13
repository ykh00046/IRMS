"""백업 무결성 자동 검증 — _verify_backup / backup_db 격리 흐름 / CLI 회귀 테스트.

serve.py 의 __main__ 가드로 import 부작용 없음(루트 conftest 가 sys.path 처리).
BACKUPS_DIR 는 monkeypatch 로 실제 backups/ 를 오염시키지 않는다.
라이브 data/·backups/ 무접촉 — 모든 픽스처는 tmp_path 기반.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import serve


# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------

# _VERIFY_TABLES 와 동일 — 테스트 자체 스키마 정의용(로직 이원화 방지용 아님, 독립 검증).
_VERIFY_TABLES = ("recipes", "recipe_items", "blend_records", "blend_details", "workers", "audit_logs")


def _make_valid_db(path: Path, *, omit: str | None = None) -> None:
    """핵심 테이블 6개를 갖춘 유효 SQLite 파일 생성. omit 지정 시 해당 테이블 제외."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE recipes (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE recipe_items (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE blend_records (id INTEGER PRIMARY KEY, product_lot TEXT)")
        conn.execute("CREATE TABLE blend_details (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE workers (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE audit_logs (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO blend_records (id, product_lot) VALUES (1, 'LOT-1')")
        conn.execute("INSERT INTO recipes (id) VALUES (1)")
        conn.commit()
    finally:
        conn.close()
    if omit is not None:
        # 테이블 드롭으로 "핵심 테이블 누락" 시나리오 구성
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(f"DROP TABLE IF EXISTS {omit}")
            conn.commit()
        finally:
            conn.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# _verify_backup 직접 케이스
# ---------------------------------------------------------------------------

def test_verify_backup_valid_passes(tmp_path: Path) -> None:
    dest = tmp_path / "irms_ok.db"
    _make_valid_db(dest)
    assert serve._verify_backup(dest) is True


def test_verify_backup_corrupt_fails(tmp_path: Path) -> None:
    dest = tmp_path / "irms_corrupt.db"
    _make_valid_db(dest)
    # 파일 앞부분 수백 바이트를 임의 바이트로 덮어쓰기 → 구조 손상
    with open(dest, "r+b") as fh:
        fh.seek(100)
        fh.write(b"\x00\xff\x00\xff" * 200)
    assert serve._verify_backup(dest) is False


def test_verify_backup_non_sqlite_fails(tmp_path: Path) -> None:
    dest = tmp_path / "not_a_db.db"
    dest.write_text("this is not a sqlite database", encoding="utf-8")
    assert serve._verify_backup(dest) is False


def test_verify_backup_missing_table_fails(tmp_path: Path) -> None:
    dest = tmp_path / "irms_missing.db"
    _make_valid_db(dest, omit="blend_records")
    assert serve._verify_backup(dest) is False


def test_verify_backup_is_readonly_no_mutation(tmp_path: Path) -> None:
    """PASS 케이스: 검증 전후 sha256 동일, -wal/-shm 미생성(읽기 전용 확인)."""
    dest = tmp_path / "irms_ro.db"
    _make_valid_db(dest)
    before = _sha256(dest)
    assert serve._verify_backup(dest) is True
    after = _sha256(dest)
    assert before == after
    assert not (tmp_path / "irms_ro.db-wal").exists()
    assert not (tmp_path / "irms_ro.db-shm").exists()


# ---------------------------------------------------------------------------
# backup_db 흐름 (monkeypatch BACKUPS_DIR + _db_path)
# ---------------------------------------------------------------------------

def _seed_source_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _make_valid_db(db_path)


def test_backup_db_isolates_when_verify_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """검증 False → irms_*.db.corrupt 생성, _mirror_backup 미호출, 원본 irms_*.db 부재."""
    monkeypatch.setattr(serve, "BACKUPS_DIR", tmp_path)
    src_dir = tmp_path / "src"
    monkeypatch.setattr(serve, "_db_path", lambda: src_dir / "irms.db")
    _seed_source_db(src_dir / "irms.db")

    mirror_calls: list[Path] = []
    monkeypatch.setattr(serve, "_mirror_backup", lambda dest: mirror_calls.append(dest))
    monkeypatch.setattr(serve, "_verify_backup", lambda dest: False)

    serve.backup_db()

    corrupt = list(tmp_path.glob("irms_*.db.corrupt"))
    leftover = list(tmp_path.glob("irms_*.db"))
    assert corrupt, "검증 실패 시 .corrupt 격리 파일이 생성돼야 함"
    assert not leftover, "검증 실패 시 정상 irms_*.db 는 남지 않아야 함"
    assert mirror_calls == [], "_mirror_backup 은 검증 통과본만 받아야 함"


def test_backup_db_normal_flow_verifies_and_mirrors(
    tmp_path: Path, monkeypatch
) -> None:
    """정상 흐름: irms_*.db 생성 + 검증 통과, _mirror_backup 호출, .corrupt 없음."""
    monkeypatch.setattr(serve, "BACKUPS_DIR", tmp_path)
    src_dir = tmp_path / "src"
    monkeypatch.setattr(serve, "_db_path", lambda: src_dir / "irms.db")
    _seed_source_db(src_dir / "irms.db")

    mirror_calls: list[Path] = []
    monkeypatch.setattr(serve, "_mirror_backup", lambda dest: mirror_calls.append(dest))

    serve.backup_db()

    normal = list(tmp_path.glob("irms_*.db"))
    corrupt = list(tmp_path.glob("irms_*.db.corrupt"))
    assert normal, "정상 흐름에서 irms_*.db 가 생성돼야 함"
    assert not corrupt, "검증 통과 시 .corrupt 가 없어야 함"
    assert len(mirror_calls) == 1, "_mirror_backup 은 통과본 1회 호출"


# ---------------------------------------------------------------------------
# prune_backups .corrupt 보존 규칙
# ---------------------------------------------------------------------------

def test_prune_keeps_only_two_recent_corrupt(tmp_path: Path, monkeypatch) -> None:
    """.corrupt 3개 → prune_backups 후 최근 2개만 잔존."""
    monkeypatch.setattr(serve, "BACKUPS_DIR", tmp_path)
    import time
    files = []
    for i in range(3):
        p = tmp_path / f"irms_2026010{i}_120000.db.corrupt"
        p.write_bytes(b"x")
        # mtime 을 i 초 차이로 벌려 순서 확정
        ts = time.time() + i
        os.utime(p, (ts, ts))
        files.append(p)
    serve.prune_backups()
    remaining = sorted(tmp_path.glob("irms_*.db.corrupt"))
    assert len(remaining) == 2, f"최근 2개만 잔존해야 함 (got {len(remaining)})"
    # 가장 오래된 첫 파일은 삭제
    assert files[0] not in remaining


# ---------------------------------------------------------------------------
# CLI exit code (subprocess — 전역 python)
# ---------------------------------------------------------------------------

def test_cli_pass_exit_code(tmp_path: Path) -> None:
    dest = tmp_path / "irms_cli_ok.db"
    _make_valid_db(dest)
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(root / "tools" / "verify_backup.py"), str(dest)],
        capture_output=True, text=True, errors="replace",
    )
    assert result.returncode == 0, f"PASS 케이스 exit 0\n{result.stdout}\n{result.stderr}"
    assert "PASS" in result.stdout


def test_cli_fail_exit_code(tmp_path: Path) -> None:
    dest = tmp_path / "irms_cli_bad.db"
    dest.write_text("not sqlite", encoding="utf-8")
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(root / "tools" / "verify_backup.py"), str(dest)],
        capture_output=True, text=True, errors="replace",
    )
    assert result.returncode == 1, f"FAIL 케이스 exit 1\n{result.stdout}\n{result.stderr}"
    assert "FAIL" in result.stdout
