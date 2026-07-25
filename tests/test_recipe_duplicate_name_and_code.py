"""같은 반제품명 재등록 차단 + 같은 제품의 품목코드 재지정 허용 (2026-07-25 감사).

증상(현장 신고): "이미 사용 중인 코드라고 뜨는데 수정이 안 된다".
원인 사슬:
  1. 임포트 중복 검사가 raw_input_hash(내용 완전 일치)만 봐서, 같은 이름으로 배합비만
     바꿔 등록하면 개정이 아니라 **독립된 새 체인**이 생겼다. 배합 화면 목록에 같은
     이름이 두 줄 뜨고 작업자는 구분할 수 없다.
  2. 두 체인이 같은 품목코드를 쥐게 되는데, 코드 충돌 검사가 '체인이 다르면 충돌'로
     판정해 자기 제품의 코드를 재지정하는 것조차 영구히 409 로 막혔다.
"""

import importlib
import uuid

import pytest


@pytest.fixture(autouse=True)
def _cleanup_test_master():
    """이 모듈이 남긴 item_code_master 'manual' 행 삭제 — 공유 pytest DB 오염 방지.

    PUT /recipes/{id}/product-code 가 _ensure_master_entry 로 만드는 source='manual' 행을
    남기면 import_parser 의 '마스터가 비었는가' 판정이 뒤집혀, 다른 파일에서 미등록 자재가
    자동 등록 대신 차단된다(test_item_code_admin·test_two_stage_item_code 와 동일 패턴).
    """
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


def _uid():
    return uuid.uuid4().hex[:6].upper()


def test_same_product_name_reimport_is_blocked_with_guidance():
    """같은 이름 재등록은 409 로 막고 '수정 등록'을 안내한다. 개정·force 는 통과."""
    client = _client()
    headers = _mgr(client)
    product = "DUP" + _uid()

    first = client.post(
        "/api/recipes/import",
        json={"raw_text": f"반제품명\t원료A\t원료B\n{product}\t60\t40"},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    rid = first.json()["created_ids"][0]

    # 같은 이름으로 내용만 바꿔 재등록 → 차단.
    dup = client.post(
        "/api/recipes/import",
        json={"raw_text": f"반제품명\t원료A\t원료B\n{product}\t61\t39"},
        headers=headers,
    )
    assert dup.status_code == 409, dup.text
    detail = dup.json()["detail"]
    assert detail["code"] == "DUPLICATE_PRODUCT_NAME"
    assert "수정 등록" in detail["message"]

    # 수정 등록(개정)은 정상 통과 — 이력이 이어진다.
    rev = client.post(
        "/api/recipes/import",
        json={"raw_text": f"반제품명\t원료A\t원료B\n{product}\t61\t39", "revision_of": rid},
        headers=headers,
    )
    assert rev.status_code == 200, rev.text

    # force 는 의도적 탈출구로 남겨둔다.
    forced = client.post(
        "/api/recipes/import",
        json={"raw_text": f"반제품명\t원료A\t원료B\n{product}\t50\t50", "force": True},
        headers=headers,
    )
    assert forced.status_code == 200, forced.text


def test_same_product_name_may_share_a_code_but_other_products_may_not():
    """같은 반제품명이 쥔 코드는 재지정 가능(같은 제품), 다른 제품 코드는 여전히 409."""
    client = _client()
    headers = _mgr(client)
    product = "CODE" + _uid()
    code_mine = "Z" + _uid()[:4]
    code_other = "Y" + _uid()[:4]

    from src.db import get_connection

    created = client.post(
        "/api/recipes/import",
        json={"raw_text": f"반제품명\t원료A\t원료B\n{product}\t60\t40"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    tip = created.json()["created_ids"][0]

    # 과거 데이터 재현: 같은 이름의 별개 체인 + 전혀 다른 제품이 각각 코드를 쥐고 있다.
    ins = (
        "INSERT INTO recipes (product_name, ink_name, status, created_at, created_by, "
        " product_code) VALUES (?, 'x', 'completed', '2026-01-01', 't', ?)"
    )
    with get_connection() as conn:
        conn.execute(ins, (product, code_mine))
        conn.execute(ins, ("전혀다른제품" + _uid(), code_other))
        conn.commit()

    # 같은 이름이 쥔 코드 → 같은 제품이므로 허용해야 한다(예전에는 여기서 막혔다).
    ok = client.put(
        f"/api/recipes/{tip}/product-code", json={"product_code": code_mine}, headers=headers
    )
    assert ok.status_code == 200, ok.text

    # 다른 제품이 쥔 코드 → 여전히 차단하고 누가 쓰는지 알려준다.
    blocked = client.put(
        f"/api/recipes/{tip}/product-code", json={"product_code": code_other}, headers=headers
    )
    assert blocked.status_code == 409, blocked.text
    assert "사용 중인 코드" in blocked.json()["detail"]
