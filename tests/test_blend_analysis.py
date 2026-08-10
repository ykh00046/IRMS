"""배합 분석(/insight) 통합 집계 blend_service.analysis() 검증.

이 화면의 숫자는 전부 이 함수 하나에서 나오고, 그중 '저울 계량률'과 '전기 대비'는
개선 성과를 말하는 데 쓰이므로 계산이 틀리면 조용히 잘못된 주장을 하게 된다.
"""

from __future__ import annotations

import sqlite3

from src.services import blend_service as bs


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE blend_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_lot TEXT NOT NULL, product_name TEXT NOT NULL,
            worker TEXT NOT NULL, work_date TEXT NOT NULL,
            total_amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            manual_entry INTEGER NOT NULL DEFAULT 0,
            rescale_count INTEGER NOT NULL DEFAULT 0,
            oversize_total INTEGER NOT NULL DEFAULT 0,
            is_bulk_regenerated INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT '2026-01-01'
        );
        CREATE TABLE blend_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blend_record_id INTEGER NOT NULL,
            material_name TEXT NOT NULL, material_lot TEXT,
            ratio REAL, theory_amount REAL, actual_amount REAL,
            sequence_order INTEGER NOT NULL DEFAULT 0,
            manual_entry INTEGER NOT NULL DEFAULT 0,
            loss_comp_g REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT '2026-01-01'
        );
        """
    )
    return conn


def _add(
    conn: sqlite3.Connection,
    *,
    date: str,
    product: str = "APB",
    worker: str = "김철수",
    total: float = 1000.0,
    status: str = "completed",
    manual: int = 0,
    rescale: int = 0,
    oversize: int = 0,
    bulk: int = 0,
    materials: list[tuple[str, float, float, float]] | None = None,
) -> int:
    """materials 항목 = (자재명, 이론량, 실제량, 로스보정g)."""
    cur = conn.execute(
        "INSERT INTO blend_records (product_lot, product_name, worker, work_date,"
        " total_amount, status, manual_entry, rescale_count, oversize_total,"
        " is_bulk_regenerated) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f"{product}-{date}-{conn.total_changes}", product, worker, date, total,
         status, manual, rescale, oversize, bulk),
    )
    rid = cur.lastrowid
    for order, (name, theory, actual, comp) in enumerate(materials or []):
        conn.execute(
            "INSERT INTO blend_details (blend_record_id, material_name, theory_amount,"
            " actual_amount, sequence_order, loss_comp_g) VALUES (?,?,?,?,?,?)",
            (rid, name, theory, actual, order, comp),
        )
    return rid


def test_summary_counts_only_completed_non_bulk() -> None:
    """취소·일괄 재생성분은 건수에도 총량에도 들어가지 않는다."""
    conn = _make_db()
    _add(conn, date="2026-03-02", total=1000)
    _add(conn, date="2026-03-03", total=2000)
    _add(conn, date="2026-03-04", total=9999, status="canceled")
    _add(conn, date="2026-03-05", total=8888, bulk=1)  # 이미 센 배치의 사본

    d = bs.analysis(conn, "2026-03-01", "2026-03-31")
    assert d["summary"]["records"] == 2
    assert d["summary"]["total_weight_g"] == 3000.0
    # 취소는 별도 지표로는 잡힌다(빠뜨리면 취소율이 늘 0이 된다).
    assert d["summary"]["canceled_records"] == 1


def test_scale_rate_and_cancel_rate() -> None:
    conn = _make_db()
    for _ in range(3):
        _add(conn, date="2026-03-02")
    _add(conn, date="2026-03-02", manual=1)          # 완료 4건 중 1건 수동
    _add(conn, date="2026-03-03", status="canceled")  # 시도 5건 중 1건 취소

    s = bs.analysis(conn, "2026-03-01", "2026-03-31")["summary"]
    assert s["records"] == 4
    assert s["manual_records"] == 1
    assert s["scale_rate"] == 75.0
    assert s["cancel_rate"] == 20.0


def test_rates_do_not_divide_by_zero_when_no_records() -> None:
    """기록 없는 기간을 조회하는 일은 흔하다 — 0 으로 나누지 않는다.

    취소율은 0%(취소가 없었던 게 맞다)지만 저울 계량률은 None 이다: 잰 게 없으면
    '0% 로 쟀다'가 아니라 '말할 수 없다'가 맞고, 화면도 '—' 로 표시한다(2026-08-10).
    """
    s = bs.analysis(_make_db(), "2026-03-01", "2026-03-31")["summary"]
    assert s["records"] == 0
    assert s["scale_rate"] is None
    assert s["scale_base_records"] == 0
    assert s["cancel_rate"] == 0.0


def test_previous_window_has_the_same_length() -> None:
    conn = _make_db()
    _add(conn, date="2026-03-05", total=1000)   # 이번 기간
    _add(conn, date="2026-02-20", total=500)    # 직전 기간
    _add(conn, date="2026-01-05", total=777)    # 그 이전 — 어느 쪽에도 안 들어간다

    d = bs.analysis(conn, "2026-03-01", "2026-03-31")
    assert d["range"]["days"] == 31
    # 3/1 시작이므로 직전 구간은 2/29 로 끝나는 31일 — 2026-02-28 이 마지막 날이다.
    assert d["previous"] == {"start": "2026-01-29", "end": "2026-02-28"}
    assert d["summary"]["records"] == 1
    assert d["summary"]["records_prev"] == 1
    assert d["summary"]["total_weight_prev"] == 500.0


def test_previous_is_none_for_open_ended_range() -> None:
    """'전체' 조회에는 같은 길이의 직전 구간이 없다 — 증감률을 꾸며내지 않는다."""
    conn = _make_db()
    _add(conn, date="2026-03-05")
    for start, end in [(None, None), ("2026-03-01", None), (None, "2026-03-31")]:
        d = bs.analysis(conn, start, end)
        assert d["previous"] is None
        assert d["range"]["days"] is None
        for key in ("records_prev", "total_weight_prev", "product_count_prev",
                    "material_count_prev", "scale_rate_prev", "cancel_rate_prev"):
            assert d["summary"][key] is None, key


def test_trend_is_ascending_by_month_and_skips_empty_months() -> None:
    conn = _make_db()
    _add(conn, date="2026-05-10")
    _add(conn, date="2026-03-02")
    _add(conn, date="2026-03-20", manual=1)
    _add(conn, date="2026-04-01", status="canceled")

    trend = bs.analysis(conn, "2026-01-01", "2026-12-31")["trend"]
    assert [t["bucket"] for t in trend] == ["2026-03", "2026-05"]
    assert trend[0]["records"] == 2
    assert trend[0]["manual_records"] == 1
    assert trend[0]["scale_rate"] == 50.0
    # 취소만 있는 달은 완료가 없으므로 추세 행이 생기지 않는다(위 목록에 2026-04 없음).


def test_trend_week_bucket() -> None:
    conn = _make_db()
    _add(conn, date="2026-03-02")
    _add(conn, date="2026-03-03")
    _add(conn, date="2026-03-16")
    trend = bs.analysis(conn, "2026-03-01", "2026-03-31", bucket="week")["trend"]
    assert len(trend) == 2
    assert all(t["bucket"].startswith("2026-W") for t in trend)
    assert trend[0]["records"] == 2
    # 알 수 없는 값은 month 로 떨어진다.
    assert bs.analysis(conn, None, None, bucket="fortnight")["bucket"] == "month"


def test_products_and_materials_sorting_and_share() -> None:
    conn = _make_db()
    _add(conn, date="2026-03-02", product="APB", total=1000,
         materials=[("PB", 600, 600, 1.0), ("톨루엔", 400, 400, 0)])
    _add(conn, date="2026-03-03", product="APB", total=1000,
         materials=[("PB", 600, 600, 1.0), ("톨루엔", 400, 400, 0)])
    _add(conn, date="2026-03-04", product="CSPB", total=2000,
         materials=[("PB", 1000, 1000, 1.0), ("자일렌", 1000, 1000, 0)])

    d = bs.analysis(conn, "2026-03-01", "2026-03-31")
    products = d["products"]
    assert [p["product_name"] for p in products] == ["APB", "CSPB"]  # 배치 수 내림차순
    assert products[0]["batch_count"] == 2
    assert round(sum(p["share"] for p in products)) == 100

    materials = d["materials"]
    assert materials[0]["material_name"] == "PB"  # 실제 사용량 내림차순
    assert materials[0]["total_actual"] == 2200.0
    assert materials[0]["usage_count"] == 3
    assert round(sum(m["share"] for m in materials)) == 100


def test_loss_comp_total_matches_detail_sum() -> None:
    conn = _make_db()
    _add(conn, date="2026-03-02", materials=[("PB", 600, 600, 1.0), ("SB", 400, 400, 0.5)])
    _add(conn, date="2026-03-03", materials=[("PB", 600, 600, 1.0)])
    # 취소분의 보정은 세지 않는다 — 투입되지 않았다.
    _add(conn, date="2026-03-04", status="canceled", materials=[("PB", 600, 600, 9.0)])

    d = bs.analysis(conn, "2026-03-01", "2026-03-31")
    assert d["summary"]["loss_comp_total_g"] == 2.5
    pb = next(m for m in d["materials"] if m["material_name"] == "PB")
    assert pb["loss_comp_g"] == 2.0


def test_quality_block_reuses_mistake_stats() -> None:
    conn = _make_db()
    _add(conn, date="2026-03-02", worker="김철수", manual=1)
    _add(conn, date="2026-03-03", worker="김철수")
    d = bs.analysis(conn, "2026-03-01", "2026-03-31")
    direct = bs.mistake_stats(conn, "2026-03-01", "2026-03-31")
    assert d["quality"] == direct
    assert d["quality"]["by_worker"][0]["manual_rate"] == 50.0


def test_partial_buckets_are_flagged() -> None:
    """양 끝 구간은 대개 잘려 있다 — 표시가 없으면 생산 급감으로 읽힌다."""
    conn = _make_db()
    _add(conn, date="2026-03-20")   # 3월 중간부터 시작 → 3월 버킷은 잘림
    _add(conn, date="2026-04-10")   # 4월은 통째로 들어간다
    _add(conn, date="2026-05-05")   # 5월 초까지만 → 5월 버킷도 잘림

    trend = bs.analysis(conn, "2026-03-15", "2026-05-10")["trend"]
    assert [t["bucket"] for t in trend] == ["2026-03", "2026-04", "2026-05"]
    assert [t["partial"] for t in trend] == [True, False, True]


def test_full_month_range_has_no_partial_bucket() -> None:
    conn = _make_db()
    _add(conn, date="2026-03-02")
    _add(conn, date="2026-03-31")
    trend = bs.analysis(conn, "2026-03-01", "2026-03-31")["trend"]
    assert [t["partial"] for t in trend] == [False]


def test_open_start_does_not_mark_first_bucket_partial() -> None:
    """'전체' 조회에서 첫 달이 잘렸다고 말하면 거짓이다 — 자료가 그때 시작했을 뿐이다."""
    conn = _make_db()
    _add(conn, date="2026-03-20")
    trend = bs.analysis(conn, None, "2026-03-31")["trend"]
    assert trend[0]["partial"] is False


def test_week_bucket_span_uses_monday_week() -> None:
    conn = _make_db()
    # 2026-03-02 는 월요일 — 그 주는 03-02 ~ 03-08.
    _add(conn, date="2026-03-04")
    full = bs.analysis(conn, "2026-03-02", "2026-03-08", bucket="week")["trend"]
    assert full[0]["partial"] is False
    cut = bs.analysis(conn, "2026-03-03", "2026-03-08", bucket="week")["trend"]
    assert cut[0]["partial"] is True


def test_workers_block_carries_production_stats() -> None:
    """작업자별 생산 실적 — 대시보드에 있던 표를 여기로 합쳤다(2026-08-08)."""
    conn = _make_db()
    _add(conn, date="2026-03-02", worker="김철수", product="APB", total=1000)
    _add(conn, date="2026-03-03", worker="김철수", product="CSPB", total=2000)
    _add(conn, date="2026-03-04", worker="이영희", product="APB", total=500)
    _add(conn, date="2026-03-05", worker="김철수", total=9999, status="canceled")
    _add(conn, date="2026-03-06", worker="김철수", total=8888, bulk=1)

    workers = bs.analysis(conn, "2026-03-01", "2026-03-31")["workers"]
    assert [w["worker"] for w in workers] == ["김철수", "이영희"]  # 완료 건수 내림차순
    kim = workers[0]
    assert kim["records"] == 2               # 취소·일괄 재생성분 제외
    assert kim["total_amount"] == 3000.0
    assert kim["product_count"] == 2


def test_workers_blank_name_is_labelled() -> None:
    """이름이 빈 기록도 표에서 사라지지 않는다 — 대시보드가 쓰던 표기를 유지한다."""
    conn = _make_db()
    _add(conn, date="2026-03-02", worker="")
    workers = bs.analysis(conn, "2026-03-01", "2026-03-31")["workers"]
    assert workers[0]["worker"] == "(미기록)"


def test_dashboard_and_analysis_count_the_same_records() -> None:
    """두 화면이 같은 기간에 다른 숫자를 내놓지 않는다.

    2026-08-08 이전에는 대시보드가 status != 'canceled'(=초안 포함), 배합 분석이
    status = 'completed' 로 세어 정의부터 어긋나 있었다.
    """
    conn = _make_db()
    _add(conn, date="2026-03-02", total=1000)
    _add(conn, date="2026-03-03", total=2000, status="draft")
    _add(conn, date="2026-03-04", total=4000, status="canceled")
    _add(conn, date="2026-03-05", total=8000, bulk=1)

    analysis = bs.analysis(conn, "2026-03-01", "2026-03-31")["summary"]
    # 대시보드 /summary 와 같은 SQL 조건.
    dash = conn.execute(
        "SELECT COUNT(*) AS cnt, COALESCE(SUM(total_amount), 0) AS w FROM blend_records "
        "WHERE status = 'completed' AND COALESCE(is_bulk_regenerated, 0) = 0 "
        "  AND work_date BETWEEN ? AND ?",
        ("2026-03-01", "2026-03-31"),
    ).fetchone()
    assert analysis["records"] == dash["cnt"] == 1
    assert analysis["total_weight_g"] == round(float(dash["w"]), 3) == 1000.0


# ── 저울 도입일 (2026-08-10, 운영 데이터에서 드러난 문제) ───────────────────
# manual_entry(저울 미사용) 표시는 저울이 붙은 뒤에야 기록된다. 그 전 기록은 전부
# 손으로 넣었지만 표시가 없어 '저울로 쟀다'와 구분되지 않는다. 그 구간까지 세면
# 계량률이 100% 로 부풀고(운영 실측 98.7%), 저울을 들여놓은 달에 오히려 급락하는
# 그래프가 되어 개선을 악화로 보여준다.

def _with_scale_since(conn, day):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS app_settings ("
        " key TEXT PRIMARY KEY, value TEXT, updated_by TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('scale_since', ?)",
        (day,),
    )
    conn.commit()


def test_scale_rate_ignores_records_before_the_scale_arrived():
    conn = _make_db()
    # 도입 전 4건: 전부 손으로 넣었지만 표시가 없다.
    for _ in range(4):
        _add(conn, date="2026-06-10")
    # 도입 후 4건 중 1건이 수동 입력.
    for _ in range(3):
        _add(conn, date="2026-07-15")
    _add(conn, date="2026-07-16", manual=1)

    before = bs.analysis(conn, None, None)["summary"]
    assert before["scale_rate"] == 87.5      # 8건 중 1건 → 부풀려진 값

    _with_scale_since(conn, "2026-07-10")
    after = bs.analysis(conn, None, None)
    assert after["scale_since"] == "2026-07-10"
    assert after["summary"]["scale_rate"] == 75.0    # 도입 후 4건 중 1건
    assert after["summary"]["scale_base_records"] == 4


def test_trend_leaves_pre_scale_buckets_without_a_rate():
    """0 도 100 도 아닌 '해당 없음' — 숫자를 채우면 그래프가 급락한다."""
    conn = _make_db()
    _add(conn, date="2026-05-10")
    _add(conn, date="2026-06-10")
    _add(conn, date="2026-07-15", manual=1)
    _add(conn, date="2026-07-16")
    _with_scale_since(conn, "2026-07-10")

    trend = {t["bucket"]: t for t in bs.analysis(conn, "2026-01-01", "2026-12-31")["trend"]}
    assert trend["2026-05"]["scale_rate"] is None
    assert trend["2026-06"]["scale_rate"] is None
    assert trend["2026-07"]["scale_rate"] == 50.0


def test_bucket_straddling_the_scale_date_counts_only_after_it():
    """도입일이 낀 달을 통째로 세면 그 달만 부풀려진다(운영에서 7월 86.8 → 78.9)."""
    conn = _make_db()
    for _ in range(6):                       # 7/1~7/9: 표시 없음
        _add(conn, date="2026-07-05")
    _add(conn, date="2026-07-20", manual=1)  # 도입 후 2건 중 1건 수동
    _add(conn, date="2026-07-21")
    _with_scale_since(conn, "2026-07-10")

    july = bs.analysis(conn, "2026-07-01", "2026-07-31")["trend"][0]
    assert july["records"] == 8              # 생산량·건수는 그대로 전부 센다
    assert july["scale_base_records"] == 2   # 계량률 표본만 도입 후
    assert july["scale_rate"] == 50.0


def test_without_the_setting_nothing_changes():
    """저울을 처음부터 쓴 현장도 있다 — 설정이 없으면 종전 동작 그대로."""
    conn = _make_db()
    _add(conn, date="2026-07-15")
    _add(conn, date="2026-07-16", manual=1)
    summary = bs.analysis(conn, None, None)["summary"]
    assert summary["scale_rate"] == 50.0
    assert summary["scale_base_records"] == 2


def test_scale_rate_is_none_when_no_record_falls_after_the_date():
    conn = _make_db()
    _add(conn, date="2026-06-10")
    _with_scale_since(conn, "2026-07-10")
    summary = bs.analysis(conn, None, None)["summary"]
    assert summary["scale_rate"] is None      # 0.0 이 아니라 '해당 없음'
    assert summary["scale_base_records"] == 0
