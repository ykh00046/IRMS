"""check_repo_hygiene.check() — 루트 1단계 위생 검사 회귀 테스트.

check() 는 순수 함수(루트 Path 입력 → 위반 문자열 리스트)이므로 tmp_path 로
격리된 가짜 루트에서 검증한다. 실제 저장소 루트를 건드리지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from tools.check_repo_hygiene import ALLOWED_DIRS, check


def _seed_clean_root(root: Path) -> None:
    """허용 구조만 있는 깨끗한 가짜 루트 구성."""
    for name in ("src", "tools", "tests", "docs", "data", "backups", ".venv", ".tmp-tests"):
        (root / name).mkdir()
    # 허용된 일반 파일(루트 1단계, 확장자 미포함) 몇 개
    (root / "serve.py").write_text("", encoding="utf-8")
    (root / "README.md").write_text("", encoding="utf-8")


def test_clean_root_no_violations(tmp_path: Path) -> None:
    _seed_clean_root(tmp_path)
    assert check(tmp_path) == []


def test_tmp_dir_is_violation(tmp_path: Path) -> None:
    _seed_clean_root(tmp_path)
    (tmp_path / "tmp_ui99").mkdir()
    violations = check(tmp_path)
    assert len(violations) == 1
    assert "tmp_ui99" in violations[0]


def test_root_png_and_db_are_violations(tmp_path: Path) -> None:
    _seed_clean_root(tmp_path)
    (tmp_path / "shot.png").write_bytes(b"")
    (tmp_path / "stray.db").write_bytes(b"")
    violations = check(tmp_path)
    assert len(violations) == 2
    joined = " ".join(violations)
    assert "shot.png" in joined
    assert "stray.db" in joined


def test_stale_venv_backup_is_violation(tmp_path: Path) -> None:
    _seed_clean_root(tmp_path)
    (tmp_path / ".venv-wsl-backup-x").mkdir()
    violations = check(tmp_path)
    assert len(violations) == 1
    assert ".venv-wsl-backup-x" in violations[0]


def test_nested_png_in_tmp_tests_not_violation(tmp_path: Path) -> None:
    """루트 1단계 한정 — .tmp-tests/ 내부 png 는 위반 아님."""
    _seed_clean_root(tmp_path)
    inner = tmp_path / ".tmp-tests" / "smoke_runtime"
    inner.mkdir(parents=True)
    (inner / "screenshot.png").write_bytes(b"")
    assert check(tmp_path) == []


def test_allowed_dirs_set_covers_standard_layout() -> None:
    """allowlist 가 예상 표준 디렉터리를 모두 포함하는지(완화 방어용 회귀)."""
    for expected in ("src", "tools", "tests", "docs", "data", "backups", ".venv", ".tmp-tests"):
        assert expected in ALLOWED_DIRS, f"allowlist 에 {expected} 누락"
