"""과거 배합 기록의 자재 표기 통일 — 같은 원재료가 여러 이름으로 남은 것을 하나로.

왜 필요한가
-----------
지금 레시피는 이미 한 이름으로 통일돼 있다(예: PMA). 갈라진 표기는 옛 시스템의
실적서를 옮겨 담을 때 그쪽 표기가 그대로 따라 들어온 과거 기록에만 남아 있다
(예: 'Propylene glycol monomethyl etheracetate'). 그 결과:

  · 자재 사용량 API 가 같은 원재료를 여러 줄로 내보낸다.
  · 기록 당시 저장된 material_code 도 제각각이라('' · 'AC0060' · '기타')
    이름이 같아도 줄이 또 갈린다 — '기타' 는 옛 분류값이 코드 자리에 들어간 것.

이 도구는 그 세 가지(material_name · material_id · material_code)만 마스터 기준으로
맞춘다. **나머지는 어떤 것도 건드리지 않는다** — 작업일·작업시간·작성시각·수량
(이론량·실제량·비율)·자재 LOT·제품 LOT·작업자·서명·수기입력/이월 플래그 모두 그대로다.
UPDATE 문에 그 컬럼들이 아예 등장하지 않는다.

무엇을 무엇으로 바꾸는가
------------------------
material_aliases(동의어) 를 매핑표로 쓴다. 화면 '자재 관리 > 품목코드' 에서 사람이
눈으로 확인하고 등록한 것이라, 도구에 이름 쌍을 직접 타이핑하는 것보다 안전하다.
따라서 순서는 **동의어 등록 → 이 도구** 다.

사용(운영 PC)
-------------
  # 1) 미리보기 — 아무것도 쓰지 않는다. 무엇이 바뀔지 표로 보여준다.
  python tools/unify_material_names.py

  # 2) 실제 반영 — 실행 직전 DB 를 자동 백업한다.
  python tools/unify_material_names.py --apply

IRMS_DATA_DIR 환경변수(또는 --data-dir)가 운영 데이터 폴더를 가리켜야 한다.
서버를 멈출 필요는 없다(짧은 단일 트랜잭션).
되돌리려면 실행 시 만들어진 backups/ 의 파일을 data/irms.db 로 되돌리면 된다.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

MARKER = "자재 표기 통일"


def _norm(value: str | None) -> str:
    """자재명 매칭용 정규화 — 대문자화 후 영숫자·한글만. src/db/queries.normalize_token 과 동일 규칙.

    이 도구는 운영 PC 에서 sqlite3 만으로 도는 단독 스크립트라 src 패키지를 쓰지 않는다.
    """
    return "".join(ch for ch in (value or "").strip().upper() if ch.isalnum())


def _plan(conn: sqlite3.Connection) -> list[dict]:
    """바꿀 내역을 만든다. 실제 쓰기는 하지 않는다.

    반환 각 항목: 지금 값(old_*) 과 바꿀 값(new_*), 해당 행 수·무게.
    """
    materials = {
        int(r["id"]): {"name": r["name"], "code": (r["code"] or "").strip()}
        for r in conn.execute("SELECT id, name, code FROM materials")
    }
    # 정규화 이름 → material_id (자재명이 우선, 그 다음 동의어)
    index: dict[str, int] = {}
    for mid, m in materials.items():
        key = _norm(m["name"])
        if key:
            index[key] = mid
    for r in conn.execute("SELECT material_id, alias_name FROM material_aliases"):
        key = _norm(r["alias_name"])
        if key and key not in index:
            index[key] = int(r["material_id"])

    rows = conn.execute(
        """
        SELECT bd.material_name AS name,
               COALESCE(bd.material_code, '') AS code,
               bd.material_id AS mid,
               COUNT(*) AS n,
               ROUND(SUM(bd.actual_amount), 1) AS g
        FROM blend_details bd
        GROUP BY bd.material_name, COALESCE(bd.material_code, ''), bd.material_id
        ORDER BY bd.material_name
        """
    ).fetchall()

    plan: list[dict] = []
    for r in rows:
        target_id = index.get(_norm(r["name"]))
        if target_id is None:
            continue  # 마스터에도 동의어에도 없는 이름 — 손대지 않는다.
        target = materials[target_id]
        same_name = (r["name"] or "") == target["name"]
        same_code = (r["code"] or "") == target["code"]
        same_id = r["mid"] is not None and int(r["mid"]) == target_id
        if same_name and same_code and same_id:
            continue  # 이미 정리된 행
        plan.append({
            "old_name": r["name"], "old_code": r["code"], "old_mid": r["mid"],
            "new_name": target["name"], "new_code": target["code"], "new_mid": target_id,
            "rows": int(r["n"]), "grams": float(r["g"] or 0),
        })
    return plan


def _backup(db_path: Path) -> Path:
    """실행 직전 스냅샷. SQLite 온라인 백업이라 서버가 떠 있어도 안전하다."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_dir = db_path.parent.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"irms_before_unify_{stamp}.db"
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.environ.get("IRMS_DATA_DIR", "data"))
    ap.add_argument("--apply", action="store_true", help="실제 반영(기본은 미리보기)")
    ap.add_argument("--actor", default="운영자", help="감사 로그에 남길 실행자 이름")
    args = ap.parse_args()

    db_path = Path(args.data_dir) / "irms.db"
    if not db_path.exists():
        print(f"DB 없음: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    plan = _plan(conn)

    if not plan:
        print("바꿀 것이 없습니다 - 모든 자재 표기가 이미 마스터와 일치합니다.")
        conn.close()
        return 0

    print(f"{'[반영]' if args.apply else '[미리보기]'} 자재 표기 통일 · {len(plan)}종\n")
    head = f"{'지금 이름':<42} {'코드':<8} {'→ 바꿀 이름':<24} {'코드':<8} {'행':>5} {'무게(g)':>12}"
    print(head)
    print("-" * len(head))
    total_rows = total_g = 0
    for p in plan:
        old_code = p["old_code"] or "-"
        new_code = p["new_code"] or "-"
        print(f"{p['old_name']:<42} {old_code:<8} → {p['new_name']:<24} {new_code:<8} "
              f"{p['rows']:>5} {p['grams']:>12,.1f}")
        total_rows += p["rows"]
        total_g += p["grams"]
    print("-" * len(head))
    print(f"{'합계':<77} {total_rows:>5} {total_g:>12,.1f}")

    print("\n바뀌는 것: 자재명 · 자재 연결(material_id) · 기록 당시 자재코드 - 이 셋뿐입니다.")
    print("그대로인 것: 작업일 · 작업시간 · 수량(이론량/실제량/비율) · 자재 LOT · 제품 LOT"
          " · 작업자 · 서명 · 수기입력/이월 표시")

    if not args.apply:
        print("\n실제로 반영하려면 --apply 를 붙여 다시 실행하세요.")
        conn.close()
        return 0

    backup = _backup(db_path)
    print(f"\n백업 생성: {backup}")

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    changed = 0
    try:
        for p in plan:
            # material_name / material_id / material_code 만 UPDATE. 다른 컬럼은
            # 이 문장에 등장조차 하지 않는다(수량·일시 보존의 근거).
            cur = conn.execute(
                "UPDATE blend_details "
                "SET material_name = ?, material_id = ?, material_code = ? "
                "WHERE material_name = ? AND COALESCE(material_code, '') = ? "
                "  AND material_id IS ?",
                (p["new_name"], p["new_mid"], p["new_code"],
                 p["old_name"], p["old_code"], p["old_mid"]),
            )
            changed += cur.rowcount

        conn.execute(
            "INSERT INTO audit_logs (action, actor_username, actor_display_name, "
            "target_type, target_label, details_json, created_at) "
            "VALUES (?, ?, ?, 'blend_details', ?, ?, ?)",
            (
                "material_names_unified", args.actor, args.actor,
                MARKER,
                json.dumps({
                    "changed_rows": changed,
                    "groups": [
                        {"from": p["old_name"], "from_code": p["old_code"],
                         "to": p["new_name"], "to_code": p["new_code"],
                         "rows": p["rows"]}
                        for p in plan
                    ],
                    "backup": backup.name,
                }, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        print("\n오류로 되돌렸습니다(변경 없음).", file=sys.stderr)
        raise
    finally:
        conn.close()

    print(f"반영 완료: {changed}행")
    print("되돌리려면 서버를 멈추고 위 백업 파일을 data/irms.db 로 복사하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
