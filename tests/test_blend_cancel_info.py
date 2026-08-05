"""GET /api/blend/records/{id} — 취소된 기록의 cancel_info(F15).

취소 시 기록엔 status/updated_at 만 남고 사유·행위자는 감사 로그가 원본이다.
상세 API 가 감사 로그에서 사유·취소자·시각을 읽어 cancel_info 로 내려주고,
보존일(CANCELED_RETENTION_DAYS) 기준 자동 삭제 예정일(purge_at)을 계산해 준다.
화면(status.js cancelBlock)은 이 필드로 '왜·언제 취소됐고 언제 사라지는지'를
보여준다 — 없으면 복원/완전 삭제를 판단할 근거가 화면에 없다(주행 검토 F15).

패턴은 tests/test_blend_export_canceled.py 의 reload + TestClient + admin/admin.
"""

from __future__ import annotations

import importlib
import uuid


def _reload_app():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def test_canceled_record_detail_carries_cancel_info():
    client = _reload_app()
    worker = "취소상세" + uuid.uuid4().hex[:5]

    def csrf_headers():
        tok = client.cookies.get("csrftoken")
        return {"x-csrftoken": tok} if tok else {}

    client.get("/api/blend/records")  # csrf 쿠키 확보
    client.post("/api/workers", json={"name": worker}, headers=csrf_headers())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf_headers())

    prod = "CI" + uuid.uuid4().hex[:5]
    created = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-01",
        "total_amount": 100, "scale": "M-65",
        "details": [
            {"material_name": "A", "ratio": 60, "theory_amount": 60, "actual_amount": 60, "material_lot": "LA"},
            {"material_name": "B", "ratio": 40, "theory_amount": 40, "actual_amount": 40, "material_lot": "LB"},
        ],
    }, headers=csrf_headers())
    assert created.status_code == 200, created.text
    rec_id = created.json()["id"]

    # 취소 전에는 cancel_info 가 없다.
    before = client.get(f"/api/blend/records/{rec_id}")
    assert before.status_code == 200
    assert "cancel_info" not in before.json()

    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})
    canceled = client.delete(
        f"/api/blend/records/{rec_id}?reason=상세표시 검증", headers=csrf_headers()
    )
    assert canceled.status_code == 200, canceled.text

    detail = client.get(f"/api/blend/records/{rec_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "canceled"
    info = body.get("cancel_info")
    assert info, body
    assert info["reason"] == "상세표시 검증"
    assert info["actor"]  # admin 의 표시명(책임자) 또는 username
    assert info["canceled_at"]
    from src.config import CANCELED_RETENTION_DAYS

    assert info["retention_days"] == CANCELED_RETENTION_DAYS
    if CANCELED_RETENTION_DAYS > 0:
        assert info["purge_at"] and info["purge_at"] > info["canceled_at"]
