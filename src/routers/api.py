from fastapi import APIRouter

from ..db import utc_now_text
from . import (
    admin_routes,
    attendance_routes,
    auth_routes,
    blend_session_routes,
    blend_routes,
    dashboard_routes,
    public_attendance_alert_routes,
    public_material_usage_routes,
    public_viscosity_reminder_routes,
    recipe_import_routes,
    recipe_manager_routes,
    recipe_operator_routes,
    viscosity_routes,
    worker_routes,
)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    public_router, auth_me_router = auth_routes.build_router()
    admin_router = admin_routes.build_router()
    public_attendance_alert_router = public_attendance_alert_routes.build_router()
    public_material_usage_router = public_material_usage_routes.build_router()
    public_viscosity_reminder_router = public_viscosity_reminder_routes.build_router()
    attendance_router = attendance_routes.build_router()
    recipe_op_router = recipe_operator_routes.build_router()
    recipe_mgr_router = recipe_manager_routes.build_router()
    import_router = recipe_import_routes.build_router()
    viscosity_op_router, viscosity_mgr_router = viscosity_routes.build_router()
    blend_router = blend_routes.build_router()
    blend_session_router = blend_session_routes.build_router()
    worker_open_router, worker_admin_router = worker_routes.build_router()
    dashboard_router = dashboard_routes.build_router()

    @public_router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "time": utc_now_text()}

    router.include_router(public_router)
    router.include_router(public_attendance_alert_router)
    router.include_router(public_material_usage_router)     # 재고 대시보드 연동(자재 불출량)
    router.include_router(public_viscosity_reminder_router)
    router.include_router(attendance_router)
    router.include_router(auth_me_router)
    router.include_router(recipe_op_router)
    router.include_router(recipe_mgr_router)       # manager recipe writes
    router.include_router(import_router)
    router.include_router(viscosity_op_router)       # operator viscosity reads + register
    router.include_router(viscosity_mgr_router)      # manager viscosity product settings
    router.include_router(blend_router)              # blend records (ink weighing overhaul, open)
    router.include_router(blend_session_router)
    router.include_router(worker_open_router)        # worker name registry (open)
    router.include_router(worker_admin_router)       # worker registry admin (cleanup)
    router.include_router(admin_router)
    router.include_router(dashboard_router)
    return router
