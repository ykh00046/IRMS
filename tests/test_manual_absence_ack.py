"""수기 입력 '책임자 부재 진행' 사후 확인 루프 (2026-07-25).

저울 전용 모드에서 비밀번호 승인 없이 사유만 남기고 손입력한 기록을 증량 부재와
동일하게 책임자가 사후 확인(ack)하도록 격상한 흐름을 검증한다.

대상:
  POST /blend/records                              manual_absence_reason 저장 → manual_unacked=1
  GET  /blend/manual-absences/unacked              (책임자 전용) 미확인 목록
  POST /blend/records/{id}/manual-absence-ack      (책임자 전용) 확인 + audit
  GET  /blend/rescales/summary                     (개방) manual_unacked 플래그 노출, 사유는 미노출
  GET  /public/rescale-alerts                      트레이 채널이 두 종류를 kind 로 구분해 함께 전달

test_rescale_ack.py 의 in-memory 클라이언트/로그인/직접 INSERT 패턴을 따른다.
"""

import importlib
import uuid

import pytest


@pytest.fixture(autouse=True)
def _cleanup_test_records():
    yield
    from src.db import get_connection

    with get_connection() as conn:
        conn.execute("DELETE FROM blend_records WHERE product_name LIKE 'MANABS%'")
        conn.commit()


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _internal_client():
    """사설 IP 위장 클라이언트 — /public/* 의 InternalNetworkOnlyMiddleware 통과."""
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app, client=("192.168.11.108", 50000))


def _login(client, username="admin", password="admin"):
    res = client.post(
        "/api/auth/management-login", json={"username": username, "password": password}
    )
    assert res.status_code == 200, res.text
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _uid():
    return uuid.uuid4().hex[:8].upper()


def _seed_record(conn, *, product, worker="홍길동", reason="야간 근무 · 책임자 부재", unacked=1):
    """manual_absence 컬럼을 직접 채운 blend_records 행 삽입 → id 반환."""
    cur = conn.execute(
        "INSERT INTO blend_records "
        "(product_lot, product_name, worker, work_date, total_amount, status, "
        " created_at, manual_entry, manual_absence_reason, manual_unacked) "
        "VALUES (?, ?, ?, '2026-07-25', 1000, 'completed', '2026-07-25T00:00:00Z', 1, ?, ?)",
        (f"{product}L", product, worker, reason, unacked),
    )
    conn.commit()
    return cur.lastrowid


# ---------------- 1. 목록 노출 ----------------


def test_unacked_manual_list_shows_record():
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"MANABS{_uid()}"
    with get_connection() as conn:
        rid = _seed_record(conn, product=product, worker="김작업")

    res = client.get("/api/blend/manual-absences/unacked", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    item = next(it for it in body["items"] if it["id"] == rid)
    assert item["product_name"] == product
    assert item["worker"] == "김작업"
    # 책임자 전용 목록에는 사유가 그대로 보인다(통제 근거).
    assert item["manual_absence_reason"] == "야간 근무 · 책임자 부재"
    assert body["total"] == len(body["items"])


def test_acked_manual_record_not_listed():
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"MANABS{_uid()}"
    with get_connection() as conn:
        rid = _seed_record(conn, product=product, unacked=0)

    res = client.get("/api/blend/manual-absences/unacked", headers=headers)
    assert rid not in [it["id"] for it in res.json()["items"]]


# ---------------- 2. 확인(ack) ----------------


def test_ack_clears_flag_and_audits():
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"MANABS{_uid()}"
    with get_connection() as conn:
        rid = _seed_record(conn, product=product)
        before = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'blend_manual_absence_acked'"
        ).fetchone()[0]

    res = client.post(f"/api/blend/records/{rid}/manual-absence-ack", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["acked_already"] is False

    with get_connection() as conn:
        row = conn.execute(
            "SELECT manual_unacked, manual_absence_reason FROM blend_records WHERE id = ?",
            (rid,),
        ).fetchone()
        after = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'blend_manual_absence_acked'"
        ).fetchone()[0]
    assert row["manual_unacked"] == 0
    # 사유는 지우지 않는다 — 확인만 처리하고 근거는 기록에 남는다.
    assert row["manual_absence_reason"]
    assert after == before + 1

    # 재확인은 멱등(감사 추가 없음).
    again = client.post(f"/api/blend/records/{rid}/manual-absence-ack", headers=headers)
    assert again.status_code == 200 and again.json()["acked_already"] is True


