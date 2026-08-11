from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.correlation import CorrelationIdMiddleware
from app.api.errors import install_exception_handlers
from app.api.request_limits import (
    SINGLE_REVIEW_MULTIPART_OVERHEAD_BYTES,
    RequestBodyLimitMiddleware,
)
from app.api.reviews import router as reviews_router
from app.api.system import router as system_router
from app.config import Settings, get_settings
from app.db import initialize_database
from app.extraction import ExtractionAdapter, create_extraction_adapter
from app.reviews import AttemptGate, NoCostFakeAttemptGate, ReviewService
from app.storage.images import DEFAULT_IMAGE_LIMITS


def create_app(
    settings: Settings | None = None,
    *,
    extraction_adapter: ExtractionAdapter | None = None,
    attempt_gate: AttemptGate | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_adapter = extraction_adapter
    resolved_attempt_gate = attempt_gate
    if resolved_settings.extraction_backend == "fake":
        resolved_adapter = resolved_adapter or create_extraction_adapter(resolved_settings)
        resolved_attempt_gate = resolved_attempt_gate or NoCostFakeAttemptGate()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        resolved_settings.prepare_local_directories()
        initialize_database(resolved_settings.database_path)
        yield

    application = FastAPI(
        title="Alcohol Label Verification Prototype",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.add_middleware(
        RequestBodyLimitMiddleware,
        path_limits={
            "/api/reviews": (
                DEFAULT_IMAGE_LIMITS.max_upload_bytes + SINGLE_REVIEW_MULTIPART_OVERHEAD_BYTES
            )
        },
    )
    application.add_middleware(CorrelationIdMiddleware)
    install_exception_handlers(application)
    application.state.settings = resolved_settings
    application.state.review_service = ReviewService(
        settings=resolved_settings,
        adapter=resolved_adapter,
        attempt_gate=resolved_attempt_gate,
    )
    application.include_router(system_router)
    application.include_router(reviews_router)

    frontend_index = resolved_settings.frontend_dist_path / "index.html"
    if frontend_index.is_file():
        application.mount(
            "/",
            StaticFiles(directory=resolved_settings.frontend_dist_path, html=True),
            name="frontend",
        )
    else:

        @application.get("/", include_in_schema=False)
        def frontend_not_built() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "frontend_not_built",
                    "message": "Build the frontend before running the production-shaped service.",
                },
            )

    return application


app = create_app()
