from fastapi import APIRouter
from fastapi.responses import JSONResponse


router = APIRouter()


@router.get("/health")
def health() -> JSONResponse:
    return JSONResponse(status_code=200, content={"status": "ok"})

