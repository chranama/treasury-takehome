"""HTTP boundary for batch templates, preflight drafts, and draft correction."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app.api.correlation import correlation_id_from_scope, elapsed_ms_from_scope
from app.batches import (
    BatchCaseDetail,
    BatchCasePatchRequest,
    BatchErrorCode,
    BatchErrorResponse,
    BatchField,
    BatchPreflightErrorResponse,
    BatchPreflightResponse,
    PreflightIssue,
    PreflightIssueCode,
    PreflightIssueScope,
    generate_csv_template,
    generate_xlsx_template,
    prepare_batch_preflight,
)
from app.batches.drafts import (
    BatchDraftService,
    DraftNotFoundError,
    DraftValidationError,
)
from app.storage import ImageIntakeError, ImageIntakeErrorKind

router = APIRouter(prefix="/api", tags=["batches"])

_UPLOAD_TOO_LARGE_CODES = frozenset(
    {
        PreflightIssueCode.WORKBOOK_TOO_LARGE,
        PreflightIssueCode.AGGREGATE_UPLOAD_TOO_LARGE,
        PreflightIssueCode.IMAGE_TOO_LARGE,
    }
)


@router.get("/batch-template.xlsx", response_class=Response)
def batch_template_xlsx() -> Response:
    return Response(
        content=generate_xlsx_template(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="label-review-batch.xlsx"'},
    )


@router.get("/batch-template.csv", response_class=Response)
def batch_template_csv() -> Response:
    return Response(
        content=generate_csv_template(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="label-review-batch.csv"'},
    )


@router.post(
    "/batches/preflight",
    response_model=BatchPreflightResponse,
    status_code=201,
    responses={
        413: {"model": BatchPreflightErrorResponse},
        422: {"model": BatchPreflightErrorResponse},
    },
)
async def preflight_batch(
    request: Request,
    spreadsheet: Annotated[UploadFile, File()],
    images: Annotated[list[UploadFile] | None, File()] = None,
) -> BatchPreflightResponse | JSONResponse:
    selected_images = images or []
    service: BatchDraftService = request.app.state.batch_draft_service
    async with prepare_batch_preflight(
        spreadsheet,
        selected_images,
        temp_dir=service.temp_dir,
    ) as parsed:
        if parsed.has_errors:
            return _preflight_error(request, parsed.issues)
        try:
            draft = await service.create_draft(parsed)
        except DraftValidationError as error:
            return _preflight_error(request, error.issues)

    return ResponseWithModel(
        draft,
        status_code=201,
        headers={"Location": f"/api/batches/{draft.batch_id}"},
    )


@router.get(
    "/batches/{batch_id}",
    response_model=BatchPreflightResponse,
    responses={404: {"model": BatchErrorResponse}},
)
async def get_batch(request: Request, batch_id: str) -> BatchPreflightResponse | JSONResponse:
    parsed_batch_id = _identifier(batch_id)
    if parsed_batch_id is None:
        return _batch_error(request, BatchErrorCode.NOT_FOUND)
    service: BatchDraftService = request.app.state.batch_draft_service
    draft = await service.get_draft(parsed_batch_id)
    if draft is None:
        return _batch_error(request, BatchErrorCode.NOT_FOUND)
    return draft


@router.get(
    "/batches/{batch_id}/cases/{case_id}",
    response_model=BatchCaseDetail,
    responses={404: {"model": BatchErrorResponse}},
)
async def get_batch_case(
    request: Request,
    batch_id: str,
    case_id: str,
) -> BatchCaseDetail | JSONResponse:
    identifiers = _identifiers(batch_id, case_id)
    if identifiers is None:
        return _batch_error(request, BatchErrorCode.NOT_FOUND)
    service: BatchDraftService = request.app.state.batch_draft_service
    detail = await service.get_case(*identifiers)
    if detail is None:
        return _batch_error(request, BatchErrorCode.NOT_FOUND)
    return detail


@router.patch(
    "/batches/{batch_id}/cases/{case_id}",
    response_model=BatchCaseDetail,
    responses={404: {"model": BatchErrorResponse}},
)
async def correct_batch_case(
    request: Request,
    batch_id: str,
    case_id: str,
    patch: Annotated[BatchCasePatchRequest, Body()],
) -> BatchCaseDetail | JSONResponse:
    identifiers = _identifiers(batch_id, case_id)
    if identifiers is None:
        return _batch_error(request, BatchErrorCode.NOT_FOUND)
    service: BatchDraftService = request.app.state.batch_draft_service
    try:
        return await service.correct_case(*identifiers, patch)
    except DraftNotFoundError:
        return _batch_error(request, BatchErrorCode.NOT_FOUND)


@router.put(
    "/batches/{batch_id}/cases/{case_id}/image",
    response_model=BatchCaseDetail,
    responses={
        404: {"model": BatchErrorResponse},
        413: {"model": BatchPreflightErrorResponse},
        422: {"model": BatchPreflightErrorResponse},
    },
)
async def replace_batch_case_image(
    request: Request,
    batch_id: str,
    case_id: str,
    image: Annotated[list[UploadFile], File()],
) -> BatchCaseDetail | JSONResponse:
    identifiers = _identifiers(batch_id, case_id)
    if identifiers is None:
        await _close_uploads(image)
        return _batch_error(request, BatchErrorCode.NOT_FOUND)
    if len(image) != 1:
        await _close_uploads(image)
        return _preflight_error(
            request,
            (
                PreflightIssue(
                    code=PreflightIssueCode.INVALID_IMAGE,
                    scope=PreflightIssueScope.IMAGE,
                ),
            ),
        )

    service: BatchDraftService = request.app.state.batch_draft_service
    try:
        return await service.replace_case_image(*identifiers, image[0])
    except DraftNotFoundError:
        return _batch_error(request, BatchErrorCode.NOT_FOUND)
    except DraftValidationError as error:
        return _preflight_error(request, error.issues)
    except ImageIntakeError as error:
        detail = await service.get_case(*identifiers)
        if detail is None:
            return _batch_error(request, BatchErrorCode.NOT_FOUND)
        issue = PreflightIssue(
            code=_image_issue_code(error.kind),
            scope=PreflightIssueScope.ROW,
            row_number=detail.summary.row_number,
            field=BatchField.LABEL_IMAGE_FILENAME,
        )
        return _preflight_error(request, (issue,))


class ResponseWithModel(JSONResponse):
    def __init__(
        self,
        model: BatchPreflightResponse,
        *,
        status_code: int,
        headers: dict[str, str],
    ) -> None:
        super().__init__(
            status_code=status_code,
            content=model.model_dump(mode="json"),
            headers=headers,
        )


def _identifier(value: str) -> UUID | None:
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError):
        return None
    if parsed.version != 4 or str(parsed) != value.casefold():
        return None
    return parsed


def _identifiers(batch_id: str, case_id: str) -> tuple[UUID, UUID] | None:
    parsed_batch_id = _identifier(batch_id)
    parsed_case_id = _identifier(case_id)
    if parsed_batch_id is None or parsed_case_id is None:
        return None
    return parsed_batch_id, parsed_case_id


def _preflight_error(
    request: Request,
    issues: tuple[PreflightIssue, ...],
) -> JSONResponse:
    correlation_id = UUID(correlation_id_from_scope(request.scope))
    payload = BatchPreflightErrorResponse(
        issues=list(issues),
        correlation_id=correlation_id,
        processing_duration_ms=elapsed_ms_from_scope(request.scope),
    )
    status_code = 413 if any(issue.code in _UPLOAD_TOO_LARGE_CODES for issue in issues) else 422
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers={"X-Correlation-ID": str(correlation_id)},
    )


def _batch_error(request: Request, code: BatchErrorCode) -> JSONResponse:
    correlation_id = UUID(correlation_id_from_scope(request.scope))
    payload = BatchErrorResponse(
        code=code,
        correlation_id=correlation_id,
        processing_duration_ms=elapsed_ms_from_scope(request.scope),
    )
    return JSONResponse(
        status_code=404 if code == BatchErrorCode.NOT_FOUND else 409,
        content=payload.model_dump(mode="json"),
        headers={"X-Correlation-ID": str(correlation_id)},
    )


def _image_issue_code(kind: ImageIntakeErrorKind) -> PreflightIssueCode:
    return {
        ImageIntakeErrorKind.EMPTY_FILE: PreflightIssueCode.EMPTY_IMAGE,
        ImageIntakeErrorKind.UPLOAD_TOO_LARGE: PreflightIssueCode.IMAGE_TOO_LARGE,
        ImageIntakeErrorKind.UNSUPPORTED_FORMAT: PreflightIssueCode.UNSUPPORTED_IMAGE,
        ImageIntakeErrorKind.CORRUPT_IMAGE: PreflightIssueCode.INVALID_IMAGE,
        ImageIntakeErrorKind.ANIMATED_IMAGE: PreflightIssueCode.ANIMATED_IMAGE,
        ImageIntakeErrorKind.DIMENSIONS_EXCEEDED: PreflightIssueCode.IMAGE_DIMENSIONS_EXCEEDED,
        ImageIntakeErrorKind.DECOMPRESSION_BOMB: PreflightIssueCode.IMAGE_DIMENSIONS_EXCEEDED,
    }[kind]


async def _close_uploads(uploads: list[UploadFile]) -> None:
    for upload in uploads:
        if not upload.file.closed:
            await upload.close()
