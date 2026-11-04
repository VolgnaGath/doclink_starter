"""Consistent JSON errors for REST; retain existing HTML redirects/responses."""

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from app.services.model_calls import ProcessingError
from app.services.review import ReviewError


def install_errors(app: FastAPI) -> None:
    def error(
        status: int, message: str, details: list[dict[str, object]] | None = None
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={
                "code": {
                    401: "unauthorized",
                    404: "not_found",
                    409: "conflict",
                    422: "validation_error",
                }.get(status, "internal_error"),
                "message": message,
                "details": details or [],
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        if request.url.path.startswith("/api/v1/"):
            return error(exc.status_code, str(exc.detail))
        return await http_exception_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def request_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        if not request.url.path.startswith("/api/v1/"):
            return await request_validation_exception_handler(request, exc)
        return error(
            422,
            "Некорректный запрос",
            [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()],
        )

    @app.exception_handler(ReviewError)
    async def review_error(request: Request, exc: ReviewError) -> JSONResponse:
        return error(exc.status, str(exc))

    @app.exception_handler(ProcessingError)
    async def processing_error(request: Request, exc: ProcessingError) -> JSONResponse:
        return error(409, str(exc))

    @app.exception_handler(ValidationError)
    async def value_error(request: Request, exc: ValidationError) -> JSONResponse:
        return error(
            422,
            "Некорректные значения полей",
            [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()],
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        return error(500, "Внутренняя ошибка сервера")
