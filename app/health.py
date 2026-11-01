import logging
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import __version__

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


class HealthStatus(BaseModel):
    status: Literal["ok", "degraded"]
    db: bool
    version: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


@router.get("/health", response_model=HealthStatus, responses={500: {"model": ErrorBody}})
def health(request: Request) -> HealthStatus | JSONResponse:
    try:
        database_ok = request.app.state.check_database()
    except Exception:
        logger.exception("Unexpected health check error")
        error = ErrorBody(code="internal_error", message="Internal server error")
        return JSONResponse(status_code=500, content=error.model_dump(exclude_none=True))
    return HealthStatus(
        status="ok" if database_ok else "degraded", db=database_ok, version=__version__
    )
