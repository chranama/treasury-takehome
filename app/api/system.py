from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import Settings
from app.db import database_is_ready

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, Literal["ok", "not_ready"]]


def temporary_storage_is_ready(temp_dir: Path) -> bool:
    try:
        with NamedTemporaryFile(dir=temp_dir):
            return True
    except OSError:
        return False


@router.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    return HealthResponse()


@router.get("/readyz", response_model=ReadinessResponse)
def readyz(request: Request) -> ReadinessResponse | JSONResponse:
    settings: Settings = request.app.state.settings
    checks: dict[str, Literal["ok", "not_ready"]] = {
        "configuration": "ok" if not settings.configuration_issues() else "not_ready",
        "database": "ok" if database_is_ready(settings.database_path) else "not_ready",
        "temporary_storage": "ok" if temporary_storage_is_ready(settings.temp_dir) else "not_ready",
    }
    status: Literal["ready", "not_ready"] = (
        "ready" if all(value == "ok" for value in checks.values()) else "not_ready"
    )
    payload = ReadinessResponse(status=status, checks=checks)
    if status == "not_ready":
        return JSONResponse(status_code=503, content=payload.model_dump())
    return payload
