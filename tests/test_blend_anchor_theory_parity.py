"""기준 자재(anchor) 모드: 서버 이론량 = 화면 이론량 (동일 산술 잠금).

배경(2026-08-04 실측). 화면(static/js/blend_lib.js computeAnchorTheory)은 레시피
원값(value_weight) 비례로 이론량을 낸다:

    theory_i = round(기준자재_실측 × w_i / w_기준, 2)

반면 서버(derive_details_from_recipe)는 4자리로 반올림된 ratio(%) 를 **두 번**
통과시켰다(실측 → 총량 되돌리기 → 다시 비율 배분). ratio 의 반올림 오차가
1/ratio_기준 배로 증폭돼 기준 자재 비율이 작을수록 커진다:

    value_weight = MainResin 12189.58 / Solvent 7679.09 / Catalyst 131.33
    기준 자재 = Catalyst(0.6567%), 실측 131.33g
      화면 : 총 20000.00 / MainResin 12189.58 / Solvent 7679.09
      서버 : 총 19998.48 / MainResin 12188.65 / Solvent 7678.52   ← -0.93g / -0.57g

작업자가 화면 목표대로 정확히 계량하면 화면 편차는 전 행 0.00 인데 서버 편차는
허용치(0.05g)의 18.6배라 저장이 400 으로 막혔고, 저장되는 경우에도 DHR 에는
작업자가 본 값과 다른 이론량이 남았다(규제 기록 무결성).

이 테스트는 **화면 공식을 정답으로 두고** 서버 산출값이 완전히 일치함을 잠근다.
"""

from __future__ import annotations

import sqlite3

from src.services import blend_service as bs


def _make_db() -> sqlite3.Connection:
    """anchor_material_id 를 가진 최소 스키마 (tests/test_blend.py _make_db 계열)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, unit_type TEXT, unit TEXT DEFAULT 'g',
            category TEXT, is_active INTEGER DEFAULT 1
        );
        CREATE TABLE recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT, position TEXT, ink_name TEXT,
            status TEXT DEFAULT 'completed', created_at TEXT DEFAULT '2026-01-01',
            revision_of INTEGER, base_total REAL, base_totals TEXT,
            anchor_material_id INTEGER
        );
        CREATE TABLE recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER, material_id INTEGER,
            value_weight REAL, value_text TEXT
        );
        CREATE TABLE viscosity_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT,
            target REAL, lower_limit REAL, upper_limit REAL, sigma_k REAL DEFAULT 3,
            rpm REAL, temperature REAL, remind_daily INTEGER DEFAULT 0,
            use_reactor INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, created_at TEXT
        );
        """
    )
    return conn


def _seed(conn, names_weights, anchor_name=None, product="ANCHORP"):
    """(자재명, value_weight) 목록으로 레시피 생성. anchor_name 을 기준 자재로 지정."""
    rid = conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status) VALUES (?, ?, 'completed')",
        (product, f"{product}-ink"),
    ).lastrowid
    ids = {}
    for name, weight in names_weights:
        mid = conn.execute(
            "INSERT INTO materials (name, unit_type, unit) VALUES (?, 'weight', 'g')",
            (name,),
        ).lastrowid
        ids[name] = mid
        conn.execute(
            "INSERT INTO recipe_items (recipe_id, material_id, value_weight) VALUES (?, ?, ?)",
            (rid, mid, weight),
        )
    if anchor_name is not None:
        conn.execute(
            "UPDATE recipes SET anchor_material_id = ? WHERE id = ?", (ids[anchor_name], rid)
        )
    return rid


def _frontend_theory(names_weights, anchor_name, anchor_actual):
    """화면 blend_lib.computeAnchorTheory 와 동일한 순수 산술(정답 기준).

    비기준 행: round(실측 × w_i / w_기준, 2), 기준 행: round(실측, 2),
    도출 총량: round(행 이론량 합, 2).
    """
    weights = dict(names_weights)
    anchor_w = weights[anchor_name]
    out = {}
    for name, w in names_weights:
        if name == anchor_name:
            out[name] = round(anchor_actual, 2)
        else:
            out[name] = round(anchor_actual * w / anchor_w, 2)
    return out, round(sum(out.values()), 2)


# 현장 실측 케이스 — 기준 자재 비율 0.6567%
_CASE = [("MainResin", 12189.58), ("Solvent", 7679.09), ("Catalyst", 131.33)]


