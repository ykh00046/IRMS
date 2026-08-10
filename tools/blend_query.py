"""배합 기록·분석 읽기 전용 조회 도구 (연구·검증용).

화면은 정해진 축(제품별·자재별·기간별)만 보여 준다. 그래서 "PB 를 가장 많이 쓴 달",
"김철수가 7월에 수동 입력한 배치", "이 자재 LOT 이 들어간 배합의 점도" 같은 교차
질문에는 답할 방법이 없었다 — 엑셀로 통째 내려받아 손으로 맞춰 보는 수밖에 없었다.
이 도구는 그 질문들을 명령 한 줄로 답하고, 결과를 JSON 으로도 낼 수 있어 사람이 아닌
쪽(외부 에이전트·스크립트)도 읽고 검증할 수 있다.

**읽기 전용이다.** DB 를 `mode=ro` URI 로 열고, 자유 SQL(`sql`)도 단일 SELECT/WITH
문만 통과시킨다. 어떤 경로로도 데이터를 바꾸지 않는다 — 운영 PC 에서 마음 놓고 돌릴
수 있어야 이 도구가 쓸모 있다.

사용:
  python tools/blend_query.py schema                      # 테이블·컬럼(자유 SQL 쓰기 전에)
  python tools/blend_query.py summary --from 2026-07-01 --to 2026-07-31
  python tools/blend_query.py records --product APB --limit 20
  python tools/blend_query.py materials --from 2026-01-01
  python tools/blend_query.py material-lot RM-2607
  python tools/blend_query.py workers --from 2026-07-01
  python tools/blend_query.py manual --from 2026-07-01       # 수동 입력 배치
  python tools/blend_query.py rescale                        # 증량 적용 배치
  python tools/blend_query.py viscosity --product PB
  python tools/blend_query.py monthly --material PB          # 자재 월별 소비
  python tools/blend_query.py sql "SELECT product_name, COUNT(*) FROM blend_records GROUP BY 1"

  --json      결과를 JSON 으로(파이프·에이전트용)
  --db PATH   DB 파일 직접 지정(기본: IRMS_DATA_DIR 또는 data/irms.db)

다른 환경(외부 에이전트·다른 PC)에서 쓸 때:
  - **표준 라이브러리만** 쓴다. 프로젝트 venv·의존성 설치 없이 아무 Python 3 로 돈다
    (test_blend_query_tool.py 가 import 를 검사해 이 성질을 고정한다).
  - 출력은 콘솔 설정과 무관하게 **UTF-8** 이다(한글 Windows 의 CP949 로 깨지지 않는다).
  - `catalog` 는 DB 없이도 답한다 — 무엇을 물어볼 수 있는지 먼저 확인하는 용도.
  - 파일 하나만 있으면 된다: `python blend_query.py catalog --json`

'완료 기록' 정의는 화면·분석과 같다: status='completed' AND is_bulk_regenerated=0.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

# 완료 기록 — blend_service.analysis 와 같은 정의(여기서 달라지면 도구가 화면과
# 다른 숫자를 말하게 된다).
DONE = "br.status = 'completed' AND COALESCE(br.is_bulk_regenerated, 0) = 0"

# 자유 SQL 가드 — 단일 읽기 문장만. 세미콜론으로 문장을 덧붙이는 것도 막는다.
_READ_ONLY_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX)\b",
    re.IGNORECASE,
)


def resolve_db(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    data_dir = os.environ.get("IRMS_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "irms.db"
    return Path("data") / "irms.db"


def connect(path: Path) -> sqlite3.Connection:
    """읽기 전용으로 연다 — 이 도구가 운영 DB 를 건드릴 방법 자체를 없앤다.

    ⚠ mode=ro 를 immutable=1 로 바꾸지 말 것. immutable 은 -shm/-wal 부산물을 안 만들어
    깔끔해 보이지만, "이 파일은 아무도 안 바꾼다"를 SQLite 에 약속하는 옵션이다. 운영
    PC 에서 서버가 돌아가는 중인 라이브 DB 를 가리키면(이 도구의 정상적인 사용법이다)
    낡은 페이지를 오류 없이 읽어 조용히 틀린 답을 준다. 부산물 몇 개가 그 위험보다 싸다.
    """
    if not path.exists():
        raise SystemExit(f"DB 를 찾을 수 없습니다: {path}")
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def scale_since(conn) -> str | None:
    """저울 도입일 설정 — 계량률이 뜻을 갖는 시작점.

    앱(blend_service.analysis)이 쓰는 것과 같은 값을 같은 규칙으로 읽는다. 이걸 빼먹으면
    도구가 화면과 다른 숫자를 말한다(실제로 그랬다: 화면 77.4% · 도구 98.7%, 2026-08-10).
    설정이 없으면 None — 그때는 전 구간으로 계산한다(종전 동작).
    """
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'scale_since'"
        ).fetchone()
    except sqlite3.OperationalError:      # app_settings 가 없는 옛 DB
        return None
    raw = (row["value"] if row else "") or ""
    raw = raw.strip()
    return raw if len(raw) == 10 and raw[4] == "-" and raw[7] == "-" else None


def _range_clause(args, alias: str = "br") -> tuple[str, list]:
    parts, params = [], []
    if getattr(args, "from_date", None):
        parts.append(f"{alias}.work_date >= ?")
        params.append(args.from_date)
    if getattr(args, "to_date", None):
        parts.append(f"{alias}.work_date <= ?")
        params.append(args.to_date)
    return ("".join(f" AND {p}" for p in parts), params)


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]


# ── 조회 ────────────────────────────────────────────────────────────────────

def q_schema(conn, _args) -> list[dict]:
    """테이블과 컬럼 — 자유 SQL 을 쓰기 전에 무엇이 있는지 본다."""
    out = []
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    for t in tables:
        cols = conn.execute(f"PRAGMA table_info({t['name']})").fetchall()
        out.append({
            "table": t["name"],
            "columns": [f"{c['name']} {c['type']}".strip() for c in cols],
            "rows": conn.execute(f"SELECT COUNT(*) FROM {t['name']}").fetchone()[0],
        })
    return out


def q_summary(conn, args) -> list[dict]:
    """기간 요약 — 화면의 배합 분석 지표와 같은 정의."""
    where, params = _range_clause(args)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS 배합건수,
               ROUND(COALESCE(SUM(br.total_amount), 0) / 1000.0, 2) AS 총생산량_kg,
               COUNT(DISTINCT br.product_name) AS 제품종수,
               COUNT(DISTINCT br.worker) AS 작업자수,
               SUM(CASE WHEN COALESCE(br.manual_entry,0)=1 THEN 1 ELSE 0 END) AS 수동입력,
               SUM(CASE WHEN COALESCE(br.rescale_count,0)>0 THEN 1 ELSE 0 END) AS 증량적용,
               SUM(CASE WHEN COALESCE(br.oversize_total,0)=1 THEN 1 ELSE 0 END) AS 상한초과,
               MIN(br.work_date) AS 첫작업일, MAX(br.work_date) AS 마지막작업일
        FROM blend_records br WHERE {DONE}{where}
        """,
        params,
    ).fetchone()
    canceled = conn.execute(
        f"SELECT COUNT(*) FROM blend_records br WHERE br.status='canceled' "
        f"AND COALESCE(br.is_bulk_regenerated,0)=0{where}",
        params,
    ).fetchone()[0]
    result = dict(row)
    result["취소"] = canceled

    # 저울 계량률 — 도입일 이후 기록만. 그 전 배합에는 '수동 입력' 표시가 없어
    # 저울로 잰 것과 구분되지 않으므로, 함께 세면 100% 에 가깝게 부풀려진다.
    since = scale_since(conn)
    swhere, sparams = _range_clause(args)
    if since:
        swhere += " AND br.work_date >= ?"
        sparams = [*sparams, since]
    srow = conn.execute(
        f"SELECT COUNT(*) AS n, SUM(CASE WHEN COALESCE(br.manual_entry,0)=1 THEN 1 ELSE 0 END)"
        f" AS m FROM blend_records br WHERE {DONE}{swhere}",
        sparams,
    ).fetchone()
    n, m = int(srow["n"] or 0), int(srow["m"] or 0)
    result["저울계량률_%"] = round((n - m) / n * 100, 1) if n else None
    result["계량률_표본"] = n
    result["계량률_기준일"] = since or "(미설정 — 전 구간)"
    return [result]


