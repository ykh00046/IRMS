"""Unit + route tests for viscosity-analysis.

Design: docs/02-design/features/viscosity-analysis.design.md
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from src.services import viscosity_service as vs


def _make_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE viscosity_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            target REAL,
            lower_limit REAL,
            upper_limit REAL,
            sigma_k REAL NOT NULL DEFAULT 3,
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
            created_by TEXT,
            created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX idx_visc_readings_product_lot
            ON viscosity_readings(product_id, lot_no);
        """
    )
    return connection


def _add_product(conn, code="PB", **kw) -> dict:
    conn.execute(
        "INSERT INTO viscosity_products (code, name, target, lower_limit, upper_limit, "
        "sigma_k, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, '2026-01-01T00:00:00Z')",
        (
            code, kw.get("name", code), kw.get("target"),
            kw.get("lower_limit"), kw.get("upper_limit"), kw.get("sigma_k", 3),
        ),
    )
    return vs.get_product_by_code(conn, code)


def _seed(conn, product_id, values, start_seq=1):
    for i, v in enumerate(values):
        vs.add_reading(
            conn, product_id=product_id, lot_no=f"2601{start_seq + i:04d}",
            viscosity=v, measured_date=f"2026-01-{(i % 28) + 1:02d}",
            memo=None, recipe_material=None, material_lot=None,
            created_by="test", created_at="2026-01-01T00:00:00Z",
        )


# ── LOT 날짜 파서 ────────────────────────────────────────────────
def test_parse_lot_date_8digit():
    assert vs.parse_lot_date(26010701) == "2026-01-07"  # PB YYMMDD+seq


def test_parse_lot_date_6digit():
    assert vs.parse_lot_date(260106) == "2026-01-06"  # SBCT YYMMDD


def test_parse_lot_date_datetime():
    assert vs.parse_lot_date(datetime(2026, 3, 14)) == "2026-03-14"


def test_parse_lot_date_iso_string():
    assert vs.parse_lot_date("2026-02-28 00:00:00") == "2026-02-28"


def test_parse_lot_date_invalid():
    assert vs.parse_lot_date("ABC") is None
    assert vs.parse_lot_date(None) is None


# ── 통계 관리한계 + 이상 판정 ───────────────────────────────────
def test_sigma_anomaly_detected():
    """평균에서 멀리 떨어진 값은 ±kσ 위반 → anomaly."""
    conn = _make_db()
    p = _add_product(conn, "PB", sigma_k=3)
    _seed(conn, p["id"], [49.0] * 10 + [60.0])  # 60은 명백한 outlier
    result = vs.analyze_product(conn, p)
    assert result["counts"]["anomaly"] == 1
    top = result["anomalies"][0]
    assert top["viscosity"] == 60.0
    assert "sigma_high" in top["reasons"]
    assert top["side"] == "high"


def test_spec_limit_anomaly():
    """관리 상/하한을 벗어나면 표준편차와 무관하게 anomaly."""
    conn = _make_db()
    p = _add_product(conn, "SBCT", lower_limit=195, upper_limit=215)
    _seed(conn, p["id"], [200, 205, 210, 218])  # 218 > 215
    result = vs.analyze_product(conn, p)
    assert result["counts"]["anomaly"] == 1
    assert "spec_high" in result["anomalies"][0]["reasons"]


def test_target_used_as_center_when_set():
    """target 설정 시 중심선은 평균이 아니라 target."""
    conn = _make_db()
    p = _add_product(conn, "PB", target=50)
    _seed(conn, p["id"], [49, 49, 49, 49])
    result = vs.analyze_product(conn, p)
    assert result["stats"]["center"] == 50.0


def test_warn_zone():
    """2σ 초과 ~ kσ 이하는 anomaly 가 아니라 warn."""
    conn = _make_db()
    p = _add_product(conn, "PB", sigma_k=3)
    # center≈50.2, std≈0.632 → uwl≈51.46, ucl≈52.10. 52.0 은 2σ~3σ 경고 구간.
    _seed(conn, p["id"], [50] * 9 + [52.0])
    result = vs.analyze_product(conn, p)
    assert result["counts"]["warn"] == 1
    assert result["counts"]["anomaly"] == 0


def test_normal_when_no_spec_and_low_variance():
    conn = _make_db()
    p = _add_product(conn, "PB")
    _seed(conn, p["id"], [49, 49, 49, 49, 49])
    result = vs.analyze_product(conn, p)
    assert result["counts"]["anomaly"] == 0
    assert result["counts"]["warn"] == 0


# ── 추세 룰 ─────────────────────────────────────────────────────
def test_run_up_trend():
    """연속 상승 5회 → run_up 추세 경보."""
    conn = _make_db()
    p = _add_product(conn, "PB")
    _seed(conn, p["id"], [48, 48.5, 49, 49.5, 50, 50.5])
    result = vs.analyze_product(conn, p)
    types = [t["type"] for t in result["trends"]]
    assert "run_up" in types


