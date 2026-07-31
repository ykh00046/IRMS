"""점도 측정 '통계 제외'(spec-out): 이상 측정 하나가 σ(이상을 잡아야 할 표준편차)를
스스로 오염시키는 문제를 끊는다.

핵심 계약:
  - 제외 측정은 삭제하지 않고 excluded=1 로만 표시한다.
  - 평균/σ/관리한계/추세/기간 집계는 유효(excluded=0) 측정만으로 계산한다.
  - readings[] 에는 제외 측정도 남기되 status='excluded' 로, anomalies/counts.anomaly
    에서는 빠지고 counts.excluded / stats.excluded_n 로 별도 집계한다.
  - 제외는 사유(reason) 필수.
"""

import sqlite3

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
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
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
    """values 를 하루씩 다른 측정일로 등록하고, 저장된 reading id 를 순서대로 반환."""
    ids = []
    for i, v in enumerate(values):
        rid = vs.add_reading(
            conn, product_id=product_id, lot_no=f"2601{start_seq + i:04d}",
            viscosity=v, measured_date=f"2026-01-{(i % 28) + 1:02d}",
            memo=None, recipe_material=None, material_lot=None,
            created_by="test", created_at="2026-01-01T00:00:00Z",
        )
        ids.append(rid)
    return ids


_MANAGER = {"display_name": "홍책임", "username": "hong", "access_level": "manager"}
_NOW = "2026-02-01T00:00:00Z"


# 1) 이상값 제외 → σ(및 target 없을 때 mean)가 실제로 변한다 = 유효-only 계산 증명.
def test_excluding_outlier_shrinks_std_and_mean():
    conn = _make_db()
    # target/spec 미설정 → 중심선은 표본 평균. 정상 8개(σ 판정 활성 최소 표본) + 이상 1개.
    p = _add_product(conn, "PB")
    normals = [49.0, 49.1, 48.9, 49.05, 49.15, 48.95, 49.0, 49.1]
    ids = _seed(conn, p["id"], normals)
    outlier_id = _seed(conn, p["id"], [90.0], start_seq=100)[0]

    before = vs.analyze_product(conn, p)
    std_before = before["stats"]["std"]
    mean_before = before["stats"]["mean"]
    assert before["stats"]["n"] == 9  # 이상 포함 전부 유효

    # 이상값을 통계에서 제외.
    res = vs.exclude_reading(conn, outlier_id, "명백한 측정 오류", by=_MANAGER, now=_NOW)
    assert res is not None and res["id"] == outlier_id

    after = vs.analyze_product(conn, p)
    std_after = after["stats"]["std"]
    mean_after = after["stats"]["mean"]

    # 이상값이 빠지면서 σ 와 평균 모두 크게 떨어져야 한다(자기 오염 제거 증명).
    assert std_after < std_before
    assert mean_after < mean_before
    assert after["stats"]["n"] == 8            # 유효 표본만 카운트
    assert after["stats"]["excluded_n"] == 1


# 2) 제외 측정은 anomalies/counts.anomaly 에서 빠지고, readings 에는 status='excluded'
#    로 남으며 counts.excluded / stats.excluded_n 로 잡힌다.
def test_excluded_reading_leaves_anomalies_but_stays_in_readings():
    conn = _make_db()
    # 규격(spec)을 준다 — 규격은 표본과 무관한 공학 기준이라, σ 가 이상치로 오염돼도
    # 90 은 언제나 spec_high 로 잡힌다(제외 전 anomaly 임을 보장).
    p = _add_product(conn, "PB", target=49.0, lower_limit=45.0, upper_limit=55.0)
    normals = [49.0, 49.1, 48.9, 49.05, 49.15, 48.95, 49.0, 49.1]
    _seed(conn, p["id"], normals)
    outlier_id = _seed(conn, p["id"], [90.0], start_seq=100)[0]

    # 제외 전: 이상값은 anomaly 로 잡힌다.
    before = vs.analyze_product(conn, p)
    assert before["counts"]["anomaly"] >= 1
    assert any(a["id"] == outlier_id for a in before["anomalies"])

    vs.exclude_reading(conn, outlier_id, "이상치 제외", by=_MANAGER, now=_NOW)
    after = vs.analyze_product(conn, p)

    # anomalies 에서 사라진다.
    assert all(a["id"] != outlier_id for a in after["anomalies"])
    # counts.anomaly 에서 빠지고 counts.excluded 로 이동.
    assert after["counts"]["anomaly"] == 0
    assert after["counts"]["excluded"] == 1
    assert after["stats"]["excluded_n"] == 1

    # readings 에는 그대로 남고 status='excluded' + 사유/책임자 노출.
    excluded_item = next(r for r in after["readings"] if r["id"] == outlier_id)
    assert excluded_item["status"] == "excluded"
    assert excluded_item["excluded"] is True
    assert excluded_item["exclude_reason"] == "이상치 제외"
    assert excluded_item["excluded_by"] == "홍책임"
    assert excluded_item["excluded_at"] == _NOW
    # 제외 측정은 spec/σ 판정을 건너뛰므로 reasons 없음.
    assert excluded_item["reasons"] == []


