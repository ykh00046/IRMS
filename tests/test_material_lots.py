"""ERP 원재료 LOT 검증 서버 코어 검증.

openpyxl 로 임시 디렉터리에 ERP_YYYY-MM-DD.xlsx 두 개를 만들어(monkeypatch 로
IRMS_ERP_EXCEL_DIR 지정 + 서비스 캐시 초기화) 최신 파일 선택·재고 판정·수동 LOT·
권한·즉석 인증(manual-verify) 을 검증한다.

커버:
  - 최신 파일(파일명 날짜) 선택
  - 재고>0 → valid(source=erp), 재고 0 → invalid(source=erp, 소진)
  - '*'/빈 LOT 행 무시, 합계/빈행(창고 열 빈) 무시
  - 엑셀에 없는 LOT → invalid(source=None)
  - 수동 LOT 추가 후 valid(source=manual) + audit 행
  - 파일 없음 → file_ok=False + valid=True(fail-open)
  - POST/DELETE manual 비로그인 → 401/403
  - manual-verify: 잘못된 비번 401, 올바른 자격 추가 성공
"""

import importlib
import sqlite3

import pytest
from openpyxl import Workbook


# ---------------- 공통 픽스처/헬퍰 ----------------


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _login(client, username="admin", password="admin"):
    res = client.post(
        "/api/auth/management-login", json={"username": username, "password": password}
    )
    assert res.status_code == 200, res.text
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _csrf_headers(client):
    """무인증 POST(manual-verify 등)용 CSRF 헤더 — GET 한 번으로 쿠키를 받아온다."""
    client.get("/api/material-lots/status")
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


HEADERS_ROW = (
    "창고", "구분", "품목코드", "품목명", "대분류", "중분류", "규격",
    "Lot.No", "기초", "입고", "출고", "재고", "검사대기", "단위",
)


