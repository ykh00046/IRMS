"""시험 배합 LOT 의 점도 — docs/test-blend-design.md §9 (패키지 C).

시험 점도는 **등록은 정식과 같게**(정정 유예·측정 불가·삭제·LOT 단건 조회) 되면서
정식 통계·알림은 오염하지 않아야 한다. 두 성질이 서로 반대라 한쪽만 지켜지기 쉬우므로
여기서 양쪽을 동시에 못 박는다.

등록(§9-1)  LOT 이 시험 배합 기록의 제품 LOT 이면 is_test=1 로 저장
대기열(§9-2) 기본은 정식만 · test=1 은 기준 레시피로 이은 시험 기록만
격리(§9-3)  analyze_product·overview·list_anomalies·summarize·관리한계·
            daily_reading_reminders(pending_lots) 에서 제외
포함(§9-4)  product_lot_alert · list_readings_for_blend 는 시험도 본다
조회(§9)    /api/blend/records?test=only 는 시험 기록만
"""

from __future__ import annotations

import importlib
import sqlite3
import uuid

from src.services import viscosity_service as vs

# ── 단위 테스트용 스키마 ────────────────────────────────────────────
# 실제 마이그레이션이 만드는 컬럼 중 이 기능이 읽는 것만 담는다
# (viscosity_readings.is_test · blend_records.is_test/base_recipe_id).
_SCHEMA = """
CREATE TABLE viscosity_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    target REAL,
    lower_limit REAL,
    upper_limit REAL,
    warn_low REAL,
    warn_high REAL,
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
    excluded_at TEXT,
    reviewed_at TEXT,
    reviewed_by TEXT,
    review_note TEXT,
    is_test INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX idx_visc_readings_product_lot
    ON viscosity_readings(product_id, lot_no);
CREATE TABLE blend_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_lot TEXT NOT NULL,
    recipe_id INTEGER,
    product_name TEXT NOT NULL,
    worker TEXT NOT NULL,
    work_date TEXT NOT NULL,
    total_amount REAL NOT NULL,
    reactor INTEGER,
    status TEXT NOT NULL DEFAULT 'completed',
    is_bulk_regenerated INTEGER NOT NULL DEFAULT 0,
    is_test INTEGER NOT NULL DEFAULT 0,
    base_recipe_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE viscosity_skips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    blend_record_id INTEGER NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT NOT NULL
);
"""


def _make_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(_SCHEMA)
    return connection


def _add_product(conn, code="PB", **kw) -> dict:
    conn.execute(
        "INSERT INTO viscosity_products (code, name, target, lower_limit, upper_limit, "
        "sigma_k, remind_daily, is_active, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, '2026-01-01T00:00:00Z')",
        (
            code,
            kw.get("name", code),
            kw.get("target"),
            kw.get("lower_limit"),
            kw.get("upper_limit"),
            kw.get("sigma_k", 3),
            1 if kw.get("remind_daily") else 0,
        ),
    )
    return vs.get_product_by_code(conn, code)


def _add_blend(conn, lot, *, product_name, is_test=0, work_date="2026-01-10", base_recipe_id=None):
    cur = conn.execute(
        "INSERT INTO blend_records (product_lot, product_name, worker, work_date, "
        "total_amount, is_test, base_recipe_id, created_at) "
        "VALUES (?, ?, '작업자', ?, 100, ?, ?, '2026-01-10T00:00:00Z')",
        (lot, product_name, work_date, is_test, base_recipe_id),
    )
    return int(cur.lastrowid)


def _add_reading(conn, product_id, lot, value, **kw):
    return vs.add_reading(
        conn,
        product_id=product_id,
        lot_no=lot,
        viscosity=value,
        measured_date=kw.get("measured_date", "2026-01-10"),
        memo=None,
        recipe_material=None,
        material_lot=kw.get("material_lot"),
        created_by="시험",
        created_at="2026-01-10T00:00:00Z",
        blend_record_id=kw.get("blend_record_id"),
        reactor=kw.get("reactor"),
    )


