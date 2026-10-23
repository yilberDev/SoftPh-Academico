from fastapi import APIRouter

from app.api.v1 import (
    account_statements,
    activation,
    auth,
    charges,
    feedback,
    health,
    invitations,
    password_reset,
    payments,
    reports,
    resident_feedback,
    resident_notifications,
    resident_payments,
    residents,
    structure,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
# HU-016: public password reset of residents (Origin + one-time token, no session).
api_router.include_router(password_reset.router)
# Before structure: /units/available must not be captured by /units/{unit_id}.
api_router.include_router(invitations.router)
# HU-019: public activation (Origin + one-time token, no session).
api_router.include_router(activation.router)
api_router.include_router(structure.router)
# HU-006 / HU-015: registered residents (administrator: list, detail, update, logical deletion).
api_router.include_router(residents.router)
# HU-011 / HU-014: the account statement of a resident's current apartment (administrator / the resident).
api_router.include_router(account_statements.admin_router)
api_router.include_router(account_statements.resident_router)
# HU-004: manual payments (administrator).
api_router.include_router(payments.router)
# HU-012: financial reports (administrator, read only).
api_router.include_router(reports.router)
# HU-005: feedback management (administrator).
api_router.include_router(feedback.router)
# Obligations extension: charges of the apartments (administrator).
api_router.include_router(charges.router)
# HU-008: the signed-in resident's own payments (resident, read-only).
api_router.include_router(resident_payments.router)
# HU-009: the signed-in resident's feedback (send and read their own).
api_router.include_router(resident_feedback.router)
# HU-017: the signed-in resident's notifications (resident, read only).
api_router.include_router(resident_notifications.router)
