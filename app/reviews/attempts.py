import asyncio
import hashlib
import secrets
from collections import defaultdict, deque
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Protocol

from anyio import to_thread

from app.db import connect
from app.extraction.pricing import COST_QUANTUM_USD


class AttemptRejectionKind(StrEnum):
    CAPACITY_REACHED = "capacity_reached"
    TRAFFIC_THROTTLED = "traffic_throttled"
    DUPLICATE_SUBMISSION = "duplicate_submission"


class AttemptRejected(RuntimeError):
    def __init__(self, kind: AttemptRejectionKind) -> None:
        self.kind = kind
        super().__init__(kind.value)


@dataclass(frozen=True, slots=True)
class AttemptSuccess:
    provider_request_id: str | None
    model: str
    image_detail: str
    requested_service_tier: str
    response_service_tier: str | None
    latency_ms: int
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: Decimal | None = None


class AttemptReservation(Protocol):
    async def settle_success(self, success: AttemptSuccess | None = None) -> None: ...

    async def settle_failure(self, error_kind: str) -> None: ...


class AttemptSubmission(Protocol):
    def reserve_attempt(self) -> AbstractAsyncContextManager[AttemptReservation]: ...

    async def complete(
        self,
        *,
        outcome: str,
        match_count: int,
        mismatch_count: int,
        needs_review_count: int,
    ) -> None: ...

    async def fail(self, error_kind: str) -> None: ...


class AttemptGate(Protocol):
    async def admit_source(self, source_identity: str) -> None: ...

    def submission(
        self,
        *,
        correlation_id: str,
        idempotency_key: str,
        source_identity: str,
    ) -> AbstractAsyncContextManager[AttemptSubmission]: ...

    def internal_submission(
        self,
        *,
        correlation_id: str,
        idempotency_key: str,
    ) -> AbstractAsyncContextManager[AttemptSubmission]: ...


class _NoCostReservation:
    async def settle_success(self, success: AttemptSuccess | None = None) -> None:
        del success

    async def settle_failure(self, error_kind: str) -> None:
        del error_kind


class _NoCostSubmission:
    @asynccontextmanager
    async def reserve_attempt(self) -> AsyncGenerator[AttemptReservation, None]:
        yield _NoCostReservation()

    async def complete(
        self,
        *,
        outcome: str,
        match_count: int,
        mismatch_count: int,
        needs_review_count: int,
    ) -> None:
        del outcome, match_count, mismatch_count, needs_review_count

    async def fail(self, error_kind: str) -> None:
        del error_kind


class NoCostFakeAttemptGate:
    """Non-durable permit used only where extraction makes no provider request."""

    def __init__(self, *, max_concurrency: int = 2) -> None:
        if max_concurrency != 2:
            raise ValueError("the prototype requires global extraction concurrency of two")
        self.max_concurrency = max_concurrency
        self._active_submissions = 0
        self._slot_condition = asyncio.Condition()

    async def admit_source(self, source_identity: str) -> None:
        del source_identity

    @asynccontextmanager
    async def submission(
        self,
        *,
        correlation_id: str,
        idempotency_key: str,
        source_identity: str,
    ) -> AsyncGenerator[AttemptSubmission, None]:
        del correlation_id, idempotency_key
        await self.admit_source(source_identity)
        async with self._slot(wait=False):
            yield _NoCostSubmission()

    @asynccontextmanager
    async def internal_submission(
        self,
        *,
        correlation_id: str,
        idempotency_key: str,
    ) -> AsyncGenerator[AttemptSubmission, None]:
        del correlation_id, idempotency_key
        async with self._slot(wait=True):
            yield _NoCostSubmission()

    @asynccontextmanager
    async def _slot(self, *, wait: bool) -> AsyncGenerator[None, None]:
        async with self._slot_condition:
            if not wait and self._active_submissions >= self.max_concurrency:
                raise AttemptRejected(AttemptRejectionKind.CAPACITY_REACHED)
            while self._active_submissions >= self.max_concurrency:
                await self._slot_condition.wait()
            self._active_submissions += 1
        try:
            yield
        finally:
            async with self._slot_condition:
                self._active_submissions = max(0, self._active_submissions - 1)
                self._slot_condition.notify(1)


def cost_to_units(cost_usd: Decimal) -> int:
    if cost_usd < 0:
        raise ValueError("cost must not be negative")
    return int((cost_usd / COST_QUANTUM_USD).to_integral_value(rounding=ROUND_CEILING))


