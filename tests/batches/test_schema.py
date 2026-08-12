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


def test_schema_proposal_is_additive_valid_sql_and_preserves_usage_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "treasury.sqlite3"
    initialize_database(database_path)

    with connect(database_path) as connection:
        connection.executescript(BATCH_SCHEMA_PROPOSAL_SQL)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    assert BATCH_SCHEMA_VERSION == 2
    assert tables >= CONTENT_BEARING_BATCH_TABLES
    assert tables >= OPERATIONAL_USAGE_TABLES
    assert CONTENT_BEARING_BATCH_TABLES.isdisjoint(OPERATIONAL_USAGE_TABLES)


def test_content_fields_do_not_enter_operational_usage_tables() -> None:
    operational_section = BATCH_SCHEMA_PROPOSAL_SQL.casefold()

    for forbidden in ("expected_brand", "original_filename", "result_json"):
        assert forbidden in operational_section
    assert "alter table review_submissions" not in operational_section
    assert "alter table provider_attempts" not in operational_section
