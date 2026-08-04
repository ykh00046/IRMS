"""배합 저장의 서버 신뢰 경계 회귀 테스트 — 화면을 통과했다고 서버가 믿지 않는다.

세 결함을 잠근다(전부 "프론트만 막고 서버는 그대로 받는다"는 같은 뿌리):

A. 전 자재 계량 완료 검사 — 실제량이 빈 자재가 하나라도 있으면 저장 거부.
   (blend_lib.rowVariance 가 빈 실제량의 편차를 0 으로 돌려주므로 프론트 편차 차단을
    그냥 통과하고, 서버 weighing_tolerance_violations 도 actual is None 이면 건너뛰어
    NULL 로 저장됐다 → 자재 사용량 SUM 이 그 자재를 '투입 안 됨'으로 집계.)
   예외: 반응기 이월(carried_over) 행은 서버가 1차 총량으로 강제 채우므로 통과해야 한다.

B. 책임자 승인 토큰의 '목적'(purpose) — 수기입력 승인을 증량 승인으로 쓸 수 없다.
   (blend_rescale_approvals 에 purpose 가 없어 저울 전용 모드의 '수기 입력 승인'
    approval_id 가 30분 안에 아무 배합의 증량 승인으로 통과했다.)

C. 저장 멱등성 — 같은 request_id 의 재시도는 기록을 두 벌 만들지 않는다.
   (타임아웃 재시도 → 같은 계량값이 두 LOT 으로 저장돼 사용량이 2배가 됐다.)
"""

from __future__ import annotations

import importlib
import sqlite3
import uuid


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
    """책임자 로그인 + CSRF 헤더 (tests/test_blend.py `_mgmt_client` 와 동일 패턴)."""
    client = _client()
    assert client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    ).status_code == 200

    def csrf():
        tok = client.cookies.get("csrftoken")
        return {"x-csrftoken": tok} if tok else {}

    return client, csrf


def _worker_session(client, csrf, worker: str) -> str:
    client.get("/api/blend/records")  # csrf 쿠키 확보
    client.post("/api/workers", json={"name": worker}, headers=csrf())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf())
    return worker


def _import_recipe(client, csrf, product, materials, *, anchor=None, is_derived=False):
    header = "반제품명\t" + "\t".join(m[0] for m in materials)
    row = product + "\t" + "\t".join(str(m[1]) for m in materials)
    body = {"raw_text": f"{header}\n{row}", "force": True}
    if anchor:
        body["anchor_material"] = anchor
    if is_derived:
        body["is_derived"] = True
    res = client.post("/api/recipes/import", json=body, headers=csrf())
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


# ── A. 전 자재 계량 완료 ────────────────────────────────────────────
def test_single_save_rejects_unweighed_material():
    """단건 저장: 실제량이 빈 자재가 있으면 400 + 그 자재명이 메시지에 담긴다."""
    client, csrf = _mgmt_client()
    product = "미계량" + _uid()
    _worker_session(client, csrf, "미계량작업" + _uid())
    rid = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)])

    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": product, "worker": "무시됨",
        "work_date": "2026-08-04", "total_amount": 100,
        "details": [
            {"material_name": "원료A", "actual_amount": 60, "material_lot": "LA"},
            # 원료B — LOT 만 채우고 계량은 하지 않았다(현장 실수 재현).
            {"material_name": "원료B", "actual_amount": None, "material_lot": "LB"},
        ],
    }, headers=csrf())
    assert res.status_code == 400, res.text
    assert "원료B" in res.text


def test_single_save_rejects_missing_actual_field():
    """actual_amount 키 자체를 빼도 동일하게 거부된다(누락 = 미계량)."""
    client, csrf = _mgmt_client()
    product = "키누락" + _uid()
    _worker_session(client, csrf, "키누락작업" + _uid())
    rid = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)])

    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": product, "worker": "무시됨",
        "work_date": "2026-08-04", "total_amount": 100,
        "details": [
            {"material_name": "원료A", "actual_amount": 60, "material_lot": "LA"},
            {"material_name": "원료B", "material_lot": "LB"},
        ],
    }, headers=csrf())
    assert res.status_code == 400, res.text
    assert "원료B" in res.text


