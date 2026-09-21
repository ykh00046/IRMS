"""시험 배합 LOT 의 점도 — docs/test-blend-design.md §9 (2026-09-18 2차 결정으로 재설계).

1차 구현은 시험 점도를 정식 viscosity_readings 에 is_test 표시로 넣고, 시험의 기준
레시피를 거쳐 반제품(PB 등)에 매달았다. 기준 레시피가 없는 새 레시피 시험은 매달 곳이
없어 점도를 아예 등록할 수 없었다. 이제 시험 점도는 반제품과 무관하게 시험 LOT 하나에
값 하나(test_viscosity_readings)이고, 정식 add_reading 은 시험 LOT 을 거부한다.

여기서 양쪽을 동시에 못 박는다:
  시험 쪽   새 레시피 시험도 등록·목록 · 409 · 400(정식/취소) · 정정 10분 유예 · 삭제 책임자 ·
            기록 상세 '점도 측정' 칸(list_readings_for_blend) · 감사 로그
  정식 쪽   add_reading 이 시험 LOT 거부(배합 연계·직접 등록) · 등록 대기열·트레이 알림에
            시험 기록 없음 · analyze_product·overview 는 시험 값을 볼 길이 없다
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import uuid

import pytest

from src.services import viscosity_service as vs

_WORK_DATE = "2026-09-18"


# ── 공통 헬퍼(라우트) ────────────────────────────────────────────────
def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _uid() -> str:
    return uuid.uuid4().hex[:6].upper()


def _csrf(client) -> dict:
    token = client.cookies.get("csrftoken")
    return {"x-csrftoken": token} if token else {}


def _mgmt_client():
    """책임자(admin) 로그인 클라이언트 — 자재 등록·삭제·취소 같은 책임자 동작용."""
    client = _client()
    client.get("/api/viscosity/products")  # csrf 쿠키
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200, res.text
    return client


def _anon_client():
    """로그인 없는 현장 단말(점도 화면은 로그인 없이 쓴다)."""
    client = _client()
    client.get("/api/viscosity/products")  # csrf 쿠키
    return client


def _worker_session(client, worker: str) -> str:
    client.get("/api/blend/records")  # csrf 쿠키
    client.post("/api/workers", json={"name": worker}, headers=_csrf(client))
    client.post("/api/blend/session/login", json={"worker": worker}, headers=_csrf(client))
    return worker


def _register_material(client, name: str) -> int:
    """자재 마스터에 등록(품목코드 없이) — 시험 배합은 등록된 자재만 쓴다(2차 결정).

    코드를 주지 않는다: 품목코드를 주면 item_code_master 가 채워지고, 같은 실행의 다른
    테스트가 쓰는 미등록 자재(원료A 등)가 막힌다(tests/conftest.py 주석의 회귀).
    """
    res = client.post("/api/materials", json={"name": name}, headers=_csrf(client))
    assert res.status_code == 200, res.text
    return int(res.json()["id"])


def _new_materials(client) -> list[tuple[int, str]]:
    names = ["시험원료A" + _uid(), "시험원료B" + _uid()]
    return [(_register_material(client, n), n) for n in names]


def _recipe_materials(client, product: str, materials: list[tuple[str, float]]):
    """레시피 등록(자재 자동 등록) → (레시피 id, [(자재 id, 자재명)])."""
    header = "반제품명\t" + "\t".join(m[0] for m in materials)
    row = product + "\t" + "\t".join(str(m[1]) for m in materials)
    res = client.post(
        "/api/recipes/import",
        json={"raw_text": f"{header}\n{row}", "force": True},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text
    recipe_id = res.json()["created_ids"][0]
    by_name = {it["name"]: it["id"] for it in client.get("/api/materials").json()["items"]}
    return recipe_id, [(int(by_name[m[0]]), m[0]) for m in materials]


def _save_test(client, name, mats, *, base_recipe_id=None, work_date=_WORK_DATE):
    """시험 배합 저장 — 행마다 등록 자재의 material_id 를 싣는다."""
    body = {
        "is_test": True,
        "product_name": name,
        "worker": "무시됨",          # 서버는 작업자 세션 이름을 쓴다
        "work_date": work_date,
        "total_amount": 1,           # 무시됨 — 서버가 목표량 합으로 산출
        "details": [
            {
                "material_id": mid,
                "material_name": mname,
                "theory_amount": amount,
                "actual_amount": amount,
                "material_lot": f"TL{i}",
            }
            for i, ((mid, mname), amount) in enumerate(zip(mats, (30, 70)))
        ],
    }
    if base_recipe_id is not None:
        body["base_recipe_id"] = base_recipe_id
    res = client.post("/api/blend/records", json=body, headers=_csrf(client))
    assert res.status_code == 200, res.text
    return res.json()


def _save_real(client, recipe_id, product, mats, *, work_date=_WORK_DATE):
    res = client.post(
        "/api/blend/records",
        json={
            "recipe_id": recipe_id,
            "product_name": product,
            "worker": "무시됨",
            "work_date": work_date,
            "total_amount": 100,
            "details": [
                {"material_name": mname, "actual_amount": amount, "material_lot": f"L{i}"}
                for i, ((_mid, mname), amount) in enumerate(zip(mats, (60, 40)))
            ],
        },
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text
    return res.json()


def _list(client, **params) -> dict:
    res = client.get("/api/viscosity/test-records", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _register(client, record_id, value, **extra):
    return client.post(
        f"/api/viscosity/test-records/{record_id}",
        json={"viscosity": value, **extra},
        headers=_csrf(client),
    )


def _audit(action: str, target_id: int) -> list[dict]:
    from src.db import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT details_json, actor_display_name FROM audit_logs "
            "WHERE action = ? AND target_id = ? ORDER BY id",
            (action, str(target_id)),
        ).fetchall()
    return [
        {"details": json.loads(r["details_json"] or "{}"), "actor": r["actor_display_name"]}
        for r in rows
    ]


# ── 스키마(§2) ───────────────────────────────────────────────────────
def test_schema_has_test_table_and_no_reading_level_flag():
    """시험 점도 전용 표가 있고, 정식 표본에는 시험 표시 열이 없다(1차 구현 제거)."""
    _client()  # 앱 기동 = 마이그레이션
    from src.db import get_connection

    with get_connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(test_viscosity_readings)")}
        reading_cols = {r["name"] for r in conn.execute("PRAGMA table_info(viscosity_readings)")}
        fks = conn.execute("PRAGMA foreign_key_list(test_viscosity_readings)").fetchall()
    assert {
        "id", "blend_record_id", "viscosity", "measured_date", "memo",
        "created_by", "created_at", "updated_by", "updated_at",
    } <= cols
    assert "is_test" not in reading_cols
    assert any(
        fk["table"] == "blend_records" and fk["on_delete"] == "CASCADE" for fk in fks
    )


# ── 등록·목록(§9-1·§9-4) ─────────────────────────────────────────────
def test_new_recipe_test_gets_viscosity_and_is_listed():
    """기준 레시피가 없는 새 레시피 시험도 점도가 등록되고 목록에 나온다(재설계의 이유).

    목록 기본(recorded)은 기록한 시험만 — 등록 전에는 기록 창 후보(unrecorded)에만 있다.
    """
    client = _mgmt_client()
    worker = _worker_session(client, "시험점도" + _uid())
    name = "새레시피시험" + _uid()
    record = _save_test(client, name, _new_materials(client))
    assert record["is_test"] is True
    assert record["base_recipe_id"] is None

    before = _list(client, q=name)
    assert before["state"] == "recorded"
    assert before["items"] == [] and before["total"] == 0
    assert before["counts"] == {"recorded": 0, "unrecorded": 1, "all": 1}
    picks = _list(client, q=name, state="unrecorded")
    assert picks["total"] == 1
    item = picks["items"][0]
    assert item["id"] == record["id"]
    assert item["product_name"] == name
    assert item["product_lot"] == record["product_lot"]
    assert item["worker"] == worker
    assert item["base_recipe_name"] is None
    assert item["registered"] is False and item["viscosity"] is None

    res = _register(client, record["id"], 123.4, measured_date="2026-09-19", memo="첫 측정")
    assert res.status_code == 200, res.text
    assert res.json()["reading"]["viscosity"] == 123.4

    after = _list(client, q=name)
    assert after["total"] == 1
    assert after["counts"] == {"recorded": 1, "unrecorded": 0, "all": 1}
    got = after["items"][0]
    assert got["registered"] is True
    assert got["viscosity"] == 123.4
    assert got["measured_date"] == "2026-09-19"
    assert got["memo"] == "첫 측정"
    # 책임자 로그인 단말에서 등록 → 로그인 이름(정식 등록과 같은 규칙).
    assert got["created_by"] and got["created_by"] != "현장"
    assert got["created_at"]
    assert _list(client, q=name, state="unrecorded")["items"] == []


def test_list_states_order_search_and_counts():
    """시험 점도 목록: 반제품과 무관 · state 별 내용과 순서 · 검색(LOT·시험명·작업자) · 건수.

    recorded(기본) = 기록한 시험만, 최근 측정 먼저(측정일, 같은 날은 나중 기록 먼저).
    unrecorded(기록 창 후보)·all = 최근 배합 먼저(작업일, id).
    """
    client = _mgmt_client()
    tag = "목록" + _uid()
    worker = _worker_session(client, "목록작업" + _uid())
    recipe_id, mats = _recipe_materials(
        client, "기준" + tag, [("목록원료A" + _uid(), 60), ("목록원료B" + _uid(), 40)]
    )
    a = _save_test(client, tag + "가", mats, base_recipe_id=recipe_id, work_date="2026-09-17")
    b = _save_test(client, tag + "나", mats, work_date="2026-09-15")
    c = _save_test(client, tag + "다", mats, work_date="2026-09-15")
    d = _save_test(client, tag + "라", mats, work_date="2026-09-14")

    # 아직 아무것도 기록하지 않았다 — 기본 목록은 비고, 후보는 최근 배합 먼저.
    assert _list(client, q=tag)["items"] == []
    picks = _list(client, q=tag, state="unrecorded")
    assert [it["id"] for it in picks["items"]] == [a["id"], c["id"], b["id"], d["id"]]
    assert picks["counts"] == {"recorded": 0, "unrecorded": 4, "all": 4}
    by_id = {it["id"]: it for it in picks["items"]}
    assert by_id[a["id"]]["base_recipe_id"] == recipe_id
    assert by_id[a["id"]]["base_recipe_name"] == "기준" + tag
    assert by_id[b["id"]]["base_recipe_name"] is None

    # 측정일 순서 · C 먼저 기록, B 나중(같은 측정일 → 나중 기록 먼저), A 는 더 최근 측정.
    assert _register(client, c["id"], 55.5, measured_date="2026-09-16").status_code == 200
    assert _register(client, b["id"], 56.5, measured_date="2026-09-16").status_code == 200
    assert _register(client, a["id"], 57.5, measured_date="2026-09-18").status_code == 200

    recorded = _list(client, q=tag)
    assert [it["id"] for it in recorded["items"]] == [a["id"], b["id"], c["id"]]
    assert all(it["registered"] for it in recorded["items"])
    assert recorded["total"] == 3
    assert recorded["counts"] == {"recorded": 3, "unrecorded": 1, "all": 4}
    unrecorded = _list(client, q=tag, state="unrecorded")
    assert [it["id"] for it in unrecorded["items"]] == [d["id"]]
    assert unrecorded["total"] == 1
    everything = _list(client, q=tag, state="all")
    assert [it["id"] for it in everything["items"]] == [a["id"], c["id"], b["id"], d["id"]]
    assert everything["total"] == 4

    # 검색(서버): 제품 LOT · 시험명 · 작업자. 기록 창 후보도 같은 검색을 쓴다.
    assert [it["id"] for it in _list(client, q=b["product_lot"])["items"]] == [b["id"]]
    assert [it["id"] for it in _list(client, q=tag + "다")["items"]] == [c["id"]]
    assert {it["id"] for it in _list(client, q=worker)["items"]} == {a["id"], b["id"], c["id"]}
    assert [it["id"] for it in _list(client, q=d["product_lot"], state="unrecorded")["items"]] == [
        d["id"]
    ]
    assert _list(client, q=d["product_lot"])["items"] == []
    # limit
    assert len(_list(client, q=tag, limit=2)["items"]) == 2


def test_list_state_rejects_unknown_values():
    """state 는 recorded·unrecorded·all 만 — 그 밖은 422(배합 기록 test 필터와 같은 방식)."""
    client = _anon_client()
    for bad in ("registered", "unregistered", "ALL", ""):
        res = client.get("/api/viscosity/test-records", params={"state": bad})
        assert res.status_code == 422, (bad, res.text)
    with pytest.raises(ValueError):
        vs.list_test_records(_make_db(), state="nope")


def test_second_create_is_409_and_bad_records_are_400_or_404():
    client = _mgmt_client()
    _worker_session(client, "거부작업" + _uid())
    product = "거부제품" + _uid()
    recipe_id, mats = _recipe_materials(
        client, product, [("거부원료A" + _uid(), 60), ("거부원료B" + _uid(), 40)]
    )
    real = _save_real(client, recipe_id, product, mats)
    test = _save_test(client, product + "시험", mats)

    assert _register(client, test["id"], 40.0).status_code == 200
    dup = _register(client, test["id"], 41.0)
    assert dup.status_code == 409, dup.text
    assert test["product_lot"] in dup.json()["detail"]

    # 정식 기록 id 로는 시험 점도를 넣을 수 없다 — 측정 등록 탭으로 안내.
    not_test = _register(client, real["id"], 40.0)
    assert not_test.status_code == 400, not_test.text
    assert "측정 등록" in not_test.json()["detail"]

    # 취소된 시험 기록은 목록에 없고 등록도 거부된다.
    canceled = _save_test(client, product + "취소", mats)
    res = client.delete(
        f"/api/blend/records/{canceled['id']}", params={"reason": "잘못 저장"},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text
    assert _register(client, canceled["id"], 40.0).status_code == 400
    assert canceled["id"] not in [
        it["id"] for it in _list(client, q=product, state="all")["items"]
    ]

    assert _register(client, 99_999_999, 40.0).status_code == 404
    # 값 범위는 정식 등록과 같다(0 초과 · 100000 이하) · 측정일 형식 검사.
    assert _register(client, test["id"], 0).status_code == 422
    assert _register(client, test["id"], 100001).status_code == 422
    fresh = _save_test(client, product + "날짜", mats)
    bad_date = _register(client, fresh["id"], 40.0, measured_date="2026-13-40")
    assert bad_date.status_code == 400, bad_date.text


def test_anonymous_registration_is_recorded_as_field():
    """로그인 없이 등록하면 created_by '현장'(정식 배합 연계 등록과 같은 규칙) · 측정일 기본 오늘."""
    from datetime import date

    client = _mgmt_client()
    _worker_session(client, "현장작업" + _uid())
    record = _save_test(client, "현장시험" + _uid(), _new_materials(client))

    anon = _anon_client()
    res = _register(anon, record["id"], 77.7)
    assert res.status_code == 200, res.text
    reading = res.json()["reading"]
    assert reading["created_by"] == "현장"
    assert reading["measured_date"] == date.today().isoformat()


# ── 정정·삭제(§9-5) — 정식 측정과 같은 권한 ────────────────────────────
def test_correction_grace_window_then_manager_only():
    """등록 후 10분은 현장 누구나(사유 필수), 이후는 책임자만. 감사에 원래 값·새 값·사유."""
    from src.db import get_connection

    client = _mgmt_client()
    _worker_session(client, "정정작업" + _uid())
    record = _save_test(client, "정정시험" + _uid(), _new_materials(client))
    rid = record["id"]

    anon = _anon_client()
    assert _register(anon, rid, 390.0).status_code == 200

    def put(c, body):
        return c.put(f"/api/viscosity/test-records/{rid}", json=body, headers=_csrf(c))

    # 값·사유 검증은 공통(정식과 같은 문구).
    assert put(anon, {"viscosity": "abc", "reason": "정정"}).status_code == 400
    assert put(anon, {"viscosity": 0, "reason": "정정"}).status_code == 400
    assert put(anon, {"viscosity": 380.0, "reason": ""}).status_code == 400

    # 유예창 안 — 비로그인 현장도 정정. 감사 grace_window True.
    res = put(anon, {"viscosity": 385.0, "reason": "등록 직후 오타 정정"})
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok", "old": 390.0, "new": 385.0}
    logged = _audit("test_viscosity_corrected", rid)
    assert logged[-1]["details"] == {
        "old": 390.0, "new": 385.0, "reason": "등록 직후 오타 정정", "grace_window": True,
    }
    item = _list(client, q=record["product_lot"])["items"][0]
    assert item["viscosity"] == 385.0
    assert item["updated_by"] == "현장" and item["updated_at"]

    # 같은 값이면 쓰지 않는다(감사도 없음).
    assert put(anon, {"viscosity": 385.0, "reason": "그대로"}).status_code == 200
    assert len(_audit("test_viscosity_corrected", rid)) == 1

    # 유예창이 지나면(등록 시각을 과거로) 현장은 403 + 책임자 안내.
    with get_connection() as conn:
        conn.execute(
            "UPDATE test_viscosity_readings SET created_at = '2026-08-13T00:00:00Z' "
            "WHERE blend_record_id = ?",
            (rid,),
        )
        conn.commit()
    late = put(anon, {"viscosity": 380.0, "reason": "늦은 정정"})
    assert late.status_code == 403, late.text
    assert "책임자" in late.json()["detail"]

    # 책임자는 유예창과 무관하게 정정한다.
    manager = put(client, {"viscosity": 380.0, "reason": "책임자 정정"})
    assert manager.status_code == 200, manager.text
    assert _audit("test_viscosity_corrected", rid)[-1]["details"]["grace_window"] is False

    # 값이 없는 기록은 404.
    other = _save_test(client, "정정없음" + _uid(), _new_materials(client))
    missing = client.put(
        f"/api/viscosity/test-records/{other['id']}",
        json={"viscosity": 1.0, "reason": "없는 값"}, headers=_csrf(client),
    )
    assert missing.status_code == 404


def test_delete_is_manager_only_and_audited():
    client = _mgmt_client()
    _worker_session(client, "삭제작업" + _uid())
    record = _save_test(client, "삭제시험" + _uid(), _new_materials(client))
    rid = record["id"]
    assert _register(client, rid, 50.0).status_code == 200

    anon = _anon_client()
    denied = anon.delete(f"/api/viscosity/test-records/{rid}", headers=_csrf(anon))
    assert denied.status_code in (401, 403), denied.text

    ok = client.delete(f"/api/viscosity/test-records/{rid}", headers=_csrf(client))
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"deleted": rid}
    # 지우면 기록 목록에서 빠지고 기록 창 후보로 돌아간다.
    assert _list(client, q=record["product_lot"])["items"] == []
    assert [it["id"] for it in _list(client, q=record["product_lot"], state="unrecorded")["items"]] == [rid]
    assert client.delete(
        f"/api/viscosity/test-records/{rid}", headers=_csrf(client)
    ).status_code == 404

    # 등록·정정·삭제 모두 감사 로그(대상 = 배합 기록, 표기 = 제품 LOT).
    assert _audit("test_viscosity_add", rid)[0]["details"]["viscosity"] == 50.0
    assert _audit("test_viscosity_delete", rid)[0]["details"]["viscosity"] == 50.0

    # 다시 등록할 수 있다(값 하나 규칙은 '지금 있는 값' 기준).
    assert _register(client, rid, 51.0).status_code == 200


def test_hard_delete_of_record_removes_its_test_viscosity():
    """배합 기록을 물리 삭제하면 시험 점도도 함께 지워진다(FK ON DELETE CASCADE)."""
    from src.db import get_connection

    client = _mgmt_client()
    _worker_session(client, "완전삭제" + _uid())
    record = _save_test(client, "완전삭제시험" + _uid(), _new_materials(client))
    assert _register(client, record["id"], 60.0).status_code == 200
    res = client.delete(
        f"/api/blend/records/{record['id']}",
        params={"hard": "true", "reason": "시험 정리"},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text
    with get_connection() as conn:
        left = conn.execute(
            "SELECT COUNT(*) FROM test_viscosity_readings WHERE blend_record_id = ?",
            (record["id"],),
        ).fetchone()[0]
    assert left == 0


# ── 기록 조회 상세(§9-6) ─────────────────────────────────────────────
def test_record_detail_shows_test_viscosity_in_the_same_shape():
    """기록 조회 상세의 '점도 측정' 칸 — 시험은 product_code '시험' 으로 같은 모양."""
    client = _mgmt_client()
    _worker_session(client, "상세작업" + _uid())
    product = "상세제품" + _uid()
    recipe_id, mats = _recipe_materials(
        client, product, [("상세원료A" + _uid(), 60), ("상세원료B" + _uid(), 40)]
    )
    record = _save_test(client, product + "시험", mats, base_recipe_id=recipe_id)
    empty = client.get(f"/api/blend/records/{record['id']}").json()
    assert empty["viscosity"] == []

    assert _register(client, record["id"], 88.8, measured_date="2026-09-19").status_code == 200
    detail = client.get(f"/api/blend/records/{record['id']}").json()
    assert detail["is_test"] is True
    assert detail["base_recipe_name"] == product
    assert len(detail["viscosity"]) == 1
    shown = detail["viscosity"][0]
    assert shown["product_code"] == "시험"
    assert shown["viscosity"] == 88.8
    assert shown["measured_date"] == "2026-09-19"
    assert shown["created_by"]
    assert shown["lot_no"] == record["product_lot"]

    # 정식 기록 상세는 그대로(반제품 코드).
    real = _save_real(client, recipe_id, product, mats)
    linked = client.post(
        f"/api/blend/records/{real['id']}/viscosity", json={"viscosity": 49.0},
        headers=_csrf(client),
    )
    assert linked.status_code == 200, linked.text
    real_detail = client.get(f"/api/blend/records/{real['id']}").json()
    assert [v["product_code"] for v in real_detail["viscosity"]] == [product]


# ── 정식 쪽 격리(§9-2·§9-3) ──────────────────────────────────────────
def test_production_registration_rejects_test_lots():
    """정식 add_reading 은 시험 LOT 을 받지 않는다 — 배합 연계·직접 등록 모두 400."""
    client = _mgmt_client()
    _worker_session(client, "정식거부" + _uid())
    product = "정식거부" + _uid()
    recipe_id, mats = _recipe_materials(
        client, product, [("정식원료A" + _uid(), 60), ("정식원료B" + _uid(), 40)]
    )
    real = _save_real(client, recipe_id, product, mats)
    assert client.post(
        f"/api/blend/records/{real['id']}/viscosity", json={"viscosity": 49.0},
        headers=_csrf(client),
    ).status_code == 200
    pid = next(
        p["id"] for p in client.get("/api/viscosity/products").json()["items"]
        if p["code"] == product
    )
    test_name = "유령방지" + _uid()
    test = _save_test(client, test_name, mats, base_recipe_id=recipe_id)

    # ① 배합 연계 등록(점도 화면의 저장 경로) — 반제품을 지정해도, 안 해도 거부.
    for body in ({"viscosity": 999.0, "product_id": pid}, {"viscosity": 999.0}):
        res = client.post(
            f"/api/blend/records/{test['id']}/viscosity", json=body, headers=_csrf(client)
        )
        assert res.status_code == 400, res.text
        assert res.json()["detail"] == vs.TEST_LOT_DETAIL
    # 시험명으로 유령 반제품이 생기지 않는다(반제품 자동 확보 전에 막는다).
    codes = {p["code"] for p in client.get("/api/viscosity/products").json()["items"]}
    assert test_name not in codes

    # ② 직접 등록(POST /viscosity/readings) — LOT 이 시험이면 거부.
    direct = client.post(
        "/api/viscosity/readings",
        json={"product_id": pid, "lot_no": test["product_lot"], "viscosity": 999.0},
        headers=_csrf(client),
    )
    assert direct.status_code == 400, direct.text
    assert direct.json()["detail"] == vs.TEST_LOT_DETAIL

    # ③ 측정 불가도 시험에는 없다(알림·대기열이 없다).
    skip = client.post(
        f"/api/viscosity/blend-records/{test['id']}/skip", json={"reason": "시료 없음"},
        headers=_csrf(client),
    )
    assert skip.status_code == 400, skip.text

    # 정식 표본은 그대로 1건.
    analysis = client.get(f"/api/viscosity/products/{pid}").json()
    assert [r["viscosity"] for r in analysis["readings"]] == [49.0]


def test_queue_stats_and_reminders_never_see_test_records():
    """시험명이 반제품 이름과 같아도: 등록 대기열·분석·요약·트레이 알림에 시험이 없다."""
    client = _mgmt_client()
    _worker_session(client, "격리작업" + _uid())
    product = "격리" + _uid()
    recipe_id, mats = _recipe_materials(
        client, product, [("격리원료A" + _uid(), 60), ("격리원료B" + _uid(), 40)]
    )
    real = _save_real(client, recipe_id, product, mats, work_date="2026-09-16")
    created = client.post(
        "/api/viscosity/products", json={"code": product, "name": product},
        headers=_csrf(client),
    )
    assert created.status_code == 200, created.text
    pid = created.json()["id"]
    # 매일 알림 대상으로 — 트레이 pending_lots 비교용.
    patched = client.patch(
        f"/api/viscosity/products/{pid}",
        json={"name": product, "sigma_k": 3, "remind_daily": True, "is_active": True},
        headers=_csrf(client),
    )
    assert patched.status_code == 200, patched.text
    # 우연한 동명 — 시험명을 반제품 이름과 똑같이.
    test = _save_test(client, product, mats, work_date="2026-09-16")

    queue = client.get(f"/api/viscosity/products/{pid}/blend-records").json()
    assert [it["id"] for it in queue["items"]] == [real["id"]]
    assert queue["total"] == 1 and queue["unregistered_total"] == 1
    assert "test" not in queue and all("is_test" not in it for it in queue["items"])

    # 트레이(공개 알림)에는 정식 LOT 만. 내부망 제한 라우트라 사내 IP 로 부른다.
    # 정리 기준일(다른 테스트가 남겼을 수 있다)은 잠시 비웠다가 되돌린다.
    from fastapi.testclient import TestClient

    from src.db import get_connection
    from src.services.settings_service import VISCOSITY_REMINDER_SINCE_KEY as since_key

    with get_connection() as conn:
        kept = conn.execute(
            "SELECT value, updated_at FROM app_settings WHERE key = ?", (since_key,)
        ).fetchone()
        conn.execute("DELETE FROM app_settings WHERE key = ?", (since_key,))
        conn.commit()
    try:
        internal = TestClient(client.app, client=("192.168.11.108", 50000))
        due = internal.get(
            "/api/public/viscosity-reminders/due",
            params={"target_date": "2026-09-30", "codes": product},
        )
    finally:
        if kept is not None:
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (since_key, kept["value"], kept["updated_at"]),
                )
                conn.commit()
    assert due.status_code == 200, due.text
    mine = [it for it in due.json()["items"] if it["code"] == product]
    assert len(mine) == 1
    assert [lot["product_lot"] for lot in mine[0]["pending_lots"]] == [real["product_lot"]]

    # 시험 점도를 등록해도 정식 분석·요약은 그것을 볼 길이 없다.
    assert _register(client, test["id"], 999.0).status_code == 200
    assert client.post(
        f"/api/blend/records/{real['id']}/viscosity",
        json={"viscosity": 49.0, "product_id": pid}, headers=_csrf(client),
    ).status_code == 200
    analysis = client.get(f"/api/viscosity/products/{pid}").json()
    assert analysis["stats"]["n"] == 1
    assert [r["viscosity"] for r in analysis["readings"]] == [49.0]
    assert analysis["counts"]["anomaly"] == 0
    overview = next(
        it for it in client.get("/api/viscosity/overview").json()["items"] if it["id"] == pid
    )
    assert overview["count"] == 1 and overview["latest_value"] == 49.0
    # 시험 탭에는 그대로 있다.
    assert _list(client, q=test["product_lot"])["items"][0]["viscosity"] == 999.0


def test_records_list_test_filter_and_detail_fields():
    """/api/blend/records?test=only 는 시험 기록만 · 상세는 기준 레시피 이름까지(§9 기록 조회)."""
    client = _mgmt_client()
    _worker_session(client, "칩작업" + _uid())
    product = "칩제품" + _uid()
    recipe_id, mats = _recipe_materials(
        client, product, [("칩원료A" + _uid(), 60), ("칩원료B" + _uid(), 40)]
    )
    real = _save_real(client, recipe_id, product, mats)
    test = _save_test(client, product + "시험", mats, base_recipe_id=recipe_id)

    only = client.get("/api/blend/records", params={"test": "only", "search": product})
    assert only.status_code == 200, only.text
    ids = [it["id"] for it in only.json()["items"]]
    assert test["id"] in ids and real["id"] not in ids
    assert all(it["is_test"] for it in only.json()["items"])

    exclude = client.get("/api/blend/records", params={"test": "exclude", "search": product})
    ex_ids = [it["id"] for it in exclude.json()["items"]]
    assert real["id"] in ex_ids and test["id"] not in ex_ids

    detail = client.get(f"/api/blend/records/{test['id']}").json()
    assert detail["is_test"] is True
    assert detail["base_recipe_name"] == product


# ── v1 이관(기동 마이그레이션) ────────────────────────────────────────
def test_v1_test_readings_move_to_test_table_on_startup(tmp_path):
    """v1(운영 배포분)이 정식 표에 is_test=1 로 남긴 시험 점도를 기동 마이그레이션이 옮긴다.

    연계 행·LOT 만 맞는 행은 옮기고, 같은 시험 LOT 에 값이 둘이면 늦게 등록한 값을 남기며
    (버린 값은 감사 로그에), 대상이 없는 고아는 그대로 두되 정식 통계에서 빠진다. 물리 삭제로
    풀린 LOT 이 나중 시험에 다시 발번돼도 옛 값이 새 기록에 붙지 않는다. 재실행해도 그대로.
    공용 테스트 DB 스키마를 건드리지 않도록 tmp_path 의 새 DB 에서 돈다.
    """
    import src.db.connection as dbconn
    from src.db import apply_schema_migrations, init_db

    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True)
    dbconn.DATA_DIR = db_dir                       # tests/conftest.py 가 테스트 뒤 되돌린다
    dbconn.DATABASE_PATH = db_dir / "irms.db"
    init_db()
    conn = dbconn.get_connection()
    try:
        # v1 모양 — 운영 DB 의 정식 표에는 is_test 열이 있다.
        conn.execute(
            "ALTER TABLE viscosity_readings ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0"
        )
        pb = conn.execute("SELECT id FROM viscosity_products WHERE code = 'PB'").fetchone()[0]
        sbct = conn.execute("SELECT id FROM viscosity_products WHERE code = 'SBCT'").fetchone()[0]

        def blend(lot, name, is_test, created="2026-09-18T01:00:00Z"):
            return conn.execute(
                "INSERT INTO blend_records (product_lot, product_name, worker, work_date, "
                "total_amount, created_at, is_test) VALUES (?, ?, '작업자', '2026-09-18', 100, ?, ?)",
                (lot, name, created, is_test),
            ).lastrowid

        def reading(product_id, lot, value, *, record=None, is_test=1,
                    created="2026-09-18T03:00:00Z"):
            return conn.execute(
                "INSERT INTO viscosity_readings (product_id, lot_no, viscosity, measured_date, "
                "created_by, created_at, blend_record_id, is_test) "
                "VALUES (?, ?, ?, '2026-09-18', '현장', ?, ?, ?)",
                (product_id, lot, value, created, record, is_test),
            ).lastrowid

        prod = blend("PB26091801", "PB", 0)
        t1 = blend("T-PB시험26091801", "PB시험", 1)
        t2 = blend("T-PB시험26091802", "PB시험", 1)
        t3 = blend("T-새시험26091801", "새시험", 1)
        reissued = blend("T-재발번26091801", "재발번", 1, created="2026-09-18T06:00:00Z")

        reading(pb, "PB26091801", 49.0, record=prod, is_test=0)          # 정식 — 그대로
        reading(pb, "T-PB시험26091801", 120.0, record=t1)                # 연계 v1 행
        reading(sbct, "T-PB시험26091801", 125.0, record=t1,              # 같은 LOT · 늦게 등록
                created="2026-09-18T04:00:00Z")
        reading(pb, "T-PB시험26091802", 130.0)                           # LOT 만(직접 등록)
        reading(pb, "T-새시험26091801", 140.0, record=t3)                # 이미 새 값이 있는 기록
        conn.execute(
            "INSERT INTO test_viscosity_readings "
            "(blend_record_id, viscosity, measured_date, created_by, created_at) "
            "VALUES (?, 141.0, '2026-09-18', '책임자', '2026-09-18T05:00:00Z')",
            (t3,),
        )
        reading(pb, "T-지운시험26091801", 999.0)                         # 기록이 지워진 고아
        reading(pb, "T-재발번26091801", 777.0)                           # 지운 뒤 다시 발번된 LOT
        conn.commit()

        apply_schema_migrations(conn)
        conn.commit()

        moved = {
            r["blend_record_id"]: r["viscosity"]
            for r in conn.execute("SELECT blend_record_id, viscosity FROM test_viscosity_readings")
        }
        assert moved == {t1: 125.0, t2: 130.0, t3: 141.0}
        assert reissued not in moved
        left = conn.execute(
            "SELECT viscosity, is_test FROM viscosity_readings ORDER BY id"
        ).fetchall()
        assert [(r["viscosity"], r["is_test"]) for r in left] == [(49.0, 0), (999.0, 1), (777.0, 1)]

        # 정식 통계는 남은 고아를 보지 않는다(_fetch_readings 의 한 곳 가드).
        analysis = vs.analyze_product(conn, vs.get_product(conn, pb))
        assert [r["viscosity"] for r in analysis["readings"]] == [49.0]
        assert analysis["stats"]["n"] == 1
        assert vs.analyze_product(conn, vs.get_product(conn, sbct))["stats"]["n"] == 0
        # 기록 조회 상세는 옮긴 값을 '시험' 으로 보여 준다.
        assert [(v["product_code"], v["viscosity"]) for v in vs.list_readings_for_blend(conn, t1)] == [
            ("시험", 125.0)
        ]
        # 옮긴 행마다 감사 로그 — 버린 값은 details.dropped 에 그대로 남는다.
        logs = [
            json.loads(r["details_json"])
            for r in conn.execute(
                "SELECT details_json FROM audit_logs "
                "WHERE action = 'test_viscosity_migrated' ORDER BY id"
            )
        ]
        assert len(logs) == 4
        assert sorted(log["dropped"]["viscosity"] for log in logs if log["dropped"]) == [120.0, 140.0]

        # 멱등 — 다시 돌려도 아무것도 바뀌지 않는다.
        apply_schema_migrations(conn)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM test_viscosity_readings").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM viscosity_readings").fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'test_viscosity_migrated'"
        ).fetchone()[0] == 4
    finally:
        conn.close()


def test_v1_migration_is_a_no_op_without_the_v1_column():
    """v1 을 거치지 않은 DB(is_test 열 없음)에서는 아무것도 하지 않는다."""
    from src.db.migrations import migrate_v1_test_viscosity

    conn = _make_db()
    assert migrate_v1_test_viscosity(conn) == {"moved": 0, "dropped": 0, "unmapped": 0}


# ── 화면 구조(§9-4) — 브라우저 없이 지킬 수 있는 것만 ──────────────────
def test_screen_layout_and_test_tab_structure():
    """점도 화면 배치(2026-09-18): 탭 줄이 맨 위 · 반제품 탭 셋 | 구분선 | 이상 관리·시험 점도 ·
    반제품 조회 조건은 반제품 탭 전용 한 벌 안 · 시험 점도 탭은 기록 목록 + [점도 기록] 창."""
    import re
    from pathlib import Path

    base = Path(__file__).resolve().parent.parent
    template = (base / "templates" / "viscosity.html").read_text(encoding="utf-8")
    controller = (base / "static" / "js" / "viscosity.js").read_text(encoding="utf-8")
    css = (base / "static" / "css" / "viscosity.css").read_text(encoding="utf-8")
    status_js = (base / "static" / "js" / "status.js").read_text(encoding="utf-8")

    # 탭 순서와 구분선 · 탭 줄이 콘텐츠 맨 앞.
    nav = template[template.index('<nav class="mgmt-tabs visc-tabs"'):]
    nav = nav[: nav.index("</nav>")]
    order = re.findall(r'data-tab="(\w+)"|class="(visc-tab-divider)"', nav)
    assert [a or b for a, b in order] == [
        "register", "trend", "pb", "visc-tab-divider", "anomaly", "test",
    ]
    assert '>시험 점도</button>' in nav and '>시험</button>' not in nav
    content = template[template.index("{% block content %}"):]
    assert content.index('<nav class="mgmt-tabs visc-tabs"') < content.index("<section")
    # 반제품 조회 조건·요약은 탭 줄 아래 한 벌(반제품 탭에서만 보인다).
    scope_at = template.index('id="visc-product-scope"')
    for marker in ('id="visc-product-select"', 'id="visc-refresh"', 'class="visc-summary"'):
        assert scope_at < template.index(marker) < template.index('<div class="tab-panel')
    assert ".visc-product-scope[hidden] { display: none; }" in css
    # 1차·2차 흔적 없음: 요약 줄 CSS 숨김 고리, 미등록만 체크, 대기열 시험 전환, 시험 그래프.
    for gone in ("visc-page", "visc-test-open-only", "visc-test-only", "visc-test-chart",
                 "visc-test-selected"):
        assert gone not in template, f"{gone} 가 남았다"
    assert "dataset.viscTab" not in controller and "data-visc-tab" not in css
    assert "test-readings" not in controller and "testChartDatasets" not in controller
    # 시험 점도 탭 · 기록 목록(기본 recorded) + [점도 기록] 창(후보 = unrecorded).
    for marker in ('id="visc-test-body"', 'id="visc-test-filter"', 'id="visc-test-add"',
                   'id="visc-test-modal"', 'id="visc-test-lot-q"', 'id="visc-test-pick-body"',
                   'id="visc-test-form"', 'id="visc-test-value"', 'id="visc-test-date"'):
        assert marker in template, f"{marker} 가 없다"
    modal_line = next(line for line in template.splitlines() if 'id="visc-test-modal"' in line)
    assert "hidden" in modal_line
    assert 'state: "recorded"' in controller and 'state: "unrecorded"' in controller
    # 딥링크 ?tab=test&lot=… · 기록 조회 상세의 링크가 여기로 온다.
    assert 'params.get("lot")' in controller
    assert "/viscosity?tab=test&amp;lot=" in status_js


# ── 서비스 단위(최소 스키마 폴백) ────────────────────────────────────
_SCHEMA = """
CREATE TABLE viscosity_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    target REAL,
    lower_limit REAL,
    upper_limit REAL,
    warn_low REAL,
    warn_high REAL,
    sigma_k REAL NOT NULL DEFAULT 3,
    rpm REAL,
    temperature REAL,
    remind_daily INTEGER NOT NULL DEFAULT 0,
    use_reactor INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE viscosity_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    lot_no TEXT NOT NULL,
    viscosity REAL NOT NULL,
    measured_date TEXT,
    memo TEXT,
    recipe_material TEXT,
    material_lot TEXT,
    reactor INTEGER,
    created_by TEXT,
    created_at TEXT NOT NULL,
    blend_record_id INTEGER,
    excluded INTEGER NOT NULL DEFAULT 0,
    exclude_reason TEXT,
    excluded_by TEXT,
    excluded_at TEXT
);
CREATE UNIQUE INDEX idx_visc_readings_product_lot ON viscosity_readings(product_id, lot_no);
CREATE TABLE blend_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_lot TEXT NOT NULL UNIQUE,
    recipe_id INTEGER,
    product_name TEXT NOT NULL,
    worker TEXT NOT NULL,
    work_date TEXT NOT NULL,
    total_amount REAL NOT NULL,
    reactor INTEGER,
    status TEXT NOT NULL DEFAULT 'completed',
    is_bulk_regenerated INTEGER NOT NULL DEFAULT 0,
    is_test INTEGER NOT NULL DEFAULT 0,
    base_recipe_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE viscosity_skips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    blend_record_id INTEGER NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE test_viscosity_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    blend_record_id INTEGER NOT NULL UNIQUE,
    viscosity REAL NOT NULL,
    measured_date TEXT,
    memo TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_by TEXT,
    updated_at TEXT
);
"""


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _add_product(conn, code="PB", **kw) -> dict:
    conn.execute(
        "INSERT INTO viscosity_products (code, name, remind_daily, is_active, created_at) "
        "VALUES (?, ?, ?, 1, '2026-01-01T00:00:00Z')",
        (code, code, 1 if kw.get("remind_daily") else 0),
    )
    return vs.get_product_by_code(conn, code)


def _add_blend(conn, lot, *, product_name, is_test=0, work_date="2026-01-10", status="completed"):
    cur = conn.execute(
        "INSERT INTO blend_records (product_lot, product_name, worker, work_date, "
        "total_amount, status, is_test, created_at) "
        "VALUES (?, ?, '작업자', ?, 100, ?, ?, '2026-01-10T00:00:00Z')",
        (lot, product_name, work_date, status, is_test),
    )
    return int(cur.lastrowid)


def _add_reading(conn, product_id, lot, value, **kw):
    return vs.add_reading(
        conn,
        product_id=product_id,
        lot_no=lot,
        viscosity=value,
        measured_date=kw.get("measured_date", "2026-01-10"),
        memo=None,
        recipe_material=None,
        material_lot=None,
        created_by="시험",
        created_at="2026-01-10T00:00:00Z",
        blend_record_id=kw.get("blend_record_id"),
    )


def test_add_reading_rejects_test_lot_by_lot_or_link():
    """LOT 자체가 결정한다 — 연계 등록이든, LOT 만 들어온 직접 등록·임포트든 거부."""
    conn = _make_db()
    product = _add_product(conn, "PB")
    real = _add_blend(conn, "PB26011001", product_name="PB")
    test = _add_blend(conn, "T-PB시험26011001", product_name="PB시험", is_test=1)

    assert _add_reading(conn, product["id"], "PB26011001", 49.0, blend_record_id=real) > 0
    with pytest.raises(vs.TestLotError) as by_link:
        _add_reading(conn, product["id"], "T-PB시험26011001", 12.0, blend_record_id=test)
    assert str(by_link.value) == vs.TEST_LOT_DETAIL
    with pytest.raises(vs.TestLotError):
        _add_reading(conn, product["id"], " T-PB시험26011001 ", 12.0)
    count = conn.execute("SELECT COUNT(*) FROM viscosity_readings").fetchone()[0]
    assert count == 1


def test_add_reading_survives_schema_without_blend_records():
    """blend_records 가 없는 최소 스키마(구 단위테스트)에서도 등록이 깨지지 않는다."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE viscosity_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT,
            target REAL, lower_limit REAL, upper_limit REAL, sigma_k REAL DEFAULT 3,
            rpm REAL, temperature REAL, use_reactor INTEGER DEFAULT 0,
            remind_daily INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, created_at TEXT
        );
        CREATE TABLE viscosity_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, lot_no TEXT,
            viscosity REAL, measured_date TEXT, memo TEXT, recipe_material TEXT,
            material_lot TEXT, reactor INTEGER, created_by TEXT, created_at TEXT,
            blend_record_id INTEGER, excluded INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO viscosity_products (code, name, created_at) "
        "VALUES ('PB', 'PB', '2026-01-01T00:00:00Z')"
    )
    product = vs.get_product_by_code(conn, "PB")
    assert _add_reading(conn, product["id"], "PB26011001", 49.0) > 0
    assert vs.analyze_product(conn, product)["stats"]["n"] == 1
    # 시험 표가 없는 스키마: 목록은 비고, 기록 상세는 정식 측정만.
    assert vs.list_test_records(conn)["items"] == []
    assert vs.get_test_reading(conn, 1) is None


