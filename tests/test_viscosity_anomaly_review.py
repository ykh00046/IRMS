"""점도 이상 '확인 처리' + 전 제품 이상 목록(2026-09-15 운영 결정).

계약:
  - 확인 처리 = 실제 이상으로 보고 조치했다는 표시. 통계에는 이상으로 남는다
    (counts.anomaly / overview.total_anomaly 불변), 미확인 건수에서만 빠진다.
  - 통계 제외(측정 실수)는 기존 그대로 책임자 전용, 제외 측정은 'excluded' 갈래.
  - 대시보드 viscosity_anomaly = overview.total_anomaly_unreviewed.
  - 확인 처리는 배합 작업자 세션 또는 로그인 사용자, 확인 취소는 책임자 전용.

하네스 주의(tests/test_viscosity_exclusion_routes.py 와 동일): 테스트 DB 는 실행 전체에서
공유되고 공용 admin 계정은 단일 session_token 을 쓴다. 라우트 테스트는 제품/측정을 DB 에
직접 심고, 보호 요청은 최소한으로 줄인다.
"""

import importlib
import sqlite3
import uuid

import pytest

from src.services import viscosity_service as vs


# ── 서비스 계층(in-memory DB) ───────────────────────────────────────
def _make_db(with_review_columns: bool = True) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    review_columns = (
        ", reviewed_at TEXT, reviewed_by TEXT, review_note TEXT"
        if with_review_columns
        else ""
    )
    connection.executescript(
        f"""
        CREATE TABLE viscosity_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            target REAL,
            lower_limit REAL,
            upper_limit REAL,
            sigma_k REAL NOT NULL DEFAULT 3,
            rpm REAL,
            temperature REAL,
            remind_daily INTEGER NOT NULL DEFAULT 0,
            use_reactor INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE viscosity_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            lot_no TEXT NOT NULL,
            viscosity REAL NOT NULL,
            measured_date TEXT,
            memo TEXT,
            recipe_material TEXT,
            material_lot TEXT,
            reactor INTEGER,
            created_by TEXT,
            created_at TEXT NOT NULL,
            blend_record_id INTEGER,
            excluded INTEGER NOT NULL DEFAULT 0,
            exclude_reason TEXT,
            excluded_by TEXT,
            excluded_at TEXT
            {review_columns}
        );
        CREATE UNIQUE INDEX idx_visc_readings_product_lot
            ON viscosity_readings(product_id, lot_no);
        CREATE TABLE audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            actor_user_id INTEGER,
            actor_username TEXT,
            actor_display_name TEXT,
            actor_access_level TEXT,
            target_type TEXT,
            target_id TEXT,
            target_label TEXT,
            details_json TEXT NOT NULL DEFAULT '{{}}',
            created_at TEXT NOT NULL
        );
        """
    )
    return connection


def _add_product(conn, code, lower=40.0, upper=60.0, active=1) -> dict:
    conn.execute(
        "INSERT INTO viscosity_products (code, name, lower_limit, upper_limit, sigma_k, "
        "is_active, created_at) VALUES (?, ?, ?, ?, 3, ?, '2026-01-01T00:00:00Z')",
        (code, code, lower, upper, active),
    )
    return vs.get_product_by_code(conn, code)


def _add(conn, product_id, lot, value, date):
    return vs.add_reading(
        conn, product_id=product_id, lot_no=lot, viscosity=value, measured_date=date,
        memo=None, recipe_material=None, material_lot=None,
        created_by="test", created_at="2026-01-01T00:00:00Z",
    )


_NOW = "2026-09-15T00:00:00Z"
_MANAGER = {"id": 1, "display_name": "홍책임", "username": "hong", "access_level": "manager"}


def _seed_scope(conn):
    """규격 40~60 제품 하나: 정상 2건 + 규격 밖 이상 2건(날짜 다름)."""
    p = _add_product(conn, "PB")
    _add(conn, p["id"], "26090001", 50.0, "2026-09-01")
    _add(conn, p["id"], "26090002", 51.0, "2026-09-02")
    a_old = _add(conn, p["id"], "26090003", 70.0, "2026-09-03")
    a_new = _add(conn, p["id"], "26090004", 30.0, "2026-09-04")
    return p, a_old, a_new


