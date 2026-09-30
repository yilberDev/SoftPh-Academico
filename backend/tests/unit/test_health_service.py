import pytest

from app.services.health import is_database_available

pytestmark = pytest.mark.anyio


async def test_database_check_treats_driver_errors_as_unavailable() -> None:
    class FailingSession:
        async def execute(self, _statement: object) -> None:
            raise RuntimeError("driver failure")

    assert await is_database_available(FailingSession()) is False  # type: ignore[arg-type]
