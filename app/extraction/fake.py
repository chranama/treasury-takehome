from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from app.comparison.constants import GOVERNMENT_WARNING_TEXT
from app.comparison.models import (
    ExtractionObservations,
    FieldObservation,
    Readability,
    TextCandidate,
    TextWeight,
    Visibility,
    WarningObservation,
)
from app.extraction.contract import (
    ExtractionAdapter,
    ExtractionError,
    ExtractionErrorKind,
    PreparedImage,
)


class FakeExtractionScenario(StrEnum):
    CLEAR_MATCHING_LABEL = "clear_matching_label"
    BRAND_MISMATCH = "brand_mismatch"
    CLASS_TYPE_MISMATCH = "class_type_mismatch"
    EQUIVALENT_PROOF_AND_ABV = "equivalent_proof_and_abv"
    CONFLICTING_PROOF_AND_ABV = "conflicting_proof_and_abv"
    EQUIVALENT_NET_CONTENTS = "equivalent_net_contents"
    MISMATCHED_NET_CONTENTS = "mismatched_net_contents"
    ALTERED_WARNING_TEXT = "altered_warning_text"
    MISSING_WARNING = "missing_warning"
    UNCERTAIN_WARNING_STYLE = "uncertain_warning_style"
    AMBIGUOUS_CANDIDATES = "ambiguous_candidates"
    UNREADABLE_IMAGE = "unreadable_image"


class FakeExtractionFailure(StrEnum):
    TIMEOUT = "timeout"
    MALFORMED_OUTPUT = "malformed_output"
    TRANSIENT_FAILURE = "transient_failure"
    UNAVAILABLE = "unavailable"
    INTERNAL_FAILURE = "internal_failure"


def _field(*values: str) -> FieldObservation:
    return FieldObservation(
        candidates=[TextCandidate(text=value) for value in values],
        visibility=Visibility.VISIBLE,
        readability=Readability.READABLE,
    )


def _warning() -> WarningObservation:
    return WarningObservation(
        text=GOVERNMENT_WARNING_TEXT,
        heading_text="GOVERNMENT WARNING:",
        heading_weight=TextWeight.BOLD,
        body_weight=TextWeight.NOT_BOLD,
        visibility=Visibility.VISIBLE,
        readability=Readability.READABLE,
    )


def _baseline() -> ExtractionObservations:
    return ExtractionObservations(
        brand_name=_field("OLD TOM"),
        class_type=_field("Kentucky Straight Bourbon Whiskey"),
        alcohol_content=_field("45% Alc./Vol."),
        net_contents=_field("750 mL"),
        government_warning=_warning(),
    )


def _brand_mismatch() -> ExtractionObservations:
    observations = _baseline()
    observations.brand_name = _field("Treasury Select")
    return observations


def _class_type_mismatch() -> ExtractionObservations:
    observations = _baseline()
    observations.class_type = _field("American Blended Whiskey")
    return observations


def _equivalent_proof_and_abv() -> ExtractionObservations:
    observations = _baseline()
    observations.alcohol_content = _field("45% Alc./Vol.", "90 Proof")
    return observations


def _conflicting_proof_and_abv() -> ExtractionObservations:
    observations = _baseline()
    observations.alcohol_content = _field("45% Alc./Vol.", "80 Proof")
    return observations


def _equivalent_net_contents() -> ExtractionObservations:
    observations = _baseline()
    observations.net_contents = _field("750 mL", "0.75 L")
    return observations


def _mismatched_net_contents() -> ExtractionObservations:
    observations = _baseline()
    observations.net_contents = _field("700 mL")
    return observations


def _altered_warning_text() -> ExtractionObservations:
    observations = _baseline()
    observations.government_warning.text = GOVERNMENT_WARNING_TEXT.replace(
        "may cause health problems", "might cause health problems"
    )
    return observations


def _missing_warning() -> ExtractionObservations:
    observations = _baseline()
    observations.government_warning = WarningObservation(
        visibility=Visibility.NOT_VISIBLE,
        readability=Readability.UNREADABLE,
        note="No Government Warning was visible in the supplied image.",
    )
    return observations


