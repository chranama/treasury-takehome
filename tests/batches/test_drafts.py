import asyncio
import csv
import stat
from datetime import UTC, datetime, timedelta
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from PIL import Image

from app.batches import (
    BATCH_RETENTION_HOURS,
    BATCH_TEMPLATE_HEADERS,
    BatchCasePatchRequest,
    BatchCaseState,
    prepare_batch_preflight,
)
from app.batches.drafts import (
    BatchDraftService,
    DraftNotFoundError,
    DraftValidationError,
)
from app.db import connect, initialize_database
from app.storage import ImageIntakeError


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def upload(content: bytes, *, filename: str) -> UploadFile:
    return UploadFile(file=BytesIO(content), filename=filename, size=len(content))


def png_bytes(color: str = "navy") -> bytes:
    image = Image.new("RGB", (40, 24), color=color)
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


def csv_bytes(rows: list[list[str]]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(BATCH_TEMPLATE_HEADERS)
    writer.writerows(rows)
    return output.getvalue().encode()


def make_service(
    tmp_path: Path,
    clock: MutableClock,
    *,
    cleanup_interval_seconds: float = 300,
) -> BatchDraftService:
    database_path = tmp_path / "treasury.sqlite3"
    initialize_database(database_path)
    return BatchDraftService(
        database_path=database_path,
        image_dir=tmp_path / "batch-images",
        temp_dir=tmp_path / "uploads",
        cleanup_interval_seconds=cleanup_interval_seconds,
        clock=clock,
    )


async def create_draft(
    service: BatchDraftService,
    rows: list[list[str]],
    images: list[UploadFile],
):
    async with prepare_batch_preflight(
        upload(csv_bytes(rows), filename="batch.csv"),
        images,
        temp_dir=service.temp_dir,
    ) as preflight:
        return await service.create_draft(preflight)


def test_unexpired_draft_recovers_from_a_new_service_instance_with_private_images(
    tmp_path: Path,
    caplog,
) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 12, tzinfo=UTC))
    service = make_service(tmp_path, clock)
    rows = [
        ["APP-PRIVATE", "first.png", "Brand", "Bourbon", "45", "750 mL"],
        ["", "second.png", "Brand", "Bourbon", "45", "750 mL"],
    ]

    async def run() -> None:
        created = await create_draft(
            service,
            rows,
            [
                upload(png_bytes("navy"), filename="first.png"),
                upload(png_bytes("green"), filename="second.png"),
            ],
        )
        recovered_service = make_service(tmp_path, clock)
        recovered = await recovered_service.get_draft(created.batch_id)

        assert created.batch_id.version == 4
        assert recovered == created
        assert recovered is not None
        assert recovered.counts.ready == 1
        assert recovered.counts.needs_correction == 1
        assert not hasattr(recovered_service, "list_drafts")

    asyncio.run(run())

    stored_files = list(service.image_dir.iterdir())
    assert len(stored_files) == 2
    assert stat.S_IMODE(service.image_dir.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in stored_files)
    assert list(service.temp_dir.iterdir()) == []
    assert "APP-PRIVATE" not in caplog.text
    assert "first.png" not in caplog.text
    assert "198.51.100.44" not in caplog.text


def test_correcting_one_case_revalidates_only_that_case(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 12, tzinfo=UTC))
    service = make_service(tmp_path, clock)
    rows = [
        ["APP-1", "one.png", "Brand", "Bourbon", "101", "750 mL"],
        ["APP-2", "two.png", "Brand", "Bourbon", "45", "750 mL"],
    ]

    async def run() -> None:
        draft = await create_draft(
            service,
            rows,
            [
                upload(png_bytes("red"), filename="one.png"),
                upload(png_bytes("blue"), filename="two.png"),
            ],
        )
        first, second = draft.cases
        with connect(service.database_path) as connection:
            untouched_before = connection.execute(
                "SELECT updated_at, issues_json, image_id FROM batch_cases WHERE case_id = ?",
                (str(second.case_id),),
            ).fetchone()

        clock.value += timedelta(seconds=5)
        corrected = await service.correct_case(
            draft.batch_id,
            first.case_id,
            BatchCasePatchRequest(expected_abv="45%"),
        )

        assert corrected.summary.state == BatchCaseState.READY
        assert corrected.summary.issues == []
        assert corrected.normalized_expected is not None
        with connect(service.database_path) as connection:
            untouched_after = connection.execute(
                "SELECT updated_at, issues_json, image_id FROM batch_cases WHERE case_id = ?",
                (str(second.case_id),),
            ).fetchone()
        assert untouched_after == untouched_before

    asyncio.run(run())


