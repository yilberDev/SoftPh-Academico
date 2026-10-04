"""Notifications of feedback management (HU-005 criterion 3.2): pure composition (no I/O).

Sent through the existing MailSender (Mailpit in development) after the change
is committed. HU-017 may later centralize notifications.

- After a response: the feedback was reviewed and the administrator's response.
- After the resolution: the feedback was reviewed and resolved; the response is
  included only if one exists. The resolution note is internal and is never
  sent, nor presented as a response.

No emails of other people, internal notes, identifiers or secrets.
"""

from enum import StrEnum

from app.services.mail import OutgoingEmail

COMMUNITY = "Urbanización San Joaquín"
PRODUCT = "SoftPh-Académico"
NO_SUBJECT = "Sin asunto"


class FeedbackNotice(StrEnum):
    RESPONSE = "response"
    RESOLUTION = "resolution"


def subject_text(subject: str | None) -> str:
    return f"«{subject}»" if subject else f"«{NO_SUBJECT}»"


def notification_subject(notice: FeedbackNotice) -> str:
    # The feedback subject is not repeated in the email subject line.
    if notice == FeedbackNotice.RESPONSE:
        return f"Respuesta a tu feedback — {PRODUCT}"
    return f"Tu feedback fue resuelto — {PRODUCT}"


def build_feedback_email(
    *, notice: FeedbackNotice, to: str, resident_name: str, subject: str | None, admin_response: str | None
) -> OutgoingEmail:
    lines = [f"Hola, {resident_name}:", ""]
    if notice == FeedbackNotice.RESPONSE:
        if admin_response is None:
            raise ValueError("A response notification always carries the response.")
        lines += [
            f"La administración de la {COMMUNITY} revisó tu feedback {subject_text(subject)} y te respondió:",
            "",
            admin_response,
        ]
    elif admin_response:
        lines += [
            f"La administración de la {COMMUNITY} revisó tu feedback {subject_text(subject)} y lo marcó como resuelto.",
            "",
            "Respuesta de la administración:",
            admin_response,
        ]
    else:
        # No written response exists: report the management, never the internal note.
        lines += [
            f"La administración de la {COMMUNITY} revisó tu feedback {subject_text(subject)}, "
            "realizó la gestión correspondiente y lo marcó como resuelto.",
        ]
    lines += ["", "Si tienes más inquietudes, comunícate con la administración.", "", PRODUCT, ""]
    return OutgoingEmail(to=to, subject=notification_subject(notice), body="\n".join(lines))
