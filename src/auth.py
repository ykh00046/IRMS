import secrets
import time
from typing import Any

from fastapi import HTTPException, Request, status

from . import config
from .db import get_connection
from .security import verify_password

# 권한 2단계: 담당자(현장·조회) < 책임자(최상위 — 레시피·근태·사용자·시스템 관리 전부).
# 구 3단계의 '관리자(admin)'는 책임자로 흡수됨. 하위호환: 남아있는 access_level='admin'
# 값은 to_public_user/마이그레이션에서 manager 로 승격 처리.
ACCESS_LEVEL_RANK = {
    "operator": 1,
    "manager": 2,
    "admin": 2,  # legacy value → 책임자와 동급으로 취급(마이그레이션 전 잔존 대비)
}

ACCESS_LEVEL_LABEL = {
    "operator": "담당자",
    "manager": "책임자",
    "admin": "책임자",  # legacy value
}

def to_public_user(row) -> dict[str, Any]:
    access_level = row["access_level"] or ("manager" if row["role"] == "admin" else "operator")
    # 구 3단계 잔존 값 정규화: 관리자(admin) → 책임자(manager).
    if access_level == "admin":
        access_level = "manager"
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


def _worker_to_public(row) -> dict[str, Any]:
    """책임자로 지정된 이용자(workers) 행 → 공용 사용자 dict(access_level=manager)."""
    return {
        "id": int(row["id"]),
        "username": row["name"],          # 이름이 곧 로그인 아이디
        "display_name": row["name"],
        "role": "manager",
        "role_label": ACCESS_LEVEL_LABEL["manager"],
        "access_level": "manager",
        "is_worker_manager": True,
    }


