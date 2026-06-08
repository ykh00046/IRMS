"""IRMS DB 패키지.

분리 이력: 2026-05-27, `src/database.py` (719 LOC) → 7개 모듈
- time_utils: ISO 시각 헬퍼
- connection: SQLite 연결 팩토리
- queries: 공통 SQL 유틸 (normalize_token, row_to_dict, in_clause)
- schema: 테이블/인덱스 정의 + init_db 진입점
- migrations: ALTER/idempotent 마이그레이션 + 고아 테이블 DROP
- seeds: 데모 데이터 시드
- audit: 감사 로그 쓰기/조회
"""

from .audit import list_audit_logs, write_audit_log
from .connection import get_connection, get_db
from .migrations import (
    apply_schema_migrations,
    ensure_column,
    has_migration,
    record_migration,
    standardize_recipe_units_to_grams,
)
from .queries import in_clause, normalize_token, row_to_dict
from .schema import init_db
from .seeds import seed_chat_rooms, seed_materials, seed_recipes, seed_users
from .time_utils import utc_cutoff_text, utc_now_text

__all__ = [
    "apply_schema_migrations",
    "ensure_column",
    "get_connection",
    "get_db",
    "has_migration",
    "in_clause",
    "init_db",
    "list_audit_logs",
    "normalize_token",
    "record_migration",
    "row_to_dict",
    "seed_chat_rooms",
    "seed_materials",
    "seed_recipes",
    "seed_users",
    "standardize_recipe_units_to_grams",
    "utc_cutoff_text",
    "utc_now_text",
    "write_audit_log",
]
