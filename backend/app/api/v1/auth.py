from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import DbDep, SettingsDep, client_ip, get_current_auth, get_optional_auth, verify_csrf_token, verify_origin
from app.core.sessions import SESSION_COOKIE_NAME, derive_csrf_token, session_cookie_params
from app.schemas.auth import AuthenticatedUser, LoginRequest, SessionResponse
from app.services import auth as auth_service
from app.services.sessions import AuthContext

router = APIRouter(prefix="/auth", tags=["auth"])

NO_STORE = {"Cache-Control": "no-store"}


@router.post("/login", response_model=SessionResponse, dependencies=[Depends(verify_origin)])
async def login(
    data: LoginRequest, request: Request, response: Response, db: DbDep, settings: SettingsDep
) -> SessionResponse:
    result = await auth_service.authenticate(
        db,
        data,
        settings=settings,
        ip_address=client_ip(request),
        previous_token=request.cookies.get(SESSION_COOKIE_NAME),
    )
    response.set_cookie(
        value=result.token,
        **session_cookie_params(
            secure=settings.session_cookie_secure,
            max_age_seconds=settings.session_absolute_ttl_minutes * 60,
        ),
    )
    response.headers.update(NO_STORE)
    return SessionResponse(user=AuthenticatedUser.model_validate(result.user), csrf_token=derive_csrf_token(result.token))


@router.get("/session", response_model=SessionResponse)
async def current_session(
    auth: Annotated[AuthContext, Depends(get_current_auth)], response: Response
) -> SessionResponse:
    response.headers.update(NO_STORE)
    return SessionResponse(user=AuthenticatedUser.model_validate(auth.user), csrf_token=auth.csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(verify_origin)])
async def logout(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    auth: Annotated[AuthContext | None, Depends(get_optional_auth)],
) -> Response:
    # Without a valid session there is nothing to change: just clear the cookie.
    if auth is not None:
        verify_csrf_token(request, auth)
        await auth_service.logout(db, auth, ip_address=client_ip(request))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    params = session_cookie_params(secure=settings.session_cookie_secure, max_age_seconds=0)
    response.delete_cookie(
        params["key"], path=params["path"], secure=params["secure"], httponly=True, samesite=params["samesite"]
    )
    return response
