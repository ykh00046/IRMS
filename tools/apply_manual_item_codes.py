"""수동 확정 품목코드 반영 — 자동 매칭(match_item_codes)이 못 푼 자재의 확정본.

2026-07-17 운영자 확정(리허설 미매칭 21종 검토 결과). 자동 매칭 --apply 후 실행한다.
기본은 보고만, --apply 로만 반영. 재실행 안전(이미 코드 있는 자재는 건너뜀).

확정 내용:
  - 자재별 품목코드 수동 부여(아래 CODE_FIXES — 마스터 실재 검증 완료, BT 계열 2건은
    마스터 4종 밖의 별도 코드 체계로 운영자 제공값 그대로).
  - 실수 중복 삭제(DELETE_TYPOS/DELETE_PLAIN): 오타·대문자 중복과 오등록('비고',
    단독 'GMMA')은 그냥 지운다 — 참조하던 레시피 항목·기록 링크만 정본으로 돌려놓고.
  - 색소류 비활성화(DEACTIVATE): 과거 이력이 실재하므로 삭제 대신 비활성.

보류(운영자 확인 대기 — 이 스크립트는 건드리지 않음):
  - PB-APB(정체 불명 — 어느 레시피가 쓰는지 운영에서 확인 후 결정)
"""

import argparse
import os
import sys

# Windows cp949 콘솔에서도 안전하게 출력
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.match_item_codes import _open_target_db  # noqa: E402

# 자재명(정확 일치) → 품목코드. 운영자 확정(2026-07-17).
CODE_FIXES = {
    "L-HEMA": "AS0031",        # L-HEMA (Lotte)
    "CH": "AC0029",            # Cyclohexanone 99%
    "EE": "AC0047",            # 2-ethoxy ethanol
    "SM": "AC0044",            # Styrene monomer
    "ME": "AC0046",            # 2-Mercaptoethanol
    "IM": "AS0047",            # isocyanatoethyl methacrylate
    "Vazo": "AW0013",          # Vazo56WSP
    "PU622": "AC0062",         # Miramer PU622
    "HA(1%)": "AW0019",        # HA/S(1%)
    "CS Pigment": "AC0024",    # Silicone Dioxide (안료 아님 — 실리카)
    "NVP": "AS0005",           # n-vinyl-2-pyrrolidone (C-NVP 와 다른 품목)
    "PMA": "AC0060",           # Propylene glycol monomethyl ether acetate
    "BMA": "AC0057",           # Butyl methacrylate
    "Glycerol": "AC0009",      # GLYCEROL(대문자 중복)은 비활성화로 통일
    "GMMA(코팅용)": "AW0031",  # 자동 매칭에서도 잡히지만 확정본에 명시(멱등)
    "메탄올": "BT000",         # 마스터 4종 밖 — 운영자 제공 코드
    "Oligomer": "BT0001",      # 마스터 4종 밖 — 운영자 제공 코드
    "C-HEMA": "AS0001",        # HEMA (Cognis) — 운영자 확정(2026-07-17)
    "PVP (K30P)": "AC0011",    # 이름 그대로 감 — 운영자 확정(2026-07-17)
    "PVP (K17P)": "AC0035",    # 이름 그대로 감(없으면 건너뜀)
    "PVP K90": "AW0027",       # K90 동일물 여러 이름이면 AW 가 정본(없으면 건너뜀)
}

# 사람 실수로 중복 등록된 자재 — 그냥 삭제한다(오기명 → 정본명).
# 삭제 전에 오기명을 참조하던 레시피 항목·배합 기록 링크만 정본 id 로 돌려놓는다
# (안 돌리면 FK 제약으로 삭제 자체가 거부됨. 기록의 이름·수치 텍스트는 불변).
DELETE_TYPOS = {
    "Dibutyltin dialurate": "Dibutyltin dilaurate",  # 오타 중복 → 정본(AS0052)
    "GLYCEROL": "Glycerol",                          # 대문자 중복 → 정본(AC0009)
}

# 정본 없이 그냥 지울 오등록. 참조가 남아 있으면 삭제 대신 비활성화로 처리하고 보고.
DELETE_PLAIN = [
    "비고",   # 임포트 실수로 자재화된 오염
    "GMMA",   # 잘못된 레시피용 이름(정품은 GMMA (Evonik)/GMMA(코팅용) 두 가지)
]

# 비활성화 대상(정확 일치). 과거 이력이 실재하므로 삭제하지 않는다.
DEACTIVATE = [
    "카본블랙", "BLACK", "RAVEN", "WHITE",   # 색소류 — 현행 배합 불사용
]