def q_records(conn, args) -> list[dict]:
    where, params = _range_clause(args)
    if args.product:
        where += " AND br.product_name = ?"
        params.append(args.product)
    if args.worker:
        where += " AND br.worker = ?"
        params.append(args.worker)
    if args.lot:
        where += " AND br.product_lot LIKE ?"
        params.append(f"%{args.lot}%")
    return rows_to_list(conn.execute(
        f"""
        SELECT br.work_date AS 작업일, br.product_lot AS 제품LOT, br.product_name AS 제품,
               br.worker AS 작업자, br.total_amount AS 총량_g,
               CASE WHEN COALESCE(br.manual_entry,0)=1 THEN 'Y' ELSE '' END AS 수동,
               COALESCE(br.rescale_count,0) AS 증량, br.status AS 상태
        FROM blend_records br WHERE {DONE}{where}
        ORDER BY br.work_date DESC, br.id DESC LIMIT ?
        """,
        [*params, args.limit],
    ))


def q_materials(conn, args) -> list[dict]:
    where, params = _range_clause(args)
    return rows_to_list(conn.execute(
        f"""
        SELECT bd.material_name AS 자재,
               ROUND(SUM(bd.actual_amount) / 1000.0, 3) AS 실제사용_kg,
               ROUND(SUM(bd.theory_amount) / 1000.0, 3) AS 이론량_kg,
               ROUND(SUM(bd.actual_amount) - SUM(bd.theory_amount), 2) AS 차이_g,
               ROUND(SUM(COALESCE(bd.loss_comp_g, 0)), 1) AS 로스보정_g,
               COUNT(DISTINCT bd.blend_record_id) AS 투입배치
        FROM blend_details bd JOIN blend_records br ON br.id = bd.blend_record_id
        WHERE {DONE}{where}
        GROUP BY bd.material_name ORDER BY SUM(bd.actual_amount) DESC LIMIT ?
        """,
        [*params, args.limit],
    ))