# ── 등록 + 멱등성 ───────────────────────────────────────────────
def test_add_reading_derives_date_from_lot():
    conn = _make_db()
    p = _add_product(conn, "PB")
    vs.add_reading(
        conn, product_id=p["id"], lot_no="26010701", viscosity=49.0,
        measured_date=None, memo=None, recipe_material=None, material_lot=None,
        created_by="t", created_at="2026-06-23T00:00:00Z",
    )
    row = conn.execute("SELECT measured_date FROM viscosity_readings").fetchone()
    assert row["measured_date"] == "2026-01-07"


def test_duplicate_lot_rejected():
    conn = _make_db()
    p = _add_product(conn, "PB")
    kw = dict(
        product_id=p["id"], lot_no="26010701", viscosity=49.0, measured_date=None,
        memo=None, recipe_material=None, material_lot=None, created_by="t",
        created_at="2026-06-23T00:00:00Z",
    )
    vs.add_reading(conn, **kw)
    with pytest.raises(sqlite3.IntegrityError):
        vs.add_reading(conn, **kw)


# ── 기간별(분기/월) 집계 ────────────────────────────────────────
def test_period_key_quarter_and_month():
    assert vs._period_key("2026-03-14", "quarter") == "2026-Q1"
    assert vs._period_key("2026-04-01", "quarter") == "2026-Q2"
    assert vs._period_key("2026-03-14", "month") == "2026-03"
    assert vs._period_key(None, "quarter") is None
    assert vs._period_key("bad", "quarter") is None


def test_quarterly_summary_with_delta():
    """분기별 평균 + 전기대비(mean_delta) 계산."""
    conn = _make_db()
    p = _add_product(conn, "PB")
    # Q1: 1~3월, Q2: 4~6월 — 측정일을 직접 지정
    specs = [
        ("26010101", 48.0, "2026-01-10"),
        ("26020101", 50.0, "2026-02-10"),
        ("26040101", 52.0, "2026-04-10"),
        ("26050101", 54.0, "2026-05-10"),
    ]
    for lot, v, d in specs:
        vs.add_reading(
            conn, product_id=p["id"], lot_no=lot, viscosity=v, measured_date=d,
            memo=None, recipe_material=None, material_lot=None,
            created_by="t", created_at="2026-06-23T00:00:00Z",
        )
    result = vs.analyze_product(conn, p, granularity="quarter")
    periods = result["periods"]
    assert [x["period"] for x in periods] == ["2026-Q1", "2026-Q2"]
    assert periods[0]["mean"] == 49.0  # (48+50)/2
    assert periods[0]["count"] == 2
    assert periods[0]["mean_delta"] is None
    assert periods[1]["mean"] == 53.0  # (52+54)/2
    assert periods[1]["mean_delta"] == 4.0  # 53 - 49


def test_monthly_granularity():
    conn = _make_db()
    p = _add_product(conn, "PB")
    for lot, d in [("26010101", "2026-01-05"), ("26020101", "2026-02-05")]:
        vs.add_reading(
            conn, product_id=p["id"], lot_no=lot, viscosity=49.0, measured_date=d,
            memo=None, recipe_material=None, material_lot=None,
            created_by="t", created_at="2026-06-23T00:00:00Z",
        )
    periods = vs.analyze_product(conn, p, granularity="month")["periods"]
    assert [x["period"] for x in periods] == ["2026-01", "2026-02"]


# ── 기간 알림 (이상 급증 / 평균 이동) ──────────────────────────
def test_period_anomaly_spike_alert():
    """spec 위반 다수가 한 분기에 몰리면 anomaly_spike 경보."""
    conn = _make_db()
    p = _add_product(conn, "PB", upper_limit=50)
    # Q1 정상, Q2 에 상한 초과 3건
    rows = [
        ("26010101", 49, "2026-01-10"), ("26020101", 49, "2026-02-10"),
        ("26040101", 55, "2026-04-10"), ("26050101", 56, "2026-05-10"),
        ("26060101", 57, "2026-06-10"),
    ]
    for lot, v, d in rows:
        vs.add_reading(
            conn, product_id=p["id"], lot_no=lot, viscosity=v, measured_date=d,
            memo=None, recipe_material=None, material_lot=None,
            created_by="t", created_at="2026-06-23T00:00:00Z",
        )
    alerts = vs.analyze_product(conn, p, granularity="quarter")["period_alerts"]
    spikes = [a for a in alerts if a["type"] == "anomaly_spike"]
    assert spikes and spikes[0]["period"] == "2026-Q2"
    assert spikes[0]["anomaly_count"] == 3


