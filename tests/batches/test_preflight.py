import asyncio
import csv
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from PIL import Image

from app.batches import (
    BATCH_TEMPLATE_HEADERS,
    MAX_AGGREGATE_UPLOAD_BYTES,
    MAX_BATCH_IMAGES,
    MAX_WORKBOOK_BYTES,
    BatchCaseState,
    PreflightIssueCode,
    prepare_batch_preflight,
)


def upload(content: bytes, *, filename: str, reported_size: int | None = None) -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        size=len(content) if reported_size is None else reported_size,
    )


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


def codes(issues) -> set[PreflightIssueCode]:
    return {issue.code for issue in issues}


def test_valid_package_matches_by_normalized_filename_and_cleans_every_file(
    tmp_path: Path,
) -> None:
    spreadsheet = upload(
        csv_bytes([["APP-1", "cafe\u0301.png", "Brand", "Bourbon", "45", "750 mL"]]),
        filename="batch.csv",
    )
    image = upload(png_bytes(), filename=" CAFÉ.PNG ")
    prepared_path: Path | None = None

    async def run() -> None:
        nonlocal prepared_path
        async with prepare_batch_preflight(
            spreadsheet,
            [image],
            temp_dir=tmp_path / "uploads",
        ) as result:
            assert result.issues == ()
            assert result.ready_case_count == 1
            assert result.correction_case_count == 0
            assert len(result.cases) == 1
            case = result.cases[0]
            assert case.state == BatchCaseState.READY
            assert case.issues == ()
            assert case.prepared_image is not None
            prepared_path = case.prepared_image.path
            assert prepared_path.is_file()
            assert result.images[0].referenced_rows == (2,)
            assert not any(
                path.name.startswith("batch-workbook-") for path in prepared_path.parent.iterdir()
            )
            assert not any(
                path.name.startswith("batch-image-") for path in prepared_path.parent.iterdir()
            )

    asyncio.run(run())

    assert prepared_path is not None
    assert not prepared_path.exists()
    assert list((tmp_path / "uploads").iterdir()) == []
    assert spreadsheet.file.closed
    assert image.file.closed


def test_missing_invalid_and_unreferenced_images_are_distinct(tmp_path: Path) -> None:
    spreadsheet = upload(
        csv_bytes(
            [
                ["APP-1", "missing.png", "Brand", "Bourbon", "45", "750 mL"],
                ["APP-2", "broken.png", "Brand", "Bourbon", "45", "750 mL"],
            ]
        ),
        filename="batch.csv",
    )
    broken = upload(b"not an image", filename="broken.png")
    unused = upload(png_bytes("green"), filename="unused.png")

    async def run() -> None:
        async with prepare_batch_preflight(
            spreadsheet,
            [broken, unused],
            temp_dir=tmp_path / "uploads",
        ) as result:
            assert result.ready_case_count == 0
            assert codes(result.cases[0].issues) == {PreflightIssueCode.MISSING_IMAGE}
            assert PreflightIssueCode.UNSUPPORTED_IMAGE in codes(result.cases[1].issues)
            by_name = {image.filename: image for image in result.images}
            assert PreflightIssueCode.UNSUPPORTED_IMAGE in codes(by_name["broken.png"].issues)
            assert PreflightIssueCode.UNREFERENCED_IMAGE in codes(by_name["unused.png"].issues)
            assert by_name["unused.png"].prepared is not None

    asyncio.run(run())
    assert list((tmp_path / "uploads").iterdir()) == []


def test_invalid_expected_values_keep_the_case_out_of_ready_state(tmp_path: Path) -> None:
    spreadsheet = upload(
        csv_bytes([["APP-1", "label.png", "Brand", "Bourbon", "101", "750 mL"]]),
        filename="batch.csv",
    )

    async def run() -> None:
        async with prepare_batch_preflight(
            spreadsheet,
            [upload(png_bytes(), filename="label.png")],
            temp_dir=tmp_path / "uploads",
        ) as result:
            case = result.cases[0]
            assert case.state == BatchCaseState.NEEDS_CORRECTION
            assert PreflightIssueCode.INVALID_ABV in codes(case.issues)
            assert case.prepared_image is not None
            assert result.ready_case_count == 0

    asyncio.run(run())


@pytest.mark.parametrize(
    ("filenames", "expected_code"),
    [
        (["label.png", "label.png"], PreflightIssueCode.DUPLICATE_IMAGE_FILENAME),
        (["Label.PNG", "label.png"], PreflightIssueCode.AMBIGUOUS_IMAGE_FILENAME),
    ],
)
def test_selected_image_filename_collisions_are_never_chosen_silently(
    tmp_path: Path,
    filenames: list[str],
    expected_code: PreflightIssueCode,
) -> None:
    spreadsheet = upload(
        csv_bytes([["APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL"]]),
        filename="batch.csv",
    )
    images = [
        upload(png_bytes(color), filename=name)
        for name, color in zip(filenames, ["red", "blue"], strict=True)
    ]

    async def run() -> None:
        async with prepare_batch_preflight(
            spreadsheet,
            images,
            temp_dir=tmp_path / "uploads",
        ) as result:
            assert expected_code in codes(result.cases[0].issues)
            assert result.cases[0].prepared_image is None
            assert all(expected_code in codes(image.issues) for image in result.images)

    asyncio.run(run())


