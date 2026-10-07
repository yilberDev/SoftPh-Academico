"""HU-009: the signed-in resident sends feedback and reads their own.

Every route requires a RESIDENTE session (401 without a session, 403 for any
other role, decided before anything is read or validated). POST also requires
Origin (checked for the whole router) and the CSRF token. The resident is
always the session's user (auth.user.id): no path, query parameter, header or
body field selects a resident, and unknown body fields and query parameters
are rejected. A feedback of another resident is 404 NOT_FOUND "El feedback no
existe.", exactly like a missing one. A thin layer over services/
resident_feedback.py (create_feedback validates, stores it PENDING and audits
FEEDBACK_CREATED). No email is sent. Personal data: never cached, not even an
error (NoStoreRoute).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status

from app.api.deps import DbDep, client_ip, require_csrf, require_roles, verify_origin
from app.api.v1.charges import NoStoreRoute
from app.models import UserRole
from app.schemas.invitation import ID_MAX
from app.schemas.resident_feedback import (
    ResidentFeedbackCreate,
    ResidentFeedbackListQuery,
    ResidentFeedbackPage,
    ResidentFeedbackRead,
)
from app.services import resident_feedback as resident_feedback_service
from app.services.sessions import AuthContext

router = APIRouter(
    prefix="/resident/feedback", tags=["resident-feedback"], dependencies=[Depends(verify_origin)],
    route_class=NoStoreRoute,
)

# Order matters: 401 without a session, 403 for other roles, then 403 CSRF_FAILED.
ResidentDep = Annotated[AuthContext, Depends(require_roles(UserRole.RESIDENTE))]
CsrfDep = Annotated[AuthContext, Depends(require_csrf)]
FeedbackId = Annotated[int, Path(ge=1, le=ID_MAX)]


@router.post("", response_model=ResidentFeedbackRead, status_code=status.HTTP_201_CREATED)
async def send_feedback(
    data: ResidentFeedbackCreate, request: Request, auth: ResidentDep, _csrf: CsrfDep, db: DbDep
) -> ResidentFeedbackRead:
    created = await resident_feedback_service.submit_resident_feedback(
        db,
        auth.user.id,  # the session's resident, never a value from the client
        subject=data.subject, message=data.message, category=data.category, urgency=data.urgency,
        ip_address=client_ip(request),
    )
    return ResidentFeedbackRead.from_detail(created)


@router.get("", response_model=ResidentFeedbackPage)
async def list_my_feedback(
    auth: ResidentDep, query: Annotated[ResidentFeedbackListQuery, Query()], db: DbDep
) -> ResidentFeedbackPage:
    page = await resident_feedback_service.list_resident_feedback(
        db, auth.user.id, resident_feedback_service.ResidentFeedbackQuery(page=query.page, page_size=query.page_size)
    )
    return ResidentFeedbackPage.from_page(page)


@router.get("/{feedback_id}", response_model=ResidentFeedbackRead)
async def get_my_feedback(feedback_id: FeedbackId, auth: ResidentDep, db: DbDep) -> ResidentFeedbackRead:
    # feedback_id AND the session's resident: another resident's feedback is "not found".
    return ResidentFeedbackRead.from_detail(
        await resident_feedback_service.get_resident_feedback(db, auth.user.id, feedback_id)
    )
