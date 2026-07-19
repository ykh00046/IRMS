"""앱 설정 엔드포인트 — 저울 전용 입력 모드(scale-only-input) 토글.

GET 은 인증 불요(배합 화면이 무로그인이므로 공개). PUT 은 책임자 전용이며
감사 로그를 남긴다. 기본값은 OFF(행 부재 = false).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_access_level
from ..db import get_connection, write_audit_log
from ..services import settings_service


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/settings/scale-only-input")
    def get_scale_only_input() -> dict[str, Any]:
        """저울 전용 입력 모드 현재 상태. 공개(배합 화면 무로그인).

        응답: {"enabled": false} (행 없음/'0' 모두 false).
        """
        with get_connection() as connection:
            enabled = settings_service.get_scale_only_input(connection)
        return {"enabled": enabled}

    @router.put("/settings/scale-only-input")
    def set_scale_only_input(
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """저울 전용 입력 모드 토글 — 책임자 전용.

        body: {"enabled": true|false}. 저장 후 감사 로그(action=setting_scale_only_set).
        응답: {"status":"ok","enabled":...}.
        """
        raw = body.get("enabled")
        if not isinstance(raw, bool):
            raise HTTPException(
                status_code=400,
                detail="enabled 는 true/false 여야 합니다.",
            )

        actor_label = (
            current_user.get("display_name")
            or current_user.get("username")
            or None
        )
        with get_connection() as connection:
            settings_service.set_scale_only_input(
                connection,
                raw,
                updated_by=actor_label,
            )
            write_audit_log(
                connection,
                action="setting_scale_only_set",
                actor=current_user,
                target_type="app_setting",
                target_label=settings_service.SCALE_ONLY_INPUT_KEY,
                details={"enabled": raw},
            )
            connection.commit()

        return {"status": "ok", "enabled": raw}

    return router
