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


def purge_expired_canceled(
    connection: sqlite3.Connection,
    *,
    retention_days: int | None = None,
) -> list[dict]:
    """취소 후 보존 기한이 지난 배합 기록을 물리 삭제한다.

    취소(soft)는 실수 되돌리기용이고, 현장 판단으로는 실수는 하루 이틀 안에 알아챈다
    (사용자 결정 2026-07-29 — "무기한 남길 필요 없다"). 기본 3일 유예 후 자동 정리.
    IRMS_CANCELED_RETENTION_DAYS=0 이면 비활성(무기한 보존, 종전 동작).

    파괴적 동작이므로 하드 삭제와 같은 원칙을 지킨다:
      · 기록 전체 스냅샷(헤더+자재 행)을 감사로그(blend_record_purged)에 남긴다
      · 일일 백업 이후에 도는 것을 전제로 한다(백업에는 남는다 — serve.py 순서 참조)
    취소 시각은 updated_at 을 쓴다(취소가 마지막 쓰기 — 취소된 기록은 수정 불가).
    """
    from datetime import datetime, timedelta, timezone

    from ..config import CANCELED_RETENTION_DAYS
    from ..db import write_audit_log

    days = CANCELED_RETENTION_DAYS if retention_days is None else retention_days
    if not days or days <= 0:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = connection.execute(
        "SELECT id FROM blend_records WHERE status = 'canceled' AND updated_at < ?",
        (cutoff,),
    ).fetchall()
    purged: list[dict] = []
    for row in rows:
        record_id = int(row["id"])
        header = connection.execute(
            """
            SELECT id, product_lot, recipe_id, product_name, ink_name, position, worker,
                   work_date, work_time, total_amount, scale, status, note, reactor,
                   manual_entry, is_bulk_regenerated, manual_absence_reason,
                   reviewed_by, reviewed_at, approved_by, approved_at,
                   created_by, created_at, updated_at
            FROM blend_records WHERE id = ?
            """,
            (record_id,),
        ).fetchone()
        details = connection.execute(
            """
            SELECT material_name, material_code, material_lot, ratio,
                   theory_amount, actual_amount, carried_over, manual_entry
            FROM blend_details WHERE blend_record_id = ? ORDER BY sequence_order, id
            """,
            (record_id,),
        ).fetchall()
        snapshot = {
            "header": {k: header[k] for k in header.keys()},
            "rows": [
                [d["material_name"], d["material_code"], d["material_lot"],
                 d["ratio"], d["theory_amount"], d["actual_amount"],
                 int(d["carried_over"] or 0), int(d["manual_entry"] or 0)]
                for d in details
            ],
        }
        result = delete_blend_record(connection, record_id)
        if result is None:
            continue
        write_audit_log(
            connection,
            action="blend_record_purged",
            target_type="blend_record",
            target_id=str(result.record_id),
            target_label=result.product_lot,
            details={
                "reason": f"취소 후 {days}일 경과 자동 정리",
                "canceled_at": header["updated_at"],
                "snapshot": snapshot,
            },
        )
        purged.append({"id": result.record_id, "product_lot": result.product_lot})
    return purged
