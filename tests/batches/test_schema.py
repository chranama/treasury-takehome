import ast
from pathlib import Path

from app.batches import (
    BATCH_SCHEMA_PROPOSAL_SQL,
    BATCH_SCHEMA_VERSION,
    CONTENT_BEARING_BATCH_TABLES,
    OPERATIONAL_USAGE_TABLES,
)
from app.db import connect, initialize_database

BATCH_ROOT = Path(__file__).resolve().parents[2] / "app" / "batches"


def test_batch_modules_do_not_import_openai_sdk() -> None:
    violations: list[str] = []
    for path in sorted(BATCH_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            if any(name == "openai" or name.startswith("openai.") for name in imported):
                violations.append(f"{path.name}:{node.lineno}")

    assert not violations, violations


def test_batch_schema_is_applied_additively_and_preserves_usage_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "treasury.sqlite3"
    initialize_database(database_path)

    with connect(database_path) as connection:
        connection.executescript(BATCH_SCHEMA_PROPOSAL_SQL)
        version = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    assert BATCH_SCHEMA_VERSION == 2
    assert version == "2"
    assert tables >= CONTENT_BEARING_BATCH_TABLES
    assert tables >= OPERATIONAL_USAGE_TABLES
    assert CONTENT_BEARING_BATCH_TABLES.isdisjoint(OPERATIONAL_USAGE_TABLES)


def test_version_one_database_migrates_without_losing_operational_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "treasury.sqlite3"
    initialize_database(database_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO review_submissions (
                idempotency_hash, correlation_id, status, created_at
            ) VALUES ('hash', 'correlation', 'completed', '2026-08-12T12:00:00+00:00')
            """
        )
        for table in (
            "batch_case_results",
            "batch_cases",
            "batch_images",
            "batch_reviews",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("UPDATE app_metadata SET value = '1' WHERE key = 'schema_version'")

    initialize_database(database_path)

    with connect(database_path) as connection:
        operational_row = connection.execute(
            "SELECT correlation_id, status FROM review_submissions"
        ).fetchone()
        version = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
        case_columns = {row[1] for row in connection.execute("PRAGMA table_info(batch_cases)")}

    assert operational_row == ("correlation", "completed")
    assert version == "2"
    assert {"label_image_filename", "normalized_label_image_filename"} <= case_columns


def test_content_fields_do_not_enter_operational_usage_tables() -> None:
    operational_section = BATCH_SCHEMA_PROPOSAL_SQL.casefold()

    for forbidden in ("expected_brand", "original_filename", "result_json"):
        assert forbidden in operational_section
    assert "alter table review_submissions" not in operational_section
    assert "alter table provider_attempts" not in operational_section
