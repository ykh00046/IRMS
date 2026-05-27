import sqlite3
from typing import Iterable


def normalize_token(value: str) -> str:
    return "".join(part for part in value.strip().upper() if part.isalnum())


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def in_clause(values: Iterable) -> str:
    return ", ".join("?" for _ in values)
