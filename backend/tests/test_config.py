import pytest

from app.core.config import validate_test_database_name


def test_accepts_dedicated_test_database() -> None:
    validate_test_database_name("softph_academico_test", "softph_academico")


@pytest.mark.parametrize(
    ("test_db_name", "db_name"),
    [
        ("softph_academico", "softph_academico"),
        ("softph_academico_dev", "softph_academico"),
        ("softph_academico_test", "softph_academico_test"),
    ],
)
def test_rejects_non_test_or_development_database(test_db_name: str, db_name: str) -> None:
    with pytest.raises(ValueError):
        validate_test_database_name(test_db_name, db_name)