def test_unreviewed_list_contains_spec_anomalies_newest_first():
    conn = _make_db()
    p, a_old, a_new = _seed_scope(conn)

    result = vs.list_anomalies(conn, state="unreviewed")
    ids = [it["reading_id"] for it in result["items"]]
    assert ids == [a_new, a_old]  # 최신 측정일 먼저
    assert result["counts"] == {"unreviewed": 2, "reviewed": 0, "excluded": 0}
    assert result["state"] == "unreviewed"

    item = result["items"][0]
    assert item["product_id"] == p["id"]
    assert item["product_code"] == "PB"
    assert item["year"] == 2026
    assert item["lot_no"] == "26090004"
    assert item["status"] == "anomaly"
    assert item["side"] == "low"
    assert "spec_low" in item["reasons"]
    assert item["anomaly_low"] == 40.0 and item["anomaly_high"] == 60.0
    assert item["reviewed"] is False and item["excluded"] is False
    for key in ("reviewed_by", "reviewed_at", "review_note", "exclude_reason",
                "excluded_by", "excluded_at", "blend_record_id", "measured_date",
                "viscosity", "product_name"):
        assert key in item


def test_review_moves_item_and_keeps_total_anomaly():
    conn = _make_db()
    p, a_old, a_new = _seed_scope(conn)
    before = vs.overview(conn)
    assert before["total_anomaly"] == 2
    assert before["total_anomaly_unreviewed"] == 2
    assert before["items"][0]["anomaly_unreviewed_count"] == 2

    res = vs.review_reading(conn, a_old, "  재측정 후 폐기  ", by_name="김작업", now=_NOW)
    assert res is not None and res["id"] == a_old

    unreviewed = vs.list_anomalies(conn, state="unreviewed")
    reviewed = vs.list_anomalies(conn, state="reviewed")
    assert [it["reading_id"] for it in unreviewed["items"]] == [a_new]
    assert [it["reading_id"] for it in reviewed["items"]] == [a_old]
    assert reviewed["counts"] == {"unreviewed": 1, "reviewed": 1, "excluded": 0}
    assert unreviewed["counts"] == reviewed["counts"]
    item = reviewed["items"][0]
    assert item["reviewed"] is True
    assert item["reviewed_by"] == "김작업"
    assert item["reviewed_at"] == _NOW
    assert item["review_note"] == "재측정 후 폐기"

    after = vs.overview(conn)
    assert after["total_anomaly"] == 2  # 통계상 이상은 그대로
    assert after["total_anomaly_unreviewed"] == 1
    analysis = vs.analyze_product(conn, p, year=2026)
    assert analysis["counts"]["anomaly"] == 2
    assert analysis["counts"]["anomaly_unreviewed"] == 1

    audit = conn.execute(
        "SELECT target_label, details_json, actor_username FROM audit_logs "
        "WHERE action = 'viscosity_reading_reviewed' AND target_id = ?",
        (str(a_old),),
    ).fetchone()
    assert audit is not None
    assert audit["target_label"] == "26090003"
    assert "김작업" in audit["details_json"]
    assert audit["actor_username"] is None  # actor 미지정 = 작업자

    both = vs.list_anomalies(conn, state="all")
    assert {it["reading_id"] for it in both["items"]} == {a_old, a_new}


def test_review_note_too_short_raises_and_unknown_id_is_none():
    conn = _make_db()
    _, a_old, _ = _seed_scope(conn)
    with pytest.raises(ValueError):
        vs.review_reading(conn, a_old, " 가 ", by_name="김작업", now=_NOW)
    with pytest.raises(ValueError):
        vs.review_reading(conn, a_old, "", by_name="김작업", now=_NOW)
    assert vs.review_reading(conn, 999999, "조치함", by_name="김작업", now=_NOW) is None
    assert vs.unreview_reading(conn, 999999, by=_MANAGER, now=_NOW) is None


def test_unreview_restores_unreviewed_and_audits():
    conn = _make_db()
    _, a_old, _ = _seed_scope(conn)
    vs.review_reading(conn, a_old, "조치 완료", by_name="홍책임", now=_NOW, actor=_MANAGER)
    assert vs.overview(conn)["total_anomaly_unreviewed"] == 1

    res = vs.unreview_reading(conn, a_old, by=_MANAGER, now=_NOW)
    assert res is not None and res["id"] == a_old
    assert vs.overview(conn)["total_anomaly_unreviewed"] == 2
    row = conn.execute(
        "SELECT reviewed_at, reviewed_by, review_note FROM viscosity_readings WHERE id = ?",
        (a_old,),
    ).fetchone()
    assert tuple(row) == (None, None, None)
    reviewed_audit = conn.execute(
        "SELECT actor_username FROM audit_logs WHERE action = 'viscosity_reading_reviewed'"
    ).fetchone()
    assert reviewed_audit["actor_username"] == "hong"
    cleared = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_logs "
        "WHERE action = 'viscosity_reading_review_cleared' AND target_id = ?",
        (str(a_old),),
    ).fetchone()
    assert cleared["c"] == 1


