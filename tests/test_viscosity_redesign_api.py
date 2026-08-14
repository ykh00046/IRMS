"""점도 화면 재설계 서버 계약(2026-08-13) 검증.

  1. GET /viscosity/products/{id}/blend-records — 등록 패널용 일괄 목록.
     등록 여부(registered)를 서버가 계산하고, '미등록만' 필터가 최근 N건 슬라이스가
     아니라 전체에서 걸러진다(종전: 잘라온 20건에만 필터 → 거짓 빈 목록).
  2. GET /viscosity/blend-records/{id}/used-pb — 사용한 PB 감지 미리보기.
     PB 가 첫 계량 자재가 아니어도 자재명으로 찾는다(종전: 무조건 첫 행).
  3. POST /blend/records/{id}/viscosity — 감지 결과 저장 + material_lot 수동 보정.
  4. summarize_periods — 전기 대비(mean_delta)가 이상 측정을 뺀 평균 기준.
  5. analyze_product 응답의 pb_link 요약(연계 실패 무증상 방지).
"""

from __future__ import annotations

import importlib
import uuid


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _csrf(client):
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _seed_recipe(client, prod, materials=("PB", "MEK")):
    """반제품 레시피 — materials 순서대로 계량 행이 생긴다(PB 위치 제어용)."""
    lines = [f"반제품명\t" + "\t".join(materials)]
    lines.append(prod + "\t" + "\t".join(["50"] * len(materials)))
    res = client.post(
        "/api/recipes/import",
        json={"raw_text": "\n".join(lines), "force": True},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text


def _make_product(client, code):
    created = client.post(
        "/api/viscosity/products",
        json={"code": code, "name": code},
        headers=_csrf(client),
    )
    assert created.status_code in (200, 201), created.text
    listed = client.get("/api/viscosity/products").json()["items"]
    return next(p["id"] for p in listed if p["code"] == code)


def _make_blend(client, prod, work_date, details, reactor=None):
    """details: [(자재명, LOT)] — 계량 순서 그대로."""
    worker = "점도재설계" + uuid.uuid4().hex[:4]
    client.post("/api/workers", json={"name": worker}, headers=_csrf(client))
    client.post(
        "/api/blend/session/login", json={"worker": worker}, headers=_csrf(client)
    )
    body = {
        "product_name": prod, "worker": worker, "work_date": work_date,
        "total_amount": 100, "scale": "M-65",
        "details": [
            {"material_name": name, "ratio": 100 / len(details),
             "theory_amount": 100 / len(details),
             "actual_amount": 100 / len(details), "material_lot": lot}
            for name, lot in details
        ],
    }
    if reactor is not None:
        body["reactor"] = reactor
    res = client.post("/api/blend/records", json=body, headers=_csrf(client))
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _login_admin(client):
    client.get("/api/blend/records")  # csrf 쿠키
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200, res.text


# ── 1. 등록 패널용 배합 기록 일괄 목록 ─────────────────────────────────────


def test_blend_records_endpoint_reports_registration_and_filters_serverside():
    client = _client()
    _login_admin(client)
    prod = "VRD" + uuid.uuid4().hex[:5].upper()
    _seed_recipe(client, prod)
    pid = _make_product(client, prod)

    old_id = _make_blend(client, prod, "2026-08-01", [("PB", "26073101"), ("MEK", "M1")])
    new_id = _make_blend(client, prod, "2026-08-12", [("PB", "26081101"), ("MEK", "M2")])
    # 최신 기록에만 점도 등록.
    reg = client.post(
        f"/api/blend/records/{new_id}/viscosity",
        json={"viscosity": 400.0, "product_id": pid},
        headers=_csrf(client),
    )
    assert reg.status_code == 200, reg.text

    res = client.get(f"/api/viscosity/products/{pid}/blend-records")
    assert res.status_code == 200, res.text
    data = res.json()
    by_id = {it["id"]: it for it in data["items"]}
    assert by_id[new_id]["registered"] is True
    assert by_id[new_id]["viscosity"] == 400.0
    assert by_id[old_id]["registered"] is False
    assert data["total"] == 2
    assert data["unregistered_total"] == 1

    # 거짓 빈 목록 회귀 방지: limit=1 이면 최신(등록됨)만 잘리지만, unregistered=1 은
    # 서버가 전체에서 거르므로 더 오래된 미등록 기록이 나온다.
    res = client.get(
        f"/api/viscosity/products/{pid}/blend-records",
        params={"unregistered": "1", "limit": 1},
    )
    items = res.json()["items"]
    assert [it["id"] for it in items] == [old_id]

    # 검색(q) — LOT/작업자 부분 일치.
    lot = by_id[old_id]["product_lot"]
    res = client.get(
        f"/api/viscosity/products/{pid}/blend-records", params={"q": lot[-4:]}
    )
    assert any(it["id"] == old_id for it in res.json()["items"])


def test_blend_records_endpoint_filters_by_reactor_serverside():
    """반응기 필터 — 클라이언트에서 거르면 limit 밖의 반응기별 옛 기록을 놓친다."""
    client = _client()
    _login_admin(client)
    prod = "VRF" + uuid.uuid4().hex[:5].upper()
    _seed_recipe(client, prod)
    pid = _make_product(client, prod)
    plain_id = _make_blend(client, prod, "2026-08-10", [("PB", "26080901"), ("MEK", "M4")])
    r2_id = _make_blend(
        client, prod, "2026-08-11", [("PB", "26081001"), ("MEK", "M5")], reactor=2
    )

    def ids(params):
        res = client.get(
            f"/api/viscosity/products/{pid}/blend-records", params=params
        )
        assert res.status_code == 200, res.text
        return {it["id"] for it in res.json()["items"]}

    assert ids({"reactor": "2"}) == {r2_id}
    assert ids({"reactor": "none"}) == {plain_id}
    assert ids({}) == {plain_id, r2_id}


# ── 2·3. 사용한 PB 감지 — 첫 행 가정 제거 + 수동 보정 ──────────────────────


def test_used_pb_detected_by_material_name_not_first_row():
    client = _client()
    _login_admin(client)
    prod = "VRB" + uuid.uuid4().hex[:5].upper()
    # PB 가 **두 번째** 계량 자재인 레시피.
    _seed_recipe(client, prod, materials=("MEK", "PB"))
    pid = _make_product(client, prod)
    rid = _make_blend(
        client, prod, "2026-08-12", [("MEK", "MK001"), ("PB", "26081201")]
    )

    preview = client.get(f"/api/viscosity/blend-records/{rid}/used-pb").json()
    assert preview["lot"] == "26081201"        # 첫 행(MEK)이 아니라 PB 행
    assert preview["method"] == "matched"

    reg = client.post(
        f"/api/blend/records/{rid}/viscosity",
        json={"viscosity": 390.0, "product_id": pid},
        headers=_csrf(client),
    )
    assert reg.status_code == 200, reg.text
    assert reg.json()["used_pb"] == {"lot": "26081201", "method": "matched"}

    detail = client.get(f"/api/viscosity/products/{pid}").json()
    reading = next(r for r in detail["readings"] if r["viscosity"] == 390.0)
    assert reading["material_lot"] == "26081201"


def test_used_pb_none_for_recipes_without_pb_and_manual_override():
    client = _client()
    _login_admin(client)
    prod = "VRC" + uuid.uuid4().hex[:5].upper()
    # PB 행이 없는 레시피 — 연계 대상이 아니므로 감지 결과도 '없음'이어야 한다.
    # (종전의 첫 계량 자재 폴백은 PB 미사용 품목 전체 — PB 자신까지 — 에 "추정"
    # 경고를 띄우는 소음이었다, 2026-08-14 현장 지적.)
    _seed_recipe(client, prod, materials=("MEK", "TOL"))
    pid = _make_product(client, prod)
    rid = _make_blend(client, prod, "2026-08-12", [("MEK", "MK002"), ("TOL", "TL001")])

    preview = client.get(f"/api/viscosity/blend-records/{rid}/used-pb").json()
    assert preview["method"] == "none"
    assert preview["lot"] is None

    # 화면이 보정한 값을 보내면 그대로 저장된다(method=manual).
    reg = client.post(
        f"/api/blend/records/{rid}/viscosity",
        json={"viscosity": 380.0, "product_id": pid, "material_lot": "26080999"},
        headers=_csrf(client),
    )
    assert reg.status_code == 200, reg.text
    assert reg.json()["used_pb"] == {"lot": "26080999", "method": "manual"}
    detail = client.get(f"/api/viscosity/products/{pid}").json()
    reading = next(r for r in detail["readings"] if r["viscosity"] == 380.0)
    assert reading["material_lot"] == "26080999"


# ── 4. 전기 대비 — 이상 측정 제외 기준 ─────────────────────────────────────


def test_period_delta_excludes_anomalies():
    from src.services.viscosity_service import summarize_periods

    readings = [
        {"measured_date": "2026-07-20", "viscosity": 399.0, "status": "normal"},
        {"measured_date": "2026-07-24", "viscosity": 128.4, "status": "anomaly"},
        {"measured_date": "2026-07-27", "viscosity": 398.4, "status": "normal"},
    ]
    periods = summarize_periods(readings, "day")
    by_key = {p["period"]: p for p in periods}
    # 이상만 있는 구간은 비교 기준을 갱신하지 않는다 → 7/27 은 7/20 대비 -0.6,
    # 종전처럼 128.4 대비 +270 이 아니다.
    assert by_key["2026-07-24"]["mean_delta"] is None or abs(
        by_key["2026-07-24"]["mean_delta"]
    ) < 1000  # 이상 구간 자체는 비교 없음(None)
    assert by_key["2026-07-24"]["mean_delta"] is None
    assert by_key["2026-07-27"]["mean_delta"] == -0.6


# ── 5. pb_link 요약 ────────────────────────────────────────────────────────


def test_analyze_response_carries_pb_link_summary():
    client = _client()
    _login_admin(client)
    # PB 제품 + PB 점도 1건.
    pb_pid = None
    listed = client.get("/api/viscosity/products").json()["items"]
    for p in listed:
        if p["code"] == "PB":
            pb_pid = p["id"]
            break
    if pb_pid is None:
        pb_pid = _make_product(client, "PB")
    pb_prod = "PB"
    _seed_recipe(client, pb_prod)
    pb_rid = _make_blend(client, pb_prod, "2026-08-11", [("PB", "26081101"), ("MEK", "M9")])
    client.post(
        f"/api/blend/records/{pb_rid}/viscosity",
        json={"viscosity": 49.5, "product_id": pb_pid},
        headers=_csrf(client),
    )

    prod = "VRE" + uuid.uuid4().hex[:5].upper()
    _seed_recipe(client, prod)
    pid = _make_product(client, prod)
    rid = _make_blend(client, prod, "2026-08-12", [("PB", "26081101"), ("MEK", "M3")])
    client.post(
        f"/api/blend/records/{rid}/viscosity",
        json={"viscosity": 400.0, "product_id": pid},
        headers=_csrf(client),
    )

    detail = client.get(f"/api/viscosity/products/{pid}").json()
    link = detail["pb_link"]
    assert link["source_code"] == "PB"
    assert link["source_exists"] is True
    assert link["readings_with_lot"] >= 1
    assert link["matched"] >= 1


# ── 6. 측정값 정정(책임자) — 오입력을 삭제+재등록 없이 고친다 ──────────────


def test_reading_correction_updates_value_and_leaves_audit():
    from src.db import get_connection

    client = _client()
    _login_admin(client)
    prod = "VRG" + uuid.uuid4().hex[:5].upper()
    _seed_recipe(client, prod)
    pid = _make_product(client, prod)
    rid = _make_blend(client, prod, "2026-08-13", [("PB", "26081301"), ("MEK", "M6")])
    reg = client.post(
        f"/api/blend/records/{rid}/viscosity",
        json={"viscosity": 4000.0, "product_id": pid},   # 0 하나 더 친 오입력
        headers=_csrf(client),
    )
    assert reg.status_code == 200, reg.text
    detail = client.get(f"/api/viscosity/products/{pid}").json()
    reading = next(r for r in detail["readings"] if r["viscosity"] == 4000.0)

    res = client.put(
        f"/api/viscosity/readings/{reading['id']}",
        json={"viscosity": 400.0, "reason": "입력 오타 정정"},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok", "old": 4000.0, "new": 400.0}

    detail = client.get(f"/api/viscosity/products/{pid}").json()
    values = [r["viscosity"] for r in detail["readings"]]
    assert 400.0 in values and 4000.0 not in values

    with get_connection() as conn:
        row = conn.execute(
            "SELECT details_json FROM audit_logs "
            "WHERE action = 'viscosity_reading_corrected' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    import json as _json

    logged = _json.loads(row["details_json"])
    assert logged["old"] == 4000.0
    assert logged["new"] == 400.0
    assert logged["reason"] == "입력 오타 정정"


def test_reading_correction_grace_window_and_validation():
    """정정 권한 2단계(2026-08-14 '10분 유예'): 등록 후 10분 이내=현장 누구나,
    이후=책임자만. 값·사유 검증은 공통."""
    from src.db import get_connection

    client = _client()
    _login_admin(client)
    prod = "VRH" + uuid.uuid4().hex[:5].upper()
    _seed_recipe(client, prod)
    pid = _make_product(client, prod)
    rid = _make_blend(client, prod, "2026-08-13", [("PB", "26081302"), ("MEK", "M7")])
    client.post(
        f"/api/blend/records/{rid}/viscosity",
        json={"viscosity": 390.0, "product_id": pid},
        headers=_csrf(client),
    )
    detail = client.get(f"/api/viscosity/products/{pid}").json()
    reading_id = detail["readings"][0]["id"]

    assert client.put(
        f"/api/viscosity/readings/{reading_id}",
        json={"viscosity": "abc", "reason": "정정"},
        headers=_csrf(client),
    ).status_code == 400
    assert client.put(
        f"/api/viscosity/readings/{reading_id}",
        json={"viscosity": 380.0, "reason": ""},
        headers=_csrf(client),
    ).status_code == 400

    # 등록 직후(유예창 안) — 비로그인 현장도 정정 가능. 감사에 grace_window 표기.
    anon = _client()
    anon.get("/api/viscosity/products")
    res = anon.put(
        f"/api/viscosity/readings/{reading_id}",
        json={"viscosity": 385.0, "reason": "등록 직후 오타 정정"},
        headers=_csrf(anon),
    )
    assert res.status_code == 200, res.text
    with get_connection() as conn:
        row = conn.execute(
            "SELECT details_json FROM audit_logs "
            "WHERE action = 'viscosity_reading_corrected' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    import json as _json

    assert _json.loads(row["details_json"])["grace_window"] is True

    # 유예창이 지나면(등록 시각을 20분 전으로 되돌림) 현장은 403 + 책임자 안내.
    with get_connection() as conn:
        conn.execute(
            "UPDATE viscosity_readings SET created_at = '2026-08-13T00:00:00Z' "
            "WHERE id = ?", (reading_id,),
        )
        conn.commit()
    res = anon.put(
        f"/api/viscosity/readings/{reading_id}",
        json={"viscosity": 380.0, "reason": "늦은 정정"},
        headers=_csrf(anon),
    )
    assert res.status_code == 403, res.text
    assert "책임자" in res.json()["detail"]

    # 책임자는 유예창과 무관하게 정정 가능.
    res = client.put(
        f"/api/viscosity/readings/{reading_id}",
        json={"viscosity": 380.0, "reason": "책임자 정정"},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text


# ── 6. 측정 불가 기록(viscosity_skips, 2026-08-14) ────────────────────────
# 시료 소진·출하 등으로 점도를 잴 수 없던 배합을 사유와 함께 종결한다.
# 기록=현장 누구나(사유 필수+감사), 되돌리기=책임자만. 기록되면 알림·미등록
# 집계에서 등록과 동일하게 빠진다.


def _set_reminder_since(value):
    """정리 기준일 저장. value=None 이면 삭제(다른 테스트 파일의 '기준일 없음'
    전제를 오염시키지 않도록 테스트가 끝나면 반드시 지운다)."""
    from src.db import get_connection
    from src.services.settings_service import VISCOSITY_REMINDER_SINCE_KEY

    with get_connection() as conn:
        if value is None:
            conn.execute(
                "DELETE FROM app_settings WHERE key = ?",
                (VISCOSITY_REMINDER_SINCE_KEY,),
            )
        else:
            conn.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (VISCOSITY_REMINDER_SINCE_KEY, value, "2026-08-14T00:00:00Z"),
            )
        conn.commit()


def _due_codes(client, target_date):
    from fastapi.testclient import TestClient

    internal = TestClient(client.app, client=("192.168.11.108", 50000))
    res = internal.get(
        "/api/public/viscosity-reminders/due", params={"target_date": target_date}
    )
    assert res.status_code == 200, res.text
    return {item["code"] for item in res.json()["items"]}


def test_skip_record_silences_reminder_and_unregistered_queue():
    import json as _json
    from datetime import date, timedelta

    from src.db import get_connection

    client = _client()
    _login_admin(client)
    prod = "VSK" + uuid.uuid4().hex[:5].upper()
    _seed_recipe(client, prod)
    pid = _make_product(client, prod)
    # 매일 알림 대상으로 — 생성이 아니라 수정에서 켠다(라우트 규약).
    upd = client.patch(
        f"/api/viscosity/products/{pid}",
        json={"name": prod, "remind_daily": True, "is_active": True},
        headers=_csrf(client),
    )
    assert upd.status_code == 200, upd.text
    today = date.today()
    _set_reminder_since((today - timedelta(days=5)).isoformat())
    try:
        yesterday = (today - timedelta(days=1)).isoformat()
        rid = _make_blend(client, prod, yesterday, [("PB", "26081310"), ("MEK", "M8")])

        # 스킵 전: 미등록 1건 + 오늘 알림 대상.
        data = client.get(f"/api/viscosity/products/{pid}/blend-records").json()
        assert data["unregistered_total"] == 1
        assert data["items"][0]["skipped"] is False
        assert prod in _due_codes(client, today.isoformat())

        # 사유 검증 — 빈/한 글자 사유는 거부.
        anon = _client()
        anon.get("/api/viscosity/products")  # csrf 쿠키
        assert anon.post(
            f"/api/viscosity/blend-records/{rid}/skip",
            json={"reason": ""}, headers=_csrf(anon),
        ).status_code == 400
        assert anon.post(
            f"/api/viscosity/blend-records/{rid}/skip",
            json={"reason": "x"}, headers=_csrf(anon),
        ).status_code == 400

        # 기록은 비로그인 현장도 가능(사유 필수). 감사에 사유가 남는다.
        res = anon.post(
            f"/api/viscosity/blend-records/{rid}/skip",
            json={"reason": "시료 없음 - 전량 출하"}, headers=_csrf(anon),
        )
        assert res.status_code == 200, res.text
        with get_connection() as conn:
            row = conn.execute(
                "SELECT details_json FROM audit_logs "
                "WHERE action = 'viscosity_skip_recorded' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert _json.loads(row["details_json"])["reason"] == "시료 없음 - 전량 출하"

        # 중복 기록은 409.
        assert anon.post(
            f"/api/viscosity/blend-records/{rid}/skip",
            json={"reason": "다시 기록"}, headers=_csrf(anon),
        ).status_code == 409

        # 스킵 후: 미등록 집계·필터에서 빠지고 알림도 조용해진다.
        data = client.get(f"/api/viscosity/products/{pid}/blend-records").json()
        assert data["unregistered_total"] == 0
        item = data["items"][0]
        assert item["skipped"] is True
        assert item["skip_reason"] == "시료 없음 - 전량 출하"
        assert item["registered"] is False
        filtered = client.get(
            f"/api/viscosity/products/{pid}/blend-records", params={"unregistered": "1"}
        ).json()["items"]
        assert filtered == []
        assert prod not in _due_codes(client, today.isoformat())

        # 되돌리기는 책임자만 — 현장은 401/403, 책임자는 성공 후 알림 재개.
        assert anon.delete(
            f"/api/viscosity/blend-records/{rid}/skip", headers=_csrf(anon)
        ).status_code in (401, 403)
        res = client.delete(
            f"/api/viscosity/blend-records/{rid}/skip", headers=_csrf(client)
        )
        assert res.status_code == 200, res.text
        data = client.get(f"/api/viscosity/products/{pid}/blend-records").json()
        assert data["items"][0]["skipped"] is False
        assert data["unregistered_total"] == 1
        assert prod in _due_codes(client, today.isoformat())
        # 없는 스킵을 또 지우면 404.
        assert client.delete(
            f"/api/viscosity/blend-records/{rid}/skip", headers=_csrf(client)
        ).status_code == 404
    finally:
        _set_reminder_since(None)  # 다른 테스트의 '기준일 없음' 전제 보호


def test_skip_rejected_when_registered_and_cleared_by_registration():
    from src.db import get_connection

    client = _client()
    _login_admin(client)
    prod = "VSL" + uuid.uuid4().hex[:5].upper()
    _seed_recipe(client, prod)
    pid = _make_product(client, prod)

    # 이미 점도가 등록된 기록은 측정 불가로 바꿀 수 없다.
    rid_done = _make_blend(client, prod, "2026-08-12", [("PB", "26081210"), ("MEK", "M9")])
    reg = client.post(
        f"/api/blend/records/{rid_done}/viscosity",
        json={"viscosity": 410.0, "product_id": pid},
        headers=_csrf(client),
    )
    assert reg.status_code == 200, reg.text
    assert client.post(
        f"/api/viscosity/blend-records/{rid_done}/skip",
        json={"reason": "뒤늦은 불가 시도"}, headers=_csrf(client),
    ).status_code == 400

    # 스킵된 기록에 실측이 들어오면 skip 은 자동 해제된다 — 값이 있는데
    # '불가'가 남으면 집계·감사가 서로 다른 말을 한다.
    rid = _make_blend(client, prod, "2026-08-13", [("PB", "26081311"), ("MEK", "M10")])
    assert client.post(
        f"/api/viscosity/blend-records/{rid}/skip",
        json={"reason": "시료 없음(착오)"}, headers=_csrf(client),
    ).status_code == 200
    reg = client.post(
        f"/api/blend/records/{rid}/viscosity",
        json={"viscosity": 420.0, "product_id": pid},
        headers=_csrf(client),
    )
    assert reg.status_code == 200, reg.text
    with get_connection() as conn:
        left = conn.execute(
            "SELECT 1 FROM viscosity_skips WHERE blend_record_id = ?", (rid,)
        ).fetchone()
    assert left is None
    data = client.get(f"/api/viscosity/products/{pid}/blend-records").json()
    item = next(it for it in data["items"] if it["id"] == rid)
    assert item["registered"] is True
    assert item["skipped"] is False
