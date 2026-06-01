from fastapi import APIRouter

from ..db import utc_now_text
from . import (
    admin_routes,
    attendance_routes,
    auth_routes,
    chat_routes,
    dashboard_routes,
    forecast_routes,
    public_attendance_alert_routes,
    recipe_import_routes,
    recipe_manager_routes,
    recipe_operator_routes,
    recipe_stats_routes,
    spreadsheet_routes,
    stock_routes,
    weighing_routes,
)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    public_router, auth_me_router = auth_routes.build_router()
    admin_router = admin_routes.build_router()
    chat_router = chat_routes.build_router()
    public_attendance_alert_router = public_attendance_alert_routes.build_router()
    attendance_router = attendance_routes.build_router()
    recipe_op_router = recipe_operator_routes.build_router()
    recipe_mgr_router = recipe_manager_routes.build_router()
    stock_op_router, stock_mgr_router = stock_routes.build_router()
    import_router = recipe_import_routes.build_router()
    stats_router = recipe_stats_routes.build_router()
    forecast_router = forecast_routes.build_router()
    weighing_router = weighing_routes.build_router()
    ss_router = spreadsheet_routes.build_router()
    dashboard_router = dashboard_routes.build_router()

    @public_router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "time": utc_now_text()}

    router.include_router(public_router)
    router.include_router(public_attendance_alert_router)
    router.include_router(attendance_router)
    router.include_router(auth_me_router)
    router.include_router(recipe_op_router)
    router.include_router(stock_op_router)         # operator stock reads
    router.include_router(chat_router)
    router.include_router(weighing_router)
    router.include_router(recipe_mgr_router)       # manager recipe writes
    router.include_router(stock_mgr_router)        # manager stock writes
    router.include_router(import_router)
    router.include_router(stats_router)
    router.include_router(forecast_router)         # manager forecast + reorder
    router.include_router(admin_router)
    router.include_router(ss_router, prefix="/spreadsheet")
    router.include_router(dashboard_router)
    return router