def _uncertain_warning_style() -> ExtractionObservations:
    observations = _baseline()
    observations.government_warning.heading_weight = TextWeight.UNCERTAIN
    observations.government_warning.note = "The heading weight could not be determined."
    return observations


def _ambiguous_candidates() -> ExtractionObservations:
    observations = _baseline()
    observations.brand_name = _field("OLD TOM", "OLD TOM RESERVE")
    return observations


def _unreadable_image() -> ExtractionObservations:
    def unreadable_field() -> FieldObservation:
        return FieldObservation(
            visibility=Visibility.UNCERTAIN,
            readability=Readability.UNREADABLE,
            note="The image was too unclear to read this field.",
        )

    return ExtractionObservations(
        brand_name=unreadable_field(),
        class_type=unreadable_field(),
        alcohol_content=unreadable_field(),
        net_contents=unreadable_field(),
        government_warning=WarningObservation(
            visibility=Visibility.UNCERTAIN,
            readability=Readability.UNREADABLE,
            note="The image was too unclear to evaluate the Government Warning.",
        ),
        note="The supplied image was unreadable.",
    )


_SCENARIO_BUILDERS: dict[FakeExtractionScenario, Callable[[], ExtractionObservations]] = {
    FakeExtractionScenario.CLEAR_MATCHING_LABEL: _baseline,
    FakeExtractionScenario.BRAND_MISMATCH: _brand_mismatch,
    FakeExtractionScenario.CLASS_TYPE_MISMATCH: _class_type_mismatch,
    FakeExtractionScenario.EQUIVALENT_PROOF_AND_ABV: _equivalent_proof_and_abv,
    FakeExtractionScenario.CONFLICTING_PROOF_AND_ABV: _conflicting_proof_and_abv,
    FakeExtractionScenario.EQUIVALENT_NET_CONTENTS: _equivalent_net_contents,
    FakeExtractionScenario.MISMATCHED_NET_CONTENTS: _mismatched_net_contents,
    FakeExtractionScenario.ALTERED_WARNING_TEXT: _altered_warning_text,
    FakeExtractionScenario.MISSING_WARNING: _missing_warning,
    FakeExtractionScenario.UNCERTAIN_WARNING_STYLE: _uncertain_warning_style,
    FakeExtractionScenario.AMBIGUOUS_CANDIDATES: _ambiguous_candidates,
    FakeExtractionScenario.UNREADABLE_IMAGE: _unreadable_image,
}

_FAILURES: dict[FakeExtractionFailure, tuple[ExtractionErrorKind, str, bool]] = {
    FakeExtractionFailure.TIMEOUT: (
        ExtractionErrorKind.TIMEOUT,
        "Label extraction timed out.",
        True,
    ),
    FakeExtractionFailure.MALFORMED_OUTPUT: (
        ExtractionErrorKind.MALFORMED_OUTPUT,
        "The extraction response could not be validated.",
        False,
    ),
    FakeExtractionFailure.TRANSIENT_FAILURE: (
        ExtractionErrorKind.TRANSIENT_FAILURE,
        "Label extraction is temporarily unavailable.",
        True,
    ),
    FakeExtractionFailure.UNAVAILABLE: (
        ExtractionErrorKind.UNAVAILABLE,
        "Label extraction is unavailable.",
        False,
    ),
    FakeExtractionFailure.INTERNAL_FAILURE: (
        ExtractionErrorKind.INTERNAL_FAILURE,
        "Label extraction failed unexpectedly.",
        False,
    ),
}


@dataclass(frozen=True, slots=True)
class FakeExtractionAdapter(ExtractionAdapter):
    scenario: FakeExtractionScenario = FakeExtractionScenario.CLEAR_MATCHING_LABEL
    failure: FakeExtractionFailure | None = None

    async def extract(self, image: PreparedImage) -> ExtractionObservations:
        del image
        if self.failure is not None:
            kind, safe_message, retryable = _FAILURES[self.failure]
            raise ExtractionError(
                kind=kind,
                safe_message=safe_message,
                retryable=retryable,
            )
        return _SCENARIO_BUILDERS[self.scenario]()
