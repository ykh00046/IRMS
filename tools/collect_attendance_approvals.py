"""근태허가원 포털 수집 — serve.py 가 하루 1회 호출한다(수동 실행도 무해).

왜 serve.py 인가(앱 lifespan 이 아니라):
  - 운영 PC 는 run_auto.bat → serve.py 로만 뜬다. serve.py 에는 이미 '오늘 한 번'
    슬롯(일일 백업·취소 기록 정리)이 있고, 날짜 표식으로 **쌓이지 않는다**.
  - 앱 lifespan 에 넣으면 잠자는 배경 태스크를 새로 만들어야 하고, 개발
    `uvicorn --reload` 에서 리로드마다 다시 뜨며, 자동 업데이트로 서버가 재시작될
    때마다 타이머가 초기화된다. 더 복잡한데 더 자주 어긋난다.
  - 그래도 같은 날 두 번 돌 수는 있으므로(serve.py 재시작) `--daily` 는 DB 의 마지막
    성공 회차를 보고 오늘 이미 성공했으면 건너뛴다. 동시 실행은 서비스 쪽 잠금이 막는다.
    단 한 회차 상한에 걸려 **남은 문서가 있으면 건너뛰지 않는다** — 밀린 것을 이어 받는다.

사용:
    python tools/collect_attendance_approvals.py          지금 한 번(강제)
    python tools/collect_attendance_approvals.py --daily  오늘 끝냈으면 건너뜀

포털 자격증명은 환경변수(.env)로만 온다. 이 스크립트는 값을 출력하지 않는다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db import get_connection, write_audit_log  # noqa: E402
from src.services import attendance_approvals as service  # noqa: E402


def _already_ran_today(connection) -> bool:
    """오늘(로컬 날짜) 성공했고 **남은 문서도 없으면** True.

    한 회차 상한에 걸려 남은 문서가 있으면 오늘 또 돌아야 한다 — "오늘 이미 수집했다"는
    말은 200건이 밀려 있는 상황에서 사실이 아니다.
    """
    from src.db.time_utils import local_today_text

    last = service.last_run(connection)
    if not last or last.get("status") != service.RUN_OK:
        return False
    if int(last.get("remaining") or 0) > 0:
        return False
    stamp = str(last.get("at") or "")
    # 저장은 UTC ISO 다. 날짜만 비교하면 자정 근처에 하루 두 번 돌 수 있지만,
    # 그건 중복 적재가 아니라 포털을 한 번 더 두드리는 정도라 감수한다.
    return stamp[:10] == local_today_text()[:10]


def main(argv: list[str]) -> int:
    daily = "--daily" in argv
    with get_connection() as connection:
        if daily and _already_ran_today(connection):
            print("근태허가원 수집: 오늘 이미 수집했습니다")
            return 0
        result = service.collect_from_portal(connection)
        if result.get("status") == service.RUN_OK:
            # 화면 버튼과 같은 액션으로 남긴다 — 감사 화면에서 "수집은 버튼을 눌러야만
            # 도는 것"으로 오해하지 않게. actor 는 없다(사람이 아니라 일일 실행).
            write_audit_log(
                connection,
                action="attendance_approvals_collected",
                target_type="attendance_approvals",
                target_id=result.get("received"),
                target_label=(
                    f"포털 수집(자동) {result.get('received', 0)}건 · "
                    f"신규 {result.get('created', 0)} · 갱신 {result.get('updated', 0)} · "
                    f"읽을 수 없음 {result.get('forbidden', 0)}"
                ),
                details={
                    "via": "portal",
                    "trigger": "daily" if daily else "cli",
                    "rows": result.get("rows"),
                    "received": result.get("received"),
                    "created": result.get("created"),
                    "updated": result.get("updated"),
                    "unchanged": result.get("unchanged"),
                    "fetched": result.get("fetched"),
                    "remaining": result.get("remaining"),
                    "forbidden": result.get("forbidden"),
                    "incomplete": result.get("incomplete_total"),
                    "unresolved": result.get("unresolved_total"),
                    "rejected": result.get("rejected_total"),
                },
            )
            connection.commit()

    status = result.get("status")
    if status == service.RUN_OK:
        tail = ""
        if result.get("remaining"):
            # 상한에 걸려 남은 건수. 다음 날(또는 화면 버튼)에 이어서 받는다.
            tail = f" · 남음 {result['remaining']}"
        print(
            f"근태허가원 수집: 문서 {result.get('rows', 0)}건 · "
            f"본문 {result.get('fetched', 0)} · 그대로 {result.get('unchanged', 0)} · "
            f"신규 {result.get('created', 0)} · 갱신 {result.get('updated', 0)} · "
            f"읽을 수 없음 {result.get('forbidden', 0)}{tail}"
        )
        return 0
    if status == service.RUN_NOT_CONFIGURED:
        print("근태허가원 수집: 설정 안 됨(IRMS_PORTAL_USERNAME·IRMS_PORTAL_PASSWORD)")
        return 0
    if status == service.RUN_BUSY:
        print("근태허가원 수집: 이미 진행 중")
        return 0
    # 실패해도 0 으로 끝낸다 — serve.py 의 일일 슬롯을 실패로 물들이지 않는다.
    print(f"근태허가원 수집 실패: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
