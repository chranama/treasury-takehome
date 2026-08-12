import sqlite3
from pathlib import Path

from app.batches.schema import BATCH_SCHEMA_PROPOSAL_SQL, BATCH_SCHEMA_VERSION

_BASE_SCHEMA_VERSION = 1
_BASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_submissions (
    idempotency_hash TEXT PRIMARY KEY,
    correlation_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    outcome TEXT,
    match_count INTEGER CHECK (match_count IS NULL OR match_count >= 0),
    mismatch_count INTEGER CHECK (mismatch_count IS NULL OR mismatch_count >= 0),
    needs_review_count INTEGER CHECK (
        needs_review_count IS NULL OR needs_review_count >= 0
    ),
    error_kind TEXT
);

CREATE TABLE IF NOT EXISTS provider_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    status TEXT NOT NULL CHECK (status IN ('reserved', 'succeeded', 'failed')),
    reserved_at TEXT NOT NULL,
    settled_at TEXT,
    reserved_cost_units INTEGER NOT NULL CHECK (reserved_cost_units > 0),
    actual_cost_units INTEGER CHECK (
        actual_cost_units IS NULL OR actual_cost_units >= 0
    ),
    provider_request_id TEXT,
    model TEXT NOT NULL,
    prompt_revision TEXT NOT NULL,
    image_detail TEXT NOT NULL,
    requested_service_tier TEXT NOT NULL,
    response_service_tier TEXT,
    latency_ms INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
    input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
    cached_input_tokens INTEGER CHECK (
        cached_input_tokens IS NULL OR cached_input_tokens >= 0
    ),
    output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
    reasoning_tokens INTEGER CHECK (reasoning_tokens IS NULL OR reasoning_tokens >= 0),
    total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
    error_kind TEXT,
    FOREIGN KEY (correlation_id) REFERENCES review_submissions(correlation_id),
    UNIQUE (correlation_id, attempt_number)
);

CREATE INDEX IF NOT EXISTS provider_attempts_reserved_at_idx
    ON provider_attempts (reserved_at);
"""


def connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=2)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 2000")
    return connection


def initialize_database(database_path: Path) -> None:
    """Create the base schema and transactionally apply every additive migration."""

    database_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(database_path) as connection:
        connection.executescript(_BASE_SCHEMA_SQL)
        row = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO app_metadata (key, value) VALUES ('schema_version', ?)",
                (str(_BASE_SCHEMA_VERSION),),
            )
            connection.commit()
            current_version = _BASE_SCHEMA_VERSION
        else:
            try:
                current_version = int(row[0])
            except (TypeError, ValueError) as error:
                raise RuntimeError("database schema version is invalid") from error

        if current_version > BATCH_SCHEMA_VERSION:
            raise RuntimeError("database schema is newer than this application")
        if current_version < _BASE_SCHEMA_VERSION:
            raise RuntimeError("database schema version is unsupported")
        if current_version < BATCH_SCHEMA_VERSION:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + BATCH_SCHEMA_PROPOSAL_SQL
                + (
                    f"\nUPDATE app_metadata SET value = '{BATCH_SCHEMA_VERSION}' "
                    "WHERE key = 'schema_version';\n"
                )
                + "COMMIT;"
            )


def database_is_ready(database_path: Path) -> bool:
    try:
        with connect(database_path) as connection:
            row = connection.execute(
                "SELECT value FROM app_metadata WHERE key = 'schema_version'"
            ).fetchone()
        return row == (str(BATCH_SCHEMA_VERSION),)
    except (OSError, sqlite3.Error):
        return False