def run(db_arg: str | None, apply: bool) -> None:
    conn, db_label = _open_target_db(db_arg)
    print(f"[db] 대상: {db_label}")
    mode = "APPLY" if apply else "DRY-RUN(보고만)"
    print(f"[mode] {mode}")

    assigned = skipped_has_code = missing = conflict = 0
    for name, code in CODE_FIXES.items():
        row = conn.execute(
            "SELECT id, code, is_active FROM materials WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            print(f"  없음   : {name} (운영 DB 에 해당 이름 자재 없음 — 건너뜀)")
            missing += 1
            continue
        if row["code"]:
            print(f"  보유   : {name} = {row['code']} (이미 코드 있음 — 건너뜀)")
            skipped_has_code += 1
            continue
        dup = conn.execute(
            "SELECT name FROM materials WHERE code = ? AND id != ?", (code, row["id"])
        ).fetchone()
        if dup:
            print(f"  충돌   : {name} → {code} 는 이미 '{dup['name']}' 에 부여됨 — 수동 확인 필요")
            conflict += 1
            continue
        print(f"  부여   : {name} → {code}")
        if apply:
            conn.execute("UPDATE materials SET code = ? WHERE id = ?", (code, row["id"]))
        assigned += 1

    # 실수 중복 삭제 — 참조를 정본으로 돌려놓고 오기명을 지운다
    deleted = 0
    for wrong, right in DELETE_TYPOS.items():
        w = conn.execute("SELECT id FROM materials WHERE name = ?", (wrong,)).fetchone()
        if w is None:
            continue  # 이미 지워졌거나 없음
        r = conn.execute("SELECT id FROM materials WHERE name = ?", (right,)).fetchone()
        if r is None:
            print(f"  삭제불가: {wrong} (정본 '{right}' 이 DB 에 없음 — 수동 확인)")
            continue
        refs = conn.execute(
            "SELECT COUNT(*) FROM recipe_items WHERE material_id = ?", (w["id"],)
        ).fetchone()[0]
        print(f"  삭제   : {wrong} → 정본 '{right}' (레시피 항목 {refs}건 정본으로 연결)")
        if apply:
            conn.execute(
                "UPDATE recipe_items SET material_id = ? WHERE material_id = ?",
                (r["id"], w["id"]),
            )
            conn.execute(
                "UPDATE blend_details SET material_id = ? WHERE material_id = ?",
                (r["id"], w["id"]),
            )
            conn.execute("DELETE FROM materials WHERE id = ?", (w["id"],))
        deleted += 1

    for name in DELETE_PLAIN:
        row = conn.execute("SELECT id, is_active FROM materials WHERE name = ?", (name,)).fetchone()
        if row is None:
            continue
        refs = conn.execute(
            "SELECT COUNT(*) FROM recipe_items WHERE material_id = ?", (row["id"],)
        ).fetchone()[0]
        if refs:
            # 레시피가 아직 참조 중 — 지우면 레시피가 깨지므로 비활성화로 대체하고 보고
            print(f"  삭제보류: {name} — 레시피 항목 {refs}건이 참조 중, 비활성화로 대체(해당 레시피 정리 후 재실행)")
            if apply and row["is_active"]:
                conn.execute("UPDATE materials SET is_active = 0 WHERE id = ?", (row["id"],))
            continue
        print(f"  삭제   : {name}")
        if apply:
            conn.execute("UPDATE blend_details SET material_id = NULL WHERE material_id = ?", (row["id"],))
            conn.execute("DELETE FROM materials WHERE id = ?", (row["id"],))
        deleted += 1

    deactivated = 0
    for name in DEACTIVATE:
        row = conn.execute(
            "SELECT id, is_active FROM materials WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            continue
        if not row["is_active"]:
            continue
        print(f"  비활성 : {name}")
        if apply:
            conn.execute("UPDATE materials SET is_active = 0 WHERE id = ?", (row["id"],))
        deactivated += 1

    if apply:
        conn.commit()
    print(f"[요약] 코드 부여 {assigned} · 이미 보유 {skipped_has_code} · 없음 {missing} "
          f"· 충돌 {conflict} · 삭제 {deleted} · 비활성화 {deactivated}"
          + ("" if apply else "  [DRY-RUN — 변경 없음]"))
    conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="수동 확정 품목코드 반영(운영자 확정본)")
    ap.add_argument("--db", default=None, help="대상 DB 경로(기본: 관례 DB)")
    ap.add_argument("--apply", action="store_true", help="실제 반영(기본은 보고만)")
    args = ap.parse_args()
    run(args.db, args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
