"""이력 시스템 도입 전 레시피들을 개정 계보로 소급 연결한다.

배경(2026-08-06): NPR → NPR-S2 → NPR-S 처럼 개정 이력 시스템 전에 이름을 바꿔가며
새 레시피로 등록해 온 흔적들이 독립 레시피로 서 있다. 연결고리(revision_of)만
이어주면 현황 가족 묶음·배합 자동 귀결·전체 Excel '개정 이력' 시트가 전부 자동으로
따라온다. 과거 배합 기록은 어떤 것도 변경되지 않는다.

규칙: 인자는 **오래된 것 → 새것 순서**의 제품명(또는 레시피 id). 연속한 쌍 (A구, B신)
마다 B 체인의 뿌리(root)의 revision_of 를 A 체인의 현재판(tip)에 잇는다 — B 가 이미
자체 개정 이력을 가져도 그 앞에 A 계보가 통째로 붙는 형태.

검증: 이름당 활성(비취소·비초안) 레시피 1개 유일해야 하고, B 뿌리는 아직 연결이
없어야 하며(revision_of NULL), 순환이 생기지 않아야 한다. 전부 통과해야만 쓴다.

사용(운영 PC):
  # 미리보기
  python tools/link_recipe_chain.py NPR NPR-S2 NPR-S
  # 실제 연결
  python tools/link_recipe_chain.py NPR NPR-S2 NPR-S --apply
IRMS_DATA_DIR(또는 --data-dir)가 운영 데이터 폴더를 가리켜야 한다.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def resolve_one(conn: sqlite3.Connection, token: str) -> sqlite3.Row:
    """이름(정확 일치) 또는 id 로 활성 레시피 1개를 찾는다. 모호하면 에러."""
    if token.isdigit():
        row = conn.execute(
            "SELECT id, product_name, status, revision_of FROM recipes WHERE id = ?",
            (int(token),),
        ).fetchone()
        if not row:
            raise SystemExit(f"오류: id {token} 레시피가 없다")
        return row
    rows = conn.execute(
        """
        SELECT id, product_name, status, revision_of FROM recipes
        WHERE product_name = ? AND status NOT IN ('canceled', 'draft')
          AND id NOT IN (
            WITH RECURSIVE descendants(ancestor, node) AS (
                SELECT revision_of, id FROM recipes WHERE revision_of IS NOT NULL
                UNION
                SELECT d.ancestor, r.id FROM recipes r JOIN descendants d ON r.revision_of = d.node
            )
            SELECT DISTINCT d.ancestor FROM descendants d
            JOIN recipes n ON n.id = d.node
            WHERE n.status NOT IN ('canceled', 'draft') AND d.ancestor IS NOT NULL
          )
        ORDER BY id
        """,
        (token,),
    ).fetchall()
    if not rows:
        raise SystemExit(f"오류: 활성 레시피 '{token}' 이 없다 (이름 정확 일치 기준)")
    if len(rows) > 1:
        cands = ", ".join(f"id={r['id']}" for r in rows)
        raise SystemExit(
            f"오류: '{token}' 활성 레시피가 여러 개다({cands}) — 이름 대신 id 로 지정하라"
        )
    return rows[0]


def chain_root(conn: sqlite3.Connection, rid: int) -> int:
    seen = set()
    cur = rid
    while True:
        if cur in seen:
            raise SystemExit(f"오류: id {rid} 위쪽 계보에 순환이 있다")
        seen.add(cur)
        row = conn.execute("SELECT revision_of FROM recipes WHERE id = ?", (cur,)).fetchone()
        if not row or row["revision_of"] is None:
            return cur
        cur = int(row["revision_of"])


def chain_tip(conn: sqlite3.Connection, rid: int) -> int:
    """자기 포함 하위 트리의 활성 최신본(resolve_chain_tip 과 같은 규칙)."""
    row = conn.execute(
        """
        WITH RECURSIVE subtree(node) AS (
            SELECT id FROM recipes WHERE id = ?
            UNION
            SELECT r.id FROM recipes r JOIN subtree s ON r.revision_of = s.node
        )
        SELECT id FROM recipes
        WHERE id IN (SELECT node FROM subtree)
          AND status NOT IN ('canceled', 'draft')
        ORDER BY id DESC LIMIT 1
        """,
        (rid,),
    ).fetchone()
    return int(row["id"]) if row else rid


def subtree_ids(conn: sqlite3.Connection, rid: int) -> set[int]:
    rows = conn.execute(
        """
        WITH RECURSIVE subtree(node) AS (
            SELECT id FROM recipes WHERE id = ?
            UNION
            SELECT r.id FROM recipes r JOIN subtree s ON r.revision_of = s.node
        )
        SELECT node FROM subtree
        """,
        (rid,),
    ).fetchall()
    return {int(r["node"]) for r in rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("lineage", nargs="+", help="오래된 것 → 새것 순서의 제품명(또는 id)")
    ap.add_argument("--data-dir", default=os.environ.get("IRMS_DATA_DIR", "data"))
    ap.add_argument("--apply", action="store_true", help="실제 연결(기본은 미리보기)")
    args = ap.parse_args()
    if len(args.lineage) < 2:
        print("계보는 2개 이상이어야 한다 (예: NPR NPR-S2 NPR-S)", file=sys.stderr)
        return 2

    db_path = Path(args.data_dir) / "irms.db"
    if not db_path.exists():
        print(f"DB 없음: {db_path}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    picked = [resolve_one(conn, t) for t in args.lineage]
    print("계보(구 → 신):")
    for r in picked:
        print(f"  id={r['id']} · {r['product_name']} · 상태 {r['status']}"
              f" · revision_of={r['revision_of']}")

    # 연속 쌍마다 연결 계획 수립 + 검증
    plans = []
    for older, newer in zip(picked, picked[1:]):
        a_tip = chain_tip(conn, chain_root(conn, older["id"]))
        b_root = chain_root(conn, newer["id"])
        b_root_row = conn.execute(
            "SELECT revision_of, product_name FROM recipes WHERE id = ?", (b_root,)
        ).fetchone()
        if b_root_row["revision_of"] is not None:
            raise SystemExit(
                f"오류: '{newer['product_name']}' 체인 뿌리(id={b_root})가 이미"
                f" id={b_root_row['revision_of']} 에 연결돼 있다"
            )
        # 순환 방지: A 의 하위 트리에 B 뿌리가 이미 있으면 안 되고, B 하위 트리에 a_tip 이 있으면 순환.
        if b_root in subtree_ids(conn, chain_root(conn, older["id"])):
            raise SystemExit("오류: 두 레시피가 이미 같은 체인에 있다")
        if a_tip in subtree_ids(conn, b_root):
            raise SystemExit("오류: 이 연결은 순환을 만든다")
        plans.append((b_root, b_root_row["product_name"], a_tip, older["product_name"]))

    print("\n연결 계획:")
    for b_root, b_name, a_tip, a_name in plans:
        print(f"  {b_name}(체인 뿌리 id={b_root}).revision_of ← {a_name}(현재판 id={a_tip})")

    if not args.apply:
        print("\n미리보기 — --apply 로 실제 연결")
        conn.close()
        return 0

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    for b_root, b_name, a_tip, a_name in plans:
        conn.execute(
            "UPDATE recipes SET revision_of = ? WHERE id = ?", (a_tip, b_root)
        )
        conn.execute(
            "INSERT INTO audit_logs (action, actor_username, actor_display_name,"
            " target_type, target_id, target_label, details_json, created_at)"
            " VALUES ('recipe_chain_linked', NULL, '도구 실행', 'recipe', ?, ?, ?, ?)",
            (
                str(b_root),
                b_name,
                json.dumps(
                    {"linked_to": a_tip, "linked_to_name": a_name,
                     "reason": "이력 시스템 도입 전 흔적 소급 연결"},
                    ensure_ascii=False,
                ),
                now,
            ),
        )
    conn.commit()

    # 연결 후 최종 tip 확인
    final_tip = chain_tip(conn, chain_root(conn, picked[0]["id"]))
    tip_row = conn.execute(
        "SELECT product_name FROM recipes WHERE id = ?", (final_tip,)
    ).fetchone()
    print(f"\n연결 완료 — 가족의 현재판: {tip_row['product_name']} (id={final_tip})")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
