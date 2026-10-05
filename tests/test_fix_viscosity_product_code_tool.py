"""tools/fix_viscosity_product_code.py 의 판정 함수(find_mismatches)를 서버 없이 검증한다.

배경(2026-10-05): 점도 제품 코드 '6-1TOP' 이 레시피 제품명 '6-1 TOP' 과 공백만 달라
배합 기록이 점도 화면에서 숨었다. 도구는 이런 제품만 골라 고칠 코드를 제안해야 한다.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "fix_viscosity_product_code.py"


def _load():
    spec = importlib.util.spec_from_file_location("fix_viscosity_product_code", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _product(pid, code, name=None, active=True):
    return {"id": pid, "code": code, "name": name or code, "is_active": active}


def test_space_only_difference_is_a_fix():
    tool = _load()
    fixes, ambiguous = tool.find_mismatches([_product(6, "6-1TOP")], ["6-1 TOP", "PB"])
    assert ambiguous == []
    assert fixes == [{"product_id": 6, "code": "6-1TOP", "name": "6-1TOP", "new_code": "6-1 TOP"}]


def test_exact_match_is_left_alone():
    tool = _load()
    fixes, ambiguous = tool.find_mismatches([_product(1, "PB"), _product(2, "6-1 TOP")], ["PB", "6-1 TOP"])
    assert fixes == [] and ambiguous == []


def test_several_recipes_with_same_key_are_ambiguous():
    tool = _load()
    fixes, ambiguous = tool.find_mismatches([_product(7, "AB12")], ["AB 12", "A B12", "PB"])
    assert fixes == []
    assert ambiguous == [{"product_id": 7, "code": "AB12", "name": "AB12", "candidates": ["A B12", "AB 12"]}]


def test_inactive_product_is_skipped():
    tool = _load()
    fixes, ambiguous = tool.find_mismatches([_product(6, "6-1TOP", active=False)], ["6-1 TOP"])
    assert fixes == [] and ambiguous == []


def test_product_without_any_matching_recipe_is_not_reported():
    tool = _load()
    fixes, ambiguous = tool.find_mismatches([_product(9, "ZZ9")], ["6-1 TOP"])
    assert fixes == [] and ambiguous == []


def test_tool_imports_only_stdlib():
    """프로젝트 모듈이나 서드파티를 쓰면 운영 PC 에서 venv 없이 못 돌린다."""
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    allowed = {"__future__", "argparse", "http", "json", "os", "re", "sys", "urllib"}
    assert imported <= allowed, f"표준 라이브러리 밖 의존: {sorted(imported - allowed)}"
