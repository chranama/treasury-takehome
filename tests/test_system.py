from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from app.config import Settings
from app.extraction import OpenAIExtractionAdapter
from app.frontend import HTML_CACHE_CONTROL, HTML_CONTENT_SECURITY_POLICY
from app.main import create_app


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_path": tmp_path / "treasury.sqlite3",
        "temp_dir": tmp_path / "tmp",
        "frontend_dist_path": tmp_path / "dist",
        "extraction_backend": "fake",
        "live_extraction_enabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_health_and_readiness_initialize_local_state(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        readiness = client.get("/readyz")

    assert readiness.status_code == 200
    assert readiness.json() == {
        "status": "ready",
        "checks": {
            "configuration": "ok",
            "database": "ok",
            "temporary_storage": "ok",
        },
    }
    assert settings.database_path.is_file()
    assert settings.temp_dir.is_dir()


def test_readiness_reports_missing_openai_configuration_without_provider_call(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        tmp_path,
        extraction_backend="openai",
        live_extraction_enabled=True,
        openai_api_key=None,
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["configuration"] == "not_ready"


def test_disabled_live_extraction_keeps_static_application_ready(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        extraction_backend="openai",
        live_extraction_enabled=False,
        openai_api_key="",
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_live_extraction_requires_all_private_usage_controls(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        extraction_backend="openai",
        live_extraction_enabled=True,
        openai_api_key="test-key",
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["configuration"] == "not_ready"
    issues = settings.configuration_issues()
    assert "live extraction requires a daily attempt limit" in issues
    assert "live extraction requires a cumulative cost limit" in issues


def test_attempt_reservation_cannot_exceed_cumulative_limit(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        extraction_backend="openai",
        live_extraction_enabled=True,
        openai_api_key="test-key",
        live_daily_attempt_limit=10,
        live_cumulative_cost_limit_usd="0.01",
        live_attempt_reservation_usd="0.02",
        live_source_window_seconds=60,
        live_source_max_submissions=5,
    )

    assert (
        "attempt cost reservation cannot exceed the cumulative cost limit"
        in settings.configuration_issues()
    )


def test_application_closes_factory_owned_openai_client(
    tmp_path: Path,
    monkeypatch,
) -> None:
    close = AsyncMock()
    client = cast(AsyncOpenAI, SimpleNamespace(close=close))
    adapter = OpenAIExtractionAdapter(client=client, model="gpt-5.6-luna")
    monkeypatch.setattr("app.main.create_extraction_adapter", lambda _: adapter)
    settings = make_settings(
        tmp_path,
        extraction_backend="openai",
        live_extraction_enabled=True,
        openai_api_key="test-key",
        live_daily_attempt_limit=10,
        live_cumulative_cost_limit_usd="1",
        live_attempt_reservation_usd="0.01",
        live_source_window_seconds=60,
        live_source_max_submissions=5,
    )

    with TestClient(create_app(settings)) as test_client:
        assert test_client.get("/healthz").status_code == 200

    close.assert_awaited_once_with()


def test_root_explains_when_frontend_has_not_been_built(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.get("/")

    assert response.status_code == 503
    assert response.json()["status"] == "frontend_not_built"


def test_compiled_frontend_prevents_edge_injection_and_remote_runtime_access(
    tmp_path: Path,
) -> None:
    frontend_dist = tmp_path / "dist"
    assets = frontend_dist / "assets"
    assets.mkdir(parents=True)
    (frontend_dist / "index.html").write_text(
        '<!doctype html><script src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    (assets / "app.js").write_text("document.body.dataset.ready = 'true'", encoding="utf-8")

    settings = make_settings(tmp_path, frontend_dist_path=frontend_dist)
    with TestClient(create_app(settings)) as client:
        html_response = client.get("/")
        asset_response = client.get("/assets/app.js")

    assert html_response.status_code == 200
    assert html_response.headers["cache-control"] == HTML_CACHE_CONTROL
    assert html_response.headers["content-security-policy"] == HTML_CONTENT_SECURITY_POLICY
    assert html_response.headers["x-content-type-options"] == "nosniff"
    assert html_response.headers["referrer-policy"] == "no-referrer"
    assert asset_response.status_code == 200
    assert asset_response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in asset_response.headers
