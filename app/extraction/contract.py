from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.comparison.models import ExtractionObservations


class ImageMediaType(StrEnum):
    JPEG = "image/jpeg"
    PNG = "image/png"
    WEBP = "image/webp"


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """Validated, metadata-stripped image produced by the image-intake boundary."""

    path: Path
    media_type: ImageMediaType
    width: int
    height: int
    byte_count: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("prepared image dimensions must be positive")
        if self.byte_count <= 0:
            raise ValueError("prepared image byte count must be positive")


class ExtractionErrorKind(StrEnum):
    TIMEOUT = "timeout"
    MALFORMED_OUTPUT = "malformed_output"
    TRANSIENT_FAILURE = "transient_failure"
    UNAVAILABLE = "unavailable"
    INTERNAL_FAILURE = "internal_failure"


class ExtractionError(RuntimeError):
    """Safe, provider-neutral failure exposed by every extraction adapter."""

    def __init__(
        self,
        *,
        kind: ExtractionErrorKind,
        safe_message: str,
        retryable: bool,
    ) -> None:
        message = safe_message.strip()
        if not message:
            raise ValueError("an extraction error requires a safe message")
        if len(message) > 300:
            raise ValueError("an extraction error message cannot exceed 300 characters")
        self.kind = kind
        self.safe_message = message
        self.retryable = retryable
        super().__init__(message)


@runtime_checkable
class ExtractionAdapter(Protocol):
    """Extract visible label observations without access to expected values."""

    async def extract(self, image: PreparedImage) -> ExtractionObservations: ...
