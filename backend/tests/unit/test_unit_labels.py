"""HU-013: the official visible identifier is derived by the backend."""

import pytest

from app.core.unit_labels import (
    BLOCK_LABEL_PREFIX,
    block_label,
    tower_label,
    tower_label_prefix,
    tower_name,
    unit_label,
    unit_label_prefix,
    unit_name,
)


def test_official_full_identifier() -> None:
    assert unit_label(1, 2, 104) == "B1-TO2-APTO-104"


def test_each_level() -> None:
    assert block_label(1) == "B1"
    assert tower_name(2) == "TO2"
    assert tower_label(1, 2) == "B1-TO2"
    assert unit_name(104) == "APTO-104"


@pytest.mark.parametrize("number", [1, 2, 9, 10, 104, 4_294_967_295])
def test_child_prefix_plus_number_is_the_label(number: int) -> None:
    # The frontend preview is child_label_prefix + typed number: it must match
    # the label the backend returns after saving.
    assert BLOCK_LABEL_PREFIX + str(number) == block_label(number)
    assert tower_label_prefix(3) + str(number) == tower_label(3, number)
    assert unit_label_prefix(3, 7) + str(number) == unit_label(3, 7, number)


def test_numbers_are_not_padded_or_limited() -> None:
    # No business maximum and no leading zeros.
    assert unit_label(12, 30, 1104) == "B12-TO30-APTO-1104"
    assert block_label(4_294_967_295) == "B4294967295"


def test_parent_renumbering_only_changes_the_prefix() -> None:
    assert unit_label(1, 2, 104) == "B1-TO2-APTO-104"
    assert unit_label(4, 2, 104) == "B4-TO2-APTO-104"
