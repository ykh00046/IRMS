"""DHR 전용 레시피 분리 — 일반 배합 선택/조회에서 제외."""

import importlib


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IRMS_ENV", "development")
    monkeypatch.setenv("IRMS_SEED_DEMO_DATA", "0")
    import src.config as cfg
    importlib.reload(cfg)
    import src.db.schema as sc
    sc.init_db()


def _add_recipe(conn, name, is_dhr):
    conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status, is_dhr, created_by, created_at) "
        "VALUES (?, '잉크', 'completed', ?, 't', '2026-06-26')",
        (name, is_dhr),
    )
    rid = conn.execute("SELECT id FROM recipes WHERE product_name = ?", (name,)).fetchone()[0]
    conn.execute(
        "INSERT INTO materials (name, unit_type, unit, color_group, category, is_active) "
        "VALUES (?, 'weight', 'g', 'none', '기타', 1)",
        (name + "-M",),
    )
    mid = conn.execute("SELECT id FROM materials ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO recipe_items (recipe_id, material_id, value_weight) VALUES (?, ?, 100)",
        (rid, mid),
    )
    return rid


def test_dhr_recipe_excluded_from_blend_selection(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from src.db import get_connection
    from src.services import blend_service

    with get_connection() as conn:
        _add_recipe(conn, "일반P", 0)
        dhr_id = _add_recipe(conn, "DHR전용P", 1)
        conn.commit()

        names = [r["product_name"] for r in blend_service.list_blend_recipes(conn)]
        assert "일반P" in names
        assert "DHR전용P" not in names  # DHR 전용은 배합 선택에서 제외

        # products 분리
        reg = [r[0] for r in conn.execute(
            "SELECT DISTINCT product_name FROM recipes WHERE COALESCE(is_dhr,0)=0")]
        dhr = [r[0] for r in conn.execute(
            "SELECT DISTINCT product_name FROM recipes WHERE COALESCE(is_dhr,0)=1")]
        assert reg == ["일반P"]
        assert dhr == ["DHR전용P"]

        # DHR 해제 → 다시 배합 선택에 포함
        conn.execute("UPDATE recipes SET is_dhr = 0 WHERE id = ?", (dhr_id,))
        conn.commit()
        names2 = [r["product_name"] for r in blend_service.list_blend_recipes(conn)]
        assert "DHR전용P" in names2
