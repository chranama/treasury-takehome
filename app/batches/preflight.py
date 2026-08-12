"""Bounded upload preparation and filename association for batch preflight."""

import os
import tempfile
import unicodedata
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from io import BufferedReader
from pathlib import Path

from anyio import open_file, to_thread
from fastapi import UploadFile

from app.batches.contracts import (
    BatchCaseState,
    BatchField,
    PreflightIssue,
    PreflightIssueCode,
    PreflightIssueScope,
    PreflightIssueSeverity,
)
from app.batches.limits import (
    MAX_AGGREGATE_UPLOAD_BYTES,
    MAX_BATCH_IMAGES,
    MAX_IMAGE_FILENAME_CHARACTERS,
    MAX_WORKBOOK_BYTES,
)
from app.batches.parsing import (
    ParsedSpreadsheet,
    ParsedSpreadsheetRow,
    SpreadsheetKind,
    normalize_filename,
    parse_spreadsheet,
)
from app.extraction import PreparedImage
from app.storage import (
    DEFAULT_IMAGE_LIMITS,
    ImageIntakeError,
    ImageIntakeErrorKind,
    prepare_uploaded_image,
)

_UPLOAD_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ParsedBatchImage:
    filename: str
    normalized_filename: str | None
    source_byte_count: int
    prepared: PreparedImage | None
    referenced_rows: tuple[int, ...]
    issues: tuple[PreflightIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == PreflightIssueSeverity.ERROR for issue in self.issues)


@dataclass(frozen=True, slots=True)
class ParsedBatchCase:
    row: ParsedSpreadsheetRow
    prepared_image: PreparedImage | None
    issues: tuple[PreflightIssue, ...]

    @property
    def state(self) -> BatchCaseState:
        if self.prepared_image is not None and not self.has_errors:
            return BatchCaseState.READY
        return BatchCaseState.NEEDS_CORRECTION

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == PreflightIssueSeverity.ERROR for issue in self.issues)


@dataclass(frozen=True, slots=True)
class ParsedBatchPreflight:
    spreadsheet_kind: SpreadsheetKind
    cases: tuple[ParsedBatchCase, ...]
    images: tuple[ParsedBatchImage, ...]
    issues: tuple[PreflightIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == PreflightIssueSeverity.ERROR for issue in self.issues)

    @property
    def ready_case_count(self) -> int:
        if self.has_errors:
            return 0
        return sum(case.state == BatchCaseState.READY for case in self.cases)

    @property
    def correction_case_count(self) -> int:
        return len(self.cases) - self.ready_case_count


@dataclass(slots=True)
class _MutableImage:
    filename: str
    normalized_filename: str | None
    source_byte_count: int
    raw_path: Path | None
    prepared: PreparedImage | None = None
    referenced_rows: list[int] = field(default_factory=list)
    issues: list[PreflightIssue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == PreflightIssueSeverity.ERROR for issue in self.issues)

    def freeze(self) -> ParsedBatchImage:
        return ParsedBatchImage(
            filename=self.filename,
            normalized_filename=self.normalized_filename,
            source_byte_count=self.source_byte_count,
            prepared=self.prepared,
            referenced_rows=tuple(self.referenced_rows),
            issues=tuple(_deduplicate_issues(self.issues)),
        )


@dataclass(slots=True)
class _MutableCase:
    row: ParsedSpreadsheetRow
    prepared_image: PreparedImage | None = None
    issues: list[PreflightIssue] = field(default_factory=list)

    def freeze(self) -> ParsedBatchCase:
        return ParsedBatchCase(
            row=self.row,
            prepared_image=self.prepared_image,
            issues=tuple(_deduplicate_issues([*self.row.issues, *self.issues])),
        )


class _SpoolFailure(StrEnum):
    FILE_TOO_LARGE = "file_too_large"
    AGGREGATE_TOO_LARGE = "aggregate_too_large"


@dataclass(frozen=True, slots=True)
class _SpoolResult:
    path: Path | None
    byte_count: int
    failure: _SpoolFailure | None = None


