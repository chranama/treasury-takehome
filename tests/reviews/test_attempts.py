import asyncio
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.db import connect, initialize_database
from app.reviews import (
    AttemptRejected,
    AttemptRejectionKind,
    AttemptSuccess,
    SQLiteUsageGate,
)


def make_gate(
    database_path: Path,
    **overrides: object,
) -> SQLiteUsageGate:
    values: dict[str, object] = {
        "database_path": database_path,
        "daily_attempt_limit": 10,
        "cumulative_cost_limit_usd": Decimal("1"),
        "attempt_reservation_usd": Decimal("0.01"),
        "source_window_seconds": 60,
        "source_max_submissions": 10,
        "model": "gpt-5.6-luna",
        "prompt_revision": "label-observations-v2",
        "image_detail": "high",
        "service_tier": "default",
    }
    values.update(overrides)
    return SQLiteUsageGate(**values)  # type: ignore[arg-type]


def success() -> AttemptSuccess:
    return AttemptSuccess(
        provider_request_id="resp_safe",
        model="gpt-5.6-luna",
        image_detail="high",
        requested_service_tier="default",
        response_service_tier="default",
        latency_ms=1234,
        input_tokens=3020,
        cached_input_tokens=3017,
        output_tokens=240,
        reasoning_tokens=0,
        total_tokens=3260,
        estimated_cost_usd=Decimal("0.00036034"),
    )


def test_successful_attempt_records_only_bounded_operational_metadata(tmp_path: Path) -> None:
    database_path = tmp_path / "usage.sqlite3"
    initialize_database(database_path)
    gate = make_gate(database_path)

    async def run() -> None:
        async with gate.submission(
            correlation_id="correlation-safe",
            idempotency_key="submission-secret-key",
            source_identity="203.0.113.10",
        ) as submission:
            async with submission.reserve_attempt() as reservation:
                await reservation.settle_success(success())
            await submission.complete(
                outcome="all_checks_passed",
                match_count=5,
                mismatch_count=0,
                needs_review_count=0,
            )

    asyncio.run(run())

    with connect(database_path) as connection:
        submission = connection.execute(
            """
            SELECT status, outcome, match_count, mismatch_count, needs_review_count
            FROM review_submissions
            """
        ).fetchone()
        attempt = connection.execute(
            """
            SELECT status, provider_request_id, model, prompt_revision, image_detail,
                   requested_service_tier, response_service_tier, latency_ms,
                   input_tokens, cached_input_tokens, output_tokens, actual_cost_units
            FROM provider_attempts
            """
        ).fetchone()

    assert submission == ("completed", "all_checks_passed", 5, 0, 0)
    assert attempt == (
        "succeeded",
        "resp_safe",
        "gpt-5.6-luna",
        "label-observations-v2",
        "high",
        "default",
        "default",
        1234,
        3020,
        3017,
        240,
        36034,
    )
    database_bytes = database_path.read_bytes()
    assert b"submission-secret-key" not in database_bytes
    assert b"203.0.113.10" not in database_bytes
    assert b"Treasury Reserve" not in database_bytes


def test_duplicate_submission_never_reserves_a_second_attempt(tmp_path: Path) -> None:
    database_path = tmp_path / "usage.sqlite3"
    initialize_database(database_path)
    gate = make_gate(database_path)

    async def run() -> None:
        async with gate.submission(
            correlation_id="first",
            idempotency_key="same-submission-key",
            source_identity="source-a",
        ) as submission:
            async with submission.reserve_attempt() as reservation:
                await reservation.settle_success()
            await submission.complete(
                outcome="all_checks_passed",
                match_count=5,
                mismatch_count=0,
                needs_review_count=0,
            )

        with pytest.raises(AttemptRejected) as captured:
            async with gate.submission(
                correlation_id="second",
                idempotency_key="same-submission-key",
                source_identity="source-a",
            ):
                pass
        assert captured.value.kind == AttemptRejectionKind.DUPLICATE_SUBMISSION

    asyncio.run(run())
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM provider_attempts").fetchone() == (1,)


