import sqlite3

from ..security import hash_password
from .time_utils import utc_now_text


def seed_workers(connection: sqlite3.Connection) -> None:
    """사용자 이름을 작업자 명단(workers)에 동기화 (시드 이후 호출). 멱등."""
    now = utc_now_text()
    for row in connection.execute(
        "SELECT DISTINCT display_name FROM users "
        "WHERE display_name IS NOT NULL AND TRIM(display_name) != ''"
    ).fetchall():
        connection.execute(
            "INSERT OR IGNORE INTO workers (name, is_active, created_at) VALUES (?, 1, ?)",
            (row["display_name"].strip(), now),
        )


def seed_users(connection: sqlite3.Connection) -> None:
    seeds = [
        # 책임자
        ("120206", "120206", "함지안", "user", "manager"),
        ("160228", "160228", "김지훈", "user", "manager"),
        ("130801", "130801", "김진우", "user", "manager"),
        ("160501", "160501", "김규철", "user", "manager"),
        ("220314", "220314", "이광준", "user", "manager"),
        ("220316", "220316", "강도윤", "user", "manager"),
        ("190308", "190308", "문동식", "user", "manager"),
        ("240212", "240212", "김성근", "user", "manager"),
        ("250411", "250411", "민윤정", "user", "manager"),
        ("250612", "250612", "이시현", "user", "manager"),
        ("250731", "250731", "김태균", "user", "manager"),
        # 담당자
        ("221023", "221023", "김도현", "user", "operator"),
        ("240909", "240909", "김민준", "user", "operator"),
        ("240910", "240910", "박효빈", "user", "operator"),
        ("250941", "250941", "한가람", "user", "operator"),
        ("251006", "251006", "김용범", "user", "operator"),
        ("251051", "251051", "배정한", "user", "operator"),
        ("251066", "251066", "설영훈", "user", "operator"),
        ("251110", "251110", "최선미", "user", "operator"),
        ("251155", "251155", "소보섭", "user", "operator"),
        ("260152", "260152", "권효성", "user", "operator"),
        ("260226", "260226", "김상욱", "user", "operator"),
    ]

    for username, password, display_name, role, access_level in seeds:
        existing = connection.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if existing:
            continue

        connection.execute(
            """
            INSERT INTO users (username, password_hash, display_name, role, access_level, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (username, hash_password(password), display_name, role, access_level, utc_now_text()),
        )

    connection.commit()
