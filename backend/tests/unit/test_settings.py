"""M-2: SESSION_COOKIE_SECURE=false only for local HTTP development."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings

SECRET = "super-secret-db-password"


def make(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "db_name": "softph_academico",
        "db_user": "softph",
        "db_password": SECRET,
        "test_db_name": "softph_academico_test",
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_ENV", "APP_ORIGIN", "SESSION_COOKIE_SECURE", "PASSWORD_HASH_MAX_CONCURRENCY"):
        monkeypatch.delenv(name, raising=False)


def test_production_requires_secure_cookie() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make(app_env="production", app_origin="https://softph.example", session_cookie_secure=False)

    assert "SESSION_COOKIE_SECURE debe ser true cuando APP_ENV=production." in str(exc_info.value)


def test_production_rejects_insecure_cookie_even_with_local_origin() -> None:
    with pytest.raises(ValidationError):
        make(app_env="production", app_origin="http://localhost:4321", session_cookie_secure=False)


def test_production_with_secure_cookie_is_valid() -> None:
    settings = make(app_env="production", app_origin="https://softph.example", session_cookie_secure=True)

    assert settings.session_cookie_secure is True


@pytest.mark.parametrize("app_env", ["development", "test"])
@pytest.mark.parametrize(
    "origin", ["http://localhost:4321", "http://127.0.0.1:4321", "http://[::1]:4321", "http://localhost"]
)
def test_insecure_cookie_allowed_for_local_http(app_env: str, origin: str) -> None:
    assert make(app_env=app_env, app_origin=origin, session_cookie_secure=False).session_cookie_secure is False


@pytest.mark.parametrize(
    "origin",
    [
        "http://192.168.1.10:4321",  # LAN, not local
        "http://softph.example",
        "https://localhost:4321",  # HTTPS: use a Secure cookie
        "http://localhost.evil.example",
        "not-a-url",
    ],
)
def test_insecure_cookie_rejected_outside_local_http(origin: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make(app_env="development", app_origin=origin, session_cookie_secure=False)

    assert "solo se permite en desarrollo HTTP local" in str(exc_info.value)


def test_secure_cookie_allowed_in_development() -> None:
    assert make(app_origin="https://localhost:4321", session_cookie_secure=True).session_cookie_secure is True


def test_configuration_errors_never_show_secrets() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make(app_env="production", session_cookie_secure=False)

    # str() is what uvicorn and the CLI print when startup fails.
    assert SECRET not in str(exc_info.value)
    assert SECRET not in repr(exc_info.value.errors(include_input=False))


@pytest.mark.parametrize("value", [0, 5])
def test_password_hash_concurrency_range(value: int) -> None:
    with pytest.raises(ValidationError):
        make(password_hash_max_concurrency=value)


@pytest.mark.parametrize("value", [1, 2, 4])
def test_password_hash_concurrency_accepted(value: int) -> None:
    assert make(password_hash_max_concurrency=value).password_hash_max_concurrency == value


def test_password_hash_concurrency_defaults_to_2() -> None:
    assert make().password_hash_max_concurrency == 2
