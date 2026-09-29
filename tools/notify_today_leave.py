"""오늘 휴무 카톡 공지 — 근태허가원을 한 번 더 수집하고 오늘 쉬는 사람을 릴레이 큐에 넣는다.

계약: docs/attendance-approvals.md §7. 문구는 src/services/leave_notice.py 가 만든다.
릴레이는 문구를 해석하지 않는 덤 파이프라 **문구 책임은 여기(생산자)** 에 있다.

왜 보내기 직전에 다시 수집하나: 일일 수집 슬롯은 자정 무렵 백업 직후에 돈다. 아침에
결재가 끝난 당일 반차는 그 회차에 없으므로, 공지 전에 한 번 더 읽어야 빠지지 않는다.
수집이 실패해도 공지는 가진 자료로 나간다(fail-open) — 수집 실패로 공지가 멈추면 안 된다.

사용:
    python tools/notify_today_leave.py              지금 한 번(시각·요일 무시)
    python tools/notify_today_leave.py --window     serve.py 용 — 평일 지정 시대, 하루 한 번
    python tools/notify_today_leave.py --dry-run    문구만 출력(보내지 않음, 표식 안 남김)
    python tools/notify_today_leave.py --no-collect 수집 없이 가진 자료로

종료 코드(serve.py 가 읽는다):
    0  오늘 몫은 끝남(보냄 · 이미 보냄 · 쉬는 사람 없음 · 주말 · 설정 안 됨)
    1  실패 — 다음 감시 주기에 다시 시도
    3  아직 시각이 아님 — 오늘 몫이 남아 있음
"""

import json
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402
from src.db import get_connection  # noqa: E402
from src.services import attendance_approvals, leave_notice, settings_service  # noqa: E402

SOURCE = "irms_leave_notice"
# 오늘 몫을 끝낸 날짜(YYYY-MM-DD). serve.py 가 재시작돼도 두 번 보내지 않게 DB 에 둔다.
# 릴레이 dedup_key 가 한 번 더 막지만, 거기까지 가기 전에 포털을 다시 두드리지 않게.
DONE_SETTING_KEY = "leave_notice_done_date"

EXIT_DONE = 0
EXIT_FAILED = 1
EXIT_NOT_YET = 3


def dedup_key(day: date) -> str:
    return f"{SOURCE}-{day.isoformat()}"


def enqueue(message: str, day: date) -> tuple[bool, str]:
    payload = json.dumps(
        {
            "source": SOURCE,
            "room": config.LEAVE_NOTICE_ROOM,
            "message": message,
            "dedup_key": dedup_key(day),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        config.RELAY_URL + "/enqueue",
        data=payload,
        headers={
            "X-Relay-Token": config.RELAY_TOKEN,
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return False, f"릴레이 HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"릴레이 연결 실패: {exc}"
    if body.get("duplicate"):
        return True, "이미 보냄(릴레이 중복)"
    return True, f"큐 등록 id={body.get('id')}"


def _collect(connection) -> str:
    try:
        result = attendance_approvals.collect_from_portal(connection)
    except Exception as exc:  # noqa: BLE001 — 수집 실패가 공지를 막으면 안 된다
        return f"수집 실패(무시): {exc!r}"
    status = result.get("status")
    if status == attendance_approvals.RUN_OK:
        return f"수집: 신규 {result.get('created', 0)} · 갱신 {result.get('updated', 0)}"
    return f"수집: {status}"


def run(argv: list[str], *, now: datetime | None = None) -> int:
    now = now or datetime.now()
    today = now.date()
    window = "--window" in argv
    dry_run = "--dry-run" in argv

    if window:
        if today.weekday() >= 5:
            print("휴무 공지: 주말이라 보내지 않음")
            return EXIT_DONE
        if now.hour < config.LEAVE_NOTICE_HOUR:
            return EXIT_NOT_YET
        if now.hour > config.LEAVE_NOTICE_HOUR:
            # 시대를 놓쳤다(서버가 그 시각에 꺼져 있었다 등). 늦은 공지는 내지 않는다.
            print(f"휴무 공지: {config.LEAVE_NOTICE_HOUR}시대를 지나 오늘은 건너뜀")
            return EXIT_DONE
    if not dry_run and not config.relay_configured():
        print("휴무 공지: 설정 안 됨(IRMS_RELAY_URL·IRMS_RELAY_TOKEN)")
        return EXIT_DONE

    with get_connection() as connection:
        if not dry_run and settings_service.get_setting(connection, DONE_SETTING_KEY) == today.isoformat():
            print("휴무 공지: 오늘 이미 끝냄")
            return EXIT_DONE
        if "--no-collect" not in argv:
            print(_collect(connection))
        message = leave_notice.message_for_day(connection, today)

        if message is None:
            print("휴무 공지: 오늘 쉬는 사람 없음 — 보내지 않음")
            outcome = EXIT_DONE
        elif dry_run:
            print(message)
            return EXIT_DONE
        else:
            ok, detail = enqueue(message, today)
            print(f"휴무 공지: {detail}")
            outcome = EXIT_DONE if ok else EXIT_FAILED

        if outcome == EXIT_DONE and not dry_run:
            settings_service.set_setting(connection, DONE_SETTING_KEY, today.isoformat())
            connection.commit()
        return outcome


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
