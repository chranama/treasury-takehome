"""Additive schema migration for short-lived P1 batch content."""

BATCH_SCHEMA_VERSION = 2

CONTENT_BEARING_BATCH_TABLES = frozenset(
    {"batch_reviews", "batch_images", "batch_cases", "batch_case_results"}
)
OPERATIONAL_USAGE_TABLES = frozenset({"review_submissions", "provider_attempts"})

BATCH_SCHEMA_PROPOSAL_SQL = """
CREATE TABLE IF NOT EXISTS batch_reviews (
    batch_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (
        status IN ('draft', 'queued', 'processing', 'completed', 'interrupted')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    start_idempotency_hash TEXT UNIQUE,
    start_selection TEXT CHECK (
        start_selection IS NULL OR start_selection IN ('all_cases', 'ready_cases_only')
    ),
    started_at TEXT,
    completed_at TEXT,
    selected_case_count INTEGER CHECK (
        selected_case_count IS NULL OR selected_case_count BETWEEN 1 AND 25
    )
);

CREATE TABLE IF NOT EXISTS batch_images (
    image_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    normalized_filename TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK (media_type IN ('image/jpeg', 'image/png', 'image/webp')),
    byte_count INTEGER NOT NULL CHECK (byte_count > 0),
    width INTEGER NOT NULL CHECK (width > 0),
    height INTEGER NOT NULL CHECK (height > 0),
    status TEXT NOT NULL CHECK (status IN ('available', 'processing', 'deleted')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    deleted_at TEXT,
    cleanup_attempts INTEGER NOT NULL DEFAULT 0 CHECK (cleanup_attempts >= 0),
    cleanup_last_attempted_at TEXT,
    cleanup_last_error_kind TEXT,
    FOREIGN KEY (batch_id) REFERENCES batch_reviews(batch_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS batch_cases (
    case_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    row_number INTEGER NOT NULL CHECK (row_number >= 2),
    application_id TEXT NOT NULL,
    normalized_application_id TEXT,
    label_image_filename TEXT NOT NULL,
    normalized_label_image_filename TEXT,
    expected_brand TEXT NOT NULL,
    expected_class_type TEXT NOT NULL,
    expected_abv TEXT NOT NULL,
    expected_net_contents TEXT NOT NULL,
    normalized_expected_json TEXT,
    image_id TEXT,
    status TEXT NOT NULL CHECK (
        status IN (
            'needs_correction', 'ready', 'queued', 'processing', 'completed',
            'failed', 'interrupted', 'not_selected'
        )
    ),
    issues_json TEXT NOT NULL,
    provider_correlation_id TEXT UNIQUE,
    safe_failure_kind TEXT,
    safe_failure_reason TEXT,
    processing_duration_ms INTEGER CHECK (
        processing_duration_ms IS NULL OR processing_duration_ms >= 0
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES batch_reviews(batch_id) ON DELETE CASCADE,
    FOREIGN KEY (image_id) REFERENCES batch_images(image_id) ON DELETE SET NULL,
    FOREIGN KEY (provider_correlation_id) REFERENCES review_submissions(correlation_id),
    UNIQUE (batch_id, row_number)
);

CREATE TABLE IF NOT EXISTS batch_case_results (
    case_id TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    processing_mode TEXT NOT NULL CHECK (processing_mode IN ('synthetic', 'live')),
    completed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES batch_cases(case_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS batch_reviews_expires_at_idx ON batch_reviews (expires_at);
CREATE INDEX IF NOT EXISTS batch_images_batch_filename_idx
    ON batch_images (batch_id, normalized_filename);
CREATE INDEX IF NOT EXISTS batch_images_expiry_status_idx ON batch_images (expires_at, status);
CREATE INDEX IF NOT EXISTS batch_cases_batch_status_idx ON batch_cases (batch_id, status);
CREATE INDEX IF NOT EXISTS batch_cases_batch_application_idx
    ON batch_cases (batch_id, normalized_application_id);
CREATE INDEX IF NOT EXISTS batch_cases_expires_at_idx ON batch_cases (expires_at);
CREATE INDEX IF NOT EXISTS batch_case_results_expires_at_idx ON batch_case_results (expires_at);
"""