def q_monthly(conn, args) -> list[dict]:
    """월별 추이 — 자재를 주면 그 자재 소비, 없으면 전체 생산량."""
    where, params = _range_clause(args)
    if args.material:
        return rows_to_list(conn.execute(
            f"""
            SELECT substr(br.work_date,1,7) AS 월,
                   ROUND(SUM(bd.actual_amount)/1000.0, 3) AS 소비_kg,
                   COUNT(DISTINCT bd.blend_record_id) AS 투입배치
            FROM blend_details bd JOIN blend_records br ON br.id = bd.blend_record_id
            WHERE {DONE} AND bd.material_name = ?{where}
            GROUP BY 월 ORDER BY 월
            """,
            [args.material, *params],
        ))
    return rows_to_list(conn.execute(
        f"""
        SELECT substr(br.work_date,1,7) AS 월, COUNT(*) AS 배합건수,
               ROUND(SUM(br.total_amount)/1000.0, 2) AS 생산량_kg,
               SUM(CASE WHEN COALESCE(br.manual_entry,0)=1 THEN 1 ELSE 0 END) AS 수동입력
        FROM blend_records br WHERE {DONE}{where}
        GROUP BY 월 ORDER BY 월
        """,
        params,
    ))


def q_material_lot(conn, args) -> list[dict]:
    """자재 LOT 역추적 — 그 LOT 이 들어간 배합(리콜 대응). 기간 무관."""
    return rows_to_list(conn.execute(
        """
        SELECT br.work_date AS 작업일, br.product_lot AS 제품LOT, br.product_name AS 제품,
               bd.material_name AS 자재, bd.material_lot AS 자재LOT,
               bd.actual_amount AS 실제량_g, br.worker AS 작업자, br.status AS 상태
        FROM blend_details bd JOIN blend_records br ON br.id = bd.blend_record_id
        WHERE bd.material_lot LIKE ?
        ORDER BY br.work_date DESC, br.id DESC LIMIT ?
        """,
        (f"%{args.lot}%", args.limit),
    ))


