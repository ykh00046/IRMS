"""배합 기록 화면 재설계 서버 계약(2026-08-14) 검증.

  1. GET /blend/records 목록 행에 증량/수기 미확인 플래그가 실린다 —
     별도 summary(무조건 최신 1000건) 왕복과 그 절단 누락을 없애는 기반.
  2. unacked=1 은 서버 전체에서 거른다 — 종전 클라이언트 필터는 LIMIT 절단 뒤라
     상한 밖 미확인 건이 영영 안 보였다(통제 사각).
  3. GET /blend/records/product-names — 필터 모집단이 목록과 같은 테이블에서 나온다
     (취소분만 있는 제품도 포함).
  4. 단건 Excel(sign=1) 이 200 으로 응답한다 — 종전에는 서명 체크가 조용히 무시됐다.
"""

from __future__ import annotations

import importlib
import uuid


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


def _make_record(client, prod, work_date):
    worker = "기록계약" + uuid.uuid4().hex[:4]
    client.post("/api/workers", json={"name": worker}, headers=_csrf(client))
    client.post(
        "/api/blend/session/login", json={"worker": worker}, headers=_csrf(client)
    )
    res = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": work_date,
        "total_amount": 100, "scale": "M-65",
        "details": [
            {"material_name": "A", "ratio": 100, "theory_amount": 100,
             "actual_amount": 100, "material_lot": "L1"},
        ],
    }, headers=_csrf(client))
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _seed_recipe(client, prod):
    raw = f"반제품명\tA\n{prod}\t100"
    res = client.post(
        "/api/recipes/import",
        json={"raw_text": raw, "force": True},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text


def _login_admin(client):
    client.get("/api/blend/records")
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200, res.text


def test_records_list_carries_unacked_flags_and_server_filter():
    from src.db import get_connection

    client = _client()
    _login_admin(client)
    prod = "REC" + uuid.uuid4().hex[:5].upper()
    _seed_recipe(client, prod)
    old_id = _make_record(client, prod, "2026-08-01")
    new_id = _make_record(client, prod, "2026-08-13")
    # 오래된 기록만 증량 미확인 상태로 만든다.
    with get_connection() as conn:
        conn.execute(
            "UPDATE blend_records SET rescale_count = 1, rescale_unacked = 1 "
            "WHERE id = ?", (old_id,),
        )
        conn.commit()

    res = client.get("/api/blend/records", params={"search": prod})
    assert res.status_code == 200, res.text
    by_id = {it["id"]: it for it in res.json()["items"]}
    assert by_id[old_id]["rescale_unacked"] is True
    assert by_id[old_id]["rescale_count"] == 1
    assert by_id[new_id]["rescale_unacked"] is False
    assert "manual_unacked" in by_id[new_id]

    # 통제 사각 회귀 방지: limit=1 이면 최신(확인 완료)만 잘리지만, unacked=1 은
    # 서버가 전체에서 거르므로 상한 밖의 오래된 미확인 건이 나온다.
    res = client.get(
        "/api/blend/records",
        params={"search": prod, "unacked": "true", "limit": 1},
    )
    data = res.json()
    assert [it["id"] for it in data["items"]] == [old_id]
    assert data["total_available"] == 1


def test_product_names_includes_canceled_only_products():
    client = _client()
    _login_admin(client)
    prod = "CXL" + uuid.uuid4().hex[:5].upper()
    _seed_recipe(client, prod)
    rid = _make_record(client, prod, "2026-08-10")
    # 유일한 기록을 취소 — 목록(취소 포함)에는 보이는데 종전 필터 모집단에는 없던 경우.
    res = client.request(
        "DELETE", f"/api/blend/records/{rid}",
        json={"reason": "계약 테스트"}, headers=_csrf(client),
    )
    assert res.status_code == 200, res.text

    names = client.get("/api/blend/records/product-names").json()["items"]
    assert prod in names


def test_single_excel_export_supports_sign_param():
    client = _client()
    _login_admin(client)
    prod = "SGN" + uuid.uuid4().hex[:5].upper()
    _seed_recipe(client, prod)
    rid = _make_record(client, prod, "2026-08-12")

    plain = client.get(f"/api/blend/records/{rid}/export")
    assert plain.status_code == 200
    signed = client.get(f"/api/blend/records/{rid}/export", params={"sign": "true"})
    # 도장 템플릿이 없는 환경이면 실패 표식 경로로라도 xlsx 가 나온다(무언 미서명 금지).
    assert signed.status_code == 200
    assert signed.headers["content-type"].startswith(
        "application/vnd.openxmlformats"
    )
