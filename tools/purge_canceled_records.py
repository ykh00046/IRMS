"""취소 보존기한 경과 배합 기록 정리 — serve.py 가 일일 백업 직후 호출한다.

백업 '이후'에 돌므로 삭제되는 기록도 그날 백업에는 남아 있다. 앱 기동 시에도
같은 정리가 한 번 돌지만(main.create_app), 서버가 오래 재시작 없이 돌 때를
이 스크립트가 메운다. 수동 실행도 무해(멱등).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db import get_connection  # noqa: E402
from src.services.record_delete_service import purge_expired_canceled  # noqa: E402


def main() -> int:
    with get_connection() as connection:
        purged = purge_expired_canceled(connection)
        connection.commit()
    if purged:
        lots = ", ".join(p["product_lot"] for p in purged[:10])
        print(f"취소 보존기한 경과 {len(purged)}건 삭제: {lots}")
    else:
        print("정리 대상 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