def test_continuous_save_rejects_unweighed_cell():
    """다중 계량 저장: 한 셀이라도 실제량이 비면 400(로트 번호 포함) — 아무것도 저장 안 됨."""
    client, csrf = _mgmt_client()
    product = "다중미계량" + _uid()
    _worker_session(client, csrf, "다중작업" + _uid())
    rid = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)])

    res = client.post("/api/blend/records/continuous", json={
        "recipe_id": rid, "product_name": product,
        "work_date": "2026-08-04", "total_amount": 100,
        "lots": [
            [
                {"material_name": "원료A", "actual_amount": 60, "material_lot": "LA"},
                {"material_name": "원료B", "actual_amount": 40, "material_lot": "LB"},
            ],
            [
                {"material_name": "원료A", "actual_amount": 60, "material_lot": "LA"},
                {"material_name": "원료B", "actual_amount": None, "material_lot": "LB"},
            ],
        ],
    }, headers=csrf())
    assert res.status_code == 400, res.text
    assert "원료B" in res.text
    # 원자성 — 통과한 로트 1 도 저장되지 않았다.
    listing = client.get("/api/blend/records", params={"product": product}).json()
    assert listing["total"] == 0, listing


def test_carryover_row_without_actual_is_still_accepted():
    """예외 경로: 반응기 이월 행은 실제량을 안 보내도 저장된다(서버가 1차 총량으로 채움).

    A 의 새 검사가 정당한 NULL 경로까지 막지 않는지 확인한다.
    """
    client, csrf = _mgmt_client()
    intermediate = "이월중간" + _uid()
    final = "이월최종" + _uid()
    worker = "이월작업" + _uid()
    _worker_session(client, csrf, worker)
    stage1 = client.post("/api/blend/records", json={
        "product_name": intermediate, "worker": worker, "work_date": "2026-08-01",
        "total_amount": 150,
        "details": [
            {"material_name": "원료1", "ratio": 100, "theory_amount": 150,
             "actual_amount": 150, "material_lot": "L1"},
        ],
    }, headers=csrf())
    assert stage1.status_code == 200, stage1.text
    stage1_lot = stage1.json()["product_lot"]

    rid = _import_recipe(client, csrf, final, [(intermediate, 60), ("최종원료", 40)],
                         anchor=intermediate, is_derived=True)
    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": final, "worker": worker,
        "work_date": "2026-08-02", "total_amount": 250,
        "details": [
            # 이월 행 — 실제량 미전송. 서버가 1차 총량(150)으로 강제 채운다.
            {"material_name": intermediate, "material_lot": stage1_lot, "carried_over": True},
            {"material_name": "최종원료", "actual_amount": 100, "material_lot": "L9"},
        ],
    }, headers=csrf())
    assert res.status_code == 200, res.text
    rows = {d["material_name"]: d for d in client.get(
        f"/api/blend/records/{res.json()['id']}").json()["details"]}
    assert rows[intermediate]["actual_amount"] == 150.0


# ── B. 승인 토큰의 목적(purpose) ────────────────────────────────────
def _approval(client, csrf, purpose=None):
    body = {"username": "admin", "password": "admin"}
    if purpose:
        body["purpose"] = purpose
    res = client.post("/api/blend/manager-verify", json=body, headers=csrf())
    assert res.status_code == 200, res.text
    return res.json()["approval_id"]


def _rescale_payload(product, worker, approval_id):
    return {
        "product_name": product, "worker": worker, "work_date": "2026-08-04",
        "total_amount": 100,
        "details": [
            {"material_name": "일반원료", "ratio": 100, "theory_amount": 100,
             "actual_amount": 100, "material_lot": "L1"},
        ],
        "rescale_events": [
            {"before_total": 100, "after_total": 120, "approval_id": approval_id},
        ],
    }


