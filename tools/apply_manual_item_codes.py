"""수동 확정 품목코드 반영 — 자동 매칭(match_item_codes)이 못 푼 자재의 확정본.

2026-07-17 운영자 확정(리허설 미매칭 21종 검토 결과). 자동 매칭 --apply 후 실행한다.
기본은 보고만, --apply 로만 반영. 재실행 안전(이미 코드 있는 자재는 건너뜀).

확정 내용:
  - 자재별 품목코드 수동 부여(아래 CODE_FIXES — 마스터 실재 검증 완료, BT 계열 2건은
    마스터 4종 밖의 별도 코드 체계로 운영자 제공값 그대로).
  - 폐기 자재 비활성화(DEACTIVATE): 안료류(카본블랙/BLACK/RAVEN/WHITE — 잉크 흔적,
    현행 배합에 불사용), 오등록('비고'), 중복('GLYCEROL'=Glycerol 중복,
    'Dibutyltin dialurate'=dilaurate 오타 중복, 'GMMA'=잘못된 레시피용 이름).
    비활성화는 배합 화면 자동완성에서만 빠지고 기존 기록·레시피 참조는 보존된다.

보류(운영자 확인 대기 — 이 스크립트는 건드리지 않음):
  - PB-APB(정체 불명 — 어느 레시피가 쓰는지 확인 후 결정)
  - C-HEMA(HEMA(Cognis)=AS0001 로 추정되나 확인 전)
  - PVP (K30P)(레시피 정합 확인 후 K90(AW0027) 정정 여부 결정)
"""

import argparse
import os
import sys

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
}

# 비활성화 대상(정확 일치). 삭제하지 않는다 — 기록·레시피 참조 보존.
DEACTIVATE = [
    "카본블랙", "BLACK", "RAVEN", "WHITE",   # 안료 — 잉크 흔적, 현행 배합 불사용
    "비고",                                    # 임포트 실수로 자재화된 오염
    "GLYCEROL",                                # Glycerol(AC0009) 의 대문자 중복
    "Dibutyltin dialurate",                    # dilaurate(AS0052) 의 오타 중복
    "GMMA",                                    # 잘못된 레시피용 이름(정품은 GMMA (Evonik)/GMMA(코팅용))
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
          f"· 충돌 {conflict} · 비활성화 {deactivated}"
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
