from evals.tier_benchmark import compare_tiers, tier_order


def test_tier_order_alternates_first_request() -> None:
    assert tier_order(0, 0) == ("default", "fast")
    assert tier_order(0, 1) == ("fast", "default")
    assert tier_order(1, 0) == ("fast", "default")
    assert tier_order(1, 1) == ("default", "fast")


def test_compare_tiers_reports_latency_changes_and_cost_ratio() -> None:
    standard = {
        "median_latency_ms": 2_000,
        "p90_latency_ms": 4_000,
        "p95_latency_ms": 5_000,
        "total_estimated_cost_usd": "0.01",
    }
    fast = {
        "median_latency_ms": 1_000,
        "p90_latency_ms": 2_000,
        "p95_latency_ms": 4_000,
        "total_estimated_cost_usd": "0.02",
    }

    assert compare_tiers(standard, fast) == {
        "median_latency_change_percent": -50.0,
        "p90_latency_change_percent": -50.0,
        "p95_latency_change_percent": -20.0,
        "cost_ratio_fast_to_standard": 2.0,
    }