def test_anchor_theory_matches_frontend_weight_proportion():
    """실측 재현 케이스: 서버 이론량 == 화면 공식 (완전 일치).

    수정 전 서버 값은 MainResin 12188.65 / Solvent 7678.52 / 총 19998.48 이었다.
    """
    conn = _make_db()
    rid = _seed(conn, _CASE, anchor_name="Catalyst")
    anchor_actual = 131.33

    details = [
        {"material_name": "MainResin", "actual_amount": 12189.58, "material_lot": "L1"},
        {"material_name": "Solvent", "actual_amount": 7679.09, "material_lot": "L2"},
        {"material_name": "Catalyst", "actual_amount": anchor_actual, "material_lot": "L3"},
    ]
    derived, total = bs.derive_details_from_recipe(conn, rid, 20000.0, details)

    want, want_total = _frontend_theory(_CASE, "Catalyst", anchor_actual)
    got = {d["material_name"]: round(float(d["theory_amount"]), 2) for d in derived}
    assert got == want, got
    assert total == want_total == 20000.0

    # 회귀 잠금 — 옛 ratio 2회 통과 값이 다시 나오면 실패
    assert got["MainResin"] == 12189.58
    assert got["Solvent"] == 7679.09
    assert total != 19998.48

    # 화면 목표대로 정확히 계량하면 편차 0 → 저장이 막히지 않는다(400 의 원인 제거)
    assert bs.weighing_tolerance_violations(derived, tolerance_g=0.05) == []


def test_anchor_theory_parity_with_tiny_anchor_ratio():
    """기준 자재 비율 0.1% 이하 — 오차가 1/ratio 로 증폭되던 최악 구간.

    Trace 13.3g / 총 19873.41g = 0.0669% (4자리에서 반올림되는 값이라 옛 경로는
    Base 를 +5.28g, Additive 를 +1.71g 어긋나게 냈다).
    """
    conn = _make_db()
    case = [("Base", 15000.0), ("Additive", 4860.11), ("Trace", 13.3)]
    rid = _seed(conn, case, anchor_name="Trace", product="TINYANC")
    anchor_actual = 13.3

    want, want_total = _frontend_theory(case, "Trace", anchor_actual)
    details = [
        {"material_name": n, "actual_amount": want[n], "material_lot": "L"} for n, _ in case
    ]
    derived, total = bs.derive_details_from_recipe(conn, rid, 19873.41, details)

    got = {d["material_name"]: round(float(d["theory_amount"]), 2) for d in derived}
    assert got == want, got
    assert total == want_total == 19873.41
    assert bs.weighing_tolerance_violations(derived, tolerance_g=0.05) == []


def test_anchor_theory_parity_when_anchor_actual_differs_from_recipe():
    """기준 자재를 레시피 원값과 다르게 계량한 실제 운용(반응기 산출량 변동).

    서버 계약: **기준 실측이 얼마든** 이론량은 원값 비례다. 증량(rescale) 후에도
    같다 — 화면의 증량 목표(blend_lib.rescalePlan → blend.js recomputeAnchorRescale)는
    아직 4자리 ratio×newTotal 이라 기준 모드에서 이 계약과 어긋난다(별도 과제).
    화면 쪽 정합 방법: 증량 후 기준 행 목표를 구한 뒤 computeAnchorTheory 로 나머지
    행을 다시 계산하면 서버 값과 정확히 일치한다.
    """
    conn = _make_db()
    rid = _seed(conn, _CASE, anchor_name="Catalyst", product="ANCDIFF")
    anchor_actual = 118.77                                # 레시피 131.33 대비 소량

    want, want_total = _frontend_theory(_CASE, "Catalyst", anchor_actual)
    details = [
        {"material_name": n, "actual_amount": want[n], "material_lot": "L"} for n, _ in _CASE
    ]
    derived, total = bs.derive_details_from_recipe(conn, rid, 20000.0, details)

    got = {d["material_name"]: round(float(d["theory_amount"]), 2) for d in derived}
    assert got == want, got
    assert total == want_total
    assert bs.weighing_tolerance_violations(derived, tolerance_g=0.05) == []


def test_anchor_row_theory_is_the_measured_value_itself():
    """기준 행의 이론량 = 실측값 그대로(편차 0) — 기존 계약 유지."""
    conn = _make_db()
    rid = _seed(conn, _CASE, anchor_name="Catalyst", product="ANCSELF")
    details = [
        {"material_name": "MainResin", "actual_amount": None},
        {"material_name": "Solvent", "actual_amount": None},
        {"material_name": "Catalyst", "actual_amount": 131.337, "material_lot": "L3"},
    ]
    derived, _total = bs.derive_details_from_recipe(conn, rid, 20000.0, details)
    anchor_row = next(d for d in derived if d["material_name"] == "Catalyst")
    assert anchor_row["theory_amount"] == 131.337


def test_non_anchor_path_unchanged():
    """기준 자재가 없는 일반 경로는 그대로 scale_theory(총량 비례) 를 쓴다(회귀 방지)."""
    conn = _make_db()
    rid = _seed(conn, _CASE, anchor_name=None, product="NOANCHOR")
    details = [
        {"material_name": n, "actual_amount": None} for n, _ in _CASE
    ]
    derived, total = bs.derive_details_from_recipe(conn, rid, 10000.0, details)

    weights = [w for _, w in _CASE]
    want = bs.scale_theory(weights, 10000.0)
    assert [float(d["theory_amount"]) for d in derived] == want
    assert total == 10000.0
