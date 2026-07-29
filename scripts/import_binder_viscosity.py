"""바인더 점도 기록 → viscosity_readings 임포트 (멱등).

「바인더 기록 (자동 저장됨).xlsx」의 연도별 시트(24/25/26년도)를 읽어, 바인더
종류(APB/CSPB/APB17 등)를 점도 관리 반제품으로 등록하고 각 점도를 저장한다.

핵심: 바인더는 우리 PB/SBCT/SCRA 와 무관한 별개 반제품이다. 다만 각 바인더를 만들
때 쓴 **사용한 PB LOT** 을 material_lot 에 함께 저장해, 나중에 그 PB 의 점도(우리 PB
반제품 데이터)와 연계해 추세를 볼 수 있게 한다("48cp PB 로 만든 CSPB 는 80, 51cp 는
90" 같은 상관 분석).

  · product   = 바인더 종류(정규화 후) — viscosity_products 에 없으면 자동 등록
  · lot_no    = 사용한PB (8자리, 고유) → (product_id, lot_no) UNIQUE 로 재임포트 멱등
  · material_lot = 사용한PB (PB 연계 키)
  · viscosity = 점도값
  · measured_date = 일자 (시트 연도로 보정)

바인더 종류 정규화(사용자 확정 2026-07-29):
  APB(17)→APB17 · CSBP→CSPB(오타) · 괄호숫자 제거(APB(1)→APB) ·
  PM/PM17/HSPU 유지 · APB(TEST) 제외 · 점도 결측 행 제외.

운영 DB 적재:
    set IRMS_DATA_DIR=...        (서버가 쓰는 데이터 디렉토리)
    python scripts/import_binder_viscosity.py "바인더 기록 (자동 저장됨).xlsx"
인자 없으면 위 기본 파일을 시도한다.
"""

import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import load_workbook  # noqa: E402

from src.db import get_connection, init_db, utc_now_text  # noqa: E402
from src.services import viscosity_service  # noqa: E402

DEFAULT_FILE = "바인더 기록 (자동 저장됨).xlsx"

# 열 위치(0-기준): 일자·바인더·사용한PB·점도·작업자
COL_DATE, COL_BINDER, COL_PB, COL_VISC, COL_WORKER = 0, 1, 2, 3, 4

