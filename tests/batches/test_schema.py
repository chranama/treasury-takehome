import ast
import sqlite3
from pathlib import Path

import pytest

import app.db as database_module
from app.batches import (
    BATCH_SCHEMA_PROPOSAL_SQL,
    BATCH_SCHEMA_VERSION,
    CONTENT_BEARING_BATCH_TABLES,
    OPERATIONAL_USAGE_TABLES,
)
from app.db import connect, database_is_ready, initialize_database

BATCH_ROOT = Path(__file__).resolve().parents[2] / "app" / "batches"


def downgrade_to_version_one(database_path: Path) -> None:
    initialize_database(database_path)
    with connect(database_path) as connection:
        for table in (
            "batch_case_results",
            "batch_cases",
            "batch_images",
            "batch_reviews",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("UPDATE app_metadata SET value = '1' WHERE key = 'schema_version'")


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
    downgrade_to_version_one(database_path)

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


def test_repeated_current_schema_initialization_preserves_existing_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "treasury.sqlite3"
    initialize_database(database_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO review_submissions (
                idempotency_hash, correlation_id, status, created_at
            ) VALUES ('repeat-hash', 'repeat-correlation', 'completed', '2026-08-12T12:00:00Z')
            """
        )

    initialize_database(database_path)

    with connect(database_path) as connection:
        version = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()
        row = connection.execute("SELECT correlation_id, status FROM review_submissions").fetchone()

    assert version == ("2",)
    assert row == ("repeat-correlation", "completed")


@pytest.mark.parametrize(
    ("version", "message"),
    [
        ("not-a-number", "database schema version is invalid"),
        ("0", "database schema version is unsupported"),
        ("3", "database schema is newer than this application"),
    ],
)
def test_invalid_or_incompatible_schema_versions_fail_closed(
    tmp_path: Path,
    version: str,
    message: str,
) -> None:
    database_path = tmp_path / "treasury.sqlite3"
    initialize_database(database_path)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE app_metadata SET value = ? WHERE key = 'schema_version'",
            (version,),
        )

    with pytest.raises(RuntimeError, match=message):
        initialize_database(database_path)


def test_failed_migration_rolls_back_partial_schema_and_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "treasury.sqlite3"
    downgrade_to_version_one(database_path)
    broken_migration = """
    CREATE TABLE partial_migration_marker (id INTEGER PRIMARY KEY);
    INSERT INTO missing_migration_table (id) VALUES (1);
    """
    monkeypatch.setattr(database_module, "BATCH_SCHEMA_PROPOSAL_SQL", broken_migration)

    with pytest.raises(sqlite3.OperationalError):
        initialize_database(database_path)

    with connect(database_path) as connection:
        version = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    assert version == ("1",)
    assert "partial_migration_marker" not in tables
    assert CONTENT_BEARING_BATCH_TABLES.isdisjoint(tables)


def test_database_readiness_requires_current_initialized_schema(tmp_path: Path) -> None:
    uninitialized_path = tmp_path / "uninitialized.sqlite3"
    assert database_is_ready(uninitialized_path) is False

    unavailable_path = tmp_path / "database-directory"
    unavailable_path.mkdir()
    assert database_is_ready(unavailable_path) is False

    current_path = tmp_path / "current.sqlite3"
    initialize_database(current_path)
    assert database_is_ready(current_path) is True

    with connect(current_path) as connection:
        connection.execute("UPDATE app_metadata SET value = '1' WHERE key = 'schema_version'")
    assert database_is_ready(current_path) is False


def test_content_fields_do_not_enter_operational_usage_tables() -> None:
    operational_section = BATCH_SCHEMA_PROPOSAL_SQL.casefold()

    for forbidden in ("expected_brand", "original_filename", "result_json"):
        assert forbidden in operational_section
    assert "alter table review_submissions" not in operational_section
    assert "alter table provider_attempts" not in operational_section
