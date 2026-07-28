"""GET /api/recipes/export — 레시피 전체 Excel 내보내기 라우트 테스트.

패턴은 tests/test_blend.py 의 importlib.reload(cfg/mainmod) + TestClient +
admin/admin 로그인 + uuid 제품명 시드를 따른다. 책임자 전용 통제(미로그인 401/403),
200 + xlsx content-type, 시트 2개, 시드한 레시피·자재명이 각 시트에 보이는지 단언.
"""

from __future__ import annotations

import io
import importlib
import uuid


def _reload_app():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _seed_recipe(client, headers, prod, materials):
    """TSV 로 레시피 한 건을 등록하고 생성된 id 를 돌려준다."""
    header = "반제품명\t" + "\t".join(materials.keys())
    row = f"{prod}\t" + "\t".join(str(v) for v in materials.values())
    raw = header + "\n" + row
    res = client.post(
        "/api/recipes/import",
        json={"raw_text": raw, "force": True},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0], list(materials.keys())


def test_recipe_export_requires_manager():
    """미로그인(또는 현장 권한) 접근은 401 또는 403."""
    client = _reload_app()
    res = client.get("/api/recipes/export")
    assert res.status_code in (401, 403), res.text


def test_recipe_export_returns_two_sheets_with_seed_data():
    """책임자 로그인 후 200 + xlsx, 시트 2개, 시드한 반제품명/자재명이 각 시트에 존재."""
    client = _reload_app()
    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}

    prod = "RE" + uuid.uuid4().hex[:6]
    materials = {"MatX": 60, "MatY": 40}
    _rid, material_names = _seed_recipe(client, headers, prod, materials)

    res = client.get("/api/recipes/export")
    assert res.status_code == 200, res.text
    assert "spreadsheetml" in res.headers.get("content-type", ""), res.headers.get("content-type")

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(res.content))
    assert wb.sheetnames == ["레시피 목록", "자재 구성"], wb.sheetnames

    ws1 = wb["레시피 목록"]
    names1 = [r[1].value for r in ws1.iter_rows(min_row=2)]
    assert prod in names1, names1

    ws2 = wb["자재 구성"]
    names2 = [r[3].value for r in ws2.iter_rows(min_row=2)]
    for mname in material_names:
        assert mname in names2, (mname, names2)
