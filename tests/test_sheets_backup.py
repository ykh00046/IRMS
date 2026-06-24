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
