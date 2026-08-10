"""앱 설정 엔드포인트 — 저울 전용 입력 모드(scale-only-input) 토글.

GET 은 인증 불요(배합 화면이 무로그인이므로 공개). PUT 은 책임자 전용이며
감사 로그를 남긴다. 기본값은 OFF(행 부재 = false).
"""

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import require_access_level
from ..db import get_connection, write_audit_log
from ..limiter import limiter
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

    # ── 점도 알림 정리 기준일 ──────────────────────────────────────
    # GET 은 공개(점도 화면이 현재 기준일을 보여준다). POST 는 책임자 전용이며 오늘 날짜로만
    # 갱신한다 — 임의 날짜를 받지 않는 이유는 "지금까지 정리" 외의 용도가 없고, 과거로
    # 되돌려 통제를 넓히거나 미래로 밀어 알림을 통째로 끄는 길을 만들지 않기 위해서다.
    @router.get("/settings/viscosity-reminder-since")
    def get_viscosity_reminder_since() -> dict[str, Any]:
        """점도 알림 정리 기준일. 없으면 null(=전 대상 품목 알림, 종전 동작)."""
        with get_connection() as connection:
            since = settings_service.get_viscosity_reminder_since(connection)
        return {"since": since}

    @router.post("/settings/viscosity-reminder-since")
    def set_viscosity_reminder_since(
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """[지금까지 정리] — 기준일을 오늘로 갱신. 책임자 전용, 감사 로그.

        이후 점도 알림은 '기준일 이후에 배합한 반제품 중 오늘 점도가 없는 것'만 대상이다.
        지나간 미측정분은 기록에 그대로 남고 알림에서만 빠진다.
        """
        today = date.today().isoformat()
        actor_label = (
            current_user.get("display_name")
            or current_user.get("username")
            or None
        )
        with get_connection() as connection:
            previous = settings_service.get_viscosity_reminder_since(connection)
            settings_service.set_viscosity_reminder_since(
                connection, today, updated_by=actor_label
            )
            write_audit_log(
                connection,
                action="setting_viscosity_reminder_since_set",
                actor=current_user,
                target_type="app_setting",
                target_label=settings_service.VISCOSITY_REMINDER_SINCE_KEY,
                details={"since": today, "previous": previous},
            )
            connection.commit()
        return {"status": "ok", "since": today, "previous": previous}

    @router.get("/settings/scale-since")
    def get_scale_since() -> dict[str, Any]:
        """저울 도입일. 없으면 null(=전 구간에서 계량률 계산, 종전 동작)."""
        with get_connection() as connection:
            since = settings_service.get_scale_since(connection)
        return {"since": since}

    @router.put("/settings/scale-since")
    def set_scale_since(
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """저울 도입일 저장 — 배합 분석의 '저울 계량률'이 이 날짜부터만 계산된다.

        저울이 붙기 전 기록에는 '수동 입력' 표시가 없어 저울로 잰 것과 구분되지 않는다.
        그 구간까지 세면 계량률이 100% 로 부풀고, 저울을 들여놓은 달에 오히려 급락하는
        그래프가 되어 개선을 악화로 보여준다(2026-08-10 운영 데이터에서 확인).
        빈 값이면 기준 해제(종전처럼 전 구간 계산).
        """
        raw = str(body.get("since") or "").strip()
        if raw:
            try:
                date.fromisoformat(raw)
            except ValueError:
                raise HTTPException(status_code=400, detail="INVALID_DATE")
        actor_label = (
            current_user.get("display_name") or current_user.get("username") or None
        )
        with get_connection() as connection:
            previous = settings_service.get_scale_since(connection)
            settings_service.set_scale_since(connection, raw, updated_by=actor_label)
            write_audit_log(
                connection,
                action="setting_scale_since_set",
                actor=current_user,
                target_type="app_setting",
                target_label=settings_service.SCALE_SINCE_KEY,
                details={"since": raw or None, "previous": previous},
            )
            connection.commit()
        return {"status": "ok", "since": raw or None, "previous": previous}

    # ── 배합 창 단일화 가드 비상 예외 코드 ──────────────────────────
    # GET/PUT 은 책임자 전용(사용자 관리 화면에서 조회·변경). verify 는 무로그인(배합 화면)
    # 이며 코드를 반환하지 않고 일치 여부만 준다 — brute-force 방지로 rate limit.
    @router.get("/settings/blend-window-override")
    def get_blend_window_override(
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """현재 예외 코드 — 책임자 전용(관리 화면 표시용)."""
        with get_connection() as connection:
            code = settings_service.get_blend_window_override_code(connection)
        return {"code": code}

    @router.put("/settings/blend-window-override")
    def set_blend_window_override(
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """예외 코드 변경 — 책임자 전용. 감사 로그(코드 값은 남기지 않음)."""
        code = str(body.get("code") or "").strip()
        if len(code) < 4 or len(code) > 32:
            raise HTTPException(
                status_code=400,
                detail="코드는 4자 이상 32자 이하여야 합니다.",
            )
        actor_label = (
            current_user.get("display_name")
            or current_user.get("username")
            or None
        )
        with get_connection() as connection:
            settings_service.set_blend_window_override_code(
                connection, code, updated_by=actor_label
            )
            write_audit_log(
                connection,
                action="setting_blend_window_override_set",
                actor=current_user,
                target_type="app_setting",
                target_label=settings_service.BLEND_WINDOW_OVERRIDE_KEY,
                details={"length": len(code)},  # 코드 값 자체는 남기지 않는다.
            )
            connection.commit()
        return {"status": "ok"}

    @router.post("/settings/blend-window-override/verify")
    @limiter.limit("10/minute")
    def verify_blend_window_override(
        request: Request,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """입력 코드 대조 — 무로그인(배합 화면 비상구). 코드는 반환하지 않고 ok 만."""
        code = str(body.get("code") or "").strip()
        with get_connection() as connection:
            ok = settings_service.verify_blend_window_override_code(connection, code)
        return {"ok": ok}

    return router
