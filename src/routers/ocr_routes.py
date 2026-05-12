"""OCR routes — image upload → Gemini parse → product matching → schedule save."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ..auth import get_current_user, require_access_level
from ..database import get_connection
from ..services import production_plan_service as plan_svc
from ..services.gemini_ocr_service import parse_ink_request_bytes
from ..services.product_matcher import match_all, match_summary

logger = logging.getLogger(__name__)


class ConfirmMatchBody(BaseModel):
    plan_id: int
    schedule_date: str
    matches: list[dict] = Field(default_factory=list)


def build_router() -> APIRouter:
    router = APIRouter(
        prefix="/ocr",
    )

    @router.post("/ink-request")
    async def ocr_ink_request(
        request: Request,
        file: UploadFile = File(...),
        plan_id: int | None = None,
    ) -> dict[str, Any]:
        """Upload INK request image → OCR parse → match against registered products."""
        if not file.content_type or not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="IMAGE_REQUIRED")

        image_bytes = await file.read()
        if len(image_bytes) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="FILE_TOO_LARGE")

        # Step 1: Gemini OCR
        try:
            parsed = parse_ink_request_bytes(image_bytes, file.filename or "upload.png")
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        # Step 2: Get registered products and match
        with get_connection() as conn:
            registered = plan_svc.get_registered_products(conn)

        ocr_names = []
        for sheet in parsed.ink_requests:
            for row in sheet.rows:
                ocr_names.append(row.product_name)

        match_results = match_all(ocr_names, registered)
        summary = match_summary(match_results)

        # Build response with match info
        matched_sheets = []
        match_idx = 0
        for sheet in parsed.ink_requests:
            rows_out = []
            for row in sheet.rows:
                from ..services.product_matcher import normalize
                if normalize(row.product_name) in ("TEST", ""):
                    rows_out.append({
                        "machine_no": row.machine_no,
                        "brand": row.brand,
                        "ocr_product_name": row.product_name,
                        "matched_product_name": None,
                        "match_confidence": 0,
                        "match_status": "skip",
                        "candidates": [],
                    })
                    continue
                mr = match_results[match_idx] if match_idx < len(match_results) else None
                match_idx += 1
                rows_out.append({
                    "machine_no": row.machine_no,
                    "brand": row.brand,
                    "ocr_product_name": row.product_name,
                    "matched_product_name": mr.matched_name if mr else None,
                    "match_confidence": mr.confidence if mr else 0,
                    "match_status": mr.status if mr else "none",
                    "candidates": mr.candidates if mr else [],
                })
            matched_sheets.append({
                "request_date": sheet.request_date,
                "shift": sheet.shift,
                "line": sheet.line,
                "rows": rows_out,
            })

        chemicals_out = [c.model_dump() for c in parsed.chemical_requests]

        return {
            "ink_requests": matched_sheets,
            "chemical_requests": chemicals_out,
            "match_summary": summary,
            "registered_product_count": len(registered),
        }

    @router.post("/ink-request/confirm")
    async def confirm_ink_request(body: ConfirmMatchBody, request: Request) -> dict[str, Any]:
        """Confirm OCR matches and save to production plan."""
        current_user = get_current_user(request, required=False)
        created_by = current_user.get("display_name", "") if current_user else ""

        with get_connection() as conn:
            plan_id = body.plan_id
            if not plan_id:
                plan = plan_svc.create_plan(
                    conn,
                    plan_name=f"INK요청 {body.schedule_date}",
                    week_start=body.schedule_date,
                    week_end=body.schedule_date,
                    created_by=created_by,
                )
                plan_id = plan["id"]

            schedules = []
            for m in body.matches:
                schedules.append({
                    "schedule_date": body.schedule_date,
                    "machine_no": m.get("machine_no"),
                    "line_type": m.get("line_type"),
                    "shift": m.get("shift"),
                    "brand": m.get("brand"),
                    "ocr_product_name": m.get("ocr_product_name"),
                    "matched_product_name": m.get("matched_product_name"),
                    "match_confidence": m.get("match_confidence", 1.0),
                    "match_status": "confirmed",
                    "ink_name": m.get("ink_name"),
                })
            count = plan_svc.save_schedules(conn, plan_id, schedules)

        return {"saved": True, "plan_id": plan_id, "schedule_count": count}

    @router.get("/registered-products")
    async def list_registered_products() -> dict[str, Any]:
        """List all registered product names for matching reference."""
        with get_connection() as conn:
            products = plan_svc.get_registered_products(conn)
        return {"items": products, "total": len(products)}

    @router.get("/plans")
    async def list_plans() -> dict[str, Any]:
        """List all saved production plans."""
        with get_connection() as conn:
            plans = plan_svc.list_plans(conn)
        return {"items": plans, "total": len(plans)}

    @router.get("/plans/{plan_id}")
    async def get_plan_detail(plan_id: int) -> dict[str, Any]:
        """Get plan detail with schedules and chemicals."""
        with get_connection() as conn:
            plan = plan_svc.get_plan(conn, plan_id)
            if not plan:
                raise HTTPException(status_code=404, detail="PLAN_NOT_FOUND")
            schedules = plan_svc.get_schedules(conn, plan_id)
            chemicals = plan_svc.get_chemicals(conn, plan_id)

        # Build weekly board: group by date+shift+line
        board: dict[str, dict] = {}
        for s in schedules:
            key = f"{s['schedule_date']}|{s['shift'] or ''}|{s['line_type'] or ''}"
            if key not in board:
                board[key] = {
                    "schedule_date": s["schedule_date"],
                    "shift": s["shift"],
                    "line": s["line_type"],
                    "machines": [],
                }
            board[key]["machines"].append({
                "machine_no": s["machine_no"],
                "brand": s["brand"],
                "product": s["matched_product_name"] or s["ocr_product_name"],
                "match_status": s["match_status"],
                "ink_name": s["ink_name"],
            })

        return {
            "plan": plan,
            "schedules": schedules,
            "chemicals": chemicals,
            "board": list(board.values()),
        }

    @router.delete("/plans/{plan_id}")
    async def delete_plan(plan_id: int) -> dict[str, Any]:
        """Delete a production plan."""
        with get_connection() as conn:
            plan = plan_svc.get_plan(conn, plan_id)
            if not plan:
                raise HTTPException(status_code=404, detail="PLAN_NOT_FOUND")
            conn.execute("DELETE FROM plan_chemical_requests WHERE plan_id = ?", (plan_id,))
            conn.execute("DELETE FROM plan_schedules WHERE plan_id = ?", (plan_id,))
            conn.execute("DELETE FROM production_plans WHERE id = ?", (plan_id,))
            conn.commit()
        return {"deleted": True, "plan_id": plan_id}

    return router
