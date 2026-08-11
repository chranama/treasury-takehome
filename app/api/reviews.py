from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from pydantic import ConfigDict, ValidationError

from app.api.client_identity import source_identity
from app.api.correlation import correlation_id_from_scope
from app.api.errors import ApiErrorResponse, ReviewApiError
from app.comparison import (
    ApplicationErrorCategory,
    ExpectedNetContents,
    ExpectedReview,
    NetContentsUnit,
    ReviewResult,
)
from app.reviews import ReviewService

router = APIRouter(prefix="/api", tags=["reviews"])


class ReviewResponse(ReviewResult):
    model_config = ConfigDict(extra="forbid")

    correlation_id: str
    processing_mode: Literal["synthetic", "live"]


@router.post(
    "/reviews",
    response_model=ReviewResponse,
    responses={
        413: {"model": ApiErrorResponse},
        409: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
        429: {"model": ApiErrorResponse},
        500: {"model": ApiErrorResponse},
        502: {"model": ApiErrorResponse},
        503: {"model": ApiErrorResponse},
        504: {"model": ApiErrorResponse},
    },
)
async def review_label(
    request: Request,
    brand_name: Annotated[str, Form(min_length=1, max_length=200)],
    class_type: Annotated[str, Form(min_length=1, max_length=200)],
    expected_abv: Annotated[Decimal, Form(ge=0, le=100)],
    expected_net_contents: Annotated[Decimal, Form(gt=0)],
    expected_net_contents_unit: Annotated[NetContentsUnit, Form()],
    image: Annotated[list[UploadFile], File()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> ReviewResponse:
    if len(image) != 1:
        for upload in image:
            await upload.close()
        raise ReviewApiError(
            ApplicationErrorCategory.INVALID_INPUT,
            "Submit exactly one label image or composite.",
        )

    try:
        expected = ExpectedReview(
            brand_name=brand_name,
            class_type=class_type,
            abv=expected_abv,
            net_contents=ExpectedNetContents(
                value=expected_net_contents,
                unit=expected_net_contents_unit,
            ),
        )
    except ValidationError as error:
        await image[0].close()
        raise ReviewApiError(
            ApplicationErrorCategory.INVALID_INPUT,
            "Check the expected application values and try again.",
        ) from error

    service: ReviewService = request.app.state.review_service
    correlation_id = correlation_id_from_scope(request.scope)
    processed = await service.process(
        expected=expected,
        upload=image[0],
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        source_identity=source_identity(request, request.app.state.settings),
    )
    return ReviewResponse(
        **processed.review.model_dump(),
        correlation_id=correlation_id,
        processing_mode=processed.processing_mode,
    )
