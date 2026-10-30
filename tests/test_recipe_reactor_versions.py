"""반응기별 현재판 지정(2026-10-21).

체인(v1→v2→v3)에서 반응기마다 쓸 판을 지정하고, 배합 상세 조회·단건 저장·이어서 계량
저장이 같은 규칙(recipe_helpers.resolve_recipe_for_reactor)으로 판을 고르는지 검증한다.
"""

import importlib
import uuid

import pytest


@pytest.fixture(autouse=True)
def _cleanup_test_master():
    yield
    from src.db import get_connection

    with get_connection() as conn:
        conn.execute("DELETE FROM item_code_master WHERE source = 'manual'")
        conn.commit()


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


def _import(client, headers, raw_text, **kw):
    body = {"raw_text": raw_text, "force": True}
    body.update(kw)
    res = client.post("/api/recipes/import", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


def _chain(client, headers):
    """use_reactor 레시피 3판 체인. (product, v1, v2, v3) — v3 이 현재판."""
    product = "RRV" + uuid.uuid4().hex[:6].upper()
    v1 = _import(client, headers, f"반제품명\t원료A\t원료B\n{product}\t60\t40")
    assert client.put(
        f"/api/recipes/{v1}/use-reactor", json={"use_reactor": True}, headers=headers
    ).status_code == 200
    v2 = _import(client, headers, f"반제품명\t원료A\t원료B\n{product}\t70\t30", revision_of=v1)
    v3 = _import(client, headers, f"반제품명\t원료A\t원료B\n{product}\t50\t50", revision_of=v2)
    for rid in (v2, v3):
        client.put(f"/api/recipes/{rid}/use-reactor", json={"use_reactor": True}, headers=headers)
    return product, v1, v2, v3


def _assign(client, headers, recipe_id, reactor, target, note=None):
    return client.put(
        f"/api/recipes/{recipe_id}/reactor-assignments",
        json={"reactor": reactor, "recipe_id": target, "note": note},
        headers=headers,
    )


def _blend_session(client, headers):
    worker = "반응기판" + uuid.uuid4().hex[:6]
    client.get("/api/blend/records")
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    return worker


def _details(a, b):
    return [
        {"material_name": "원료A", "ratio": a, "theory_amount": a, "actual_amount": a, "material_lot": "LA"},
        {"material_name": "원료B", "ratio": b, "theory_amount": b, "actual_amount": b, "material_lot": "LB"},
    ]


def test_assign_reactor_and_read_back():
    client = _client()
    headers = _mgr(client)
    _product, v1, v2, v3 = _chain(client, headers)

    got = client.get(f"/api/recipes/{v3}/reactor-assignments").json()
    assert got["current_id"] == v3
    assert got["use_reactor"] is True
    assert [r["reactor"] for r in got["reactors"]] == [1, 2, 3, 4]
    assert all(r["is_fallback"] and r["effective_recipe_id"] == v3 for r in got["reactors"])
    assert got["history"] == []

    res = _assign(client, headers, v3, 2, v2, note="고점도용")
    assert res.status_code == 200, res.text
    r2 = res.json()["reactors"][1]
    assert r2["recipe_id"] == v2 and r2["version_label"] == "v2"
    assert r2["effective_recipe_id"] == v2 and r2["is_fallback"] is False
    assert r2["note"] == "고점도용" and r2["assigned_by"]

    got = client.get(f"/api/recipes/{v1}/reactor-assignments").json()
    assert got["reactors"][1]["effective_recipe_id"] == v2
    assert got["reactors"][0]["is_fallback"] is True
    assert len(got["history"]) == 1
    assert got["history"][0]["reactor"] == 2 and got["history"][0]["version_label"] == "v2"

    from src.db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT details_json FROM audit_logs WHERE action = 'recipe_reactor_assigned' "
            "AND CAST(target_id AS TEXT) = ? ORDER BY id DESC LIMIT 1",
            (str(v2),),
        ).fetchone()
    assert row is not None


