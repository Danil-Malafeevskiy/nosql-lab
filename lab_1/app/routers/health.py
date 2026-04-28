from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..http import cookie_refresh, extract_sid_cookie
from ..settings import get_settings

router = APIRouter()


@router.get("/health")
def health(request: Request) -> JSONResponse:
    s = get_settings()
    sid = extract_sid_cookie(request)
    headers = {}
    if sid:
        headers["Set-Cookie"] = cookie_refresh(sid, s.session_ttl)
    return JSONResponse(status_code=200, content={"status": "ok"}, headers=headers)
