from time import perf_counter
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORRELATION_HEADER = b"x-correlation-id"


class CorrelationIdMiddleware:
    """Attach a server-generated correlation ID and request timer to every response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        correlation_id = str(uuid4())
        state["correlation_id"] = correlation_id
        state["request_started_at"] = perf_counter()

        async def send_with_correlation(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if not any(name.lower() == CORRELATION_HEADER for name, _ in headers):
                    headers.append((CORRELATION_HEADER, correlation_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_correlation)


def correlation_id_from_scope(scope: Scope) -> str:
    state = scope.get("state", {})
    correlation_id = state.get("correlation_id")
    return correlation_id if isinstance(correlation_id, str) else str(uuid4())


def elapsed_ms_from_scope(scope: Scope) -> int:
    state = scope.get("state", {})
    started_at = state.get("request_started_at")
    if not isinstance(started_at, float):
        return 0
    return max(0, int((perf_counter() - started_at) * 1000))
