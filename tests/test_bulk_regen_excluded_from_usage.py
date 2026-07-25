"""일괄 재생성 기록이 소비·생산 집계에 섞이지 않는지 검증 (2026-07-25 감사).

일괄 재생성(create_bulk)은 실제 계량이 아니라 문서·계획용으로 이론값을 복제한 기록이다
(actual = theory, 자재 LOT 없음, is_bulk_regenerated=1). 그런데 집계 쿼리 어디에도 이
플래그 조건이 없어서, 인허가 서류용으로 일괄 생성한 건이 자재 사용량에 그대로 더해지고
그 숫자가 /public/material-usage 를 통해 상위 재고 대시보드로 나가 실제로 쓰지 않은
자재가 차감되고 있었다.

LOT 존재 확인·역추적은 반대로 일괄 재생성 기록도 포함해야 하므로(유효한 LOT 이다)
여기서는 '집계에서만' 빠지는지를 확인한다.
"""

import importlib
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


def _uid():
    return uuid.uuid4().hex[:6].upper()


def test_bulk_regenerated_record_is_not_counted_as_consumption():
    client = _client()
    headers = _mgr(client)
    tag = _uid()
    material = f"벌크자재{tag}"
    product = f"벌크제품{tag}"

    from src.db import get_connection

    # 일반 기록 1건(실제 계량) + 일괄 재생성 기록 1건을 같은 자재로 직접 심는다.
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO materials (name, unit_type, unit, color_group) "
            "VALUES (?, 'weight', 'g', 'none')",
            (material,),
        )
        for lot_suffix, is_bulk in (("01", 0), ("02", 1)):
            cur = conn.execute(
                "INSERT INTO blend_records "
                "(product_lot, product_name, worker, work_date, total_amount, status, "
                " created_at, is_bulk_regenerated) "
                "VALUES (?, ?, '집계작업', '2026-07-25', 100, 'completed', "
                "        '2026-07-25T00:00:00Z', ?)",
                (f"{product}2607250{lot_suffix}", product, is_bulk),
            )
            conn.execute(
                "INSERT INTO blend_details "
                "(blend_record_id, material_name, ratio, theory_amount, actual_amount, "
                " sequence_order, created_at) "
                "VALUES (?, ?, 100, 100, 100, 1, '2026-07-25T00:00:00Z')",
                (cur.lastrowid, material),
            )
        conn.commit()

    # 자재 사용량 — 실제 계량 1건(100g)만 잡혀야 한다(일괄 재생성분 100g 은 제외).
    usage = client.get(
        "/api/blend/material-usage",
        params={"start_date": "2026-07-01", "end_date": "2026-07-31"},
        headers=headers,
    )
    assert usage.status_code == 200, usage.text
    mine = [i for i in usage.json()["items"] if i["material_name"] == material]
    assert len(mine) == 1, mine
    assert mine[0]["total_actual"] == 100, mine[0]

    # 제품별 빈도 — 배치 1건만.
    prod = client.get(
        "/api/blend/product-usage",
        params={"start_date": "2026-07-01", "end_date": "2026-07-31"},
        headers=headers,
    )
    assert prod.status_code == 200, prod.text
    rows = [i for i in prod.json()["items"] if i["product_name"] == product]
    assert len(rows) == 1 and rows[0]["batch_count"] == 1, rows

    # 기록 목록에는 두 건 다 보인다 — 집계에서만 빠지고 기록 자체는 정상 조회·추적 대상.
    recs = client.get("/api/blend/records", params={"search": product}, headers=headers)
    assert len({r["product_lot"] for r in recs.json()["items"]}) == 2
