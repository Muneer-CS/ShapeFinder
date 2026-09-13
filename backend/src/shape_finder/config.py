from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and an optional local .env."""

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    twelve_data_api_key: SecretStr | None = None
    twelve_data_base_url: str = "https://api.twelvedata.com"
    market_data_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    universe_ttl_hours: int = Field(default=24, ge=1, le=720)
    universe_hydration_max_symbols: int = Field(default=5, ge=1, le=25)
    universe_hydration_intraday_max_symbols: int = Field(default=2, ge=1, le=10)
    universe_hydration_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    database_path: Path = Path("data/shapefinder.sqlite3")
    scan_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_concurrent_scans: int = Field(default=2, ge=1, le=32)
    max_request_body_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in values:
            value = raw.strip().rstrip("/")
            parsed = urlparse(value)
            if (
                value == "*"
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.params
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS_ORIGINS must contain explicit HTTP(S) origins.")
            normalized.append(value)
        return list(dict.fromkeys(normalized))

    @field_validator("database_path")
    @classmethod
    def validate_database_path(cls, value: Path) -> Path:
        if "\x00" in str(value):
            raise ValueError("DATABASE_PATH contains an invalid character.")
        return value

    @field_validator("twelve_data_base_url")
    @classmethod
    def validate_provider_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("TWELVE_DATA_BASE_URL must be a credential-free HTTP(S) URL.")
        return normalized

    @model_validator(mode="after")
    def validate_environment(self) -> "Settings":
        if self.app_env == "production" and not self.cors_origins:
            raise ValueError("Production requires at least one explicit CORS origin.")
        if self.app_env == "production" and not self.twelve_data_base_url.startswith("https://"):
            raise ValueError("Production requires an HTTPS market-data provider URL.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
