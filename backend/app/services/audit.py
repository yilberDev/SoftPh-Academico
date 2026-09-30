from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditAction, AuditLog


def record_audit(
    session: AsyncSession,
    *,
    action: AuditAction,
    entity_type: str | None = None,
    entity_id: int | None = None,
    actor_user_id: int | None = None,
    context: dict[str, Any] | None = None,
) -> AuditLog:
    """Adds an audit entry to the caller's transaction (it does not commit).

    The context must never contain passwords, tokens or other secrets.
    """
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        context=context,
    )
    session.add(entry)
    return entry
