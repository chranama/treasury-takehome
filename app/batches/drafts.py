"""Durable, short-lived batch drafts with protected image storage."""

import asyncio
import json
import shutil
import sqlite3
import unicodedata
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from anyio import to_thread
from fastapi import UploadFile

from app.batches.contracts import (
    BatchCaseDetail,
    BatchCasePatchRequest,
    BatchCaseState,
    BatchCaseSummary,
    BatchExpectedInput,
    BatchField,
    BatchPreflightResponse,
    BatchState,
    BatchStateCounts,
    PreflightIssue,
    PreflightIssueCode,
    PreflightIssueScope,
    PreflightIssueSeverity,
)
from app.batches.limits import (
    BATCH_CLEANUP_INTERVAL_SECONDS,
    BATCH_RETENTION_HOURS,
    MAX_IMAGE_FILENAME_CHARACTERS,
)
from app.batches.parsing import normalize_filename, validate_expected_input
from app.batches.preflight import ParsedBatchPreflight
from app.comparison import ExpectedReview
from app.db import connect
from app.extraction import ImageMediaType, PreparedImage
from app.storage import prepare_uploaded_image

_EXPECTED_FIELDS = frozenset(
    {
        BatchField.EXPECTED_BRAND,
        BatchField.EXPECTED_CLASS_TYPE,
        BatchField.EXPECTED_ABV,
        BatchField.EXPECTED_NET_CONTENTS,
    }
)
_IMAGE_ISSUE_CODES = frozenset(
    {
        PreflightIssueCode.MISSING_IMAGE_FILENAME,
        PreflightIssueCode.INVALID_IMAGE_FILENAME,
        PreflightIssueCode.MISSING_IMAGE,
        PreflightIssueCode.DUPLICATE_IMAGE_FILENAME,
        PreflightIssueCode.AMBIGUOUS_IMAGE_FILENAME,
        PreflightIssueCode.UNSUPPORTED_IMAGE,
        PreflightIssueCode.EMPTY_IMAGE,
        PreflightIssueCode.IMAGE_TOO_LARGE,
        PreflightIssueCode.ANIMATED_IMAGE,
        PreflightIssueCode.IMAGE_DIMENSIONS_EXCEEDED,
        PreflightIssueCode.INVALID_IMAGE,
    }
)


class DraftNotFoundError(LookupError):
    """An unknown, expired, or non-draft identifier."""


class DraftValidationError(ValueError):
    """A safe validation failure represented by bounded preflight issues."""

    def __init__(self, issues: tuple[PreflightIssue, ...]) -> None:
        if not issues:
            raise ValueError("draft validation errors require at least one issue")
        self.issues = issues
        super().__init__(issues[0].code.value)


@dataclass(frozen=True, slots=True)
class DraftCleanupResult:
    expired_batch_count: int
    deleted_file_count: int
    failed_file_count: int


@dataclass(frozen=True, slots=True)
class _StoredImage:
    image_id: UUID
    storage_key: str
    path: Path
    original_filename: str
    normalized_filename: str
    prepared: PreparedImage


