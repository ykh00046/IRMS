"""verify_backup — backups/ 사본 무결성 수동 검증 (읽기 전용).

사용 (저장소 루트에서):
    python tools/verify_backup.py                 # backups/ 최신 irms_*.db 검증
    python tools/verify_backup.py backups\\irms_20260712_120000.db
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import serve  # noqa: E402  (serve.py 의 _verify_backup / BACKUPS_DIR 재사용 — 로직 이원화 금지)


def main() -> int:
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        candidates = sorted(serve.BACKUPS_DIR.glob("irms_*.db"), key=lambda p: p.stat().st_mtime)
        target = candidates[-1] if candidates else None
    if target is None or not target.exists():
        print("검증할 백업 파일이 없습니다.")
        return 1
    ok = serve._verify_backup(target)
    print(f"{'PASS' if ok else 'FAIL'}: {target}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
