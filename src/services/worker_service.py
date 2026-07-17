"""작업자 명단(비밀번호 없는 이름 등록부) 서비스.

근태를 제외한 작업자는 로그인 대신 이름만 입력한다. 처음 보는 이름은 등록 확인 후
명단(workers)에 추가된다. 자동완성·오타중복 정리에 사용.
"""

import re
import sqlite3
from typing import Any

# 완성형 한글·영문·숫자 2~20자만 허용. 동명이인 구분 숫자는 허용(김민호3),
# 공백('김 민호')과 자모 낱글자('ㄱ', 'ㅏ') 같은 명백한 입력 실수는 차단.
_NAME_RE = re.compile(r"^[가-힣A-Za-z0-9]{2,20}$")


def validate_name(name: str) -> str:
    """이름 검증 후 정리된 이름 반환. 문제가 있으면 ValueError(한글 메시지)."""
    clean = (name or "").strip()
    if not clean:
        raise ValueError("이름을 입력하세요.")
    if re.search(r"\s", clean):
        raise ValueError("이름에 공백을 넣을 수 없습니다. (예: '김 민호' → '김민호')")
    if len(clean) < 2:
        raise ValueError("이름이 너무 짧습니다. 성과 이름을 포함해 2자 이상 입력하세요.")
    if not _NAME_RE.fullmatch(clean):
        raise ValueError(
            "이름은 한글·영문·숫자 2~20자만 가능합니다. "
            "자모 낱글자(ㄱ, ㅏ 등)나 특수문자는 쓸 수 없습니다."
        )
    return clean


def list_workers(connection: sqlite3.Connection, *, active_only: bool = True) -> list[dict[str, Any]]:
    where = "WHERE is_active = 1" if active_only else ""
    rows = connection.execute(
        f"""
        SELECT id, name, is_active, created_at,
               COALESCE(is_manager, 0) AS is_manager,
               (password_hash IS NOT NULL) AS has_password,
               category
        FROM workers {where} ORDER BY name
        """
    ).fetchall()
    return [
        {
            "id": int(r["id"]), "name": r["name"], "is_active": bool(r["is_active"]),
            "created_at": r["created_at"],
            "is_manager": bool(r["is_manager"]) and bool(r["has_password"]),
            # 분류(파트): 약품/합성/잉크/용수. 미지정 시 NULL.
            "category": r["category"],
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


def register(
    connection: sqlite3.Connection,
    name: str,
    created_at: str,
    *,
    category: str | None = None,
) -> dict[str, Any]:
    """이름을 명단에 등록(이미 있으면 그대로). {name, created, reactivated, category} 반환.

    category 는 키워드 전용(하위호환 — 기존 register(conn, name, now) 호출처 unaffected).
    신규 생성 시 저장하고, 비활성 동명 작업자 재활성화 시에도 category 가 주어지면 갱신한다.
    값 검증(허용 파트 여부)은 라우트에서, 빈 문자열은 None 으로 정리해서 받는 것을 전제.
    """
    clean = validate_name(name)
    clean_category = (category or "").strip() or None
    existing = connection.execute(
        "SELECT id, is_active FROM workers WHERE name = ?", (clean,)
    ).fetchone()
    if existing:
        reactivated = False
        if not existing["is_active"]:
            # 재활성화 시 책임자 권한·비밀번호는 부활시키지 않는다(무인증 등록 경로라
            # 비활성화된 책임자 계정이 이름 입력만으로 살아나는 것을 차단).
            connection.execute(
                "UPDATE workers SET is_active = 1, is_manager = 0, "
                "password_hash = NULL, session_token = NULL, "
                "category = COALESCE(?, category) WHERE id = ?",
                (clean_category, existing["id"]),
            )
            reactivated = True
        elif clean_category:
            # 이미 활성인 동명 작업자가 있어도 category 가 주어지면 갱신(등록 흐름에서
            # 파트 선택창을 거친 요청이므로 사용자 의도로 본다).
            connection.execute(
                "UPDATE workers SET category = ? WHERE id = ?",
                (clean_category, existing["id"]),
            )
        return {"name": clean, "created": False, "reactivated": reactivated,
                "category": clean_category}
    connection.execute(
        "INSERT INTO workers (name, is_active, created_at, category) "
        "VALUES (?, 1, ?, ?)",
        (clean, created_at, clean_category),
    )
    return {"name": clean, "created": True, "reactivated": False,
            "category": clean_category}


def set_active(connection: sqlite3.Connection, worker_id: int, active: bool) -> None:
    connection.execute(
        "UPDATE workers SET is_active = ? WHERE id = ?", (1 if active else 0, worker_id)
    )


def set_category(connection: sqlite3.Connection, worker_id: int, category: str | None) -> None:
    """분류(파트) 지정/해제 — 단순 UPDATE. 값 검증은 라우트에서."""
    connection.execute(
        "UPDATE workers SET category = ? WHERE id = ?", (category, worker_id)
    )


def rename(connection: sqlite3.Connection, worker_id: int, new_name: str) -> dict[str, Any]:
    """이름 변경(오타 정정용) — 과거 배합 기록의 작업자명도 함께 동기화.

    동기화하지 않으면 옛 기록이 옛 이름으로 남아 화면 연결이 끊기고,
    '기록 있는 이름 삭제 차단' 안전장치(has_blend_records)도 우회된다.
    반환: {old, new, records_updated}
    """
    clean = validate_name(new_name)
    row = connection.execute(
        "SELECT name FROM workers WHERE id = ?", (worker_id,)
    ).fetchone()
    if not row:
        raise ValueError("이용자를 찾을 수 없습니다.")
    old = row["name"]
    if old == clean:
        return {"old": old, "new": clean, "records_updated": 0}
    connection.execute("UPDATE workers SET name = ? WHERE id = ?", (clean, worker_id))
    try:
        cur = connection.execute(
            "UPDATE blend_records SET worker = ? WHERE worker = ?", (clean, old)
        )
        updated = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    except sqlite3.OperationalError:  # blend_records 없는 테스트 DB 등
        updated = 0
    return {"old": old, "new": clean, "records_updated": updated}


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
