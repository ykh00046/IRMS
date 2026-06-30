import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecipeDeletionResult:
    recipe_id: int
    product_name: str
    linked_record_count: int
    deleted_linked_records: bool


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
        "SELECT id, product_name FROM recipes WHERE id = ?",
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

    connection.execute("UPDATE recipes SET revision_of = NULL WHERE revision_of = ?", (recipe_id,))
    connection.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (recipe_id,))
    connection.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    return RecipeDeletionResult(
        recipe_id=recipe_id,
        product_name=str(row["product_name"]),
        linked_record_count=len(linked_ids),
        deleted_linked_records=delete_linked_records,
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
    return BlendRecordDeletionResult(
        record_id=record_id,
        product_lot=str(row["product_lot"]),
    )