@asynccontextmanager
async def prepare_batch_preflight(
    spreadsheet: UploadFile,
    images: list[UploadFile],
    *,
    temp_dir: Path,
) -> AsyncGenerator[ParsedBatchPreflight, None]:
    """Validate a bounded package and own every prepared image until context exit."""

    temp_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw_paths: list[Path] = []
    async with AsyncExitStack() as stack:
        try:
            early_issues = _early_package_issues(spreadsheet, images)
            if early_issues:
                await _close_uploads([spreadsheet, *images])
                yield _empty_preflight(spreadsheet, early_issues)
                return

            aggregate_bytes = 0
            workbook_spool = await _spool_upload(
                spreadsheet,
                temp_dir=temp_dir,
                prefix="batch-workbook-",
                max_file_bytes=MAX_WORKBOOK_BYTES,
                aggregate_bytes=aggregate_bytes,
            )
            aggregate_bytes += workbook_spool.byte_count
            if workbook_spool.path is not None:
                raw_paths.append(workbook_spool.path)
            if workbook_spool.failure is not None:
                await _close_uploads(images)
                issue_code = (
                    PreflightIssueCode.AGGREGATE_UPLOAD_TOO_LARGE
                    if workbook_spool.failure == _SpoolFailure.AGGREGATE_TOO_LARGE
                    else PreflightIssueCode.WORKBOOK_TOO_LARGE
                )
                yield _empty_preflight(spreadsheet, [_batch_issue(issue_code)])
                return

            mutable_images: list[_MutableImage] = []
            aggregate_failed = False
            for index, upload in enumerate(images):
                display_filename, filename_too_long = _bounded_filename(upload.filename or "")
                normalized = None if filename_too_long else normalize_filename(display_filename)
                spooled = await _spool_upload(
                    upload,
                    temp_dir=temp_dir,
                    prefix="batch-image-",
                    max_file_bytes=DEFAULT_IMAGE_LIMITS.max_upload_bytes,
                    aggregate_bytes=aggregate_bytes,
                )
                aggregate_bytes += spooled.byte_count
                if spooled.path is not None:
                    raw_paths.append(spooled.path)
                image = _MutableImage(
                    filename=display_filename,
                    normalized_filename=normalized,
                    source_byte_count=spooled.byte_count,
                    raw_path=spooled.path,
                )
                if normalized is None:
                    image.issues.append(_image_issue(PreflightIssueCode.INVALID_IMAGE_FILENAME))
                if spooled.failure == _SpoolFailure.FILE_TOO_LARGE:
                    image.issues.append(_image_issue(PreflightIssueCode.IMAGE_TOO_LARGE))
                elif spooled.failure == _SpoolFailure.AGGREGATE_TOO_LARGE:
                    aggregate_failed = True
                mutable_images.append(image)
                if aggregate_failed:
                    await _close_uploads(images[index + 1 :])
                    break

            if aggregate_failed:
                yield _empty_preflight(
                    spreadsheet,
                    [_batch_issue(PreflightIssueCode.AGGREGATE_UPLOAD_TOO_LARGE)],
                )
                return

            assert workbook_spool.path is not None
            parsed = await to_thread.run_sync(
                partial(
                    parse_spreadsheet,
                    workbook_spool.path,
                    filename=spreadsheet.filename or "",
                )
            )
            workbook_spool.path.unlink(missing_ok=True)
            raw_paths.remove(workbook_spool.path)

            _mark_image_filename_collisions(mutable_images)
            if not parsed.has_errors:
                await _prepare_images(
                    mutable_images,
                    stack=stack,
                    temp_dir=temp_dir,
                    raw_paths=raw_paths,
                )
            cases = _associate(parsed, mutable_images)
            yield ParsedBatchPreflight(
                spreadsheet_kind=parsed.kind,
                cases=tuple(case.freeze() for case in cases),
                images=tuple(image.freeze() for image in mutable_images),
                issues=parsed.issues,
            )
        finally:
            await _close_uploads([spreadsheet, *images])
            for path in raw_paths:
                path.unlink(missing_ok=True)


