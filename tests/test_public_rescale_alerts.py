"""증량 미확인 트레이 알림 공개 API — GET /api/public/rescale-alerts.

책임자 미확인 증량(blend_records.rescale_unacked=1)을 트레이가 폴링해 반복 알림한다.
트레이 공개 API 와 동일한 내부망/토큰 보호(main.py protected_prefixes) — 비사설 IP 는 403.

seed 는 rescale_unacked 컬럼을 직접 SQL 로 채워(마이그레이션 완료 전제),
페이로드({count, items:[{id, product_name, product_lot, work_date, worker}]})와
내부망 접근 제한을 test_public_material_usage / test_security_headers 관례대로 검증한다.
"""

from __future__ import annotations

import importlib
import uuid

from fastapi.testclient import TestClient


def _reload_app():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    return mainmod


def _seed_record(product_name: str, product_lot: str, *, unacked: int,
                 worker: str = "증량작업", work_date: str = "2026-07-22") -> int:
    """blend_records 에 한 건을 직접 INSERT 하고 rescale_unacked 를 지정한다."""
    from src.db import get_connection

    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO blend_records "
            "(product_lot, product_name, worker, work_date, total_amount, "
            " status, rescale_unacked, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'completed', ?, ?)",
            (product_lot, product_name, worker, work_date, 1000.0, unacked,
             f"{work_date}T00:00:00Z"),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _internal_client(mainmod) -> TestClient:
    # 사설 IP 위장 클라이언트 — InternalNetworkOnlyMiddleware 통과.
    return TestClient(mainmod.app, client=("192.168.11.108", 50000))


def test_rescale_alerts_payload_only_unacked():
    """rescale_unacked=1 인 기록만, 계약대로의 필드로 반환된다."""
    mainmod = _reload_app()

    tag = uuid.uuid4().hex[:6].upper()
    prod_a, lot_a = f"RS-A-{tag}", f"LOT-A-{tag}"
    prod_b, lot_b = f"RS-B-{tag}", f"LOT-B-{tag}"
    prod_acked, lot_acked = f"RS-C-{tag}", f"LOT-C-{tag}"

    id_a = _seed_record(prod_a, lot_a, unacked=1)
    id_b = _seed_record(prod_b, lot_b, unacked=1)          # 더 최신(더 큰 id)
    id_acked = _seed_record(prod_acked, lot_acked, unacked=0)

    res = _internal_client(mainmod).get("/api/public/rescale-alerts")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["count"] == len(body["items"])
    by_id = {item["id"]: item for item in body["items"]}

    # 미확인 두 건은 포함, 확인 완료(rescale_unacked=0)는 제외.
    assert id_a in by_id and id_b in by_id
    assert id_acked not in by_id

    # 필드 계약 — 정확히 이 키들.
    assert set(by_id[id_a].keys()) == {"id", "product_name", "product_lot", "work_date", "worker"}
    assert by_id[id_a]["product_name"] == prod_a
    assert by_id[id_a]["product_lot"] == lot_a
    assert by_id[id_a]["work_date"] == "2026-07-22"
    assert by_id[id_a]["worker"] == "증량작업"

    # 최신순(id DESC) — 나중에 넣은 B 가 A 보다 앞선다.
    order = [item["id"] for item in body["items"]]
    assert order.index(id_b) < order.index(id_a)


def test_rescale_alerts_capped_at_20():
    """최대 20건으로 제한된다(미확인이 더 많아도)."""
    mainmod = _reload_app()

    tag = uuid.uuid4().hex[:6].upper()
    for i in range(22):
        _seed_record(f"CAP-{tag}-{i}", f"LOT-{tag}-{i}", unacked=1)

    res = _internal_client(mainmod).get("/api/public/rescale-alerts")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 20
    assert len(body["items"]) == 20


def test_rescale_alerts_blocked_outside_internal_network():
    """비사설 IP(기본 TestClient) 는 403 — 트레이 공개 API 와 동일한 내부망 제한."""
    mainmod = _reload_app()
    client = TestClient(mainmod.app)  # host == 'testclient' → 비사설
    res = client.get("/api/public/rescale-alerts")
    assert res.status_code == 403
    assert res.json() == {"detail": "INTERNAL_NETWORK_ONLY"}
