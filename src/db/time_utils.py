from datetime import date, datetime, timezone


def utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def local_today_text() -> str:
    """현장 기준 '오늘'(로컬 날짜, YYYY-MM-DD).

    저장 타임스탬프는 UTC(utc_now_text)를 유지하되, '오늘' 판정·기본 측정일 등
    날짜 개념은 로컬로 통일 — 자정 부근 UTC 하루 밀림 방지(단일 사이트 KST 운영).
    """
    return date.today().isoformat()
