"""Single-process ownership for durable, idempotent batch processing."""

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from anyio import to_thread

from app.api.errors import ReviewApiError
from app.batches.contracts import (
    BatchCaseDetail,
    BatchCaseState,
    BatchCaseSummary,
    BatchErrorCode,
    BatchExpectedInput,
    BatchResponse,
    BatchStartSelection,
    BatchState,
    BatchStateCounts,
    PreflightIssue,
    StoredBatchCaseResult,
)
from app.batches.export import BatchExportCase, build_results_csv, completed_short_reason
from app.batches.limits import GRACEFUL_SHUTDOWN_DRAIN_SECONDS, POLL_INTERVAL_MILLISECONDS
from app.comparison import ExpectedReview, ReviewResult
from app.db import connect
from app.extraction import ImageMediaType, PreparedImage
from app.reviews import ReviewService


class BatchProcessingError(RuntimeError):
    """A bounded batch lifecycle conflict safe for the API boundary."""

    def __init__(self, code: BatchErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class _ClaimedCase:
    case_id: UUID
    expected: ExpectedReview
    image: PreparedImage
    storage_key: str


class BatchProcessingService:
    """Own active tasks while SQLite remains the browser-visible source of truth."""

    def __init__(
        self,
        *,
        database_path: Path,
        image_dir: Path,
        review_service: ReviewService,
        drain_seconds: float = GRACEFUL_SHUTDOWN_DRAIN_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if drain_seconds <= 0:
            raise ValueError("shutdown drain must be positive")
        self.database_path = database_path
        self.image_dir = image_dir
        self.review_service = review_service
        self.drain_seconds = drain_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._start_lock = asyncio.Lock()
        self._accepting_starts = False

    @property
    def active_task_count(self) -> int:
        return len(self._tasks)

    async def start(self) -> None:
        await self.reconcile_incomplete()
        self._accepting_starts = True

    async def aclose(self) -> None:
        self._accepting_starts = False
        tasks = tuple(self._tasks.values())
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self.drain_seconds)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        await self.reconcile_incomplete()

    async def admit_source(self, source_identity: str) -> None:
        await self.review_service.admit_source(source_identity)

    async def start_batch(
        self,
        *,
        batch_id: UUID,
        selection: BatchStartSelection,
        idempotency_key: str,
        source_identity: str,
    ) -> BatchResponse:
        digest = hashlib.sha256(f"{batch_id}:{idempotency_key}".encode()).hexdigest()
        async with self._start_lock:
            state = await to_thread.run_sync(self._inspect_start, batch_id, digest, self._now())
            if state == "missing":
                raise BatchProcessingError(BatchErrorCode.NOT_FOUND)
            if state == "same":
                response = await self.get_batch(batch_id)
                if response is None:
                    raise BatchProcessingError(BatchErrorCode.NOT_FOUND)
                return response
            if state == "conflict" or not self._accepting_starts:
                raise BatchProcessingError(BatchErrorCode.STATE_CONFLICT)

            await self.admit_source(source_identity)
            queued = await to_thread.run_sync(
                self._queue_batch,
                batch_id,
                selection,
                digest,
                self._now(),
            )
            if queued == "no_ready":
                raise BatchProcessingError(BatchErrorCode.NO_READY_CASES)
            if queued == "corrections":
                raise BatchProcessingError(BatchErrorCode.CORRECTIONS_REMAIN)
            if queued == "conflict":
                raise BatchProcessingError(BatchErrorCode.STATE_CONFLICT)

            task = asyncio.create_task(
                self._run_batch(batch_id),
                name=f"batch-review-{batch_id}",
            )
            self._tasks[batch_id] = task
            task.add_done_callback(lambda completed, key=batch_id: self._task_done(key, completed))

        response = await self.get_batch(batch_id)
        if response is None:
            raise BatchProcessingError(BatchErrorCode.NOT_FOUND)
        return response

    async def get_batch(self, batch_id: UUID) -> BatchResponse | None:
        return await to_thread.run_sync(self._get_batch, batch_id, self._now())

    async def get_case(self, batch_id: UUID, case_id: UUID) -> BatchCaseDetail | None:
        return await to_thread.run_sync(self._get_case, batch_id, case_id, self._now())

    async def get_results_csv(self, batch_id: UUID) -> bytes:
        status, content = await to_thread.run_sync(
            self._get_results_csv,
            batch_id,
            self._now(),
        )
        if status == "missing":
            raise BatchProcessingError(BatchErrorCode.NOT_FOUND)
        if status == "not_started":
            raise BatchProcessingError(BatchErrorCode.RESULTS_UNAVAILABLE)
        assert content is not None
        return content

    async def reconcile_incomplete(self) -> None:
        storage_keys = await to_thread.run_sync(self._reconcile_incomplete, self._now())
        for storage_key in storage_keys:
            await to_thread.run_sync(self._delete_storage_file, storage_key)

    def _task_done(self, batch_id: UUID, task: asyncio.Task[None]) -> None:
        self._tasks.pop(batch_id, None)
        if not task.cancelled():
            task.exception()

    async def _run_batch(self, batch_id: UUID) -> None:
        try:
            case_ids = await to_thread.run_sync(self._begin_processing, batch_id, self._now())
            queue: asyncio.Queue[UUID] = asyncio.Queue()
            for case_id in case_ids:
                queue.put_nowait(case_id)

            async def worker() -> None:
                while True:
                    try:
                        case_id = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        await self._process_case(batch_id, case_id)
                    finally:
                        queue.task_done()

            workers = [
                asyncio.create_task(worker(), name=f"batch-case-worker-{batch_id}-{index}")
                for index in range(min(2, len(case_ids)))
            ]
            if workers:
                await asyncio.gather(*workers)
            await to_thread.run_sync(self._complete_batch, batch_id, self._now())
        except asyncio.CancelledError:
            await to_thread.run_sync(self._interrupt_batch, batch_id, self._now())
            raise
        except BaseException:
            await to_thread.run_sync(self._interrupt_batch, batch_id, self._now())

    async def _process_case(self, batch_id: UUID, case_id: UUID) -> None:
        claimed = await to_thread.run_sync(self._claim_case, batch_id, case_id, self._now())
        if claimed is None:
            return
        correlation_id = str(case_id)
        try:
            processed = await self.review_service.process_prepared(
                expected=claimed.expected,
                prepared=claimed.image,
                correlation_id=correlation_id,
                idempotency_key=f"batch-case:{case_id}",
            )
            await to_thread.run_sync(
                self._complete_case,
                batch_id,
                case_id,
                correlation_id,
                processed.review,
                processed.processing_mode,
                self._now(),
            )
        except asyncio.CancelledError:
            await to_thread.run_sync(
                self._interrupt_case,
                batch_id,
                case_id,
                correlation_id,
                self._now(),
            )
            raise
        except ReviewApiError as error:
            await to_thread.run_sync(
                self._fail_case,
                batch_id,
                case_id,
                correlation_id,
                error.category.value,
                error.safe_message,
                self._now(),
            )
        except BaseException:
            await to_thread.run_sync(
                self._fail_case,
                batch_id,
                case_id,
                correlation_id,
                "internal_error",
                "The review could not be completed. Try again.",
                self._now(),
            )
        finally:
            deleted = await to_thread.run_sync(self._delete_storage_file, claimed.storage_key)
            if deleted:
                await to_thread.run_sync(
                    self._mark_image_deleted,
                    batch_id,
                    case_id,
                    self._now(),
                )

    def _inspect_start(self, batch_id: UUID, digest: str, now: datetime) -> str:
        with connect(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT status, start_idempotency_hash
                FROM batch_reviews
                WHERE batch_id = ? AND expires_at > ?
                """,
                (str(batch_id), _iso(now)),
            ).fetchone()
        if row is None:
            return "missing"
        if row[1] is not None:
            return "same" if row[1] == digest else "conflict"
        return "draft" if row[0] == BatchState.DRAFT.value else "conflict"

    def _queue_batch(
        self,
        batch_id: UUID,
        selection: BatchStartSelection,
        digest: str,
        now: datetime,
    ) -> str:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            batch = connection.execute(
                """
                SELECT status, expires_at, start_idempotency_hash
                FROM batch_reviews WHERE batch_id = ?
                """,
                (str(batch_id),),
            ).fetchone()
            if batch is None or batch[1] <= _iso(now) or batch[0] != BatchState.DRAFT.value:
                return "conflict"
            counts = dict(
                connection.execute(
                    "SELECT status, COUNT(*) FROM batch_cases WHERE batch_id = ? GROUP BY status",
                    (str(batch_id),),
                ).fetchall()
            )
            ready = counts.get(BatchCaseState.READY.value, 0)
            corrections = counts.get(BatchCaseState.NEEDS_CORRECTION.value, 0)
            if ready == 0:
                return "no_ready"
            if selection == BatchStartSelection.ALL_CASES and corrections:
                return "corrections"

            connection.execute(
                """
                UPDATE batch_reviews
                SET status = 'queued', updated_at = ?, start_idempotency_hash = ?,
                    start_selection = ?, started_at = ?, selected_case_count = ?
                WHERE batch_id = ? AND status = 'draft' AND start_idempotency_hash IS NULL
                """,
                (_iso(now), digest, selection.value, _iso(now), ready, str(batch_id)),
            )
            connection.execute(
                "UPDATE batch_cases SET status = 'queued', updated_at = ? "
                "WHERE batch_id = ? AND status = 'ready'",
                (_iso(now), str(batch_id)),
            )
            if selection == BatchStartSelection.READY_CASES_ONLY:
                connection.execute(
                    "UPDATE batch_cases SET status = 'not_selected', updated_at = ? "
                    "WHERE batch_id = ? AND status = 'needs_correction'",
                    (_iso(now), str(batch_id)),
                )
        return "queued"

    def _begin_processing(self, batch_id: UUID, now: datetime) -> list[UUID]:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE batch_reviews SET status = 'processing', updated_at = ? "
                "WHERE batch_id = ? AND status = 'queued'",
                (_iso(now), str(batch_id)),
            )
            rows = connection.execute(
                "SELECT case_id FROM batch_cases WHERE batch_id = ? AND status = 'queued' "
                "ORDER BY row_number",
                (str(batch_id),),
            ).fetchall()
        return [UUID(row[0]) for row in rows]

    def _claim_case(
        self,
        batch_id: UUID,
        case_id: UUID,
        now: datetime,
    ) -> _ClaimedCase | None:
        with connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT c.normalized_expected_json, i.storage_key, i.media_type,
                       i.width, i.height, i.byte_count
                FROM batch_cases AS c
                JOIN batch_reviews AS b ON b.batch_id = c.batch_id
                JOIN batch_images AS i ON i.image_id = c.image_id
                WHERE c.batch_id = ? AND c.case_id = ? AND c.status = 'queued'
                  AND b.status = 'processing' AND b.expires_at > ?
                  AND i.status = 'available'
                """,
                (str(batch_id), str(case_id), _iso(now)),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE batch_cases SET status = 'processing', updated_at = ? WHERE case_id = ?",
                (_iso(now), str(case_id)),
            )
            connection.execute(
                "UPDATE batch_images SET status = 'processing' WHERE storage_key = ?",
                (row["storage_key"],),
            )
        path = self._storage_path(row["storage_key"])
        if not path.is_file():
            self._fail_case(
                batch_id,
                case_id,
                str(case_id),
                "invalid_input",
                "The stored label image is unavailable.",
                now,
            )
            self._mark_image_deleted(batch_id, case_id, now)
            return None
        return _ClaimedCase(
            case_id=case_id,
            expected=ExpectedReview.model_validate_json(row["normalized_expected_json"]),
            image=PreparedImage(
                path=path,
                media_type=ImageMediaType(row["media_type"]),
                width=row["width"],
                height=row["height"],
                byte_count=row["byte_count"],
            ),
            storage_key=row["storage_key"],
        )

    def _complete_case(
        self,
        batch_id: UUID,
        case_id: UUID,
        correlation_id: str,
        review: ReviewResult,
        processing_mode: str,
        now: datetime,
    ) -> None:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            provider_id = _existing_correlation(connection, correlation_id)
            expires_at = connection.execute(
                "SELECT expires_at FROM batch_reviews WHERE batch_id = ?",
                (str(batch_id),),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO batch_case_results (
                    case_id, result_json, processing_mode, completed_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (str(case_id), review.model_dump_json(), processing_mode, _iso(now), expires_at),
            )
            connection.execute(
                """
                UPDATE batch_cases
                SET status = 'completed', provider_correlation_id = ?,
                    processing_duration_ms = ?, updated_at = ?
                WHERE batch_id = ? AND case_id = ? AND status = 'processing'
                """,
                (
                    provider_id,
                    review.processing_duration_ms,
                    _iso(now),
                    str(batch_id),
                    str(case_id),
                ),
            )

    def _fail_case(
        self,
        batch_id: UUID,
        case_id: UUID,
        correlation_id: str,
        kind: str,
        reason: str,
        now: datetime,
    ) -> None:
        with connect(self.database_path) as connection:
            provider_id = _existing_correlation(connection, correlation_id)
            connection.execute(
                """
                UPDATE batch_cases
                SET status = 'failed', provider_correlation_id = ?, safe_failure_kind = ?,
                    safe_failure_reason = ?, updated_at = ?
                WHERE batch_id = ? AND case_id = ? AND status IN ('queued', 'processing')
                """,
                (
                    provider_id,
                    kind[:100],
                    reason[:300],
                    _iso(now),
                    str(batch_id),
                    str(case_id),
                ),
            )

    def _interrupt_case(
        self,
        batch_id: UUID,
        case_id: UUID,
        correlation_id: str,
        now: datetime,
    ) -> None:
        with connect(self.database_path) as connection:
            provider_id = _existing_correlation(connection, correlation_id)
            connection.execute(
                """
                UPDATE batch_cases
                SET status = 'interrupted', provider_correlation_id = ?,
                    safe_failure_kind = 'interrupted',
                    safe_failure_reason = 'Processing was interrupted and was not replayed.',
                    updated_at = ?
                WHERE batch_id = ? AND case_id = ? AND status IN ('queued', 'processing')
                """,
                (provider_id, _iso(now), str(batch_id), str(case_id)),
            )

    def _complete_batch(self, batch_id: UUID, now: datetime) -> None:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT COUNT(*) FROM batch_cases WHERE batch_id = ? "
                "AND status IN ('queued', 'processing')",
                (str(batch_id),),
            ).fetchone()[0]
            if active == 0:
                connection.execute(
                    """
                    UPDATE batch_reviews
                    SET status = 'completed', updated_at = ?, completed_at = ?
                    WHERE batch_id = ? AND status = 'processing'
                    """,
                    (_iso(now), _iso(now), str(batch_id)),
                )

    def _interrupt_batch(self, batch_id: UUID, now: datetime) -> None:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE batch_cases
                SET status = 'interrupted', safe_failure_kind = 'interrupted',
                    safe_failure_reason = 'Processing was interrupted and was not replayed.',
                    updated_at = ?
                WHERE batch_id = ? AND status IN ('queued', 'processing')
                """,
                (_iso(now), str(batch_id)),
            )
            connection.execute(
                """
                UPDATE batch_reviews
                SET status = 'interrupted', updated_at = ?, completed_at = ?
                WHERE batch_id = ? AND status IN ('queued', 'processing')
                """,
                (_iso(now), _iso(now), str(batch_id)),
            )

    def _reconcile_incomplete(self, now: datetime) -> list[str]:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            storage_keys = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT i.storage_key
                    FROM batch_images AS i
                    JOIN batch_cases AS c ON c.image_id = i.image_id
                    JOIN batch_reviews AS b ON b.batch_id = c.batch_id
                    WHERE b.status IN ('queued', 'processing')
                      AND c.status = 'processing' AND i.status = 'processing'
                    """
                ).fetchall()
            ]
            connection.execute(
                """
                UPDATE batch_cases
                SET status = 'interrupted', safe_failure_kind = 'interrupted',
                    safe_failure_reason = 'Processing was interrupted and was not replayed.',
                    updated_at = ?
                WHERE status IN ('queued', 'processing')
                  AND batch_id IN (
                      SELECT batch_id FROM batch_reviews WHERE status IN ('queued', 'processing')
                  )
                """,
                (_iso(now),),
            )
            connection.execute(
                """
                UPDATE batch_images SET status = 'deleted', deleted_at = ?
                WHERE storage_key IN (
                    SELECT i.storage_key
                    FROM batch_images AS i
                    JOIN batch_cases AS c ON c.image_id = i.image_id
                    WHERE c.status = 'interrupted' AND i.status = 'processing'
                )
                """,
                (_iso(now),),
            )
            connection.execute(
                """
                UPDATE batch_reviews
                SET status = 'interrupted', updated_at = ?, completed_at = ?
                WHERE status IN ('queued', 'processing')
                """,
                (_iso(now), _iso(now)),
            )
        return storage_keys

    def _mark_image_deleted(self, batch_id: UUID, case_id: UUID, now: datetime) -> None:
        with connect(self.database_path) as connection:
            connection.execute(
                """
                UPDATE batch_images SET status = 'deleted', deleted_at = ?
                WHERE image_id = (
                    SELECT image_id FROM batch_cases WHERE batch_id = ? AND case_id = ?
                ) AND status = 'processing'
                """,
                (_iso(now), str(batch_id), str(case_id)),
            )

    def _get_batch(self, batch_id: UUID, now: datetime) -> BatchResponse | None:
        with connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            batch = connection.execute(
                """
                SELECT batch_id, status, created_at, expires_at
                FROM batch_reviews WHERE batch_id = ? AND expires_at > ?
                """,
                (str(batch_id), _iso(now)),
            ).fetchone()
            if batch is None:
                return None
            rows = connection.execute(
                """
                SELECT c.*, r.result_json
                FROM batch_cases AS c
                LEFT JOIN batch_case_results AS r ON r.case_id = c.case_id
                WHERE c.batch_id = ? ORDER BY c.row_number
                """,
                (str(batch_id),),
            ).fetchall()
        summaries = [_case_summary(row) for row in rows]
        state = BatchState(batch["status"])
        return BatchResponse(
            batch_id=UUID(batch["batch_id"]),
            state=state,
            created_at=_datetime(batch["created_at"]),
            expires_at=_datetime(batch["expires_at"]),
            counts=_state_counts(summaries),
            cases=summaries,
            next_poll_after_ms=(
                POLL_INTERVAL_MILLISECONDS
                if state in {BatchState.QUEUED, BatchState.PROCESSING}
                else None
            ),
        )

    def _get_case(
        self,
        batch_id: UUID,
        case_id: UUID,
        now: datetime,
    ) -> BatchCaseDetail | None:
        with connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT c.*, r.result_json, r.processing_mode, r.completed_at
                FROM batch_cases AS c
                JOIN batch_reviews AS b ON b.batch_id = c.batch_id
                LEFT JOIN batch_case_results AS r ON r.case_id = c.case_id
                WHERE c.batch_id = ? AND c.case_id = ? AND b.expires_at > ?
                """,
                (str(batch_id), str(case_id), _iso(now)),
            ).fetchone()
        if row is None:
            return None
        result = None
        if row["result_json"] is not None:
            result = StoredBatchCaseResult(
                result=ReviewResult.model_validate_json(row["result_json"]),
                processing_mode=row["processing_mode"],
                correlation_id=UUID(row["provider_correlation_id"] or row["case_id"]),
                completed_at=_datetime(row["completed_at"]),
                expires_at=_datetime(row["expires_at"]),
            )
        return BatchCaseDetail(
            summary=_case_summary(row),
            expected_input=BatchExpectedInput(
                brand_name=row["expected_brand"],
                class_type=row["expected_class_type"],
                expected_abv=row["expected_abv"],
                expected_net_contents=row["expected_net_contents"],
            ),
            normalized_expected=(
                ExpectedReview.model_validate_json(row["normalized_expected_json"])
                if row["normalized_expected_json"] is not None
                else None
            ),
            result=result,
        )

    def _get_results_csv(
        self,
        batch_id: UUID,
        now: datetime,
    ) -> tuple[str, bytes | None]:
        with connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            batch = connection.execute(
                """
                SELECT start_idempotency_hash
                FROM batch_reviews WHERE batch_id = ? AND expires_at > ?
                """,
                (str(batch_id), _iso(now)),
            ).fetchone()
            if batch is None:
                return "missing", None
            if batch["start_idempotency_hash"] is None:
                return "not_started", None
            rows = connection.execute(
                """
                SELECT c.*, r.result_json
                FROM batch_cases AS c
                LEFT JOIN batch_case_results AS r ON r.case_id = c.case_id
                WHERE c.batch_id = ?
                  AND c.status NOT IN ('needs_correction', 'ready', 'not_selected')
                ORDER BY c.row_number
                """,
                (str(batch_id),),
            ).fetchall()

        cases: list[BatchExportCase] = []
        for row in rows:
            result = (
                ReviewResult.model_validate_json(row["result_json"])
                if row["result_json"] is not None
                else None
            )
            cases.append(
                BatchExportCase(
                    application_id=row["application_id"],
                    state=BatchCaseState(row["status"]),
                    expected_input=BatchExpectedInput(
                        brand_name=row["expected_brand"],
                        class_type=row["expected_class_type"],
                        expected_abv=row["expected_abv"],
                        expected_net_contents=row["expected_net_contents"],
                    ),
                    processing_duration_ms=row["processing_duration_ms"],
                    result=result,
                    short_reason=row["safe_failure_reason"],
                )
            )
        return "ready", build_results_csv(cases)

    def _delete_storage_file(self, storage_key: str) -> bool:
        try:
            self._storage_path(storage_key).unlink(missing_ok=True)
        except OSError:
            return False
        return True

    def _storage_path(self, storage_key: str) -> Path:
        if (
            len(storage_key) != 36
            or not storage_key.endswith(".png")
            or any(character not in "0123456789abcdef" for character in storage_key[:-4])
        ):
            raise RuntimeError("invalid internal batch image storage key")
        return self.image_dir / storage_key

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("batch processing clocks must return timezone-aware datetimes")
        return now.astimezone(UTC)


def _existing_correlation(connection: sqlite3.Connection, correlation_id: str) -> str | None:
    row = connection.execute(
        "SELECT correlation_id FROM review_submissions WHERE correlation_id = ?",
        (correlation_id,),
    ).fetchone()
    return row[0] if row is not None else None


def _case_summary(row: sqlite3.Row) -> BatchCaseSummary:
    review = (
        ReviewResult.model_validate_json(row["result_json"])
        if row["result_json"] is not None
        else None
    )
    return BatchCaseSummary(
        case_id=UUID(row["case_id"]),
        row_number=row["row_number"],
        application_id=row["application_id"],
        label_image_filename=row["label_image_filename"],
        state=BatchCaseState(row["status"]),
        issues=[PreflightIssue.model_validate(value) for value in json.loads(row["issues_json"])],
        outcome=review.outcome if review is not None else None,
        processing_duration_ms=row["processing_duration_ms"],
        short_reason=(
            row["safe_failure_reason"]
            or (completed_short_reason(review) if review is not None else None)
        ),
    )


def _state_counts(cases: list[BatchCaseSummary]) -> BatchStateCounts:
    values = {state.value: 0 for state in BatchCaseState}
    for case in cases:
        values[case.state.value] += 1
    return BatchStateCounts(total=len(cases), **values)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError("stored batch timestamps must be timezone-aware")
    return parsed.astimezone(UTC)