class _SQLiteAttemptReservation:
    def __init__(self, gate: "SQLiteUsageGate", attempt_id: int) -> None:
        self._gate = gate
        self._attempt_id = attempt_id
        self._settled = False

    async def settle_success(self, success: AttemptSuccess | None = None) -> None:
        if self._settled:
            raise RuntimeError("attempt reservation has already been settled")
        await self._gate._settle_success(self._attempt_id, success)
        self._settled = True

    async def settle_failure(self, error_kind: str) -> None:
        if self._settled:
            raise RuntimeError("attempt reservation has already been settled")
        await self._gate._settle_failure(self._attempt_id, error_kind)
        self._settled = True


class _SQLiteSubmission:
    def __init__(self, gate: "SQLiteUsageGate", correlation_id: str) -> None:
        self._gate = gate
        self._correlation_id = correlation_id
        self._terminal = False

    @asynccontextmanager
    async def reserve_attempt(self) -> AsyncGenerator[AttemptReservation, None]:
        attempt_id = await self._gate._reserve_attempt(self._correlation_id)
        reservation = _SQLiteAttemptReservation(self._gate, attempt_id)
        completed_normally = False
        try:
            yield reservation
            completed_normally = True
        except BaseException:
            if not reservation._settled:
                await reservation.settle_failure("interrupted")
            raise
        finally:
            if not reservation._settled:
                await reservation.settle_failure(
                    "internal_failure" if completed_normally else "interrupted"
                )

    async def complete(
        self,
        *,
        outcome: str,
        match_count: int,
        mismatch_count: int,
        needs_review_count: int,
    ) -> None:
        if self._terminal:
            raise RuntimeError("submission has already reached a terminal state")
        await self._gate._finish_submission(
            self._correlation_id,
            status="completed",
            outcome=outcome,
            match_count=match_count,
            mismatch_count=mismatch_count,
            needs_review_count=needs_review_count,
            error_kind=None,
        )
        self._terminal = True

    async def fail(self, error_kind: str) -> None:
        if self._terminal:
            return
        await self._gate._finish_submission(
            self._correlation_id,
            status="failed",
            outcome=None,
            match_count=None,
            mismatch_count=None,
            needs_review_count=None,
            error_kind=error_kind,
        )
        self._terminal = True


