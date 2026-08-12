import asyncio
import csv
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from openpyxl import load_workbook
from PIL import Image

from app.batches import (
    BATCH_TEMPLATE_HEADERS,
    PreflightIssueCode,
    generate_xlsx_template,
    parse_spreadsheet,
    prepare_batch_preflight,
)


def csv_bytes(rows: list[list[str]]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(BATCH_TEMPLATE_HEADERS)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def xlsx_bytes(rows: list[list[str]]) -> bytes:
    workbook = load_workbook(BytesIO(generate_xlsx_template()))
    for row in rows:
        workbook["Batch"].append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def png_bytes(color: str = "navy") -> bytes:
    image = Image.new("RGB", (40, 24), color=color)
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


def upload(content: bytes, filename: str) -> UploadFile:
    return UploadFile(file=BytesIO(content), filename=filename, size=len(content))


def valid_rows(count: int) -> list[list[str]]:
    return [
        [
            f"APP-{index:02d}",
            f"label-{index:02d}.png",
            "Treasury Reserve",
            "Kentucky Straight Bourbon Whiskey",
            "45%",
            "0.75 L",
        ]
        for index in range(1, count + 1)
    ]


@pytest.mark.parametrize("case_count", [2, 5, 25])
def test_generated_valid_csv_and_xlsx_package_sizes_parse_identically(
    tmp_path: Path,
    case_count: int,
) -> None:
    rows = valid_rows(case_count)
    csv_path = tmp_path / f"valid-{case_count}.csv"
    xlsx_path = tmp_path / f"valid-{case_count}.xlsx"
    csv_path.write_bytes(csv_bytes(rows))
    xlsx_path.write_bytes(xlsx_bytes(rows))

    parsed_csv = parse_spreadsheet(csv_path, filename=csv_path.name)
    parsed_xlsx = parse_spreadsheet(xlsx_path, filename=xlsx_path.name)

    assert parsed_csv.issues == parsed_xlsx.issues == ()
    assert len(parsed_csv.rows) == len(parsed_xlsx.rows) == case_count
    assert parsed_csv.rows == parsed_xlsx.rows

    async def preflight_both_formats() -> None:
        packages = (
            (csv_path.name, csv_path.read_bytes()),
            (xlsx_path.name, xlsx_path.read_bytes()),
        )
        for filename, content in packages:
            images = [
                upload(png_bytes(), f"label-{index:02d}.png")
                for index in range(1, case_count + 1)
            ]
            async with prepare_batch_preflight(
                upload(content, filename),
                images,
                temp_dir=tmp_path / f"prepared-{filename}",
            ) as prepared:
                assert prepared.issues == (), filename
                assert prepared.ready_case_count == case_count
                assert prepared.correction_case_count == 0

    asyncio.run(preflight_both_formats())


@dataclass(frozen=True)
class GeneratedPreflightCase:
    name: str
    rows: list[list[str]]
    images: list[tuple[str, bytes]]
    expected_codes: frozenset[PreflightIssueCode]
    ready_count: int


BASE_ROW = ["APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL"]
GENERATED_PREFLIGHT_MATRIX = (
    GeneratedPreflightCase(
        "missing-image",
        [BASE_ROW],
        [],
        frozenset({PreflightIssueCode.MISSING_IMAGE}),
        0,
    ),
    GeneratedPreflightCase(
        "extra-unreferenced-image",
        [BASE_ROW],
        [("label.png", png_bytes()), ("unused.png", png_bytes("green"))],
        frozenset({PreflightIssueCode.UNREFERENCED_IMAGE}),
        1,
    ),
    GeneratedPreflightCase(
        "duplicate-application-id",
        [BASE_ROW, [" app-1 ", "two.png", "Brand", "Bourbon", "45", "750 mL"]],
        [("label.png", png_bytes()), ("two.png", png_bytes("blue"))],
        frozenset({PreflightIssueCode.DUPLICATE_APPLICATION_ID}),
        0,
    ),
    GeneratedPreflightCase(
        "duplicate-image-reference",
        [BASE_ROW, ["APP-2", "LABEL.PNG", "Brand", "Bourbon", "45", "750 mL"]],
        [("label.png", png_bytes())],
        frozenset({PreflightIssueCode.DUPLICATE_IMAGE_FILENAME}),
        0,
    ),
    GeneratedPreflightCase(
        "unicode-filename-match",
        [["APP-1", "cafe\u0301.png", "Brand", "Bourbon", "45", "750 mL"]],
        [(" CAFÉ.PNG ", png_bytes())],
        frozenset(),
        1,
    ),
    GeneratedPreflightCase(
        "ambiguous-filename-collision",
        [BASE_ROW],
        [("Label.PNG", png_bytes()), ("label.png", png_bytes("blue"))],
        frozenset({PreflightIssueCode.AMBIGUOUS_IMAGE_FILENAME}),
        0,
    ),
    GeneratedPreflightCase(
        "invalid-values",
        [["APP-1", "label.png", "Brand", "Bourbon", "101", "750 oz"]],
        [("label.png", png_bytes())],
        frozenset(
            {PreflightIssueCode.INVALID_ABV, PreflightIssueCode.INVALID_NET_CONTENTS}
        ),
        0,
    ),
    GeneratedPreflightCase(
        "corrupt-image",
        [BASE_ROW],
        [("label.png", b"not an image")],
        frozenset({PreflightIssueCode.UNSUPPORTED_IMAGE}),
        0,
    ),
    GeneratedPreflightCase(
        "over-case-limit",
        valid_rows(26),
        [],
        frozenset({PreflightIssueCode.TOO_MANY_CASES}),
        0,
    ),
    GeneratedPreflightCase(
        "over-image-limit",
        [BASE_ROW],
        [(f"image-{index}.png", png_bytes()) for index in range(26)],
        frozenset({PreflightIssueCode.TOO_MANY_IMAGES}),
        0,
    ),
)


@pytest.mark.parametrize("fixture", GENERATED_PREFLIGHT_MATRIX, ids=lambda fixture: fixture.name)
def test_generated_preflight_fixture_matrix_is_deterministic(
    tmp_path: Path,
    fixture: GeneratedPreflightCase,
) -> None:
    async def run() -> None:
        async with prepare_batch_preflight(
            upload(csv_bytes(fixture.rows), f"{fixture.name}.csv"),
            [upload(content, filename) for filename, content in fixture.images],
            temp_dir=tmp_path / fixture.name,
        ) as parsed:
            issue_codes = {
                issue.code
                for issue in [
                    *parsed.issues,
                    *(issue for case in parsed.cases for issue in case.issues),
                    *(issue for image in parsed.images for issue in image.issues),
                ]
            }
            assert fixture.expected_codes <= issue_codes
            assert parsed.ready_case_count == fixture.ready_count

    asyncio.run(run())
