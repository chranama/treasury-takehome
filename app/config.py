from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Server-owned configuration loaded from TREASURY_* environment variables."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="TREASURY_",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    database_path: Path = PROJECT_ROOT / ".data" / "treasury.sqlite3"
    temp_dir: Path = PROJECT_ROOT / ".data" / "tmp"
    frontend_dist_path: Path = PROJECT_ROOT / "frontend" / "dist"

    extraction_backend: Literal["fake", "openai"] = "fake"
    fake_extraction_scenario: str = "clear_matching_label"
    live_extraction_enabled: bool = False
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.6-luna"
    openai_image_detail: Literal["high", "original"] = "high"
    openai_service_tier: Literal["default", "fast"] = "default"
    openai_max_output_tokens: Annotated[int, Field(ge=256, le=2_000)] = 1_000
    openai_transient_retries: Annotated[int, Field(ge=0, le=1)] = 1
    extraction_timeout_seconds: Annotated[float, Field(gt=0, le=15)] = 12.0

    live_daily_attempt_limit: Annotated[int | None, Field(ge=1)] = None
    live_cumulative_cost_limit_usd: Annotated[Decimal | None, Field(gt=0)] = None
    live_attempt_reservation_usd: Annotated[Decimal | None, Field(gt=0)] = None
    live_source_window_seconds: Annotated[int | None, Field(ge=1, le=3_600)] = None
    live_source_max_submissions: Annotated[int | None, Field(ge=1)] = None
    trust_cloudflare_client_ip: bool = False

    @field_validator("database_path", "temp_dir", "frontend_dist_path", mode="after")
    @classmethod
    def resolve_project_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    @field_validator("openai_model")
    @classmethod
    def require_openai_model(cls, value: str) -> str:
        model = value.strip()
        if not model:
            raise ValueError("OpenAI model must not be blank")
        return model

    def prepare_local_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def provider_configuration_issues(self) -> list[str]:
        issues: list[str] = []
        if self.live_extraction_enabled:
            if self.extraction_backend != "openai":
                issues.append("live extraction requires the openai backend")
            elif self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip():
                issues.append("live extraction requires an API key")
        return issues

    def configuration_issues(self) -> list[str]:
        issues = self.provider_configuration_issues()
        if self.live_extraction_enabled:
            required_controls = {
                "daily attempt limit": self.live_daily_attempt_limit,
                "cumulative cost limit": self.live_cumulative_cost_limit_usd,
                "attempt cost reservation": self.live_attempt_reservation_usd,
                "source throttle window": self.live_source_window_seconds,
                "source throttle limit": self.live_source_max_submissions,
            }
            for label, value in required_controls.items():
                if value is None:
                    issues.append(f"live extraction requires a {label}")
            if (
                self.live_attempt_reservation_usd is not None
                and self.live_cumulative_cost_limit_usd is not None
                and self.live_attempt_reservation_usd > self.live_cumulative_cost_limit_usd
            ):
                issues.append("attempt cost reservation cannot exceed the cumulative cost limit")
        return issues


@lru_cache
def get_settings() -> Settings:
    return Settings()
