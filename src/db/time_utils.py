from datetime import datetime, timedelta, timezone


def utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_cutoff_text(now_text: str, seconds: int) -> str:
    """Return the ISO timestamp ``seconds`` before ``now_text``."""
    now = datetime.fromisoformat(now_text.replace("Z", "+00:00"))
    return (now - timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