def test_excluded_reading_listed_under_excluded_only():
    conn = _make_db()
    _, a_old, a_new = _seed_scope(conn)
    vs.exclude_reading(conn, a_new, "측정 실수", by=_MANAGER, now=_NOW)

    unreviewed = vs.list_anomalies(conn, state="unreviewed")
    excluded = vs.list_anomalies(conn, state="excluded")
    assert [it["reading_id"] for it in unreviewed["items"]] == [a_old]
    assert [it["reading_id"] for it in excluded["items"]] == [a_new]
    assert excluded["counts"] == {"unreviewed": 1, "reviewed": 0, "excluded": 1}
    item = excluded["items"][0]
    assert item["status"] == "excluded"
    assert item["excluded"] is True
    assert item["exclude_reason"] == "측정 실수"

    both = vs.list_anomalies(conn, state="all")
    assert {it["reading_id"] for it in both["items"]} == {a_old, a_new}


def test_invalid_state_raises():
    conn = _make_db()
    _seed_scope(conn)
    with pytest.raises(ValueError):
        vs.list_anomalies(conn, state="bogus")


def test_scope_active_products_and_explicit_product():
    conn = _make_db()
    _add_product(conn, "PB")
    idle = _add_product(conn, "OLD", active=0)
    rid = _add(conn, idle["id"], "25010001", 99.0, "2025-01-01")

    assert vs.list_anomalies(conn)["items"] == []  # 사용 안 함 제품은 기본 범위 밖
    only = vs.list_anomalies(conn, product_id=idle["id"])
    assert [it["reading_id"] for it in only["items"]] == [rid]
    assert only["items"][0]["year"] == 2025  # 연도 미지정 = 그 제품의 최신 연도
    assert vs.list_anomalies(conn, product_id=idle["id"], year=2026)["items"] == []


def test_old_schema_without_review_columns_still_analyzes():
    conn = _make_db(with_review_columns=False)
    p, _, _ = _seed_scope(conn)
    analysis = vs.analyze_product(conn, p)
    assert analysis["counts"]["anomaly"] == 2
    assert analysis["counts"]["anomaly_unreviewed"] == 2
    assert all(r["reviewed"] is False and r["review_note"] is None for r in analysis["readings"])
    assert vs.overview(conn)["total_anomaly_unreviewed"] == 2


# ── HTTP 라우트(공유 테스트 DB) ────────────────────────────────────
def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _csrf(client):
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _manager_client():
    client = _client()
    login = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert login.status_code == 200, login.text
    return client, _csrf(client)


def _worker_client():
    """배합 작업자 세션 개설(tests/test_blend_drafts_page.py 와 같은 절차)."""
    client = _client()
    worker = "점도확인" + uuid.uuid4().hex[:6]
    client.get("/api/blend/records")  # csrf 쿠키 확보
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200
    headers = _csrf(client)
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/auth/logout", headers=headers)
    client.get("/api/blend/records")
    headers = _csrf(client)
    res = client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    assert res.status_code == 200, res.text
    return client, _csrf(client), worker


def _seed_anomaly_direct():
    """규격 40~60 제품 + 규격 밖 측정 1건을 공유 DB 에 직접 삽입. (product_id, reading_id)."""
    from src.db import get_connection, utc_now_text

    code = "ANR" + uuid.uuid4().hex[:8].upper()
    now = utc_now_text()
    with get_connection() as conn:
        pid = int(
            conn.execute(
                "INSERT INTO viscosity_products "
                "(code, name, lower_limit, upper_limit, sigma_k, is_active, created_at) "
                "VALUES (?, ?, 40, 60, 3, 1, ?)",
                (code, code, now),
            ).lastrowid
        )
        rid = int(
            conn.execute(
                "INSERT INTO viscosity_readings "
                "(product_id, lot_no, viscosity, measured_date, created_by, created_at, excluded) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                (pid, f"{code}L1", 75.0, "2026-09-10", "test", now),
            ).lastrowid
        )
        conn.commit()
    return pid, rid


def test_review_route_with_blend_worker_records_worker_name():
    from src.db import get_connection

    client, headers, worker = _worker_client()
    _, rid = _seed_anomaly_direct()
    r = client.post(
        f"/api/viscosity/readings/{rid}/review",
        json={"note": "재측정 후 폐기"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "id": rid}
    with get_connection() as conn:
        row = conn.execute(
            "SELECT reviewed_by, review_note, reviewed_at FROM viscosity_readings WHERE id = ?",
            (rid,),
        ).fetchone()
        assert row["reviewed_by"] == worker
        assert row["review_note"] == "재측정 후 폐기"
        assert row["reviewed_at"]
        audit = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_logs "
            "WHERE action = 'viscosity_reading_reviewed' AND target_id = ?",
            (str(rid),),
        ).fetchone()
        assert audit["c"] >= 1


