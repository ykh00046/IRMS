"""Google Sheets 백업(선택) — Program-estimation v3 GoogleSheetsBackup 이식.

배합 기록을 Google 스프레드시트 "배합 기록" 워크시트에 누적 백업한다. gspread/
google-auth 와 서비스 계정 자격증명이 필요한 **선택 기능**으로, 미설치/미설정 시
비활성(명확한 메시지) 처리한다. 설정은 data/sheets_config.json.
"""

import json
import os
from typing import Any

from .. import config

try:  # 선택 의존성
    import gspread
    from google.oauth2.service_account import Credentials
    _GSPREAD = True
except ImportError:
    gspread = None
    Credentials = None
    _GSPREAD = False

_SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
_WORKSHEET = "배합 기록"

# 백업 컬럼(원본 BACKUP_COLUMNS와 동일 순서)
BACKUP_COLUMNS = [
    "제품LOT", "레시피명", "작업자", "작업일자", "작업시간", "총배합량", "스케일",
    "품목코드", "품목명", "자재LOT", "배합비율", "이론량", "실제량", "순서",
]

_DEFAULTS = {"enabled": False, "spreadsheet_url": "", "credentials_file": ""}


def _path():
    return config.DATA_DIR / "sheets_config.json"


def load_config() -> dict[str, Any]:
    cfg = dict(_DEFAULTS)
    p = _path()
    if p.exists():
        try:
            cfg.update({k: v for k, v in json.loads(p.read_text(encoding="utf-8")).items() if k in _DEFAULTS})
        except (ValueError, OSError):
            pass
    return cfg


def save_config(updates: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    if "enabled" in updates:
        cfg["enabled"] = bool(updates["enabled"])
    if "spreadsheet_url" in updates:
        cfg["spreadsheet_url"] = str(updates["spreadsheet_url"] or "").strip()
    if "credentials_file" in updates:
        cfg["credentials_file"] = str(updates["credentials_file"] or "").strip()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def status() -> dict[str, Any]:
    cfg = load_config()
    creds_ok = bool(cfg["credentials_file"]) and os.path.exists(cfg["credentials_file"])
    return {
        "gspread_available": _GSPREAD,
        "enabled": cfg["enabled"],
        "spreadsheet_url": cfg["spreadsheet_url"],
        "credentials_file": cfg["credentials_file"],
        "credentials_exists": creds_ok,
        "configured": bool(cfg["spreadsheet_url"]) and creds_ok,
    }


def build_backup_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """배합 기록(상세 포함) 목록 → 백업 행(자재별 1행) dict 목록."""
    rows: list[dict[str, Any]] = []
    for rec in records:
        recipe = rec.get("product_name", "")
        if rec.get("ink_name"):
            recipe = f"{recipe} / {rec['ink_name']}"
        for i, d in enumerate(rec.get("details", []) or [], start=1):
            rows.append({
                "제품LOT": rec.get("product_lot", ""),
                "레시피명": recipe,
                "작업자": rec.get("worker", ""),
                "작업일자": rec.get("work_date", ""),
                "작업시간": rec.get("work_time") or "",
                "총배합량": rec.get("total_amount", ""),
                "스케일": rec.get("scale") or "",
                "품목코드": d.get("material_code") or "",
                "품목명": d.get("material_name", ""),
                "자재LOT": d.get("material_lot") or "",
                "배합비율": d.get("ratio", ""),
                "이론량": d.get("theory_amount", ""),
                "실제량": d.get("actual_amount", ""),
                "순서": i,
            })
    return rows


def push_records(records: list[dict[str, Any]]) -> tuple[bool, str]:
    """배합 기록을 Google Sheets에 누적. (성공여부, 메시지)."""
    cfg = load_config()
    if not _GSPREAD:
        return False, "gspread/google-auth 미설치 — 'pip install gspread google-auth' 후 사용하세요."
    if not cfg["enabled"]:
        return False, "Google Sheets 백업이 비활성화되어 있습니다."
    if not cfg["spreadsheet_url"] or not os.path.exists(cfg["credentials_file"] or ""):
        return False, "설정 미완료 — 스프레드시트 URL과 서비스계정 인증 파일을 지정하세요."

    rows = build_backup_rows(records)
    if not rows:
        return True, "백업할 기록이 없습니다."

    try:
        creds = Credentials.from_service_account_file(cfg["credentials_file"], scopes=_SCOPES)
        client = gspread.authorize(creds)
        ss = client.open_by_url(cfg["spreadsheet_url"])
        try:
            ws = ss.worksheet(_WORKSHEET)
        except gspread.exceptions.WorksheetNotFound:
            ws = ss.add_worksheet(title=_WORKSHEET, rows=1000, cols=len(BACKUP_COLUMNS))
            ws.append_row(BACKUP_COLUMNS)
        header = ws.row_values(1)
        if not header:
            ws.insert_row(BACKUP_COLUMNS, 1)
            header = BACKUP_COLUMNS
        data = [[r.get(col, "") for col in header] for r in rows]
        ws.append_rows(data)
        return True, f"{len(rows)}행을 Google Sheets에 백업했습니다."
    except Exception as exc:  # noqa: BLE001 — 외부 API 다양한 예외를 사용자 메시지로
        return False, f"백업 실패: {exc}"
