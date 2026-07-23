import sqlite3
from typing import Iterable


def normalize_token(value: str) -> str:
    """자재/제품 이름 매칭용 정규화 토큰 — strip + upper 후 isalnum() 문자만 남긴다.

    주의: 파이썬에서 한글 음절은 `str.isalnum()`==True 라 **한글은 제거되지 않고 보존된다**
    (예: normalize_token('카본블랙') 은 빈 문자열이 아니다). 공백/괄호/기호만 걸러진다.
    따라서 한글 자재명끼리는 공백·기호 차이만 무시하고 그대로 비교되며, 한글 토큰이
    영문 토큰과 우연히 매칭되는 일은 없다. 향후 한글 정규화(자모/괄호 통일)를 도입하려면
    이 전제를 먼저 바로잡아야 한다.
    """
    return "".join(part for part in value.strip().upper() if part.isalnum())


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def in_clause(values: Iterable) -> str:
    return ", ".join("?" for _ in values)
