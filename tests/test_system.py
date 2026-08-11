from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from app.config import Settings
from app.extraction import OpenAIExtractionAdapter
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
    )

    with TestClient(create_app(settings)) as test_client:
        assert test_client.get("/healthz").status_code == 200

    close.assert_awaited_once_with()


def test_root_explains_when_frontend_has_not_been_built(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.get("/")

    assert response.status_code == 503
    assert response.json()["status"] == "frontend_not_built"
