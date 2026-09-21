"""통계 제외는 모든 통계 소비자에서 똑같이 빠진다(2026-09-21 현장 지적).

운영 APB 2026-08-10(439.5, 사유 '폐기')은 '통계 제외'인데도 PB 연계 산점도에 찍히고
추세선·상관 문장에까지 들어갔다. 사용자 말: "통계 제외가 다 같이 공유되어야 맞는데
그게 안 되는 건 놓친 것."

규칙 세 갈래를 여기서 못 박는다.
  통계   평균·σ·판정·이상 목록·추세·기간 집계·상관·구간표·요약 카드의 '최근값' —
         제외된 측정은 절대 들어가지 않는다.
  기록   측정 표·기록 조회 상세·PB LOT 역방향 조회 — 값은 남고 제외 표식·사유가 붙는다.
  등록수  "이 LOT 을 재기는 했나"(등록 대기열·트레이 알림) — 제외돼도 '쟀다'로 센다.

배합 화면 LOT 경고(product_lot_alert)만 예외다 — 실제로 잰 값이 중요해 제외된 측정도
보여 주되, 통계에서 빠졌다는 사실(excluded·exclude_reason)을 함께 준다.
"""

from __future__ import annotations

import importlib
import io
import uuid

from openpyxl import load_workbook


# ── 공통 헬퍼 ────────────────────────────────────────────────────────
def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _csrf(client) -> dict:
    token = client.cookies.get("csrftoken")
    return {"x-csrftoken": token} if token else {}


def _login_admin(client):
    client.get("/api/blend/records")  # csrf 쿠키
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200, res.text


def _digits8() -> str:
    return f"{uuid.uuid4().int % 10**8:08d}"


