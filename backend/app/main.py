from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.api.v1 import demo_payments
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.db.session import engine
from app.services.auth import dummy_password_hash

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Warm up the dummy Argon2 hash (off the event loop) before serving logins.
    await dummy_password_hash()
    yield
    await engine.dispose()


DEMO_IN_PRODUCTION_MESSAGE = "DEMO_PAYMENTS_ENABLED no puede ser true cuando APP_ENV=production."
DEMO_PREFIX = f"{API_PREFIX}{demo_payments.router.prefix}"

# The APP_ENV each application was created for, recorded by create_app only. Private to this
# module and keyed by the instance (weakly: it goes away with the application); a str snapshot,
# never the Settings object, which could be changed afterwards. Not in app.state on purpose.
_CREATED_FOR: WeakKeyDictionary[FastAPI, str] = WeakKeyDictionary()


def _demo_routes_published(app: FastAPI) -> bool:
    """Read from the application's real routes (as its OpenAPI is built), never from a flag."""
    paths = get_openapi(title=app.title, version=app.version, routes=app.routes)["paths"]
    return any(path == DEMO_PREFIX or path.startswith(f"{DEMO_PREFIX}/") for path in paths)


def include_demo_payments(app: FastAPI, settings: Settings) -> None:
    """HU-010: publishes the routes of the internal demo payment gateway, once.

    Checked on every call, closed by default:
    - the application must have been created by create_app, for an APP_ENV other than production
      (an application created for production never gets them, whatever Settings is passed);
    - the Settings received, whatever built it (model_copy(update=...) skips its validators),
      must not be production and must enable DEMO_PAYMENTS_ENABLED.
    create_app calls it when the flag is on; the integration tests call it with their own test
    Settings. Without it the routes do not exist at all (404, not in OpenAPI).
    """
    created_for = _CREATED_FOR.get(app)
    if created_for is None:
        raise RuntimeError("La aplicación no fue creada por create_app: las rutas de pago de demostración no se publican.")
    if created_for == "production":
        raise RuntimeError("La aplicación fue creada para APP_ENV=production: las rutas de pago de demostración no se publican.")
    if settings.app_env == "production":
        raise RuntimeError("Las rutas de pago de demostración nunca se publican con APP_ENV=production.")
    if not settings.demo_payments_enabled:
        raise RuntimeError("DEMO_PAYMENTS_ENABLED no está habilitado: las rutas de pago de demostración no se publican.")
    if _demo_routes_published(app):
        return
    app.include_router(demo_payments.router, prefix=API_PREFIX)
    app.openapi_schema = None  # published again with the new routes


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Defense in depth: Settings already refuses this combination, but a Settings object built
    # around its validators (e.g. model_copy(update=...)) must not start a production API either.
    if settings.demo_payments_enabled and settings.app_env == "production":
        raise RuntimeError(DEMO_IN_PRODUCTION_MESSAGE)
    docs_enabled = settings.app_env != "production"

    # No CORS middleware: frontend and API share a single origin (see skills).
    app = FastAPI(
        title="SoftPh-Academico API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json" if docs_enabled else None,
    )
    _CREATED_FOR[app] = settings.app_env
    register_exception_handlers(app)
    app.include_router(api_router, prefix=API_PREFIX)
    if settings.demo_payments_enabled:
        include_demo_payments(app, settings)
    return app


app = create_app()