def test_period_mean_shift_alert():
    """전기대비 평균이 전체 σ 이상 이동하면 mean_shift 경보."""
    conn = _make_db()
    p = _add_product(conn, "PB")
    rows = [
        ("26010101", 40, "2026-01-10"), ("26020101", 40, "2026-02-10"),
        ("26040101", 60, "2026-04-10"), ("26050101", 60, "2026-05-10"),
    ]
    for lot, v, d in rows:
        vs.add_reading(
            conn, product_id=p["id"], lot_no=lot, viscosity=v, measured_date=d,
            memo=None, recipe_material=None, material_lot=None,
            created_by="t", created_at="2026-06-23T00:00:00Z",
        )
    alerts = vs.analyze_product(conn, p, granularity="quarter")["period_alerts"]
    shifts = [a for a in alerts if a["type"].startswith("mean_shift")]
    assert shifts and shifts[0]["type"] == "mean_shift_up"


# ── 연도별 기준 ─────────────────────────────────────────────────
def _add_dated(conn, pid, lot, v, d):
    vs.add_reading(
        conn, product_id=pid, lot_no=lot, viscosity=v, measured_date=d,
        memo=None, recipe_material=None, material_lot=None,
        created_by="t", created_at="2026-06-23T00:00:00Z",
    )


def test_available_years_desc():
    conn = _make_db()
    p = _add_product(conn, "N-TOP")
    _add_dated(conn, p["id"], "a", 165, "2024-03-04")
    _add_dated(conn, p["id"], "b", 144, "2025-01-09")
    _add_dated(conn, p["id"], "c", 131, "2026-05-04")
    assert vs.available_years(conn, p["id"]) == [2026, 2025, 2024]


def test_year_filtered_baseline():
    """같은 제품이라도 연도별로 중심선/표본이 분리된다."""
    conn = _make_db()
    p = _add_product(conn, "N-TOP")
    # 2024 대역 ~165, 2026 대역 ~131
    for i, v in enumerate([160, 165, 170]):
        _add_dated(conn, p["id"], f"24{i}", v, f"2024-03-0{i+1}")
    for i, v in enumerate([130, 131, 132]):
        _add_dated(conn, p["id"], f"26{i}", v, f"2026-05-0{i+1}")

    a24 = vs.analyze_product(conn, p, year=2024)
    a26 = vs.analyze_product(conn, p, year=2026)
    assert a24["stats"]["n"] == 3 and a24["stats"]["mean"] == 165.0
    assert a26["stats"]["n"] == 3 and a26["stats"]["mean"] == 131.0
    assert a24["year"] == 2024 and a26["year"] == 2026
    # 전체(year=None)는 두 대역을 합쳐 σ가 매우 커진다
    allp = vs.analyze_product(conn, p)
    assert allp["stats"]["n"] == 6
    assert allp["stats"]["std"] > a24["stats"]["std"]


def test_year_granularity_buckets():
    conn = _make_db()
    p = _add_product(conn, "SBCT")
    _add_dated(conn, p["id"], "a", 200, "2024-03-04")
    _add_dated(conn, p["id"], "b", 210, "2024-05-04")
    _add_dated(conn, p["id"], "c", 198, "2025-01-09")
    periods = vs.analyze_product(conn, p, granularity="year")["periods"]
    assert [x["period"] for x in periods] == ["2024", "2025"]
    assert periods[0]["mean"] == 205.0
    assert periods[1]["mean_delta"] == -7.0  # 198 - 205


# ── 신규 입력 즉시 판정 ─────────────────────────────────────────
def test_classify_value_for_new_input():
    """입력 전 표본 기준으로 신규 값을 판정 (등록 즉시 경고용)."""
    conn = _make_db()
    p = _add_product(conn, "PB", upper_limit=50)
    _seed(conn, p["id"], [49, 49, 49])
    verdict = vs.classify_value(conn, p, 55.0)
    assert verdict["status"] == "anomaly"
    assert "spec_high" in verdict["reasons"]
    normal = vs.classify_value(conn, p, 49.0)
    assert normal["status"] == "normal"


# ── overview 집계 ───────────────────────────────────────────────
def test_overview_aggregates_anomaly_count():
    conn = _make_db()
    p1 = _add_product(conn, "PB", sigma_k=3)
    _seed(conn, p1["id"], [49.0] * 10 + [60.0])
    _add_product(conn, "SBCT")
    ov = vs.overview(conn)
    assert ov["product_count"] == 2
    assert ov["total_anomaly"] == 1
    pb = next(it for it in ov["items"] if it["code"] == "PB")
    assert pb["anomaly_count"] == 1
    assert pb["latest_value"] == 60.0


# ── 라우트 인증 ─────────────────────────────────────────────────
def test_viscosity_route_is_public():
    """점도 조회는 로그인 없이 접근 가능(사내 공용 단말 운영 편의)."""
    import importlib

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    res = client.get("/api/viscosity/overview")
    assert res.status_code == 200
    # 마이그레이션 시드 제품(PB/SBCT/SCRA)이 비로그인으로도 보인다
    codes = {it["code"] for it in res.json()["items"]}
    assert {"PB", "SBCT", "SCRA"} <= codes


def test_viscosity_page_open_without_login():
    """/viscosity 페이지가 로그인 리다이렉트 없이 200 으로 열린다."""
    import importlib

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    res = client.get("/viscosity")
    assert res.status_code == 200
    assert "visc-chart" in res.text