def test_manual_approval_cannot_be_spent_as_rescale():
    """수기입력 승인(purpose=manual) 토큰을 증량 승인으로 쓰면 400."""
    client, csrf = _mgmt_client()
    worker = "목적작업" + _uid()
    _worker_session(client, csrf, worker)
    manual_id = _approval(client, csrf, purpose="manual")

    res = client.post(
        "/api/blend/records",
        json=_rescale_payload("목적" + _uid(), worker, manual_id),
        headers=csrf(),
    )
    assert res.status_code == 400, res.text
    assert "증량 승인이 유효하지 않습니다" in res.text


def test_rescale_approval_still_works():
    """정상 경로 보존: purpose=rescale(기본) 토큰은 증량 승인으로 소비된다."""
    client, csrf = _mgmt_client()
    worker = "증량작업" + _uid()
    _worker_session(client, csrf, worker)
    approval_id = _approval(client, csrf)

    res = client.post(
        "/api/blend/records",
        json=_rescale_payload("증량" + _uid(), worker, approval_id),
        headers=csrf(),
    )
    assert res.status_code == 200, res.text
    from src.db import get_connection
    with get_connection() as conn:
        row = conn.execute(
            "SELECT used, purpose FROM blend_rescale_approvals WHERE id = ?", (approval_id,)
        ).fetchone()
    assert row["used"] == 1
    assert row["purpose"] == "rescale"


def test_manual_approval_is_consumed_at_issue():
    """수기입력 승인은 발급 시점이 곧 승인 — used=1 로 남아 재고처럼 쌓이지 않는다."""
    client, csrf = _mgmt_client()
    manual_id = _approval(client, csrf, purpose="manual")
    from src.db import get_connection
    with get_connection() as conn:
        row = conn.execute(
            "SELECT used, purpose FROM blend_rescale_approvals WHERE id = ?", (manual_id,)
        ).fetchone()
    assert row["purpose"] == "manual"
    assert row["used"] == 1


def test_legacy_approval_row_without_purpose_is_accepted_as_rescale():
    """하위호환: purpose 가 NULL 인 옛 행(마이그레이션 이전 발급)은 증량으로 소비된다."""
    client, csrf = _mgmt_client()
    worker = "구토큰" + _uid()
    _worker_session(client, csrf, worker)
    from src.db import get_connection
    from src.db.time_utils import utc_now_text
    with get_connection() as conn:
        legacy_id = conn.execute(
            "INSERT INTO blend_rescale_approvals (approver, created_at, used) VALUES (?, ?, 0)",
            ("옛책임자", utc_now_text()),
        ).lastrowid
        conn.commit()

    res = client.post(
        "/api/blend/records",
        json=_rescale_payload("구토큰" + _uid(), worker, legacy_id),
        headers=csrf(),
    )
    assert res.status_code == 200, res.text


# ── C. 저장 멱등성 ──────────────────────────────────────────────────
def test_duplicate_request_id_returns_first_record():
    """같은 request_id 재전송은 기록을 두 벌 만들지 않고 첫 결과를 돌려준다."""
    client, csrf = _mgmt_client()
    product = "멱등" + _uid()
    worker = "멱등작업" + _uid()
    _worker_session(client, csrf, worker)
    rid = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)])
    body = {
        "recipe_id": rid, "product_name": product, "worker": worker,
        "work_date": "2026-08-04", "total_amount": 100,
        "request_id": "req-" + uuid.uuid4().hex,
        "details": [
            {"material_name": "원료A", "actual_amount": 60, "material_lot": "LA"},
            {"material_name": "원료B", "actual_amount": 40, "material_lot": "LB"},
        ],
    }
    first = client.post("/api/blend/records", json=body, headers=csrf())
    assert first.status_code == 200, first.text
    second = client.post("/api/blend/records", json=body, headers=csrf())
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["product_lot"] == second.json()["product_lot"]

    listing = client.get("/api/blend/records", params={"product": product}).json()
    assert listing["total"] == 1, listing

    # 억제 근거가 DB 에 남는다 — request_id → 그때 만든 기록 id (사후 확인용).
    from src.db import get_connection
    with get_connection() as conn:
        row = conn.execute(
            "SELECT endpoint, record_ids FROM blend_save_requests WHERE request_id = ?",
            (body["request_id"],),
        ).fetchone()
    assert row is not None
    assert row["endpoint"] == "blend_create"
    assert str(first.json()["id"]) in row["record_ids"]


