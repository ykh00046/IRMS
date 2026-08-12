"""배합 기록·분석 읽기 전용 조회 도구 (연구·검증용).

화면은 정해진 축(제품별·자재별·기간별)만 보여 준다. 그래서 "PB 를 가장 많이 쓴 달",
"김철수가 7월에 수동 입력한 배치", "이 자재 LOT 이 들어간 배합의 점도" 같은 교차
질문에는 답할 방법이 없었다 — 엑셀로 통째 내려받아 손으로 맞춰 보는 수밖에 없었다.
이 도구는 그 질문들을 명령 한 줄로 답하고, 결과를 JSON 으로도 낼 수 있어 사람이 아닌
쪽(외부 에이전트·스크립트)도 읽고 검증할 수 있다.

**읽기 전용이다.** DB 를 `mode=ro` URI 로 열고, 자유 SQL(`sql`)도 단일 SELECT/WITH
문만 통과시킨다. 어떤 경로로도 데이터를 바꾸지 않는다 — 운영 PC 에서 마음 놓고 돌릴
수 있어야 이 도구가 쓸모 있다.

**기본은 운영 서버에 직접 묻는다(실시간).** DB 파일을 복사해 오지 않는다 — 사본은 만든
순간부터 낡고, "지금 어떤가"를 묻는 조회에는 답이 될 수 없다. 사내망에서 운영 서버의 읽기
전용 GET 경로를 그대로 호출하므로 화면이 보는 값과 같은 값을 본다. 서버에 닿지 않으면
스냅샷 파일로 물러나고, 어느 쪽을 읽었는지 출력 첫 줄에 밝힌다.

자유 SQL(`sql`)·`schema`·`manual`·`rescale` 은 서버 API 로는 답할 수 없어 파일이 필요하다
(서버에 임의 SQL 창구를 여는 건 위험해서 만들지 않는다).

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
  --api URL   운영 서버 주소(기본 IRMS_API_URL 또는 사내망 기본값)
  --offline   서버를 쓰지 않고 스냅샷 파일로만 조회
  --db PATH   DB 파일 직접 지정(주면 --offline 과 같다 — 그 파일이 답한다)

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
import urllib.error
import urllib.parse
import urllib.request
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


# 스냅샷이 쌓이는 자리. 파일명에 시각이 들어가므로(irms_20260810_130412.db) 새 사본이
# 오면 이름이 바뀐다 — 그때마다 경로를 고쳐 넣는 건 사람이 할 일이 아니다.
SNAPSHOT_DIRS = ("local-data", "backups")


def _newest_snapshot(root: Path) -> Path | None:
    """스냅샷 폴더에서 가장 최근 *.db — 수정시각 기준."""
    candidates: list[Path] = []
    for name in SNAPSHOT_DIRS:
        folder = root / name
        if folder.is_dir():
            candidates.extend(f for f in folder.glob("*.db") if f.is_file())
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_mtime)


def resolve_db(explicit: str | None) -> Path:
    """읽을 DB 를 정한다 — 우선순위대로 처음 찾은 것.

    ① --db          직접 지정
    ② IRMS_QUERY_DB 환경변수(파일 또는 폴더)
    ③ local-data/ · backups/ 의 **가장 최근 스냅샷**
    ④ IRMS_DATA_DIR/irms.db (없으면 data/irms.db)

    ③ 이 있는 이유: 스냅샷 파일명에 시각이 박혀 있어 새 사본이 올 때마다 경로가 바뀐다.
    자동으로 최신 것을 집으면 명령을 고쳐 쓸 일이 없다. ④ 는 개발용 DB 라 배합 기록이
    비어 있을 수 있는데, 그 경우 호출부가 [DB] 경로와 함께 경고를 찍는다.
    """
    if explicit:
        return Path(explicit)

    env_db = os.environ.get("IRMS_QUERY_DB", "").strip()
    if env_db:
        path = Path(env_db)
        if path.is_dir():                     # 폴더를 주면 그 안의 최신 *.db
            files = [f for f in path.glob("*.db") if f.is_file()]
            if files:
                return max(files, key=lambda f: f.stat().st_mtime)
        return path

    here = Path(__file__).resolve().parent.parent   # tools/ 의 상위 = 저장소 루트
    for root in (Path.cwd(), here):
        newest = _newest_snapshot(root)
        if newest is not None:
            return newest

    data_dir = os.environ.get("IRMS_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "irms.db"
    return here / "data" / "irms.db"


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


# ── 실시간 조회(운영 서버에 직접 묻기) ──────────────────────────────────────
#
# 스냅샷 사본은 만든 순간부터 낡는다. 조회는 "지금 어떤가"를 묻는 일이므로 기본 경로는
# 운영 서버의 읽기 API 다. 내부망(사설 IP)에서만 열리는 GET 경로라 자격증명이 없고,
# 이 도구가 무엇을 바꿀 방법도 없다. 화면이 부르는 것과 같은 경로이므로 화면과 같은
# 값을 본다 — 도구가 제 SQL 로 따로 계산해 화면과 다른 숫자를 말할 여지도 사라진다.
#
# 기본 주소는 현장 도우미가 쓰는 것과 같다(tray_client/src/config.py DEFAULT_SERVER_URL).
DEFAULT_API = "http://192.168.11.194:9000"

# 조회 중 알아낸 주의사항(상한에 걸려 잘렸다 등). 헤더 뒤에 함께 찍는다.
WARNINGS: list[str] = []


def resolve_api(explicit: str | None) -> str:
    """물어볼 서버 — --api → IRMS_API_URL → 기본값."""
    raw = (explicit or os.environ.get("IRMS_API_URL", "").strip() or DEFAULT_API).strip()
    return raw.rstrip("/")


def api_get(base: str, path: str, params: dict | None = None, timeout: float = 20) -> dict:
    """읽기 GET 하나. 이 도구가 서버로 보내는 요청은 이것뿐이다(쓰기 경로 없음)."""
    query = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    url = f"{base}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 — http(s) 고정
        return json.loads(resp.read().decode("utf-8"))


def api_alive(base: str, timeout: float = 4) -> bool:
    """서버가 지금 응답하는가. 안 되면 조용히 스냅샷으로 물러난다."""
    try:
        req = urllib.request.Request(f"{base}/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _kg(value) -> float | None:
    return None if value is None else round(value / 1000.0, 3)


# 응답 → 표 변환은 순수 함수로 둔다. 네트워크 없이 저장된 응답만으로 검증할 수 있어야
# 이 경로에도 테스트가 붙는다(실시간 경로라고 검증을 못 하면 그게 제일 위험하다).

def shape_summary(payload: dict, _args) -> list[dict]:
    s, rng = payload.get("summary", {}), payload.get("range", {})
    workers = payload.get("workers") or []
    return [{
        "조회기간": f"{rng.get('start') or '처음'} ~ {rng.get('end') or '지금'}"
                     if (rng.get("start") or rng.get("end")) else "전체",
        "배합건수": s.get("records"),
        "총생산량_kg": _kg(s.get("total_weight_g")),
        "제품종수": s.get("product_count"),
        "자재종수": s.get("material_count"),
        "작업자수": len(workers),
        "수동입력": s.get("manual_records"),
        "취소": s.get("canceled_records"),
        "증량적용": s.get("rescale_records"),
        "상한초과": s.get("oversize_records"),
        "저울계량률_%": s.get("scale_rate"),
        "계량률_표본": s.get("scale_base_records"),
        "계량률_기준일": payload.get("scale_since") or "(미설정 — 전 구간)",
    }]


def shape_materials(payload: dict, args) -> list[dict]:
    rows = []
    for m in (payload.get("materials") or [])[: args.limit]:
        actual, theory = m.get("total_actual") or 0, m.get("total_theory") or 0
        rows.append({
            "자재": m.get("material_name"),
            "실제사용_kg": _kg(actual),
            "이론량_kg": _kg(theory),
            "차이_g": round(actual - theory, 2),
            "로스보정_g": round(m.get("loss_comp_g") or 0, 1),
            "투입배치": m.get("usage_count"),
            "비중_%": m.get("share"),
        })
    return rows


def shape_monthly(payload: dict, _args) -> list[dict]:
    rows = []
    for t in payload.get("trend") or []:
        rows.append({
            "월": t.get("bucket"),
            "배합건수": t.get("records"),
            "생산량_kg": _kg(t.get("weight_g")),
            "수동입력": t.get("manual_records"),
            "취소": t.get("canceled_records"),
            "저울계량률_%": t.get("scale_rate"),
            "계량률_표본": t.get("scale_base_records"),
            # 아직 안 끝난 달은 다른 달과 나란히 비교하면 안 된다.
            "진행중": "진행중" if t.get("partial") else "",
        })
    return rows


def shape_workers(payload: dict, _args) -> list[dict]:
    quality_block = payload.get("quality") or {}
    quality = {q.get("worker"): q for q in quality_block.get("by_worker") or []}
    if quality_block.get("manual_visible") is False:
        # 빈 칸을 '수동 입력 0건'으로 읽으면 거짓말이 된다.
        WARNINGS.append("수동 입력 열은 책임자만 볼 수 있어 비어 있습니다"
                        " — 0 건이라는 뜻이 아닙니다(목록은 --offline 의 manual).")
    rows = []
    for w in payload.get("workers") or []:
        q = quality.get(w.get("worker"), {})
        rows.append({
            "작업자": w.get("worker"),
            "완료": w.get("records"),
            "생산량_kg": _kg(w.get("total_amount")),
            "제품종수": w.get("product_count"),
            "수동입력": q.get("manual_records"),
            "수동비율_퍼센트": q.get("manual_rate"),
            "취소": q.get("canceled_records"),
        })
    return rows


def shape_records(payload: dict, _args) -> list[dict]:
    # 수동 입력 표시는 서버가 책임자에게만 준다(_mask_manual_entry). 자격 없이 받은 값은
    # 전부 0 이므로 '수동 0건'으로 읽히면 거짓말이 된다 — 열 자체를 내지 않는다.
    # 수동 입력 목록이 필요하면 --offline(스냅샷)으로 `manual` 을 쓴다.
    rows = []
    for r in payload.get("items") or []:
        rows.append({
            "작업일": r.get("work_date"),
            "제품LOT": r.get("product_lot"),
            "제품": r.get("product_name"),
            "작업자": r.get("worker"),
            "총량_g": r.get("total_amount"),
            "반응기": r.get("reactor") or "",
            "상태": r.get("status"),
        })
    return rows


def shape_material_lot(payload: dict, _args) -> list[dict]:
    rows = []
    for r in payload.get("items") or []:
        rows.append({
            "작업일": r.get("work_date"),
            "제품LOT": r.get("product_lot"),
            "제품": r.get("product_name"),
            "자재": r.get("material_name"),
            "자재LOT": r.get("material_lot"),
            "실제량_g": r.get("actual_amount"),
            "작업자": r.get("worker"),
            "상태": r.get("status"),
        })
    return rows


def shape_viscosity_overview(payload: dict, args) -> list[dict]:
    rows = []
    for p in (payload.get("items") or [])[: args.limit]:
        rows.append({
            "반제품": p.get("code"),
            "이름": p.get("name"),
            "측정수": p.get("count"),
            "평균": p.get("mean"),
            "표준편차": p.get("std"),
            "최근값": p.get("latest_value"),
            "최근측정": p.get("latest_date"),
            "최근판정": p.get("last_status"),
            "이상": p.get("anomaly_count"),
            "경고": p.get("warn_count"),
        })
    return rows


_VISC_VERDICT = {"anomaly": "이상", "warn": "경고", "normal": ""}


def shape_viscosity_readings(payload: dict, args) -> list[dict]:
    product = payload.get("product") or {}
    rows = []
    for r in payload.get("readings") or []:
        date = r.get("measured_date") or ""
        if args.from_date and date < args.from_date:
            continue
        if args.to_date and date > args.to_date:
            continue
        verdict = _VISC_VERDICT.get(r.get("status"), r.get("status") or "")
        side = r.get("side")
        rows.append({
            "반제품": product.get("code"),
            "측정일": date,
            "LOT": r.get("lot_no"),
            "점도": r.get("viscosity"),
            "목표": product.get("target"),
            "하한": product.get("lower_limit"),
            "상한": product.get("upper_limit"),
            "판정": f"{verdict}({side})" if verdict and side else verdict,
            "제외": "통계제외" if r.get("excluded") else "",
            "자재LOT": r.get("material_lot") or "",
            "반응기": r.get("reactor") or "",
        })
    # 서버는 오래된 순으로 준다. --limit 로 자르면 2024 년 측정만 남아 "요즘 점도"를
    # 물은 사람에게 옛날 값을 답하게 된다 — 최근 순으로 돌린 뒤 자른다.
    rows.sort(key=lambda r: r["측정일"] or "", reverse=True)
    return rows[: args.limit]


def _analysis(base: str, args, bucket: str | None = None) -> dict:
    return api_get(base, "/api/blend/analysis", {
        "start_date": args.from_date, "end_date": args.to_date, "bucket": bucket,
    })


def a_summary(base, args):
    return shape_summary(_analysis(base, args), args)


def a_materials(base, args):
    return shape_materials(_analysis(base, args), args)


def a_monthly(base, args):
    return shape_monthly(_analysis(base, args, bucket="month"), args)


def a_workers(base, args):
    return shape_workers(_analysis(base, args), args)


def a_records(base, args):
    payload = api_get(base, "/api/blend/records", {
        "start_date": args.from_date, "end_date": args.to_date,
        "worker": args.worker, "product": args.product, "search": args.lot,
        "limit": min(args.limit, 1000),
    })
    rows = shape_records(payload, args)
    if payload.get("truncated"):
        # 조용히 잘린 목록을 전부인 것처럼 읽으면 "그런 배합 없다"는 오답이 된다.
        WARNINGS.append(
            f"전체 {payload.get('total_available')}건 중 {len(rows)}건만 받았습니다"
            f" — --from/--to 로 기간을 좁히세요(서버 상한 1000건)."
        )
    return rows


def a_material_lot(base, args):
    return shape_material_lot(api_get(base, "/api/blend/material-lot-trace", {
        "lot": args.lot, "limit": min(args.limit, 1000),
    }), args)


def a_viscosity(base, args):
    overview = api_get(base, "/api/viscosity/overview")
    if not args.product:
        return shape_viscosity_overview(overview, args)
    wanted = args.product.strip().lower()
    match = next(
        (p for p in overview.get("items") or []
         if (p.get("code") or "").lower() == wanted or (p.get("name") or "").lower() == wanted),
        None,
    )
    if match is None:
        codes = ", ".join(p.get("code") or "" for p in (overview.get("items") or [])[:20])
        raise SystemExit(f"그런 반제품이 없습니다: {args.product}  (있는 것: {codes})")
    return shape_viscosity_readings(
        api_get(base, f"/api/viscosity/products/{match['id']}"), args
    )


# 서버에 물어 답할 수 있는 명령. 여기 없는 것은 파일이 필요하다:
#   schema/sql — 자유 조회. 서버에 임의 SQL 창구를 여는 건 위험해서 만들지 않는다.
#   manual     — 수동 입력 표시는 책임자에게만 주는 값이라 익명 조회로는 전부 0 이다.
#   rescale    — 증량 '배치 목록'을 주는 읽기 경로가 없다(요약·미확인만 있다).
API_COMMANDS = {
    "summary": a_summary, "records": a_records, "materials": a_materials,
    "monthly": a_monthly, "workers": a_workers, "material-lot": a_material_lot,
    "viscosity": a_viscosity,
}


def live_blocked_reason(args) -> str | None:
    """이 명령을 실시간으로 답할 수 없는 이유(없으면 None)."""
    if args.command not in API_COMMANDS:
        return {
            "schema": "테이블 구조는 DB 파일에서만 볼 수 있습니다",
            "sql": "자유 SQL 은 서버에 창구가 없습니다(의도적)",
            "manual": "수동 입력 표시는 책임자 전용이라 서버 조회로는 전부 0 으로 보입니다",
            "rescale": "증량 배치 목록을 주는 읽기 경로가 서버에 없습니다",
        }.get(args.command, "서버로 답할 수 없는 명령입니다")
    if args.command == "monthly" and args.material:
        return "자재별 월 소비를 주는 읽기 경로가 서버에 없습니다"
    return None


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
    # 어떤 명령이 실시간으로 답하는지도 함께 낸다 — 부르는 쪽이 사본을 준비해야 하는지
    # 미리 알 수 있어야 한다.
    return [
        {**entry,
         "조회원본": "실시간" if entry["command"] in API_COMMANDS
                     else ("DB 불필요" if entry["command"] == "catalog" else "스냅샷 파일")}
        for entry in CATALOG
    ]


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
    WARNINGS.clear()          # 같은 프로세스에서 여러 번 부를 수 있다(테스트)
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
    parser.add_argument("--api", help="운영 서버 주소(기본: IRMS_API_URL 또는 사내망 기본값)")
    parser.add_argument("--offline", action="store_true",
                        help="서버를 쓰지 않고 스냅샷 파일로만 조회")
    parser.add_argument("--db", help="DB 파일 경로(주면 --offline 과 같다)")
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

    # ── 어디에 물을 것인가 ──────────────────────────────────────────────
    # 기본은 살아 있는 서버다. 사본을 만들어 두고 읽는 건 조회가 아니라 덤프이고,
    # 만든 순간부터 낡는다. 파일은 서버가 못 답하는 것(자유 SQL 등)과 서버에 못 닿을
    # 때의 대비책이다. --db 를 직접 준 사람은 그 파일을 보겠다는 뜻이므로 그게 이긴다.
    offline = args.offline or bool(args.db) or os.environ.get("IRMS_QUERY_OFFLINE") == "1"
    blocked = live_blocked_reason(args) if args.command != "catalog" else "카탈로그"
    api_base = None if offline else resolve_api(args.api)
    note = None
    use_live = False
    if args.command != "catalog" and not offline:
        if blocked:
            note = f"{blocked} — 스냅샷 파일로 조회합니다"
        elif not api_alive(api_base):
            note = f"서버({api_base})에 닿지 않아 스냅샷 파일로 조회합니다"
        else:
            use_live = True

    if use_live:
        rows = API_COMMANDS[args.command](api_base, args)
        source, origin, conn_had_no_records = "live", api_base, False
        db_path = None
    else:
        db_path = resolve_db(args.db)
        source, origin = "snapshot", str(db_path)
        # catalog 는 DB 가 없어도 답한다 — 에이전트가 접속 전에 먼저 물어보는 명령이다.
        conn = None if args.command == "catalog" else connect(db_path)
        conn_had_no_records = False
        try:
            rows = COMMANDS[args.command](conn, args)
            if conn is not None:
                try:
                    conn_had_no_records = conn.execute(
                        "SELECT COUNT(*) FROM blend_records"
                    ).fetchone()[0] == 0
                except sqlite3.OperationalError:   # 배합 테이블이 없는 DB 도 있을 수 있다
                    conn_had_no_records = True
        finally:
            if conn is not None:
                conn.close()

    # 어느 DB 를 읽었는지 표 출력에도 밝힌다 — 이 PC 에는 배합 기록 0건짜리 개발용
    # data/irms.db 가 있어서, --db 를 빼면 오류 없이 '0건'을 답한다. 그 0 이 어느
    # 파일에서 나왔는지 보이지 않으면 빈 답을 진짜 답으로 읽는다(2026-08-10).
    if not args.json and args.command != "catalog":
        empty = conn_had_no_records if args.command != "schema" else False
        if use_live:
            print(f"[실시간] {origin} - 운영 서버의 지금 값")
        else:
            print(f"[스냅샷] {db_path}")
            if note:
                print(f"  · {note}")
        if empty:
            print("  [주의] 이 DB 에는 배합 기록이 없습니다 - 조회 대상을 잘못 가리켰을 수 있습니다"
                  " (--db 로 스냅샷 경로를 지정하세요).")
        for warning in WARNINGS:
            print(f"  [주의] {warning}")
        print()

    if args.json:
        # 봉투에 조건을 실어 준다 - 결과만 보면 어떤 질문의 답인지 알 수 없다.
        filters = {
            k: v for k, v in {
                "from": args.from_date, "to": args.to_date, "product": args.product,
                "worker": args.worker, "material": args.material, "lot": args.lot,
                "statement": args.statement or None,
            }.items() if v
        }
        print(json.dumps({
            "command": args.command,
            "source": source,          # live = 운영 서버의 지금 값 / snapshot = 사본
            "origin": origin,
            "note": note,
            "warnings": list(WARNINGS),
            "db": str(db_path) if db_path is not None else None,
            "filters": filters,
            "row_count": len(rows),
            "database_has_records": not conn_had_no_records,
            "rows": rows,
        }, ensure_ascii=False, indent=2, default=str))
    else:
        print_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