def _seed_recipe(client, product, materials=("PB", "MEK")):
    lines = [
        "반제품명\t" + "\t".join(materials),
        product + "\t" + "\t".join(["50"] * len(materials)),
    ]
    res = client.post(
        "/api/recipes/import",
        json={"raw_text": "\n".join(lines), "force": True},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text


def _make_product(client, code, **limits) -> int:
    _seed_recipe(client, code)
    body = {"code": code, "name": code}
    body.update(limits)
    created = client.post("/api/viscosity/products", json=body, headers=_csrf(client))
    assert created.status_code in (200, 201), created.text
    return int(created.json()["id"])


def _pb_product_id(client) -> int:
    listed = client.get("/api/viscosity/products").json()["items"]
    for item in listed:
        if item["code"] == "PB":
            return int(item["id"])
    return _make_product(client, "PB")


def _add_reading(client, product_id, lot, value, date, *, material_lot=None) -> int:
    body = {
        "product_id": product_id,
        "lot_no": lot,
        "viscosity": value,
        "measured_date": date,
    }
    if material_lot is not None:
        body["material_lot"] = material_lot
    res = client.post("/api/viscosity/readings", json=body, headers=_csrf(client))
    assert res.status_code == 200, res.text
    found = next((r for r in res.json()["readings"] if r["lot_no"] == lot), None)
    assert found, f"방금 등록한 측정이 목록에 없다: {lot}"
    return int(found["id"])


def _exclude(client, reading_id, reason="폐기"):
    res = client.post(
        f"/api/viscosity/readings/{reading_id}/exclude",
        json={"reason": reason},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text


def _blend_record(client, product, work_date, details) -> dict:
    worker = "제외일관" + uuid.uuid4().hex[:5]
    client.post("/api/workers", json={"name": worker}, headers=_csrf(client))
    client.post("/api/blend/session/login", json={"worker": worker}, headers=_csrf(client))
    res = client.post(
        "/api/blend/records",
        json={
            "product_name": product, "worker": worker, "work_date": work_date,
            "total_amount": 100, "scale": "M-65",
            "details": [
                {"material_name": name, "ratio": 100 / len(details),
                 "theory_amount": 100 / len(details),
                 "actual_amount": 100 / len(details), "material_lot": lot}
                for name, lot in details
            ],
        },
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text
    return res.json()


# ── 1. PB 연계 — 표에는 남고 통계에는 빠진다 ─────────────────────────
def test_pb_link_counts_records_while_the_pb_side_drops_excluded():
    """pb_link 는 기록 커버리지라 제외된 이 반제품 측정도 센다(의도적 결정).

    반대로 상대편 PB 측정이 제외되면 연계 자체가 끊겨 pb_excluded 로 분류된다 —
    제외된 PB 점도가 상관·구간 평균에 들어가면 안 되기 때문이다.
    """
    client = _client()
    _login_admin(client)
    pb_id = _pb_product_id(client)
    good_lot, dropped_pb_lot = _digits8(), _digits8()
    _add_reading(client, pb_id, f"PB{good_lot}", 49.0, "2026-09-01")
    dropped = _add_reading(client, pb_id, f"PB{dropped_pb_lot}", 52.0, "2026-09-02")
    _exclude(client, dropped, "PB 재측정 예정")

    code = "EXC" + uuid.uuid4().hex[:5].upper()
    pid = _make_product(client, code)
    _add_reading(client, pid, f"{code}-1", 300.0, "2026-09-03", material_lot=good_lot)
    rid = _add_reading(client, pid, f"{code}-2", 439.5, "2026-09-04", material_lot=good_lot)
    _add_reading(client, pid, f"{code}-3", 310.0, "2026-09-05", material_lot=dropped_pb_lot)
    _exclude(client, rid)  # 이 반제품 쪽 측정 하나를 폐기

    detail = client.get(f"/api/viscosity/products/{pid}").json()
    link = detail["pb_link"]
    assert link["total"] == 3
    # 제외된 바인더 측정도 matched 로 센다 — 그림 아래 표에 그대로 남기 때문이다.
    assert link["matched"] == 2
    assert link["pb_excluded"] == 1
    assert (
        link["matched"] + link["no_lot"] + link["lot_unreadable"]
        + link["pb_missing"] + link["pb_excluded"]
    ) == link["total"]

    readings = {r["lot_no"]: r for r in detail["readings"]}
    # 제외된 측정은 판정 대신 status='excluded' 와 사유를 달고 목록에 남는다.
    assert readings[f"{code}-2"]["status"] == "excluded"
    assert readings[f"{code}-2"]["exclude_reason"] == "폐기"
    assert readings[f"{code}-2"]["source_pb_viscosity"] == 49.0
    # 제외된 PB 를 쓴 측정은 연계 좌표 자체가 없다.
    assert readings[f"{code}-3"]["source_pb_viscosity"] is None
    # 통계는 제외를 뺀 표본이다(300 한 건).
    assert detail["stats"]["n"] == 2
    assert detail["counts"]["excluded"] == 1


# ── 2. 요약 카드의 '최근값' ──────────────────────────────────────────
def test_overview_latest_skips_an_excluded_reading():
    client = _client()
    _login_admin(client)
    code = "EXL" + uuid.uuid4().hex[:5].upper()
    pid = _make_product(client, code)
    _add_reading(client, pid, f"{code}-1", 300.0, "2026-09-01")
    last = _add_reading(client, pid, f"{code}-2", 999.0, "2026-09-09")

    before = next(
        it for it in client.get("/api/viscosity/overview").json()["items"]
        if it["id"] == pid
    )
    assert before["latest_value"] == 999.0

    _exclude(client, last)
    after = next(
        it for it in client.get("/api/viscosity/overview").json()["items"]
        if it["id"] == pid
    )
    # 건수·평균이 이미 유효 표본이므로 최근값도 같은 표본을 말해야 한다.
    assert after["latest_value"] == 300.0
    assert after["latest_date"] == "2026-09-01"
    assert after["last_status"] != "excluded"
    assert after["count"] == 1


# ── 3. 트레이 알림의 '마지막 측정' ───────────────────────────────────
def test_reminder_latest_value_skips_excluded_but_pending_still_counts_it():
    from src.db import get_connection
    from src.services import viscosity_service as vs

    client = _client()
    _login_admin(client)
    code = "EXR" + uuid.uuid4().hex[:5].upper()
    pid = _make_product(client, code)
    res = client.patch(
        f"/api/viscosity/products/{pid}",
        json={"name": code, "remind_daily": True},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text

    _add_reading(client, pid, f"{code}-OLD", 300.0, "2026-09-01")
    # 이 LOT 은 배합 기록에 연결해 등록한다 — 등록 여부 판정에 쓰이는 경로.
    record = _blend_record(client, code, "2026-09-02", [("PB", "26090101")])
    reg = client.post(
        f"/api/blend/records/{record['id']}/viscosity",
        json={"viscosity": 999.0, "product_id": pid},
        headers=_csrf(client),
    )
    assert reg.status_code == 200, reg.text
    reading_id = next(
        r["id"] for r in client.get(f"/api/viscosity/products/{pid}").json()["readings"]
        if r["lot_no"] == record["product_lot"]
    )
    # 알림 대상이 되도록 배합 하나를 더 만든다(측정 안 한 LOT = pending).
    _blend_record(client, code, "2026-09-03", [("PB", "26090201")])

    with get_connection() as conn:
        before = [
            it for it in vs.daily_reading_reminders(conn, target_date="2026-09-20")
            if it["id"] == pid
        ]
    assert before and before[0]["latest_value"] == 999.0
    pending_before = before[0]["pending_count"]

    _exclude(client, reading_id)
    with get_connection() as conn:
        after = [
            it for it in vs.daily_reading_reminders(conn, target_date="2026-09-20")
            if it["id"] == pid
        ]
    assert after, "제외는 알림 대상 자체를 없애지 않는다"
    # 마지막 측정은 통계 숫자 — 폐기한 값을 현장에 최근값으로 알리지 않는다.
    assert after[0]["latest_value"] == 300.0
    # 반면 '쟀는가'는 그대로다 — 제외했다고 그 LOT 이 다시 미측정이 되지는 않는다.
    assert after[0]["pending_count"] == pending_before
    lots = {lot["blend_record_id"] for lot in after[0]["pending_lots"]}
    assert record["id"] not in lots


# ── 4. 기록 화면은 값을 남기고 표식을 붙인다 ─────────────────────────
def test_blend_record_detail_marks_an_excluded_reading():
    client = _client()
    _login_admin(client)
    code = "EXB" + uuid.uuid4().hex[:5].upper()
    pid = _make_product(client, code)
    record = _blend_record(client, code, "2026-09-04", [("PB", "26090301")])
    reg = client.post(
        f"/api/blend/records/{record['id']}/viscosity",
        json={"viscosity": 410.0, "product_id": pid},
        headers=_csrf(client),
    )
    assert reg.status_code == 200, reg.text
    reading_id = next(
        r["id"] for r in client.get(f"/api/viscosity/products/{pid}").json()["readings"]
        if r["lot_no"] == record["product_lot"]
    )

    before = client.get(f"/api/blend/records/{record['id']}").json()["viscosity"]
    assert before[0]["excluded"] is False
    assert before[0]["exclude_reason"] is None

    _exclude(client, reading_id, "시료 오염")
    after = client.get(f"/api/blend/records/{record['id']}").json()["viscosity"]
    # 값은 그대로 남는다(기록) — 다만 제외 표식과 사유가 붙는다.
    assert after[0]["viscosity"] == 410.0
    assert after[0]["excluded"] is True
    assert after[0]["exclude_reason"] == "시료 오염"


def test_pb_lot_detail_shows_an_excluded_reading_without_a_verdict():
    client = _client()
    _login_admin(client)
    pb_id = _pb_product_id(client)
    digits = _digits8()
    pb_lot = f"PB{digits}"
    _add_reading(client, pb_id, pb_lot, 49.5, "2026-09-06")

    code = "EXD" + uuid.uuid4().hex[:5].upper()
    pid = _make_product(client, code, upper_limit=320.0)
    _add_reading(client, pid, f"{code}-1", 300.0, "2026-09-07", material_lot=digits)
    rid = _add_reading(client, pid, f"{code}-2", 330.0, "2026-09-08", material_lot=digits)
    _exclude(client, rid, "폐기")

    data = client.get(f"/api/viscosity/pb-lots/{pb_lot}").json()
    mine = {it["lot_no"]: it for it in data["items"] if it["product_code"] == code}
    assert set(mine) == {f"{code}-1", f"{code}-2"}, "제외 측정도 목록에는 남는다"
    # 규격 상한을 넘었지만 '이상'이 아니라 '제외'다 — 통계에서 뺀 값에 판정을 붙이지 않는다.
    assert mine[f"{code}-2"]["status"] == "excluded"
    assert mine[f"{code}-2"]["excluded"] is True
    assert mine[f"{code}-2"]["exclude_reason"] == "폐기"
    assert mine[f"{code}-2"]["reasons"] == []
    assert mine[f"{code}-1"]["status"] in ("normal", "warn")


# ── 5. 배합 화면 LOT 경고 — 유일한 예외 ──────────────────────────────
def test_lot_alert_keeps_an_excluded_value_but_says_it_is_excluded():
    client = _client()
    _login_admin(client)
    pb_id = _pb_product_id(client)
    lot = f"PB{_digits8()}"
    reading_id = _add_reading(client, pb_id, lot, 44.5, "2026-09-09")

    before = client.get(
        "/api/blend/product-lot-viscosity", params={"name": "PB", "lot": lot}
    ).json()
    assert before["found"] is True and before["level"] == "warn"
    assert before["excluded"] is False

    _exclude(client, reading_id, "재측정 예정")
    after = client.get(
        "/api/blend/product-lot-viscosity", params={"name": "PB", "lot": lot}
    ).json()
    # 실제로 잰 값이 중요하므로 값·경고는 그대로 둔다.
    assert after["found"] is True
    assert after["viscosity"] == 44.5
    assert after["level"] == "warn"
    assert after["message"] == before["message"]
    # 다만 통계에서 빠졌다는 사실을 함께 알린다.
    assert after["excluded"] is True
    assert after["exclude_reason"] == "재측정 예정"


# ── 6. Excel 은 제외를 전용 열로 드러낸다 ────────────────────────────
def test_excel_exports_carry_the_exclusion_state_as_its_own_column():
    client = _client()
    _login_admin(client)
    code = "EXX" + uuid.uuid4().hex[:5].upper()
    pid = _make_product(client, code)
    _add_reading(client, pid, f"{code}-1", 300.0, "2026-09-10")
    rid = _add_reading(client, pid, f"{code}-2", 999.0, "2026-09-11")
    _exclude(client, rid, "폐기")

    res = client.get(f"/api/viscosity/products/{pid}/export")
    assert res.status_code == 200, res.text
    ws = load_workbook(io.BytesIO(res.content))["측정 원본"]
    header = [c.value for c in ws[1]]
    assert "통계 제외" in header and "제외 사유" in header
    col_lot = header.index("LOT")
    col_flag = header.index("통계 제외")
    col_reason = header.index("제외 사유")
    col_verdict = header.index("판정")
    rows = {r[col_lot]: r for r in ws.iter_rows(min_row=2, values_only=True)}
    assert rows[f"{code}-2"][col_flag] == "제외"
    assert rows[f"{code}-2"][col_reason] == "폐기"
    assert rows[f"{code}-2"][col_verdict] == "제외"
    assert rows[f"{code}-1"][col_flag] in (None, "")

    res_all = client.get("/api/viscosity/export-all")
    assert res_all.status_code == 200, res_all.text
    ws_all = load_workbook(io.BytesIO(res_all.content))["전체 측정"]
    header_all = [c.value for c in ws_all[1]]
    assert "통계 제외" in header_all and "제외 사유" in header_all
    a_lot = header_all.index("LOT")
    a_flag = header_all.index("통계 제외")
    a_verdict = header_all.index("판정")
    all_rows = {r[a_lot]: r for r in ws_all.iter_rows(min_row=2, values_only=True)}
    assert all_rows[f"{code}-2"][a_flag] == "제외"
    # 종전에는 판정 칸이 빈칸이라 제외된 값이 정상 기록과 구별되지 않았다.
    assert all_rows[f"{code}-2"][a_verdict] == "제외"


# ── 7. 등록 여부는 제외와 무관하다 ───────────────────────────────────
def test_registration_state_still_counts_an_excluded_reading_as_measured():
    client = _client()
    _login_admin(client)
    code = "EXQ" + uuid.uuid4().hex[:5].upper()
    pid = _make_product(client, code)
    record = _blend_record(client, code, "2026-09-12", [("PB", "26091101")])
    client.post(
        f"/api/blend/records/{record['id']}/viscosity",
        json={"viscosity": 305.0, "product_id": pid},
        headers=_csrf(client),
    ).raise_for_status()
    reading_id = next(
        r["id"] for r in client.get(f"/api/viscosity/products/{pid}").json()["readings"]
        if r["lot_no"] == record["product_lot"]
    )
    _exclude(client, reading_id, "폐기")

    queue = client.get(f"/api/viscosity/products/{pid}/blend-records").json()
    item = next(it for it in queue["items"] if it["id"] == record["id"])
    # '쟀는가'는 그대로 예 — 제외는 통계 이야기이지 등록 이야기가 아니다.
    assert item["registered"] is True
    assert queue["unregistered_total"] == 0
    # 배합 기록 목록의 점도 상태도 같은 규칙을 쓴다.
    rows = client.get("/api/blend/records", params={"q": record["product_lot"]}).json()
    row = next(r for r in rows["items"] if r["id"] == record["id"])
    assert row["viscosity_state"] == "done"
