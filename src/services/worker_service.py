"""작업자 명단(비밀번호 없는 이름 등록부) 서비스.

근태를 제외한 작업자는 로그인 대신 이름만 입력한다. 처음 보는 이름은 등록 확인 후
명단(workers)에 추가된다. 자동완성·오타중복 정리에 사용.
"""

import sqlite3
from typing import Any


def list_workers(connection: sqlite3.Connection, *, active_only: bool = True) -> list[dict[str, Any]]:
    where = "WHERE is_active = 1" if active_only else ""
    rows = connection.execute(
        f"""
        SELECT id, name, is_active, created_at,
               COALESCE(is_manager, 0) AS is_manager,
               (password_hash IS NOT NULL) AS has_password
        FROM workers {where} ORDER BY name
        """
    ).fetchall()
    return [
        {
            "id": int(r["id"]), "name": r["name"], "is_active": bool(r["is_active"]),
            "created_at": r["created_at"],
            "is_manager": bool(r["is_manager"]) and bool(r["has_password"]),
        }
        for r in rows
    ]


def manager_names(connection: sqlite3.Connection) -> list[str]:
    """로그인 가능한(비번 있는) 책임자 이름 목록 — 로그인 화면 자동완성용."""
    rows = connection.execute(
        "SELECT name FROM workers WHERE is_active = 1 AND is_manager = 1 "
        "AND password_hash IS NOT NULL ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def active_manager_count(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS c FROM workers WHERE is_active = 1 AND is_manager = 1 "
        "AND password_hash IS NOT NULL"
    ).fetchone()
    return int(row["c"])


def set_manager(connection: sqlite3.Connection, worker_id: int, password_hash: str) -> None:
    """이용자를 책임자로 지정(개인 비밀번호 설정)."""
    connection.execute(
        "UPDATE workers SET is_manager = 1, password_hash = ? WHERE id = ?",
        (password_hash, worker_id),
    )


def reset_manager_password(connection: sqlite3.Connection, worker_id: int, password_hash: str) -> None:
    connection.execute(
        "UPDATE workers SET password_hash = ?, session_token = NULL WHERE id = ? AND is_manager = 1",
        (password_hash, worker_id),
    )


def revoke_manager(connection: sqlite3.Connection, worker_id: int) -> None:
    """책임자 해제 — 비밀번호·세션 제거, 다시 이름만 쓰는 이용자로."""
    connection.execute(
        "UPDATE workers SET is_manager = 0, password_hash = NULL, session_token = NULL WHERE id = ?",
        (worker_id,),
    )


def get_worker(connection: sqlite3.Connection, worker_id: int) -> dict[str, Any] | None:
    r = connection.execute(
        "SELECT id, name, is_active, COALESCE(is_manager,0) AS is_manager, "
        "(password_hash IS NOT NULL) AS has_password FROM workers WHERE id = ?",
        (worker_id,),
    ).fetchone()
    if not r:
        return None
    return {
        "id": int(r["id"]), "name": r["name"], "is_active": bool(r["is_active"]),
        "is_manager": bool(r["is_manager"]) and bool(r["has_password"]),
    }


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


def has_blend_records(connection: sqlite3.Connection, name: str) -> bool:
    """이 이름으로 남은 배합 기록이 있는가(삭제 안전장치 — 있으면 비활성화 권장)."""
    try:
        row = connection.execute(
            "SELECT 1 FROM blend_records WHERE worker = ? LIMIT 1", (name.strip(),)
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def delete_worker(connection: sqlite3.Connection, worker_id: int) -> None:
    """명단에서 완전 삭제(오타 정리용). 호출 전 책임자·기록 보유 여부를 확인할 것."""
    connection.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
