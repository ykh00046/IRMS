"""미해소 LOT 대사 · 총 배합량 상한 · 증량 승인 우회 감지 (2026-08-04).

대상:
  A. src/routers/blend_lot_audit_routes.py — 책임자 전용 대사·이상 조회
  B. src/routers/models.py 총량 상한(BLEND_TOTAL_MAX_G) + oversize_total 플래그
  C. src/services/blend_service.detect_total_bypass — 증량 승인 우회 감지(기록만)

테스트 DB(.tmp-tests/pytest-data/irms.db)는 실행 간 유지되므로, 이 모듈이 심은
행(product_name LIKE 'LAUD%')은 각 테스트 종료 후 정리한다(test_rescale_ack 관례).
"""

import importlib
import sqlite3
import uuid

import pytest


@pytest.fixture(autouse=True)
def _cleanup_test_records():
    yield
    from src.db import get_connection

    with get_connection() as conn:
        conn.execute(
            "DELETE FROM blend_lot_acks WHERE record_id IN "
            "(SELECT id FROM blend_records WHERE product_name LIKE 'LAUD%')"
        )
        conn.execute("DELETE FROM blend_records WHERE product_name LIKE 'LAUD%'")
        conn.commit()


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _login(client, username="admin", password="admin"):
    res = client.post(
        "/api/auth/management-login", json={"username": username, "password": password}
    )
    assert res.status_code == 200, res.text
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _uid():
    return uuid.uuid4().hex[:8].upper()


def _prod():
    return f"LAUD{_uid()}"


def _worker_session(client, headers):
    """배합 저장에 필요한 작업자 세션 확보 → 작업자명."""
    worker = "대사작업" + uuid.uuid4().hex[:6]
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    return worker


