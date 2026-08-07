import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..auth import get_current_user, require_access_level
from ..db import get_connection, list_audit_logs, write_audit_log
from ..services import blend_service, dhr_pdf, sheets_backup, signature_config, signature_samples


PASSWORD_EXPIRATION_NOTICE = "초기화된 비밀번호는 임시 비밀번호입니다. 다음 로그인 시 반드시 변경해주세요."


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_access_level("manager"))])

    # NOTE: 레거시 users 테이블 CRUD 6종(/admin/users GET·POST·PATCH·DELETE,
    #       /admin/users/{id}/password, /admin/deactivate-others)은 제거했다
    #       (2026-08-08). 인증 단순화(2026-06-24) 이후 화면에서 아무도 부르지 않는
    #       채로 남아 있었고, 그중 deactivate-others 는 UI·확인창 없이 admin 외 전
    #       계정을 일괄 비활성화하는 파괴적 경로였다. 계정 관리는 사용자 관리 화면의
    #       담당자 명단(workers)이 담당하고, admin 비밀번호는 '내 비밀번호 변경'으로 바꾼다.

    @router.get("/admin/audit-logs")
    def admin_list_audit_logs(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        action: str | None = None,
    ) -> dict[str, Any]:
        with get_connection() as connection:
            items = list_audit_logs(connection, limit=limit, offset=offset, action=action)
        return {"items": items, "total": len(items)}

    @router.get("/admin/signature-config")
    def admin_get_signature_config() -> dict[str, Any]:
        """배합일지 서명 합성·스캔 파라미터(관리자 튜닝)."""
        return {
            "config": signature_config.load(),
            "defaults": signature_config.DEFAULTS,
            "ranges": {k: list(v) for k, v in signature_config.RANGES.items()},
        }

    @router.put("/admin/signature-config")
    def admin_save_signature_config(body: dict[str, Any], request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        cfg = signature_config.save(body or {})
        with get_connection() as connection:
            write_audit_log(
                connection,
                action="signature_config_updated",
                actor=current_user,
                target_type="signature_config",
                target_label="배합일지 서명 설정",
                details=cfg,
            )
            connection.commit()
        return {"config": cfg}

    @router.get("/admin/signature-preview")
    def admin_signature_preview(worker: str | None = Query(default=None)) -> StreamingResponse:
        """현재 설정으로 합성한 샘플 배합일지 미리보기 PNG."""
        png = dhr_pdf.build_signature_preview_png(worker)
        return StreamingResponse(io.BytesIO(png), media_type="image/png")

    @router.get("/admin/signature-samples")
    def admin_list_signature_samples() -> dict[str, Any]:
        """작업자 서명 샘플 목록(역할/작업자별)."""
        return {"roles": signature_samples.ROLES, "items": signature_samples.list_samples()}

    @router.post("/admin/signature-samples")
    def admin_add_signature_sample(body: dict[str, Any], request: Request) -> dict[str, Any]:
        import base64
        current_user = get_current_user(request)
        role = str(body.get("role") or "")
        worker = str(body.get("worker") or "")
        image = str(body.get("image_data") or "")
        if "," in image:
            image = image.split(",", 1)[1]
        try:
            data = base64.b64decode(image)
            fname = signature_samples.add_sample(role, worker, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            raise HTTPException(status_code=400, detail="이미지 디코딩에 실패했습니다.")
        with get_connection() as connection:
            write_audit_log(
                connection, action="signature_sample_added", actor=current_user,
                target_type="signature_sample", target_label=fname,
            )
            connection.commit()
        return {"ok": True, "filename": fname, "items": signature_samples.list_samples()}

    @router.delete("/admin/signature-samples/{filename}")
    def admin_delete_signature_sample(filename: str, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        if not signature_samples.delete_sample(filename):
            raise HTTPException(status_code=404, detail="샘플을 찾을 수 없습니다.")
        with get_connection() as connection:
            write_audit_log(
                connection, action="signature_sample_deleted", actor=current_user,
                target_type="signature_sample", target_label=filename,
            )
            connection.commit()
        return {"ok": True, "items": signature_samples.list_samples()}

    @router.get("/admin/sheets-config")
    def admin_get_sheets_config() -> dict[str, Any]:
        """Google Sheets 백업 설정·상태."""
        return sheets_backup.status()

    @router.put("/admin/sheets-config")
    def admin_save_sheets_config(body: dict[str, Any], request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        sheets_backup.save_config(body or {})
        with get_connection() as connection:
            write_audit_log(
                connection,
                action="sheets_config_updated",
                actor=current_user,
                target_type="sheets_config",
                target_label="Google Sheets 백업 설정",
                details=sheets_backup.status(),
            )
            connection.commit()
        return sheets_backup.status()

    @router.post("/admin/sheets-backup")
    def admin_sheets_backup(request: Request) -> dict[str, Any]:
        """전체 배합 기록을 Google Sheets에 백업."""
        current_user = get_current_user(request)
        with get_connection() as connection:
            recs = blend_service.list_blend_records(connection, limit=10000)
            full = [blend_service.get_blend_record(connection, r["id"]) for r in recs]
        ok, message = sheets_backup.push_records([r for r in full if r])
        with get_connection() as connection:
            write_audit_log(
                connection,
                action="sheets_backup_run",
                actor=current_user,
                target_type="sheets_backup",
                target_label="Google Sheets 백업 실행",
                details={"ok": ok, "message": message, "records": len(full)},
            )
            connection.commit()
        return {"ok": ok, "message": message}

    return router