def test_image_replacement_is_atomic_and_deletes_replaced_or_rejected_files(
    tmp_path: Path,
) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 12, tzinfo=UTC))
    service = make_service(tmp_path, clock)

    async def run() -> None:
        draft = await create_draft(
            service,
            [["APP-1", "missing.png", "Brand", "Bourbon", "45", "750 mL"]],
            [],
        )
        case_id = draft.cases[0].case_id
        first = await service.replace_case_image(
            draft.batch_id,
            case_id,
            upload(png_bytes("red"), filename="replacement-one.png"),
        )
        first_image = await service.get_case_image(draft.batch_id, case_id)
        assert first.summary.state == BatchCaseState.READY
        assert first.summary.label_image_filename == "replacement-one.png"
        assert first_image is not None and first_image.path.is_file()

        second = await service.replace_case_image(
            draft.batch_id,
            case_id,
            upload(png_bytes("blue"), filename="replacement-two.png"),
        )
        second_image = await service.get_case_image(draft.batch_id, case_id)
        assert second.summary.state == BatchCaseState.READY
        assert second_image is not None and second_image.path.is_file()
        assert second_image.path != first_image.path
        assert not first_image.path.exists()
        assert list(service.image_dir.iterdir()) == [second_image.path]

        with pytest.raises(ImageIntakeError):
            await service.replace_case_image(
                draft.batch_id,
                case_id,
                upload(b"not an image", filename="rejected.png"),
            )
        with pytest.raises(DraftValidationError):
            await service.replace_case_image(
                draft.batch_id,
                case_id,
                upload(png_bytes(), filename="../rejected.png"),
            )
        assert await service.get_case_image(draft.batch_id, case_id) == second_image
        assert list(service.image_dir.iterdir()) == [second_image.path]
        assert list(service.temp_dir.iterdir()) == []

    asyncio.run(run())


def test_expiry_cleanup_removes_database_content_and_orphaned_images(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 12, tzinfo=UTC))
    service = make_service(tmp_path, clock)

    async def run() -> None:
        draft = await create_draft(
            service,
            [["APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL"]],
            [upload(png_bytes(), filename="label.png")],
        )
        orphan = service.image_dir / "orphan.png"
        orphan.write_bytes(png_bytes("orange"))
        clock.value += timedelta(hours=BATCH_RETENTION_HOURS)

        assert await service.get_draft(draft.batch_id) is None
        result = await service.cleanup_expired_and_orphaned()

        assert result.expired_batch_count == 1
        assert result.deleted_file_count == 2
        assert result.failed_file_count == 0
        assert list(service.image_dir.iterdir()) == []
        with connect(service.database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM batch_reviews").fetchone() == (0,)

    asyncio.run(run())


def test_periodic_cleanup_preserves_an_unexpired_processing_image(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 12, tzinfo=UTC))
    service = make_service(tmp_path, clock)

    async def run() -> None:
        draft = await create_draft(
            service,
            [["APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL"]],
            [upload(png_bytes(), filename="label.png")],
        )
        stored = next(service.image_dir.iterdir())
        with connect(service.database_path) as connection:
            connection.execute(
                "UPDATE batch_reviews SET status = 'processing' WHERE batch_id = ?",
                (str(draft.batch_id),),
            )
            connection.execute(
                "UPDATE batch_cases SET status = 'processing' WHERE batch_id = ?",
                (str(draft.batch_id),),
            )
            connection.execute(
                "UPDATE batch_images SET status = 'processing' WHERE batch_id = ?",
                (str(draft.batch_id),),
            )

        result = await service.cleanup_expired_and_orphaned()

        assert result.deleted_file_count == 0
        assert stored.is_file()

    asyncio.run(run())


