from __future__ import annotations

import secrets
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

COOKIE_NAME = "momo_session"
_MAX_AGE = 60 * 60 * 24 * 14  # 14 days
_PUBLIC_PATHS = frozenset({"/login", "/logout"})


def _env(request: Request):
    return request.scope.get("env")


def auth_enabled(env) -> bool:
    if env is None:
        return False
    user = getattr(env, "AUTH_USERNAME", None) or ""
    password = getattr(env, "AUTH_PASSWORD", None) or ""
    return bool(str(user) and str(password))


def verify_credentials(env, username: str, password: str) -> bool:
    if not auth_enabled(env):
        return False
    expected_user = str(getattr(env, "AUTH_USERNAME", "") or "")
    expected_pass = str(getattr(env, "AUTH_PASSWORD", "") or "")
    return secrets.compare_digest(username, expected_user) and secrets.compare_digest(
        password, expected_pass
    )


def _serializer(env) -> URLSafeTimedSerializer:
    secret = str(getattr(env, "SESSION_SECRET", None) or "momo-dev-session-secret")
    return URLSafeTimedSerializer(secret, salt="momo-worker-auth")


def current_user(request: Request) -> str | None:
    env = _env(request)
    if env is None:
        return None
    if not auth_enabled(env):
        return None
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = _serializer(env).loads(token, max_age=_MAX_AGE)
    except (BadSignature, SignatureExpired, Exception):
        return None
    user = (data or {}).get("user")
    expected = str(getattr(env, "AUTH_USERNAME", "") or "")
    if user and user == expected:
        return user
    return None


def set_session_cookie(response: Response, env, username: str) -> None:
    token = _serializer(env).dumps({"user": username})
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=_MAX_AGE,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


class RequireLoginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        env = _env(request)
        if env is None or not auth_enabled(env):
            return await call_next(request)

        path = request.url.path
        if path.startswith("/static") or path in _PUBLIC_PATHS:
            return await call_next(request)

        if current_user(request):
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
