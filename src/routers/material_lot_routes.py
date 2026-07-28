"""ERP 원재료 LOT 검증 라우터.

배합 화면(무로그인 공용 단말)에서 자재 LOT 가 유효한지(ERP 엑셀 재고>0 또는 수동
LOT) 조회하고, 책임자가 즉석 인증으로 수동 LOT 를 추가·삭제한다.

엔드포인트:
    GET    /material-lots/check?code&lot      LOT 검증(무인증)
    GET    /material-lots/status              파일·자재별 LOT 목록(무인증 조회)
    POST   /material-lots/manual              수동 LOT 추가(책임자 전용)
    DELETE /material-lots/manual/{id}         수동 LOT 삭제(책임자 전용)
    POST   /material-lots/manual-verify       즉석 책임자 인증으로 추가(무인증 + rate-limit)

⚠️ 이 파일에는 `from __future__ import annotations` 를 넣지 말 것.
   slowapi @limiter.limit + Pydantic 본문 모델 조합에서 타입힌트가 문자열로 남아
   FastAPI 가 본문을 못 풀어 422 전면 장애를 일으킨 실사고가 있다
   (src/routers/attendance_routes.py 상단 주석 참고).
"""

import datetime as _dt
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth import (
    authenticate_manager_worker,
    authenticate_user,
    has_access_level,
    require_access_level,
)
from ..db import get_db, utc_now_text, write_audit_log
from ..limiter import limiter
from ..services import erp_lot_service


class ManualLotBody(BaseModel):
    material_code: str = Field(min_length=1, max_length=50)
    lot: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=500)


class ManualVerifyBody(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)
    material_code: str = Field(min_length=1, max_length=50)
    lot: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=500)


def _verify_manager_credentials(username: str, password: str) -> dict[str, Any] | None:
    """책임자 자격 검증 — blend_routes 의 manager-verify 검증 로직 재사용.

    이름 기반 책임자(workers.is_manager) 우선, 없으면 레거시 admin(users) 폴백.
    유효 계정이지만 책임자 권한이 아니면 ('non_manager', legacy_dict) 로 구분 반환.
    통과하면 user dict, 자체 없으면 None.
    """
    user = authenticate_manager_worker(username, password)
    if user is not None:
        return user
    legacy = authenticate_user(username, password)
    if legacy is not None and has_access_level(legacy, "manager"):
        return legacy
    return None


def _stale_days(file_date_iso: str | None) -> int | None:
    """파일명 날짜와 오늘 차이(일). 파일명 날짜가 없으면 None."""
    if not file_date_iso:
        return None
    try:
        d = _dt.date.fromisoformat(file_date_iso)
    except ValueError:
        return None
    return (_dt.date.today() - d).days


