"""레시피 등록 취소의 되돌리기 경로 + 삭제 전 영향 규모 노출 (2026-07-26 UI 검토).

취소는 배합 화면의 레시피 목록에서 그 반제품을 사라지게 하는데, canceled 에서 빠져나오는
상태 전이가 없어 DB 를 직접 고치지 않으면 복구할 수 없었다. /status 의 기록은 이미
취소→복원이 되는데 레시피만 편도였다.

또 '레시피+기록 삭제'는 확인창이 규모를 말해주지 않아, 몇 건이 함께 사라지는지 모른 채
확인을 누르고 나서야 성공 토스트에서 건수를 봤다. 상세 응답에 건수를 실어 확인창이
미리 말할 수 있게 한다.
"""

import importlib
import uuid

import pytest


@pytest.fixture(autouse=True)
def _cleanup_master():
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


def _import(client, headers, product):
    tab, nl = chr(9), chr(10)
    raw = tab.join(["반제품명", "원료A", "원료B"]) + nl + tab.join([product, "60", "40"])
    res = client.post("/api/recipes/import", json={"raw_text": raw}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


def _in_blend_list(client, product):
    items = client.get("/api/blend/recipes").json()["items"]
    return any(i["product_name"] == product for i in items)


def test_canceled_recipe_can_be_restored():
    client = _client()
    headers = _mgr(client)
    product = "RST" + uuid.uuid4().hex[:6].upper()
    rid = _import(client, headers, product)
    assert _in_blend_list(client, product)

    cancelled = client.patch(
        f"/api/recipes/{rid}/status",
        json={"action": "cancel", "reason": "시험 등록분 정리"},
        headers=headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "canceled"
    # 취소하면 현장 배합 화면 목록에서 사라진다 — 되돌릴 수 없으면 이게 영구다.
    assert not _in_blend_list(client, product)

    # 사유가 실제로 저장된다(예전에는 UI 가 안 보내 항상 비어 있었다).
    from src.db import get_connection

    with get_connection() as conn:
        reason = conn.execute(
            "SELECT cancel_reason FROM recipes WHERE id = ?", (rid,)
        ).fetchone()[0]
    assert reason == "시험 등록분 정리"

    restored = client.patch(
        f"/api/recipes/{rid}/status", json={"action": "restore"}, headers=headers
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "completed"
    assert _in_blend_list(client, product), "복원했는데 배합 목록에 돌아오지 않음"


def test_restore_only_applies_to_canceled_recipes():
    client = _client()
    headers = _mgr(client)
    rid = _import(client, headers, "RST" + uuid.uuid4().hex[:6].upper())
    # 이미 사용중인 레시피에 restore 는 의미가 없다 → 409.
    res = client.patch(
        f"/api/recipes/{rid}/status", json={"action": "restore"}, headers=headers
    )
    assert res.status_code == 409, res.text


def test_detail_exposes_linked_record_count_before_deletion():
    """삭제 확인창이 '몇 건이 함께 사라지는지' 미리 말할 수 있어야 한다."""
    client = _client()
    headers = _mgr(client)
    product = "RST" + uuid.uuid4().hex[:6].upper()
    rid = _import(client, headers, product)

    detail = client.get(f"/api/recipes/{rid}/detail", headers=headers).json()
    assert detail["linked_record_count"] == 0

    # 이 레시피로 기록 2건을 남기면 상세가 그 수를 보고해야 한다.
    from src.db import get_connection

    with get_connection() as conn:
        for i in (1, 2):
            conn.execute(
                "INSERT INTO blend_records (product_lot, product_name, worker, work_date, "
                " total_amount, status, created_at, recipe_id) "
                "VALUES (?, ?, '집계', '2026-07-26', 100, 'completed', "
                "        '2026-07-26T00:00:00Z', ?)",
                (f"{product}2607260{i}", product, rid),
            )
        conn.commit()

    detail2 = client.get(f"/api/recipes/{rid}/detail", headers=headers).json()
    assert detail2["linked_record_count"] == 2


def test_canceled_records_are_excluded_from_batch_dhr_output():
    """취소된 기록은 배합일지 일괄 출력에 섞이지 않는다.

    '취소 포함'으로 조회한 뒤 전체 선택하면 취소분이 정상 기록과 한 문서로 인쇄됐고,
    PIL 폴백 렌더러에는 취소 표식이 없어 정상 문서처럼 보였다.
    """
    client = _client()
    headers = _mgr(client)
    product = "BAT" + uuid.uuid4().hex[:6].upper()

    from src.db import get_connection

    ids = []
    with get_connection() as conn:
        for i, status in enumerate(("completed", "canceled"), start=1):
            cur = conn.execute(
                "INSERT INTO blend_records (product_lot, product_name, worker, work_date, "
                " total_amount, status, created_at) "
                "VALUES (?, ?, '출력', '2026-07-26', 100, ?, '2026-07-26T00:00:00Z')",
                (f"{product}2607260{i}", product, status),
            )
            rid = cur.lastrowid
            ids.append(rid)
            conn.execute(
                "INSERT INTO blend_details (blend_record_id, material_name, ratio, "
                " theory_amount, actual_amount, sequence_order, created_at) "
                "VALUES (?, '자재', 100, 100, 100, 1, '2026-07-26T00:00:00Z')",
                (rid,),
            )
        conn.commit()

    # 정상 1건 + 취소 1건을 함께 요청 → 정상만 나가야 한다(200, 취소분 제외).
    res = client.get(
        "/api/blend/records/dhr-batch",
        params={"ids": ",".join(str(i) for i in ids)},
        headers=headers,
    )
    assert res.status_code == 200, res.text[:300]

    # 취소분만 고르면 404 로 '대상 없음'을 분명히 알린다(빈 문서 대신).
    only_canceled = client.get(
        "/api/blend/records/dhr-batch", params={"ids": str(ids[1])}, headers=headers
    )
    assert only_canceled.status_code == 404
    assert "취소" in only_canceled.json()["detail"]
