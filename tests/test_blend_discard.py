"""계량 중 자재 폐기 기록(discard_events) — '처음부터 다시' 재계량의 실물 흔적.

편차 강제 체계에서 최종 기록은 항상 이론량과 일치하므로, 재계량 때 버린 자재는
이 기록이 아니면 어디에도 남지 않는다. 저장을 막지 않는 순수 기록임을 잠근다.
"""

from __future__ import annotations

import json
import sqlite3

from src.services import blend_service as bs


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE blend_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_lot TEXT NOT NULL,
            discard_events_json TEXT
        );
        INSERT INTO blend_records (product_lot) VALUES ('P-1');
        """
    )
    return conn


def test_discard_events_normalized_and_saved():
    conn = _conn()
    saved = bs.apply_discard_events_to_record(conn, 1, [
        {"material_name": " 자재A ", "material_code": "C1", "amount_g": 440.006},
        {"material_name": "", "amount_g": 10},            # 이름 없음 → 제외
        {"material_name": "B", "amount_g": 0},             # 0 이하 → 제외
        {"material_name": "C", "amount_g": "abc"},        # 비수치 → 제외
        {"material_name": "D", "amount_g": -5},            # 음수 → 제외
    ])
    assert saved is not None
    events = json.loads(saved)
    assert events == [
        {"material_name": "자재A", "material_code": "C1", "amount_g": 440.01}
    ]
    row = conn.execute("SELECT discard_events_json FROM blend_records WHERE id=1").fetchone()
    assert row["discard_events_json"] == saved


def test_discard_events_empty_or_all_invalid_writes_nothing():
    conn = _conn()
    assert bs.apply_discard_events_to_record(conn, 1, None) is None
    assert bs.apply_discard_events_to_record(conn, 1, []) is None
    assert bs.apply_discard_events_to_record(conn, 1, [{"material_name": "", "amount_g": 1}]) is None
    row = conn.execute("SELECT discard_events_json FROM blend_records WHERE id=1").fetchone()
    assert row["discard_events_json"] is None


def test_discard_events_capped_at_limit():
    conn = _conn()
    events = [{"material_name": f"M{i}", "amount_g": 1.0} for i in range(30)]
    saved = bs.apply_discard_events_to_record(conn, 1, events)
    assert len(json.loads(saved)) == bs.DISCARD_EVENTS_MAX


def test_discard_events_round_trip_via_api():
    """저장 body.discard_events → 기록 상세의 discard_events_json 왕복."""
    import importlib
    import uuid

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    worker = "폐기작업" + uuid.uuid4().hex[:6]
    prod = "DISC" + uuid.uuid4().hex[:4]

    def csrf_headers():
        tok = client.cookies.get("csrftoken")
        return {"x-csrftoken": tok} if tok else {}

    client.get("/api/blend/records")  # csrf 쿠키 확보
    client.post("/api/workers", json={"name": worker}, headers=csrf_headers())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf_headers())
    created = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-08-05",
        "total_amount": 100, "scale": "M-65",
        "details": [
            {"material_name": "A", "ratio": 60, "theory_amount": 60, "actual_amount": 60, "material_lot": "LA"},
            {"material_name": "B", "ratio": 40, "theory_amount": 40, "actual_amount": 40, "material_lot": "LB"},
        ],
        "discard_events": [
            {"material_name": "A", "material_code": "", "amount_g": 12.5},
        ],
    }, headers=csrf_headers())
    assert created.status_code == 200, created.text
    rid = created.json()["id"]

    rec = client.get(f"/api/blend/records/{rid}").json()
    events = json.loads(rec["discard_events_json"])
    assert events == [{"material_name": "A", "material_code": "", "amount_g": 12.5}]

    # 폐기 없는 저장은 컬럼이 비어 있어야 한다(기존 동작 불변).
    created2 = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-08-05",
        "total_amount": 100, "scale": "M-65",
        "details": [
            {"material_name": "A", "ratio": 60, "theory_amount": 60, "actual_amount": 60, "material_lot": "LA"},
            {"material_name": "B", "ratio": 40, "theory_amount": 40, "actual_amount": 40, "material_lot": "LB"},
        ],
    }, headers=csrf_headers())
    assert created2.status_code == 200, created2.text
    rec2 = client.get(f"/api/blend/records/{created2.json()['id']}").json()
    assert rec2["discard_events_json"] is None
