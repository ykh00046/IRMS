"""알림 시간대(슬롯) 스케줄 — 근태·점도가 동일하게 쓰는 공용 로직.

하루 정해진 시각(09/13/16시)에 슬롯당 1번만 알린다. 앱을 재시작해도 이미 30분 넘게
지난 슬롯은 다시 띄우지 않는다(켤 때마다 도로 뜨는 것 방지).
"""

from __future__ import annotations

import datetime as _dt

# 알림 시각(24시간). 근태·점도 공통.
SCHEDULED_ALERT_HOURS = (9, 13, 16)
# 슬롯 시작 후 이 시간이 지나 앱이 켜지면 그 슬롯은 지난 것으로 보고 건너뛴다.
SLOT_STALE_GRACE_MINUTES = 30


def current_slot_key(now: _dt.datetime) -> str | None:
    """현재 시각 기준 '지금 알려야 할' 가장 최근 슬롯 키(YYYY-MM-DDTHH). 없으면 None."""
    due_hour: int | None = None
    for hour in SCHEDULED_ALERT_HOURS:
        if now.hour >= hour:
            due_hour = hour
        else:
            break
    if due_hour is None:
        return None
    return f"{now.date().isoformat()}T{due_hour:02d}"


def seconds_until_next_slot(now: _dt.datetime) -> int:
    """다음 슬롯까지 남은 초(오늘 남은 슬롯이 없으면 내일 첫 슬롯까지)."""
    for hour in SCHEDULED_ALERT_HOURS:
        scheduled = _dt.datetime.combine(now.date(), _dt.time(hour=hour))
        if scheduled > now:
            return max(int((scheduled - now).total_seconds()), 1)
    tomorrow = now.date() + _dt.timedelta(days=1)
    scheduled = _dt.datetime.combine(tomorrow, _dt.time(hour=SCHEDULED_ALERT_HOURS[0]))
    return max(int((scheduled - now).total_seconds()), 1)


def stale_slot_key_on_startup(now: _dt.datetime) -> str | None:
    """앱 시작 시, 현재 슬롯이 이미 유예(30분)를 넘겼으면 그 슬롯 키를 반환(=처리된 것으로 표시)."""
    slot_key = current_slot_key(now)
    if not slot_key:
        return None
    slot_hour = int(slot_key.split("T", 1)[1])
    slot_start = _dt.datetime.combine(now.date(), _dt.time(hour=slot_hour))
    elapsed = (now - slot_start).total_seconds()
    if elapsed > SLOT_STALE_GRACE_MINUTES * 60:
        return slot_key
    return None