class SQLiteUsageGate:
    """One-process concurrency guard backed by a durable SQLite budget ledger."""

    def __init__(
        self,
        *,
        database_path: Path,
        daily_attempt_limit: int,
        cumulative_cost_limit_usd: Decimal,
        attempt_reservation_usd: Decimal,
        source_window_seconds: int,
        source_max_submissions: int,
        model: str,
        prompt_revision: str,
        image_detail: str,
        service_tier: str,
        max_concurrency: int = 2,
        max_attempts_per_submission: int = 2,
    ) -> None:
        if daily_attempt_limit <= 0:
            raise ValueError("daily attempt limit must be positive")
        if source_window_seconds <= 0 or source_max_submissions <= 0:
            raise ValueError("source throttle limits must be positive")
        if max_concurrency != 2:
            raise ValueError("the prototype requires global extraction concurrency of two")
        if max_attempts_per_submission not in {1, 2}:
            raise ValueError("a submission may have one or two provider attempts")
        self.database_path = database_path
        self.daily_attempt_limit = daily_attempt_limit
        self.cumulative_cost_limit_units = cost_to_units(cumulative_cost_limit_usd)
        self.attempt_reservation_units = cost_to_units(attempt_reservation_usd)
        if self.cumulative_cost_limit_units <= 0 or self.attempt_reservation_units <= 0:
            raise ValueError("cost limits must be positive")
        self.source_window_seconds = source_window_seconds
        self.source_max_submissions = source_max_submissions
        self.model = model
        self.prompt_revision = prompt_revision
        self.image_detail = image_detail
        self.service_tier = service_tier
        self.max_concurrency = max_concurrency
        self.max_attempts_per_submission = max_attempts_per_submission
        self._state_lock = asyncio.Lock()
        self._slot_condition = asyncio.Condition(self._state_lock)
        self._active_submissions = 0
        self._source_secret = secrets.token_bytes(32)
        self._source_events: dict[bytes, deque[float]] = defaultdict(deque)

    @asynccontextmanager
    async def submission(
        self,
        *,
        correlation_id: str,
        idempotency_key: str,
        source_identity: str,
    ) -> AsyncGenerator[AttemptSubmission, None]:
        await self.admit_source(source_identity)
        async with self._processing_submission(
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            wait_for_slot=False,
        ) as submission:
            yield submission

    @asynccontextmanager
    async def internal_submission(
        self,
        *,
        correlation_id: str,
        idempotency_key: str,
    ) -> AsyncGenerator[AttemptSubmission, None]:
        async with self._processing_submission(
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            wait_for_slot=True,
        ) as submission:
            yield submission

    @asynccontextmanager
    async def _processing_submission(
        self,
        *,
        correlation_id: str,
        idempotency_key: str,
        wait_for_slot: bool,
    ) -> AsyncGenerator[AttemptSubmission, None]:
        await self._acquire_slot(wait=wait_for_slot)
        submission: _SQLiteSubmission | None = None
        completed_normally = False
        try:
            idempotency_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
            await to_thread.run_sync(
                self._start_submission,
                correlation_id,
                idempotency_hash,
            )
            submission = _SQLiteSubmission(self, correlation_id)
            try:
                yield submission
                completed_normally = True
            except AttemptRejected as error:
                await submission.fail(error.kind.value)
                raise
            except BaseException:
                await submission.fail("interrupted")
                raise
            finally:
                if not submission._terminal:
                    await submission.fail(
                        "internal_failure" if completed_normally else "interrupted"
                    )
        finally:
            await self._release_slot()

    async def admit_source(self, source_identity: str) -> None:
        await self._check_source_throttle(source_identity)

    async def reconcile_incomplete(self) -> None:
        await to_thread.run_sync(self._reconcile_incomplete)

    async def _check_source_throttle(self, source_identity: str) -> None:
        digest = hashlib.sha256(
            self._source_secret + source_identity.encode("utf-8", errors="replace")
        ).digest()
        now = monotonic()
        cutoff = now - self.source_window_seconds
        async with self._state_lock:
            events = self._source_events[digest]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= self.source_max_submissions:
                raise AttemptRejected(AttemptRejectionKind.TRAFFIC_THROTTLED)
            events.append(now)

    async def _acquire_slot(self, *, wait: bool) -> None:
        async with self._slot_condition:
            if not wait and self._active_submissions >= self.max_concurrency:
                raise AttemptRejected(AttemptRejectionKind.CAPACITY_REACHED)
            while self._active_submissions >= self.max_concurrency:
                await self._slot_condition.wait()
            self._active_submissions += 1

    async def _release_slot(self) -> None:
        async with self._slot_condition:
            self._active_submissions = max(0, self._active_submissions - 1)
            self._slot_condition.notify(1)

    def _start_submission(self, correlation_id: str, idempotency_hash: str) -> None:
        created_at = datetime.now(UTC).isoformat()
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT 1 FROM review_submissions WHERE idempotency_hash = ?",
                (idempotency_hash,),
            ).fetchone()
            if existing is not None:
                raise AttemptRejected(AttemptRejectionKind.DUPLICATE_SUBMISSION)
            connection.execute(
                """
                INSERT INTO review_submissions (
                    idempotency_hash, correlation_id, status, created_at
                ) VALUES (?, ?, 'processing', ?)
                """,
                (idempotency_hash, correlation_id, created_at),
            )

    async def _reserve_attempt(self, correlation_id: str) -> int:
        return await to_thread.run_sync(self._reserve_attempt_sync, correlation_id)

    def _reserve_attempt_sync(self, correlation_id: str) -> int:
        reserved_at = datetime.now(UTC)
        day_start = reserved_at.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            submission = connection.execute(
                "SELECT status FROM review_submissions WHERE correlation_id = ?",
                (correlation_id,),
            ).fetchone()
            if submission != ("processing",):
                raise RuntimeError("provider attempt requires a processing submission")

            attempt_count = connection.execute(
                "SELECT COUNT(*) FROM provider_attempts WHERE correlation_id = ?",
                (correlation_id,),
            ).fetchone()[0]
            if attempt_count >= self.max_attempts_per_submission:
                raise AttemptRejected(AttemptRejectionKind.CAPACITY_REACHED)

            daily_count = connection.execute(
                "SELECT COUNT(*) FROM provider_attempts WHERE reserved_at >= ?",
                (day_start,),
            ).fetchone()[0]
            accounted_cost = connection.execute(
                """
                SELECT COALESCE(SUM(COALESCE(actual_cost_units, reserved_cost_units)), 0)
                FROM provider_attempts
                """
            ).fetchone()[0]
            if daily_count >= self.daily_attempt_limit or (
                accounted_cost + self.attempt_reservation_units > self.cumulative_cost_limit_units
            ):
                raise AttemptRejected(AttemptRejectionKind.CAPACITY_REACHED)

            cursor = connection.execute(
                """
                INSERT INTO provider_attempts (
                    correlation_id, attempt_number, status, reserved_at,
                    reserved_cost_units, model, prompt_revision, image_detail,
                    requested_service_tier
                ) VALUES (?, ?, 'reserved', ?, ?, ?, ?, ?, ?)
                """,
                (
                    correlation_id,
                    attempt_count + 1,
                    reserved_at.isoformat(),
                    self.attempt_reservation_units,
                    self.model,
                    self.prompt_revision,
                    self.image_detail,
                    self.service_tier,
                ),
            )
            return int(cursor.lastrowid)

    async def _settle_success(
        self,
        attempt_id: int,
        success: AttemptSuccess | None,
    ) -> None:
        await to_thread.run_sync(self._settle_success_sync, attempt_id, success)

    def _settle_success_sync(
        self,
        attempt_id: int,
        success: AttemptSuccess | None,
    ) -> None:
        actual_cost_units = (
            cost_to_units(success.estimated_cost_usd)
            if success is not None and success.estimated_cost_usd is not None
            else None
        )
        values = (
            actual_cost_units,
            success.provider_request_id if success else None,
            success.model if success else self.model,
            success.image_detail if success else self.image_detail,
            success.requested_service_tier if success else self.service_tier,
            success.response_service_tier if success else None,
            success.latency_ms if success else None,
            success.input_tokens if success else None,
            success.cached_input_tokens if success else None,
            success.output_tokens if success else None,
            success.reasoning_tokens if success else None,
            success.total_tokens if success else None,
            datetime.now(UTC).isoformat(),
            attempt_id,
        )
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE provider_attempts
                SET status = 'succeeded', actual_cost_units = ?, provider_request_id = ?,
                    model = ?, image_detail = ?, requested_service_tier = ?,
                    response_service_tier = ?, latency_ms = ?, input_tokens = ?,
                    cached_input_tokens = ?, output_tokens = ?, reasoning_tokens = ?,
                    total_tokens = ?, settled_at = ?
                WHERE id = ? AND status = 'reserved'
                """,
                values,
            )
            if cursor.rowcount != 1:
                raise RuntimeError("attempt reservation could not be settled")

    async def _settle_failure(self, attempt_id: int, error_kind: str) -> None:
        await to_thread.run_sync(self._settle_failure_sync, attempt_id, error_kind)

    def _settle_failure_sync(self, attempt_id: int, error_kind: str) -> None:
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE provider_attempts
                SET status = 'failed', settled_at = ?, error_kind = ?
                WHERE id = ? AND status = 'reserved'
                """,
                (datetime.now(UTC).isoformat(), error_kind[:100], attempt_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("attempt reservation could not be settled")

    async def _finish_submission(
        self,
        correlation_id: str,
        *,
        status: str,
        outcome: str | None,
        match_count: int | None,
        mismatch_count: int | None,
        needs_review_count: int | None,
        error_kind: str | None,
    ) -> None:
        await to_thread.run_sync(
            self._finish_submission_sync,
            correlation_id,
            status,
            outcome,
            match_count,
            mismatch_count,
            needs_review_count,
            error_kind,
        )

    def _finish_submission_sync(
        self,
        correlation_id: str,
        status: str,
        outcome: str | None,
        match_count: int | None,
        mismatch_count: int | None,
        needs_review_count: int | None,
        error_kind: str | None,
    ) -> None:
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE review_submissions
                SET status = ?, completed_at = ?, outcome = ?, match_count = ?,
                    mismatch_count = ?, needs_review_count = ?, error_kind = ?
                WHERE correlation_id = ? AND status = 'processing'
                """,
                (
                    status,
                    datetime.now(UTC).isoformat(),
                    outcome,
                    match_count,
                    mismatch_count,
                    needs_review_count,
                    error_kind[:100] if error_kind else None,
                    correlation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("submission could not be completed")

    def _reconcile_incomplete(self) -> None:
        settled_at = datetime.now(UTC).isoformat()
        with connect(self.database_path) as connection:
            connection.execute(
                """
                UPDATE provider_attempts
                SET status = 'failed', settled_at = ?, error_kind = 'interrupted'
                WHERE status = 'reserved'
                """,
                (settled_at,),
            )
            connection.execute(
                """
                UPDATE review_submissions
                SET status = 'failed', completed_at = ?, error_kind = 'interrupted'
                WHERE status = 'processing'
                """,
                (settled_at,),
            )