def _write_erp_file(path, rows):
    """rows(각 행은 14열 튜플/리스트) 로 ERP 파일을 만든다. 헤더 자동 추가."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet"
    ws.append(list(HEADERS_ROW))
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    wb.close()


@pytest.fixture(autouse=True)
def _isolate_erp_dir(tmp_path, monkeypatch):
    """각 테스트마다 빈 임시 디렉터리를 IRMS_ERP_EXCEL_DIR 로 지정 + 캐시 초기화."""
    monkeypatch.setenv("IRMS_ERP_EXCEL_DIR", str(tmp_path))
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    yield
    svc.reset_cache()


@pytest.fixture(autouse=True)
def _cleanup_manual_lots():
    """각 테스트 종료 후 이 모듈이 만든 수동 LOT 행을 지운다(테스트 DB 오염 방지).

    init_db 가 아직 실행되지 않은 경로(get_connection 직접 호출)에서는 테이블이
    없을 수 있어 OperationalError 가 난다 — 그냥 무시(다른 테스트가 init_db 를
    돌린 뒤엔 항상 존재)."""
    yield
    from src.db import get_connection

    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM manual_material_lots")
            conn.execute(
                "DELETE FROM audit_logs "
                "WHERE action IN ('material_lot_added','material_lot_deleted')"
            )
            conn.commit()
    except sqlite3.OperationalError:
        pass


# ---------------- 1. 최신 파일 선택 ----------------


def test_latest_file_picks_most_recent_date(tmp_path):
    older = tmp_path / "ERP_2026-01-01.xlsx"
    newer = tmp_path / "ERP_2026-01-02.xlsx"
    _write_erp_file(older, [])
    _write_erp_file(newer, [])

    import src.services.erp_lot_service as svc

    assert svc.latest_erp_file() == str(newer)


def test_latest_file_ignores_unparseable_names(tmp_path):
    bad = tmp_path / "ERP_misc.xlsx"
    good = tmp_path / "ERP_2026-01-01.xlsx"
    _write_erp_file(bad, [])
    _write_erp_file(good, [])

    import src.services.erp_lot_service as svc

    assert svc.latest_erp_file() == str(good)


def test_latest_file_none_when_empty(tmp_path):
    import src.services.erp_lot_service as svc

    assert svc.latest_erp_file() is None


# ---------------- 2. 재고 판정 + 행 무시 규칙 ----------------


def _seed_stock_rows():
    """검증용 데이터 행:
    AC0001 / LOT-A : 재고 100 → valid(erp)
    AC0001 / LOT-Z : 재고 0   → invalid(erp, 소진)
    AC0001 / '*'   : 무시
    AC0001 / 빈    : 무시
    합계행(창고 빈): 무시
    AC0002 / LOT-B : 재고 50 → valid(erp)
    """
    return [
        ("원료창고", "원자재", "AC0001", "자재A", "원자재", "그룹", "규격", "LOT-A",
         0, 0, 0, 100, 0, "g"),
        ("원료창고", "원자재", "AC0001", "자재A", "원자재", "그룹", "규격", "LOT-Z",
         0, 0, 0, 0, 0, "g"),
        ("원료창고", "원자재", "AC0001", "자재A", "원자재", "그룹", "규격", "*",
         0, 0, 0, 999, 0, "g"),
        ("원료창고", "원자재", "AC0001", "자재A", "원자재", "그룹", "규격", "",
         0, 0, 0, 999, 0, "g"),
        (None, None, None, None, None, None, None, None,
         0, 0, 0, 999, 0, None),  # 합계/빈행(창고 열 빈) → 무시
        ("원료창고", "소모품", "AC0002", "자재B", "소모품", "그룹", "규격", "LOT-B",
         0, 0, 0, 50, 0, "g"),
    ]


def test_check_lot_positive_stock_valid_erp(tmp_path):
    f = tmp_path / "ERP_2026-01-02.xlsx"
    _write_erp_file(f, _seed_stock_rows())
    from src.db import get_connection
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    with get_connection() as conn:
        result = svc.check_lot(conn, "AC0001", "LOT-A")
    assert result["valid"] is True
    assert result["source"] == "erp"
    assert result["stock"] == 100.0
    assert result["file_ok"] is True
    assert result["file_name"] == "ERP_2026-01-02.xlsx"


def test_check_lot_zero_stock_invalid_consumed(tmp_path):
    f = tmp_path / "ERP_2026-01-02.xlsx"
    _write_erp_file(f, _seed_stock_rows())
    from src.db import get_connection
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    with get_connection() as conn:
        result = svc.check_lot(conn, "AC0001", "LOT-Z")
    assert result["valid"] is False
    assert result["source"] == "erp"
    assert result["stock"] == 0.0


def test_check_lot_unknown_lot_invalid(tmp_path):
    f = tmp_path / "ERP_2026-01-02.xlsx"
    _write_erp_file(f, _seed_stock_rows())
    from src.db import get_connection
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    with get_connection() as conn:
        result = svc.check_lot(conn, "AC0001", "NOPE")
    assert result["valid"] is False
    assert result["source"] is None


def test_check_lot_unknown_code_invalid(tmp_path):
    f = tmp_path / "ERP_2026-01-02.xlsx"
    _write_erp_file(f, _seed_stock_rows())
    from src.db import get_connection
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    with get_connection() as conn:
        result = svc.check_lot(conn, "AC9999", "LOT-A")
    assert result["valid"] is False
    assert result["source"] is None


def test_check_lot_strips_whitespace(tmp_path):
    """LOT 비교는 양끝 공백 strip 후 정확 일치."""
    f = tmp_path / "ERP_2026-01-02.xlsx"
    _write_erp_file(f, _seed_stock_rows())
    from src.db import get_connection
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    with get_connection() as conn:
        # 엑셀 LOT-A(재고100) 에 공백 붙여 조회 → strip 후 일치 → valid.
        result = svc.check_lot(conn, "AC0001", "  LOT-A  ")
    assert result["valid"] is True
    assert result["source"] == "erp"


def test_star_and_blank_lot_rows_ignored(tmp_path):
    """'*'/빈 LOT 행은 무시 — 같은 코드라도 LOT 로 조회되지 않는다."""
    f = tmp_path / "ERP_2026-01-02.xlsx"
    _write_erp_file(f, _seed_stock_rows())
    from src.db import get_connection
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    with get_connection() as conn:
        # '*' 행(재고999) 은 무시됐으므로 LOT='*' 조회 시 unknown.
        star = svc.check_lot(conn, "AC0001", "*")
        blank = svc.check_lot(conn, "AC0001", "")
    assert star["source"] is None
    assert star["valid"] is False
    assert blank["source"] is None
    assert blank["valid"] is False


# ---------------- 3. 파일 없음: fail-open ----------------


def test_check_lot_no_file_fail_open():
    from src.db import get_connection
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    with get_connection() as conn:
        result = svc.check_lot(conn, "AC0001", "LOT-A")
    assert result["valid"] is True
    assert result["file_ok"] is False
    assert result["file_name"] is None
    assert result["source"] is None


# ---------------- 4. 수동 LOT 추가 후 valid(manual) ----------------


def test_manual_lot_added_makes_check_valid(tmp_path):
    f = tmp_path / "ERP_2026-01-02.xlsx"
    _write_erp_file(f, _seed_stock_rows())
    client = _client()
    headers = _login(client)
    import src.services.erp_lot_service as svc
    from src.db import get_connection

    svc.reset_cache()
    # 엑셀에 없는 LOT → 추가 전 invalid.
    with get_connection() as conn:
        before = svc.check_lot(conn, "AC0001", "MANUAL1")
    assert before["valid"] is False

    res = client.post(
        "/api/material-lots/manual",
        json={"material_code": "AC0001", "lot": "MANUAL1", "note": "비고"},
        headers=headers,
    )
    assert res.status_code == 200, res.text

    with get_connection() as conn:
        after = svc.check_lot(conn, "AC0001", "MANUAL1")
        # audit 행
        arow = conn.execute(
            "SELECT action FROM audit_logs WHERE action='material_lot_added' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert after["valid"] is True
    assert after["source"] == "manual"
    assert arow is not None


def test_manual_lot_duplicate_409(tmp_path):
    _write_erp_file(tmp_path / "ERP_2026-01-02.xlsx", _seed_stock_rows())
    client = _client()
    headers = _login(client)
    body = {"material_code": "AC0001", "lot": "DUP1"}
    r1 = client.post("/api/material-lots/manual", json=body, headers=headers)
    assert r1.status_code == 200
    r2 = client.post("/api/material-lots/manual", json=body, headers=headers)
    assert r2.status_code == 409


def test_manual_lot_delete(tmp_path):
    _write_erp_file(tmp_path / "ERP_2026-01-02.xlsx", _seed_stock_rows())
    client = _client()
    headers = _login(client)
    created = client.post(
        "/api/material-lots/manual",
        json={"material_code": "AC0001", "lot": "DEL1"},
        headers=headers,
    ).json()
    rid = created["id"]

    res = client.delete(f"/api/material-lots/manual/{rid}", headers=headers)
    assert res.status_code == 200, res.text

    from src.db import get_connection
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    with get_connection() as conn:
        gone = svc.check_lot(conn, "AC0001", "DEL1")
        arow = conn.execute(
            "SELECT action FROM audit_logs WHERE action='material_lot_deleted' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert gone["valid"] is False  # 삭제 후 다시 invalid
    assert arow is not None


def test_manual_lot_delete_not_found_404(tmp_path):
    _write_erp_file(tmp_path / "ERP_2026-01-02.xlsx", _seed_stock_rows())
    client = _client()
    headers = _login(client)
    res = client.delete("/api/material-lots/manual/9999999", headers=headers)
    assert res.status_code == 404


# ---------------- 5. 권한: 비로그인 401/403 ----------------


def test_manual_add_requires_manager():
    client = _client()
    res = client.post(
        "/api/material-lots/manual",
        json={"material_code": "AC0001", "lot": "X1"},
    )
    assert res.status_code in (401, 403)


def test_manual_delete_requires_manager():
    client = _client()
    res = client.delete("/api/material-lots/manual/1")
    assert res.status_code in (401, 403)


# ---------------- 6. manual-verify: 즉석 책임자 인증 ----------------


def test_manual_verify_wrong_password_401(tmp_path):
    _write_erp_file(tmp_path / "ERP_2026-01-02.xlsx", _seed_stock_rows())
    client = _client()
    headers = _csrf_headers(client)
    res = client.post(
        "/api/material-lots/manual-verify",
        json={
            "username": "admin",
            "password": "wrong-password",
            "material_code": "AC0001",
            "lot": "VER1",
        },
        headers=headers,
    )
    assert res.status_code == 401


def test_manual_verify_correct_credentials_adds_lot(tmp_path):
    _write_erp_file(tmp_path / "ERP_2026-01-02.xlsx", _seed_stock_rows())
    client = _client()
    headers = _csrf_headers(client)
    res = client.post(
        "/api/material-lots/manual-verify",
        json={
            "username": "admin",
            "password": "admin",
            "material_code": "AC0001",
            "lot": "VER2",
            "note": "현장추가",
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok"
    assert body["material_code"] == "AC0001"
    assert body["lot"] == "VER2"
    assert body["approved_by"]

    # 추가된 LOT 가 check 에서 valid(manual) 로 보여야 한다.
    from src.db import get_connection
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    with get_connection() as conn:
        result = svc.check_lot(conn, "AC0001", "VER2")
        arow = conn.execute(
            "SELECT details_json FROM audit_logs WHERE action='material_lot_added' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert result["valid"] is True
    assert result["source"] == "manual"
    assert arow is not None
    assert "VER2" in (arow["details_json"] or "")


# ---------------- 7. status 엔드포인트 ----------------


def test_status_lists_lots_with_manual_and_zero_stock(tmp_path):
    """status 는 엑셀 LOT(재고 0 포함, valid=False) 와 수동 LOT(source=manual) 합침."""
    _write_erp_file(tmp_path / "ERP_2026-01-02.xlsx", _seed_stock_rows())
    client = _client()
    headers = _login(client)
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    # 수동 LOT 하나 추가
    client.post(
        "/api/material-lots/manual",
        json={"material_code": "AC0001", "lot": "MANSTAT"},
        headers=headers,
    )
    svc.reset_cache()

    res = client.get("/api/material-lots/status")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["file_ok"] is True
    assert body["file_name"] == "ERP_2026-01-02.xlsx"
    assert body["file_date"] == "2026-01-02"

    by_code = {it["code"]: it for it in body["items"]}
    assert "AC0001" in by_code
    lots = {entry["lot"]: entry for entry in by_code["AC0001"]["lots"]}
    # LOT-A 재고100 → valid erp
    assert lots["LOT-A"]["valid"] is True
    assert lots["LOT-A"]["source"] == "erp"
    # LOT-Z 재고0 → invalid erp (목록에는 포함)
    assert lots["LOT-Z"]["valid"] is False
    assert lots["LOT-Z"]["source"] == "erp"
    # 수동 LOT → valid manual
    assert lots["MANSTAT"]["valid"] is True
    assert lots["MANSTAT"]["source"] == "manual"


def test_status_no_file_returns_empty():
    client = _client()
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    res = client.get("/api/material-lots/status")
    assert res.status_code == 200
    body = res.json()
    assert body["file_ok"] is False
    assert body["items"] == []
    assert body["stale_days"] is None


def test_check_endpoint_via_http(tmp_path):
    """GET /material-lots/check 가 무인증으로 동작."""
    _write_erp_file(tmp_path / "ERP_2026-01-02.xlsx", _seed_stock_rows())
    client = _client()
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    res = client.get(
        "/api/material-lots/check", params={"code": "AC0001", "lot": "LOT-A"}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["valid"] is True
    assert body["source"] == "erp"
    assert body["stock"] == 100.0


def test_stale_days_computed(tmp_path):
    _write_erp_file(tmp_path / "ERP_2026-01-02.xlsx", _seed_stock_rows())
    client = _client()
    import src.services.erp_lot_service as svc
    import datetime as _dt

    svc.reset_cache()
    res = client.get("/api/material-lots/status")
    body = res.json()
    expected = (_dt.date.today() - _dt.date(2026, 1, 2)).days
    assert body["stale_days"] == expected
