"""Application factory."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from hearth import db
from hearth.clock import Clock, system_clock
from hearth.config import Settings
from hearth.disclosure import Concealed
from hearth.ledger import Ledger
from hearth.web.deps import Hearth, session_outside_routing
from hearth.web.errors import FormExpired, PasswordChangeRequired, SignInRequired
from hearth.web.routes import ROUTERS
from hearth.web.templating import render

_STATIC = Path(__file__).parent / "static"

_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self';"
        " connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    # Not "no-referrer": that makes browsers send "Origin: null" on this site's own
    # forms, which the cross-site check below would then have to accept blindly.
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

type CallNext = Callable[[Request], Awaitable[Response]]


def create_app(settings: Settings, ledger: Ledger, clock: Clock = system_clock) -> FastAPI:
    db.initialise(settings.database)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.state.hearth = Hearth(settings=settings, ledger=ledger, clock=clock)

    app.middleware("http")(_refuse_cross_site_changes)
    app.middleware("http")(_add_security_headers)
    _add_error_pages(app)

    app.mount("/static", StaticFiles(directory=_STATIC), name="static")
    for router in ROUTERS:
        app.include_router(router)
    return app


async def _add_security_headers(request: Request, call_next: CallNext) -> Response:
    response = await call_next(request)
    response.headers.update(_SECURITY_HEADERS)
    if not request.url.path.startswith("/static/"):
        # Pages hold private figures; never leave a copy in a cache for the next person.
        response.headers["Cache-Control"] = "no-store"
    return response


async def _refuse_cross_site_changes(request: Request, call_next: CallNext) -> Response:
    """Refuse state changes started from another site, the sign-in form included."""
    if request.method in _UNSAFE_METHODS and _is_cross_site(request):
        return render(request, "error.html", status_code=403)
    return await call_next(request)


def _is_cross_site(request: Request) -> bool:
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site is not None:
        return fetch_site not in {"same-origin", "none"}
    origin = request.headers.get("Origin")
    return origin is not None and origin != f"{request.url.scheme}://{request.url.netloc}"


def _add_error_pages(app: FastAPI) -> None:
    def page(request: Request, template: str, status_code: int) -> Response:
        session = session_outside_routing(request)
        return render(request, template, status_code=status_code, session=session)

    def not_found(request: Request) -> Response:
        # Identical for everything a viewer asks for, so "does not exist" and
        # "not yours" read the same. It depends on who is asking, never on what.
        return page(request, "not_found.html", 404)

    async def on_concealed(request: Request, _: Exception) -> Response:
        return not_found(request)

    async def on_http_error(request: Request, error: Exception) -> Response:
        assert isinstance(error, HTTPException)  # noqa: S101 - registered for HTTPException only
        if error.status_code in {404, 405}:
            return not_found(request)
        return page(request, "error.html", error.status_code)

    async def on_invalid_request(request: Request, _: Exception) -> Response:
        return page(request, "error.html", 400)

    async def on_sign_in_required(request: Request, _: Exception) -> Response:
        destination = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/sign-in?next={quote(destination, safe='')}", status_code=303)

    async def on_password_change_required(request: Request, _: Exception) -> Response:
        return RedirectResponse("/settings/password", status_code=303)

    async def on_form_expired(request: Request, _: Exception) -> Response:
        return page(request, "form_expired.html", 403)

    app.add_exception_handler(Concealed, on_concealed)
    app.add_exception_handler(HTTPException, on_http_error)
    app.add_exception_handler(RequestValidationError, on_invalid_request)
    app.add_exception_handler(SignInRequired, on_sign_in_required)
    app.add_exception_handler(PasswordChangeRequired, on_password_change_required)
    app.add_exception_handler(FormExpired, on_form_expired)