def _early_package_issues(
    spreadsheet: UploadFile,
    images: list[UploadFile],
) -> list[PreflightIssue]:
    issues: list[PreflightIssue] = []
    suffix = Path(spreadsheet.filename or "").suffix.casefold()
    if suffix == ".xlsm":
        issues.append(_batch_issue(PreflightIssueCode.MACRO_ENABLED_WORKBOOK))
    elif suffix not in {".csv", ".xlsx"}:
        issues.append(_batch_issue(PreflightIssueCode.UNSUPPORTED_SPREADSHEET))
    if len(images) > MAX_BATCH_IMAGES:
        issues.append(_batch_issue(PreflightIssueCode.TOO_MANY_IMAGES))

    uploads = [spreadsheet, *images]
    if all(upload.size is not None for upload in uploads):
        known_total = sum(upload.size or 0 for upload in uploads)
        if known_total > MAX_AGGREGATE_UPLOAD_BYTES:
            issues.append(_batch_issue(PreflightIssueCode.AGGREGATE_UPLOAD_TOO_LARGE))
    return _deduplicate_issues(issues)


async def _spool_upload(
    upload: UploadFile,
    *,
    temp_dir: Path,
    prefix: str,
    max_file_bytes: int,
    aggregate_bytes: int,
) -> _SpoolResult:
    descriptor, raw_path = tempfile.mkstemp(prefix=prefix, suffix=".upload", dir=temp_dir)
    os.close(descriptor)
    path = Path(raw_path)
    byte_count = 0
    failure: _SpoolFailure | None = None
    try:
        async with await open_file(path, "wb") as output:
            while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
                byte_count += len(chunk)
                if aggregate_bytes + byte_count > MAX_AGGREGATE_UPLOAD_BYTES:
                    failure = _SpoolFailure.AGGREGATE_TOO_LARGE
                    break
                if byte_count > max_file_bytes:
                    failure = _SpoolFailure.FILE_TOO_LARGE
                    break
                await output.write(chunk)
    finally:
        await upload.close()

    if failure is not None:
        path.unlink(missing_ok=True)
        path = None
    return _SpoolResult(path=path, byte_count=byte_count, failure=failure)


async def _prepare_images(
    images: list[_MutableImage],
    *,
    stack: AsyncExitStack,
    temp_dir: Path,
    raw_paths: list[Path],
) -> None:
    for image in images:
        if image.raw_path is None:
            continue
        raw_path = image.raw_path
        source: BufferedReader = raw_path.open("rb")
        validation_upload = UploadFile(
            file=source,
            filename=image.filename,
            size=image.source_byte_count,
        )
        try:
            image.prepared = await stack.enter_async_context(
                prepare_uploaded_image(validation_upload, temp_dir=temp_dir)
            )
        except ImageIntakeError as error:
            image.issues.append(_image_issue(_image_issue_code(error.kind)))
        finally:
            raw_path.unlink(missing_ok=True)
            if raw_path in raw_paths:
                raw_paths.remove(raw_path)
            image.raw_path = None


def _mark_image_filename_collisions(images: list[_MutableImage]) -> None:
    grouped: dict[str, list[_MutableImage]] = {}
    for image in images:
        if image.normalized_filename is not None:
            grouped.setdefault(image.normalized_filename, []).append(image)
    for collisions in grouped.values():
        if len(collisions) < 2:
            continue
        code = (
            PreflightIssueCode.DUPLICATE_IMAGE_FILENAME
            if len({image.filename for image in collisions}) == 1
            else PreflightIssueCode.AMBIGUOUS_IMAGE_FILENAME
        )
        for image in collisions:
            image.issues.append(_image_issue(code))


