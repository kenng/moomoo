from __future__ import annotations

import secrets
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from momo.config import get_settings

_PUBLIC_PATHS = frozenset({"/login", "/logout"})


def auth_enabled() -> bool:
    s = get_settings()
    return bool(s.auth_username and s.auth_password)


def verify_credentials(username: str, password: str) -> bool:
    s = get_settings()
    if not s.auth_username or not s.auth_password:
        return False
    user_ok = secrets.compare_digest(username, s.auth_username)
    pass_ok = secrets.compare_digest(password, s.auth_password)
    return user_ok and pass_ok


def is_logged_in(request: Request) -> bool:
    if not auth_enabled():
        return True
    return request.session.get("user") == get_settings().auth_username


class RequireLoginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not auth_enabled():
            return await call_next(request)

        path = request.url.path
        if path.startswith("/static") or path in _PUBLIC_PATHS:
            return await call_next(request)

        if is_logged_in(request):
            return await call_next(request)

        if path.startswith("/api/"):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        nxt = path
        if request.url.query:
            nxt = f"{path}?{request.url.query}"
        return RedirectResponse(
            url=f"/login?next={quote(nxt, safe='')}",
            status_code=303,
        )
