from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from enum import StrEnum
from typing import Protocol


class AttemptRejectionKind(StrEnum):
    CAPACITY_REACHED = "capacity_reached"
    TRAFFIC_THROTTLED = "traffic_throttled"


class AttemptRejected(RuntimeError):
    def __init__(self, kind: AttemptRejectionKind) -> None:
        self.kind = kind
        super().__init__(kind.value)


class AttemptGate(Protocol):
    def reserve(self, correlation_id: str) -> AbstractAsyncContextManager[None]: ...


class NoCostFakeAttemptGate:
    """Non-durable permit used only where extraction makes no provider request."""

    @asynccontextmanager
    async def reserve(self, correlation_id: str) -> AsyncGenerator[None, None]:
        del correlation_id
        yield
