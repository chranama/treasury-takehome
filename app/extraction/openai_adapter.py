import base64
from dataclasses import dataclass
from time import perf_counter

from anyio import open_file
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
    NotFoundError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from openai.types.responses.response_usage import ResponseUsage
from pydantic import ValidationError

from app.comparison.models import ExtractionObservations
from app.extraction.contract import ExtractionError, ExtractionErrorKind, PreparedImage

PROMPT_REVISION = "label-observations-v2"
EXTRACTION_INSTRUCTIONS = """You extract visible observations from one alcohol label image for a
human reviewer.

Report only visual evidence. Do not decide compliance, compare against application data, approve,
reject, or infer text that is hidden, absent, or unreadable.

For brand name, class or type, alcohol content, and net contents:
- return every distinct plausible visible candidate verbatim;
- use an empty candidate list when no text is reliably readable;
- describe whether the field is visible and readable; and
- use uncertain states instead of selecting a guess.

Use not_visible only when image quality is sufficient to determine that the field or warning is
absent. When blur, low resolution, obstruction, cropping, or another image-quality problem prevents
you from determining whether it is present, report visibility as uncertain and readability as
unreadable or uncertain.

For the Government Warning:
- transcribe the complete visible warning exactly, preserving capitalization and punctuation;
- separately transcribe the heading;
- report whether the heading appears bold and whether the remaining warning body appears not bold;
- report visibility and readability; and
- leave text null when it cannot be read reliably.

Keep notes short and limited to visible evidence. Never provide numeric confidence."""


@dataclass(frozen=True, slots=True)
class OpenAIUsage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class OpenAIExtractionResult:
    observations: ExtractionObservations
    provider_request_id: str
    model: str
    prompt_revision: str
    image_detail: str
    requested_service_tier: str
    response_service_tier: str | None
    attempt_count: int
    latency_ms: int
    usage: OpenAIUsage | None


@dataclass(frozen=True, slots=True)
class OpenAIExtractionAdapter:
    client: AsyncOpenAI
    model: str
    image_detail: str = "high"
    service_tier: str = "default"
    max_output_tokens: int = 1_000
    timeout_seconds: float = 12.0

    def __post_init__(self) -> None:
        if self.image_detail not in {"high", "original"}:
            raise ValueError("image detail must be high or original")
        if self.service_tier not in {"default", "fast"}:
            raise ValueError("service tier must be default or fast")
        if not 256 <= self.max_output_tokens <= 2_000:
            raise ValueError("maximum output tokens must be between 256 and 2,000")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout must be positive")

    async def extract(self, image: PreparedImage) -> ExtractionObservations:
        result = await self.extract_with_metadata(image)
        return result.observations

    async def aclose(self) -> None:
        """Close the provider client owned by this adapter."""

        await self.client.close()

    async def extract_with_metadata(self, image: PreparedImage) -> OpenAIExtractionResult:
        image_url = await self._data_url(image)
        started_at = perf_counter()
        try:
            response = await self.client.responses.parse(
                model=self.model,
                instructions=EXTRACTION_INSTRUCTIONS,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Extract visible observations from this label image.",
                            },
                            {
                                "type": "input_image",
                                "image_url": image_url,
                                "detail": self.image_detail,
                            },
                        ],
                    }
                ],
                text_format=ExtractionObservations,
                tools=[],
                reasoning={"effort": "none"},
                service_tier=self.service_tier,
                max_output_tokens=self.max_output_tokens,
                store=False,
                timeout=self.timeout_seconds,
            )
        except Exception as error:
            raise self._map_error(error) from error

        parsed = response.output_parsed
        if response.status != "completed" or parsed is None:
            raise ExtractionError(
                kind=ExtractionErrorKind.MALFORMED_OUTPUT,
                safe_message="The extraction response could not be validated.",
                retryable=False,
            )

        try:
            observations = ExtractionObservations.model_validate(parsed)
        except ValidationError as error:
            raise ExtractionError(
                kind=ExtractionErrorKind.MALFORMED_OUTPUT,
                safe_message="The extraction response could not be validated.",
                retryable=False,
            ) from error

        latency_ms = max(0, int((perf_counter() - started_at) * 1_000))
        return OpenAIExtractionResult(
            observations=observations,
            provider_request_id=response.id,
            model=response.model,
            prompt_revision=PROMPT_REVISION,
            image_detail=self.image_detail,
            requested_service_tier=self.service_tier,
            response_service_tier=response.service_tier,
            attempt_count=1,
            latency_ms=latency_ms,
            usage=self._usage(response.usage),
        )

    async def _data_url(self, image: PreparedImage) -> str:
        try:
            async with await open_file(image.path, "rb") as input_file:
                image_bytes = await input_file.read()
        except OSError as error:
            raise ExtractionError(
                kind=ExtractionErrorKind.INTERNAL_FAILURE,
                safe_message="The prepared image could not be read.",
                retryable=False,
            ) from error

        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{image.media_type.value};base64,{encoded}"

    @staticmethod
    def _map_error(error: Exception) -> ExtractionError:
        if isinstance(error, APITimeoutError):
            return ExtractionError(
                kind=ExtractionErrorKind.TIMEOUT,
                safe_message="Label extraction timed out.",
                retryable=False,
            )
        if isinstance(
            error,
            (
                APIResponseValidationError,
                ContentFilterFinishReasonError,
                LengthFinishReasonError,
            ),
        ):
            return ExtractionError(
                kind=ExtractionErrorKind.MALFORMED_OUTPUT,
                safe_message="The extraction response could not be validated.",
                retryable=False,
            )
        if isinstance(error, RateLimitError):
            return ExtractionError(
                kind=ExtractionErrorKind.TRANSIENT_FAILURE,
                safe_message="Label extraction is temporarily unavailable.",
                retryable=False,
            )
        if isinstance(error, APIConnectionError) or (
            isinstance(error, APIStatusError) and error.status_code >= 500
        ):
            return ExtractionError(
                kind=ExtractionErrorKind.TRANSIENT_FAILURE,
                safe_message="Label extraction is temporarily unavailable.",
                retryable=True,
            )
        if isinstance(error, (AuthenticationError, PermissionDeniedError, NotFoundError)):
            return ExtractionError(
                kind=ExtractionErrorKind.UNAVAILABLE,
                safe_message="Label extraction is unavailable.",
                retryable=False,
            )
        if isinstance(error, (BadRequestError, UnprocessableEntityError, OpenAIError)):
            return ExtractionError(
                kind=ExtractionErrorKind.INTERNAL_FAILURE,
                safe_message="Label extraction failed unexpectedly.",
                retryable=False,
            )
        return ExtractionError(
            kind=ExtractionErrorKind.INTERNAL_FAILURE,
            safe_message="Label extraction failed unexpectedly.",
            retryable=False,
        )

    @staticmethod
    def _usage(usage: ResponseUsage | None) -> OpenAIUsage | None:
        if usage is None:
            return None
        input_details = usage.input_tokens_details
        output_details = usage.output_tokens_details
        return OpenAIUsage(
            input_tokens=usage.input_tokens,
            cached_input_tokens=input_details.cached_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=output_details.reasoning_tokens,
            total_tokens=usage.total_tokens,
        )