def test_reminders_skip_test_records_even_with_a_matching_name():
    """트레이 pending_lots 에 시험 LOT 이 섞이지 않는다(시험명이 반제품 이름과 같아도)."""
    conn = _make_db()
    _add_product(conn, "PB", remind_daily=True)
    _add_blend(conn, "PB26011001", product_name="PB", work_date="2026-01-10")
    _add_blend(conn, "T-PB26011001", product_name="PB", is_test=1, work_date="2026-01-10")

    items = vs.daily_reading_reminders(conn, target_date="2026-01-11")
    assert len(items) == 1
    assert [lot["product_lot"] for lot in items[0]["pending_lots"]] == ["PB26011001"]
    assert items[0]["pending_count"] == 1


def test_service_functions_round_trip_on_minimal_schema():
    """서비스 단위: 등록(시험·완료만) → 목록 → 상세 모양 → 정정 → 삭제."""
    conn = _make_db()
    real = _add_blend(conn, "PB26011001", product_name="PB")
    test = _add_blend(conn, "T-X26011001", product_name="X시험", is_test=1)
    canceled = _add_blend(
        conn, "T-X26011002", product_name="X시험", is_test=1, status="canceled"
    )

    with pytest.raises(vs.TestViscosityError) as not_test:
        vs.add_test_reading(conn, blend_record_id=real, viscosity=1.0, measured_date=None,
                            memo=None, created_by="현장", created_at="t")
    assert not_test.value.status == 400
    with pytest.raises(vs.TestViscosityError) as cancel_err:
        vs.add_test_reading(conn, blend_record_id=canceled, viscosity=1.0, measured_date=None,
                            memo=None, created_by="현장", created_at="t")
    assert cancel_err.value.status == 400
    with pytest.raises(vs.TestViscosityError) as missing:
        vs.add_test_reading(conn, blend_record_id=9999, viscosity=1.0, measured_date=None,
                            memo=None, created_by="현장", created_at="t")
    assert missing.value.status == 404

    saved = vs.add_test_reading(conn, blend_record_id=test, viscosity=10.5,
                                measured_date="2026-01-11", memo=" 메모 ",
                                created_by="현장", created_at="2026-01-11T00:00:00Z")
    assert saved["product_lot"] == "T-X26011001" and saved["memo"] == "메모"
    with pytest.raises(vs.TestViscosityError) as dup:
        vs.add_test_reading(conn, blend_record_id=test, viscosity=11.0, measured_date=None,
                            memo=None, created_by="현장", created_at="t")
    assert dup.value.status == 409

    listed = vs.list_test_records(conn)
    assert [it["id"] for it in listed["items"]] == [test]   # 취소·정식은 없다
    assert listed["items"][0]["viscosity"] == 10.5

    shape = vs.list_readings_for_blend(conn, test)
    assert shape == [{
        "id": saved["id"], "viscosity": 10.5, "measured_date": "2026-01-11", "memo": "메모",
        "lot_no": "T-X26011001", "reactor": None, "product_id": None,
        "product_code": "시험", "product_name": "시험", "created_by": "현장",
        # 시험 점도에는 통계가 없어 제외 개념도 없다 — 정식과 키 모양만 맞춘다(2026-09-21).
        "excluded": False, "exclude_reason": None,
    }]
    assert vs.list_readings_for_blend(conn, real) == []

    assert vs.correct_test_reading(conn, test, viscosity=10.5, updated_by="x", updated_at="u") == {
        "old": 10.5, "new": 10.5, "changed": False,
    }
    assert vs.correct_test_reading(conn, test, viscosity=9.5, updated_by="책임자", updated_at="u")[
        "changed"
    ] is True
    assert vs.get_test_reading(conn, test)["updated_by"] == "책임자"
    assert vs.delete_test_reading(conn, test)["viscosity"] == 9.5
    assert vs.delete_test_reading(conn, test) is None
    assert vs.correct_test_reading(conn, test, viscosity=1.0, updated_by="x", updated_at="u") is None
