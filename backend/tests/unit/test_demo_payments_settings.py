"""HU-010-C: DEMO_PAYMENTS_ENABLED is off by default and can never be on in production."""

from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.main import create_app, include_demo_payments


def make(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "db_name": "softph_academico", "db_user": "softph", "db_password": "not-a-real-password",
        "test_db_name": "softph_academico_test", **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_ENV", "APP_ORIGIN", "SESSION_COOKIE_SECURE", "DEMO_PAYMENTS_ENABLED"):
        monkeypatch.delenv(name, raising=False)


def test_disabled_by_default() -> None:
    assert make().demo_payments_enabled is False


@pytest.mark.parametrize("app_env", ["development", "test"])
def test_can_be_enabled_outside_production(app_env: str) -> None:
    assert make(app_env=app_env, demo_payments_enabled=True).demo_payments_enabled is True


def test_never_in_production() -> None:
    with pytest.raises(ValidationError, match="DEMO_PAYMENTS_ENABLED no puede ser true cuando APP_ENV=production"):
        make(app_env="production", session_cookie_secure=True, demo_payments_enabled=True)
    assert make(app_env="production", session_cookie_secure=True).demo_payments_enabled is False


def test_the_app_factory_refuses_production_even_without_the_validator() -> None:
    valid = make(app_env="production", session_cookie_secure=True)
    # model_copy(update=...) does not run the validators: this object is inconsistent on purpose.
    inconsistent = valid.model_copy(update={"demo_payments_enabled": True})
    assert (inconsistent.app_env, inconsistent.demo_payments_enabled) == ("production", True)

    with pytest.raises(RuntimeError, match="DEMO_PAYMENTS_ENABLED no puede ser true cuando APP_ENV=production"):
        create_app(settings=inconsistent)

    # The consistent combinations still build: production without the simulator, development with it.
    def demo_paths(settings: Settings) -> list[str]:
        return [path for path in create_app(settings=settings).openapi()["paths"] if "demo" in path]

    assert demo_paths(valid) == []
    assert demo_paths(make(app_env="development", demo_payments_enabled=True)) == [
        "/api/v1/resident/demo-payments", "/api/v1/resident/demo-payments/obligations",
        "/api/v1/resident/demo-payments/instruments",
        "/api/v1/resident/demo-payments/{transaction_id}",
        "/api/v1/resident/demo-payments/{transaction_id}/complete",
        "/api/v1/resident/demo-payments/{transaction_id}/cancel",
    ]


# --- include_demo_payments: checked on every call, whatever built the Settings -----------------------------

DEMO_PATHS = [
    "/api/v1/resident/demo-payments", "/api/v1/resident/demo-payments/obligations",
    "/api/v1/resident/demo-payments/instruments",
    "/api/v1/resident/demo-payments/{transaction_id}",
    "/api/v1/resident/demo-payments/{transaction_id}/complete",
    "/api/v1/resident/demo-payments/{transaction_id}/cancel",
]


def published(app: FastAPI) -> list[tuple[str, str]]:
    """Every demo operation in the app's OpenAPI (built again, never the cached one)."""
    app.openapi_schema = None
    return [(method, path) for path, ops in app.openapi()["paths"].items() if "demo" in path for method in ops]


def plain_app() -> FastAPI:
    """An application without the demo gateway (test environment, flag off)."""
    app = create_app(settings=make(app_env="test"))
    assert published(app) == []
    return app


def test_include_refuses_production_even_through_model_copy() -> None:
    app = plain_app()
    production = make(app_env="production", session_cookie_secure=True).model_copy(update={"demo_payments_enabled": True})
    with pytest.raises(RuntimeError, match="nunca se publican con APP_ENV=production"):
        include_demo_payments(app, production)
    assert published(app) == []


def test_include_refuses_production_even_with_the_flag_off() -> None:
    app = plain_app()
    with pytest.raises(RuntimeError, match="nunca se publican con APP_ENV=production"):
        include_demo_payments(app, make(app_env="production", session_cookie_secure=True))
    assert published(app) == []


@pytest.mark.parametrize("app_env", ["development", "test"])
def test_include_refuses_a_disabled_flag(app_env: str) -> None:
    app = plain_app()
    with pytest.raises(RuntimeError, match="DEMO_PAYMENTS_ENABLED no está habilitado"):
        include_demo_payments(app, make(app_env=app_env))
    assert published(app) == []


def test_include_publishes_the_routes_once_outside_production() -> None:
    app = plain_app()
    enabled = make(app_env="test", demo_payments_enabled=True)

    before = len(app.routes)
    include_demo_payments(app, enabled)
    once = len(app.routes)
    include_demo_payments(app, enabled)  # repeated: nothing is added (OpenAPI would hide a duplicate; routes do not)

    assert once == before + 1 and len(app.routes) == once
    assert sorted({path for _, path in published(app)}) == sorted(DEMO_PATHS) and len(published(app)) == 6
    # Every call checks its Settings again, even when the routes are already there.
    with pytest.raises(RuntimeError, match="DEMO_PAYMENTS_ENABLED no está habilitado"):
        include_demo_payments(app, make(app_env="test"))
    app.state.demo_payments_included = False  # no state attribute decides anything
    include_demo_payments(app, enabled)
    assert len(app.routes) == once


# --- The application's own environment, recorded by create_app -------------------------------------------


def test_an_application_created_for_production_never_gets_the_routes() -> None:
    production_app = create_app(settings=make(app_env="production", session_cookie_secure=True))
    development = make(app_env="development", demo_payments_enabled=True)  # valid, and enabled
    before = len(production_app.routes)

    with pytest.raises(RuntimeError, match="creada para APP_ENV=production"):
        include_demo_payments(production_app, development)

    assert len(production_app.routes) == before and published(production_app) == []
    # Changing what the application exposes publicly does not change what it was created for.
    production_app.state.app_env = "development"
    with pytest.raises(RuntimeError, match="creada para APP_ENV=production"):
        include_demo_payments(production_app, make(app_env="test", demo_payments_enabled=True))
    assert published(production_app) == []


@pytest.mark.parametrize("app_env", ["development", "test"])
def test_an_application_created_with_the_flag_gets_the_routes_once(app_env: str) -> None:
    enabled = make(app_env=app_env, demo_payments_enabled=True)
    app = create_app(settings=enabled)  # create_app includes them
    routes = len(app.routes)
    include_demo_payments(app, enabled)  # again: nothing added
    assert len(app.routes) == routes
    assert sorted({path for _, path in published(app)}) == sorted(DEMO_PATHS) and len(published(app)) == 6


def test_an_application_not_created_by_create_app_is_refused() -> None:
    from app.api.v1.router import api_router

    unknown = FastAPI()
    unknown.include_router(api_router, prefix="/api/v1")
    unknown.state.app_env = "test"  # nothing in app.state is trusted
    with pytest.raises(RuntimeError, match="no fue creada por create_app"):
        include_demo_payments(unknown, make(app_env="test", demo_payments_enabled=True))
    assert published(unknown) == []


def test_the_real_configuration_with_the_flag_off_publishes_nothing() -> None:
    # The developer's .env (whatever it holds), validated again with the flag off.
    real = Settings(**{**get_settings().model_dump(), "demo_payments_enabled": False})
    assert published(create_app(settings=real)) == []


def test_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_PAYMENTS_ENABLED", "true")
    assert make().demo_payments_enabled is True


# The frontend reads the same variable with the same rule (frontend/src/lib/resident/demo-payment-flag.ts,
# tests/demo-payment-flag.test.ts): if this table changes, that one must change too.
@pytest.mark.parametrize(("value", "expected"), [
    *((v, True) for v in ("true", "TRUE", "tRuE", "1", "yes", "YES", "y", "Y", "on", "ON", "t", "T")),
    *((v, False) for v in ("false", "FALSE", "0", "no", "NO", "n", "N", "off", "OFF", "f", "F")),
    *((v, None) for v in ("", " ", " true", "true ", "2", "1.0", "enabled", "si", "yes please")),
])
def test_the_values_accepted_from_the_environment(monkeypatch: pytest.MonkeyPatch, value: str,
                                                  expected: bool | None) -> None:
    monkeypatch.setenv("DEMO_PAYMENTS_ENABLED", value)
    if expected is None:  # invalid: the backend does not start
        with pytest.raises(ValidationError):
            make()
    else:
        assert make().demo_payments_enabled is expected
