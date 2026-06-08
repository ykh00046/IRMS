import sqlite3
from collections.abc import Generator

from ..config import DATA_DIR, DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI dependency that provides one SQLite connection per request."""
    connection = get_connection()
    try:
        yield connection
    finally:
        connection.close()
