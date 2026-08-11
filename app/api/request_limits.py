from collections.abc import Mapping

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.correlation import correlation_id_from_scope, elapsed_ms_from_scope

SINGLE_REVIEW_MULTIPART_OVERHEAD_BYTES = 256 * 1024


class _BodyLimitExceeded(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Reject selected request bodies before multipart parsing can spool them."""

    def __init__(self, app: ASGIApp, *, path_limits: Mapping[str, int]) -> None:
        self.app = app
        self.path_limits = dict(path_limits)
        if any(limit <= 0 for limit in self.path_limits.values()):
            raise ValueError("request body limits must be positive")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        limit = self.path_limits.get(scope.get("path", ""))
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
    response = JSONResponse(
        status_code=413,
        content={
            "category": "invalid_input",
            "message": "The review request exceeds the allowed upload size.",
            "correlation_id": correlation_id,
            "processing_duration_ms": elapsed_ms_from_scope(scope),
        },
        headers={"X-Correlation-ID": correlation_id},
    )
    await response(scope, receive, send)