def q_workers(conn, args) -> list[dict]:
    where, params = _range_clause(args)
    return rows_to_list(conn.execute(
        f"""
        SELECT br.worker AS 작업자, COUNT(*) AS 완료,
               ROUND(SUM(br.total_amount)/1000.0, 2) AS 생산량_kg,
               COUNT(DISTINCT br.product_name) AS 제품종수,
               SUM(CASE WHEN COALESCE(br.manual_entry,0)=1 THEN 1 ELSE 0 END) AS 수동입력,
               ROUND(SUM(CASE WHEN COALESCE(br.manual_entry,0)=1 THEN 1.0 ELSE 0 END)
                     / COUNT(*) * 100, 1) AS 수동비율_퍼센트
        FROM blend_records br WHERE {DONE}{where}
        GROUP BY br.worker ORDER BY 완료 DESC
        """,
        params,
    ))


def q_manual(conn, args) -> list[dict]:
    """수동 입력 배치 — 저울을 쓰지 않은 건. 이상 추적의 출발점."""
    where, params = _range_clause(args)
    return rows_to_list(conn.execute(
        f"""
        SELECT br.work_date AS 작업일, br.product_lot AS 제품LOT, br.product_name AS 제품,
               br.worker AS 작업자, br.manual_absence_reason AS 사유,
               CASE WHEN COALESCE(br.manual_unacked,0)=1 THEN '미확인' ELSE '' END AS 확인
        FROM blend_records br
        WHERE {DONE} AND COALESCE(br.manual_entry,0)=1{where}
        ORDER BY br.work_date DESC LIMIT ?
        """,
        [*params, args.limit],
    ))


def q_rescale(conn, args) -> list[dict]:
    """증량 적용 배치 — 승인/미확인 여부까지."""
    where, params = _range_clause(args)
    return rows_to_list(conn.execute(
        f"""
        SELECT br.work_date AS 작업일, br.product_lot AS 제품LOT, br.product_name AS 제품,
               br.worker AS 작업자, COALESCE(br.rescale_count,0) AS 증량횟수,
               br.total_amount AS 총량_g,
               CASE WHEN COALESCE(br.rescale_unacked,0)=1 THEN '미확인' ELSE '' END AS 확인,
               CASE WHEN COALESCE(br.oversize_total,0)=1 THEN '상한초과' ELSE '' END AS 비고
        FROM blend_records br
        WHERE {DONE} AND COALESCE(br.rescale_count,0) > 0{where}
        ORDER BY br.work_date DESC LIMIT ?
        """,
        [*params, args.limit],
    ))


def q_viscosity(conn, args) -> list[dict]:
    """배합 ↔ 점도 연계 — 화면에서는 반제품 하나씩만 볼 수 있다."""
    where, params = _range_clause(args)
    extra, xparams = "", []
    if args.product:
        extra = " AND vp.code = ?"
        xparams = [args.product]
    return rows_to_list(conn.execute(
        f"""
        SELECT vp.code AS 반제품, vr.measured_date AS 측정일, vr.lot_no AS LOT,
               vr.viscosity AS 점도, vp.target AS 목표,
               vp.lower_limit AS 하한, vp.upper_limit AS 상한,
               CASE WHEN vp.lower_limit IS NOT NULL AND vr.viscosity < vp.lower_limit THEN '하한이탈'
                    WHEN vp.upper_limit IS NOT NULL AND vr.viscosity > vp.upper_limit THEN '상한이탈'
                    ELSE '' END AS 판정,
               CASE WHEN COALESCE(vr.excluded,0)=1 THEN '통계제외' ELSE '' END AS 제외,
               br.product_lot AS 연계배합, br.worker AS 작업자
        FROM viscosity_readings vr
        JOIN viscosity_products vp ON vp.id = vr.product_id
        LEFT JOIN blend_records br ON br.id = vr.blend_record_id
        WHERE 1=1{extra}{where.replace('br.work_date', 'vr.measured_date')}
        ORDER BY vr.measured_date DESC LIMIT ?
        """,
        [*xparams, *params, args.limit],
    ))