def test_duplicate_row_references_do_not_share_one_prepared_image(tmp_path: Path) -> None:
    spreadsheet = upload(
        csv_bytes(
            [
                ["APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL"],
                ["APP-2", "LABEL.PNG", "Brand", "Bourbon", "45", "750 mL"],
            ]
        ),
        filename="batch.csv",
    )

    async def run() -> None:
        async with prepare_batch_preflight(
            spreadsheet,
            [upload(png_bytes(), filename="label.png")],
            temp_dir=tmp_path / "uploads",
        ) as result:
            assert all(case.state == BatchCaseState.NEEDS_CORRECTION for case in result.cases)
            assert all(case.prepared_image is None for case in result.cases)
            assert result.images[0].referenced_rows == (2, 3)

    asyncio.run(run())


def test_path_components_in_selected_image_name_are_rejected(tmp_path: Path) -> None:
    spreadsheet = upload(
        csv_bytes([["APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL"]]),
        filename="batch.csv",
    )

    async def run() -> None:
        async with prepare_batch_preflight(
            spreadsheet,
            [upload(png_bytes(), filename="folder/label.png")],
            temp_dir=tmp_path / "uploads",
        ) as result:
            assert PreflightIssueCode.INVALID_IMAGE_FILENAME in codes(result.images[0].issues)
            assert PreflightIssueCode.MISSING_IMAGE in codes(result.cases[0].issues)

    asyncio.run(run())


def test_overlong_selected_image_filename_is_bounded_and_never_matched(tmp_path: Path) -> None:
    long_name = f"{'a' * 256}.png"
    spreadsheet = upload(
        csv_bytes([["APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL"]]),
        filename="batch.csv",
    )

    async def run() -> None:
        async with prepare_batch_preflight(
            spreadsheet,
            [upload(png_bytes(), filename=long_name)],
            temp_dir=tmp_path / "uploads",
        ) as result:
            image = result.images[0]
            assert len(image.filename) == 255
            assert image.normalized_filename is None
            assert PreflightIssueCode.INVALID_IMAGE_FILENAME in codes(image.issues)
            assert PreflightIssueCode.MISSING_IMAGE in codes(result.cases[0].issues)

    asyncio.run(run())


def test_structurally_invalid_spreadsheet_is_deleted_before_result_is_yielded(
    tmp_path: Path,
) -> None:
    spreadsheet = upload(b'"unterminated', filename="batch.csv")
    temp_dir = tmp_path / "uploads"

    async def run() -> None:
        async with prepare_batch_preflight(
            spreadsheet,
            [upload(png_bytes(), filename="label.png")],
            temp_dir=temp_dir,
        ) as result:
            assert result.issues[0].code == PreflightIssueCode.MALFORMED_SPREADSHEET
            assert not any(path.name.startswith("batch-workbook-") for path in temp_dir.iterdir())
            assert all(image.prepared is None for image in result.images)

    asyncio.run(run())
    assert list(temp_dir.iterdir()) == []


def test_workbook_image_count_and_aggregate_bounds_precede_decoding(tmp_path: Path) -> None:
    too_large_workbook = upload(
        b"x" * (MAX_WORKBOOK_BYTES + 1),
        filename="batch.csv",
    )
    too_many_images = [
        upload(b"bad", filename=f"{index}.png") for index in range(MAX_BATCH_IMAGES + 1)
    ]
    aggregate_spreadsheet = upload(
        csv_bytes([["APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL"]]),
        filename="batch.csv",
        reported_size=MAX_AGGREGATE_UPLOAD_BYTES,
    )
    aggregate_image = upload(b"bad", filename="label.png", reported_size=1)

    async def run() -> None:
        async with prepare_batch_preflight(
            too_large_workbook,
            [],
            temp_dir=tmp_path / "workbook",
        ) as workbook_result:
            assert codes(workbook_result.issues) == {PreflightIssueCode.WORKBOOK_TOO_LARGE}
        async with prepare_batch_preflight(
            upload(csv_bytes([]), filename="batch.csv"),
            too_many_images,
            temp_dir=tmp_path / "count",
        ) as count_result:
            assert PreflightIssueCode.TOO_MANY_IMAGES in codes(count_result.issues)
        async with prepare_batch_preflight(
            aggregate_spreadsheet,
            [aggregate_image],
            temp_dir=tmp_path / "aggregate",
        ) as aggregate_result:
            assert codes(aggregate_result.issues) == {PreflightIssueCode.AGGREGATE_UPLOAD_TOO_LARGE}

    asyncio.run(run())


def test_image_size_bound_is_reported_without_preparing_the_image(tmp_path: Path) -> None:
    spreadsheet = upload(
        csv_bytes([["APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL"]]),
        filename="batch.csv",
    )
    oversized = upload(
        b"x" * (10 * 1024 * 1024 + 1),
        filename="label.png",
    )

    async def run() -> None:
        async with prepare_batch_preflight(
            spreadsheet,
            [oversized],
            temp_dir=tmp_path / "uploads",
        ) as result:
            assert PreflightIssueCode.IMAGE_TOO_LARGE in codes(result.images[0].issues)
            assert PreflightIssueCode.IMAGE_TOO_LARGE in codes(result.cases[0].issues)
            assert result.cases[0].prepared_image is None

    asyncio.run(run())
    assert list((tmp_path / "uploads").iterdir()) == []
