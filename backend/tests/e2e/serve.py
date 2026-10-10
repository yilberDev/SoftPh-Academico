"""API instance of the Playwright E2E tests: the same application, on the *_test database.

Usage (from backend/, started by frontend/playwright.config.ts):
    .venv/bin/python -m tests.e2e.serve --port 8100 --origin http://localhost:4400

Nothing in .env changes: DB_NAME is replaced by TEST_DB_NAME for this process
only (refused unless it ends in "_test" and differs from DB_NAME), and the
public origin is the E2E frontend's. Email keeps the configured development
sandbox (Mailpit); no real email or payment provider is involved. The internal
demo gateway of HU-010 (DEMO_PAYMENTS_ENABLED) is enabled for this process only,
with APP_ENV=test (never allowed in production).
"""

import argparse
import os

import uvicorn

from app.core.config import get_settings, validate_test_database_name


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()

    settings = get_settings()
    validate_test_database_name(settings.test_db_name, settings.db_name)
    os.environ["DB_NAME"] = settings.test_db_name
    os.environ["APP_ENV"] = "test"
    os.environ["APP_ORIGIN"] = args.origin
    os.environ["DEMO_PAYMENTS_ENABLED"] = "true"
    get_settings.cache_clear()
    if not get_settings().db_name.endswith("_test"):
        raise SystemExit("The E2E API only runs on the *_test database.")

    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