def test_assignment_validation_and_clear():
    client = _client()
    headers = _mgr(client)
    _product, v1, v2, v3 = _chain(client, headers)
    other = _import(client, headers, f"반제품명\t원료A\nRRO{uuid.uuid4().hex[:6].upper()}\t100")

    res = _assign(client, headers, v3, 5, v2)
    assert res.status_code == 400 and res.json()["detail"] == "반응기는 1~4입니다."
    res = _assign(client, headers, v3, 1, other)
    assert res.status_code == 400 and res.json()["detail"] == "같은 레시피의 판이 아닙니다."
    res = _assign(client, headers, v3, 1, v2, note="가" * 201)
    assert res.status_code == 400

    from src.db import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE recipes SET status = 'canceled' WHERE id = ?", (v1,))
        conn.commit()
    res = _assign(client, headers, v3, 1, v1)
    assert res.status_code == 409 and res.json()["detail"] == "취소된 판은 지정할 수 없습니다."

    assert _assign(client, headers, v3, 3, v2).status_code == 200
    res = _assign(client, headers, v3, 3, None)
    assert res.status_code == 200
    r3 = res.json()["reactors"][2]
    assert r3["recipe_id"] is None and r3["is_fallback"] is True
    assert r3["effective_recipe_id"] == v3
    assert len(res.json()["history"]) == 2


def test_blend_detail_resolves_by_reactor():
    client = _client()
    headers = _mgr(client)
    _product, _v1, v2, v3 = _chain(client, headers)

    plain = client.get(f"/api/blend/recipes/{v3}").json()["recipe"]
    assert plain["id"] == v3
    assert plain["chain_has_reactor_assignments"] is False
    assert plain["version_label"] == "v3"

    assert _assign(client, headers, v3, 2, v2, note="고점도용").status_code == 200
    client.put(f"/api/recipes/{v2}/version-name", json={"version_name": "고점도용"}, headers=headers)

    r2 = client.get(f"/api/blend/recipes/{v3}?reactor=2").json()["recipe"]
    assert r2["id"] == v2
    assert r2["reactor_assigned"] is True
    assert r2["chain_has_reactor_assignments"] is True
    assert r2["version_label"] == "v2" and r2["version_name"] == "고점도용"

    r1 = client.get(f"/api/blend/recipes/{v3}?reactor=1").json()["recipe"]
    assert r1["id"] == v3 and r1["reactor_assigned"] is False
    assert r1["chain_has_reactor_assignments"] is True

    none = client.get(f"/api/blend/recipes/{v2}").json()["recipe"]
    assert none["id"] == v3 and none["reactor_assigned"] is False

    assert client.get(f"/api/blend/recipes/{v3}?reactor=5").status_code == 422


