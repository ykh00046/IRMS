"""백업 복구 도구 — 검증 → 기존 DB 대피 → 복사까지 한 번에 (2026-07-25).

왜 스크립트인가: 복구는 이미 사고가 난 상태에서, 당황한 비개발자가, 한 번에 정확히
해야 하는 작업이다. 그런데 지금까지는 문서의 절차를 손으로 따라가야 했고 그 중
**한 단계를 빼먹으면 복구본이 깨진다**:

    운영 DB 는 WAL 모드다. `data/irms.db` 만 옮기고 `data/irms.db-wal` 을 남겨두면,
    새로 복사한 백업 위에 **옛 DB 의 WAL 프레임이 그대로 재생**된다(WAL 은 DB 파일과
    묶여 있지 않고 페이지 번호로 적용된다). 서버는 켜지지만 데이터가 뒤섞인다.

이 도구는 그 실수를 불가능하게 만든다.

사용:
    1) 서버를 멈춘다(run_auto.bat 창을 닫는다).
    2) python tools/restore_backup.py --backup backups/irms_20260725_030000.db
    3) 서버를 다시 시작한다(run_auto.bat).

옵션:
    --backup PATH   복구할 백업 파일(생략하면 backups/ 의 가장 최근 정상 백업)
    --data-dir DIR  대상 데이터 폴더(생략하면 IRMS_DATA_DIR 또는 data/)
    --yes           확인 프롬프트 없이 진행(자동화용 — 평소에는 쓰지 말 것)

동작:
    · 백업 파일을 먼저 검증한다(무결성 + 핵심 테이블 + 기록 건수). 실패하면 아무것도
      건드리지 않고 중단한다.
    · 기존 data/irms.db 와 **-wal/-shm 을 함께** `restore-before/` 로 대피시킨다(삭제 아님).
    · 백업을 data/irms.db 로 복사하고 다시 한 번 검증한다.
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_VERIFY_TABLES = ("blend_records", "recipes")


def _out(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    print(text)


def verify(db_path: Path) -> tuple[bool, str]:
    """백업 파일이 실제로 열리고 핵심 테이블이 있는지 확인 → (성공, 요약).

    ⚠ 검증이 만든 짝 파일만 지운다. 원래 있던 -wal/-shm 은 절대 건드리지 않는다 —
    현재 운영 DB 를 검증할 때 그 안에는 아직 본체로 넘어가지 않은 실데이터가 있고,
    대피(restore-before)시키기 전에 지우면 그만큼이 영영 사라진다.
    """
    if not db_path.exists():
        return False, f"파일이 없습니다: {db_path}"
    pre_existing = {
        suffix for suffix in ("-wal", "-shm")
        if db_path.with_name(db_path.name + suffix).exists()
    }
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            return False, "무결성 검사 실패 — 이 백업은 손상됐습니다."
        counts = {}
        for table in _VERIFY_TABLES:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if row is None:
                return False, f"핵심 테이블이 없습니다: {table}"
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return True, f"배합 기록 {counts['blend_records']}건 · 레시피 {counts['recipes']}건"
    except Exception as exc:  # noqa: BLE001
        return False, f"열 수 없습니다: {exc}"
    finally:
        if conn is not None:
            conn.close()
        # 검증이 '새로 만든' 짝 파일만 정리한다(읽기 전용으로 열어도 생긴다).
        # 원래 있던 것은 남긴다 — 아래에서 대피 대상이 되어야 한다.
        for suffix in ("-wal", "-shm"):
            if suffix in pre_existing:
                continue
            try:
                db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass


def latest_backup(backups_dir: Path) -> Path | None:
    """가장 최근 백업(.corrupt 제외)."""
    if not backups_dir.exists():
        return None
    files = sorted(
        backups_dir.glob("irms_*.db"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return files[0] if files else None


def main() -> int:
    parser = argparse.ArgumentParser(description="BRM 백업 복구")
    parser.add_argument("--backup", default=None, help="복구할 백업 파일(.db)")
    parser.add_argument("--data-dir", default=None, help="대상 데이터 폴더")
    parser.add_argument("--yes", action="store_true", help="확인 없이 진행")
    args = parser.parse_args()

    data_dir = Path(args.data_dir or os.environ.get("IRMS_DATA_DIR") or (ROOT / "data"))
    target = data_dir / "irms.db"
    backups_dir = ROOT / "backups"

    src = Path(args.backup) if args.backup else latest_backup(backups_dir)
    if src is None:
        _out(f"[중단] 복구할 백업을 찾지 못했습니다: {backups_dir}")
        return 2

    _out("=== BRM 백업 복구 ===")
    _out(f"백업 파일 : {src}")
    _out(f"복구 대상 : {target}")

    ok, summary = verify(src)
    _out(f"백업 검증 : {'OK — ' + summary if ok else '실패 — ' + summary}")
    if not ok:
        _out("[중단] 손상된 백업으로는 복구하지 않습니다. 다른 백업을 고르세요.")
        return 2

    if target.exists():
        ok_cur, summary_cur = verify(target)
        _out(f"현재 DB   : {summary_cur if ok_cur else '읽을 수 없음 — ' + summary_cur}")

    if not args.yes:
        _out("")
        _out("서버가 멈춰 있어야 합니다(run_auto.bat 창을 닫았는지 확인하세요).")
        answer = input("이 백업으로 복구할까요? (y/N) ").strip().lower()
        if answer != "y":
            _out("취소했습니다. 아무것도 바뀌지 않았습니다.")
            return 1

    # 기존 DB 를 -wal/-shm 과 '함께' 대피시킨다. 이 셋을 함께 옮기는 것이 이 도구의 핵심 —
    # .db 만 옮기고 -wal 을 남기면 새 DB 위에 옛 WAL 이 재생돼 데이터가 뒤섞인다.
    data_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    keep_dir = data_dir / f"restore-before-{stamp}"
    moved = []
    for suffix in ("", "-wal", "-shm"):
        old = target.with_name(target.name + suffix)
        if old.exists():
            keep_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(keep_dir / old.name))
            moved.append(old.name)
    if moved:
        _out(f"기존 파일 대피: {', '.join(moved)} → {keep_dir}")

    shutil.copy2(src, target)
    ok_new, summary_new = verify(target)
    if not ok_new:
        _out(f"[중대] 복구본 검증 실패: {summary_new}")
        _out(f"       대피시킨 원본이 {keep_dir} 에 그대로 있습니다.")
        return 2

    _out(f"복구 완료 : {target} ({summary_new})")
    _out("이제 서버를 다시 시작하세요(run_auto.bat).")
    _out(f"문제가 없으면 {keep_dir} 는 나중에 지워도 됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
