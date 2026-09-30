"""Shared test settings. No fixture here touches the database.

- tests/unit: no MySQL needed.
- tests/integration: dedicated MySQL *_test database, schema created with Alembic.
"""

import pytest


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"
