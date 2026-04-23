from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import get_current_user, has_access_level, list_users_by_access_levels
from ..config import SEED_DEMO_DATA


def _safe_next(next_url: str | None, default: str) -> str:
    if not next_url or not next_url.startswith("/"):
        return default
    return next_url


def _build_context(request: Request, **extra) -> dict:
    context = {
        "request": request,
        "current_user": get_current_user(request, required=False),
    }
    context.update(extra)
    return context


def _render(templates: Jinja2Templates, request: Request, name: str, context: dict) -> Response:
    return templates.TemplateResponse(request, name, context)


def _entry_redirect(target_path: str, request: Request) -> RedirectResponse:
    next_url = quote(request.url.path or target_path, safe="/")
    joiner = "&" if "?" in target_path else "?"
    return RedirectResponse(url=f"{target_path}{joiner}next={next_url}", status_code=303)


def _protected_page_response(
    request: Request,
    templates: Jinja2Templates,
    template_name: str,
    required_level: str,
) -> Response:
    current_user = get_current_user(request, required=False)
    if not current_user:
        target = "/weighing/select" if required_level == "operator" else "/management/login"
        return _entry_redirect(target, request)

    if not has_access_level(current_user, required_level):
        return _entry_redirect("/management/login", request)

    return _render(templates, request, template_name, {
        "current_user": current_user,
    })


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def entry_page(request: Request) -> Response:
        return _render(templates, request, "entry.html", {
            "current_user": get_current_user(request, required=False),
        })

    @router.get("/login", response_class=HTMLResponse)
    async def legacy_login_page(request: Request, next: str | None = None) -> Response:
        next_url = _safe_next(next, "/management")
        return RedirectResponse(url=f"/management/login?next={quote(next_url, safe='/')}", status_code=303)

    @router.get("/weighing/select", response_class=HTMLResponse)
    async def weighing_select_page(request: Request, next: str | None = None) -> Response:
        return _render(templates, request, "weighing_select.html", {
            "current_user": get_current_user(request, required=False),
            "next_url": _safe_next(next, "/weighing"),
            "operators": list_users_by_access_levels("operator", "manager"),
        })

    @router.get("/management/login", response_class=HTMLResponse)
    async def management_login_page(request: Request, next: str | None = None) -> Response:
        current_user = get_current_user(request, required=False)
        next_url = _safe_next(next, "/management")
        if current_user and has_access_level(current_user, "manager"):
            return RedirectResponse(url=next_url, status_code=303)

        return _render(templates, request, "management_login.html", {
            "current_user": current_user,
            "next_url": next_url,
            "show_demo_credentials": SEED_DEMO_DATA,
            "managers": list_users_by_access_levels("manager"),
        })

    @router.get("/weighing", response_class=HTMLResponse)
    async def work_page(request: Request) -> Response:
        return _protected_page_response(request, templates, "work.html", "operator")

    @router.get("/management", response_class=HTMLResponse)
    async def management_page(request: Request) -> Response:
        return _protected_page_response(request, templates, "management.html", "manager")

    @router.get("/insight", response_class=HTMLResponse)
    async def insight_page(request: Request) -> Response:
        return _protected_page_response(request, templates, "insight.html", "manager")

    @router.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_page(request: Request) -> Response:
        return _protected_page_response(request, templates, "dashboard.html", "manager")

    @router.get("/status", response_class=HTMLResponse)
    async def status_page(request: Request) -> Response:
        return _protected_page_response(request, templates, "status.html", "manager")

    @router.get("/base", response_class=HTMLResponse)
    async def base_page(request: Request) -> Response:
        return _protected_page_response(request, templates, "base.html", "manager")

    @router.get("/admin/users", response_class=HTMLResponse)
    async def admin_users_page(request: Request) -> Response:
        return _protected_page_response(request, templates, "admin_users.html", "admin")

    @router.get("/work.html", response_class=HTMLResponse)
    async def work_page_alias(request: Request) -> Response:
        return RedirectResponse(url="/weighing", status_code=303)

    @router.get("/management.html", response_class=HTMLResponse)
    async def management_page_alias(request: Request) -> Response:
        return RedirectResponse(url="/management", status_code=303)

    @router.get("/insight.html", response_class=HTMLResponse)
    async def insight_page_alias(request: Request) -> Response:
        return RedirectResponse(url="/insight", status_code=303)

    @router.get("/status.html", response_class=HTMLResponse)
    async def status_page_alias(request: Request) -> Response:
        return RedirectResponse(url="/status", status_code=303)

    @router.get("/base.html", response_class=HTMLResponse)
    async def base_page_alias(request: Request) -> Response:
        return RedirectResponse(url="/base", status_code=303)

    @router.get("/admin/users.html", response_class=HTMLResponse)
    async def admin_users_page_alias(request: Request) -> Response:
        return RedirectResponse(url="/admin/users", status_code=303)

    return router
