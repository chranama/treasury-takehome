import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.comparison import (
    CheckStatus,
    ExpectedReview,
    OverallOutcome,
    Readability,
    TextWeight,
    Visibility,
)

CASE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FixtureFamily(StrEnum):
    DETERMINISTIC_DOMAIN = "deterministic_domain"
    IMAGE_INTAKE_SECURITY = "image_intake_security"
    HOSTED_MODEL_VISUAL = "hosted_model_visual"
    P1_BATCH_PACKAGE = "p1_batch_package"
    REVIEWER_DEMO = "reviewer_demo"


class SuiteOwner(StrEnum):
    COMPARISON = "comparison"
    IMAGE_INTAKE = "image_intake"
    HOSTED_EXTRACTION = "hosted_extraction"
    BATCH_WORKFLOW = "batch_workflow"
    DEMO_BUNDLE = "demo_bundle"


OWNER_FAMILY = {
    SuiteOwner.COMPARISON: FixtureFamily.DETERMINISTIC_DOMAIN,
    SuiteOwner.IMAGE_INTAKE: FixtureFamily.IMAGE_INTAKE_SECURITY,
    SuiteOwner.HOSTED_EXTRACTION: FixtureFamily.HOSTED_MODEL_VISUAL,
    SuiteOwner.BATCH_WORKFLOW: FixtureFamily.P1_BATCH_PACKAGE,
    SuiteOwner.DEMO_BUNDLE: FixtureFamily.REVIEWER_DEMO,
}


class EvaluationLayer(StrEnum):
    MANIFEST_SCHEMA = "manifest_schema"
    DETERMINISTIC_RENDERING = "deterministic_rendering"
    DOMAIN = "domain"
    IMAGE_INTAKE = "image_intake"
    FAKE_ADAPTER_API = "fake_adapter_api"
    BROWSER = "browser"
    P1_PREFLIGHT_BATCH = "p1_preflight_batch"
    MANUAL_VISUAL_INSPECTION = "manual_visual_inspection"
    LIVE_PROVIDER = "live_provider"
    DEPLOYED_PATH = "deployed_path"


class UncertaintyExpectation(StrEnum):
    REQUIRED = "required"
    ALLOWED = "allowed"
    FORBIDDEN = "forbidden"


class CandidatePolicy(StrEnum):
    EXACT = "exact"
    CONTAINS_ALL = "contains_all"
    EMPTY = "empty"


class TextPolicy(StrEnum):
    EXACT = "exact"
    ABSENT = "absent"
    ANY = "any"


class RendererSpec(ManifestModel):
    id: Annotated[str, Field(min_length=1, max_length=100)]
    version: Annotated[str, Field(min_length=1, max_length=50)]
    font_identity: Annotated[str, Field(min_length=1, max_length=100)]
    seed: int | None = None

    @field_validator("id", "version", "font_identity")
    @classmethod
    def strip_nonblank_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("renderer identity values must not be blank")
        return stripped