def _is_test(conn, reading_id) -> int:
    return conn.execute(
        "SELECT is_test FROM viscosity_readings WHERE id = ?", (reading_id,)
    ).fetchone()["is_test"]


# ── §9-1 등록 ───────────────────────────────────────────────────────
def test_add_reading_marks_test_when_lot_is_a_test_blend():
    """LOT 이 시험 배합 기록의 제품 LOT 이면 is_test=1, 정식 LOT 은 0."""
    conn = _make_db()
    product = _add_product(conn, "PB")
    _add_blend(conn, "PB26011001", product_name="PB")
    test_record = _add_blend(conn, "T-PB시험26011001", product_name="PB시험", is_test=1)

    real_id = _add_reading(conn, product["id"], "PB26011001", 49.0)
    test_id = _add_reading(
        conn, product["id"], "T-PB시험26011001", 12.0, blend_record_id=test_record
    )
    assert _is_test(conn, real_id) == 0
    assert _is_test(conn, test_id) == 1


def test_add_reading_marks_test_by_lot_without_blend_record_id():
    """연계 없이(엑셀 임포트·직접 등록) 들어와도 LOT 이 시험이면 시험이다."""
    conn = _make_db()
    product = _add_product(conn, "PB")
    _add_blend(conn, "T-PB시험26011001", product_name="PB시험", is_test=1)
    rid = _add_reading(conn, product["id"], "T-PB시험26011001", 12.0)
    assert _is_test(conn, rid) == 1


