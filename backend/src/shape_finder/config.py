import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shape_finder.core.current_data import AccountPolicy, DispatchLimits, TimingPolicy


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and an optional local .env."""

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), extra="ignore", hide_input_in_errors=True
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    twelve_data_api_key: SecretStr | None = None
    twelve_data_base_url: str = "https://api.twelvedata.com"
    current_daily_provider: Literal["twelve_data", "massive"] = "twelve_data"
    massive_backfill_sessions: int = Field(default=63, ge=1, le=240)
    massive_publication_time_et: str = Field(
        default="20:15", pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"
    )
    massive_api_key: SecretStr | None = None
    massive_refresh_enabled: bool = False
    shapefinder_owner_username: SecretStr | None = None
    shapefinder_owner_password: SecretStr | None = None
    shapefinder_public_origin: str | None = None
    shapefinder_data_dir: Path | None = None
    railway_project_id: str | None = None
    railway_environment_id: str | None = None
    railway_volume_mount_path: Path | None = None
    market_data_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    universe_ttl_hours: int = Field(default=24, ge=1, le=720)
    universe_hydration_max_symbols: int = Field(default=5, ge=1, le=25)
    universe_hydration_intraday_max_symbols: int = Field(default=2, ge=1, le=10)
    universe_hydration_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    database_path: Path = Path("data/shapefinder.sqlite3")
    scan_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_concurrent_scans: int = Field(default=2, ge=1, le=32)
    max_request_body_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    current_dispatch_limits: DispatchLimits = Field(default_factory=DispatchLimits)
    current_timing_policies: tuple[TimingPolicy, ...] = ()
    current_account_policy: AccountPolicy | None = None
    current_account_scope: str = Field(default="twelve_data", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    current_accept_configured_policies: bool = False
    current_scan_timeout_seconds: float = Field(default=30, gt=0, le=120)
    current_scoring_seconds: float = Field(default=8, gt=0, le=30)
    current_candidate_limit: int = Field(default=200, ge=1, le=1000)
    current_hypothesis_limit: int = Field(default=1_000_000, ge=0, le=1_000_000)

    @field_validator("current_timing_policies")
    @classmethod
    def unique_timing_intervals(cls, values: tuple[TimingPolicy, ...]) -> tuple[TimingPolicy, ...]:
        if len({p.interval for p in values}) != len(values):
            raise ValueError("Current timing policies must have unique intervals.")
        return values

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
        if self.massive_refresh_enabled and (
            self.current_daily_provider != "massive"
            or not self.massive_api_key
            or not self.massive_api_key.get_secret_value().strip()
        ):
            raise ValueError("Scheduled refresh requires the Massive provider and MASSIVE_API_KEY.")
        if not self.hosted:
            return self
        if not self.twelve_data_base_url.startswith("https://"):
            raise ValueError("Production requires an HTTPS market-data provider URL.")
        owner = self.shapefinder_owner_username
        password = self.shapefinder_owner_password
        if (
            owner is None
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", owner.get_secret_value())
            or password is None
            or not re.fullmatch(r"[!-~]{24,128}", password.get_secret_value())
        ):
            raise ValueError(
                "Production requires valid SHAPEFINDER_OWNER_USERNAME and "
                "SHAPEFINDER_OWNER_PASSWORD (at least 24 printable ASCII characters)."
            )
        origin = urlparse(self.shapefinder_public_origin or "")
        loopback = not self.on_railway and origin.hostname in {"127.0.0.1", "localhost", "::1"}
        if (
            not origin.hostname
            or not (origin.scheme == "https" or (loopback and origin.scheme == "http"))
            or self.shapefinder_public_origin != f"{origin.scheme}://{origin.netloc}"
            or origin.username is not None
            or origin.password is not None
        ):
            raise ValueError("Production requires an explicit HTTPS SHAPEFINDER_PUBLIC_ORIGIN.")
        # Railway supplies the real mount directory at runtime. Local hosted checks
        # use an explicit disposable directory in place of a mounted volume.
        root = self.railway_volume_mount_path if self.on_railway else self.shapefinder_data_dir
        if (
            root is None
            or not root.is_absolute()
            or not root.is_dir()
            or not self.database_path.is_absolute()
            or not self.database_path.resolve().is_relative_to(root.resolve())
            or self.database_path.resolve() == root.resolve()
        ):
            raise ValueError("Production requires DATABASE_PATH inside its persistent data mount.")
        return self

    @property
    def on_railway(self) -> bool:
        return bool(self.railway_project_id or self.railway_environment_id)

    @property
    def hosted(self) -> bool:
        return self.app_env == "production" or self.on_railway


@lru_cache
def get_settings() -> Settings:
    return Settings()