def test_retry_has_a_separate_reservation_and_is_bounded_at_two(tmp_path: Path) -> None:
    database_path = tmp_path / "usage.sqlite3"
    initialize_database(database_path)
    gate = make_gate(database_path, max_attempts_per_submission=2)

    async def run() -> None:
        async with gate.submission(
            correlation_id="retry-review",
            idempotency_key="retry-submission-key",
            source_identity="source-a",
        ) as submission:
            async with submission.reserve_attempt() as first:
                await first.settle_failure("transient_failure")
            async with submission.reserve_attempt() as second:
                await second.settle_success(success())
            with pytest.raises(AttemptRejected) as captured:
                async with submission.reserve_attempt():
                    pass
            assert captured.value.kind == AttemptRejectionKind.CAPACITY_REACHED
            await submission.complete(
                outcome="all_checks_passed",
                match_count=5,
                mismatch_count=0,
                needs_review_count=0,
            )

    asyncio.run(run())
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT attempt_number, status, error_kind
            FROM provider_attempts ORDER BY attempt_number
            """
        ).fetchall()
    assert rows == [(1, "failed", "transient_failure"), (2, "succeeded", None)]


def test_concurrent_reservations_cannot_exceed_cumulative_ceiling(tmp_path: Path) -> None:
    database_path = tmp_path / "usage.sqlite3"
    initialize_database(database_path)
    gate = make_gate(
        database_path,
        cumulative_cost_limit_usd=Decimal("0.01"),
        attempt_reservation_usd=Decimal("0.01"),
    )

    async def contender(index: int) -> bool:
        try:
            async with gate.submission(
                correlation_id=f"concurrent-{index}",
                idempotency_key=f"concurrent-key-{index}",
                source_identity=f"source-{index}",
            ) as submission:
                async with submission.reserve_attempt() as reservation:
                    await reservation.settle_failure("test_failure")
                await submission.fail("test_failure")
            return True
        except AttemptRejected as error:
            assert error.kind == AttemptRejectionKind.CAPACITY_REACHED
            return False

    async def run() -> list[bool]:
        return list(await asyncio.gather(contender(1), contender(2)))

    results = asyncio.run(run())
    assert sum(results) == 1
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM provider_attempts").fetchone() == (1,)


def test_cumulative_ceiling_survives_gate_recreation(tmp_path: Path) -> None:
    database_path = tmp_path / "usage.sqlite3"
    initialize_database(database_path)

    async def consume(gate: SQLiteUsageGate) -> None:
        async with gate.submission(
            correlation_id="before-restart",
            idempotency_key="before-restart-key",
            source_identity="source-a",
        ) as submission:
            async with submission.reserve_attempt() as reservation:
                await reservation.settle_failure("provider_timeout")
            await submission.fail("provider_timeout")

    asyncio.run(
        consume(
            make_gate(
                database_path,
                cumulative_cost_limit_usd=Decimal("0.01"),
                attempt_reservation_usd=Decimal("0.01"),
            )
        )
    )

    recreated = make_gate(
        database_path,
        cumulative_cost_limit_usd=Decimal("0.01"),
        attempt_reservation_usd=Decimal("0.01"),
    )

    async def rejected_after_restart() -> None:
        with pytest.raises(AttemptRejected) as captured:
            async with recreated.submission(
                correlation_id="after-restart",
                idempotency_key="after-restart-key",
                source_identity="source-b",
            ) as submission:
                async with submission.reserve_attempt():
                    pass
        assert captured.value.kind == AttemptRejectionKind.CAPACITY_REACHED

    asyncio.run(rejected_after_restart())


def test_daily_attempt_allowance_is_durable(tmp_path: Path) -> None:
    database_path = tmp_path / "usage.sqlite3"
    initialize_database(database_path)
    gate = make_gate(database_path, daily_attempt_limit=1)

    async def run() -> None:
        async with gate.submission(
            correlation_id="daily-first",
            idempotency_key="daily-key-one",
            source_identity="source-a",
        ) as submission:
            async with submission.reserve_attempt() as reservation:
                await reservation.settle_failure("provider_timeout")
            await submission.fail("provider_timeout")

        with pytest.raises(AttemptRejected) as captured:
            async with gate.submission(
                correlation_id="daily-second",
                idempotency_key="daily-key-two",
                source_identity="source-b",
            ) as submission:
                async with submission.reserve_attempt():
                    pass
        assert captured.value.kind == AttemptRejectionKind.CAPACITY_REACHED

    asyncio.run(run())


def test_global_concurrency_is_two(tmp_path: Path) -> None:
    database_path = tmp_path / "usage.sqlite3"
    initialize_database(database_path)
    gate = make_gate(database_path)

    async def run() -> None:
        release = asyncio.Event()
        entered = [asyncio.Event(), asyncio.Event()]

        async def hold(index: int) -> None:
            async with gate.submission(
                correlation_id=f"held-{index}",
                idempotency_key=f"held-key-{index}",
                source_identity=f"source-{index}",
            ) as submission:
                entered[index].set()
                await release.wait()
                await submission.fail("test_complete")

        tasks = [asyncio.create_task(hold(0)), asyncio.create_task(hold(1))]
        await asyncio.gather(*(event.wait() for event in entered))
        with pytest.raises(AttemptRejected) as captured:
            async with gate.submission(
                correlation_id="third",
                idempotency_key="third-key",
                source_identity="source-third",
            ):
                pass
        assert captured.value.kind == AttemptRejectionKind.CAPACITY_REACHED
        release.set()
        await asyncio.gather(*tasks)

    asyncio.run(run())


def test_internal_batch_cases_and_public_reviews_share_global_concurrency(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "usage.sqlite3"
    initialize_database(database_path)
    gate = make_gate(database_path)

    async def run() -> None:
        release = asyncio.Event()
        entered = [asyncio.Event(), asyncio.Event()]

        async def hold_internal(index: int) -> None:
            async with gate.internal_submission(
                correlation_id=f"batch-case-{index}",
                idempotency_key=f"batch-case-key-{index}",
            ) as submission:
                entered[index].set()
                await release.wait()
                await submission.fail("test_complete")

        tasks = [asyncio.create_task(hold_internal(0)), asyncio.create_task(hold_internal(1))]
        await asyncio.gather(*(event.wait() for event in entered))
        with pytest.raises(AttemptRejected) as captured:
            async with gate.submission(
                correlation_id="public-review",
                idempotency_key="public-review-key",
                source_identity="source-public",
            ):
                pass
        assert captured.value.kind == AttemptRejectionKind.CAPACITY_REACHED
        release.set()
        await asyncio.gather(*tasks)

    asyncio.run(run())


def test_source_throttle_is_distinct_and_does_not_store_source(tmp_path: Path) -> None:
    database_path = tmp_path / "usage.sqlite3"
    initialize_database(database_path)
    gate = make_gate(database_path, source_max_submissions=1)

    async def run() -> None:
        async with gate.submission(
            correlation_id="first-source-request",
            idempotency_key="source-key-one",
            source_identity="198.51.100.9",
        ) as submission:
            await submission.fail("test_complete")

        with pytest.raises(AttemptRejected) as captured:
            async with gate.submission(
                correlation_id="second-source-request",
                idempotency_key="source-key-two",
                source_identity="198.51.100.9",
            ):
                pass
        assert captured.value.kind == AttemptRejectionKind.TRAFFIC_THROTTLED

    asyncio.run(run())
    assert b"198.51.100.9" not in database_path.read_bytes()


def test_startup_reconciles_interrupted_rows_conservatively(tmp_path: Path) -> None:
    database_path = tmp_path / "usage.sqlite3"
    initialize_database(database_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO review_submissions (
                idempotency_hash, correlation_id, status, created_at
            ) VALUES ('hash', 'crashed', 'processing', '2026-08-11T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO provider_attempts (
                correlation_id, attempt_number, status, reserved_at,
                reserved_cost_units, model, prompt_revision, image_detail,
                requested_service_tier
            ) VALUES (
                'crashed', 1, 'reserved', '2026-08-11T00:00:00+00:00',
                1000000, 'gpt-5.6-luna', 'label-observations-v2', 'high', 'default'
            )
            """
        )

    gate = make_gate(database_path)
    asyncio.run(gate.reconcile_incomplete())

    with connect(database_path) as connection:
        submission = connection.execute(
            "SELECT status, error_kind FROM review_submissions WHERE correlation_id = 'crashed'"
        ).fetchone()
        attempt = connection.execute(
            "SELECT status, error_kind, actual_cost_units FROM provider_attempts"
        ).fetchone()
    assert submission == ("failed", "interrupted")
    assert attempt == ("failed", "interrupted", None)


def test_schema_rejects_duplicate_attempt_numbers(tmp_path: Path) -> None:
    database_path = tmp_path / "usage.sqlite3"
    initialize_database(database_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO review_submissions (
                idempotency_hash, correlation_id, status, created_at
            ) VALUES ('hash', 'duplicate-attempt', 'processing', '2026-08-11T00:00:00+00:00')
            """
        )
        values = (
            "duplicate-attempt",
            1,
            "reserved",
            "2026-08-11T00:00:00+00:00",
            100,
            "gpt-5.6-luna",
            "label-observations-v2",
            "high",
            "default",
        )
        connection.execute(
            """
            INSERT INTO provider_attempts (
                correlation_id, attempt_number, status, reserved_at, reserved_cost_units,
                model, prompt_revision, image_detail, requested_service_tier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO provider_attempts (
                    correlation_id, attempt_number, status, reserved_at,
                    reserved_cost_units, model, prompt_revision, image_detail,
                    requested_service_tier
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
