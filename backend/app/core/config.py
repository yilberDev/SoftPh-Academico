from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

# Single .env at the repository root, shared with the frontend.
ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"

    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_name: str
    db_user: str
    db_password: SecretStr
    test_db_name: str

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


def validate_test_database_name(test_db_name: str, db_name: str) -> None:
    """Refuse to run tests against anything that is not a dedicated *_test database."""
    if not test_db_name.endswith("_test"):
        raise ValueError("TEST_DB_NAME must end with '_test'.")
    if test_db_name == db_name:
        raise ValueError("TEST_DB_NAME must differ from DB_NAME.")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