# 3) include_reading 은 제외를 해제해 다시 통계에 포함시킨다.
def test_include_restores_reading_into_statistics():
    conn = _make_db()
    p = _add_product(conn, "PB")
    normals = [49.0, 49.1, 48.9, 49.05, 49.15, 48.95, 49.0, 49.1]
    _seed(conn, p["id"], normals)
    outlier_id = _seed(conn, p["id"], [90.0], start_seq=100)[0]

    baseline = vs.analyze_product(conn, p)  # 전부 포함
    vs.exclude_reading(conn, outlier_id, "일시 제외", by=_MANAGER, now=_NOW)
    excluded = vs.analyze_product(conn, p)
    assert excluded["stats"]["n"] == 8

    restored = vs.include_reading(conn, outlier_id, by=_MANAGER, now=_NOW)
    assert restored is not None
    after = vs.analyze_product(conn, p)

    # 통계가 원래(전부 포함)와 같아진다.
    assert after["stats"]["n"] == baseline["stats"]["n"] == 9
    assert after["stats"]["std"] == baseline["stats"]["std"]
    assert after["stats"]["mean"] == baseline["stats"]["mean"]
    assert after["stats"]["excluded_n"] == 0
    assert after["counts"]["excluded"] == 0
    # 제외 메타가 비워진다.
    row = conn.execute(
        "SELECT excluded, exclude_reason, excluded_by, excluded_at "
        "FROM viscosity_readings WHERE id = ?",
        (outlier_id,),
    ).fetchone()
    assert row["excluded"] == 0
    assert row["exclude_reason"] is None
    assert row["excluded_by"] is None
    assert row["excluded_at"] is None


# 4) 제외는 사유 필수 — 빈 사유는 거부한다.
def test_exclude_requires_nonempty_reason():
    conn = _make_db()
    p = _add_product(conn, "PB")
    rid = _seed(conn, p["id"], [49.0])[0]

    with pytest.raises(ValueError):
        vs.exclude_reading(conn, rid, "", by=_MANAGER, now=_NOW)
    with pytest.raises(ValueError):
        vs.exclude_reading(conn, rid, "   ", by=_MANAGER, now=_NOW)

    # 사유 없이 제외되지 않았음.
    row = conn.execute(
        "SELECT excluded FROM viscosity_readings WHERE id = ?", (rid,)
    ).fetchone()
    assert row["excluded"] == 0


# 알 수 없는 id 는 None 반환(라우트에서 404 로 매핑).
def test_exclude_and_include_unknown_id_returns_none():
    conn = _make_db()
    assert vs.exclude_reading(conn, 9999, "사유", by=_MANAGER, now=_NOW) is None
    assert vs.include_reading(conn, 9999, by=_MANAGER, now=_NOW) is None


# 감사 로그가 남는다(제외/복원).
def test_exclude_and_include_write_audit_rows():
    conn = _make_db()
    p = _add_product(conn, "PB")
    rid = _seed(conn, p["id"], [49.0])[0]

    vs.exclude_reading(conn, rid, "이상치", by=_MANAGER, now=_NOW)
    vs.include_reading(conn, rid, by=_MANAGER, now=_NOW)

    actions = [
        r["action"]
        for r in conn.execute("SELECT action FROM audit_logs ORDER BY id").fetchall()
    ]
    assert "viscosity_reading_excluded" in actions
    assert "viscosity_reading_restored" in actions
