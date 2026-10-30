"""레시피 판 삭제 + 연결 기록 함께 삭제 — 단건 완전 삭제와 같은 통제(사유 필수·기록별 사본 감사).

예전엔 연결 기록을 함께 지우면 감사에 건수만 남았다. 가장 파괴적인 경로가 가장 약한 증적을
남기던 것을 막는다.
"""

import importlib
import json
import uuid


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _mgr(client):
    assert client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    ).status_code == 200
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _recipe_with_records(client, headers, n):
    product = "RDR" + uuid.uuid4().hex[:6].upper()
    res = client.post(
        "/api/recipes/import",
        json={"raw_text": f"반제품명\t원료A\t원료B\n{product}\t60\t40", "force": True},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    rid = res.json()["created_ids"][0]
    worker = "삭제검증" + uuid.uuid4().hex[:6]
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    lots = []
    for _ in range(n):
        rec = client.post(
            "/api/blend/records",
            json={
                "recipe_id": rid, "product_name": product, "worker": worker,
                "work_date": "2026-10-30", "total_amount": 100,
                "details": [
                    {"material_name": "원료A", "ratio": 60, "theory_amount": 60, "actual_amount": 60, "material_lot": "LA"},
                    {"material_name": "원료B", "ratio": 40, "theory_amount": 40, "actual_amount": 40, "material_lot": "LB"},
                ],
            },
            headers=headers,
        )
        assert rec.status_code == 200, rec.text
        lots.append(rec.json()["product_lot"])
    return rid, lots


def test_delete_with_records_requires_reason_and_snapshots_each_record():
    client = _client()
    headers = _mgr(client)
    rid, lots = _recipe_with_records(client, headers, 2)

    # 사유 없음 → 400, 아무것도 지워지지 않는다.
    res = client.delete(f"/api/recipes/{rid}?delete_blend_records=1", headers=headers)
    assert res.status_code == 400
    from src.db import get_connection

    with get_connection() as conn:
        left = conn.execute(
            "SELECT COUNT(*) FROM blend_records WHERE recipe_id = ?", (rid,)
        ).fetchone()[0]
    assert left == 2

    res = client.delete(
        f"/api/recipes/{rid}", params={"delete_blend_records": 1, "reason": "시험 등록 잘못"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT target_label, details_json FROM audit_logs "
            "WHERE action = 'blend_record_deleted' AND target_label IN (?, ?)",
            tuple(lots),
        ).fetchall()
        recipe_audit = conn.execute(
            "SELECT details_json FROM audit_logs WHERE action = 'recipe_deleted' "
            "AND target_id = ? ORDER BY id DESC LIMIT 1",
            (str(rid),),
        ).fetchone()
    assert sorted(r["target_label"] for r in rows) == sorted(lots)
    for r in rows:
        d = json.loads(r["details_json"])
        assert d["reason"] == "시험 등록 잘못" and d["via_recipe_delete"] == rid
        assert d["snapshot"]["header"]["product_lot"] == r["target_label"]
        assert [row[0] for row in d["snapshot"]["rows"]] == ["원료A", "원료B"]
    assert json.loads(recipe_audit["details_json"])["reason"] == "시험 등록 잘못"


def test_delete_without_records_needs_no_reason():
    client = _client()
    headers = _mgr(client)
    rid, _ = _recipe_with_records(client, headers, 0)
    res = client.delete(f"/api/recipes/{rid}?delete_blend_records=1", headers=headers)
    assert res.status_code == 200, res.text