def test_different_request_id_creates_new_record():
    """다른 request_id 는 정상적으로 새 기록을 만든다(과잉 차단 방지)."""
    client, csrf = _mgmt_client()
    product = "멱등2" + _uid()
    worker = "멱등2작업" + _uid()
    _worker_session(client, csrf, worker)
    rid = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)])

    def body():
        return {
            "recipe_id": rid, "product_name": product, "worker": worker,
            "work_date": "2026-08-04", "total_amount": 100,
            "request_id": "req-" + uuid.uuid4().hex,
            "details": [
                {"material_name": "원료A", "actual_amount": 60, "material_lot": "LA"},
                {"material_name": "원료B", "actual_amount": 40, "material_lot": "LB"},
            ],
        }

    a = client.post("/api/blend/records", json=body(), headers=csrf())
    b = client.post("/api/blend/records", json=body(), headers=csrf())
    assert a.status_code == 200 and b.status_code == 200
    assert a.json()["id"] != b.json()["id"]
    assert client.get("/api/blend/records", params={"product": product}).json()["total"] == 2


def test_failed_save_does_not_burn_request_id():
    """400 으로 막힌 저장은 request_id 를 소모하지 않는다 — 고친 뒤 같은 id 로 저장된다."""
    client, csrf = _mgmt_client()
    product = "멱등3" + _uid()
    worker = "멱등3작업" + _uid()
    _worker_session(client, csrf, worker)
    rid = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)])
    token = "req-" + uuid.uuid4().hex
    bad = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": product, "worker": worker,
        "work_date": "2026-08-04", "total_amount": 100, "request_id": token,
        "details": [
            {"material_name": "원료A", "actual_amount": 60, "material_lot": ""},
            {"material_name": "원료B", "actual_amount": 40, "material_lot": "LB"},
        ],
    }, headers=csrf())
    assert bad.status_code == 400, bad.text

    good = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": product, "worker": worker,
        "work_date": "2026-08-04", "total_amount": 100, "request_id": token,
        "details": [
            {"material_name": "원료A", "actual_amount": 60, "material_lot": "LA"},
            {"material_name": "원료B", "actual_amount": 40, "material_lot": "LB"},
        ],
    }, headers=csrf())
    assert good.status_code == 200, good.text


def test_continuous_duplicate_request_id_returns_first_ids():
    """다중 계량도 동일 — 재전송이 N로트를 두 벌 만들지 않는다."""
    client, csrf = _mgmt_client()
    product = "멱등연속" + _uid()
    worker = "멱등연속작업" + _uid()
    _worker_session(client, csrf, worker)
    rid = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)])
    lot = [
        {"material_name": "원료A", "actual_amount": 60, "material_lot": "LA"},
        {"material_name": "원료B", "actual_amount": 40, "material_lot": "LB"},
    ]
    body = {
        "recipe_id": rid, "product_name": product, "work_date": "2026-08-04",
        "total_amount": 100, "request_id": "req-" + uuid.uuid4().hex,
        "lots": [lot, lot],
    }
    first = client.post("/api/blend/records/continuous", json=body, headers=csrf())
    assert first.status_code == 200, first.text
    second = client.post("/api/blend/records/continuous", json=body, headers=csrf())
    assert second.status_code == 200, second.text
    assert first.json()["ids"] == second.json()["ids"]
    assert first.json()["product_lots"] == second.json()["product_lots"]
    assert client.get("/api/blend/records", params={"product": product}).json()["total"] == 2


def test_missing_actual_names_treats_zero_as_weighed():
    """단위: 0 은 '0g 으로 계량됨' — 미계량은 None/빈 문자열뿐이다."""
    from src.services import blend_service as bs

    rows = [
        {"material_name": "A", "actual_amount": 0},
        {"material_name": "B", "actual_amount": ""},
        {"material_name": "C", "actual_amount": None},
        {"material_name": "D", "actual_amount": 12.5},
        {"actual_amount": None},
    ]
    assert bs.missing_actual_names(rows) == ["B", "C", "(이름 없음)"]


