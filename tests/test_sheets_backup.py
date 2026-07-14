"""Google Sheets 백업(선택) — 행 변환·설정·graceful 동작."""

import importlib


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path))
    import src.config as cfg
    importlib.reload(cfg)
    from src.services import sheets_backup
    importlib.reload(sheets_backup)
    return sheets_backup


def test_build_backup_rows_columns(tmp_path, monkeypatch):
    sb = _fresh(tmp_path, monkeypatch)
    rows = sb.build_backup_rows([{
        "product_lot": "L1", "product_name": "제품A", "ink_name": "잉크X",
        "worker": "김도현", "work_date": "2026-06-25", "work_time": "10:00",
        "total_amount": 1000, "scale": "M-65",
        "details": [
            {"material_code": "MC1", "material_name": "HEMA", "material_lot": "MN1",
             "ratio": 71.4, "theory_amount": 714, "actual_amount": 714},
            {"material_code": "MC2", "material_name": "NVP", "material_lot": "MN2",
             "ratio": 28.6, "theory_amount": 286, "actual_amount": 286},
        ],
    }])
    assert len(rows) == 2
    assert set(rows[0]) == set(sb.BACKUP_COLUMNS)
    assert rows[0]["제품LOT"] == "L1"
    assert rows[0]["레시피명"] == "제품A / 잉크X"
    assert rows[0]["순서"] == 1
    assert rows[1]["순서"] == 2


def test_status_keys(tmp_path, monkeypatch):
    sb = _fresh(tmp_path, monkeypatch)
    s = sb.status()
    for k in ("gspread_available", "enabled", "spreadsheet_url", "configured"):
        assert k in s
    assert s["enabled"] is False  # 기본 비활성


def test_push_disabled_is_graceful(tmp_path, monkeypatch):
    sb = _fresh(tmp_path, monkeypatch)
    ok, msg = sb.push_records([{"product_lot": "L1", "details": []}])
    assert ok is False
    assert isinstance(msg, str) and msg


def test_config_roundtrip_creds_missing(tmp_path, monkeypatch):
    sb = _fresh(tmp_path, monkeypatch)
    sb.save_config({"spreadsheet_url": "https://x", "credentials_file": "/nope.json", "enabled": True})
    s = sb.status()
    assert s["spreadsheet_url"] == "https://x"
    assert s["enabled"] is True
    assert s["configured"] is False  # 인증 파일 부재


class _FakeWorksheet:
    """gspread 워크시트 흉내 — 시트에 실제로 남는 내용을 추적한다."""

    def __init__(self):
        self.rows: list[list] = []

    def row_values(self, n):
        return list(self.rows[n - 1]) if len(self.rows) >= n else []

    def append_rows(self, data):
        self.rows.extend([list(r) for r in data])

    def append_row(self, row):
        self.rows.append(list(row))

    def insert_row(self, row, index):
        self.rows.insert(index - 1, list(row))

    def clear(self):
        self.rows = []

    def update(self, data):
        self.rows = [list(r) for r in data]


class _FakeSpreadsheet:
    def __init__(self, ws):
        self._ws = ws

    def worksheet(self, title):
        return self._ws

    def add_worksheet(self, title, rows, cols):
        return self._ws


def _install_fake_gspread(sb, monkeypatch, ws):
    """gspread/Credentials/설정을 가짜로 갈아끼워 push_records 를 실제로 태운다."""
    import types

    fake_gspread = types.SimpleNamespace(
        authorize=lambda creds: types.SimpleNamespace(
            open_by_url=lambda url: _FakeSpreadsheet(ws)
        ),
        exceptions=types.SimpleNamespace(WorksheetNotFound=KeyError),
    )
    monkeypatch.setattr(sb, "_GSPREAD", True)
    monkeypatch.setattr(sb, "gspread", fake_gspread, raising=False)
    monkeypatch.setattr(
        sb, "Credentials",
        types.SimpleNamespace(from_service_account_file=lambda f, scopes: object()),
        raising=False,
    )
    monkeypatch.setattr(sb, "load_config", lambda: {
        "enabled": True, "spreadsheet_url": "https://sheet", "credentials_file": __file__,
    })


def test_push_records_is_idempotent(tmp_path, monkeypatch):
    """감사 F-6: 백업을 두 번 눌러도 시트 내용은 같다 (덧붙이기 아님 — 미러).

    옛 구현은 매번 전량을 append 해서 두 번 실행하면 모든 기록이 두 벌씩 쌓였다.
    """
    sb = _fresh(tmp_path, monkeypatch)
    ws = _FakeWorksheet()
    _install_fake_gspread(sb, monkeypatch, ws)

    records = [{
        "product_lot": "L1", "product_name": "제품A", "ink_name": "잉크X",
        "worker": "김도현", "work_date": "2026-06-25", "work_time": "10:00",
        "total_amount": 1000, "scale": "M-65",
        "details": [
            {"material_code": "MC1", "material_name": "HEMA", "material_lot": "MN1",
             "ratio": 71.4, "theory_amount": 714, "actual_amount": 714},
            {"material_code": "MC2", "material_name": "NVP", "material_lot": "MN2",
             "ratio": 28.6, "theory_amount": 286, "actual_amount": 286},
        ],
    }]

    ok, _ = sb.push_records(records)
    assert ok is True
    after_first = [list(r) for r in ws.rows]
    assert len(after_first) == 3            # 헤더 1 + 자재 2

    ok, _ = sb.push_records(records)         # 같은 기록으로 다시 백업
    assert ok is True
    assert ws.rows == after_first            # 두 벌로 늘어나지 않는다


def test_push_records_reflects_deletions(tmp_path, monkeypatch):
    """기록이 줄면 시트도 줄어든다 — append-only 면 취소된 기록이 시트에 영영 남는다."""
    sb = _fresh(tmp_path, monkeypatch)
    ws = _FakeWorksheet()
    _install_fake_gspread(sb, monkeypatch, ws)

    two = [{"product_lot": "L1", "product_name": "A", "details": [
        {"material_name": "M1"}, {"material_name": "M2"}]}]
    one = [{"product_lot": "L1", "product_name": "A", "details": [
        {"material_name": "M1"}]}]

    sb.push_records(two)
    assert len(ws.rows) == 3
    sb.push_records(one)
    assert len(ws.rows) == 2                 # 헤더 1 + 자재 1