def test_review_route_anonymous_needs_registered_reviewer_not_401():
    # 점도 화면은 로그인 없이 쓴다 · 401 은 공용 request() 가 로그인 화면으로 옮겨 적던
    # 조치 내용을 잃게 했다. 이름이 없거나 명단에 없으면 400 으로 모달 안에 알린다.
    from src.db import get_connection, utc_now_text

    client = _client()
    client.get("/viscosity")  # csrftoken 쿠키 확보
    _, rid = _seed_anomaly_direct()
    no_name = client.post(
        f"/api/viscosity/readings/{rid}/review", json={"note": "조치함"}, headers=_csrf(client)
    )
    assert no_name.status_code == 400, no_name.text
    stranger = client.post(
        f"/api/viscosity/readings/{rid}/review",
        json={"note": "조치함", "reviewer": "없는사람" + uuid.uuid4().hex[:4]},
        headers=_csrf(client),
    )
    assert stranger.status_code == 400, stranger.text

    name = "현장확인" + uuid.uuid4().hex[:6]
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO workers (name, is_active, created_at) VALUES (?, 1, ?)",
            (name, utc_now_text()),
        )
        conn.commit()
    ok = client.post(
        f"/api/viscosity/readings/{rid}/review",
        json={"note": "재측정 후 정상", "reviewer": f" {name} "},
        headers=_csrf(client),
    )
    assert ok.status_code == 200, ok.text
    with get_connection() as conn:
        row = conn.execute(
            "SELECT reviewed_by FROM viscosity_readings WHERE id = ?", (rid,)
        ).fetchone()
    assert row["reviewed_by"] == name


def test_review_route_session_worker_wins_over_body_reviewer():
    from src.db import get_connection

    client, headers, worker = _worker_client()
    _, rid = _seed_anomaly_direct()
    r = client.post(
        f"/api/viscosity/readings/{rid}/review",
        json={"note": "조치함", "reviewer": "다른사람"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    with get_connection() as conn:
        row = conn.execute(
            "SELECT reviewed_by FROM viscosity_readings WHERE id = ?", (rid,)
        ).fetchone()
    assert row["reviewed_by"] == worker


def test_review_route_note_validation_and_unknown_id():
    client, headers, _ = _worker_client()
    short = client.post(
        "/api/viscosity/readings/1/review", json={"note": "가"}, headers=headers
    )
    assert short.status_code == 400, short.text
    empty = client.post(
        "/api/viscosity/readings/1/review", json={"note": ""}, headers=headers
    )
    assert empty.status_code == 422, empty.text
    missing = client.post(
        "/api/viscosity/readings/999999/review", json={"note": "조치함"}, headers=headers
    )
    assert missing.status_code == 404, missing.text


def test_unreview_route_anonymous_is_401():
    client = _client()
    client.get("/viscosity")
    r = client.post("/api/viscosity/readings/1/unreview", headers=_csrf(client))
    assert r.status_code == 401, r.text


def test_unreview_route_as_manager_clears_review():
    from src.db import get_connection

    client, headers = _manager_client()
    _, rid = _seed_anomaly_direct()
    with get_connection() as conn:
        conn.execute(
            "UPDATE viscosity_readings SET reviewed_at = ?, reviewed_by = ?, review_note = ? "
            "WHERE id = ?",
            ("2026-09-15T00:00:00Z", "김작업", "조치함", rid),
        )
        conn.commit()
    r = client.post(f"/api/viscosity/readings/{rid}/unreview", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "id": rid}
    with get_connection() as conn:
        row = conn.execute(
            "SELECT reviewed_at, reviewed_by, review_note FROM viscosity_readings WHERE id = ?",
            (rid,),
        ).fetchone()
        assert (row["reviewed_at"], row["reviewed_by"], row["review_note"]) == (None, None, None)


def test_anomalies_route_invalid_state_is_400_and_lists_seeded():
    client = _client()
    bad = client.get("/api/viscosity/anomalies?state=bogus")
    assert bad.status_code == 400, bad.text

    pid, rid = _seed_anomaly_direct()
    ok = client.get(f"/api/viscosity/anomalies?state=unreviewed&product_id={pid}")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert [it["reading_id"] for it in body["items"]] == [rid]
    assert body["counts"]["unreviewed"] == 1


def test_dashboard_summary_counts_unreviewed_anomalies():
    from src.db import get_connection

    client = _client()
    _seed_anomaly_direct()
    r = client.get("/api/dashboard/summary")
    assert r.status_code == 200, r.text
    with get_connection() as conn:
        expected = vs.overview(conn)["total_anomaly_unreviewed"]
    assert r.json()["viscosity_anomaly"] == expected
    assert expected >= 1
