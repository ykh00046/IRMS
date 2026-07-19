"""앱 전역 설정(app_settings) 읽기/쓰기 서비스 헬퍼.

키-값(TEXT) 저장소. 시드 없음 — 행 부재가 기본값을 의미한다.
구버전(app_settings 테이블이 없는) DB 는 OperationalError 를 잡아 기본값으로
폴백한다(recipe_tolerance_g 패턴). 프런트가 죽으면 안 되기 때문.

scale-only-mode 설정값("scale_only_input") 규약:
  - "1" = 켜짐(true), 그 외/행 없음 = 꺼짐(false). bool ↔ "1"/"0" 변환은 이 모듈이
    단일 소스로 담당한다.
"""

import sqlite3

from ..db.time_utils import utc_now_text

SCALE_ONLY_INPUT_KEY = "scale_only_input"


def _table_exists(connection: sqlite3.Connection) -> bool:
    """app_settings 테이블 존재 여부. 구버전 DB 방어."""
    try:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_settings'"
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def get_setting(
    connection: sqlite3.Connection, key: str, default: str | None = None
) -> str | None:
    """app_settings 에서 key 조회. 행이 없거나 테이블이 없으면 default 반환."""
    if not _table_exists(connection):
        return default
    try:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
    except sqlite3.OperationalError:  # 구버전/테스트 DB — 방어적 폴백
        return default
    if row is None:
        return default
    try:
        return row["value"]
    except (IndexError, KeyError):
        return row[0]


def set_setting(
    connection: sqlite3.Connection,
    key: str,
    value: str,
    updated_by: str | None = None,
) -> None:
    """app_settings upsert. 테이블이 없으면 아무것도 하지 않는다(방어적 폴백)."""
    if not _table_exists(connection):
        return
    connection.execute(
        """
        INSERT INTO app_settings (key, value, updated_at, updated_by)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at,
            updated_by = excluded.updated_by
        """,
        (key, value, utc_now_text(), updated_by),
    )


def get_scale_only_input(connection: sqlite3.Connection) -> bool:
    """저울 전용 입력 모드 활성화 여부. 행 없음/구버전/비-"1" → False."""
    raw = get_setting(connection, SCALE_ONLY_INPUT_KEY)
    return raw == "1"


def set_scale_only_input(
    connection: sqlite3.Connection,
    enabled: bool,
    updated_by: str | None = None,
) -> None:
    """저울 전용 입력 모드 저장. bool → "1"/"0"."""
    set_setting(
        connection,
        SCALE_ONLY_INPUT_KEY,
        "1" if enabled else "0",
        updated_by=updated_by,
    )