class BatchDraftService:
    """Persist draft content without exposing a batch-list operation."""

    def __init__(
        self,
        *,
        database_path: Path,
        image_dir: Path,
        temp_dir: Path,
        cleanup_interval_seconds: float = BATCH_CLEANUP_INTERVAL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if cleanup_interval_seconds <= 0:
            raise ValueError("cleanup interval must be positive")
        self.database_path = database_path
        self.image_dir = image_dir
        self.temp_dir = temp_dir
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._storage_lock = asyncio.Lock()
        self._stop_cleanup = asyncio.Event()
        self._cleanup_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._cleanup_task is not None:
            return
        await self.cleanup_expired_and_orphaned()
        self._stop_cleanup = asyncio.Event()
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(),
            name="batch-draft-cleanup",
        )

    async def aclose(self) -> None:
        task = self._cleanup_task
        if task is None:
            return
        self._stop_cleanup.set()
        await task
        self._cleanup_task = None

    async def create_draft(self, preflight: ParsedBatchPreflight) -> BatchPreflightResponse:
        if preflight.has_errors:
            raise DraftValidationError(preflight.issues)
        if not preflight.cases:
            raise DraftValidationError(
                (
                    PreflightIssue(
                        code=PreflightIssueCode.EMPTY_BATCH,
                        scope=PreflightIssueScope.BATCH,
                    ),
                )
            )
        now = self._now()
        async with self._storage_lock:
            batch_id = await to_thread.run_sync(self._create_draft, preflight, now)
        draft = await self.get_draft(batch_id)
        if draft is None:
            raise RuntimeError("newly created batch draft could not be recovered")
        return draft

    async def get_draft(self, batch_id: UUID) -> BatchPreflightResponse | None:
        return await to_thread.run_sync(self._get_draft, batch_id, self._now())

    async def get_case(self, batch_id: UUID, case_id: UUID) -> BatchCaseDetail | None:
        return await to_thread.run_sync(self._get_case, batch_id, case_id, self._now())

    async def get_case_image(self, batch_id: UUID, case_id: UUID) -> PreparedImage | None:
        return await to_thread.run_sync(
            self._get_case_image,
            batch_id,
            case_id,
            self._now(),
        )

    async def correct_case(
        self,
        batch_id: UUID,
        case_id: UUID,
        patch: BatchCasePatchRequest,
    ) -> BatchCaseDetail:
        now = self._now()
        await to_thread.run_sync(self._correct_case, batch_id, case_id, patch, now)
        detail = await self.get_case(batch_id, case_id)
        if detail is None:
            raise DraftNotFoundError
        return detail

    async def replace_case_image(
        self,
        batch_id: UUID,
        case_id: UUID,
        upload: UploadFile,
    ) -> BatchCaseDetail:
        detail = await self.get_case(batch_id, case_id)
        if detail is None:
            await upload.close()
            raise DraftNotFoundError

        filename = unicodedata.normalize("NFC", (upload.filename or "").strip())
        normalized_filename = normalize_filename(filename)
        if len(filename) > MAX_IMAGE_FILENAME_CHARACTERS or normalized_filename is None:
            await upload.close()
            raise DraftValidationError(
                (
                    PreflightIssue(
                        code=PreflightIssueCode.INVALID_IMAGE_FILENAME,
                        scope=PreflightIssueScope.ROW,
                        row_number=detail.summary.row_number,
                        field=BatchField.LABEL_IMAGE_FILENAME,
                    ),
                )
            )

        stored: _StoredImage | None = None
        old_storage_key: str | None = None
        committed = False
        try:
            async with (
                prepare_uploaded_image(upload, temp_dir=self.temp_dir) as prepared,
                self._storage_lock,
            ):
                stored = await to_thread.run_sync(
                    self._store_prepared_image,
                    prepared,
                    filename,
                    normalized_filename,
                )
                old_storage_key = await to_thread.run_sync(
                    self._replace_case_image,
                    batch_id,
                    case_id,
                    stored,
                    self._now(),
                )
                committed = True
                if old_storage_key is not None:
                    _unlink_quietly(self._storage_path(old_storage_key))
        except OSError:
            if not committed:
                if stored is not None:
                    _unlink_quietly(stored.path)
                raise
        except BaseException:
            if not committed and stored is not None:
                _unlink_quietly(stored.path)
            raise

        updated = await self.get_case(batch_id, case_id)
        if updated is None:
            raise DraftNotFoundError
        return updated

    async def cleanup_expired_and_orphaned(self) -> DraftCleanupResult:
        async with self._storage_lock:
            return await to_thread.run_sync(self._cleanup_expired_and_orphaned, self._now())

    def _create_draft(self, preflight: ParsedBatchPreflight, now: datetime) -> UUID:
        self._prepare_image_dir()
        batch_id = uuid4()
        expires_at = now + timedelta(hours=BATCH_RETENTION_HOURS)
        images_by_path = {
            image.prepared.path: image
            for image in preflight.images
            if image.prepared is not None and image.normalized_filename is not None
        }
        stored_by_row: dict[int, _StoredImage] = {}
        try:
            for case in preflight.cases:
                if case.prepared_image is None:
                    continue
                source_image = images_by_path.get(case.prepared_image.path)
                original_filename = (
                    source_image.filename
                    if source_image is not None
                    else case.row.label_image_filename
                )
                normalized_filename = (
                    source_image.normalized_filename
                    if source_image is not None
                    else case.row.normalized_label_image_filename
                )
                if normalized_filename is None:
                    raise RuntimeError("prepared batch images require a normalized filename")
                stored_by_row[case.row.row_number] = self._store_prepared_image(
                    case.prepared_image,
                    original_filename,
                    normalized_filename,
                )

            with connect(self.database_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO batch_reviews (
                        batch_id, status, created_at, updated_at, expires_at
                    ) VALUES (?, 'draft', ?, ?, ?)
                    """,
                    (str(batch_id), _iso(now), _iso(now), _iso(expires_at)),
                )
                for case in preflight.cases:
                    stored = stored_by_row.get(case.row.row_number)
                    if stored is not None:
                        connection.execute(
                            """
                            INSERT INTO batch_images (
                                image_id, batch_id, storage_key, original_filename,
                                normalized_filename, media_type, byte_count, width, height,
                                status, created_at, expires_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', ?, ?)
                            """,
                            (
                                str(stored.image_id),
                                str(batch_id),
                                stored.storage_key,
                                stored.original_filename,
                                stored.normalized_filename,
                                stored.prepared.media_type.value,
                                stored.prepared.byte_count,
                                stored.prepared.width,
                                stored.prepared.height,
                                _iso(now),
                                _iso(expires_at),
                            ),
                        )
                    connection.execute(
                        """
                        INSERT INTO batch_cases (
                            case_id, batch_id, row_number, application_id,
                            normalized_application_id, label_image_filename,
                            normalized_label_image_filename, expected_brand,
                            expected_class_type, expected_abv, expected_net_contents,
                            normalized_expected_json, image_id, status, issues_json,
                            created_at, updated_at, expires_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid4()),
                            str(batch_id),
                            case.row.row_number,
                            case.row.application_id,
                            case.row.normalized_application_id,
                            case.row.label_image_filename,
                            case.row.normalized_label_image_filename,
                            case.row.expected_input.brand_name,
                            case.row.expected_input.class_type,
                            case.row.expected_input.expected_abv,
                            case.row.expected_input.expected_net_contents,
                            _encode_expected(case.row.normalized_expected),
                            str(stored.image_id) if stored is not None else None,
                            case.state.value,
                            _encode_issues(case.issues),
                            _iso(now),
                            _iso(now),
                            _iso(expires_at),
                        ),
                    )
        except BaseException:
            for stored in stored_by_row.values():
                _unlink_quietly(stored.path)
            raise
        return batch_id

    def _get_draft(self, batch_id: UUID, now: datetime) -> BatchPreflightResponse | None:
        with connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            batch = connection.execute(
                """
                SELECT batch_id, status, created_at, expires_at
                FROM batch_reviews
                WHERE batch_id = ? AND status = 'draft' AND expires_at > ?
                """,
                (str(batch_id), _iso(now)),
            ).fetchone()
            if batch is None:
                return None
            rows = connection.execute(
                """
                SELECT case_id, row_number, application_id, label_image_filename,
                       status, issues_json
                FROM batch_cases
                WHERE batch_id = ?
                ORDER BY row_number
                """,
                (str(batch_id),),
            ).fetchall()

        summaries = [_case_summary(row) for row in rows]
        counts = _state_counts(summaries)
        return BatchPreflightResponse(
            batch_id=UUID(batch["batch_id"]),
            state=BatchState(batch["status"]),
            created_at=_datetime(batch["created_at"]),
            expires_at=_datetime(batch["expires_at"]),
            counts=counts,
            cases=summaries,
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
                SELECT c.*
                FROM batch_cases AS c
                JOIN batch_reviews AS b ON b.batch_id = c.batch_id
                WHERE c.batch_id = ? AND c.case_id = ?
                  AND b.status = 'draft' AND b.expires_at > ?
                """,
                (str(batch_id), str(case_id), _iso(now)),
            ).fetchone()
        return _case_detail(row) if row is not None else None

    def _get_case_image(
        self,
        batch_id: UUID,
        case_id: UUID,
        now: datetime,
    ) -> PreparedImage | None:
        with connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT i.storage_key, i.media_type, i.width, i.height, i.byte_count
                FROM batch_cases AS c
                JOIN batch_reviews AS b ON b.batch_id = c.batch_id
                JOIN batch_images AS i ON i.image_id = c.image_id
                WHERE c.batch_id = ? AND c.case_id = ?
                  AND b.expires_at > ? AND i.status = 'available'
                """,
                (str(batch_id), str(case_id), _iso(now)),
            ).fetchone()
        if row is None:
            return None
        path = self._storage_path(row["storage_key"])
        if not path.is_file():
            return None
        return PreparedImage(
            path=path,
            media_type=ImageMediaType(row["media_type"]),
            width=row["width"],
            height=row["height"],
            byte_count=row["byte_count"],
        )

    def _correct_case(
        self,
        batch_id: UUID,
        case_id: UUID,
        patch: BatchCasePatchRequest,
        now: datetime,
    ) -> None:
        with connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = self._mutable_case_row(connection, batch_id, case_id, now)
            if row is None:
                raise DraftNotFoundError
            values = {
                "brand_name": row["expected_brand"],
                "class_type": row["expected_class_type"],
                "expected_abv": row["expected_abv"],
                "expected_net_contents": row["expected_net_contents"],
            }
            values.update(patch.model_dump(exclude_none=True))
            validated = validate_expected_input(
                BatchExpectedInput(**values),
                row_number=row["row_number"],
            )
            preserved = [
                issue
                for issue in _decode_issues(row["issues_json"])
                if issue.field not in _EXPECTED_FIELDS
            ]
            issues = _deduplicate_issues([*preserved, *validated.issues])
            state = _draft_case_state(
                normalized_expected=validated.normalized_expected,
                image_id=row["image_id"],
                issues=issues,
            )
            connection.execute(
                """
                UPDATE batch_cases
                SET expected_brand = ?, expected_class_type = ?, expected_abv = ?,
                    expected_net_contents = ?, normalized_expected_json = ?, status = ?,
                    issues_json = ?, updated_at = ?
                WHERE case_id = ?
                """,
                (
                    validated.expected_input.brand_name,
                    validated.expected_input.class_type,
                    validated.expected_input.expected_abv,
                    validated.expected_input.expected_net_contents,
                    _encode_expected(validated.normalized_expected),
                    state.value,
                    _encode_issues(issues),
                    _iso(now),
                    str(case_id),
                ),
            )
            connection.execute(
                "UPDATE batch_reviews SET updated_at = ? WHERE batch_id = ?",
                (_iso(now), str(batch_id)),
            )

    def _replace_case_image(
        self,
        batch_id: UUID,
        case_id: UUID,
        stored: _StoredImage,
        now: datetime,
    ) -> str | None:
        with connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = self._mutable_case_row(connection, batch_id, case_id, now)
            if row is None:
                raise DraftNotFoundError
            old_storage_key = None
            if row["image_id"] is not None:
                old = connection.execute(
                    "SELECT storage_key FROM batch_images WHERE image_id = ?",
                    (row["image_id"],),
                ).fetchone()
                old_storage_key = old["storage_key"] if old is not None else None

            issues = _deduplicate_issues(
                [
                    issue
                    for issue in _decode_issues(row["issues_json"])
                    if issue.field != BatchField.LABEL_IMAGE_FILENAME
                    and issue.code not in _IMAGE_ISSUE_CODES
                ]
            )
            normalized_expected = _decode_expected(row["normalized_expected_json"])
            state = _draft_case_state(
                normalized_expected=normalized_expected,
                image_id=str(stored.image_id),
                issues=issues,
            )
            connection.execute(
                """
                INSERT INTO batch_images (
                    image_id, batch_id, storage_key, original_filename,
                    normalized_filename, media_type, byte_count, width, height,
                    status, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', ?, ?)
                """,
                (
                    str(stored.image_id),
                    str(batch_id),
                    stored.storage_key,
                    stored.original_filename,
                    stored.normalized_filename,
                    stored.prepared.media_type.value,
                    stored.prepared.byte_count,
                    stored.prepared.width,
                    stored.prepared.height,
                    _iso(now),
                    row["expires_at"],
                ),
            )
            connection.execute(
                """
                UPDATE batch_cases
                SET image_id = ?, label_image_filename = ?,
                    normalized_label_image_filename = ?, status = ?, issues_json = ?,
                    updated_at = ?
                WHERE case_id = ?
                """,
                (
                    str(stored.image_id),
                    stored.original_filename,
                    stored.normalized_filename,
                    state.value,
                    _encode_issues(issues),
                    _iso(now),
                    str(case_id),
                ),
            )
            if row["image_id"] is not None:
                connection.execute(
                    "DELETE FROM batch_images WHERE image_id = ?",
                    (row["image_id"],),
                )
            connection.execute(
                "UPDATE batch_reviews SET updated_at = ? WHERE batch_id = ?",
                (_iso(now), str(batch_id)),
            )
        return old_storage_key

    @staticmethod
    def _mutable_case_row(
        connection: sqlite3.Connection,
        batch_id: UUID,
        case_id: UUID,
        now: datetime,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT c.*, b.status AS batch_status
            FROM batch_cases AS c
            JOIN batch_reviews AS b ON b.batch_id = c.batch_id
            WHERE c.batch_id = ? AND c.case_id = ?
              AND b.status = 'draft' AND b.expires_at > ?
            """,
            (str(batch_id), str(case_id), _iso(now)),
        ).fetchone()

    def _store_prepared_image(
        self,
        prepared: PreparedImage,
        original_filename: str,
        normalized_filename: str,
    ) -> _StoredImage:
        self._prepare_image_dir()
        image_id = uuid4()
        storage_key = f"{uuid4().hex}.png"
        destination = self._storage_path(storage_key)
        try:
            with prepared.path.open("rb") as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target, length=64 * 1024)
            destination.chmod(0o600)
            stored_prepared = PreparedImage(
                path=destination,
                media_type=prepared.media_type,
                width=prepared.width,
                height=prepared.height,
                byte_count=destination.stat().st_size,
            )
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return _StoredImage(
            image_id=image_id,
            storage_key=storage_key,
            path=destination,
            original_filename=original_filename,
            normalized_filename=normalized_filename,
            prepared=stored_prepared,
        )

    def _cleanup_expired_and_orphaned(self, now: datetime) -> DraftCleanupResult:
        self._prepare_image_dir()
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            expired_batch_count = connection.execute(
                "SELECT COUNT(*) FROM batch_reviews WHERE expires_at <= ?",
                (_iso(now),),
            ).fetchone()[0]
            connection.execute(
                "DELETE FROM batch_reviews WHERE expires_at <= ?",
                (_iso(now),),
            )
            live_storage_keys = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT i.storage_key
                    FROM batch_images AS i
                    JOIN batch_reviews AS b ON b.batch_id = i.batch_id
                    WHERE i.status IN ('available', 'processing') AND b.expires_at > ?
                    """,
                    (_iso(now),),
                )
            }

        deleted_file_count = 0
        failed_file_count = 0
        for path in self.image_dir.iterdir():
            if path.name in live_storage_keys or (not path.is_file() and not path.is_symlink()):
                continue
            try:
                path.unlink(missing_ok=True)
                deleted_file_count += 1
            except OSError:
                failed_file_count += 1
        return DraftCleanupResult(
            expired_batch_count=expired_batch_count,
            deleted_file_count=deleted_file_count,
            failed_file_count=failed_file_count,
        )

    async def _cleanup_loop(self) -> None:
        while not self._stop_cleanup.is_set():
            delay = await to_thread.run_sync(self._next_cleanup_delay, self._now())
            try:
                await asyncio.wait_for(self._stop_cleanup.wait(), timeout=delay)
            except TimeoutError:
                try:
                    await self.cleanup_expired_and_orphaned()
                except (OSError, sqlite3.Error):
                    continue

    def _next_cleanup_delay(self, now: datetime) -> float:
        with connect(self.database_path) as connection:
            row = connection.execute("SELECT MIN(expires_at) FROM batch_reviews").fetchone()
        if row is None or row[0] is None:
            return float(self.cleanup_interval_seconds)
        seconds_until_expiry = (_datetime(row[0]) - now).total_seconds()
        return max(0.01, min(float(self.cleanup_interval_seconds), seconds_until_expiry))

    def _prepare_image_dir(self) -> None:
        self.image_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.image_dir.chmod(0o700)

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
            raise ValueError("batch draft clocks must return timezone-aware datetimes")
        return now.astimezone(UTC)