def authenticate_manager_worker(name: str, password: str) -> dict[str, Any] | None:
    """이름+비밀번호로 책임자(이용자 명단에서 지정된 사람) 인증."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT id, name, password_hash, is_active, COALESCE(is_manager,0) AS is_manager "
            "FROM workers WHERE name = ?",
            (name.strip(),),
        ).fetchone()
    if not row or not row["is_active"] or not row["is_manager"] or not row["password_hash"]:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return _worker_to_public(row)


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


# 관리(책임자) 인증이 세션에 쓰는 키 — 이것만 지운다. 같은 쿠키 세션을 배합 작업자
# (blend_session)·근태(att_user)도 쓰므로 session.clear() 를 부르면 책임자
# 로그인/로그아웃이 현장 작업자 세션까지 끊어버린다(배합 재로그인 요구 증상).
_AUTH_SESSION_KEYS = ("user_id", "session_token", "mgr_worker_id", "mgr_token", "auth_seen")

# 유휴 기준 시각을 담는 세션 키. 책임자 요청이 들어올 때마다 갱신되고,
# MANAGER_IDLE_TIMEOUT_SECONDS 를 넘기면 로그인 세션만 끊는다.
_AUTH_SEEN_KEY = "auth_seen"


def _clear_auth_session(request: Request) -> None:
    for key in _AUTH_SESSION_KEYS:
        request.session.pop(key, None)


def _now() -> float:
    """유휴 판정용 시계 — 전역 time 을 직접 쓰지 않고 여기로 모은다.

    (테스트가 time.time 을 통째로 밀면 세션 쿠키 서명 검증까지 만료돼 버려
     엉뚱한 401 이 난다. 유휴 로직만 따로 흔들 수 있도록 분리.)
    """
    return time.time()


def _idle_expired(request: Request) -> bool:
    """책임자 세션이 유휴 만료됐는지. 기준 시각이 없으면(구 세션) 지금으로 본다.

    배합 화면 하트비트(/api/blend/session/me)는 이 함수를 타지 않으므로,
    화면만 띄워둔 채 방치하면 정상적으로 만료된다(작업자 세션은 영향 없음).
    """
    timeout = int(getattr(config, "MANAGER_IDLE_TIMEOUT_SECONDS", 0) or 0)
    if timeout <= 0:  # 0 이하 = 유휴 만료 비활성(환경변수로 끌 수 있게)
        return False
    seen = request.session.get(_AUTH_SEEN_KEY)
    if not isinstance(seen, (int, float)):
        return False
    return (_now() - float(seen)) > timeout


def _touch_auth_session(request: Request) -> None:
    request.session[_AUTH_SEEN_KEY] = _now()


def _expire_idle(request: Request, required: bool) -> None:
    """유휴 만료 처리 — 로그인 세션만 지우고 401. 작업자·근태 세션은 보존."""
    _clear_auth_session(request)
    if required:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="AUTH_REQUIRED")


def get_current_user(request: Request, required: bool = True) -> dict[str, Any] | None:
    # 0) 유휴 만료 검사 — 공용 PC 에서 책임자 권한이 열린 채 방치되는 것을 막는다.
    #    (로그인 세션이 있을 때만 의미가 있으므로 키 존재 여부부터 확인)
    has_auth_session = bool(request.session.get("mgr_worker_id") or request.session.get("user_id"))
    if has_auth_session and _idle_expired(request):
        _expire_idle(request, required)
        return None

    # 1) 이름 기반 책임자(이용자 명단) 세션
    mgr_worker_id = request.session.get("mgr_worker_id")
    if mgr_worker_id:
        token = request.session.get("mgr_token")
        with get_connection() as connection:
            row = connection.execute(
                "SELECT id, name, is_active, COALESCE(is_manager,0) AS is_manager, session_token "
                "FROM workers WHERE id = ?",
                (int(mgr_worker_id),),
            ).fetchone()
        if row and row["is_active"] and row["is_manager"] and (token or "") == (row["session_token"] or ""):
            _touch_auth_session(request)  # 활동 있음 → 유휴 카운트 리셋
            return _worker_to_public(row)
        _clear_auth_session(request)
        if required:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="AUTH_REQUIRED")
        return None

    # 2) 레거시 users 계정(admin 부트스트랩/폴백) 세션
    user_id = request.session.get("user_id")
    if not user_id:
        if required:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="AUTH_REQUIRED",
            )
        return None

    cookie_token = request.session.get("session_token")
    with get_connection() as connection:
        row = connection.execute(
            "SELECT id, username, display_name, role, access_level, is_active, session_token "
            "FROM users WHERE id = ? AND is_active = 1",
            (int(user_id),),
        ).fetchone()
    if row and (cookie_token or "") == (row["session_token"] or ""):
        _touch_auth_session(request)  # 활동 있음 → 유휴 카운트 리셋
        return to_public_user(row)

    _clear_auth_session(request)
    if required:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="AUTH_REQUIRED",
        )
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


def login_user(request: Request, user: dict[str, Any]) -> None:
    token = secrets.token_urlsafe(32)
    with get_connection() as connection:
        connection.execute(
            "UPDATE users SET session_token = ? WHERE id = ?",
            (token, int(user["id"])),
        )
        connection.commit()
    _clear_auth_session(request)  # 배합 작업자·근태 세션은 보존
    request.session["user_id"] = user["id"]
    request.session["session_token"] = token
    _touch_auth_session(request)


def login_manager_worker(request: Request, worker: dict[str, Any]) -> None:
    """이름 기반 책임자 로그인 — workers.session_token 회전(단일 세션)."""
    token = secrets.token_urlsafe(32)
    with get_connection() as connection:
        connection.execute(
            "UPDATE workers SET session_token = ? WHERE id = ?",
            (token, int(worker["id"])),
        )
        connection.commit()
    _clear_auth_session(request)  # 배합 작업자·근태 세션은 보존
    request.session["mgr_worker_id"] = worker["id"]
    request.session["mgr_token"] = token
    _touch_auth_session(request)


def logout_user(request: Request) -> None:
    mgr_worker_id = request.session.get("mgr_worker_id")
    if mgr_worker_id:
        with get_connection() as connection:
            connection.execute(
                "UPDATE workers SET session_token = NULL WHERE id = ?",
                (int(mgr_worker_id),),
            )
            connection.commit()
    user_id = request.session.get("user_id")
    if user_id:
        with get_connection() as connection:
            connection.execute(
                "UPDATE users SET session_token = NULL WHERE id = ?",
                (int(user_id),),
            )
            connection.commit()
    _clear_auth_session(request)  # 배합 작업자·근태 세션은 보존
