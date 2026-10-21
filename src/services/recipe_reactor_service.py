"""반응기별 현재판 지정(2026-10-21) — 조회 응답 조립과 지정 기록.

판정 규칙 자체는 recipe_helpers.resolve_recipe_for_reactor 한 곳에 있다. 여기는 버전 관리
화면이 읽는 응답 모양(반응기 1~4 각각의 지정·효력 판·이력)을 만들고, 지정 행을 추가한다.
"""

import sqlite3
from typing import Any

from .recipe_helpers import (
    REACTOR_NUMBERS,
    active_reactor_assignments,
    current_reactor_assignments,
    fetch_chain,
    find_chain_root,
    resolve_chain_tip,
    version_labels,
)

HISTORY_LIMIT = 20


def _version_names(connection, ids: list[int]) -> dict[int, str | None]:
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    return {
        int(r["id"]): r["version_name"]
        for r in connection.execute(
            f"SELECT id, version_name FROM recipes WHERE id IN ({placeholders})", ids
        ).fetchall()
    }


def build_reactor_assignment_payload(connection, recipe_id: int) -> dict[str, Any]:
    """GET /recipes/{id}/reactor-assignments 응답. recipe_id 는 존재가 확인된 판이어야 한다."""
    root_id = find_chain_root(connection, int(recipe_id))
    chain = fetch_chain(connection, root_id)
    chain_ids = [int(r["id"]) for r in chain]
    labels = version_labels(connection, root_id)
    names = _version_names(connection, chain_ids)
    current_id = resolve_chain_tip(connection, root_id)
    current = current_reactor_assignments(connection, root_id)
    active = active_reactor_assignments(connection, root_id)

    reactors: list[dict[str, Any]] = []
    for reactor in REACTOR_NUMBERS:
        a = current.get(reactor) or {}
        assigned_id = a.get("recipe_id")
        effective_id = active.get(reactor, current_id)
        reactors.append({
            "reactor": reactor,
            "recipe_id": assigned_id,
            "version_label": labels.get(assigned_id) if assigned_id is not None else None,
            "version_name": names.get(assigned_id) if assigned_id is not None else None,
            "effective_recipe_id": effective_id,
            "effective_version_label": labels.get(effective_id),
            "effective_version_name": names.get(effective_id),
            "is_fallback": reactor not in active,
            "note": a.get("note"),
            "assigned_by": a.get("assigned_by"),
            "assigned_at": a.get("assigned_at"),
        })

    history: list[dict[str, Any]] = []
    try:
        rows = connection.execute(
            "SELECT reactor, recipe_id, note, assigned_by, assigned_at "
            "FROM recipe_reactor_assignments WHERE chain_root_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (root_id, HISTORY_LIMIT),
        ).fetchall()
    except sqlite3.OperationalError:  # 표 없는 구버전 스키마
        rows = []
    extra_ids = [
        int(r["recipe_id"]) for r in rows
        if r["recipe_id"] is not None and int(r["recipe_id"]) not in names
    ]
    names.update(_version_names(connection, extra_ids))
    for r in rows:
        rid = int(r["recipe_id"]) if r["recipe_id"] is not None else None
        history.append({
            "reactor": int(r["reactor"]),
            "recipe_id": rid,
            "version_label": labels.get(rid) if rid is not None else None,
            "version_name": names.get(rid) if rid is not None else None,
            "note": r["note"],
            "assigned_by": r["assigned_by"],
            "assigned_at": r["assigned_at"],
        })

    versions = [
        {
            "id": int(rec["id"]),
            "version_label": labels.get(int(rec["id"])),
            "version_name": names.get(int(rec["id"])),
            "status": rec["status"],
            "is_current": int(rec["id"]) == current_id,
        }
        for rec in chain
    ]

    return {
        "root_id": root_id,
        "current_id": current_id,
        "use_reactor": any(bool(rec.get("use_reactor")) for rec in chain),
        "reactors": reactors,
        "history": history,
        "versions": versions,
    }


def insert_reactor_assignment(
    connection,
    *,
    root_id: int,
    reactor: int,
    recipe_id: int | None,
    note: str | None,
    assigned_by: str,
    assigned_at: str,
) -> None:
    connection.execute(
        "INSERT INTO recipe_reactor_assignments "
        "(chain_root_id, reactor, recipe_id, note, assigned_by, assigned_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (int(root_id), int(reactor), recipe_id, note, assigned_by, assigned_at),
    )


def reactors_assigned_to(connection, recipe_id: int) -> list[int]:
    """이 판을 현재 지정으로 쓰는 반응기 번호(오름차순)."""
    root_id = find_chain_root(connection, int(recipe_id))
    current = current_reactor_assignments(connection, root_id)
    return sorted(r for r, a in current.items() if a.get("recipe_id") == int(recipe_id))


def rekey_chain_root(connection, old_root_id: int, new_root_id: int) -> None:
    """루트 판이 삭제돼 루트가 바뀌면 지정 행의 chain_root_id 를 새 루트로 옮긴다."""
    if int(old_root_id) == int(new_root_id):
        return
    try:
        connection.execute(
            "UPDATE recipe_reactor_assignments SET chain_root_id = ? WHERE chain_root_id = ?",
            (int(new_root_id), int(old_root_id)),
        )
    except sqlite3.OperationalError:
        pass