def test_add_reading_survives_schema_without_is_test():
    """is_test 컬럼·blend_records 가 없는 최소 스키마에서도 등록이 깨지지 않는다."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE viscosity_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT,
            target REAL, lower_limit REAL, upper_limit REAL, sigma_k REAL DEFAULT 3,
            rpm REAL, temperature REAL, use_reactor INTEGER DEFAULT 0,
            remind_daily INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, created_at TEXT
        );
        CREATE TABLE viscosity_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, lot_no TEXT,
            viscosity REAL, measured_date TEXT, memo TEXT, recipe_material TEXT,
            material_lot TEXT, reactor INTEGER, created_by TEXT, created_at TEXT,
            blend_record_id INTEGER, excluded INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO viscosity_products (code, name, created_at) "
        "VALUES ('PB', 'PB', '2026-01-01T00:00:00Z')"
    )
    product = vs.get_product_by_code(conn, "PB")
    rid = _add_reading(conn, product["id"], "PB26011001", 49.0)
    assert rid > 0
    assert vs.analyze_product(conn, product)["stats"]["n"] == 1


# ── §9-3 격리 ───────────────────────────────────────────────────────
def _seed_normal(conn, product_id, values, start=1):
    for i, value in enumerate(values):
        _add_reading(
            conn, product_id, f"PB2601{start + i:04d}", value,
            measured_date=f"2026-01-{(i % 28) + 1:02d}",
        )


def test_test_reading_ignored_by_stats_and_anomalies():
    """규격을 크게 벗어난 시험 값이 평균·σ·이상 건수·기간 집계에 들어가지 않는다."""
    conn = _make_db()
    product = _add_product(conn, "PB", target=49, lower_limit=45, upper_limit=53)
    _seed_normal(conn, product["id"], [49.0, 49.1, 48.9, 49.2, 49.0, 48.8, 49.3, 49.1])
    baseline = vs.analyze_product(conn, product)

    _add_blend(conn, "T-PB시험26011001", product_name="PB시험", is_test=1)
    _add_reading(conn, product["id"], "T-PB시험26011001", 200.0)

    after = vs.analyze_product(conn, product)
    assert after["stats"]["n"] == baseline["stats"]["n"] == 8
    assert after["stats"]["mean"] == baseline["stats"]["mean"]
    assert after["stats"]["std"] == baseline["stats"]["std"]
    assert after["counts"]["anomaly"] == 0
    assert all(not r["is_test"] for r in after["readings"])
    assert not any(r["lot_no"].startswith("T-") for r in after["readings"])
    # 기간 집계(summarize_periods)도 유효 측정만 본다.
    assert sum(p["count"] for p in after["periods"]) == 8

    # 전 제품 요약·이상 목록도 같다.
    assert vs.overview(conn)["total_anomaly"] == 0
    assert vs.list_anomalies(conn, state="all")["items"] == []

    # 연도 탭에도 시험만 있는 해가 끼어들지 않는다.
    assert vs.available_years(conn, product["id"]) == [2026]


def test_test_reading_omitted_from_reminder_pending_lots():
    """트레이가 읽는 pending_lots 에 시험 LOT 이 절대 섞이지 않는다.

    시험명이 우연히 반제품 이름과 같아도 막힌다(product_name 조건만으로는 뚫린다).
    """
    conn = _make_db()
    product = _add_product(conn, "PB", remind_daily=True)
    _add_blend(conn, "PB26011001", product_name="PB", work_date="2026-01-10")
    _add_blend(conn, "T-PB26011001", product_name="PB", is_test=1, work_date="2026-01-10")

    items = vs.daily_reading_reminders(conn, target_date="2026-01-11")
    assert len(items) == 1
    lots = [lot["product_lot"] for lot in items[0]["pending_lots"]]
    assert lots == ["PB26011001"]
    assert items[0]["pending_count"] == 1


def test_reminder_latest_value_ignores_test_reading():
    """알림 항목의 '최근 점도'도 정식 측정만 본다."""
    conn = _make_db()
    product = _add_product(conn, "PB", remind_daily=True)
    _add_blend(conn, "PB26011001", product_name="PB", work_date="2026-01-10")
    _seed_normal(conn, product["id"], [49.0], start=50)   # 2026-01-01
    _add_blend(conn, "T-PB시험26012001", product_name="PB시험", is_test=1)
    _add_reading(
        conn, product["id"], "T-PB시험26012001", 300.0, measured_date="2026-01-20"
    )

    items = vs.daily_reading_reminders(conn, target_date="2026-01-11")
    assert len(items) == 1
    assert items[0]["latest_value"] == 49.0


# ── §9-4 포함 ───────────────────────────────────────────────────────
def test_lot_lookups_still_see_test_readings():
    """LOT 단건 조회는 시험도 본다 — 작업자에게는 '그 LOT 이 실제로 잰 값'이 중요하다."""
    conn = _make_db()
    product = _add_product(conn, "PB", target=49, lower_limit=45, upper_limit=53)
    record_id = _add_blend(conn, "T-PB시험26011001", product_name="PB시험", is_test=1)
    _add_reading(
        conn, product["id"], "T-PB시험26011001", 40.0, blend_record_id=record_id
    )

    alert = vs.product_lot_alert(conn, "PB", "T-PB시험26011001")
    assert alert["found"] is True
    assert alert["viscosity"] == 40.0
    assert alert["level"] == "anomaly"

    linked = vs.list_readings_for_blend(conn, record_id)
    assert [r["viscosity"] for r in linked] == [40.0]
    assert linked[0]["is_test"] is True


# ── §9-5 시험 보기 ──────────────────────────────────────────────────
def test_test_readings_view_lists_only_test_values_without_verdicts():
    conn = _make_db()
    product = _add_product(conn, "PB", target=49, lower_limit=45, upper_limit=53)
    _seed_normal(conn, product["id"], [49.0, 49.1])
    _add_blend(conn, "T-PB시험26011001", product_name="PB시험", is_test=1)
    _add_blend(conn, "T-PB시험26011002", product_name="PB시험", is_test=1)
    _add_reading(conn, product["id"], "T-PB시험26011001", 120.0)
    _add_reading(conn, product["id"], "T-PB시험26011002", 130.0)

    view = vs.test_readings(conn, product)
    assert view["count"] == 2
    assert [it["viscosity"] for it in view["items"]] == [120.0, 130.0]
    assert view["mean"] == 125.0
    # 판정·σ 는 붙이지 않는다.
    assert all("status" not in it for it in view["items"])
    assert all(it["is_test"] for it in view["items"])
    assert view["product"]["lower_limit"] == 45


# ── §9-6 정정·삭제 ──────────────────────────────────────────────────
def test_exclude_and_delete_work_on_test_readings():
    """통계 제외·삭제 서비스는 시험 측정에도 그대로 동작한다(id 로 찾는다)."""
    conn = _make_db()
    # 제외·복원은 감사 로그를 남긴다(tests/test_viscosity_exclusion.py 와 같은 스키마).
    conn.executescript(
        """
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
    product = _add_product(conn, "PB")
    _add_blend(conn, "T-PB시험26011001", product_name="PB시험", is_test=1)
    rid = _add_reading(conn, product["id"], "T-PB시험26011001", 120.0)

    assert vs.exclude_reading(conn, rid, "시험 재측정", by="책임자", now="t")["id"] == rid
    assert vs.test_readings(conn, product)["count"] == 1
    conn.execute("DELETE FROM viscosity_readings WHERE id = ?", (rid,))
    assert vs.test_readings(conn, product)["count"] == 0


