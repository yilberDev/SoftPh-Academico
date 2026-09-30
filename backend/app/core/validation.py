"""Spanish, user-facing validation messages shared by the API and the CLI."""

from collections.abc import Sequence
from typing import Any

from pydantic_core import PydanticCustomError

_CUSTOM_ERROR_TYPES: set[str] = set()

# Messages for built-in Pydantic errors that can reach the user.
_BUILTIN_MESSAGES = {
    "missing": "Este campo es obligatorio.",
    "string_type": "Debe ser un texto.",
    "json_invalid": "El cuerpo de la solicitud no es un JSON válido.",
}
_FALLBACK_MESSAGE = "El valor no es válido."


def invalid(error_type: str, message: str) -> PydanticCustomError:
    """Builds a validation error whose message is already user-facing."""
    _CUSTOM_ERROR_TYPES.add(error_type)
    return PydanticCustomError(error_type, message)


def error_details(errors: Sequence[Any]) -> list[dict[str, str]]:
    """Converts Pydantic errors to [{"field", "message"}] with Spanish messages."""
    details = []
    for error in errors:
        error_type = error["type"]
        if error_type in _CUSTOM_ERROR_TYPES:
            message = error["msg"]
        else:
            message = _BUILTIN_MESSAGES.get(error_type, _FALLBACK_MESSAGE)
        field = ".".join(str(part) for part in error["loc"] if part != "body")
        details.append({"field": field, "message": message})
    return details
