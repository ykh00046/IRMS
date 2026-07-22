"""증량 승인제 — 책임자 확인(ack)·조회 라우터 검증 (rescale-approval 2026-07-22).

대상: src/routers/blend_rescale_ack_routes.py
  GET  /blend/rescales/unacked            (책임자 전용) 미확인 증량 목록
  POST /blend/records/{id}/rescale-ack    (책임자 전용) 확인 처리 + audit
  GET  /blend/rescales/summary            (조회 개방) 배지·모달용 요약

기존 test_item_code_admin.py 의 in-memory 클라이언트/로그인/직접 INSERT 패턴을 따른다.
테스트 DB(.tmp-tests/pytest-data/irms.db)는 실행 간 유지되므로, 이 모듈이 심은
blend_records 행(product_name LIKE 'RESCTEST%')은 각 테스트 종료 후 정리한다.

커버:
  1. unacked 목록에 미확인 건이 노출된다.
  2. ack → rescale_unacked=0 + audit(blend_rescale_acked) 행 생성.
  3. 비책임자/미로그인 → 401/403.
  4. 없는 id → 404.
  5. 이미 확인된 건 재확인 → 멱등(acked_already=True).
  6. summary(개방) 는 rescale_count>0 만 노출한다.
"""

import importlib
import json
import uuid

import pytest


@pytest.fixture(autouse=True)
def _cleanup_test_records():
    yield
    from src.db import get_connection

    with get_connection() as conn:
        conn.execute("DELETE FROM blend_records WHERE product_name LIKE 'RESCTEST%'")
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


def _seed_record(conn, *, product, worker="홍길동", unacked=1, count=1, events=None):
    """rescale 컬럼을 직접 채운 blend_records 행 삽입 → id 반환."""
    if events is None:
        events = [
            {"before_total": 24000, "after_total": 25000, "absence_reason": "야간"}
        ]
    cur = conn.execute(
        "INSERT INTO blend_records "
        "(product_lot, product_name, worker, work_date, total_amount, status, "
        " created_at, rescale_events_json, rescale_count, rescale_unacked) "
        "VALUES (?, ?, ?, '2026-07-22', 25000, 'completed', '2026-07-22T00:00:00Z', ?, ?, ?)",
        (f"{product}L", product, worker, json.dumps(events), count, unacked),
    )
    conn.commit()
    return cur.lastrowid


# ---------------- 1. unacked 목록 노출 ----------------


