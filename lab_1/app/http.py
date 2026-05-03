from __future__ import annotations

from http.cookies import SimpleCookie
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .constants import SESSION_COOKIE_NAME


def extract_sid_cookie(request: Request) -> str | None:
    raw = request.headers.get("cookie")
    if not raw:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
    except Exception:
        return None
    morsel = cookie.get(SESSION_COOKIE_NAME)
    if not morsel:
        return None
    return morsel.value


def cookie_refresh(sid: str, ttl: int) -> str:
    return f"{SESSION_COOKIE_NAME}={sid}; HttpOnly; Path=/; Max-Age={ttl}"


def cookie_clear() -> str:
    return f"{SESSION_COOKIE_NAME}=; HttpOnly; Path=/; Max-Age=0"


def resp_empty(status: int, set_cookie: str | None = None) -> Response:
    headers = {}
    if set_cookie is not None:
        headers["Set-Cookie"] = set_cookie
    return Response(status_code=status, headers=headers)


def resp_json(status: int, payload: dict[str, Any], set_cookie: str | None = None) -> JSONResponse:
    headers = {}
    if set_cookie is not None:
        headers["Set-Cookie"] = set_cookie
    return JSONResponse(status_code=status, content=payload, headers=headers)