def test_update_may_not_blank_an_existing_actual():
    """정정(PUT)으로 채워져 있던 실제량을 비울 수 없다(create 와 대칭)."""
    client, csrf = _mgmt_client()
    product = "정정" + _uid()
    worker = "정정작업" + _uid()
    _worker_session(client, csrf, worker)
    rid = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)])
    created = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": product, "worker": worker,
        "work_date": "2026-08-04", "total_amount": 100,
        "details": [
            {"material_name": "원료A", "actual_amount": 60, "material_lot": "LA"},
            {"material_name": "원료B", "actual_amount": 40, "material_lot": "LB"},
        ],
    }, headers=csrf())
    assert created.status_code == 200, created.text
    rec_id = created.json()["id"]

    res = client.put(f"/api/blend/records/{rec_id}", json={
        "recipe_id": rid, "product_name": product, "worker": worker,
        "work_date": "2026-08-04", "total_amount": 100,
        "details": [
            {"material_name": "원료A", "actual_amount": 60, "material_lot": "LA"},
            {"material_name": "원료B", "actual_amount": None, "material_lot": "LB"},
        ],
    }, headers=csrf())
    assert res.status_code == 400, res.text
    assert "실제량을 비울 수 없습니다" in res.text and "원료B" in res.text


# ── 마이그레이션 하위호환 (운용 DB 안전성) ──────────────────────────
def test_migration_on_existing_db_backfills_and_preserves_rows(tmp_path):
    """옛 스키마(purpose 없음 · 멱등 테이블 없음) DB 에 마이그레이션을 적용해도

    ① 기존 행이 그대로 살아있고 ② purpose 는 'rescale' 로 백필되며
    ③ 새 테이블이 생기고 ④ 재적용(멱등)해도 값이 변하지 않는다.
    """
    import src.db.connection as dbconn
    import src.db.schema as schema
    from src.db.migrations import apply_schema_migrations

    db_path = tmp_path / "legacy.db"
    dbconn.DATA_DIR = tmp_path
    dbconn.DATABASE_PATH = db_path
    schema.init_db()  # 현행 스키마로 1회 생성

    # ── '옛 운용 DB' 로 되돌린다: purpose 컬럼 제거 + 멱등 테이블 제거 + 마커 삭제.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(blend_rescale_approvals)")}
    if "purpose" in cols:
        conn.execute("ALTER TABLE blend_rescale_approvals DROP COLUMN purpose")
    conn.execute("DROP TABLE IF EXISTS blend_save_requests")
    conn.execute("DELETE FROM schema_migrations WHERE name LIKE 'blend_rescale_approvals_purpose%'")
    conn.execute(
        "INSERT INTO blend_rescale_approvals (approver, created_at, used) VALUES (?, ?, 0)",
        ("옛책임자", "2026-08-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO blend_rescale_approvals (approver, created_at, used) VALUES (?, ?, 1)",
        ("이미쓴책임자", "2026-08-01T00:10:00Z"),
    )
    conn.commit()

    apply_schema_migrations(conn)
    conn.commit()

    rows = conn.execute(
        "SELECT approver, used, purpose FROM blend_rescale_approvals ORDER BY id"
    ).fetchall()
    assert [r["approver"] for r in rows] == ["옛책임자", "이미쓴책임자"]
    assert [r["used"] for r in rows] == [0, 1]              # 기존 값 보존
    assert [r["purpose"] for r in rows] == ["rescale", "rescale"]  # 백필
    save_tbl = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='blend_save_requests'"
    ).fetchone()
    assert save_tbl is not None

    # 재적용해도 그대로(멱등)
    conn.execute("UPDATE blend_rescale_approvals SET purpose = 'manual' WHERE approver = '옛책임자'")
    conn.commit()
    apply_schema_migrations(conn)
    conn.commit()
    again = conn.execute(
        "SELECT purpose FROM blend_rescale_approvals WHERE approver = '옛책임자'"
    ).fetchone()
    assert again["purpose"] == "manual", "백필은 1회만 — 이후 값을 덮어쓰지 않는다"
    conn.close()