def build_router() -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------------
    # GET /material-lots/check — LOT 검증(무인증, 배합 화면용)
    # ------------------------------------------------------------------
    @router.get("/material-lots/check")
    def check_material_lot(
        code: str = Query(..., min_length=1, max_length=50),
        lot: str = Query(..., min_length=1, max_length=100),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        return erp_lot_service.check_lot(connection, code, lot)

    # ------------------------------------------------------------------
    # GET /material-lots/status — 파일·자재별 LOT 목록(무인증 조회)
    # ------------------------------------------------------------------
    @router.get("/material-lots/status")
    def material_lot_status(
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        parsed = erp_lot_service.load_lots()
        if parsed is None:
            return {
                "file_name": None,
                "file_date": None,
                "file_ok": False,
                "read_at": None,
                "stale_days": None,
                "items": [],
            }
        # 엑셀 LOT 와 수동 LOT 를 합친다. 수동은 source='manual', valid=True.
        manual_rows = erp_lot_service.list_manual_lots(connection)
        items: list[dict[str, Any]] = []
        # 자재 코드 → items 인덱스(중복 자재 행 방지).
        by_code: dict[str, int] = {}

        def _ensure_item(code: str, name: str) -> dict[str, Any]:
            idx = by_code.get(code)
            if idx is not None:
                return items[idx]
            item = {"code": code, "name": name, "lots": []}
            items.append(item)
            by_code[code] = len(items) - 1
            return item

        for code, entry in parsed["lots"].items():
            item = _ensure_item(code, entry.get("name") or "")
            for lot, lot_entry in entry["lots"].items():
                stock = float(lot_entry.get("stock") or 0.0)
                item["lots"].append(
                    {
                        "lot": lot,
                        "stock": stock,
                        "source": "erp",
                        "valid": stock > 0,
                    }
                )

        for m in manual_rows:
            item = _ensure_item(m["material_code"], "")
            # 같은 LOT 가 엑셀에도 있으면 수동(source=manual, valid=True)로 덮어쓴다.
            item["lots"] = [
                lot for lot in item["lots"] if lot["lot"] != m["lot"]
            ]
            item["lots"].append(
                {
                    "lot": m["lot"],
                    "stock": None,
                    "source": "manual",
                    "valid": True,
                    "id": m["id"],
                    "note": m["note"],
                }
            )

        return {
            "file_name": parsed["file_name"],
            "file_date": parsed["file_date"],
            "file_ok": True,
            "read_at": parsed["read_at"],
            "stale_days": _stale_days(parsed["file_date"]),
            "items": items,
        }

    # ------------------------------------------------------------------
    # POST /material-lots/manual — 수동 LOT 추가(책임자 전용)
    # ------------------------------------------------------------------
    @router.post(
        "/material-lots/manual",
        dependencies=[Depends(require_access_level("manager"))],
    )
    def add_manual_lot(
        body: ManualLotBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        material_code = body.material_code.strip()
        lot = body.lot.strip()
        note = (body.note or "").strip() or None
        try:
            cur = connection.execute(
                "INSERT INTO manual_material_lots "
                "(material_code, lot, note, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (material_code, lot, note, current_user.get("display_name"), utc_now_text()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="이미 등록된 수동 LOT 입니다.",
            )
        new_id = cur.lastrowid
        write_audit_log(
            connection,
            action="material_lot_added",
            actor=current_user,
            target_type="manual_material_lot",
            target_id=new_id,
            target_label=f"{material_code} / {lot}",
            details={"material_code": material_code, "lot": lot, "note": note},
        )
        connection.commit()
        return {
            "status": "ok",
            "id": new_id,
            "material_code": material_code,
            "lot": lot,
            "note": note,
        }

    # ------------------------------------------------------------------
    # DELETE /material-lots/manual/{id} — 수동 LOT 삭제(책임자 전용)
    # ------------------------------------------------------------------
    @router.delete(
        "/material-lots/manual/{lot_id}",
        dependencies=[Depends(require_access_level("manager"))],
    )
    def delete_manual_lot(
        lot_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT id, material_code, lot FROM manual_material_lots WHERE id = ?",
            (lot_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="수동 LOT 를 찾을 수 없습니다.")
        connection.execute(
            "DELETE FROM manual_material_lots WHERE id = ?", (lot_id,)
        )
        write_audit_log(
            connection,
            action="material_lot_deleted",
            actor=current_user,
            target_type="manual_material_lot",
            target_id=lot_id,
            target_label=f"{row['material_code']} / {row['lot']}",
            details={"material_code": row["material_code"], "lot": row["lot"]},
        )
        connection.commit()
        return {"status": "ok", "deleted": lot_id}

    # ------------------------------------------------------------------
    # POST /material-lots/manual-verify — 즉석 책임자 인증으로 추가
    # (무인증 + slowapi rate-limit). blend_routes.manager-verify 검증 로직 재사용.
    # ------------------------------------------------------------------
    @router.post("/material-lots/manual-verify")
    @limiter.limit("10/minute")
    def manual_verify_add(
        request: Request,
        body: ManualVerifyBody,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        username = body.username.strip()
        password = body.password
        user = _verify_manager_credentials(username, password)
        if user is None:
            raise HTTPException(
                status_code=401, detail="INVALID_CREDENTIALS"
            )
        material_code = body.material_code.strip()
        lot = body.lot.strip()
        note = (body.note or "").strip() or None
        approver = user.get("display_name") or user.get("username") or "책임자"
        try:
            cur = connection.execute(
                "INSERT INTO manual_material_lots "
                "(material_code, lot, note, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (material_code, lot, note, approver, utc_now_text()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="이미 등록된 수동 LOT 입니다.",
            )
        new_id = cur.lastrowid
        write_audit_log(
            connection,
            action="material_lot_added",
            actor=user,
            target_type="manual_material_lot",
            target_id=new_id,
            target_label=f"{material_code} / {lot}",
            details={
                "material_code": material_code,
                "lot": lot,
                "note": note,
                "via": "manual-verify",
            },
        )
        connection.commit()
        return {
            "status": "ok",
            "id": new_id,
            "material_code": material_code,
            "lot": lot,
            "note": note,
            "approved_by": approver,
        }

    return router
