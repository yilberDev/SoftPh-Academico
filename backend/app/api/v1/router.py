from fastapi import APIRouter

from app.api.v1 import (
    activation,
    auth,
    charges,
    feedback,
    health,
    invitations,
    password_reset,
    payments,
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
# HU-006: registered residents (administrator, read-only).
api_router.include_router(residents.router)
# HU-004: manual payments (administrator).
api_router.include_router(payments.router)
# HU-005: feedback management (administrator).
api_router.include_router(feedback.router)
# Obligations extension: charges of the apartments (administrator).
api_router.include_router(charges.router)
# HU-008: the signed-in resident's own payments (resident, read-only).
api_router.include_router(resident_payments.router)
