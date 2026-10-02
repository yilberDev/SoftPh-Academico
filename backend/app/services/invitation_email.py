"""Invitation email of HU-003 (pure composition, no I/O).

The activation link carries the token in the URL fragment (#token=...): the
browser never sends the fragment to the server, so it does not end up in
access logs or in the Referer header. /activate-account is implemented by HU-019.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.services.mail import OutgoingEmail

BOGOTA = ZoneInfo("America/Bogota")
SUBJECT = "Invitación para activar tu cuenta en SoftPh-Académico"
ACTIVATION_PATH = "/activate-account"

_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def activation_link(app_origin: str, token: str) -> str:
    return f"{app_origin.rstrip('/')}{ACTIVATION_PATH}#token={token}"


def format_bogota(moment_utc: datetime) -> str:
    """'8 de octubre de 2026, 3:05 p. m. (hora de Colombia)' from a naive or aware UTC datetime."""
    aware = moment_utc if moment_utc.tzinfo else moment_utc.replace(tzinfo=UTC)
    local = aware.astimezone(BOGOTA)
    hour = local.hour % 12 or 12
    meridiem = "a. m." if local.hour < 12 else "p. m."
    return (
        f"{local.day} de {_MONTHS[local.month - 1]} de {local.year}, "
        f"{hour}:{local.minute:02d} {meridiem} (hora de Colombia)"
    )


def build_invitation_email(
    *, to: str, full_name: str, unit_label: str, app_origin: str, token: str, expires_at: datetime
) -> OutgoingEmail:
    body = (
        f"Hola, {full_name}:\n"
        "\n"
        "La administración de la Urbanización San Joaquín te invitó a SoftPh-Académico "
        f"como residente del apartamento {unit_label}.\n"
        "\n"
        "Para activar tu cuenta y crear tu contraseña, abre este enlace:\n"
        f"{activation_link(app_origin, token)}\n"
        "\n"
        f"El enlace vence el {format_bogota(expires_at)}. Después de esa fecha deberás "
        "solicitar una nueva invitación a la administración.\n"
        "\n"
        "Si no esperabas esta invitación, puedes ignorar este correo.\n"
        "\n"
        "SoftPh-Académico\n"
    )
    return OutgoingEmail(to=to, subject=SUBJECT, body=body)
