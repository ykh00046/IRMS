import sqlite3
from dataclasses import dataclass

from . import dhr_cache


@dataclass(frozen=True, slots=True)
class RecipeDeletionResult:
    recipe_id: int
    product_name: str
    linked_record_count: int
    deleted_linked_records: bool
    # BUG 2: 삭제 대상의 자식들을 조부모(삭제 대상의 revision_of)로 재연결한 id 목록.
    #        루트 삭제면 NULL 로 승격(승격 id 도 여기 기록).
    relinked_child_ids: tuple[int, ...] = ()
    # GAP 4: 삭제 대상을 1차(stage1)로 참조하던 2차 레시피들의 링크를 NULL 로 정리한 id 목록.
    stage1_cleared_recipe_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class BlendRecordDeletionResult:
    record_id: int
    product_lot: str


def delete_recipe(
    connection: sqlite3.Connection,
    recipe_id: int,
    *,
    delete_linked_records: bool,
) -> RecipeDeletionResult | None:
    row = connection.execute(
        "SELECT id, product_name, revision_of FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    if row is None:
        return None

    linked_rows = connection.execute(
        "SELECT id FROM blend_records WHERE recipe_id = ?",
        (recipe_id,),
    ).fetchall()
    linked_ids = [int(linked_row["id"]) for linked_row in linked_rows]

    if delete_linked_records:
        for linked_id in linked_ids:
            delete_blend_record(connection, linked_id)
    else:
        connection.execute(
            "UPDATE blend_records SET recipe_id = NULL WHERE recipe_id = ?",
            (recipe_id,),
        )

    # BUG 2: 개정 체인 중간 노드를 삭제해도 이력이 끊기지 않도록, 삭제 대상의 자식들을
    # 삭제 대상의 부모(조부모)로 재연결한다. v1→v2→v3 에서 v2 삭제 시 v3.revision_of=v1
    # 이 되어 v1 과의 계보가 유지된다. 삭제 대상이 루트면 revision_of 가 NULL 이므로
    # 자식들이 루트로 승격(기존 동작 보존).
    grandparent = row["revision_of"]
    child_rows = connection.execute(
        "SELECT id FROM recipes WHERE revision_of = ?",
        (recipe_id,),
    ).fetchall()
    relinked_child_ids = tuple(int(r["id"]) for r in child_rows)
    connection.execute(
        "UPDATE recipes SET revision_of = ? WHERE revision_of = ?",
        (grandparent, recipe_id),
    )

    # GAP 4: 삭제 대상을 1차(stage1)로 참조하던 2차 레시피의 링크를 정리(NULL). 참조
    # 무결성(FK) 이 없어 앱에서 정리하지 않으면 stage1_recipe_id 가 댕글링된다.
    # (일부 단위테스트의 최소 스키마엔 컬럼이 없으므로 존재할 때만 실행.)
    stage1_cleared_recipe_ids: tuple[int, ...] = ()
    recipe_cols = {
        r["name"] for r in connection.execute("PRAGMA table_info(recipes)").fetchall()
    }
    if "stage1_recipe_id" in recipe_cols:
        ref_rows = connection.execute(
            "SELECT id FROM recipes WHERE stage1_recipe_id = ?",
            (recipe_id,),
        ).fetchall()
        stage1_cleared_recipe_ids = tuple(int(r["id"]) for r in ref_rows)
        if stage1_cleared_recipe_ids:
            connection.execute(
                "UPDATE recipes SET stage1_recipe_id = NULL WHERE stage1_recipe_id = ?",
                (recipe_id,),
            )

    connection.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (recipe_id,))
    connection.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    return RecipeDeletionResult(
        recipe_id=recipe_id,
        product_name=str(row["product_name"]),
        linked_record_count=len(linked_ids),
        deleted_linked_records=delete_linked_records,
        relinked_child_ids=relinked_child_ids,
        stage1_cleared_recipe_ids=stage1_cleared_recipe_ids,
    )


def delete_blend_record(
    connection: sqlite3.Connection,
    record_id: int,
) -> BlendRecordDeletionResult | None:
    row = connection.execute(
        "SELECT id, product_lot FROM blend_records WHERE id = ?",
        (record_id,),
    ).fetchone()
    if row is None:
        return None

    connection.execute(
        "UPDATE viscosity_readings SET blend_record_id = NULL WHERE blend_record_id = ?",
        (record_id,),
    )
    connection.execute("DELETE FROM blend_details WHERE blend_record_id = ?", (record_id,))
    connection.execute("DELETE FROM blend_records WHERE id = ?", (record_id,))
    # POLISH-7a: hard 삭제된 기록의 DHR PDF 캐시(비서명본)를 함께 지운다 — 디스크 잔류 방지.
    dhr_cache.purge(record_id)
    return BlendRecordDeletionResult(
        record_id=record_id,
        product_lot=str(row["product_lot"]),
    )
