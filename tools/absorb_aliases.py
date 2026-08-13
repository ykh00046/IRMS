"""남은 자재 동의어를 일괄 흡수하고 삭제하는 정리 도구.

코드 중심·이름 하나 원칙(2026-08-13 사용자 결정): 동의어는 영구 다리가 아니라
정리 대상이다. 이 도구는 material_aliases 의 각 동의어에 대해

  1. 그 표기로 남은 배합 기록(blend_details)의 이름을 소유 자재의 정본 이름으로
     고쳐 쓰고 끊긴 FK(material_id)를 연결한 뒤   (= 화면 [기록 흡수]와 같은 규칙)
  2. 동의어를 삭제한다.

대부분의 동의어는 구 이관이 넣은 품목코드 값(AC0060 등)이라 흡수 대상 기록이
0행이고, 그냥 삭제만 된다. 다른 자재에 FK 로 연결된 행은 이름이 같아도 절대
빼앗지 않는다.

안전장치:
  - 기본은 미리보기. --apply 때만 변경하며, 실행 직전 SQLite 온라인 백업을 뜬다.
  - 정규화(normalize_token) 기준으로 다른 자재의 이름 또는 다른 자재의 동의어와
    충돌하는 동의어는 건드리지 않고 보고만 한다(사람 판단 필요).
  - 전 과정이 한 트랜잭션 - 오류 시 전부 되돌린다. audit_logs 에 요약을 남긴다.

사용 (운영 PC, 저장소 루트):
  python tools/absorb_aliases.py              # 미리보기
  python tools/absorb_aliases.py --apply      # 실제 반영(백업 자동)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.queries import normalize_token  # noqa: E402  (sys.path 조정 뒤 import)


def _backup(db_path: Path) -> Path:
    """실행 직전 스냅샷. SQLite 온라인 백업이라 서버가 떠 있어도 안전하다."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_dir = db_path.parent.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"irms_before_absorb_{stamp}.db"
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def build_plan(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """동의어별 처리 계획. 반환: (plan, conflicts).

    plan 항목: {alias_id, alias_name, material_id, material_name, rows(흡수 대상 행 수),
               record_names(흡수될 기록 표기 목록)}
    conflicts 항목: {alias_name, owner, reason} - 건드리지 않는다.
    """
    materials = conn.execute("SELECT id, name FROM materials").fetchall()
    name_key_to = {}
    for m in materials:
        key = normalize_token(m["name"] or "")
        if key:
            name_key_to.setdefault(key, []).append(m)

    aliases = conn.execute(
        "SELECT a.id, a.alias_name, a.material_id, m.name AS owner "
        "FROM material_aliases a JOIN materials m ON m.id = a.material_id "
        "ORDER BY m.name, a.alias_name"
    ).fetchall()

    # 동의어 정규화 키 → 소유 자재 목록(서로 다른 자재의 동의어가 같은 키면 충돌).
    alias_key_owners: dict[str, set[int]] = {}
    for a in aliases:
        key = normalize_token(a["alias_name"] or "")
        if key:
            alias_key_owners.setdefault(key, set()).add(int(a["material_id"]))

    plan: list[dict] = []
    conflicts: list[dict] = []
    for a in aliases:
        key = normalize_token(a["alias_name"] or "")
        owner_id = int(a["material_id"])
        if not key:
            # 기호만 남는 동의어 - 어떤 기록과도 매칭 불가. 삭제만 한다(흡수 0행).
            plan.append({
                "alias_id": int(a["id"]), "alias_name": a["alias_name"],
                "material_id": owner_id, "material_name": a["owner"],
                "rows": 0, "record_names": [],
            })
            continue
        # 다른 자재의 이름과 같은 키 - 소유가 갈린다. 사람 판단으로 넘긴다.
        other_names = [m for m in name_key_to.get(key, []) if int(m["id"]) != owner_id]
        if other_names:
            conflicts.append({
                "alias_name": a["alias_name"], "owner": a["owner"],
                "reason": f"다른 자재 '{other_names[0]['name']}' 의 이름과 같음",
            })
            continue
        # 서로 다른 자재의 동의어가 같은 키 - 어느 쪽으로 흡수할지 사람이 정해야 한다.
        if len(alias_key_owners.get(key, set())) > 1:
            conflicts.append({
                "alias_name": a["alias_name"], "owner": a["owner"],
                "reason": "같은 표기가 여러 자재의 동의어",
            })
            continue

        owner_name = a["owner"] or ""
        owner_key = normalize_token(owner_name)
        rows = 0
        record_names: list[str] = []
        for r in conn.execute(
            "SELECT material_name, COUNT(*) AS n FROM blend_details "
            "WHERE material_name IS NOT NULL "
            "  AND (material_id IS NULL OR material_id = ?) "
            "GROUP BY material_name",
            (owner_id,),
        ).fetchall():
            rec_name = r["material_name"]
            if rec_name == owner_name:
                continue
            if normalize_token(rec_name or "") != key:
                continue
            # 표기가 정본과 같은 키인 동의어(대소문자 차이 등)는 owner_key == key 로
            # 여기 올 수 있다 - 그래도 정본 표기로 고치는 것이 맞으므로 그대로 진행.
            _ = owner_key
            rows += int(r["n"])
            record_names.append(rec_name)
        plan.append({
            "alias_id": int(a["id"]), "alias_name": a["alias_name"],
            "material_id": owner_id, "material_name": owner_name,
            "rows": rows, "record_names": record_names,
        })
    return plan, conflicts


def apply_plan(conn: sqlite3.Connection, plan: list[dict], *, actor: str,
               backup_name: str) -> int:
    """계획 반영(한 트랜잭션). 반환: 고친 기록 행 수 합계."""
    now = datetime.now().isoformat(timespec="seconds")
    changed = 0
    for p in plan:
        for rec_name in p["record_names"]:
            cur = conn.execute(
                "UPDATE blend_details SET material_name = ?, material_id = ? "
                "WHERE material_name = ? AND (material_id IS NULL OR material_id = ?)",
                (p["material_name"], p["material_id"], rec_name, p["material_id"]),
            )
            changed += cur.rowcount or 0
        conn.execute("DELETE FROM material_aliases WHERE id = ?", (p["alias_id"],))
    conn.execute(
        "INSERT INTO audit_logs (action, actor_username, actor_display_name, "
        "target_type, target_label, details_json, created_at) "
        "VALUES (?, ?, ?, 'material_aliases', ?, ?, ?)",
        (
            "aliases_absorbed", actor, actor,
            "동의어 일괄 흡수",
            json.dumps({
                "aliases_removed": len(plan),
                "records_renamed": changed,
                "backup": backup_name,
            }, ensure_ascii=False),
            now,
        ),
    )
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="남은 자재 동의어를 일괄 흡수(기록 통합)하고 삭제한다."
    )
    ap.add_argument("--data-dir", default=os.environ.get("IRMS_DATA_DIR", "data"))
    ap.add_argument("--apply", action="store_true", help="실제 반영(기본은 미리보기)")
    ap.add_argument("--actor", default="운영자", help="감사 로그에 남길 실행자 이름")
    args = ap.parse_args(argv)

    db_path = Path(args.data_dir) / "irms.db"
    if not db_path.exists():
        print(f"DB 없음: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        plan, conflicts = build_plan(conn)
        absorb = [p for p in plan if p["rows"]]
        plain = [p for p in plan if not p["rows"]]
        print(f"동의어 {len(plan)}건 처리 예정 "
              f"(기록 흡수 동반 {len(absorb)}건 / 삭제만 {len(plain)}건), "
              f"충돌 보류 {len(conflicts)}건")
        for p in absorb:
            names = ", ".join(p["record_names"])
            print(f"  [흡수] '{names}' {p['rows']}행 -> {p['material_name']} "
                  f"(동의어 '{p['alias_name']}' 삭제)")
        if conflicts:
            print("충돌 - 건드리지 않음(사람 판단 필요):")
            for c in conflicts:
                print(f"  [보류] '{c['alias_name']}' (소유 {c['owner']}): {c['reason']}")

        if not args.apply:
            print("\n[미리보기] 변경 없음 - 반영하려면 --apply 를 붙이세요.")
            return 0
        if not plan:
            print("처리할 동의어가 없습니다.")
            return 0

        backup = _backup(db_path)
        print(f"백업: {backup}")
        try:
            changed = apply_plan(conn, plan, actor=args.actor, backup_name=backup.name)
            conn.commit()
        except Exception:
            conn.rollback()
            print("\n오류로 되돌렸습니다(변경 없음).", file=sys.stderr)
            raise
        print(f"완료: 동의어 {len(plan)}건 삭제, 기록 {changed}행 이름 통일.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
