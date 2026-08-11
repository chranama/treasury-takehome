import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.request_limits import RequestBodyLimitMiddleware


def limited_app(*, limit: int = 5) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, path_limits={"/consume": limit})

    @app.post("/consume")
    async def consume(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    @app.post("/unlimited")
    async def unlimited(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    return app


def test_content_length_is_rejected_before_body_parsing() -> None:
    with TestClient(limited_app()) as client:
        response = client.post("/consume", content=b"123456")

    assert response.status_code == 413
    assert response.json() == {
        "category": "invalid_input",
        "message": "The review request exceeds the allowed upload size.",
    }


def test_request_body_limit_requires_positive_values() -> None:
    with (
        pytest.raises(ValueError, match="request body limits must be positive"),
        TestClient(limited_app(limit=0)),
    ):
        pass


def test_streamed_body_is_bounded_without_content_length() -> None:
    def body() -> list[bytes]:
        return [b"123", b"456"]

    with TestClient(limited_app()) as client:
        response = client.post(
            "/consume",
            content=iter(body()),
            headers={"transfer-encoding": "chunked"},
        )

    assert response.status_code == 413


def test_request_at_limit_and_unselected_path_pass_through() -> None:
    with TestClient(limited_app()) as client:
        at_limit = client.post("/consume", content=b"12345")
        unselected = client.post("/unlimited", content=b"123456")

    assert at_limit.status_code == 200
    assert at_limit.json() == {"received": 5}
    assert unselected.status_code == 200
    assert unselected.json() == {"received": 6}