def test_single_and_continuous_save_follow_reactor_assignment():
    client = _client()
    headers = _mgr(client)
    product, _v1, v2, v3 = _chain(client, headers)
    assert _assign(client, headers, v3, 2, v2).status_code == 200
    worker = _blend_session(client, headers)

    base = {
        "product_name": product, "worker": worker, "work_date": "2026-10-21",
        "total_amount": 100, "reactor": 2,
    }
    ok = client.post(
        "/api/blend/records", json={**base, "recipe_id": v2, "details": _details(70, 30)},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    from src.db import get_connection

    with get_connection() as conn:
        stored = conn.execute(
            "SELECT recipe_id FROM blend_records WHERE id = ?", (ok.json()["id"],)
        ).fetchone()["recipe_id"]
    assert stored == v2

    bad = client.post(
        "/api/blend/records", json={**base, "recipe_id": v3, "details": _details(50, 50)},
        headers=headers,
    )
    assert bad.status_code == 409
    assert bad.json()["detail"] == "이 반응기에 지정된 판이 아닙니다. 반응기를 다시 선택하세요."

    cont = {
        "product_name": product, "work_date": "2026-10-21", "total_amount": 100, "reactor": 2,
    }
    ok = client.post(
        "/api/blend/records/continuous",
        json={**cont, "recipe_id": v2, "lots": [_details(70, 30)]},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    bad = client.post(
        "/api/blend/records/continuous",
        json={**cont, "recipe_id": v3, "lots": [_details(50, 50)]},
        headers=headers,
    )
    assert bad.status_code == 409
    assert bad.json()["detail"] == "이 반응기에 지정된 판이 아닙니다. 반응기를 다시 선택하세요."

    # 지정 없는 반응기는 현재판(v3) 그대로.
    ok = client.post(
        "/api/blend/records",
        json={**base, "reactor": 1, "recipe_id": v3, "details": _details(50, 50)},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text


def test_delete_blocked_while_assigned_and_history_flag():
    client = _client()
    headers = _mgr(client)
    _product, _v1, v2, v3 = _chain(client, headers)
    assert _assign(client, headers, v3, 2, v2).status_code == 200

    hist = client.get(f"/api/recipes/{v3}/history").json()["items"]
    by_id = {it["id"]: it for it in hist}
    assert by_id[v2]["assigned_reactors"] == [2]
    assert by_id[v3]["assigned_reactors"] == []

    res = client.delete(f"/api/recipes/{v2}", headers=headers)
    assert res.status_code == 409
    assert res.json()["detail"] == "반응기 2에 지정된 판입니다. 지정을 먼저 바꾸세요."

    assert _assign(client, headers, v3, 2, None).status_code == 200
    res = client.delete(f"/api/recipes/{v2}", headers=headers)
    assert res.status_code == 200, res.text


def test_root_delete_keeps_assignments_on_new_root():
    client = _client()
    headers = _mgr(client)
    _product, v1, v2, v3 = _chain(client, headers)
    assert _assign(client, headers, v3, 4, v2).status_code == 200
    assert client.delete(f"/api/recipes/{v1}", headers=headers).status_code == 200
    got = client.get(f"/api/recipes/{v3}/reactor-assignments").json()
    assert got["root_id"] == v2
    assert got["reactors"][3]["effective_recipe_id"] == v2


def _iso_ago(hours):
    from datetime import datetime, timedelta, timezone

    at = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=hours)
    return at.isoformat().replace("+00:00", "Z")


def _backdate_assignments(product_root, hours):
    """지금까지의 지정 행을 hours 시간 전으로 옮긴다(같은 초 안에 지정·계량이 겹치지 않게)."""
    from src.db import get_connection

    with get_connection() as conn:
        conn.execute(
            "UPDATE recipe_reactor_assignments SET assigned_at = ? WHERE chain_root_id = ?",
            (_iso_ago(hours), product_root),
        )
        conn.commit()


def _kept_audits(target_label_like):
    from src.db import get_connection

    with get_connection() as conn:
        return conn.execute(
            "SELECT details_json FROM audit_logs WHERE action = 'blend_reactor_version_kept' "
            "AND target_label LIKE ?",
            (target_label_like,),
        ).fetchall()


def test_save_keeps_weighed_version_when_assignment_changed_after_start():
    """계량을 v2(반응기 2 지정)로 시작한 뒤 책임자가 반응기 2를 v3로 바꿔도 v2 로 저장한다."""
    client = _client()
    headers = _mgr(client)
    product, v1, v2, v3 = _chain(client, headers)
    assert _assign(client, headers, v3, 2, v2).status_code == 200
    _backdate_assignments(v1, 2)          # 2시간 전: 반응기 2 → v2
    started = _iso_ago(1)                 # 1시간 전: v2 로 계량 시작
    assert _assign(client, headers, v3, 2, v3).status_code == 200  # 지금: 반응기 2 → v3
    worker = _blend_session(client, headers)
    base = {
        "product_name": product, "worker": worker, "work_date": "2026-10-21",
        "total_amount": 100, "reactor": 2, "recipe_id": v2, "details": _details(70, 30),
    }

    # 시작 시각이 없거나 24시간보다 오래됐으면 종전대로 409.
    assert client.post("/api/blend/records", json=base, headers=headers).status_code == 409
    old = client.post(
        "/api/blend/records", json={**base, "weighing_started_at": _iso_ago(30)}, headers=headers
    )
    assert old.status_code == 409

    ok = client.post(
        "/api/blend/records", json={**base, "weighing_started_at": started}, headers=headers
    )
    assert ok.status_code == 200, ok.text
    from src.db import get_connection

    with get_connection() as conn:
        stored = conn.execute(
            "SELECT recipe_id FROM blend_records WHERE id = ?", (ok.json()["id"],)
        ).fetchone()["recipe_id"]
    assert stored == v2
    audits = _kept_audits(ok.json()["product_lot"])
    assert len(audits) == 1
    assert '"assigned_recipe_id": %d' % v3 in audits[0]["details_json"]

    # 다중 계량도 같은 규칙.
    cont = {
        "product_name": product, "work_date": "2026-10-21", "total_amount": 100, "reactor": 2,
        "recipe_id": v2, "lots": [_details(70, 30)],
    }
    assert client.post("/api/blend/records/continuous", json=cont, headers=headers).status_code == 409
    ok = client.post(
        "/api/blend/records/continuous",
        json={**cont, "weighing_started_at": started}, headers=headers,
    )
    assert ok.status_code == 200, ok.text
    assert len(_kept_audits(f"{product} (1로트)")) == 1

    # 시작 시각에도 v2 가 아니었던 판(v1)은 여전히 거부.
    bad = client.post(
        "/api/blend/records",
        json={**base, "recipe_id": v1, "details": _details(60, 40), "weighing_started_at": started},
        headers=headers,
    )
    assert bad.status_code == 409


def test_save_keeps_tip_when_reactor_assigned_after_start():
    """지정 없이 현재판(v3)으로 계량을 시작했는데 그 뒤 반응기 2가 v2로 지정됐다 — v3 로 저장."""
    client = _client()
    headers = _mgr(client)
    product, _v1, v2, v3 = _chain(client, headers)
    started = _iso_ago(1)
    assert _assign(client, headers, v3, 2, v2).status_code == 200
    worker = _blend_session(client, headers)
    body = {
        "product_name": product, "worker": worker, "work_date": "2026-10-21",
        "total_amount": 100, "reactor": 2, "recipe_id": v3, "details": _details(50, 50),
    }
    assert client.post("/api/blend/records", json=body, headers=headers).status_code == 409
    ok = client.post(
        "/api/blend/records", json={**body, "weighing_started_at": started}, headers=headers
    )
    assert ok.status_code == 200, ok.text


def test_blend_detail_returns_weighed_version_for_restore():
    """초안 복구: 시작 시각을 주면 그때의 판(v2)을 돌려주고, 없으면 지금 지정(v3)으로 귀결한다."""
    client = _client()
    headers = _mgr(client)
    _product, v1, v2, v3 = _chain(client, headers)
    assert _assign(client, headers, v3, 2, v2).status_code == 200
    _backdate_assignments(v1, 2)
    started = _iso_ago(1)
    assert _assign(client, headers, v3, 2, v3).status_code == 200

    now = client.get(f"/api/blend/recipes/{v2}", params={"reactor": 2}).json()["recipe"]
    assert now["id"] == v3 and not now.get("version_kept")

    kept = client.get(
        f"/api/blend/recipes/{v2}", params={"reactor": 2, "weighing_started_at": started}
    ).json()["recipe"]
    assert kept["id"] == v2 and kept["version_kept"] is True
    assert kept["version_label"] == "v2" and kept["assigned_version_label"] == "v3"

    stale = client.get(
        f"/api/blend/recipes/{v2}", params={"reactor": 2, "weighing_started_at": _iso_ago(30)}
    ).json()["recipe"]
    assert stale["id"] == v3
