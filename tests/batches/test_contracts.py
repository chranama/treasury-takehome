from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.batches import (
    BatchCasePatchRequest,
    BatchCaseState,
    BatchCaseSummary,
    BatchErrorCode,
    BatchErrorResponse,
    BatchPreflightResponse,
    BatchResponse,
    BatchState,
    BatchStateCounts,
    PreflightIssue,
    PreflightIssueCode,
    PreflightIssueScope,
    PreflightIssueSeverity,
    batch_transition_allowed,
    case_transition_allowed,
)


def case_summary(**overrides: object) -> BatchCaseSummary:
    values: dict[str, object] = {
        "case_id": uuid4(),
        "row_number": 2,
        "application_id": "APP-001",
        "label_image_filename": "label-001.png",
        "state": BatchCaseState.READY,
    }
    values.update(overrides)
    return BatchCaseSummary(**values)


def test_preflight_messages_are_derived_from_stable_codes() -> None:
    issue = PreflightIssue(
        code=PreflightIssueCode.MISSING_IMAGE,
        scope=PreflightIssueScope.ROW,
        row_number=4,
    )

    assert issue.message == "Select the label image named by this row."
    assert issue.severity == PreflightIssueSeverity.ERROR
    assert "message" in issue.model_dump()
    assert "severity" in issue.model_dump()


def test_unknown_and_expired_batches_share_one_bounded_not_found_message() -> None:
    error = BatchErrorResponse(
        code=BatchErrorCode.NOT_FOUND,
        correlation_id=uuid4(),
        processing_duration_ms=2,
    )

    assert error.message == "The requested batch is unavailable."


def test_unreferenced_images_are_warnings() -> None:
    issue = PreflightIssue(
        code=PreflightIssueCode.UNREFERENCED_IMAGE,
        scope=PreflightIssueScope.IMAGE,
    )

    assert issue.severity == PreflightIssueSeverity.WARNING


def test_row_issue_requires_an_original_spreadsheet_row_number() -> None:
    with pytest.raises(ValidationError):
        PreflightIssue(
            code=PreflightIssueCode.INVALID_ABV,
            scope=PreflightIssueScope.ROW,
        )


def test_correction_request_must_change_at_least_one_expected_value() -> None:
    with pytest.raises(ValidationError):
        BatchCasePatchRequest()


def test_batch_state_counts_must_equal_total() -> None:
    with pytest.raises(ValidationError):
        BatchStateCounts(total=2, ready=1)


def test_completed_case_requires_an_outcome() -> None:
    with pytest.raises(ValidationError):
        case_summary(state=BatchCaseState.COMPLETED)


def test_noncompleted_case_cannot_claim_an_outcome() -> None:
    with pytest.raises(ValidationError):
        case_summary(state=BatchCaseState.FAILED, outcome="needs_review")


def test_batch_response_has_absolute_non_sliding_expiry() -> None:
    created_at = datetime(2026, 8, 12, 12, tzinfo=UTC)
    case = case_summary()
    response = BatchResponse(
        batch_id=uuid4(),
        state=BatchState.DRAFT,
        created_at=created_at,
        expires_at=created_at + timedelta(hours=24),
        counts=BatchStateCounts(total=1, ready=1),
        cases=[case],
    )

    assert response.expires_at == created_at + timedelta(hours=24)

    with pytest.raises(ValidationError):
        BatchResponse(
            **{
                **response.model_dump(),
                "expires_at": created_at + timedelta(hours=24, microseconds=1),
            }
        )


def test_preflight_response_cannot_claim_a_started_state() -> None:
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)

    with pytest.raises(ValidationError):
        BatchPreflightResponse(
            batch_id=uuid4(),
            state=BatchState.QUEUED,
            created_at=now,
            expires_at=now + timedelta(hours=24),
            counts=BatchStateCounts(total=1, queued=1),
            cases=[case_summary(state=BatchCaseState.QUEUED)],
            next_poll_after_ms=1_500,
        )


def test_only_active_batch_responses_include_a_poll_interval() -> None:
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    case = case_summary(state=BatchCaseState.QUEUED)
    active = {
        "batch_id": uuid4(),
        "state": BatchState.QUEUED,
        "created_at": now,
        "expires_at": now + timedelta(hours=24),
        "counts": BatchStateCounts(total=1, queued=1),
        "cases": [case],
    }

    with pytest.raises(ValidationError):
        BatchResponse(**active)

    response = BatchResponse(**active, next_poll_after_ms=1_500)
    assert response.next_poll_after_ms == 1_500


def test_state_transition_contract_prevents_replay_of_terminal_work() -> None:
    assert batch_transition_allowed(BatchState.DRAFT, BatchState.QUEUED)
    assert not batch_transition_allowed(BatchState.COMPLETED, BatchState.PROCESSING)
    assert case_transition_allowed(BatchCaseState.READY, BatchCaseState.QUEUED)
    assert case_transition_allowed(BatchCaseState.NEEDS_CORRECTION, BatchCaseState.NOT_SELECTED)
    assert not case_transition_allowed(BatchCaseState.COMPLETED, BatchCaseState.PROCESSING)