# ── 라우트(§9-2 대기열 · §9 기록 조회) ──────────────────────────────
def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _uid() -> str:
    return uuid.uuid4().hex[:6].upper()


def _mgmt_client():
    client = _client()
    assert client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    ).status_code == 200

    def csrf():
        token = client.cookies.get("csrftoken")
        return {"x-csrftoken": token} if token else {}

    return client, csrf


def _worker_session(client, csrf, worker):
    client.get("/api/blend/records")
    client.post("/api/workers", json={"name": worker}, headers=csrf())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf())
    return worker


def _import_recipe(client, csrf, product, materials):
    header = "반제품명\t" + "\t".join(m[0] for m in materials)
    row = product + "\t" + "\t".join(str(m[1]) for m in materials)
    res = client.post(
        "/api/recipes/import",
        json={"raw_text": f"{header}\n{row}", "force": True},
        headers=csrf(),
    )
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


_WORK_DATE = "2026-09-18"


def _save_real(client, csrf, recipe_id, product, materials):
    res = client.post(
        "/api/blend/records",
        json={
            "recipe_id": recipe_id,
            "product_name": product,
            "worker": "무시됨",
            "work_date": _WORK_DATE,
            "total_amount": sum(m[1] for m in materials),
            "details": [
                {"material_name": m[0], "actual_amount": m[1], "material_lot": f"L{i}"}
                for i, m in enumerate(materials)
            ],
        },
        headers=csrf(),
    )
    assert res.status_code == 200, res.text
    return res.json()


