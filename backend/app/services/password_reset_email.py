"""Password reset email of HU-016 (pure composition, no I/O).

Same rules as the invitation email: the token goes in the URL fragment
(#token=...), the body is never logged, and the expiry is shown in Bogotá time.
"""

from datetime import datetime

from app.services.invitation_email import format_bogota, token_link
from app.services.mail import OutgoingEmail

RESET_PATH = "/reset-password"
SUBJECT = "Restablece tu contraseña de SoftPh-Académico"


def reset_link(app_origin: str, token: str) -> str:
    return token_link(app_origin, RESET_PATH, token)


def build_password_reset_email(
    *, to: str, full_name: str, app_origin: str, token: str, expires_at: datetime
) -> OutgoingEmail:
    body = (
        f"Hola, {full_name}:\n"
        "\n"
        "Recibimos una solicitud para restablecer la contraseña de tu cuenta de residente "
        "en SoftPh-Académico.\n"
        "\n"
        "Para crear una contraseña nueva, abre este enlace:\n"
        f"{reset_link(app_origin, token)}\n"
        "\n"
        f"El enlace vence el {format_bogota(expires_at)} y solo puede usarse una vez. "
        "Si solicitas otro, este dejará de funcionar.\n"
        "\n"
        "Si no solicitaste este cambio, puedes ignorar este correo: tu contraseña actual "
        "seguirá funcionando.\n"
        "\n"
        "SoftPh-Académico\n"
    )
    return OutgoingEmail(to=to, subject=SUBJECT, body=body)
