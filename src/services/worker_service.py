"""작업자 명단(비밀번호 없는 이름 등록부) 서비스.

근태를 제외한 작업자는 로그인 대신 이름만 입력한다. 처음 보는 이름은 등록 확인 후
명단(workers)에 추가된다. 자동완성·오타중복 정리에 사용.
"""

import sqlite3
from typing import Any


def list_workers(connection: sqlite3.Connection, *, active_only: bool = True) -> list[dict[str, Any]]:
    where = "WHERE is_active = 1" if active_only else ""
    rows = connection.execute(
        f"SELECT id, name, is_active, created_at FROM workers {where} ORDER BY name"
    ).fetchall()
    return [
        {"id": int(r["id"]), "name": r["name"], "is_active": bool(r["is_active"]),
         "created_at": r["created_at"]}
        for r in rows
    ]


def worker_names(connection: sqlite3.Connection) -> list[str]:
    return [w["name"] for w in list_workers(connection)]


def exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM workers WHERE name = ? AND is_active = 1", (name.strip(),)
    ).fetchone()
    return row is not None


def register(connection: sqlite3.Connection, name: str, created_at: str) -> dict[str, Any]:
    """이름을 명단에 등록(이미 있으면 그대로). {name, created} 반환."""
    clean = name.strip()
    if not clean:
        raise ValueError("이름이 비어 있습니다.")
    existing = connection.execute(
        "SELECT id, is_active FROM workers WHERE name = ?", (clean,)
    ).fetchone()
    if existing:
        if not existing["is_active"]:
            connection.execute(
                "UPDATE workers SET is_active = 1 WHERE id = ?", (existing["id"],)
            )
        return {"name": clean, "created": False}
    connection.execute(
        "INSERT INTO workers (name, is_active, created_at) VALUES (?, 1, ?)",
        (clean, created_at),
    )
    return {"name": clean, "created": True}


def set_active(connection: sqlite3.Connection, worker_id: int, active: bool) -> None:
    connection.execute(
        "UPDATE workers SET is_active = ? WHERE id = ?", (1 if active else 0, worker_id)
    )


def rename(connection: sqlite3.Connection, worker_id: int, new_name: str) -> None:
    clean = new_name.strip()
    if not clean:
        raise ValueError("이름이 비어 있습니다.")
    connection.execute("UPDATE workers SET name = ? WHERE id = ?", (clean, worker_id))
