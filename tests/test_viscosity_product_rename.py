"""PUT /api/viscosity/products/{id}/code — 반제품 코드 교정(책임자 전용).

배경(2026-10-05): 레시피 제품명은 '6-1 TOP' 인데 점도 제품 코드가 '6-1TOP' 으로 만들어져
배합 기록이 점도 화면·목록 상태에서 보이지 않았다. 배합 기록과 점도 제품은 글자 그대로
일치로만 묶이므로, 코드를 고치면 숨어 있던 기록이 드러나야 한다.

하네스 주의(test_viscosity_exclusion_routes.py 참조): 공용 admin 세션 토큰이 하나라
클라이언트를 만들고 로그인한 뒤 데이터는 DB 에 직접 심고, 보호 요청은 짧게 끝낸다.
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


def _manager_client():
    client = _client()
    login = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert login.status_code == 200, login.text
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}
    return client, headers


def _add_recipe(conn, product_name: str) -> int:
    from src.db import utc_now_text

    return int(
        conn.execute(
            "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at) "
            "VALUES (?, ?, 'completed', 'test', ?)",
            (product_name, product_name, utc_now_text()),
        ).lastrowid
    )


def _add_product(conn, code: str, name: str) -> int:
    from src.db import utc_now_text

    return int(
        conn.execute(
            "INSERT INTO viscosity_products (code, name, sigma_k, is_active, created_at) "
            "VALUES (?, ?, 3, 1, ?)",
            (code, name, utc_now_text()),
        ).lastrowid
    )


def _add_blend_record(conn, product_name: str, recipe_id: int | None) -> int:
    from src.db import utc_now_text

    lot = f"{product_name}-{uuid.uuid4().hex[:6]}"
    return int(
        conn.execute(
            "INSERT INTO blend_records (product_lot, recipe_id, product_name, worker, "
            "work_date, total_amount, status, created_at) "
            "VALUES (?, ?, ?, '테스트작업자', '2026-10-01', 100.0, 'completed', ?)",
            (lot, recipe_id, product_name, utc_now_text()),
        ).lastrowid
    )


def _add_reading(conn, product_id: int, lot: str) -> int:
    from src.db import utc_now_text

    return int(
        conn.execute(
            "INSERT INTO viscosity_readings "
            "(product_id, lot_no, viscosity, measured_date, created_by, created_at, excluded) "
            "VALUES (?, ?, 50.0, '2026-09-30', 'test', ?, 0)",
            (product_id, lot, utc_now_text()),
        ).lastrowid
    )


def _record_state(client, product: str, record_id: int) -> str | None:
    r = client.get("/api/blend/records", params={"product": product, "limit": 50})
    assert r.status_code == 200, r.text
    rows = {int(row["id"]): row for row in r.json()["items"]}
    return rows[record_id].get("viscosity_state")


def test_rename_reveals_hidden_blend_records_end_to_end():
    from src.db import get_connection

    client, headers = _manager_client()
    with get_connection() as conn:
        recipe_id = _add_recipe(conn, "6-1 TOP")
        record_id = _add_blend_record(conn, "6-1 TOP", recipe_id)
        pid = _add_product(conn, "6-1TOP", "6-1TOP")
        _add_reading(conn, pid, "6-1TOP-OLD1")
        conn.commit()

    before = client.get(f"/api/viscosity/products/{pid}/blend-records")
    assert before.status_code == 200, before.text
    assert before.json()["items"] == []
    assert _record_state(client, "6-1 TOP", record_id) == "na"

    r = client.put(
        f"/api/viscosity/products/{pid}/code", json={"code": " 6-1 TOP "}, headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == "6-1 TOP"
    assert body["name"] == "6-1 TOP"

    after = client.get(f"/api/viscosity/products/{pid}/blend-records")
    assert after.status_code == 200, after.text
    items = after.json()["items"]
    assert [int(i["id"]) for i in items] == [record_id]
    assert items[0]["registered"] is False
    assert _record_state(client, "6-1 TOP", record_id) == "missing"

    with get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM viscosity_readings WHERE product_id = ?", (pid,)
        ).fetchone()[0]
        assert n == 1
        audit = conn.execute(
            "SELECT details_json FROM audit_logs WHERE action = 'viscosity_product_rename' "
            "AND target_id = ?",
            (str(pid),),
        ).fetchall()
        assert len(audit) == 1
        import json

        details = json.loads(audit[0]["details_json"])
        assert details == {"old_code": "6-1TOP", "new_code": "6-1 TOP", "name": "6-1 TOP"}


def test_same_code_is_noop_without_audit():
    from src.db import get_connection

    client, headers = _manager_client()
    tag = "RNS" + uuid.uuid4().hex[:6].upper()
    with get_connection() as conn:
        _add_recipe(conn, tag)
        pid = _add_product(conn, tag, tag)
        conn.commit()

    r = client.put(f"/api/viscosity/products/{pid}/code", json={"code": tag}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["code"] == tag
    with get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'viscosity_product_rename' "
            "AND target_id = ?",
            (str(pid),),
        ).fetchone()[0]
        assert n == 0


def test_rename_preserves_a_name_that_differs_from_code():
    from src.db import get_connection

    client, headers = _manager_client()
    tag = "RNP" + uuid.uuid4().hex[:6].upper()
    with get_connection() as conn:
        _add_recipe(conn, f"{tag} A")
        pid = _add_product(conn, f"{tag}A", "상부 코팅액")
        conn.commit()

    r = client.put(
        f"/api/viscosity/products/{pid}/code", json={"code": f"{tag} A"}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["code"] == f"{tag} A"
    assert r.json()["name"] == "상부 코팅액"


def test_rename_to_code_without_recipe_is_400():
    from src.db import get_connection

    client, headers = _manager_client()
    tag = "RNR" + uuid.uuid4().hex[:6].upper()
    with get_connection() as conn:
        pid = _add_product(conn, tag, tag)
        conn.commit()

    r = client.put(
        f"/api/viscosity/products/{pid}/code", json={"code": f"{tag} X"}, headers=headers
    )
    assert r.status_code == 400, r.text
    assert "레시피에 없는 제품입니다" in r.json()["detail"]


def test_rename_to_code_owned_by_another_product_is_409():
    from src.db import get_connection

    client, headers = _manager_client()
    tag = "RNC" + uuid.uuid4().hex[:6].upper()
    with get_connection() as conn:
        _add_recipe(conn, tag.lower())
        _add_product(conn, tag, tag)  # 다른 제품이 대문자 코드로 이미 가지고 있다
        pid = _add_product(conn, f"{tag}B", f"{tag}B")
        conn.commit()

    r = client.put(
        f"/api/viscosity/products/{pid}/code", json={"code": tag.lower()}, headers=headers
    )
    assert r.status_code == 409, r.text
    assert "이미 존재하는 코드입니다" in r.json()["detail"]


def test_rename_unknown_product_is_404():
    client, headers = _manager_client()
    r = client.put(
        "/api/viscosity/products/999999/code", json={"code": "6-1 TOP"}, headers=headers
    )
    assert r.status_code == 404, r.text


def test_rename_empty_code_is_422():
    client, headers = _manager_client()
    r = client.put("/api/viscosity/products/1/code", json={"code": ""}, headers=headers)
    assert r.status_code == 422, r.text


def test_rename_denied_for_anonymous():
    client = _client()
    client.get("/viscosity")  # csrftoken 쿠키 확보
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}
    r = client.put(
        "/api/viscosity/products/1/code", json={"code": "6-1 TOP"}, headers=headers
    )
    assert r.status_code == 401, r.text