def q_sql(conn, args) -> list[dict]:
    """자유 조회 — 단일 SELECT/WITH 만. 그 외는 실행하지 않는다."""
    text = args.statement.strip().rstrip(";")
    if ";" in text:
        raise SystemExit("한 번에 한 문장만 실행합니다(세미콜론 불가).")
    if not _READ_ONLY_START.match(text):
        raise SystemExit("SELECT 또는 WITH 로 시작하는 조회만 실행합니다.")
    if _FORBIDDEN.search(text):
        raise SystemExit("쓰기·스키마 변경 키워드가 들어 있어 실행하지 않습니다.")
    return rows_to_list(conn.execute(text).fetchmany(args.limit))


# 명령 카탈로그 — 사람이 아닌 쪽(외부 에이전트)이 --help 를 파싱하지 않고도
# 무엇을 물어볼 수 있는지 알 수 있게 한다. catalog 명령이 이걸 그대로 낸다.
CATALOG = [
    {"command": "schema", "설명": "테이블·컬럼·행수(자유 SQL 전에)", "필터": []},
    {"command": "summary", "설명": "기간 요약(건수·생산량·저울 계량률·취소)",
     "필터": ["--from", "--to"]},
    {"command": "records", "설명": "배합 기록 목록",
     "필터": ["--from", "--to", "--product", "--worker", "--lot", "--limit"]},
    {"command": "materials", "설명": "자재별 소비·이론량 차이·로스 보정",
     "필터": ["--from", "--to", "--limit"]},
    {"command": "monthly", "설명": "월별 추이(--material 주면 그 자재 소비)",
     "필터": ["--from", "--to", "--material"]},
    {"command": "material-lot", "설명": "자재 LOT 역추적(기간 무관)",
     "필터": ["<LOT>", "--limit"]},
    {"command": "workers", "설명": "작업자별 실적·수동 입력 비율",
     "필터": ["--from", "--to"]},
    {"command": "manual", "설명": "수동 입력 배치 목록(이상 추적)",
     "필터": ["--from", "--to", "--limit"]},
    {"command": "rescale", "설명": "증량 적용 배치(승인·미확인 포함)",
     "필터": ["--from", "--to", "--limit"]},
    {"command": "viscosity", "설명": "점도 측정 + 연계 배합·규격 판정",
     "필터": ["--from", "--to", "--product", "--limit"]},
    {"command": "sql", "설명": "자유 조회(단일 SELECT/WITH 만)", "필터": ["<SQL>", "--limit"]},
    {"command": "catalog", "설명": "이 목록", "필터": []},
]


def q_catalog(_conn, _args) -> list[dict]:
    return list(CATALOG)


COMMANDS = {
    "schema": q_schema, "summary": q_summary, "records": q_records,
    "materials": q_materials, "monthly": q_monthly, "material-lot": q_material_lot,
    "workers": q_workers, "manual": q_manual, "rescale": q_rescale,
    "viscosity": q_viscosity, "sql": q_sql, "catalog": q_catalog,
}


def print_table(rows: list[dict]) -> None:
    if not rows:
        print("(결과 없음)")
        return
    if "columns" in rows[0]:      # schema 는 표가 아니라 목록으로
        for r in rows:
            print(f"\n[{r['table']}]  {r['rows']}행")
            for c in r["columns"]:
                print(f"  - {c}")
        return
    headers = list(rows[0].keys())
    widths = [
        max(len(str(h)), *(len(str(r.get(h, ""))) for r in rows)) for h in headers
    ]
    print(" | ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(" | ".join(str(r.get(h, "") if r.get(h) is not None else "").ljust(w)
                         for h, w in zip(headers, widths)))
    print(f"\n{len(rows)}행")


