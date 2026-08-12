"""Provider-neutral batch-review request, response, and state contracts."""

from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from app.batches.limits import (
    BATCH_RETENTION_HOURS,
    MAX_APPLICATION_ID_CHARACTERS,
    MAX_BATCH_CASES,
    MAX_CASE_SUMMARY_REASON_CHARACTERS,
    MAX_EXPECTED_TEXT_CHARACTERS,
    MAX_IMAGE_FILENAME_CHARACTERS,
    MAX_NET_CONTENTS_CELL_CHARACTERS,
    MAX_PREFLIGHT_ISSUES_PER_CASE,
)
from app.comparison import ExpectedReview, OverallOutcome, ReviewResult


class BatchContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BatchState(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"


class BatchCaseState(StrEnum):
    NEEDS_CORRECTION = "needs_correction"
    READY = "ready"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    NOT_SELECTED = "not_selected"


class BatchStartSelection(StrEnum):
    ALL_CASES = "all_cases"
    READY_CASES_ONLY = "ready_cases_only"


class PreflightIssueScope(StrEnum):
    BATCH = "batch"
    ROW = "row"
    IMAGE = "image"


class PreflightIssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class BatchField(StrEnum):
    APPLICATION_ID = "application_id"
    LABEL_IMAGE_FILENAME = "label_image_filename"
    EXPECTED_BRAND = "expected_brand"
    EXPECTED_CLASS_TYPE = "expected_class_type"
    EXPECTED_ABV = "expected_abv"
    EXPECTED_NET_CONTENTS = "expected_net_contents"


class PreflightIssueCode(StrEnum):
    EMPTY_BATCH = "empty_batch"
    TOO_MANY_CASES = "too_many_cases"
    TOO_MANY_IMAGES = "too_many_images"
    WORKBOOK_TOO_LARGE = "workbook_too_large"
    AGGREGATE_UPLOAD_TOO_LARGE = "aggregate_upload_too_large"
    UNSUPPORTED_SPREADSHEET = "unsupported_spreadsheet"
    MALFORMED_SPREADSHEET = "malformed_spreadsheet"
    ENCRYPTED_WORKBOOK = "encrypted_workbook"
    MACRO_ENABLED_WORKBOOK = "macro_enabled_workbook"
    MISSING_BATCH_WORKSHEET = "missing_batch_worksheet"
    SOURCE_ROW_LIMIT_EXCEEDED = "source_row_limit_exceeded"
    WORKBOOK_EXPANSION_LIMIT_EXCEEDED = "workbook_expansion_limit_exceeded"
    INVALID_CSV_ENCODING = "invalid_csv_encoding"
    NUL_BYTE_NOT_ALLOWED = "nul_byte_not_allowed"
    MISSING_REQUIRED_COLUMN = "missing_required_column"
    UNEXPECTED_COLUMN = "unexpected_column"
    DUPLICATE_COLUMN = "duplicate_column"
    INVALID_COLUMN_ORDER = "invalid_column_order"
    CELL_TOO_LONG = "cell_too_long"
    FORMULA_NOT_ALLOWED = "formula_not_allowed"
    EXTERNAL_LINK_NOT_ALLOWED = "external_link_not_allowed"
    MISSING_APPLICATION_ID = "missing_application_id"
    DUPLICATE_APPLICATION_ID = "duplicate_application_id"
    MISSING_IMAGE_FILENAME = "missing_image_filename"
    INVALID_IMAGE_FILENAME = "invalid_image_filename"
    MISSING_IMAGE = "missing_image"
    DUPLICATE_IMAGE_FILENAME = "duplicate_image_filename"
    AMBIGUOUS_IMAGE_FILENAME = "ambiguous_image_filename"
    UNREFERENCED_IMAGE = "unreferenced_image"
    UNSUPPORTED_IMAGE = "unsupported_image"
    EMPTY_IMAGE = "empty_image"
    IMAGE_TOO_LARGE = "image_too_large"
    ANIMATED_IMAGE = "animated_image"
    IMAGE_DIMENSIONS_EXCEEDED = "image_dimensions_exceeded"
    INVALID_IMAGE = "invalid_image"
    INVALID_BRAND = "invalid_brand"
    INVALID_CLASS_TYPE = "invalid_class_type"
    INVALID_ABV = "invalid_abv"
    INVALID_NET_CONTENTS = "invalid_net_contents"


_ISSUE_MESSAGES: dict[PreflightIssueCode, str] = {
    PreflightIssueCode.EMPTY_BATCH: "Add at least one application row.",
    PreflightIssueCode.TOO_MANY_CASES: "A batch can contain no more than 25 application rows.",
    PreflightIssueCode.TOO_MANY_IMAGES: "Select no more than 25 label images.",
    PreflightIssueCode.WORKBOOK_TOO_LARGE: "Choose a spreadsheet no larger than 1 MB.",
    PreflightIssueCode.AGGREGATE_UPLOAD_TOO_LARGE: (
        "The spreadsheet and images together must not exceed 100 MB."
    ),
    PreflightIssueCode.UNSUPPORTED_SPREADSHEET: "Choose an XLSX workbook or UTF-8 CSV file.",
    PreflightIssueCode.MALFORMED_SPREADSHEET: (
        "The spreadsheet could not be read. Download a fresh template and try again."
    ),
    PreflightIssueCode.ENCRYPTED_WORKBOOK: (
        "Encrypted or legacy binary workbooks are not supported."
    ),
    PreflightIssueCode.MACRO_ENABLED_WORKBOOK: (
        "Macro-enabled workbooks are not supported. Save a macro-free XLSX file."
    ),
    PreflightIssueCode.MISSING_BATCH_WORKSHEET: (
        "The workbook must contain a worksheet named Batch."
    ),
    PreflightIssueCode.SOURCE_ROW_LIMIT_EXCEEDED: (
        "Keep application rows within the first 250 spreadsheet rows."
    ),
    PreflightIssueCode.WORKBOOK_EXPANSION_LIMIT_EXCEEDED: (
        "The workbook expands beyond the safe processing limit."
    ),
    PreflightIssueCode.INVALID_CSV_ENCODING: "Save the CSV as UTF-8 and try again.",
    PreflightIssueCode.NUL_BYTE_NOT_ALLOWED: "Remove NUL characters from the CSV and try again.",
    PreflightIssueCode.MISSING_REQUIRED_COLUMN: "A required spreadsheet column is missing.",
    PreflightIssueCode.UNEXPECTED_COLUMN: (
        "Remove spreadsheet columns that are not in the template."
    ),
    PreflightIssueCode.DUPLICATE_COLUMN: "Each template column may appear only once.",
    PreflightIssueCode.INVALID_COLUMN_ORDER: (
        "Keep the spreadsheet columns in the same order as the template."
    ),
    PreflightIssueCode.CELL_TOO_LONG: "Shorten this spreadsheet value and try again.",
    PreflightIssueCode.FORMULA_NOT_ALLOWED: "Replace the formula with its visible text value.",
    PreflightIssueCode.EXTERNAL_LINK_NOT_ALLOWED: "Remove external workbook links and try again.",
    PreflightIssueCode.MISSING_APPLICATION_ID: "Enter an application ID.",
    PreflightIssueCode.DUPLICATE_APPLICATION_ID: "Use a unique application ID for each row.",
    PreflightIssueCode.MISSING_IMAGE_FILENAME: "Enter the label image filename for this row.",
    PreflightIssueCode.INVALID_IMAGE_FILENAME: (
        "Use only the image's base filename, including its extension."
    ),
    PreflightIssueCode.MISSING_IMAGE: "Select the label image named by this row.",
    PreflightIssueCode.DUPLICATE_IMAGE_FILENAME: "Select each label image filename only once.",
    PreflightIssueCode.AMBIGUOUS_IMAGE_FILENAME: (
        "Two selected image names become identical after filename normalization."
    ),
    PreflightIssueCode.UNREFERENCED_IMAGE: "This selected image is not referenced by any row.",
    PreflightIssueCode.UNSUPPORTED_IMAGE: "Choose a JPEG, PNG, or WebP image.",
    PreflightIssueCode.EMPTY_IMAGE: "Choose a non-empty JPEG, PNG, or WebP image.",
    PreflightIssueCode.IMAGE_TOO_LARGE: "Choose an image no larger than 10 MB.",
    PreflightIssueCode.ANIMATED_IMAGE: "Animated images are not supported.",
    PreflightIssueCode.IMAGE_DIMENSIONS_EXCEEDED: (
        "Choose an image no larger than 40 megapixels or 6,000 pixels on either side."
    ),
    PreflightIssueCode.INVALID_IMAGE: "The selected image could not be validated.",
    PreflightIssueCode.INVALID_BRAND: "Enter an expected brand name of at most 200 characters.",
    PreflightIssueCode.INVALID_CLASS_TYPE: (
        "Enter an expected class/type of at most 200 characters."
    ),
    PreflightIssueCode.INVALID_ABV: "Enter an expected ABV from 0 through 100.",
    PreflightIssueCode.INVALID_NET_CONTENTS: (
        "Enter metric net contents such as 750 mL or 0.75 L."
    ),
}

_WARNING_ISSUES = frozenset({PreflightIssueCode.UNREFERENCED_IMAGE})


class PreflightIssue(BatchContractModel):
    code: PreflightIssueCode
    scope: PreflightIssueScope
    row_number: Annotated[int | None, Field(ge=2)] = None
    field: BatchField | None = None

    @computed_field
    @property
    def severity(self) -> PreflightIssueSeverity:
        if self.code in _WARNING_ISSUES:
            return PreflightIssueSeverity.WARNING
        return PreflightIssueSeverity.ERROR

    @computed_field
    @property
    def message(self) -> str:
        return _ISSUE_MESSAGES[self.code]

    @model_validator(mode="after")
    def require_row_number_only_for_row_issues(self) -> Self:
        if self.scope == PreflightIssueScope.ROW and self.row_number is None:
            raise ValueError("row issues require a spreadsheet row number")
        if self.scope != PreflightIssueScope.ROW and self.row_number is not None:
            raise ValueError("only row issues may include a spreadsheet row number")
        return self


class BatchExpectedInput(BatchContractModel):
    brand_name: Annotated[str, Field(max_length=MAX_EXPECTED_TEXT_CHARACTERS)]
    class_type: Annotated[str, Field(max_length=MAX_EXPECTED_TEXT_CHARACTERS)]
    expected_abv: Annotated[str, Field(max_length=32)]
    expected_net_contents: Annotated[str, Field(max_length=MAX_NET_CONTENTS_CELL_CHARACTERS)]


class BatchCasePatchRequest(BatchContractModel):
    brand_name: Annotated[str | None, Field(max_length=MAX_EXPECTED_TEXT_CHARACTERS)] = None
    class_type: Annotated[str | None, Field(max_length=MAX_EXPECTED_TEXT_CHARACTERS)] = None
    expected_abv: Annotated[str | None, Field(max_length=32)] = None
    expected_net_contents: Annotated[
        str | None, Field(max_length=MAX_NET_CONTENTS_CELL_CHARACTERS)
    ] = None

    @model_validator(mode="after")
    def require_at_least_one_correction(self) -> Self:
        if all(value is None for value in self.__dict__.values()):
            raise ValueError("a correction request must include at least one expected value")
        return self


class BatchStartRequest(BatchContractModel):
    selection: BatchStartSelection


class BatchStateCounts(BatchContractModel):
    total: Annotated[int, Field(ge=1, le=MAX_BATCH_CASES)]
    needs_correction: Annotated[int, Field(ge=0, le=MAX_BATCH_CASES)] = 0
    ready: Annotated[int, Field(ge=0, le=MAX_BATCH_CASES)] = 0
    queued: Annotated[int, Field(ge=0, le=MAX_BATCH_CASES)] = 0
    processing: Annotated[int, Field(ge=0, le=MAX_BATCH_CASES)] = 0
    completed: Annotated[int, Field(ge=0, le=MAX_BATCH_CASES)] = 0
    failed: Annotated[int, Field(ge=0, le=MAX_BATCH_CASES)] = 0
    interrupted: Annotated[int, Field(ge=0, le=MAX_BATCH_CASES)] = 0
    not_selected: Annotated[int, Field(ge=0, le=MAX_BATCH_CASES)] = 0

    @model_validator(mode="after")
    def require_state_counts_to_equal_total(self) -> Self:
        state_total = sum(
            (
                self.needs_correction,
                self.ready,
                self.queued,
                self.processing,
                self.completed,
                self.failed,
                self.interrupted,
                self.not_selected,
            )
        )
        if state_total != self.total:
            raise ValueError("case state counts must equal the total case count")
        return self


class BatchCaseSummary(BatchContractModel):
    case_id: UUID
    row_number: Annotated[int, Field(ge=2)]
    application_id: Annotated[str, Field(max_length=MAX_APPLICATION_ID_CHARACTERS)]
    label_image_filename: Annotated[str, Field(max_length=MAX_IMAGE_FILENAME_CHARACTERS)]
    state: BatchCaseState
    issues: Annotated[list[PreflightIssue], Field(max_length=MAX_PREFLIGHT_ISSUES_PER_CASE)] = (
        Field(default_factory=list)
    )
    outcome: OverallOutcome | None = None
    processing_duration_ms: Annotated[int | None, Field(ge=0)] = None
    short_reason: Annotated[str | None, Field(max_length=MAX_CASE_SUMMARY_REASON_CHARACTERS)] = None

    @model_validator(mode="after")
    def require_terminal_summary_fields(self) -> Self:
        if self.state == BatchCaseState.COMPLETED and (
            self.outcome is None or self.short_reason is None
        ):
            raise ValueError("completed cases require a comparison outcome and short reason")
        if self.state != BatchCaseState.COMPLETED and self.outcome is not None:
            raise ValueError("only completed cases may include a comparison outcome")
        if self.state in {BatchCaseState.FAILED, BatchCaseState.INTERRUPTED}:
            if self.short_reason is None:
                raise ValueError("failed and interrupted cases require a short reason")
        elif self.state != BatchCaseState.COMPLETED and self.short_reason is not None:
            raise ValueError("only terminal cases may include a short reason")
        return self


class StoredBatchCaseResult(BatchContractModel):
    result: ReviewResult
    processing_mode: Literal["synthetic", "live"]
    correlation_id: UUID
    completed_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def require_result_expiry_after_completion(self) -> Self:
        if self.expires_at <= self.completed_at:
            raise ValueError("stored result expiry must be after completion")
        return self


class BatchCaseDetail(BatchContractModel):
    summary: BatchCaseSummary
    expected_input: BatchExpectedInput
    normalized_expected: ExpectedReview | None = None
    result: StoredBatchCaseResult | None = None

    @model_validator(mode="after")
    def require_detail_consistent_with_state(self) -> Self:
        state = self.summary.state
        validated_states = {
            BatchCaseState.READY,
            BatchCaseState.QUEUED,
            BatchCaseState.PROCESSING,
            BatchCaseState.COMPLETED,
            BatchCaseState.FAILED,
            BatchCaseState.INTERRUPTED,
        }
        if state in validated_states and self.normalized_expected is None:
            raise ValueError("valid cases require normalized expected values")
        if state == BatchCaseState.COMPLETED and self.result is None:
            raise ValueError("completed cases require a stored result")
        if state != BatchCaseState.COMPLETED and self.result is not None:
            raise ValueError("only completed cases may include a stored result")
        return self


class BatchResponse(BatchContractModel):
    batch_id: UUID
    state: BatchState
    created_at: AwareDatetime
    expires_at: AwareDatetime
    counts: BatchStateCounts
    cases: Annotated[list[BatchCaseSummary], Field(max_length=MAX_BATCH_CASES)]
    next_poll_after_ms: Annotated[int | None, Field(ge=250, le=10_000)] = None

    @model_validator(mode="after")
    def require_bounded_absolute_expiry_and_matching_counts(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("batch expiry must be after creation")
        if self.expires_at > self.created_at + timedelta(hours=BATCH_RETENTION_HOURS):
            raise ValueError("batch content cannot be retained for more than 24 hours")
        if self.counts.total != len(self.cases):
            raise ValueError("batch counts must match the returned case summaries")
        active = self.state in {BatchState.QUEUED, BatchState.PROCESSING}
        if active != (self.next_poll_after_ms is not None):
            raise ValueError("only active batches include a next-poll interval")
        return self


class BatchPreflightResponse(BatchResponse):
    state: Literal[BatchState.DRAFT] = BatchState.DRAFT


class BatchPreflightErrorResponse(BatchContractModel):
    issues: Annotated[list[PreflightIssue], Field(min_length=1, max_length=20)]
    correlation_id: UUID
    processing_duration_ms: Annotated[int, Field(ge=0)]


class BatchErrorCode(StrEnum):
    NOT_FOUND = "batch_not_found"
    STATE_CONFLICT = "batch_state_conflict"
    NO_READY_CASES = "batch_has_no_ready_cases"
    CORRECTIONS_REMAIN = "batch_has_corrections"
    RESULTS_UNAVAILABLE = "batch_results_unavailable"
    REQUEST_TOO_LARGE = "batch_request_too_large"
    PROCESSING_UNAVAILABLE = "batch_processing_unavailable"


BATCH_NOT_FOUND_MESSAGE = "The requested batch is unavailable."
_BATCH_ERROR_MESSAGES: dict[BatchErrorCode, str] = {
    BatchErrorCode.NOT_FOUND: BATCH_NOT_FOUND_MESSAGE,
    BatchErrorCode.STATE_CONFLICT: "This batch can no longer be changed or started.",
    BatchErrorCode.NO_READY_CASES: "Correct at least one case before starting the batch.",
    BatchErrorCode.CORRECTIONS_REMAIN: (
        "Correct every case or explicitly choose to process ready cases only."
    ),
    BatchErrorCode.RESULTS_UNAVAILABLE: "Start the batch before downloading results.",
    BatchErrorCode.REQUEST_TOO_LARGE: "The batch request exceeds the allowed size.",
    BatchErrorCode.PROCESSING_UNAVAILABLE: (
        "Batch processing is temporarily unavailable. "
        "Review the preflight results and try again later."
    ),
}


class BatchErrorResponse(BatchContractModel):
    code: BatchErrorCode
    correlation_id: UUID
    processing_duration_ms: Annotated[int, Field(ge=0)]

    @computed_field
    @property
    def message(self) -> str:
        return _BATCH_ERROR_MESSAGES[self.code]


ALLOWED_BATCH_TRANSITIONS: dict[BatchState, frozenset[BatchState]] = {
    BatchState.DRAFT: frozenset({BatchState.QUEUED}),
    BatchState.QUEUED: frozenset({BatchState.PROCESSING, BatchState.INTERRUPTED}),
    BatchState.PROCESSING: frozenset({BatchState.COMPLETED, BatchState.INTERRUPTED}),
    BatchState.COMPLETED: frozenset(),
    BatchState.INTERRUPTED: frozenset(),
}

ALLOWED_CASE_TRANSITIONS: dict[BatchCaseState, frozenset[BatchCaseState]] = {
    BatchCaseState.NEEDS_CORRECTION: frozenset({BatchCaseState.READY, BatchCaseState.NOT_SELECTED}),
    BatchCaseState.READY: frozenset(
        {
            BatchCaseState.NEEDS_CORRECTION,
            BatchCaseState.QUEUED,
            BatchCaseState.NOT_SELECTED,
        }
    ),
    BatchCaseState.QUEUED: frozenset(
        {BatchCaseState.PROCESSING, BatchCaseState.FAILED, BatchCaseState.INTERRUPTED}
    ),
    BatchCaseState.PROCESSING: frozenset(
        {BatchCaseState.COMPLETED, BatchCaseState.FAILED, BatchCaseState.INTERRUPTED}
    ),
    BatchCaseState.COMPLETED: frozenset(),
    BatchCaseState.FAILED: frozenset(),
    BatchCaseState.INTERRUPTED: frozenset(),
    BatchCaseState.NOT_SELECTED: frozenset(),
}


def batch_transition_allowed(current: BatchState, target: BatchState) -> bool:
    return target in ALLOWED_BATCH_TRANSITIONS[current]


def case_transition_allowed(current: BatchCaseState, target: BatchCaseState) -> bool:
    return target in ALLOWED_CASE_TRANSITIONS[current]