class ArtifactExpectation(ManifestModel):
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    media_type: Annotated[str, Field(min_length=1, max_length=100)]
    sha256: str

    @field_validator("filename")
    @classmethod
    def require_basename(cls, value: str) -> str:
        if value != Path(value).name or value in {".", ".."}:
            raise ValueError("artifact filename must be a basename")
        return value

    @field_validator("media_type")
    @classmethod
    def strip_media_type(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("artifact media type must not be blank")
        return stripped

    @field_validator("sha256")
    @classmethod
    def require_lowercase_sha256(cls, value: str) -> str:
        if not SHA256_PATTERN.fullmatch(value):
            raise ValueError("artifact sha256 must be 64 lowercase hexadecimal characters")
        return value


class VisibleTextExpectation(ManifestModel):
    brand_name: list[str]
    class_type: list[str]
    alcohol_content: list[str]
    net_contents: list[str]
    government_warning: str | None
    warning_heading: str | None

    @field_validator(
        "brand_name",
        "class_type",
        "alcohol_content",
        "net_contents",
    )
    @classmethod
    def require_unique_nonblank_values(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("expected visible text values must not be blank")
        if len(values) != len(set(values)):
            raise ValueError("expected visible text values must be unique")
        return values

    @field_validator("government_warning", "warning_heading")
    @classmethod
    def reject_blank_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("optional visible text must be null or nonblank")
        return value


class FieldObservationRequirement(ManifestModel):
    candidates: CandidatePolicy
    visibility: Annotated[list[Visibility], Field(min_length=1)]
    readability: Annotated[list[Readability], Field(min_length=1)]

    @field_validator("visibility", "readability")
    @classmethod
    def require_unique_allowed_values(cls, values: list[object]) -> list[object]:
        if len(values) != len(set(values)):
            raise ValueError("allowed observation values must be unique")
        return values


class WarningObservationRequirement(ManifestModel):
    text: TextPolicy
    heading_text: TextPolicy
    heading_weight: Annotated[list[TextWeight], Field(min_length=1)]
    body_weight: Annotated[list[TextWeight], Field(min_length=1)]
    visibility: Annotated[list[Visibility], Field(min_length=1)]
    readability: Annotated[list[Readability], Field(min_length=1)]

    @field_validator("heading_weight", "body_weight", "visibility", "readability")
    @classmethod
    def require_unique_allowed_values(cls, values: list[object]) -> list[object]:
        if len(values) != len(set(values)):
            raise ValueError("allowed observation values must be unique")
        return values


class ObservationRequirements(ManifestModel):
    brand_name: FieldObservationRequirement
    class_type: FieldObservationRequirement
    alcohol_content: FieldObservationRequirement
    net_contents: FieldObservationRequirement
    government_warning: WarningObservationRequirement


class ExpectedCheckStatuses(ManifestModel):
    brand_name: CheckStatus
    class_type: CheckStatus
    alcohol_content: CheckStatus
    net_contents: CheckStatus
    government_warning: CheckStatus


class ExpectedReviewResult(ManifestModel):
    outcome: OverallOutcome
    checks: ExpectedCheckStatuses

    @model_validator(mode="after")
    def require_consistent_outcome(self) -> Self:
        statuses = list(self.checks.model_dump().values())
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


class EvaluationCaseV2(ManifestModel):
    id: Annotated[str, Field(min_length=1, max_length=100)]
    purpose: Annotated[str, Field(min_length=1, max_length=500)]
    families: Annotated[list[FixtureFamily], Field(min_length=1)]
    layers: Annotated[list[EvaluationLayer], Field(min_length=1)]
    renderer: RendererSpec | None = None
    artwork: dict[str, object] | None = None
    expected_visible_text: VisibleTextExpectation | None = None
    expected_application: ExpectedReview | None = None
    required_observations: ObservationRequirements | None = None
    expected_review: ExpectedReviewResult
    uncertainty: UncertaintyExpectation
    artifacts: list[ArtifactExpectation] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def require_stable_case_id(cls, value: str) -> str:
        if not CASE_ID_PATTERN.fullmatch(value):
            raise ValueError("case id must be a lowercase hyphenated identifier")
        return value

    @field_validator("purpose")
    @classmethod
    def strip_purpose(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("case purpose must not be blank")
        return stripped

    @field_validator("families", "layers")
    @classmethod
    def require_unique_membership(cls, values: list[object]) -> list[object]:
        if len(values) != len(set(values)):
            raise ValueError("fixture family and layer memberships must be unique")
        return values

    @model_validator(mode="after")
    def require_layer_specific_metadata(self) -> Self:
        artifact_names = [artifact.filename for artifact in self.artifacts]
        if len(artifact_names) != len(set(artifact_names)):
            raise ValueError("artifact filenames must be unique within a case")

        if FixtureFamily.HOSTED_MODEL_VISUAL in self.families:
            required = {
                "renderer": self.renderer,
                "artwork": self.artwork,
                "expected_visible_text": self.expected_visible_text,
                "expected_application": self.expected_application,
                "required_observations": self.required_observations,
                "artifacts": self.artifacts,
            }
            missing = [name for name, value in required.items() if value is None or value == []]
            if missing:
                raise ValueError("hosted-model visual cases require " + ", ".join(sorted(missing)))
            if EvaluationLayer.LIVE_PROVIDER not in self.layers:
                raise ValueError("hosted-model visual cases must include the live-provider layer")

        if self.renderer is not None and (self.artwork is None or not self.artifacts):
            raise ValueError("rendered cases require artwork parameters and hashed artifacts")

        if EvaluationLayer.LIVE_PROVIDER in self.layers and self.required_observations is None:
            raise ValueError("live-provider cases require observation properties")
        return self


class EvaluationManifestV2(ManifestModel):
    schema_version: Literal[2]
    revision: Annotated[str, Field(min_length=1, max_length=100)]
    owner: SuiteOwner
    purpose: Annotated[str, Field(min_length=1, max_length=500)]
    cases: Annotated[list[EvaluationCaseV2], Field(min_length=1)]

    @field_validator("revision")
    @classmethod
    def require_stable_revision(cls, value: str) -> str:
        if not CASE_ID_PATTERN.fullmatch(value):
            raise ValueError("manifest revision must be a lowercase hyphenated identifier")
        return value

    @field_validator("purpose")
    @classmethod
    def strip_purpose(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("manifest purpose must not be blank")
        return stripped

    @model_validator(mode="after")
    def require_unique_owned_cases(self) -> Self:
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case ids must be unique within a manifest")

        owned_family = OWNER_FAMILY[self.owner]
        unowned = [case.id for case in self.cases if owned_family not in case.families]
        if unowned:
            raise ValueError(
                f"suite owner {self.owner.value} requires family {owned_family.value} "
                f"on cases: {', '.join(unowned)}"
            )
        return self


def load_manifest_v2(path: Path) -> EvaluationManifestV2:
    return EvaluationManifestV2.model_validate_json(path.read_text(encoding="utf-8"))


def manifest_schema_v2() -> dict[str, object]:
    """Return the authoritative JSON Schema for tooling and future fixture generators."""

    return EvaluationManifestV2.model_json_schema()
