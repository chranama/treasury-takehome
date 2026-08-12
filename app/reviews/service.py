import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Literal, cast

from fastapi import UploadFile
from pydantic import ValidationError

from app.api.errors import ReviewApiError
from app.comparison import (
    ApplicationErrorCategory,
    CheckStatus,
    ExpectedReview,
    ExtractionObservations,
    ReviewResult,
    compare_review,
)
from app.config import Settings
from app.extraction import (
    ExtractionAdapter,
    ExtractionError,
    ExtractionErrorKind,
    MeteredExtractionAdapter,
    MeteredExtractionResult,
    PreparedImage,
    estimated_cost_usd,
)
from app.reviews.attempts import (
    AttemptGate,
    AttemptRejected,
    AttemptRejectionKind,
    AttemptSubmission,
    AttemptSuccess,
)
from app.storage import ImageIntakeError, ImageIntakeErrorKind, prepare_uploaded_image

ProcessingMode = Literal["synthetic", "live"]


@dataclass(frozen=True, slots=True)
class ReviewProcessResult:
    review: ReviewResult
    processing_mode: ProcessingMode


@dataclass(frozen=True, slots=True)
class ReviewService:
    settings: Settings
    adapter: ExtractionAdapter | None
    attempt_gate: AttemptGate | None

    async def admit_source(self, source_identity: str) -> None:
        """Apply the public source throttle without creating a provider submission."""

        gate = self.attempt_gate
        if gate is None:
            return
        try:
            await gate.admit_source(source_identity)
        except AttemptRejected as error:
            raise self._attempt_error(error) from error

    async def process(
        self,
        *,
        expected: ExpectedReview,
        upload: UploadFile,
        correlation_id: str,
        idempotency_key: str,
        source_identity: str,
    ) -> ReviewProcessResult:
        started_at = perf_counter()
        try:
            async with prepare_uploaded_image(
                upload,
                temp_dir=self.settings.temp_dir,
            ) as prepared:
                return await self._process_prepared(
                    expected=expected,
                    prepared=prepared,
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                    source_identity=source_identity,
                    started_at=started_at,
                )
        except ImageIntakeError as error:
            status_code = 413 if error.kind == ImageIntakeErrorKind.UPLOAD_TOO_LARGE else 422
            raise ReviewApiError(
                ApplicationErrorCategory.INVALID_INPUT,
                error.safe_message,
                status_code=status_code,
            ) from error
        except ExtractionError as error:
            raise self._extraction_error(error) from error

    async def process_prepared(
        self,
        *,
        expected: ExpectedReview,
        prepared: PreparedImage,
        correlation_id: str,
        idempotency_key: str,
    ) -> ReviewProcessResult:
        """Process one already-validated internal case without public source admission."""

        try:
            return await self._process_prepared(
                expected=expected,
                prepared=prepared,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                source_identity=None,
                started_at=perf_counter(),
            )
        except ExtractionError as error:
            raise self._extraction_error(error) from error

    async def _process_prepared(
        self,
        *,
        expected: ExpectedReview,
        prepared: PreparedImage,
        correlation_id: str,
        idempotency_key: str,
        source_identity: str | None,
        started_at: float,
    ) -> ReviewProcessResult:
        adapter = self.adapter
        gate = self.attempt_gate
        if adapter is None or gate is None:
            raise ReviewApiError(
                ApplicationErrorCategory.LIVE_EXTRACTION_DISABLED,
                "Live label extraction is not available.",
            )
        processing_mode = self._processing_mode()
        submission_context = (
            gate.submission(
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                source_identity=source_identity,
            )
            if source_identity is not None
            else gate.internal_submission(
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        try:
            async with submission_context as submission:
                try:
                    async with asyncio.timeout(self.settings.extraction_timeout_seconds):
                        raw_observations = await self._extract_with_retries(
                            adapter,
                            prepared,
                            submission,
                            processing_mode,
                        )
                except TimeoutError as error:
                    await submission.fail(ExtractionErrorKind.TIMEOUT.value)
                    raise ReviewApiError(
                        ApplicationErrorCategory.PROVIDER_TIMEOUT,
                        "Label extraction timed out. Try again.",
                    ) from error
                except ExtractionError as error:
                    await submission.fail(error.kind.value)
                    raise

                try:
                    observations = ExtractionObservations.model_validate(raw_observations)
                except ValidationError as error:
                    await submission.fail(ExtractionErrorKind.MALFORMED_OUTPUT.value)
                    raise ReviewApiError(
                        ApplicationErrorCategory.MALFORMED_PROVIDER_OUTPUT,
                        "The extraction response could not be validated. Try again.",
                    ) from error

                duration_ms = max(0, int((perf_counter() - started_at) * 1000))
                review = compare_review(
                    expected,
                    observations,
                    processing_duration_ms=duration_ms,
                )
                status_counts = {
                    status: sum(check.status == status for check in review.checks)
                    for status in CheckStatus
                }
                await submission.complete(
                    outcome=review.outcome.value,
                    match_count=status_counts[CheckStatus.MATCH],
                    mismatch_count=status_counts[CheckStatus.MISMATCH],
                    needs_review_count=status_counts[CheckStatus.NEEDS_REVIEW],
                )
                return ReviewProcessResult(
                    review=review,
                    processing_mode=processing_mode,
                )
        except AttemptRejected as error:
            raise self._attempt_error(error) from error

    async def _extract_with_retries(
        self,
        adapter: ExtractionAdapter,
        prepared: PreparedImage,
        submission: AttemptSubmission,
        processing_mode: ProcessingMode,
    ) -> ExtractionObservations:
        retry_count = self.settings.openai_transient_retries if processing_mode == "live" else 0
        for attempt_number in range(1, retry_count + 2):
            async with submission.reserve_attempt() as reservation:
                try:
                    observations, success = await self._extract_once(adapter, prepared)
                except ExtractionError as error:
                    await reservation.settle_failure(error.kind.value)
                    if error.retryable and attempt_number <= retry_count:
                        await asyncio.sleep(0.25)
                        continue
                    raise
                await reservation.settle_success(success)
                return observations
        raise RuntimeError("unreachable retry state")

    @staticmethod
    async def _extract_once(
        adapter: ExtractionAdapter,
        prepared: PreparedImage,
    ) -> tuple[ExtractionObservations, AttemptSuccess | None]:
        if not isinstance(adapter, MeteredExtractionAdapter):
            return await adapter.extract(prepared), None

        result = cast(MeteredExtractionResult, await adapter.extract_with_metadata(prepared))
        usage = result.usage
        billed_service_tier = result.response_service_tier or result.requested_service_tier
        success = AttemptSuccess(
            provider_request_id=result.provider_request_id,
            model=result.model,
            image_detail=result.image_detail,
            requested_service_tier=result.requested_service_tier,
            response_service_tier=result.response_service_tier,
            latency_ms=result.latency_ms,
            input_tokens=usage.input_tokens if usage else None,
            cached_input_tokens=usage.cached_input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            reasoning_tokens=usage.reasoning_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            estimated_cost_usd=estimated_cost_usd(result.model, usage, billed_service_tier),
        )
        return result.observations, success

    def _processing_mode(self) -> ProcessingMode:
        if self.settings.extraction_backend == "fake":
            if self.settings.app_env == "production":
                raise ReviewApiError(
                    ApplicationErrorCategory.LIVE_EXTRACTION_DISABLED,
                    "Live label extraction is not available.",
                )
            return "synthetic"
        if not self.settings.live_extraction_enabled:
            raise ReviewApiError(
                ApplicationErrorCategory.LIVE_EXTRACTION_DISABLED,
                "Live label extraction is disabled.",
            )
        return "live"

    @staticmethod
    def _attempt_error(error: AttemptRejected) -> ReviewApiError:
        if error.kind == AttemptRejectionKind.TRAFFIC_THROTTLED:
            return ReviewApiError(
                ApplicationErrorCategory.TRAFFIC_THROTTLED,
                "Too many review requests were received. Try again shortly.",
            )
        if error.kind == AttemptRejectionKind.DUPLICATE_SUBMISSION:
            return ReviewApiError(
                ApplicationErrorCategory.DUPLICATE_SUBMISSION,
                "This review submission was already received. Start a new review to run it again.",
            )
        return ReviewApiError(
            ApplicationErrorCategory.CAPACITY_REACHED,
            "Live review capacity is temporarily unavailable.",
        )

    @staticmethod
    def _extraction_error(error: ExtractionError) -> ReviewApiError:
        if error.kind == ExtractionErrorKind.TIMEOUT:
            return ReviewApiError(
                ApplicationErrorCategory.PROVIDER_TIMEOUT,
                "Label extraction timed out. Try again.",
            )
        if error.kind == ExtractionErrorKind.MALFORMED_OUTPUT:
            return ReviewApiError(
                ApplicationErrorCategory.MALFORMED_PROVIDER_OUTPUT,
                "The extraction response could not be validated. Try again.",
            )
        if error.kind in {
            ExtractionErrorKind.TRANSIENT_FAILURE,
            ExtractionErrorKind.UNAVAILABLE,
        }:
            return ReviewApiError(
                ApplicationErrorCategory.PROVIDER_UNAVAILABLE,
                "Label extraction is temporarily unavailable. Try again.",
            )
        return ReviewApiError(
            ApplicationErrorCategory.INTERNAL_ERROR,
            "The review could not be completed. Try again.",
        )
