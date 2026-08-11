"""tools/import_legacy_records.py — 구 프로그램 실적서 이관의 자재 연결 검증.

이 도구는 종전에 blend_details 를 `VALUES (?,NULL,'',...)` 로 넣었다. material_id 와
material_code 를 통째로 비운 것인데, 그래서 이관분이 자재 사용량 API 에서 품목코드
없이 나가 상위 재고 대시보드가 전부 버렸다 — 2026-08-11 에 드러난 미매핑 71kg 이
예외 없이 이 경로였다. 더 나쁜 건 이어질 수 있었던 이름(마스터에 그대로 있는 자재)
까지 함께 버려졌다는 점이다.

고정할 성질:
  ① 자재명이 마스터와 같으면 material_id·code 로 이어진다.
  ② 마스터에 없고 별칭(동의어)에만 있어도 이어진다 — 운영자가 화면에서 동의어를
     등록해 과거 이름을 잇는 것이 정식 경로이기 때문.
  ③ 대소문자·공백·기호 차이는 무시한다(해석기와 같은 정규화).
  ④ 못 찾아도 자재를 새로 만들지 않는다 — 구 프로그램 표기로 마스터가 오염된다.
  ⑤ 표준 라이브러리 + openpyxl 만 쓴다(운영 PC 단독 실행, src 패키지 비의존).
"""

from __future__ import annotations

import ast
import importlib.util
import sqlite3
from pathlib import Path

TOOL = Path("tools/import_legacy_records.py")


def _load():
    spec = importlib.util.spec_from_file_location("import_legacy_records", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            code TEXT
        );
        CREATE TABLE material_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
            alias_name TEXT NOT NULL UNIQUE
        );
        """
    )
    return conn


# ── ① 자재명 직접 매칭 ─────────────────────────────────────────────────────
def test_index_resolves_material_by_name():
    m = _load()
    conn = _make_db()
    conn.execute("INSERT INTO materials (name, code) VALUES ('PMA', 'AC0060')")
    index = m._material_index(conn)
    assert index[m._norm("PMA")] == (1, "AC0060")


# ── ② 별칭(동의어) 매칭 ────────────────────────────────────────────────────
def test_index_resolves_material_by_alias():
    """마스터에 없는 이름이라도 동의어로 등록돼 있으면 이어진다.

    이게 이 도구와 화면(자재 관리 > 품목코드)을 잇는 지점이다 — 운영자가 동의어를
    등록한 뒤 다시 이관하면 과거 표기까지 정상 연결된다.
    """
    m = _load()
    conn = _make_db()
    conn.execute("INSERT INTO materials (name, code) VALUES ('PMA', 'AC0060')")
    conn.execute(
        "INSERT INTO material_aliases (material_id, alias_name) "
        "VALUES (1, 'Propylene glycol monomethyl etheracetate')"
    )
    index = m._material_index(conn)
    assert index[m._norm("Propylene glycol monomethyl etheracetate")] == (1, "AC0060")


def test_material_name_wins_over_another_materials_alias():
    """자재명이 별칭보다 우선한다 — 같은 키가 겹치면 실제 자재명 쪽으로 해석."""
    m = _load()
    conn = _make_db()
    conn.execute("INSERT INTO materials (name, code) VALUES ('NVP', 'AS0005')")
    conn.execute("INSERT INTO materials (name, code) VALUES ('PMA', 'AC0060')")
    # 다른 자재가 'NVP' 를 별칭으로 갖고 있어도 자재명 NVP 가 이긴다.
    conn.execute(
        "INSERT INTO material_aliases (material_id, alias_name) VALUES (2, 'NVP')"
    )
    index = m._material_index(conn)
    assert index[m._norm("NVP")] == (1, "AS0005")


# ── ③ 정규화 ───────────────────────────────────────────────────────────────
def test_norm_ignores_case_space_and_symbols():
    m = _load()
    assert m._norm("L-HEMA (Lotte)") == m._norm("l hema lotte")
    assert m._norm("  BYK-199 ") == m._norm("byk199")
    assert m._norm("---") == ""
    assert m._norm(None) == ""


def test_index_matches_despite_spacing_variants():
    m = _load()
    conn = _make_db()
    conn.execute("INSERT INTO materials (name, code) VALUES ('CS Pigment', 'AC0024')")
    index = m._material_index(conn)
    assert index[m._norm("cspigment")] == (1, "AC0024")


# ── ④ 못 찾아도 자재를 만들지 않는다 ──────────────────────────────────────
def test_unresolved_name_is_absent_and_creates_nothing():
    """색인에 없으면 None 으로 남을 뿐, materials 에 행이 늘지 않는다.

    자매 도구 import_legacy.py 의 _resolve_material 은 없으면 INSERT 하지만, 이 도구는
    구 프로그램 표기를 그대로 자재로 만들면 마스터가 오염되므로 만들지 않는다.
    """
    m = _load()
    conn = _make_db()
    conn.execute("INSERT INTO materials (name, code) VALUES ('PMA', 'AC0060')")
    index = m._material_index(conn)
    assert index.get(m._norm("듣도보도못한자재")) is None
    assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 1


def test_material_without_code_still_links_id():
    """코드가 없는 자재도 material_id 는 이어 준다(코드는 빈 문자열)."""
    m = _load()
    conn = _make_db()
    conn.execute("INSERT INTO materials (name, code) VALUES ('무코드자재', NULL)")
    index = m._material_index(conn)
    assert index[m._norm("무코드자재")] == (1, "")


# ── ⑤ INSERT 가 더 이상 NULL 을 못박지 않는다 ─────────────────────────────
def test_blend_details_insert_no_longer_hardcodes_null():
    """회귀 방지 — 소스에 `VALUES (?,NULL,''` 형태가 다시 나타나면 실패."""
    source = TOOL.read_text(encoding="utf-8")
    assert "?,NULL,''" not in source, "material_id 를 다시 NULL 로 못박았다"
    assert "_material_index" in source
    assert "unresolved" in source, "못 찾은 자재명 보고가 사라졌다"


def test_tool_does_not_import_src_package():
    """운영 PC 단독 실행 — src 패키지에 의존하지 않는다(표준 라이브러리 + openpyxl)."""
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src"):
            raise AssertionError(f"src 패키지 임포트: {node.module}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("src"), f"src 임포트: {alias.name}"