def _save_test_record(client, csrf, name, recipe_id, materials):
    res = client.post(
        "/api/blend/records",
        json={
            "is_test": True,
            "base_recipe_id": recipe_id,
            "product_name": name,
            "worker": "무시됨",
            "work_date": _WORK_DATE,
            "total_amount": 1,
            "details": [
                {
                    "material_name": m[0],
                    "theory_amount": m[1],
                    "actual_amount": m[1],
                    "material_lot": f"T{i}",
                }
                for i, m in enumerate(materials)
            ],
        },
        headers=csrf(),
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_records_list_test_filter_and_chip_fields():
    """/api/blend/records?test=only 는 시험 기록만 · 행에 is_test 가 실린다(칩 근거)."""
    client, csrf = _mgmt_client()
    product = "칩제품" + _uid()
    _worker_session(client, csrf, "칩작업" + _uid())
    recipe_id = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)])
    real = _save_real(client, csrf, recipe_id, product, [("원료A", 60), ("원료B", 40)])
    test = _save_test_record(
        client, csrf, product + "시험", recipe_id, [("원료A", 30), ("원료B", 70)]
    )

    only = client.get("/api/blend/records", params={"test": "only", "search": product})
    assert only.status_code == 200, only.text
    ids = [it["id"] for it in only.json()["items"]]
    assert test["id"] in ids
    assert real["id"] not in ids
    assert all(it["is_test"] for it in only.json()["items"])

    exclude = client.get(
        "/api/blend/records", params={"test": "exclude", "search": product}
    )
    ex_ids = [it["id"] for it in exclude.json()["items"]]
    assert real["id"] in ex_ids and test["id"] not in ex_ids

    # 상세는 기준 레시피 이름까지 준다(상세 헤더 '기준 레시피: ...' 의 근거).
    detail = client.get(f"/api/blend/records/{test['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["is_test"] is True
    assert detail.json()["base_recipe_name"] == product


def test_blend_records_queue_default_excludes_tests_and_test_mode_includes_only_them():
    """등록 대기열: 기본은 정식만 · test=1 은 기준 레시피로 이은 시험 기록만(§9-2)."""
    client, csrf = _mgmt_client()
    product = "대기열" + _uid()
    _worker_session(client, csrf, "대기작업" + _uid())
    recipe_id = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)])
    real = _save_real(client, csrf, recipe_id, product, [("원료A", 60), ("원료B", 40)])
    test = _save_test_record(
        client, csrf, product + "시험", recipe_id, [("원료A", 30), ("원료B", 70)]
    )

    # 반제품(점도 제품)은 정식 배합에 점도를 등록하면 자동 생성된다.
    linked = client.post(
        f"/api/blend/records/{real['id']}/viscosity",
        json={"viscosity": 49.0},
        headers=csrf(),
    )
    assert linked.status_code == 200, linked.text
    products = client.get("/api/viscosity/products").json()["items"]
    pid = next(p["id"] for p in products if p["code"] == product)

    default = client.get(f"/api/viscosity/products/{pid}/blend-records").json()
    assert [it["id"] for it in default["items"]] == [real["id"]]
    assert default["total"] == 1
    assert default["test"] is False

    tests_only = client.get(
        f"/api/viscosity/products/{pid}/blend-records", params={"test": "1"}
    ).json()
    assert [it["id"] for it in tests_only["items"]] == [test["id"]]
    assert tests_only["total"] == 1
    assert tests_only["unregistered_total"] == 1
    assert tests_only["items"][0]["is_test"] is True

    # 시험 LOT 에 점도를 등록해도 정식 통계·이상·알림은 그대로다.
    saved = client.post(
        f"/api/blend/records/{test['id']}/viscosity",
        json={"viscosity": 999.0, "product_id": pid},
        headers=csrf(),
    )
    assert saved.status_code == 200, saved.text
    analysis = client.get(f"/api/viscosity/products/{pid}").json()
    assert analysis["stats"]["n"] == 1
    assert all(r["viscosity"] != 999.0 for r in analysis["readings"])
    assert analysis["counts"]["anomaly"] == 0

    view = client.get(f"/api/viscosity/products/{pid}/test-readings").json()
    assert [it["viscosity"] for it in view["items"]] == [999.0]

    # 대기열에서는 등록됨으로 바뀐다(미등록 카운트 0).
    after = client.get(
        f"/api/viscosity/products/{pid}/blend-records", params={"test": "1"}
    ).json()
    assert after["unregistered_total"] == 0
    assert after["items"][0]["registered"] is True

    # 공개 알림(트레이)에도 시험 LOT 이 나타나지 않는다.
    # 내부망 제한이 걸린 라우트라 사내 IP 로 접속한다(test_viscosity_reminder_since 패턴).
    from fastapi.testclient import TestClient

    internal = TestClient(client.app, client=("192.168.11.108", 50000))
    due = internal.get(
        "/api/public/viscosity-reminders/due", params={"target_date": "2026-09-30"}
    )
    assert due.status_code == 200, due.text
    for item in due.json()["items"]:
        for lot in item["pending_lots"]:
            assert not lot["product_lot"].startswith("T-")
