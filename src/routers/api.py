from fastapi import APIRouter

from ..db import utc_now_text
from . import (
    admin_routes,
    attendance_routes,
    auth_routes,
    blend_routes,
    chat_routes,
    dashboard_routes,
    forecast_routes,
    lot_routes,
    order_routes,
    public_attendance_alert_routes,
    receiving_routes,
    recipe_import_routes,
    recipe_manager_routes,
    recipe_operator_routes,
    recipe_stats_routes,
    spreadsheet_routes,
    stock_routes,
    viscosity_routes,
    weighing_routes,
    worker_routes,
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
    lot_op_router, lot_mgr_router = lot_routes.build_router()
    order_router = order_routes.build_router()
    receiving_router = receiving_routes.build_router()
    weighing_router = weighing_routes.build_router()
    viscosity_op_router, viscosity_mgr_router = viscosity_routes.build_router()
    blend_router = blend_routes.build_router()
    worker_open_router, worker_admin_router = worker_routes.build_router()
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
    router.include_router(lot_op_router)            # operator LOT reads
    router.include_router(lot_mgr_router)           # manager LOT writes + export
    router.include_router(order_router)             # manager purchase orders + ERP
    router.include_router(receiving_router)         # manager PO receiving (LOT + stock)
    router.include_router(viscosity_op_router)       # operator viscosity reads + register
    router.include_router(viscosity_mgr_router)      # manager viscosity product settings
    router.include_router(blend_router)              # blend records (ink weighing overhaul, open)
    router.include_router(worker_open_router)        # worker name registry (open)
    router.include_router(worker_admin_router)       # worker registry admin (cleanup)
    router.include_router(admin_router)
    router.include_router(ss_router, prefix="/spreadsheet")
    router.include_router(dashboard_router)
    return router