def test_ack_requires_manager_and_404():
    client = _client()
    # 미로그인 → 401/403
    assert client.get("/api/blend/manual-absences/unacked").status_code in (401, 403)
    assert client.post("/api/blend/records/1/manual-absence-ack").status_code in (401, 403)

    headers = _login(client)
    assert client.post(
        "/api/blend/records/99999999/manual-absence-ack", headers=headers
    ).status_code == 404


# ---------------- 3. 개방 요약 / 트레이 채널 ----------------


def test_summary_exposes_flag_but_not_reason():
    """개방 요약(무로그인)은 manual_unacked 플래그만 — 사유 텍스트는 노출하지 않는다."""
    client = _client()

    from src.db import get_connection

    product = f"MANABS{_uid()}"
    secret = "비밀사유" + _uid()
    with get_connection() as conn:
        rid = _seed_record(conn, product=product, reason=secret)

    res = client.get("/api/blend/rescales/summary")
    assert res.status_code == 200, res.text
    item = next(it for it in res.json()["items"] if it["id"] == rid)
    assert item["manual_unacked"] is True
    assert item["rescale_count"] == 0        # 증량은 없다
    assert secret not in res.text            # 사유는 개방 payload 에 없다


def test_public_alerts_include_manual_with_kind():
    """트레이 채널이 증량·수기 입력을 한 목록으로 주되 kind 로 구분한다."""
    from src.db import get_connection

    product = f"MANABS{_uid()}"
    with get_connection() as conn:
        rid = _seed_record(conn, product=product)

    res = _internal_client().get("/api/public/rescale-alerts")
    assert res.status_code == 200, res.text
    item = next(it for it in res.json()["items"] if it["id"] == rid)
    assert item["kind"] == "manual"
    assert item["product_name"] == product


def test_both_kinds_collapse_to_single_item():
    """한 기록이 증량·수기 모두 미확인이면 kind='both' 로 한 건만 나간다(중복 알림 방지)."""
    from src.db import get_connection

    product = f"MANABS{_uid()}"
    with get_connection() as conn:
        rid = _seed_record(conn, product=product)
        conn.execute(
            "UPDATE blend_records SET rescale_unacked = 1, rescale_count = 1 WHERE id = ?",
            (rid,),
        )
        conn.commit()

    items = _internal_client().get("/api/public/rescale-alerts").json()["items"]
    mine = [it for it in items if it["id"] == rid]
    assert len(mine) == 1
    assert mine[0]["kind"] == "both"


# ---------------- 4. 저장 경로(POST /blend/records) ----------------


def test_create_with_manual_absence_marks_unacked():
    """배합 저장 시 manual_absence_reason 을 보내면 그 기록이 미확인으로 남는다."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    # 작업자 세션 확보(배합 저장은 작업자 세션 필요).
    workers = client.get("/api/workers").json()
    items = workers.get("items") or workers
    name = (items[0]["name"] if isinstance(items[0], dict) else items[0]) if items else None
    if not name:
        client.post("/api/workers", json={"name": "부재테스터"}, headers=headers)
        name = "부재테스터"
    assert client.post(
        "/api/blend/session/login", json={"worker": name}, headers=headers
    ).status_code == 200

    product = f"MANABS{_uid()}"
    body = {
        "product_name": product,
        "worker": name,
        "work_date": "2026-07-25",
        "total_amount": 100,
        "manual_entry": True,
        "manual_absence_reason": "야간 근무 · 책임자 부재",
        "details": [
            {
                "material_name": "테스트자재",
                "material_lot": "L-1",
                "ratio": 100,
                "theory_amount": 100,
                "actual_amount": 100,
                "manual_entry": True,
            }
        ],
    }
    res = client.post("/api/blend/records", json=body, headers=headers)
    assert res.status_code == 200, res.text
    rid = res.json()["id"]

    with get_connection() as conn:
        row = conn.execute(
            "SELECT manual_unacked, manual_absence_reason FROM blend_records WHERE id = ?",
            (rid,),
        ).fetchone()
    assert row["manual_unacked"] == 1
    assert row["manual_absence_reason"] == "야간 근무 · 책임자 부재"

    # 사유 없이 저장하면 미확인이 아니다(기존 동작 무영향).
    body2 = dict(body, manual_absence_reason=None)
    res2 = client.post("/api/blend/records", json=body2, headers=headers)
    assert res2.status_code == 200, res2.text
    with get_connection() as conn:
        row2 = conn.execute(
            "SELECT manual_unacked FROM blend_records WHERE id = ?", (res2.json()["id"],)
        ).fetchone()
    assert row2["manual_unacked"] == 0