_DATE_KO_YMD = re.compile(r"(?:(\d{2,4})\s*년)?\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_YEAR_FROM_SHEET = re.compile(r"(\d{2,4})\s*년")


def _normalize_binder(raw: str) -> str | None:
    """바인더 종류 표기 정규화. 제외 대상은 None."""
    s = str(raw).strip()
    if not s:
        return None
    if "TEST" in s.upper():
        return None  # 테스트값 제외
    if s == "CSBP":
        return "CSPB"  # 오타
    # 괄호와 그 안 내용 제거: APB(17)→APB17 은 숫자 보존, APB(1)→APB
    m = re.fullmatch(r"([A-Za-z]+)\((\d+)\)", s)
    if m:
        head, num = m.group(1), m.group(2)
        # (17) 은 등급이라 붙인다(APB(17)→APB17), 그 외 (1)(2) 는 제거
        return f"{head}{num}" if num == "17" else head
    # 남은 괄호형은 괄호째 제거
    s = re.sub(r"\(.*?\)", "", s).strip()
    return s or None


def _sheet_year(sheet_name: str) -> int | None:
    m = _YEAR_FROM_SHEET.search(sheet_name)
    if not m:
        return None
    y = int(m.group(1))
    return 2000 + y if y < 100 else y


def _parse_date(value, sheet_year: int | None) -> str | None:
    """일자 셀 → ISO(YYYY-MM-DD). 형식이 제각각이라 여러 경로로 시도."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    if not s:
        return None
    # 이미 ISO
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return s
    # "26년1월5일" / "1월 09일"(연도 없음 → 시트 연도)
    m = _DATE_KO_YMD.search(s)
    if m:
        yr, mo, dy = m.group(1), int(m.group(2)), int(m.group(3))
        if yr:
            year = int(yr)
            year = 2000 + year if year < 100 else year
        elif sheet_year:
            year = sheet_year
        else:
            return None
        try:
            return date(year, mo, dy).isoformat()
        except ValueError:
            return None
    return None


def _pb_lot(value) -> str | None:
    """사용한 PB LOT → 8자리 숫자 문자열. 형식 이상은 None."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = str(value).strip()
    return s if re.fullmatch(r"\d{8}", s) else None


def _visc(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _ensure_product(connection, code: str, now: str) -> dict:
    product = viscosity_service.get_product_by_code(connection, code)
    if product:
        return product
    connection.execute(
        "INSERT INTO viscosity_products (code, name, sigma_k, is_active, created_at) "
        "VALUES (?, ?, 3, 1, ?)",
        (code, code, now),
    )
    return viscosity_service.get_product_by_code(connection, code)


def import_binder(paths: list[str]) -> dict:
    init_db()
    now = utc_now_text()
    stats = {
        "read": 0, "inserted": 0, "dup": 0,
        "skip_binder": 0, "skip_pb": 0, "skip_visc": 0, "skip_date": 0,
        "products": {}, "linkable_pb": set(),
    }
    with get_connection() as connection:
        for path in paths:
            p = Path(path)
            if not p.exists():
                print(f"[건너뜀] 파일 없음: {p}")
                continue
            wb = load_workbook(p, read_only=True, data_only=True)
            for sheet_name in wb.sheetnames:
                if "바인더" not in sheet_name:
                    continue  # 데이터 시트만 (Sheet1 등 제외)
                year = _sheet_year(sheet_name)
                ws = wb[sheet_name]
                for row in ws.iter_rows(values_only=True):
                    if not row or len(row) <= COL_VISC:
                        continue
                    binder_raw = row[COL_BINDER]
                    if not isinstance(binder_raw, str) or binder_raw.strip() in ("", "바인더"):
                        continue  # 헤더/빈행
                    stats["read"] += 1
                    binder = _normalize_binder(binder_raw)
                    if not binder:
                        stats["skip_binder"] += 1
                        continue
                    visc = _visc(row[COL_VISC])
                    if visc is None:
                        stats["skip_visc"] += 1
                        continue
                    pb = _pb_lot(row[COL_PB])
                    if not pb:
                        stats["skip_pb"] += 1
                        continue
                    measured = _parse_date(row[COL_DATE], year)
                    worker = None
                    if len(row) > COL_WORKER and isinstance(row[COL_WORKER], str):
                        worker = row[COL_WORKER].strip() or None

                    product = _ensure_product(connection, binder, now)
                    try:
                        viscosity_service.add_reading(
                            connection,
                            product_id=product["id"],
                            lot_no=pb,                # 사용한PB = 고유 LOT
                            viscosity=visc,
                            measured_date=measured,
                            memo=None,
                            recipe_material=None,
                            material_lot=pb,          # PB 연계 키
                            created_by=worker,
                            created_at=now,
                        )
                        stats["inserted"] += 1
                        stats["products"][binder] = stats["products"].get(binder, 0) + 1
                        stats["linkable_pb"].add(pb)
                    except sqlite3.IntegrityError:
                        stats["dup"] += 1
            wb.close()
        connection.commit()
    return stats


def main() -> int:
    paths = sys.argv[1:] or [DEFAULT_FILE]
    s = import_binder(paths)
    print("\n=== 바인더 점도 임포트 결과 ===")
    print(f"읽은 데이터행 : {s['read']}")
    print(f"등록          : {s['inserted']}")
    print(f"중복(멱등)    : {s['dup']}")
    print(f"제외 — 바인더 정규화 실패/제외: {s['skip_binder']} · "
          f"점도 결측: {s['skip_visc']} · 사용한PB 형식 이상: {s['skip_pb']}")
    print(f"바인더별 등록 : {s['products']}")
    print(f"연계 가능 PB LOT 수(사용한PB 고유): {len(s['linkable_pb'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
