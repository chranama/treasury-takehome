import ast
import inspect
from pathlib import Path
from typing import get_type_hints

import pytest

from app.comparison.models import ExtractionObservations
from app.extraction import (
    ExtractionAdapter,
    ExtractionError,
    ExtractionErrorKind,
    ImageMediaType,
    PreparedImage,
)

EXTRACTION_ROOT = Path(__file__).resolve().parents[2] / "app" / "extraction"
BOUNDARY_FILES = (EXTRACTION_ROOT / "contract.py", EXTRACTION_ROOT / "fake.py")
FORBIDDEN_IMPORTS = {"fastapi", "openai", "sqlite3"}
FORBIDDEN_EXPECTED_MODELS = {"ExpectedNetContents", "ExpectedReview"}


def test_prepared_image_requires_positive_metadata(tmp_path: Path) -> None:
    image = PreparedImage(
        path=tmp_path / "prepared.png",
        media_type=ImageMediaType.PNG,
        width=1200,
        height=800,
        byte_count=4096,
    )

    assert image.media_type == ImageMediaType.PNG

    with pytest.raises(ValueError, match="dimensions must be positive"):
        PreparedImage(
            path=tmp_path / "invalid.png",
            media_type=ImageMediaType.PNG,
            width=0,
            height=800,
            byte_count=4096,
        )

    with pytest.raises(ValueError, match="byte count must be positive"):
        PreparedImage(
            path=tmp_path / "invalid.png",
            media_type=ImageMediaType.PNG,
            width=1200,
            height=800,
            byte_count=0,
        )


def test_extraction_error_has_bounded_safe_contract() -> None:
    error = ExtractionError(
        kind=ExtractionErrorKind.TIMEOUT,
        safe_message=" Extraction timed out. ",
        retryable=True,
    )

    assert error.kind == ExtractionErrorKind.TIMEOUT
    assert error.safe_message == "Extraction timed out."
    assert error.retryable is True
    assert str(error) == "Extraction timed out."

    with pytest.raises(ValueError, match="requires a safe message"):
        ExtractionError(
            kind=ExtractionErrorKind.INTERNAL_FAILURE,
            safe_message="  ",
            retryable=False,
        )

    with pytest.raises(ValueError, match="cannot exceed 300"):
        ExtractionError(
            kind=ExtractionErrorKind.INTERNAL_FAILURE,
            safe_message="x" * 301,
            retryable=False,
        )


def test_extraction_interface_accepts_only_a_prepared_image() -> None:
    signature = inspect.signature(ExtractionAdapter.extract)
    type_hints = get_type_hints(ExtractionAdapter.extract)

    assert list(signature.parameters) == ["self", "image"]
    assert type_hints["image"] is PreparedImage
    assert type_hints["return"] is ExtractionObservations


def test_extraction_contract_and_fake_are_infrastructure_independent() -> None:
    violations: list[str] = []
    for path in BOUNDARY_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported_names: set[str] = set()
            imported_roots: set[str] = set()
            if isinstance(node, ast.Import):
                imported_roots = {alias.name.partition(".")[0] for alias in node.names}
                imported_names = {alias.asname or alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots = {node.module.partition(".")[0]}
                imported_names = {alias.name for alias in node.names}

            for forbidden in sorted(imported_roots & FORBIDDEN_IMPORTS):
                violations.append(f"{path.name}:{node.lineno} imports {forbidden}")
            for forbidden in sorted(imported_names & FORBIDDEN_EXPECTED_MODELS):
                violations.append(f"{path.name}:{node.lineno} imports {forbidden}")

    assert not violations, violations