def _associate(
    parsed: ParsedSpreadsheet,
    images: list[_MutableImage],
) -> list[_MutableCase]:
    by_name: dict[str, list[_MutableImage]] = {}
    for image in images:
        if image.normalized_filename is not None:
            by_name.setdefault(image.normalized_filename, []).append(image)

    cases = [_MutableCase(row=row) for row in parsed.rows]
    for case in cases:
        normalized = case.row.normalized_label_image_filename
        if normalized is None:
            continue
        candidates = by_name.get(normalized, [])
        if not candidates:
            case.issues.append(_row_issue(PreflightIssueCode.MISSING_IMAGE, case.row.row_number))
            continue
        for image in candidates:
            image.referenced_rows.append(case.row.row_number)
        if len(candidates) > 1:
            collision_codes = {
                issue.code
                for image in candidates
                for issue in image.issues
                if issue.code
                in {
                    PreflightIssueCode.DUPLICATE_IMAGE_FILENAME,
                    PreflightIssueCode.AMBIGUOUS_IMAGE_FILENAME,
                }
            }
            for code in sorted(collision_codes, key=str):
                case.issues.append(_row_issue(code, case.row.row_number))
            continue

        image = candidates[0]
        for issue in image.issues:
            if issue.severity == PreflightIssueSeverity.ERROR:
                case.issues.append(_row_issue(issue.code, case.row.row_number))
        has_duplicate_reference = any(
            issue.code == PreflightIssueCode.DUPLICATE_IMAGE_FILENAME for issue in case.row.issues
        )
        if not image.has_errors and not has_duplicate_reference:
            case.prepared_image = image.prepared

    referenced_names = {
        case.row.normalized_label_image_filename
        for case in cases
        if case.row.normalized_label_image_filename is not None
    }
    for image in images:
        if (
            image.normalized_filename is not None
            and image.normalized_filename not in referenced_names
        ):
            image.issues.append(_image_issue(PreflightIssueCode.UNREFERENCED_IMAGE))
    return cases


def _image_issue_code(kind: ImageIntakeErrorKind) -> PreflightIssueCode:
    return {
        ImageIntakeErrorKind.EMPTY_FILE: PreflightIssueCode.EMPTY_IMAGE,
        ImageIntakeErrorKind.UPLOAD_TOO_LARGE: PreflightIssueCode.IMAGE_TOO_LARGE,
        ImageIntakeErrorKind.UNSUPPORTED_FORMAT: PreflightIssueCode.UNSUPPORTED_IMAGE,
        ImageIntakeErrorKind.CORRUPT_IMAGE: PreflightIssueCode.INVALID_IMAGE,
        ImageIntakeErrorKind.ANIMATED_IMAGE: PreflightIssueCode.ANIMATED_IMAGE,
        ImageIntakeErrorKind.DIMENSIONS_EXCEEDED: (PreflightIssueCode.IMAGE_DIMENSIONS_EXCEEDED),
        ImageIntakeErrorKind.DECOMPRESSION_BOMB: (PreflightIssueCode.IMAGE_DIMENSIONS_EXCEEDED),
    }[kind]


def _bounded_filename(value: str) -> tuple[str, bool]:
    normalized = unicodedata.normalize("NFC", value.strip())
    return normalized[:MAX_IMAGE_FILENAME_CHARACTERS], len(
        normalized
    ) > MAX_IMAGE_FILENAME_CHARACTERS


async def _close_uploads(uploads: list[UploadFile]) -> None:
    for upload in uploads:
        if not upload.file.closed:
            await upload.close()


def _empty_preflight(
    spreadsheet: UploadFile,
    issues: list[PreflightIssue],
) -> ParsedBatchPreflight:
    suffix = Path(spreadsheet.filename or "").suffix.casefold()
    kind = {
        ".csv": SpreadsheetKind.CSV,
        ".xlsx": SpreadsheetKind.XLSX,
    }.get(suffix, SpreadsheetKind.UNSUPPORTED)
    return ParsedBatchPreflight(
        spreadsheet_kind=kind,
        cases=(),
        images=(),
        issues=tuple(_deduplicate_issues(issues)),
    )


def _batch_issue(code: PreflightIssueCode) -> PreflightIssue:
    return PreflightIssue(code=code, scope=PreflightIssueScope.BATCH)


def _image_issue(code: PreflightIssueCode) -> PreflightIssue:
    return PreflightIssue(code=code, scope=PreflightIssueScope.IMAGE)


def _row_issue(code: PreflightIssueCode, row_number: int) -> PreflightIssue:
    return PreflightIssue(
        code=code,
        scope=PreflightIssueScope.ROW,
        row_number=row_number,
        field=BatchField.LABEL_IMAGE_FILENAME,
    )


def _deduplicate_issues(issues: list[PreflightIssue]) -> list[PreflightIssue]:
    result: list[PreflightIssue] = []
    keys: set[tuple[PreflightIssueCode, PreflightIssueScope, int | None, BatchField | None]] = set()
    for issue in issues:
        key = (issue.code, issue.scope, issue.row_number, issue.field)
        if key not in keys:
            keys.add(key)
            result.append(issue)
    return result
