from typing import Any

from fastapi import HTTPException, Request, status

from .database import get_connection
from .security import verify_password

ACCESS_LEVEL_RANK = {
    "operator": 1,
    "manager": 2,
}

ACCESS_LEVEL_LABEL = {
    "operator": "담당자",
    "manager": "책임자",
}


def to_public_user(row) -> dict[str, Any]:
    access_level = row["access_level"] or ("manager" if row["role"] == "admin" else "operator")
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "role_label": ACCESS_LEVEL_LABEL.get(access_level, "User"),
        "access_level": access_level,
    }


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, username, display_name, role, access_level, is_active
            FROM users
            WHERE id = ? AND is_active = 1
            """,
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return to_public_user(row)


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, username, password_hash, display_name, role, access_level, is_active
            FROM users
            WHERE username = ?
            """,
            (username.strip(),),
        ).fetchone()
    if not row or not row["is_active"]:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return to_public_user(row)


def get_user_for_selection(user_id: int, allowed_levels: tuple[str, ...]) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, username, display_name, role, access_level, is_active
            FROM users
            WHERE id = ? AND is_active = 1
            """,
            (user_id,),
        ).fetchone()
    if not row:
        return None
    user = to_public_user(row)
    if user["access_level"] not in allowed_levels:
        return None
    return user


def list_users_by_access_levels(*access_levels: str) -> list[dict[str, Any]]:
    if not access_levels:
        return []

    placeholders = ", ".join("?" for _ in access_levels)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT id, username, display_name, role, access_level, is_active
            FROM users
            WHERE is_active = 1 AND access_level IN ({placeholders})
            ORDER BY display_name ASC, username ASC
            """,
            access_levels,
        ).fetchall()
    return [to_public_user(row) for row in rows]


def get_current_user(request: Request, required: bool = True) -> dict[str, Any] | None:
    user_id = request.session.get("user_id")
    if not user_id:
        if required:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="AUTH_REQUIRED")
        return None

    user = get_user_by_id(int(user_id))
    if user:
        max_level = request.session.get("max_level")
        if max_level:
            max_rank = ACCESS_LEVEL_RANK.get(max_level, 0)
            user_rank = ACCESS_LEVEL_RANK.get(user.get("access_level", ""), 0)
            if user_rank > max_rank:
                user["access_level"] = max_level
        return user

    request.session.clear()
    if required:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="AUTH_REQUIRED")
    return None


def require_authenticated(request: Request) -> dict[str, Any]:
    return get_current_user(request, required=True)


def has_access_level(user: dict[str, Any], required_level: str) -> bool:
    current_rank = ACCESS_LEVEL_RANK.get(str(user.get("access_level")), 0)
    required_rank = ACCESS_LEVEL_RANK.get(required_level, 0)
    return current_rank >= required_rank


def require_access_level(required_level: str):
    def dependency(request: Request) -> dict[str, Any]:
        user = get_current_user(request, required=True)
        if not has_access_level(user, required_level):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FORBIDDEN")
        return user

    return dependency


def login_user(request: Request, user: dict[str, Any], max_level: str | None = None) -> None:
    request.session.clear()
    request.session["user_id"] = user["id"]
    if max_level:
        request.session["max_level"] = max_level


def logout_user(request: Request) -> None:
    request.session.clear()
