import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError
from app.core.validation import error_details

logger = logging.getLogger(__name__)

# Stable machine-readable codes for the standard error format.
STATUS_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "NOT_AUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    500: "INTERNAL_ERROR",
}

# User-facing messages (Spanish) for errors without a specific message.
DEFAULT_MESSAGES: dict[int, str] = {
    400: "La solicitud no es válida.",
    401: "Debes iniciar sesión para continuar.",
    403: "No tienes permiso para realizar esta acción.",
    404: "El recurso solicitado no existe.",
    405: "Método no permitido para este recurso.",
    409: "La operación entra en conflicto con el estado actual.",
    422: "Los datos enviados no son válidos.",
    500: "Ocurrió un error interno. Intenta de nuevo más tarde.",
}


def error_response(
    status_code: int,
    message: str | None = None,
    details: list[dict[str, str]] | None = None,
    code: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code or STATUS_CODES.get(status_code, "ERROR"),
                "message": message or DEFAULT_MESSAGES.get(status_code, "Ocurrió un error al procesar la solicitud."),
                "details": details or [],
            }
        },
    )


def _status_phrase(status_code: int) -> str | None:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return None


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # Starlette fills detail with the English status phrase by default; only
    # custom (user-facing) details are passed through.
    message = exc.detail if isinstance(exc.detail, str) and exc.detail != _status_phrase(exc.status_code) else None
    response = error_response(exc.status_code, message)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(422, details=error_details(exc.errors()))


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return error_response(exc.status_code, exc.message, code=exc.code)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