def test_unacked_list_shows_record():
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"RESCTEST{_uid()}"
    with get_connection() as conn:
        rid = _seed_record(conn, product=product, worker="김작업")

    res = client.get("/api/blend/rescales/unacked", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    ids = [it["id"] for it in body["items"]]
    assert rid in ids
    assert body["total"] == len(body["items"])
    item = next(it for it in body["items"] if it["id"] == rid)
    assert item["product_name"] == product
    assert item["product_lot"] == f"{product}L"
    assert item["worker"] == "김작업"
    # 이벤트가 JSON 리스트로 파싱되어 내려온다.
    assert isinstance(item["rescale_events"], list)
    assert item["rescale_events"][0]["after_total"] == 25000


def test_acked_record_not_in_unacked_list():
    """rescale_count>0 이지만 이미 확인된(unacked=0) 건은 목록에서 빠진다."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"RESCTEST{_uid()}"
    with get_connection() as conn:
        rid = _seed_record(conn, product=product, unacked=0, count=1)

    res = client.get("/api/blend/rescales/unacked", headers=headers)
    assert res.status_code == 200
    ids = [it["id"] for it in res.json()["items"]]
    assert rid not in ids


# ---------------- 2. ack → 확인 처리 + audit ----------------


def test_ack_clears_flag_and_writes_audit():
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"RESCTEST{_uid()}"
    with get_connection() as conn:
        rid = _seed_record(conn, product=product)

    res = client.post(f"/api/blend/records/{rid}/rescale-ack", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok"
    assert body["record_id"] == rid
    assert body["acked_already"] is False

    with get_connection() as conn:
        flag = conn.execute(
            "SELECT rescale_unacked FROM blend_records WHERE id=?", (rid,)
        ).fetchone()["rescale_unacked"]
        assert flag == 0
        arow = conn.execute(
            "SELECT action, target_type, target_id FROM audit_logs "
            "WHERE action='blend_rescale_acked' AND target_id=? ORDER BY id DESC LIMIT 1",
            (str(rid),),
        ).fetchone()
    assert arow is not None
    assert arow["target_type"] == "blend_record"


# ---------------- 3. 권한: 비책임자/미로그인 차단 ----------------


def test_unacked_list_requires_manager():
    client = _client()
    res = client.get("/api/blend/rescales/unacked")
    assert res.status_code in (401, 403)


def test_ack_requires_manager():
    client = _client()
    res = client.post("/api/blend/records/1/rescale-ack")
    assert res.status_code in (401, 403)


# ---------------- 4. 없는 id → 404 ----------------


def test_ack_unknown_id_404():
    client = _client()
    headers = _login(client)

    res = client.post("/api/blend/records/9999999/rescale-ack", headers=headers)
    assert res.status_code == 404


# ---------------- 5. 멱등: 이미 확인된 건 재확인 ----------------


def test_ack_idempotent_when_already_acked():
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"RESCTEST{_uid()}"
    with get_connection() as conn:
        rid = _seed_record(conn, product=product, unacked=0, count=1)

    res = client.post(f"/api/blend/records/{rid}/rescale-ack", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok"
    assert body["acked_already"] is True


# ---------------- 6. summary(개방) — rescale_count>0 만 ----------------


def test_summary_lists_only_rescaled_records():
    client = _client()

    from src.db import get_connection

    product_r = f"RESCTEST{_uid()}"    # 증량 있음
    product_n = f"RESCTEST{_uid()}"    # 증량 없음(count=0)
    with get_connection() as conn:
        rid = _seed_record(conn, product=product_r, unacked=1, count=2)
        nid = _seed_record(conn, product=product_n, unacked=0, count=0)

    # 로그인 없이(개방) 호출 가능해야 한다.
    res = client.get("/api/blend/rescales/summary")
    assert res.status_code == 200, res.text
    by_id = {it["id"]: it for it in res.json()["items"]}
    assert rid in by_id
    assert nid not in by_id  # count=0 은 빠진다
    assert by_id[rid]["rescale_count"] == 2
    assert by_id[rid]["rescale_unacked"] is True
    assert isinstance(by_id[rid]["rescale_events"], list)


# ---------------- 7. 개방 summary 개인정보 마스킹(정책 ⓑ) ----------------


def test_summary_strips_approver_and_absence_reason_but_unacked_keeps_them():
    """개방 summary 는 approver(책임자명)/absence_reason(부재 사유)을 가린다.

    책임자 전용 unacked 엔드포인트는 전체 detail 을 그대로 유지한다(정책 ⓑ 프라이버시).
    """
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"RESCTEST{_uid()}"
    events = [
        {"before_total": 24000, "after_total": 25000, "approver": "책임자김"},
        {"before_total": 24000, "after_total": 25500, "absence_reason": "야간 무인"},
    ]
    with get_connection() as conn:
        rid = _seed_record(conn, product=product, unacked=1, count=2, events=events)

    # 개방 summary — 개인정보 필드 제거, 수치(before/after)는 유지.
    res = client.get("/api/blend/rescales/summary")
    assert res.status_code == 200, res.text
    item = next(it for it in res.json()["items"] if it["id"] == rid)
    for ev in item["rescale_events"]:
        assert "approver" not in ev
        assert "absence_reason" not in ev
    assert item["rescale_events"][0]["after_total"] == 25000

    # 책임자 unacked — 전체 detail 유지.
    res2 = client.get("/api/blend/rescales/unacked", headers=headers)
    assert res2.status_code == 200, res2.text
    item2 = next(it for it in res2.json()["items"] if it["id"] == rid)
    joined = json.dumps(item2["rescale_events"], ensure_ascii=False)
    assert "책임자김" in joined
    assert "야간 무인" in joined
