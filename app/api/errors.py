from typing import Annotated

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.correlation import correlation_id_from_scope, elapsed_ms_from_scope
from app.comparison.models import ApplicationErrorCategory


class ApiErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: ApplicationErrorCategory
    message: str
    correlation_id: str
    processing_duration_ms: Annotated[int, Field(ge=0)]


_STATUS_BY_CATEGORY: dict[ApplicationErrorCategory, int] = {
    ApplicationErrorCategory.INVALID_INPUT: 422,
    ApplicationErrorCategory.LIVE_EXTRACTION_DISABLED: 503,
    ApplicationErrorCategory.CAPACITY_REACHED: 503,
    ApplicationErrorCategory.TRAFFIC_THROTTLED: 429,
    ApplicationErrorCategory.PROVIDER_TIMEOUT: 504,
    ApplicationErrorCategory.PROVIDER_UNAVAILABLE: 502,
    ApplicationErrorCategory.MALFORMED_PROVIDER_OUTPUT: 502,
    ApplicationErrorCategory.INTERNAL_ERROR: 500,
}


class ReviewApiError(RuntimeError):
    """Bounded application failure safe to return from the review API."""

    def __init__(
        self,
        category: ApplicationErrorCategory,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        safe_message = message.strip()
        if not safe_message or len(safe_message) > 300:
            raise ValueError("API errors require a safe message of at most 300 characters")
        self.category = category
        self.safe_message = safe_message
        self.status_code = status_code or _STATUS_BY_CATEGORY[category]
        super().__init__(safe_message)


def error_response(
    request: Request,
    *,
    category: ApplicationErrorCategory,
    message: str,
    status_code: int | None = None,
) -> JSONResponse:
    correlation_id = correlation_id_from_scope(request.scope)
    payload = ApiErrorResponse(
        category=category,
        message=message,
        correlation_id=correlation_id,
        processing_duration_ms=elapsed_ms_from_scope(request.scope),
    )
    return JSONResponse(
        status_code=status_code or _STATUS_BY_CATEGORY[category],
        content=payload.model_dump(mode="json"),
        headers={"X-Correlation-ID": correlation_id},
    )


def install_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(ReviewApiError)
    async def handle_review_error(request: Request, error: ReviewApiError) -> JSONResponse:
        return error_response(
            request,
            category=error.category,
            message=error.safe_message,
            status_code=error.status_code,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request,
        _: RequestValidationError,
    ) -> JSONResponse:
        return error_response(
            request,
            category=ApplicationErrorCategory.INVALID_INPUT,
            message="Check the required fields and submit exactly one valid image.",
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, _: Exception) -> JSONResponse:
        return error_response(
            request,
            category=ApplicationErrorCategory.INTERNAL_ERROR,
            message="The review could not be completed. Try again.",
        )