def _case_summary(row: sqlite3.Row) -> BatchCaseSummary:
    return BatchCaseSummary(
        case_id=UUID(row["case_id"]),
        row_number=row["row_number"],
        application_id=row["application_id"],
        label_image_filename=row["label_image_filename"],
        state=BatchCaseState(row["status"]),
        issues=_decode_issues(row["issues_json"]),
    )


def _case_detail(row: sqlite3.Row) -> BatchCaseDetail:
    return BatchCaseDetail(
        summary=_case_summary(row),
        expected_input=BatchExpectedInput(
            brand_name=row["expected_brand"],
            class_type=row["expected_class_type"],
            expected_abv=row["expected_abv"],
            expected_net_contents=row["expected_net_contents"],
        ),
        normalized_expected=_decode_expected(row["normalized_expected_json"]),
    )


def _state_counts(cases: list[BatchCaseSummary]) -> BatchStateCounts:
    values = {state.value: 0 for state in BatchCaseState}
    for case in cases:
        values[case.state.value] += 1
    return BatchStateCounts(total=len(cases), **values)


def _draft_case_state(
    *,
    normalized_expected: ExpectedReview | None,
    image_id: str | None,
    issues: list[PreflightIssue],
) -> BatchCaseState:
    has_errors = any(issue.severity == PreflightIssueSeverity.ERROR for issue in issues)
    if normalized_expected is not None and image_id is not None and not has_errors:
        return BatchCaseState.READY
    return BatchCaseState.NEEDS_CORRECTION


