"""Official visible identifier of the physical structure (HU-013).

B{block}-TO{tower}-APTO-{apartment}, e.g. "B1-TO2-APTO-104". The identifier
is never stored: it is always derived from the persisted hierarchy, so
renumbering a block or a tower changes the identifiers of its descendants
without rewriting any row. The frontend never builds it; for previews it only
appends a number to the child_label_prefix returned by the API.

- name: the level on its own ("B1", "TO2", "APTO-104"), unique only within its parent.
- label: the full identifier ("B1", "B1-TO2", "B1-TO2-APTO-104").
- *_label_prefix: the label of a child without its number (label = prefix + number).
"""

BLOCK_LABEL_PREFIX = "B"
_TOWER_PREFIX = "TO"
_UNIT_PREFIX = "APTO-"
_SEPARATOR = "-"


def block_label(block_number: int) -> str:
    return f"{BLOCK_LABEL_PREFIX}{block_number}"


def tower_name(tower_number: int) -> str:
    return f"{_TOWER_PREFIX}{tower_number}"


def unit_name(unit_number: int) -> str:
    return f"{_UNIT_PREFIX}{unit_number}"


def tower_label_prefix(block_number: int) -> str:
    return f"{block_label(block_number)}{_SEPARATOR}{_TOWER_PREFIX}"


def tower_label(block_number: int, tower_number: int) -> str:
    return f"{block_label(block_number)}{_SEPARATOR}{tower_name(tower_number)}"


def unit_label_prefix(block_number: int, tower_number: int) -> str:
    return f"{tower_label(block_number, tower_number)}{_SEPARATOR}{_UNIT_PREFIX}"


def unit_label(block_number: int, tower_number: int, unit_number: int) -> str:
    return f"{tower_label(block_number, tower_number)}{_SEPARATOR}{unit_name(unit_number)}"
