"""HU-017: the signed-in resident's notifications (read only).

    GET /resident/notifications   RESIDENTE: always the session's own resident (auth.user.id); no path,
        query parameter, header or body selects anyone (page and page_size only; anything else is 422).

401 without a session and 403 for the other role, decided before anything is read. GET only: no CSRF
token. Personal data: never cached, not even an error (NoStoreRoute). The notifications themselves
are created by the flows that notify (HU-004 payments, HU-005 feedback): services/notifications.py.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbDep, require_roles
from app.api.v1.charges import NoStoreRoute
from app.models import UserRole
from app.schemas.notification import ResidentNotificationPage, ResidentNotificationQuery
from app.services import notifications as notification_service
from app.services.sessions import AuthContext

router = APIRouter(prefix="/resident/notifications", tags=["notifications"], route_class=NoStoreRoute)

ResidentDep = Annotated[AuthContext, Depends(require_roles(UserRole.RESIDENTE))]


@router.get("", response_model=ResidentNotificationPage)
async def list_my_notifications(
    auth: ResidentDep, query: Annotated[ResidentNotificationQuery, Query()], db: DbDep
) -> ResidentNotificationPage:
    page = await notification_service.list_resident_notifications(
        db, auth.user.id, page=query.page, page_size=query.page_size  # the session's resident only
    )
    return ResidentNotificationPage.from_page(page)
