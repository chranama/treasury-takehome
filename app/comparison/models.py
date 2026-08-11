from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Visibility(StrEnum):
    VISIBLE = "visible"
    NOT_VISIBLE = "not_visible"
    UNCERTAIN = "uncertain"


class Readability(StrEnum):
    READABLE = "readable"
    PARTIALLY_READABLE = "partially_readable"
    UNREADABLE = "unreadable"
    UNCERTAIN = "uncertain"


class TextWeight(StrEnum):
    BOLD = "bold"
    NOT_BOLD = "not_bold"
    UNCERTAIN = "uncertain"


class NetContentsUnit(StrEnum):
    MILLILITER = "mL"
    LITER = "L"


class CheckName(StrEnum):
    BRAND_NAME = "brand_name"
    CLASS_TYPE = "class_type"
    ALCOHOL_CONTENT = "alcohol_content"
    NET_CONTENTS = "net_contents"
    GOVERNMENT_WARNING = "government_warning"


class CheckStatus(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    NEEDS_REVIEW = "needs_review"
    NOT_EVALUATED = "not_evaluated"


class OverallOutcome(StrEnum):
    ALL_CHECKS_PASSED = "all_checks_passed"
    NEEDS_REVIEW = "needs_review"
    UNABLE_TO_PROCESS = "unable_to_process"


class ApplicationErrorCategory(StrEnum):
    INVALID_INPUT = "invalid_input"
    LIVE_EXTRACTION_DISABLED = "live_extraction_disabled"
    CAPACITY_REACHED = "capacity_reached"
    TRAFFIC_THROTTLED = "traffic_throttled"
    DUPLICATE_SUBMISSION = "duplicate_submission"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MALFORMED_PROVIDER_OUTPUT = "malformed_provider_output"
    INTERNAL_ERROR = "internal_error"


class ExpectedNetContents(DomainModel):
    value: Annotated[Decimal, Field(gt=0)]
    unit: NetContentsUnit


class ExpectedReview(DomainModel):
    brand_name: str
    class_type: str
    abv: Annotated[Decimal, Field(ge=0, le=100)]
    net_contents: ExpectedNetContents

    @field_validator("brand_name", "class_type")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped


class TextCandidate(DomainModel):
    text: str
    note: Annotated[str | None, Field(max_length=500)] = None

    @field_validator("text")
    @classmethod
    def require_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("candidate text must not be blank")
        return value


class FieldObservation(DomainModel):
    candidates: list[TextCandidate] = Field(default_factory=list)
    visibility: Visibility
    readability: Readability
    note: Annotated[str | None, Field(max_length=500)] = None


class WarningObservation(DomainModel):
    text: str | None = None
    heading_text: str | None = None
    heading_weight: TextWeight = TextWeight.UNCERTAIN
    body_weight: TextWeight = TextWeight.UNCERTAIN
    visibility: Visibility
    readability: Readability
    note: Annotated[str | None, Field(max_length=500)] = None


class ExtractionObservations(DomainModel):
    brand_name: FieldObservation
    class_type: FieldObservation
    alcohol_content: FieldObservation
    net_contents: FieldObservation
    government_warning: WarningObservation
    note: Annotated[str | None, Field(max_length=500)] = None


class CheckResult(DomainModel):
    name: CheckName
    status: CheckStatus
    expected_value: str | None = None
    extracted_values: list[str] = Field(default_factory=list)
    normalized_expected: str | None = None
    normalized_extracted: list[str] = Field(default_factory=list)
    reason: str
    limitations: list[str] = Field(default_factory=list)

    @field_validator("reason")
    @classmethod
    def require_reason(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("a check result requires a reason")
        return stripped


class ReviewResult(DomainModel):
    outcome: OverallOutcome
    checks: list[CheckResult]
    processing_duration_ms: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def require_exactly_one_of_each_check(self) -> Self:
        names = [check.name for check in self.checks]
        if len(names) != len(CheckName) or set(names) != set(CheckName):
            raise ValueError("review results require exactly one of each check")

        statuses = [check.status for check in self.checks]
        if self.outcome == OverallOutcome.ALL_CHECKS_PASSED and any(
            status != CheckStatus.MATCH for status in statuses
        ):
            raise ValueError("all checks passed requires five matching checks")
        if self.outcome == OverallOutcome.UNABLE_TO_PROCESS and any(
            status != CheckStatus.NOT_EVALUATED for status in statuses
        ):
            raise ValueError("unable to process requires five unevaluated checks")
        if self.outcome == OverallOutcome.NEEDS_REVIEW and (
            all(status == CheckStatus.MATCH for status in statuses)
            or all(status == CheckStatus.NOT_EVALUATED for status in statuses)
        ):
            raise ValueError("needs review requires at least one evaluated nonmatch")
        return self


class SafeApplicationError(DomainModel):
    category: ApplicationErrorCategory
    message: str
    correlation_id: str | None = None

    @field_validator("message")
    @classmethod
    def require_safe_message(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("an application error requires a message")
        return stripped
