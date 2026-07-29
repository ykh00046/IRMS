"""취소된 배합 기록의 보존기한 자동 정리 검사.

취소(soft)는 실수 되돌리기용이고 실수는 하루 이틀 안에 드러난다는 현장 판단
(2026-07-29)에 따라, 취소 후 N일(기본 3)이 지나면 물리 삭제한다. 파괴적 동작이므로
하드 삭제와 같은 원칙 — 기록 전체 스냅샷을 감사로그(blend_record_purged)에 남긴다 —
이 지켜지는지, 그리고 기한 안쪽 취소분·정상 기록은 절대 건드리지 않는지 고정한다.
"""

import importlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app), cfg


def _csrf(client):
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _make_canceled_record(client, product):
    """레시피 임포트 → 기록 생성 → soft 취소. record id 반환."""
    headers = _csrf(client)
    raw = f"반제품명\t자재A\n{product}\t1000"
    res = client.post("/api/recipes/import",
                      json={"raw_text": raw, "force": True}, headers=headers)
    assert res.status_code == 200, res.text
    rid = res.json()["created_ids"][0]
    detail = client.get(f"/api/blend/recipes/{rid}").json()
    details = [
        {
            "material_id": it.get("material_id"),
            "material_name": it["material_name"],
            "material_code": it.get("material_code"),
            "ratio": it.get("ratio"),
            "theory_amount": it["theory_amount"],
            "actual_amount": it["theory_amount"],
            "material_lot": "L1",
            "sequence_order": i + 1,
            "manual_entry": False,
            "carried_over": False,
        }
        for i, it in enumerate(detail["items"])
    ]
    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": product, "worker": "정리테스트",
        "work_date": "2026-07-29", "total_amount": 1000, "scale": "M-65",
        "details": details,
    }, headers=_csrf(client))
    assert res.status_code == 200, res.text
    record_id = res.json()["id"]
    res = client.delete(f"/api/blend/records/{record_id}", headers=_csrf(client))
    assert res.status_code == 200, res.text
    return record_id


def test_purge_deletes_only_expired_canceled_with_snapshot():
    client, cfg = _client()
    client.post("/api/auth/management-login",
                json={"username": "admin", "password": "admin"})
    client.post("/api/workers", json={"name": "정리테스트"}, headers=_csrf(client))
    client.post("/api/blend/session/login", json={"worker": "정리테스트"},
                headers=_csrf(client))

    p_old = "PG" + uuid.uuid4().hex[:6].upper()
    p_new = "PG" + uuid.uuid4().hex[:6].upper()
    old_id = _make_canceled_record(client, p_old)
    new_id = _make_canceled_record(client, p_new)

    # 오래된 취소로 백데이트(취소 시각 = updated_at)
    conn = sqlite3.connect(cfg.DATABASE_PATH)
    backdated = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE blend_records SET updated_at = ? WHERE id = ?", (backdated, old_id))
    conn.commit()
    conn.close()

    from src.db import get_connection
    from src.services.record_delete_service import purge_expired_canceled

    with get_connection() as connection:
        purged = purge_expired_canceled(connection, retention_days=3)
        connection.commit()

    purged_ids = {p["id"] for p in purged}
    assert old_id in purged_ids, "기한 지난 취소분이 정리되지 않았습니다"
    assert new_id not in purged_ids, "기한 안쪽 취소분까지 지웠습니다"

    # 실제로 사라졌는지 / 남았는지
    assert client.get(f"/api/blend/records/{old_id}").status_code == 404
    assert client.get(f"/api/blend/records/{new_id}").status_code == 200

    # 스냅샷 감사가 남았는지 — 자재 행까지
    conn = sqlite3.connect(cfg.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT details_json FROM audit_logs WHERE action = 'blend_record_purged' "
        "AND target_id = ? ORDER BY id DESC LIMIT 1",
        (str(old_id),),
    ).fetchone()
    conn.close()
    assert row is not None, "blend_record_purged 감사로그가 없습니다"
    details = json.loads(row["details_json"])
    assert details["snapshot"]["header"]["product_lot"].startswith(p_old)
    assert details["snapshot"]["rows"], "자재 행 스냅샷이 비었습니다"


def test_purge_disabled_with_zero_retention():
    client, cfg = _client()
    client.post("/api/auth/management-login",
                json={"username": "admin", "password": "admin"})
    client.post("/api/workers", json={"name": "정리테스트"}, headers=_csrf(client))
    client.post("/api/blend/session/login", json={"worker": "정리테스트"},
                headers=_csrf(client))
    p = "PZ" + uuid.uuid4().hex[:6].upper()
    rec_id = _make_canceled_record(client, p)

    conn = sqlite3.connect(cfg.DATABASE_PATH)
    backdated = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE blend_records SET updated_at = ? WHERE id = ?", (backdated, rec_id))
    conn.commit()
    conn.close()

    from src.db import get_connection
    from src.services.record_delete_service import purge_expired_canceled

    with get_connection() as connection:
        purged = purge_expired_canceled(connection, retention_days=0)
        connection.commit()
    assert purged == []
    assert client.get(f"/api/blend/records/{rec_id}").status_code == 200


def test_root_redirects_to_dashboard():
    """'/' 는 대시보드로 — 구 홈 런처는 은퇴(개편 2026-07-29)."""
    client, _cfg = _client()
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/dashboard"
