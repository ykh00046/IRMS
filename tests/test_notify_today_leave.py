"""오늘 휴무 공지 도구 — docs/attendance-approvals.md §7 의 발송 규칙.

  1. --window: 주말·시대 지남은 끝(0), 시대 전은 '아직'(3), 시대 안에서만 보낸다
  2. 하루 한 번: 끝낸 날짜 표식이 있으면 다시 수집·발송하지 않는다
  3. 릴레이 실패는 1(다음 주기 재시도), 표식을 남기지 않는다
  4. 쉬는 사람이 없으면 보내지 않고 끝낸다. 설정이 없으면 조용히 끝낸다
  5. 수집이 터져도 공지는 나간다(fail-open)

릴레이·포털·엑셀은 부르지 않는다 — 전부 갈아끼운다.
"""

from __future__ import annotations

import contextlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import notify_today_leave as tool  # noqa: E402
from src import config  # noqa: E402

WED_8 = datetime(2026, 9, 30, 8, 10)


@pytest.fixture
def env(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT, updated_by TEXT)"
    )
    sent: list[dict] = []
    state = {"message": "🌴 오늘 휴무 (9/30 수) 총 1명\n\n· 홍길동 연차", "ok": True, "collects": 0}

    def fake_collect(_connection):
        state["collects"] += 1
        return {"status": "ok", "created": 0, "updated": 0}

    def fake_enqueue(message, day):
        sent.append({"message": message, "day": day})
        return state["ok"], "큐 등록 id=1" if state["ok"] else "릴레이 연결 실패"

    monkeypatch.setattr(tool, "get_connection", lambda: contextlib.nullcontext(connection))
    monkeypatch.setattr(tool.attendance_approvals, "collect_from_portal", fake_collect)
    monkeypatch.setattr(tool.leave_notice, "message_for_day", lambda _c, _d: state["message"])
    monkeypatch.setattr(tool, "enqueue", fake_enqueue)
    monkeypatch.setattr(config, "RELAY_URL", "http://relay.test")
    monkeypatch.setattr(config, "RELAY_TOKEN", "t")
    monkeypatch.setattr(config, "LEAVE_NOTICE_HOUR", 8)
    return {"connection": connection, "sent": sent, "state": state}


def _done_date(env):
    row = env["connection"].execute(
        "SELECT value FROM app_settings WHERE key = ?", (tool.DONE_SETTING_KEY,)
    ).fetchone()
    return None if row is None else row["value"]


# ── 1. 시각 창 ───────────────────────────────────────────────────────────────


def test_window_sends_inside_the_hour_once(env):
    assert tool.run(["--window"], now=WED_8) == tool.EXIT_DONE
    assert len(env["sent"]) == 1
    assert _done_date(env) == "2026-09-30"
    # 같은 날 다음 주기 — 수집도 발송도 다시 하지 않는다
    assert tool.run(["--window"], now=WED_8.replace(minute=40)) == tool.EXIT_DONE
    assert len(env["sent"]) == 1
    assert env["state"]["collects"] == 1


def test_window_before_the_hour_is_not_yet(env):
    assert tool.run(["--window"], now=WED_8.replace(hour=7)) == tool.EXIT_NOT_YET
    assert env["sent"] == [] and env["state"]["collects"] == 0


def test_window_after_the_hour_gives_up_for_the_day(env):
    assert tool.run(["--window"], now=WED_8.replace(hour=9)) == tool.EXIT_DONE
    assert env["sent"] == []


def test_window_skips_weekend(env):
    saturday = datetime(2026, 10, 3, 8, 10)
    assert tool.run(["--window"], now=saturday) == tool.EXIT_DONE
    assert env["sent"] == []


# ── 2~4. 결과별 ──────────────────────────────────────────────────────────────


def test_relay_failure_retries_and_leaves_no_mark(env):
    env["state"]["ok"] = False
    assert tool.run(["--window"], now=WED_8) == tool.EXIT_FAILED
    assert _done_date(env) is None
    env["state"]["ok"] = True
    assert tool.run(["--window"], now=WED_8.replace(minute=20)) == tool.EXIT_DONE
    assert len(env["sent"]) == 2


def test_nobody_off_sends_nothing_but_finishes(env):
    env["state"]["message"] = None
    assert tool.run(["--window"], now=WED_8) == tool.EXIT_DONE
    assert env["sent"] == []
    assert _done_date(env) == "2026-09-30"


def test_not_configured_finishes_quietly(env, monkeypatch):
    monkeypatch.setattr(config, "RELAY_TOKEN", "")
    assert tool.run(["--window"], now=WED_8) == tool.EXIT_DONE
    assert env["sent"] == [] and env["state"]["collects"] == 0


def test_dry_run_prints_without_sending_or_marking(env, capsys):
    assert tool.run(["--dry-run"], now=WED_8) == tool.EXIT_DONE
    assert "홍길동" in capsys.readouterr().out
    assert env["sent"] == [] and _done_date(env) is None


# ── 5. 수집 실패 ─────────────────────────────────────────────────────────────


def test_collection_crash_still_sends(env, monkeypatch):
    def boom(_connection):
        raise RuntimeError("portal down")

    monkeypatch.setattr(tool.attendance_approvals, "collect_from_portal", boom)
    assert tool.run(["--window"], now=WED_8) == tool.EXIT_DONE
    assert len(env["sent"]) == 1


def test_dedup_key_is_one_per_day():
    assert tool.dedup_key(WED_8.date()) == "irms_leave_notice-2026-09-30"
