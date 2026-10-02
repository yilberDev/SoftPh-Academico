from fastapi import APIRouter

from app.api.v1 import activation, auth, health, invitations, structure

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
# Before structure: /units/available must not be captured by /units/{unit_id}.
api_router.include_router(invitations.router)
# HU-019: public activation (Origin + one-time token, no session).
api_router.include_router(activation.router)
api_router.include_router(structure.router)
