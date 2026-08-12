"""Run a bounded P0 smoke review without printing submitted or extracted content."""

from __future__ import annotations

import argparse
import json
import tempfile
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.api.reviews import ReviewResponse
from evals.fixtures import EvaluationCase, load_manifest, render_fixture

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "fixtures" / "live-evaluation-v1.json"
BASE_URLS = {
    "local": "http://127.0.0.1:8081",
    "public": "https://label-review.mealcheck.dev",
}


def _multipart(case: EvaluationCase, image_bytes: bytes) -> tuple[bytes, str]:
    boundary = f"treasury-smoke-{uuid.uuid4().hex}"
    body = bytearray()

    def field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    expected = case.expected
    field("brand_name", expected.brand_name)
    field("class_type", expected.class_type)
    field("expected_abv", str(expected.abv))
    field("expected_net_contents", str(expected.net_contents.value))
    field("expected_net_contents_unit", expected.net_contents.unit.value)
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="image"; filename="synthetic-label.png"\r\n')
    body.extend(b"Content-Type: image/png\r\n\r\n")
    body.extend(image_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _case(case_id: str) -> EvaluationCase:
    manifest = load_manifest(MANIFEST_PATH)
    for case in manifest.cases:
        if case.id == case_id:
            return case
    available = ", ".join(item.id for item in manifest.cases)
    raise SystemExit(f"unknown fixture; choose one of: {available}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=BASE_URLS)
    parser.add_argument("--fixture", default="clear-matching-label")
    parser.add_argument(
        "--confirm-live-request",
        action="store_true",
        help="Acknowledge that this smoke test can incur one provider attempt and one retry.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.confirm_live_request:
        raise SystemExit("Refusing a potentially paid request without --confirm-live-request")

    case = _case(args.fixture)
    with tempfile.TemporaryDirectory(prefix="treasury-smoke-") as directory:
        prepared = render_fixture(case, Path(directory) / "fixture.png")
        body, content_type = _multipart(case, prepared.path.read_bytes())

    request = Request(
        f"{BASE_URLS[args.target]}/api/reviews",
        data=body,
        method="POST",
        headers={
            "Content-Type": content_type,
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed allowlisted URLs
            payload = json.load(response)
    except HTTPError as error:
        try:
            payload = json.load(error)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        category = payload.get("category", "unknown")
        correlation = payload.get("correlation_id", "unavailable")
        raise SystemExit(
            f"smoke request failed: http={error.code} category={category} "
            f"correlation_id={correlation}"
        ) from error
    except URLError as error:
        raise SystemExit("smoke request could not reach the configured service") from error

    result = ReviewResponse.model_validate(payload)
    if result.processing_mode != "live":
        raise SystemExit("smoke request did not use live extraction")
    if result.outcome != case.expected_outcome:
        raise SystemExit("smoke outcome did not match the fixture contract")

    statuses = {check.name.value: check.status.value for check in result.checks}
    for field, expected_status in case.required_checks.items():
        if statuses.get(field.value) != expected_status.value:
            raise SystemExit("smoke check statuses did not match the fixture contract")

    counts: dict[str, int] = {}
    for status in statuses.values():
        counts[status] = counts.get(status, 0) + 1
    print(
        "smoke passed",
        f"target={args.target}",
        f"fixture={case.id}",
        f"outcome={result.outcome.value}",
        f"duration_ms={result.processing_duration_ms}",
        f"status_counts={json.dumps(counts, sort_keys=True)}",
        f"correlation_id={result.correlation_id}",
    )


if __name__ == "__main__":
    main()
