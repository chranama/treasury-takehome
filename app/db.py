import sqlite3
from pathlib import Path


def connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=2)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 2000")
    return connection


def initialize_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO app_metadata (key, value)
            VALUES ('schema_version', '0')
            ON CONFLICT(key) DO NOTHING
            """
        )


def database_is_ready(database_path: Path) -> bool:
    try:
        with connect(database_path) as connection:
            row = connection.execute("SELECT 1").fetchone()
        return row == (1,)
    except (OSError, sqlite3.Error):
        return False