def _import_recipe(client, headers, product, *, base_totals=None, anchor=None):
    """원료A 60 / 원료B 40 레시피 등록 → recipe_id."""
    payload = {
        "raw_text": f"반제품명\t원료A\t원료B\n{product}\t60\t40",
        "force": True,
    }
    if base_totals is not None:
        payload["base_totals"] = base_totals
    if anchor is not None:
        payload["anchor_material"] = anchor
    res = client.post("/api/recipes/import", json=payload, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


def _save_blend(client, headers, *, recipe_id, product, worker, total, **extra):
    """레시피 이론량 그대로 계량한 배합 실적 저장 → 응답."""
    blend = client.get(f"/api/blend/recipes/{recipe_id}", params={"total": total}).json()
    details = [
        {
            "material_id": it["material_id"],
            "material_name": it["material_name"],
            "ratio": it["ratio"],
            "theory_amount": it["theory_amount"],
            "actual_amount": it["theory_amount"],
            "material_lot": f"LOT-{it['material_name']}",
        }
        for it in blend["items"]
    ]
    body = {
        "recipe_id": recipe_id,
        "product_name": product,
        "worker": worker,
        "work_date": "2026-08-04",
        "total_amount": total,
        "details": details,
    }
    body.update(extra)
    return client.post("/api/blend/records", json=body, headers=headers)


def _flags(record_id):
    from src.db import get_connection

    with get_connection() as conn:
        return dict(
            conn.execute(
                "SELECT oversize_total, total_bypass_suspect, total_bypass_base, "
                "total_amount, rescale_count FROM blend_records WHERE id = ?",
                (record_id,),
            ).fetchone()
        )


def _seed_record(conn, *, product, lot, status="completed", work_date="2026-07-01"):
    cur = conn.execute(
        "INSERT INTO blend_records "
        "(product_lot, product_name, worker, work_date, total_amount, status, created_at) "
        "VALUES (?, ?, '김작업', ?, 1000, ?, '2026-07-01T00:00:00Z')",
        (lot, product, work_date, status),
    )
    return cur.lastrowid


def _seed_ack(conn, *, record_id, material_name, material_lot,
              acknowledged=1, reason="", created_at="2026-07-01T00:00:00Z"):
    cur = conn.execute(
        "INSERT INTO blend_lot_acks "
        "(record_id, material_name, material_lot, reason, acknowledged, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (record_id, material_name, material_lot, reason, acknowledged, created_at),
    )
    return cur.lastrowid


# ══════════════════════════════════════════════════════════════════════════
# A. 미해소 LOT 대사
# ══════════════════════════════════════════════════════════════════════════


def test_unresolved_lists_missing_lot_and_drops_when_stage1_appears():
    """핵심 대사 규칙: 1차 기록이 **나중에** 생기면 목록에서 빠진다(해소).

    같은 ack 행을 건드리지 않고 blend_records 만 추가해도 목록에서 사라져야 한다 —
    대사가 자기 치유된다는 뜻이다.
    """
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    semi = _prod()                     # 1차 반제품명
    host = _prod()                     # 이 LOT 를 쓴 2차 배합
    missing_lot = f"{semi}26080499"

    with get_connection() as conn:
        # 1차 반제품이 '자가 반제품'으로 인정되려면 completed 기록이 하나는 있어야 한다.
        _seed_record(conn, product=semi, lot=f"{semi}26070101")
        host_id = _seed_record(conn, product=host, lot=f"{host}26080401")
        ack_id = _seed_ack(
            conn, record_id=host_id, material_name=semi, material_lot=missing_lot,
        )
        conn.commit()

    res = client.get("/api/blend/lot-audit/unresolved", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    item = next((it for it in body["items"] if it["ack_id"] == ack_id), None)
    assert item is not None, "미해소 건이 목록에 떠야 한다"
    assert item["material_name"] == semi
    assert item["material_lot"] == missing_lot
    assert item["product_lot"] == f"{host}26080401"   # 해당 배합 기록으로 갈 수 있는 키
    assert item["record_id"] == host_id
    assert isinstance(item["age_days"], int) and item["age_days"] >= 0

    # ── 1차 기록이 나중에 생긴다 → 해소 ──
    with get_connection() as conn:
        _seed_record(conn, product=semi, lot=missing_lot)
        conn.commit()

    res2 = client.get("/api/blend/lot-audit/unresolved", headers=headers)
    assert res2.status_code == 200
    body2 = res2.json()
    assert all(it["ack_id"] != ack_id for it in body2["items"]), \
        "1차 기록이 생기면 미해소 목록에서 빠져야 한다"
    assert body2["resolved"] >= 1


def test_unresolved_marks_unacknowledged_separately():
    """acknowledged=0(확인 창조차 못 본 경로)은 별도로 구분·집계된다."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    semi = _prod()
    host = _prod()
    with get_connection() as conn:
        _seed_record(conn, product=semi, lot=f"{semi}26070101")
        host_id = _seed_record(conn, product=host, lot=f"{host}26080401")
        acked = _seed_ack(conn, record_id=host_id, material_name=semi,
                          material_lot=f"{semi}26080401", acknowledged=1, reason="확인함")
        unacked = _seed_ack(conn, record_id=host_id, material_name=semi,
                            material_lot=f"{semi}26080402", acknowledged=0)
        conn.commit()

    body = client.get("/api/blend/lot-audit/unresolved", headers=headers).json()
    by_id = {it["ack_id"]: it for it in body["items"]}
    assert by_id[acked]["acknowledged"] is True
    assert by_id[unacked]["acknowledged"] is False
    assert body["unacknowledged"] >= 1


def test_unresolved_excludes_canceled_host_record():
    """취소된 배합(status != 'completed')의 ack 는 대사 대상에서 걸러진다."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    semi = _prod()
    host_ok = _prod()
    host_canceled = _prod()
    with get_connection() as conn:
        _seed_record(conn, product=semi, lot=f"{semi}26070101")
        ok_id = _seed_record(conn, product=host_ok, lot=f"{host_ok}26080401")
        cancel_id = _seed_record(conn, product=host_canceled,
                                 lot=f"{host_canceled}26080401", status="canceled")
        keep = _seed_ack(conn, record_id=ok_id, material_name=semi,
                         material_lot=f"{semi}26080411")
        dropped = _seed_ack(conn, record_id=cancel_id, material_name=semi,
                            material_lot=f"{semi}26080412")
        conn.commit()

    body = client.get("/api/blend/lot-audit/unresolved", headers=headers).json()
    ids = {it["ack_id"] for it in body["items"]}
    assert keep in ids
    assert dropped not in ids


def test_lot_audit_endpoints_require_manager():
    client = _client()
    assert client.get("/api/blend/lot-audit/unresolved").status_code in (401, 403)
    assert client.get("/api/blend/lot-audit/total-anomalies").status_code in (401, 403)


def test_lot_audit_page_requires_manager():
    """페이지도 책임자 전용 — 비로그인은 로그인 화면으로 리다이렉트(admin/users 와 동일)."""
    client = _client()
    res = client.get("/lot-audit", follow_redirects=False)
    assert res.status_code == 303
    assert "/management/login" in res.headers["location"]

    headers = _login(client)
    assert client.get("/lot-audit", headers=headers).status_code == 200


# ══════════════════════════════════════════════════════════════════════════
# B. 총 배합량 서버 상한 + 25kg 플래그
# ══════════════════════════════════════════════════════════════════════════


def test_total_above_server_cap_is_rejected():
    """상한(200,000 g) 초과는 Pydantic 단계에서 거부 — 오타 10톤 차단."""
    client = _client()
    headers = _login(client)
    worker = _worker_session(client, headers)
    product = _prod()

    for bad_total in (200_000.01, 10_000_000):
        res = client.post("/api/blend/records", json={
            "product_name": product, "worker": worker, "work_date": "2026-08-04",
            "total_amount": bad_total,
            "details": [{"material_name": "A", "ratio": 100,
                         "theory_amount": bad_total, "actual_amount": bad_total,
                         "material_lot": "LOT-A"}],
        }, headers=headers)
        assert res.status_code == 422, f"{bad_total} → {res.status_code} {res.text}"


def test_total_at_server_cap_is_accepted_and_flagged():
    """상한 경계값(200,000 g)은 저장된다 — 상한은 '오타 차단'이지 배치 금지가 아니다."""
    client = _client()
    headers = _login(client)
    worker = _worker_session(client, headers)
    product = _prod()

    res = client.post("/api/blend/records", json={
        "product_name": product, "worker": worker, "work_date": "2026-08-04",
        "total_amount": 200_000,
        "details": [{"material_name": "A", "ratio": 100,
                     "theory_amount": 200_000, "actual_amount": 200_000,
                     "material_lot": "LOT-A"}],
    }, headers=headers)
    assert res.status_code == 200, res.text
    flags = _flags(res.json()["id"])
    assert flags["oversize_total"] == 1


def test_rescale_over_25kg_still_saves_and_is_flagged():
    """'그래도 증량'(폐기 권장 무시) 경로 — 25,000 g 초과여도 여전히 저장된다.

    저장은 되고 기록에 oversize_total 플래그만 남으며, 책임자 이상 목록에 뜬다.
    (증량 이력이 있으므로 우회 의심은 켜지지 않는다.)
    """
    client = _client()
    headers = _login(client)
    worker = _worker_session(client, headers)
    product = _prod()
    recipe_id = _import_recipe(client, headers, product, base_totals=[24000])

    res = _save_blend(
        client, headers, recipe_id=recipe_id, product=product, worker=worker,
        total=30000,
        rescale_events=[{
            "before_total": 24000, "after_total": 30000,
            "absence_reason": "책임자 부재 — 야간",
        }],
    )
    assert res.status_code == 200, res.text
    rid = res.json()["id"]
    flags = _flags(rid)
    assert flags["total_amount"] == 30000
    assert flags["oversize_total"] == 1
    assert flags["total_bypass_suspect"] == 0     # 증량 이력이 있으니 우회가 아니다
    assert flags["rescale_count"] == 1

    body = client.get("/api/blend/lot-audit/total-anomalies", headers=headers).json()
    item = next((it for it in body["items"] if it["id"] == rid), None)
    assert item is not None
    assert item["oversize_total"] is True
    assert item["over_limit_g"] == 5000.0
    assert body["limit_g"] == 25000.0
    assert body["max_total_g"] == 200000.0


def test_normal_total_is_not_flagged_oversize():
    client = _client()
    headers = _login(client)
    worker = _worker_session(client, headers)
    product = _prod()
    recipe_id = _import_recipe(client, headers, product, base_totals=[4000])

    res = _save_blend(client, headers, recipe_id=recipe_id, product=product,
                      worker=worker, total=4000)
    assert res.status_code == 200, res.text
    assert _flags(res.json()["id"])["oversize_total"] == 0


# ══════════════════════════════════════════════════════════════════════════
# C. 증량 승인 우회 감지 — 감지되는 경우 / 오탐이 아닌 경우
# ══════════════════════════════════════════════════════════════════════════


def test_bypass_scenario_is_detected_and_recorded():
    """우회 시나리오: 초과 계량 후 새로고침 → 총량을 먼저 키워 입력 → 증량 이력 0.

    총량 4,632.19 g 는 '초과 계량한 실측 × 100 / 비율' 로 고정된 값이라 라운드가
    아니다. 기준 배합량 4,000 g 대비 +15.8% → 감지되어 기록에 남는다(저장은 성공).
    """
    client = _client()
    headers = _login(client)
    worker = _worker_session(client, headers)
    product = _prod()
    recipe_id = _import_recipe(client, headers, product, base_totals=[4000])

    res = _save_blend(client, headers, recipe_id=recipe_id, product=product,
                      worker=worker, total=4632.19)
    assert res.status_code == 200, res.text     # 저장은 절대 막지 않는다
    rid = res.json()["id"]
    flags = _flags(rid)
    assert flags["total_bypass_suspect"] == 1
    assert flags["total_bypass_base"] == 4000.0
    assert flags["rescale_count"] == 0

    body = client.get("/api/blend/lot-audit/total-anomalies", headers=headers).json()
    item = next((it for it in body["items"] if it["id"] == rid), None)
    assert item is not None
    assert item["total_bypass_suspect"] is True
    assert item["base_total"] == 4000.0
    assert item["excess_pct"] == 15.8

    # 감사 로그에도 판정 근거가 남는다.
    from src.db import get_connection
    with get_connection() as conn:
        row = conn.execute(
            "SELECT details_json FROM audit_logs "
            "WHERE action='blend_total_bypass_suspect' AND target_id=?", (str(rid),)
        ).fetchone()
    assert row is not None
    assert "4000" in row["details_json"]


@pytest.mark.parametrize(
    "total, why",
    [
        (5000, "손으로 친 라운드 커스텀 총량(정당한 커스텀 배치)"),
        (12000, "3배 배치 — 기준의 정수배는 정상 관행"),
        (8000, "2배 배치"),
        (4100, "기준 대비 +2.5% — 5% 임계 미만"),
        (4020, "기준 대비 +0.5% · 절대 20 g — 절대 임계(50 g) 미만"),
        (2000, "기준보다 작은 분할 배치 — 하향은 우회가 아니다"),
        (4630, "10 g 단위로 떨어지는 손입력 값"),
    ],
)
def test_legitimate_custom_totals_are_not_flagged(total, why):
    """오탐 방지 — 정당한 커스텀 총량은 감지되지 않는다."""
    client = _client()
    headers = _login(client)
    worker = _worker_session(client, headers)
    product = _prod()
    recipe_id = _import_recipe(client, headers, product, base_totals=[4000])

    res = _save_blend(client, headers, recipe_id=recipe_id, product=product,
                      worker=worker, total=total)
    assert res.status_code == 200, res.text
    assert _flags(res.json()["id"])["total_bypass_suspect"] == 0, why


def test_recipe_without_base_total_is_never_flagged():
    """기준 배합량이 없는 레시피(현장에 많다)는 비교 근거가 없어 영영 제외된다."""
    client = _client()
    headers = _login(client)
    worker = _worker_session(client, headers)
    product = _prod()
    recipe_id = _import_recipe(client, headers, product)   # base_totals 미지정

    res = _save_blend(client, headers, recipe_id=recipe_id, product=product,
                      worker=worker, total=4632.19)
    assert res.status_code == 200, res.text
    assert _flags(res.json()["id"])["total_bypass_suspect"] == 0


def test_anchor_recipe_is_never_flagged():
    """기준 자재(anchor) 레시피는 총량이 실측 파생이라 비교가 성립하지 않는다.

    이 케이스를 제외하지 않으면 파생 총량(라운드 아님 + 기준 대비 큰 차이)이
    전부 우회로 잡혀 오탐이 된다.
    """
    client = _client()
    headers = _login(client)
    worker = _worker_session(client, headers)
    product = _prod()
    recipe_id = _import_recipe(client, headers, product,
                               base_totals=[4000], anchor="원료A")

    blend = client.get(f"/api/blend/recipes/{recipe_id}").json()
    assert blend["recipe"]["anchor_material_id"] is not None
    items = blend["items"]
    anchor_w = next(it["value_weight"] for it in items if it["is_anchor"])
    anchor_actual = 2779.31
    theory = {
        it["material_name"]: (
            anchor_actual if it["is_anchor"]
            else round(anchor_actual * it["value_weight"] / anchor_w, 2)
        )
        for it in items
    }
    derived_total = round(sum(theory.values()), 2)
    # 기준 4,000 g 대비 +15% 이상이고 라운드도 아니다 — anchor 제외가 없으면 오탐.
    assert derived_total > 4000 * 1.05
    assert abs(derived_total - round(derived_total / 10) * 10) > 1e-6

    res = client.post("/api/blend/records", json={
        "recipe_id": recipe_id, "product_name": product, "worker": worker,
        "work_date": "2026-08-04", "total_amount": 4000,
        "details": [
            {"material_id": it["material_id"], "material_name": it["material_name"],
             "ratio": it["ratio"], "theory_amount": theory[it["material_name"]],
             "actual_amount": theory[it["material_name"]],
             "material_lot": f"LOT-{it['material_name']}"}
            for it in items
        ],
    }, headers=headers)
    assert res.status_code == 200, res.text
    flags = _flags(res.json()["id"])
    assert flags["total_amount"] == derived_total
    assert flags["total_bypass_suspect"] == 0


def test_update_recomputes_total_flags():
    """수정(PUT)으로 총량을 정정하면 플래그도 다시 판정된다(켜짐 → 꺼짐)."""
    client = _client()
    headers = _login(client)
    worker = _worker_session(client, headers)
    product = _prod()
    recipe_id = _import_recipe(client, headers, product, base_totals=[4000])

    created = _save_blend(client, headers, recipe_id=recipe_id, product=product,
                          worker=worker, total=4632.19)
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    assert _flags(rid)["total_bypass_suspect"] == 1

    blend = client.get(f"/api/blend/recipes/{recipe_id}", params={"total": 4000}).json()
    res = client.put(f"/api/blend/records/{rid}", json={
        "recipe_id": recipe_id, "product_name": product, "worker": worker,
        "work_date": "2026-08-04", "total_amount": 4000,
        "details": [
            {"material_id": it["material_id"], "material_name": it["material_name"],
             "ratio": it["ratio"], "theory_amount": it["theory_amount"],
             "actual_amount": it["theory_amount"],
             "material_lot": f"LOT-{it['material_name']}"}
            for it in blend["items"]
        ],
    }, headers=headers)
    assert res.status_code == 200, res.text
    assert _flags(rid)["total_bypass_suspect"] == 0


# ── C 순수 규칙 단위 테스트(레시피 컬럼만 보는 얇은 경로) ────────────────────


def _rule_conn(base_totals=None, anchor_id=None):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE recipes (id INTEGER PRIMARY KEY, base_total REAL, "
        "base_totals TEXT, anchor_material_id INTEGER)"
    )
    conn.execute(
        "INSERT INTO recipes (id, base_total, base_totals, anchor_material_id) "
        "VALUES (1, NULL, ?, ?)",
        (base_totals, anchor_id),
    )
    return conn


@pytest.mark.parametrize(
    "total, expected",
    [
        (4632.19, 4000.0),   # 우회 총량 — 라운드 아님 · +15.8%
        (4283.37, 4000.0),   # +7.1%
        (5000, None),        # 라운드 커스텀
        (4630, None),        # 10 g 단위
        (8000, None),        # 2배 배치
        (4100, None),        # +2.5% (임계 미만)
        (3000, None),        # 하향
        (4000, None),        # 기준 그대로
    ],
)
def test_detect_total_bypass_rules(total, expected):
    from src.services import blend_service

    conn = _rule_conn(base_totals="4000")
    assert blend_service.detect_total_bypass(conn, 1, total) == expected


def test_detect_total_bypass_skips_when_rescaled_or_anchored():
    from src.services import blend_service

    conn = _rule_conn(base_totals="4000")
    assert blend_service.detect_total_bypass(conn, 1, 4632.19, rescale_count=1) is None

    anchored = _rule_conn(base_totals="4000", anchor_id=7)
    assert blend_service.detect_total_bypass(anchored, 1, 4632.19) is None

    no_recipe = _rule_conn(base_totals="4000")
    assert blend_service.detect_total_bypass(no_recipe, None, 4632.19) is None


def test_detect_total_bypass_multi_base_totals():
    """기준이 여러 개면 '모든 기준보다 큰' 경우에만, 가장 큰 기준을 대상으로 본다."""
    from src.services import blend_service

    conn = _rule_conn(base_totals="1000,4000")
    assert blend_service.detect_total_bypass(conn, 1, 4632.19) == 4000.0
    # 기준 사이의 값(1000 < 2317.11 < 4000)은 '작은 배치'일 수 있어 제외한다.
    assert blend_service.detect_total_bypass(conn, 1, 2317.11) is None


# ══════════════════════════════════════════════════════════════════════════
# 마이그레이션 안전성 — 기존 데이터가 있는 옛 스키마 DB
# ══════════════════════════════════════════════════════════════════════════


def test_migration_is_additive_on_old_schema_db_with_data(tmp_path):
    """새 컬럼이 없던 시절의 DB(데이터 보유)에 마이그레이션을 적용해도 손실이 없다.

    현행 스키마로 DB 를 만든 뒤 새 인덱스·컬럼 3개를 제거해 '옛 스키마'를 재현하고,
    기존 행을 남긴 상태에서 apply_schema_migrations 를 다시 돌린다.
    """
    import src.config as cfg
    import src.db.connection as dbconn
    from src.db.migrations import apply_schema_migrations
    from src.db.schema import init_db

    importlib.reload(cfg)
    dbconn.DATA_DIR = tmp_path
    dbconn.DATABASE_PATH = tmp_path / "old.db"
    init_db()

    with dbconn.get_connection() as conn:
        # 옛 스키마 재현 — 인덱스를 먼저 지워야 컬럼을 뗄 수 있다.
        conn.execute("DROP INDEX IF EXISTS idx_blend_records_oversize_total")
        conn.execute("DROP INDEX IF EXISTS idx_blend_records_total_bypass")
        for col in ("oversize_total", "total_bypass_suspect", "total_bypass_base"):
            conn.execute(f"ALTER TABLE blend_records DROP COLUMN {col}")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(blend_records)")}
        assert "oversize_total" not in cols
        # 기존 데이터
        conn.execute(
            "INSERT INTO blend_records "
            "(product_lot, product_name, worker, work_date, total_amount, status, created_at) "
            "VALUES ('OLD26070101', 'OLDPROD', '김작업', '2026-07-01', 1234.5, "
            "'completed', '2026-07-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO blend_lot_acks "
            "(record_id, material_name, material_lot, reason, acknowledged, created_at) "
            "VALUES (1, 'SEMI', 'SEMI26070101', '', 0, '2026-07-01T00:00:00Z')"
        )
        conn.commit()

    # 재적용 — 추가 전용이어야 한다.
    with dbconn.get_connection() as conn:
        apply_schema_migrations(conn)
        conn.commit()

    with dbconn.get_connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(blend_records)")}
        assert {"oversize_total", "total_bypass_suspect", "total_bypass_base"} <= cols
        row = conn.execute(
            "SELECT total_amount, oversize_total, total_bypass_suspect, total_bypass_base "
            "FROM blend_records WHERE product_lot = 'OLD26070101'"
        ).fetchone()
        assert row is not None, "기존 행이 보존돼야 한다"
        assert row["total_amount"] == 1234.5
        assert row["oversize_total"] == 0          # NOT NULL DEFAULT 0
        assert row["total_bypass_suspect"] == 0
        assert row["total_bypass_base"] is None
        ack = conn.execute("SELECT acknowledged FROM blend_lot_acks").fetchone()
        assert ack is not None and ack["acknowledged"] == 0
        # 인덱스도 다시 생성된다.
        idx = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "idx_blend_records_oversize_total" in idx
        assert "idx_blend_records_total_bypass" in idx

        # 두 번 더 돌려도 안전(멱등).
        apply_schema_migrations(conn)
        apply_schema_migrations(conn)
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM blend_records"
        ).fetchone()["n"] == 1
