"""근태 엑셀 열 인식 진단 + 폴더 경로 환경변수.

배경: ERP 는 2026-06 에 열 순서를 바꿔 내보내기 시작했고, 고정 인덱스로 읽던 코드가
성명을 '근무공장' 값으로 잡는 등 조용히 어긋났다(test_attendance_excel_column_map).
헤더 자동 매핑이 그 대책이고, 선택 열을 못 찾았을 때의 경고가 안전망이다 — 그런데 그
경고가 서버 로그에만 남아 아무도 보지 않았다. 화면(책임자)이 물어볼 수 있게 진단을
연 것이 header_diagnostics 다(2026-08-08).

같은 날 근태 폴더 경로도 환경변수로 열었다. 같은 C:\\ErpExcel 을 읽는 자재 LOT 은
IRMS_ERP_EXCEL_DIR 로 옮길 수 있는데 근태만 상수로 박혀 있었다.
"""

from __future__ import annotations

import importlib

from openpyxl import Workbook

_GROUP = [""] * 15 + ["평일근무시간"] * 4 + ["휴일근무시간"] * 4 + ["", "", "", "비고"]
_SUB = [
    "근무일자", "요일", "구분", "사번", "남여", "근무공장", "성명", "근무직구분",
    "부서명", "근무조구분", "근무타임", "근태코드", "출근", "퇴근", "익일",
    "조출", "정상", "연장", "야근",
    "조출", "정상", "연장", "야근",
    "지각시간", "조퇴시간", "외출시간", "",
]


def _write(dir_path, sub_row) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(_GROUP)
    ws.append(sub_row)
    ws.append([
        "2026-08-03", "월", "평일", "120206", "남", "1공장", "김철수", "생산직",
        "합성부", "주간", "주간(09-18)", "", "09:00", "18:00", "",
        0, 8.0, 0, 0, 0, 0, 0, 0, "", "", "", "",
    ])
    wb.save(dir_path / "monthly_attendance_2026-08.xlsx")


def _reload_with_dir(monkeypatch, dir_path):
    monkeypatch.setenv("IRMS_ATTENDANCE_EXCEL_DIR", str(dir_path))
    import src.services.attendance_excel.files as files_mod

    importlib.reload(files_mod)
    import src.services.attendance_excel as ex

    return ex


def test_attendance_dir_follows_the_environment(tmp_path, monkeypatch):
    """근태 폴더를 환경변수로 옮길 수 있다 — 자재 LOT 과 같은 방식."""
    import src.services.attendance_excel.files as files_mod

    monkeypatch.setenv("IRMS_ATTENDANCE_EXCEL_DIR", str(tmp_path))
    importlib.reload(files_mod)
    assert files_mod.ATTENDANCE_DIR == tmp_path

    # 근태 전용 변수가 없으면 자재 LOT 과 같은 변수를 따른다(보통 한꺼번에 옮긴다).
    monkeypatch.delenv("IRMS_ATTENDANCE_EXCEL_DIR")
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("IRMS_ERP_EXCEL_DIR", str(shared))
    importlib.reload(files_mod)
    assert files_mod.ATTENDANCE_DIR == shared

    # 둘 다 없으면 기존 기본 경로.
    monkeypatch.delenv("IRMS_ERP_EXCEL_DIR")
    importlib.reload(files_mod)
    assert str(files_mod.ATTENDANCE_DIR) == r"C:\ErpExcel"


def test_header_diagnostics_reports_a_healthy_file(tmp_path, monkeypatch):
    _write(tmp_path, _SUB)
    ex = _reload_with_dir(monkeypatch, tmp_path)
    result = ex.header_diagnostics("2026-08")
    assert len(result) == 1
    assert result[0]["ok"] is True
    assert result[0]["fallback"] is False
    assert result[0]["missing_optional"] == []


def test_header_diagnostics_catches_a_renamed_optional_column(tmp_path, monkeypatch):
    """선택 열이 헤더에서 사라지면 그 열만 옛 인덱스로 폴백된다 — 조용히 넘기지 않는다."""
    sub = list(_SUB)
    sub[14] = ""          # '익일' 헤더가 사라진 상황(ERP 가 이름을 바꾸면 이렇게 된다)
    _write(tmp_path, sub)
    ex = _reload_with_dir(monkeypatch, tmp_path)
    result = ex.header_diagnostics("2026-08")
    assert result[0]["ok"] is False
    assert result[0]["fallback"] is False
    assert result[0]["missing_optional"] == ["next_day"]


def test_header_diagnostics_flags_a_whole_file_fallback(tmp_path, monkeypatch):
    """필수 열을 못 찾으면 파일 전체를 옛 인덱스로 읽는다 — 가장 위험한 상태."""
    sub = [""] * len(_SUB)   # 헤더가 통째로 다른 형식
    _write(tmp_path, sub)
    ex = _reload_with_dir(monkeypatch, tmp_path)
    result = ex.header_diagnostics("2026-08")
    assert result[0]["ok"] is False
    assert result[0]["fallback"] is True


def test_header_diagnostics_is_empty_without_a_file(tmp_path, monkeypatch):
    ex = _reload_with_dir(monkeypatch, tmp_path)
    assert ex.header_diagnostics("2026-08") == []


def test_header_check_route_requires_a_manager(tmp_path, monkeypatch):
    """진단은 책임자 전용 — 열 구성은 직원이 볼 정보가 아니다."""
    _write(tmp_path, _SUB)
    _reload_with_dir(monkeypatch, tmp_path)
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    assert client.get("/api/attendance/admin/header-check").status_code == 401

    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})
    res = client.get("/api/attendance/admin/header-check")
    assert res.status_code == 200
    body = res.json()
    assert body["month"] == body["month"]
    assert isinstance(body["files"], list)
