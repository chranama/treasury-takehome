"""HTTP boundary for batch templates, preflight drafts, and draft correction."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, File, Header, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app.api.client_identity import source_identity
from app.api.correlation import correlation_id_from_scope, elapsed_ms_from_scope
from app.batches import (
    BatchCaseDetail,
    BatchCasePatchRequest,
    BatchErrorCode,
    BatchErrorResponse,
    BatchField,
    BatchPreflightErrorResponse,
    BatchPreflightResponse,
    BatchResponse,
    BatchStartRequest,
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
from app.batches.limits import (
    MAX_POLL_RESPONSE_BYTES,
    START_IDEMPOTENCY_KEY_MAX_CHARACTERS,
    START_IDEMPOTENCY_KEY_MIN_CHARACTERS,
)
from app.batches.processing import BatchProcessingError, BatchProcessingService
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
    processing: BatchProcessingService = request.app.state.batch_processing_service
    await processing.admit_source(source_identity(request, request.app.state.settings))
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
    response_model=BatchResponse,
    responses={404: {"model": BatchErrorResponse}},
)
async def get_batch(request: Request, batch_id: str) -> BatchResponse | JSONResponse:
    parsed_batch_id = _identifier(batch_id)
    if parsed_batch_id is None:
        return _batch_error(request, BatchErrorCode.NOT_FOUND)
    service: BatchProcessingService = request.app.state.batch_processing_service
    batch = await service.get_batch(parsed_batch_id)
    if batch is None:
        return _batch_error(request, BatchErrorCode.NOT_FOUND)
    return ResponseWithModel(
        batch,
        status_code=200,
        headers={"Cache-Control": "no-store"},
    )


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
    service: BatchProcessingService = request.app.state.batch_processing_service
    detail = await service.get_case(*identifiers)
    if detail is None:
        return _batch_error(request, BatchErrorCode.NOT_FOUND)
    return ResponseWithModel(
        detail,
        status_code=200,
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/batches/{batch_id}/start",
    response_model=BatchResponse,
    status_code=202,
    responses={
        404: {"model": BatchErrorResponse},
        409: {"model": BatchErrorResponse},
        503: {"model": BatchErrorResponse},
    },
)
async def start_batch(
    request: Request,
    batch_id: str,
    start: Annotated[BatchStartRequest, Body()],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=START_IDEMPOTENCY_KEY_MIN_CHARACTERS,
            max_length=START_IDEMPOTENCY_KEY_MAX_CHARACTERS,
        ),
    ],
) -> BatchResponse | JSONResponse:
    parsed_batch_id = _identifier(batch_id)
    if parsed_batch_id is None:
        return _batch_error(request, BatchErrorCode.NOT_FOUND)
    service: BatchProcessingService = request.app.state.batch_processing_service
    try:
        batch = await service.start_batch(
            batch_id=parsed_batch_id,
            selection=start.selection,
            idempotency_key=idempotency_key,
            source_identity=source_identity(request, request.app.state.settings),
        )
    except BatchProcessingError as error:
        return _batch_error(request, error.code)
    return ResponseWithModel(
        batch,
        status_code=202,
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/batches/{batch_id}/results.csv",
    response_class=Response,
    responses={
        404: {"model": BatchErrorResponse},
        409: {"model": BatchErrorResponse},
    },
)
async def batch_results_csv(request: Request, batch_id: str) -> Response:
    parsed_batch_id = _identifier(batch_id)
    if parsed_batch_id is None:
        return _batch_error(request, BatchErrorCode.NOT_FOUND)
    service: BatchProcessingService = request.app.state.batch_processing_service
    try:
        content = await service.get_results_csv(parsed_batch_id)
    except BatchProcessingError as error:
        return _batch_error(request, error.code)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="label-review-results.csv"',
            "X-Content-Type-Options": "nosniff",
        },
    )


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
        model: BatchResponse | BatchCaseDetail,
        *,
        status_code: int,
        headers: dict[str, str],
    ) -> None:
        super().__init__(
            status_code=status_code,
            content=model.model_dump(mode="json"),
            headers=headers,
        )
        if len(self.body) > MAX_POLL_RESPONSE_BYTES:
            raise RuntimeError("batch response exceeds the polling representation limit")


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
        status_code=(
            404
            if code == BatchErrorCode.NOT_FOUND
            else 503
            if code == BatchErrorCode.PROCESSING_UNAVAILABLE
            else 409
        ),
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
