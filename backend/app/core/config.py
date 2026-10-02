from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

# Single .env at the repository root, shared with the frontend.
ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        # Configuration errors never echo values (e.g. DB_PASSWORD).
        hide_input_in_errors=True,
    )

    app_env: Literal["development", "test", "production"] = "development"

    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_name: str
    db_user: str
    db_password: SecretStr
    test_db_name: str

    # Single public origin (frontend + API). Used for the Origin/Referer check.
    app_origin: str = "http://localhost:4321"

    # Sessions (HU-002): persisted in MySQL, identified by an HttpOnly cookie.
    session_cookie_secure: bool = False
    session_absolute_ttl_minutes: int = Field(default=480, gt=0)
    session_idle_ttl_minutes: int = Field(default=30, gt=0)

    # Failed login control (HU-002, criterion 3).
    login_max_failed_attempts: int = Field(default=5, gt=0)
    login_failure_window_minutes: int = Field(default=15, gt=0)
    login_lockout_minutes: int = Field(default=15, gt=0)

    # Simultaneous Argon2 operations (64 MiB each), run outside the event loop.
    password_hash_max_concurrency: int = Field(default=2, ge=1, le=4)

    # Outgoing email (HU-003). Development uses Mailpit (SMTP 127.0.0.1:1025,
    # web UI http://localhost:8025); real mail is never sent from development.
    smtp_host: str = "127.0.0.1"
    smtp_port: int = Field(default=1025, ge=1, le=65535)
    smtp_from: str = "SoftPh-Académico <no-reply@softph.local>"
    smtp_timeout_seconds: float = Field(default=10, gt=0, le=60)

    @model_validator(mode="after")
    def require_secure_cookie_outside_local_http(self) -> Self:
        """SESSION_COOKIE_SECURE=false is only allowed for local HTTP development."""
        if self.session_cookie_secure:
            return self
        if self.app_env == "production":
            raise ValueError("SESSION_COOKIE_SECURE debe ser true cuando APP_ENV=production.")
        if not is_local_http_origin(self.app_origin):
            raise ValueError(
                "SESSION_COOKIE_SECURE=false solo se permite en desarrollo HTTP local: "
                "APP_ORIGIN debe ser http://localhost, http://127.0.0.1 o http://[::1]."
            )
        return self

    def database_url(self, database: str | None = None) -> URL:
        return URL.create(
            drivername="mysql+asyncmy",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=database or self.db_name,
            query={"charset": "utf8mb4"},
        )

    def test_database_url(self) -> URL:
        validate_test_database_name(self.test_db_name, self.db_name)
        return self.database_url(self.test_db_name)


def is_local_http_origin(origin: str) -> bool:
    parts = urlsplit(origin)
    return parts.scheme == "http" and parts.hostname in LOCAL_HOSTS


def validate_test_database_name(test_db_name: str, db_name: str) -> None:
    """Refuse to run tests against anything that is not a dedicated *_test database."""
    if not test_db_name.endswith("_test"):
        raise ValueError("TEST_DB_NAME must end with '_test'.")
    if test_db_name == db_name:
        raise ValueError("TEST_DB_NAME must differ from DB_NAME.")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
