"""AI 도우미 도구 함수 테스트.

도구는 화면이 쓰는 서비스를 그대로 부른다. 그래서 여기서는 실제 기록을 심고
도구가 그 숫자를 그대로 되돌려 주는지, 잘못된 입력에 error 를 주는지 본다.
"""

import importlib
import uuid
from datetime import date

import pytest


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _csrf(client):
    token = client.cookies.get("csrftoken")
    return {"x-csrftoken": token} if token else {}


def _login_admin(client):
    client.get("/api/blend/records")
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200, res.text


TODAY = date.today().isoformat()


@pytest.fixture(scope="module")
def seeded():
    """레시피 하나와 배합 기록 하나를 심고 (제품명, LOT, 자재명) 을 돌려준다."""
    client = _client()
    _login_admin(client)

    product = "도우미" + uuid.uuid4().hex[:6].upper()
    material = "도움자재" + uuid.uuid4().hex[:4].upper()
    raw = f"반제품명\t{material}\n{product}\t100"
    res = client.post(
        "/api/recipes/import",
        json={"raw_text": raw, "force": True},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text

    worker = "도움작업" + uuid.uuid4().hex[:4]
    client.post("/api/workers", json={"name": worker}, headers=_csrf(client))
    client.post(
        "/api/blend/session/login", json={"worker": worker}, headers=_csrf(client)
    )
    res = client.post(
        "/api/blend/records",
        json={
            "product_name": product,
            "worker": worker,
            "work_date": TODAY,
            "total_amount": 1000,
            "scale": "M-65",
            "details": [
                {
                    "material_name": material,
                    "ratio": 100,
                    "theory_amount": 1000,
                    "actual_amount": 1000,
                    "material_lot": "MLOT-1",
                }
            ],
        },
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text
    payload = res.json()

    from src.services.assistant import tools

    tools.clear_cache()
    return {
        "product": product,
        "material": material,
        "worker": worker,
        "record_id": payload["id"],
        "product_lot": payload.get("product_lot"),
    }


@pytest.fixture(autouse=True)
def _fresh_cache():
    from src.services.assistant import tools

    tools.clear_cache()
    yield
    tools.clear_cache()


def test_get_blend_summary_counts_today(seeded):
    from src.services.assistant import tools

    result = tools.get_blend_summary(TODAY, TODAY)
    assert "error" not in result
    assert result["기간"] == {"시작일": TODAY, "종료일": TODAY}
    assert result["배합_건수"] >= 1
    assert result["총_배합량_kg"] >= 1.0
    names = [row["제품"] for row in result["제품별"]]
    assert seeded["product"] in names
    workers = [row["작업자"] for row in result["작업자별"]]
    assert seeded["worker"] in workers


def test_get_blend_summary_bad_date_falls_back_to_today(seeded):
    from src.services.assistant import tools

    result = tools.get_blend_summary("이상한날", "")
    assert result["기간"]["시작일"] == TODAY


def test_get_recent_records(seeded):
    from src.services.assistant import tools

    result = tools.get_recent_records(seeded["product"], 5)
    assert "error" not in result
    assert result["건수"] >= 1
    row = result["기록"][0]
    assert row["LOT"] == seeded["product_lot"]
    assert row["작업자"] == seeded["worker"]
    assert row["총량_g"] == 1000.0
    assert row["점도_측정"] is False


def test_get_record_detail(seeded):
    from src.services.assistant import tools

    result = tools.get_record(seeded["product_lot"])
    assert "error" not in result
    assert result["LOT"] == seeded["product_lot"]
    assert result["자재"][0]["자재"] == seeded["material"]
    assert result["자재"][0]["자재_LOT"] == "MLOT-1"
    assert result["편차_합계_g"] == 0.0


def test_get_record_rejects_blank_and_unknown_lot(seeded):
    from src.services.assistant import tools

    assert "error" in tools.get_record("")
    assert "error" in tools.get_record("없는LOT-999999")


def test_get_material_usage(seeded):
    from src.services.assistant import tools

    result = tools.get_material_usage(TODAY, TODAY, seeded["material"])
    assert "error" not in result
    assert result["자재별"], "자재 사용량이 비었다"
    assert result["자재별"][0]["자재"] == seeded["material"]
    assert result["자재별"][0]["실제_사용량_kg"] == 1.0


def test_get_recipe(seeded):
    from src.services.assistant import tools

    result = tools.get_recipe(seeded["product"])
    assert "error" not in result
    assert result["제품"] == seeded["product"]
    assert result["기본_총량_g"] == 100.0
    assert result["자재"][0]["자재"] == seeded["material"]


def test_get_recipe_unknown_product(seeded):
    from src.services.assistant import tools

    assert "error" in tools.get_recipe("없는반제품XYZ")
    assert "error" in tools.get_recipe("")


def test_get_viscosity_status_unknown_product():
    from src.services.assistant import tools

    assert "error" in tools.get_viscosity_status("없는반제품XYZ")
    assert "error" in tools.get_viscosity_status("")


def test_get_viscosity_status_shape():
    """등록된 반제품이 있으면 규격·문턱 열쇠가 모두 나온다."""
    from src.db import get_connection
    from src.services import viscosity_service
    from src.services.assistant import tools

    with get_connection() as conn:
        products = viscosity_service.list_products(conn)
    if not products:
        pytest.skip("등록된 점도 반제품이 없다")

    result = tools.get_viscosity_status(products[0]["code"])
    assert "error" not in result
    for key in ("반제품", "측정_건수", "중심", "이상_건수", "경고_건수", "규격", "경고_문턱"):
        assert key in result
    assert set(result["규격"]) == {"목표", "하한", "상한"}
    assert set(result["경고_문턱"]) == {"낮음", "높음"}


def test_get_lot_changes(seeded):
    from src.services.assistant import tools

    result = tools.get_lot_changes("", seeded["material"], TODAY, TODAY)
    assert "error" not in result
    assert result["교체_건수"] >= 0
    assert isinstance(result["교체"], list)


def test_get_lot_changes_unknown_recipe():
    from src.services.assistant import tools

    assert "error" in tools.get_lot_changes("없는레시피XYZ", "", "", "")


def test_get_attention_shape(seeded):
    from src.services.assistant import tools

    result = tools.get_attention()
    assert "error" not in result
    assert result["기준일"] == TODAY
    for key in ("점도_미입력", "점도_이상_건수", "자재_파일", "미확인_증량_수기_건수"):
        assert key in result
    assert set(result["자재_파일"]) == {"파일명", "파일일자", "경과일", "있음"}


def test_cache_returns_a_copy(seeded):
    from src.services.assistant import tools

    first = tools.get_blend_summary(TODAY, TODAY)
    first["배합_건수"] = -999
    first["제품별"].clear()

    second = tools.get_blend_summary(TODAY, TODAY)
    assert second["배합_건수"] >= 1
    assert second["제품별"], "캐시가 호출자의 수정에 오염됐다"


def test_call_tool_rejects_unknown_and_extra_params(seeded):
    from src.services.assistant import tools

    assert "error" in tools.call_tool("드롭테이블", {})
    # 허용 목록에 없는 인자는 조용히 버린다(예외로 죽지 않는다).
    result = tools.call_tool("get_attention", {"몰래": 1})
    assert "error" not in result


def test_tool_registry_and_labels():
    from src.services.assistant import tools

    assert len(tools.ASSISTANT_TOOLS) == 8
    for name in tools.TOOL_NAMES:
        assert name in tools.TOOL_LABELS
    # Gemini SDK 가 런타임 힌트를 읽으므로 주석이 문자열로 남아 있으면 안 된다.
    for fn in tools.ASSISTANT_TOOLS:
        for value in fn.__annotations__.values():
            assert not isinstance(value, str), f"{fn.__name__} 의 타입 힌트가 문자열이다"


def test_tool_errors_are_returned_not_raised(monkeypatch):
    """서비스가 터져도 도구는 error dict 를 돌려준다."""
    from src.services import blend_service
    from src.services.assistant import tools

    def _boom(*args, **kwargs):
        raise RuntimeError("DB 폭발")

    monkeypatch.setattr(blend_service, "analysis", _boom)
    result = tools.get_blend_summary(TODAY, TODAY)
    assert "error" in result
