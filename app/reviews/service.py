import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from fastapi import UploadFile
from pydantic import ValidationError

from app.api.errors import ReviewApiError
from app.comparison import (
    ApplicationErrorCategory,
    ExpectedReview,
    ExtractionObservations,
    ReviewResult,
    compare_review,
)
from app.config import Settings
from app.extraction import ExtractionAdapter, ExtractionError, ExtractionErrorKind
from app.reviews.attempts import AttemptGate, AttemptRejected, AttemptRejectionKind
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

    async def process(
        self,
        *,
        expected: ExpectedReview,
        upload: UploadFile,
        correlation_id: str,
    ) -> ReviewProcessResult:
        started_at = perf_counter()
        try:
            async with prepare_uploaded_image(
                upload,
                temp_dir=self.settings.temp_dir,
            ) as prepared:
                adapter = self.adapter
                if adapter is None:
                    raise ReviewApiError(
                        ApplicationErrorCategory.LIVE_EXTRACTION_DISABLED,
                        "Live label extraction is not available.",
                    )
                processing_mode = self._processing_mode()
                if self.attempt_gate is None:
                    raise ReviewApiError(
                        ApplicationErrorCategory.LIVE_EXTRACTION_DISABLED,
                        "Live label extraction is not available.",
                    )

                try:
                    async with self.attempt_gate.reserve(correlation_id):
                        try:
                            async with asyncio.timeout(self.settings.extraction_timeout_seconds):
                                raw_observations = await adapter.extract(prepared)
                        except TimeoutError as error:
                            raise ReviewApiError(
                                ApplicationErrorCategory.PROVIDER_TIMEOUT,
                                "Label extraction timed out. Try again.",
                            ) from error
                except AttemptRejected as error:
                    raise self._attempt_error(error) from error

                try:
                    observations = ExtractionObservations.model_validate(raw_observations)
                except ValidationError as error:
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
                return ReviewProcessResult(review=review, processing_mode=processing_mode)
        except ImageIntakeError as error:
            status_code = 413 if error.kind == ImageIntakeErrorKind.UPLOAD_TOO_LARGE else 422
            raise ReviewApiError(
                ApplicationErrorCategory.INVALID_INPUT,
                error.safe_message,
                status_code=status_code,
            ) from error
        except ExtractionError as error:
            raise self._extraction_error(error) from error

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
