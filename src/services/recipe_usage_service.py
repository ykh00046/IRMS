"""양산대응 배합 사용량 — Recipe-Intake 일일 장부 수신·집계.

메신저 'C,S관 양산대응' 방의 레시피 이미지에서 Recipe-Intake 가 일별·자재별 실제 사용량(g)을
만들어 보낸다. 이 배합은 IRMS 배합 기록(blend_records)에 없으므로 별도 테이블에 둔다.
반제품제조 불출량(material_usage_periods)과 섞지 않는다 — 재고 대시보드는 출고 유형을 따로 받는다.

- replace_snapshot: (source, first_day~through) 를 한 트랜잭션에서 지우고 통째로 넣는다(멱등).
- usage_periods:    기간·그룹(total/day/month)·종류(A 원재료 / B 반제품 / all) 집계.
- import_status:    마지막 수신 — 대시보드가 '오래된 숫자' 경고에 쓴다.
"""

import json
import sqlite3
from typing import Any

from ..db import utc_now_text

DEFAULT_SOURCE = "recipe-intake"


def replace_snapshot(
    connection: sqlite3.Connection,
    *,
    source: str,
    first_day: str,
    through: str,
    computed_at: str | None,
    rows: list[dict[str, Any]],
    client_ip: str | None,
) -> dict[str, Any]:
    """rows: {day, material, item_code, used_g, batch_g, lines, images} — 검증은 라우터(Pydantic)가 끝낸 상태."""
    codes = sorted({r["item_code"] for r in rows if r.get("item_code")})
    known: set[str] = set()
    if codes:
        marks = ",".join("?" * len(codes))
        known = {
            row["code"]
            for row in connection.execute(
                f"SELECT code FROM item_code_master WHERE code IN ({marks})", codes
            )
        }
    unknown = [c for c in codes if c not in known]
    total = round(sum(r["used_g"] for r in rows), 3)
    with connection:
        cur = connection.execute(
            """
            INSERT INTO recipe_usage_imports
                (source, first_day, through, computed_at, received_at, row_count, total_g, unknown_codes, client_ip)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source, first_day, through, computed_at, utc_now_text(), len(rows), total,
             json.dumps(unknown, ensure_ascii=False), client_ip),
        )
        import_id = cur.lastrowid
        replaced = connection.execute(
            "DELETE FROM recipe_usage_daily WHERE source = ? AND day BETWEEN ? AND ?",
            (source, first_day, through),
        ).rowcount
        connection.execute("UPDATE recipe_usage_imports SET replaced = ? WHERE id = ?", (replaced, import_id))
        connection.executemany(
            """
            INSERT INTO recipe_usage_daily
                (source, day, material_name, item_code, used_g, batch_g, lines, images, import_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (source, r["day"], r["material"], r.get("item_code") or None, r["used_g"],
                 r.get("batch_g") or 0, r.get("lines") or 0, r.get("images") or 0, import_id)
                for r in rows
            ],
        )
    return {
        "import_id": import_id,
        "source": source,
        "first_day": first_day,
        "through": through,
        "row_count": len(rows),
        "replaced": replaced,
        "total_g": total,
        "unknown_codes": unknown,
    }


def usage_periods(
    connection: sqlite3.Connection,
    *,
    start_date: str,
    end_date: str,
    group: str = "total",
    kind: str = "all",
    source: str = DEFAULT_SOURCE,
) -> dict[str, Any]:
    """기간 내 자재별 사용량(g). 같은 코드에 이름이 여럿(BLACK·BLACK FW-200 → AC0018)이면 이름별 행으로
    두고, 코드 단위 합산은 받는 쪽이 한다(재고 대시보드는 item_code 로 묶는다).

    kind: A = 코드가 A 로 시작(원재료·안료, 재고 차감 대상) / B = B 로 시작(반제품) / all = 코드 없음 포함."""
    period_expr = {"total": "NULL", "day": "day", "month": "substr(day, 1, 7)"}[group]
    kind_sql = {"A": "AND item_code LIKE 'A%'", "B": "AND item_code LIKE 'B%'", "all": ""}[kind]
    rows = connection.execute(
        f"""
        SELECT {period_expr} AS period, item_code, material_name,
               ROUND(SUM(used_g), 3) AS used_g, SUM(lines) AS lines
        FROM recipe_usage_daily
        WHERE source = ? AND day BETWEEN ? AND ? {kind_sql}
        GROUP BY period, item_code, material_name
        ORDER BY period, used_g DESC
        """,
        (source, start_date, end_date),
    ).fetchall()
    items = [dict(r) for r in rows]
    return {
        "source": source,
        "start_date": start_date,
        "end_date": end_date,
        "group": group,
        "kind": kind,
        "unit": "g",
        "total_g": round(sum(i["used_g"] for i in items), 3),
        "items": items,
        "status": import_status(connection, source=source),
    }


def import_status(connection: sqlite3.Connection, *, source: str = DEFAULT_SOURCE) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, first_day, through, computed_at, received_at, row_count, replaced, total_g, unknown_codes
        FROM recipe_usage_imports WHERE source = ? ORDER BY id DESC LIMIT 1
        """,
        (source,),
    ).fetchone()
    if row is None:
        return {"source": source, "received": False}
    out = dict(row)
    out["unknown_codes"] = json.loads(out["unknown_codes"] or "[]")
    out["received"] = True
    return out