def _encode_issues(issues: tuple[PreflightIssue, ...] | list[PreflightIssue]) -> str:
    values = [issue.model_dump(mode="json", exclude={"message", "severity"}) for issue in issues]
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _decode_issues(value: str) -> list[PreflightIssue]:
    decoded = json.loads(value)
    if not isinstance(decoded, list):
        raise RuntimeError("stored preflight issues must be a list")
    return [PreflightIssue.model_validate(issue) for issue in decoded]


def _encode_expected(expected: ExpectedReview | None) -> str | None:
    if expected is None:
        return None
    return expected.model_dump_json()


def _decode_expected(value: str | None) -> ExpectedReview | None:
    if value is None:
        return None
    return ExpectedReview.model_validate_json(value)


def _deduplicate_issues(issues: list[PreflightIssue]) -> list[PreflightIssue]:
    result: list[PreflightIssue] = []
    keys: set[tuple[PreflightIssueCode, PreflightIssueScope, int | None, BatchField | None]] = set()
    for issue in issues:
        key = (issue.code, issue.scope, issue.row_number, issue.field)
        if key not in keys:
            keys.add(key)
            result.append(issue)
    return result


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError("stored batch timestamps must be timezone-aware")
    return parsed.astimezone(UTC)


def _unlink_quietly(path: Path) -> None:
    with suppress(OSError):
        path.unlink(missing_ok=True)
