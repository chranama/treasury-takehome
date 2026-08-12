import re
from collections.abc import Mapping

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.correlation import correlation_id_from_scope, elapsed_ms_from_scope

SINGLE_REVIEW_MULTIPART_OVERHEAD_BYTES = 256 * 1024
BATCH_PREFLIGHT_MULTIPART_OVERHEAD_BYTES = 512 * 1024
BATCH_CORRECTION_BODY_BYTES = 16 * 1024


class _BodyLimitExceeded(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Reject selected request bodies before multipart parsing can spool them."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        path_limits: Mapping[str, int],
        path_pattern_limits: Mapping[str, int] | None = None,
    ) -> None:
        self.app = app
        self.path_limits = dict(path_limits)
        self.path_pattern_limits = [
            (re.compile(pattern), limit) for pattern, limit in (path_pattern_limits or {}).items()
        ]
        limits = [
            *self.path_limits.values(),
            *(limit for _, limit in self.path_pattern_limits),
        ]
        if any(limit <= 0 for limit in limits):
            raise ValueError("request body limits must be positive")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {"PATCH", "POST", "PUT"}:
            await self.app(scope, receive, send)
            return

        limit = self.path_limits.get(scope.get("path", ""))
        if limit is None:
            path = scope.get("path", "")
            limit = next(
                (
                    candidate_limit
                    for pattern, candidate_limit in self.path_pattern_limits
                    if pattern.fullmatch(path)
                ),
                None,
            )
        if limit is None:
            await self.app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length is not None and content_length > limit:
            await _send_too_large(scope, receive, send)
            return

        consumed = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    raise _BodyLimitExceeded
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _BodyLimitExceeded:
            if response_started:
                raise
            await _send_too_large(scope, receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"content-length":
            try:
                parsed = int(value)
            except ValueError:
                return None
            return parsed if parsed >= 0 else None
    return None


async def _send_too_large(scope: Scope, receive: Receive, send: Send) -> None:
    correlation_id = correlation_id_from_scope(scope)
    path = scope.get("path", "")
    if path == "/api/batches/preflight" or path.endswith("/image"):
        code = "image_too_large" if path.endswith("/image") else "aggregate_upload_too_large"
        message = (
            "Choose an image no larger than 10 MB."
            if path.endswith("/image")
            else "The spreadsheet and images together must not exceed 100 MB."
        )
        content = {
            "issues": [
                {
                    "code": code,
                    "scope": "image" if path.endswith("/image") else "batch",
                    "row_number": None,
                    "field": None,
                    "severity": "error",
                    "message": message,
                }
            ],
            "correlation_id": correlation_id,
            "processing_duration_ms": elapsed_ms_from_scope(scope),
        }
    elif path.startswith("/api/batches"):
        content = {
            "code": "batch_request_too_large",
            "message": "The batch request exceeds the allowed size.",
            "correlation_id": correlation_id,
            "processing_duration_ms": elapsed_ms_from_scope(scope),
        }
    else:
        content = {
            "category": "invalid_input",
            "message": "The review request exceeds the allowed upload size.",
            "correlation_id": correlation_id,
            "processing_duration_ms": elapsed_ms_from_scope(scope),
        }
    response = JSONResponse(
        status_code=413,
        content=content,
        headers={"X-Correlation-ID": correlation_id},
    )
    await response(scope, receive, send)
