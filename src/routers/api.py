from fastapi import APIRouter

from ..database import utc_now_text
from . import (
    admin_routes,
    auth_routes,
    chat_routes,
    dashboard_routes,
    public_notice_routes,
    recipe_routes,
    spreadsheet_routes,
    weighing_routes,
)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    public_router, auth_me_router = auth_routes.build_router()
    admin_router = admin_routes.build_router()
    chat_router = chat_routes.build_router()
    public_notice_router = public_notice_routes.build_router()
    recipe_op_router, recipe_mgr_router = recipe_routes.build_router()
    weighing_router = weighing_routes.build_router()
    ss_router = spreadsheet_routes.build_router()
    dashboard_router = dashboard_routes.build_router()

    @public_router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "time": utc_now_text()}

    router.include_router(public_router)
    router.include_router(public_notice_router)
    router.include_router(auth_me_router)
    router.include_router(recipe_op_router)
    router.include_router(chat_router)
    router.include_router(weighing_router)
    router.include_router(recipe_mgr_router)
    router.include_router(admin_router)
    router.include_router(ss_router, prefix="/spreadsheet")
    router.include_router(dashboard_router)
    return router
