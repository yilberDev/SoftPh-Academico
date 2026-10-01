"""Input and output of the physical structure (HU-013): Bloque → Torre → Apartamento."""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, StrictInt, field_validator
from pydantic.json_schema import SkipJsonSchema

from app.core.validation import invalid

# INT UNSIGNED columns: a technical limit of the data type, NOT a domain rule
# (there is no maximum number of blocks, towers or apartments).
NUMBER_TECHNICAL_MAX = 4_294_967_295


def _positive_number(value: int) -> int:
    if value < 1:
        raise invalid("number_positive", "El número debe ser un entero positivo (1 o mayor).")
    if value > NUMBER_TECHNICAL_MAX:
        raise invalid("number_too_large", "El número es demasiado grande para el sistema.")
    return value


def _positive_version(value: int) -> int:
    if not 1 <= value <= NUMBER_TECHNICAL_MAX:
        raise invalid("version_invalid", "La versión no es válida. Recarga la página e inténtalo de nuevo.")
    return value


# Strict: "104", 104.0 and true are rejected instead of being coerced.
StructureNumber = Annotated[StrictInt, AfterValidator(_positive_number)]
StructureVersion = Annotated[StrictInt, AfterValidator(_positive_version)]


class NumberCreate(BaseModel):
    """Body to create a block, a tower or an apartment: only its number.

    The parent comes from the URL, never from the body.
    """

    model_config = ConfigDict(extra="forbid")

    number: StructureNumber


class NumberUpdate(NumberCreate):
    """Only the number can be edited, with the version the client last read."""

    version: StructureVersion


class BlockUpdate(NumberUpdate):
    pass


class TowerUpdate(NumberUpdate):
    # Declared only to reject it with a clear message (no move flow exists).
    block_id: SkipJsonSchema[object] = None

    @field_validator("block_id")
    @classmethod
    def reject_block_change(cls, _value: object) -> object:
        raise invalid("parent_immutable", "El bloque de una torre no se puede cambiar.")


class UnitUpdate(NumberUpdate):
    block_id: SkipJsonSchema[object] = None
    tower_id: SkipJsonSchema[object] = None

    @field_validator("block_id")
    @classmethod
    def reject_block_change(cls, _value: object) -> object:
        raise invalid("parent_immutable", "El bloque de un apartamento no se puede cambiar.")

    @field_validator("tower_id")
    @classmethod
    def reject_tower_change(cls, _value: object) -> object:
        raise invalid("parent_immutable", "La torre de un apartamento no se puede cambiar.")


# --- Responses. name ("TO2") and label ("B1-TO2") are always built by the backend. --


class BlockRef(BaseModel):
    id: int
    number: int
    name: str
    label: str


class TowerRef(BaseModel):
    id: int
    number: int
    name: str
    label: str


class BlockRead(BaseModel):
    id: int
    number: int
    name: str
    label: str
    version: int
    active_towers: int
    active_units: int


class TowerRead(BaseModel):
    id: int
    block_id: int
    number: int
    name: str
    label: str
    version: int
    active_units: int
    block: BlockRef


class UnitRead(BaseModel):
    id: int
    block_id: int
    tower_id: int
    number: int
    name: str
    label: str
    version: int
    block: BlockRef
    tower: TowerRef


class BlockList(BaseModel):
    items: list[BlockRead]
    # Preview only (prefix + typed number); the official label comes after saving.
    child_label_prefix: str


class TowerList(BaseModel):
    block: BlockRead
    items: list[TowerRead]
    child_label_prefix: str


class UnitList(BaseModel):
    block: BlockRef
    tower: TowerRead
    items: list[UnitRead]
    child_label_prefix: str
