import json
import sqlite3
from typing import Any

from .queries import row_to_dict
from .time_utils import utc_now_text


def write_audit_log(
    connection: sqlite3.Connection,
    *,
    action: str,
    actor: dict[str, Any] | None = None,
    target_type: str | None = None,
    target_id: Any | None = None,
    target_label: str | None = None,
    details: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO audit_logs (
            action,
            actor_user_id,
            actor_username,
            actor_display_name,
            actor_access_level,
            target_type,
            target_id,
            target_label,
            details_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            action,
            actor.get("id") if actor else None,
            actor.get("username") if actor else None,
            actor.get("display_name") if actor else None,
            actor.get("access_level") if actor else None,
            target_type,
            None if target_id is None else str(target_id),
            target_label,
            json.dumps(details or {}, ensure_ascii=False),
            created_at or utc_now_text(),
        ),
    )


def list_audit_logs(
    connection: sqlite3.Connection,
    *,
    limit: int = 100,
    offset: int = 0,
    action: str | None = None,
    after_id: int | None = None,
    ascending: bool = False,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 500))
    where_parts: list[str] = []
    params: list[Any] = []

    if action:
        where_parts.append("action = ?")
        params.append(action)

    if after_id is not None:
        where_parts.append("id > ?")
        params.append(int(after_id))

    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    order_sql = "ORDER BY created_at ASC, id ASC" if ascending else "ORDER BY created_at DESC, id DESC"
    rows = connection.execute(
        f"""
        SELECT
            id,
            action,
            actor_user_id,
            actor_username,
            actor_display_name,
            actor_access_level,
            target_type,
            target_id,
            target_label,
            details_json,
            created_at
        FROM audit_logs
        {where_sql}
        {order_sql}
        LIMIT ? OFFSET ?
        """,
        [*params, safe_limit, max(0, int(offset))],
    ).fetchall()

    items = [row_to_dict(row) for row in rows]
    for item in items:
        try:
            item["details"] = json.loads(item.pop("details_json") or "{}")
        except json.JSONDecodeError:
            item["details"] = {}
            item.pop("details_json", None)
    return items
