from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
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

    @field_validator("database_path", "temp_dir", "frontend_dist_path", mode="after")
    @classmethod
    def resolve_project_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    def prepare_local_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def configuration_issues(self) -> list[str]:
        issues: list[str] = []
        if self.live_extraction_enabled:
            if self.extraction_backend != "openai":
                issues.append("live extraction requires the openai backend")
            elif self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip():
                issues.append("live extraction requires an API key")
        return issues


@lru_cache
def get_settings() -> Settings:
    return Settings()