def test_periodic_cleanup_retries_terminal_image_deletion_with_safe_bookkeeping(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 12, tzinfo=UTC))
    service = make_service(tmp_path, clock)

    async def run() -> None:
        draft = await create_draft(
            service,
            [["APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL"]],
            [upload(png_bytes(), filename="label.png")],
        )
        stored = next(service.image_dir.iterdir())
        with connect(service.database_path) as connection:
            connection.execute(
                "UPDATE batch_reviews SET status = 'processing' WHERE batch_id = ?",
                (str(draft.batch_id),),
            )
            connection.execute(
                "UPDATE batch_cases SET status = 'failed', safe_failure_reason = 'Safe failure' "
                "WHERE batch_id = ?",
                (str(draft.batch_id),),
            )
            connection.execute(
                "UPDATE batch_images SET status = 'processing' WHERE batch_id = ?",
                (str(draft.batch_id),),
            )

        original_unlink = Path.unlink
        fail_once = True

        def flaky_unlink(path: Path, *, missing_ok: bool = False) -> None:
            nonlocal fail_once
            if path == stored and fail_once:
                fail_once = False
                raise OSError("simulated deletion failure")
            original_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", flaky_unlink)
        first = await service.cleanup_expired_and_orphaned()
        assert first.deleted_file_count == 0
        assert first.failed_file_count == 1
        assert stored.is_file()
        with connect(service.database_path) as connection:
            assert connection.execute(
                "SELECT status, cleanup_attempts, cleanup_last_error_kind "
                "FROM batch_images WHERE batch_id = ?",
                (str(draft.batch_id),),
            ).fetchone() == ("processing", 1, "os_error")

        second = await service.cleanup_expired_and_orphaned()
        assert second.deleted_file_count == 1
        assert second.failed_file_count == 0
        assert not stored.exists()
        with connect(service.database_path) as connection:
            assert connection.execute(
                "SELECT status, cleanup_attempts, cleanup_last_error_kind, deleted_at "
                "FROM batch_images WHERE batch_id = ?",
                (str(draft.batch_id),),
            ).fetchone() == ("deleted", 2, None, clock.value.isoformat())

    asyncio.run(run())


def test_startup_and_periodic_cleanup_enforce_expiry(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 12, tzinfo=UTC))
    creator = make_service(tmp_path, clock)

    async def run() -> None:
        startup_draft = await create_draft(
            creator,
            [["APP-1", "one.png", "Brand", "Bourbon", "45", "750 mL"]],
            [upload(png_bytes("red"), filename="one.png")],
        )
        clock.value += timedelta(hours=BATCH_RETENTION_HOURS)
        lifecycle = make_service(tmp_path, clock, cleanup_interval_seconds=0.02)
        await lifecycle.start()
        assert await lifecycle.get_draft(startup_draft.batch_id) is None
        assert list(lifecycle.image_dir.iterdir()) == []

        periodic_draft = await create_draft(
            lifecycle,
            [["APP-2", "two.png", "Brand", "Bourbon", "45", "750 mL"]],
            [upload(png_bytes("blue"), filename="two.png")],
        )
        clock.value += timedelta(hours=BATCH_RETENTION_HOURS)
        await asyncio.sleep(0.08)
        assert await lifecycle.get_draft(periodic_draft.batch_id) is None
        assert list(lifecycle.image_dir.iterdir()) == []
        await lifecycle.aclose()

    asyncio.run(run())


def test_mutations_hide_unknown_expired_and_cross_batch_case_ids(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 12, tzinfo=UTC))
    service = make_service(tmp_path, clock)

    async def run() -> None:
        first = await create_draft(
            service,
            [["APP-1", "one.png", "Brand", "Bourbon", "101", "750 mL"]],
            [upload(png_bytes("red"), filename="one.png")],
        )
        second = await create_draft(
            service,
            [["APP-2", "two.png", "Brand", "Bourbon", "45", "750 mL"]],
            [upload(png_bytes("blue"), filename="two.png")],
        )
        with pytest.raises(DraftNotFoundError):
            await service.correct_case(
                first.batch_id,
                second.cases[0].case_id,
                BatchCasePatchRequest(expected_abv="45"),
            )

        clock.value += timedelta(hours=BATCH_RETENTION_HOURS)
        with pytest.raises(DraftNotFoundError):
            await service.correct_case(
                first.batch_id,
                first.cases[0].case_id,
                BatchCasePatchRequest(expected_abv="45"),
            )

    asyncio.run(run())