def main(argv: list[str] | None = None) -> int:
    # 한글 Windows 콘솔은 기본이 CP949 라, 이 도구를 다른 프로그램이 붙잡아 읽으면
    # 한글 컬럼명에서 깨지거나 UnicodeDecodeError 로 죽는다. 기계가 읽는 출력이므로
    # 콘솔 설정과 무관하게 UTF-8 로 고정한다(PYTHONIOENCODING 을 부르는 쪽에
    # 요구하지 않는다 — 그건 이 도구를 쓰기 어렵게 만든다).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):   # 리다이렉트된 스트림 등은 그냥 둔다
            pass

    parser = argparse.ArgumentParser(
        description="배합 기록·분석 읽기 전용 조회 (연구·검증용)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("command", choices=sorted(COMMANDS))
    # nargs="*" — 옵션(--db 등)이 앞에 와도 뒤따르는 인자를 놓치지 않는다.
    # ("sql --db X \"SELECT ...\"" 형태를 nargs="?" 로는 못 받는다.)
    parser.add_argument("statement", nargs="*", help="sql / material-lot 의 인자")
    parser.add_argument("--db", help="DB 파일 경로(기본: IRMS_DATA_DIR 또는 data/irms.db)")
    parser.add_argument("--from", dest="from_date", help="시작일 YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", help="종료일 YYYY-MM-DD")
    parser.add_argument("--product", help="제품(반제품)명")
    parser.add_argument("--worker", help="작업자명")
    parser.add_argument("--material", help="자재명")
    parser.add_argument("--lot", help="LOT(부분 일치)")
    parser.add_argument("--limit", type=int, default=200, help="최대 행 수(기본 200)")
    parser.add_argument("--json", action="store_true", help="JSON 으로 출력")
    # argparse 는 "sql --db X \"SELECT ...\"" 처럼 옵션이 위치 인자 사이에 끼면 뒤의
    # 조회문을 못 받는다. sql 에 한해 남은 인자를 조회문으로 이어 붙인다(다른 명령에서는
    # 오타 난 옵션이 조용히 삼켜지지 않도록 그대로 오류로 낸다).
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        if args.command == "sql":
            args.statement = [*args.statement, *unknown]
        else:
            parser.error(f"알 수 없는 인자: {' '.join(unknown)}")

    positional = " ".join(args.statement).strip()
    # material-lot 은 위치 인자로 LOT 을 받는다(자주 쓰는 형태).
    if args.command == "material-lot" and positional and not args.lot:
        args.lot = positional
    if args.command == "material-lot" and not args.lot:
        raise SystemExit("자재 LOT 을 지정하세요: blend_query.py material-lot RM-2607")
    if args.command == "sql" and not positional:
        raise SystemExit("조회문을 지정하세요: blend_query.py sql \"SELECT ...\"")
    args.statement = positional

    db_path = resolve_db(args.db)
    # catalog 는 DB 가 없어도 답한다 — 에이전트가 접속 전에 먼저 물어보는 명령이다.
    conn = None if args.command == "catalog" else connect(db_path)
    try:
        rows = COMMANDS[args.command](conn, args)
    finally:
        if conn is not None:
            conn.close()

    if args.json:
        # 봉투에 조건을 실어 준다 — 결과만 보면 어떤 질문의 답인지 알 수 없다.
        filters = {
            k: v for k, v in {
                "from": args.from_date, "to": args.to_date, "product": args.product,
                "worker": args.worker, "material": args.material, "lot": args.lot,
                "statement": args.statement or None,
            }.items() if v
        }
        print(json.dumps({
            "command": args.command,
            "db": str(db_path),
            "filters": filters,
            "row_count": len(rows),
            "rows": rows,
        }, ensure_ascii=False, indent=2, default=str))
    else:
        print_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
