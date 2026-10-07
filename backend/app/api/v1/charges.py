"""Obligations extension: obligations (charges) of the apartments (administrator only).

Every route requires an ADMINISTRADOR session (401 without a session, 403 for
other roles). POST also requires Origin (checked for the whole router) and the
CSRF token. A thin layer: validation of the format, then services/charges.py,
which owns the financial rules, the transactions, the locks and the audit.
"Today" is the Bogotá calendar date. Financial data: never cached, not even an
error (NoStoreRoute).
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.deps import DbDep, client_ip, require_csrf, require_roles, verify_origin
from app.core.clock import bogota_today
from app.core.errors import app_error_handler, http_exception_handler, validation_exception_handler
from app.core.exceptions import AppError, MonthlyPreviewOutdatedError
from app.models import UserRole
from app.schemas.charge import (
    ChargeCreate,
    ChargeCreated,
    ChargeDetailRead,
    ChargeListParams,
    ChargePage,
    ChargeRead,
    MonthlyGenerationCreate,
    MonthlyGenerationRead,
    MonthlyPreviewQuery,
    MonthlyPreviewRead,
)
from app.schemas.invitation import ID_MAX
from app.services import charges as charge_service
from app.services.monthly_rules import MonthlyParams
from app.services.sessions import AuthContext
from app.services.structure import Actor

NO_STORE = {"Cache-Control": "no-store"}


class NoStoreRoute(APIRoute):
    """Every answer of these routes carries Cache-Control: no-store, success or error.

    Errors raised by the dependencies (401, 403, 403 CSRF_FAILED), the request
    validation (422) or the service (404, 409, 422) are turned into the usual
    response by the same handlers the application registers (same JSON), then
    marked no-store. Only this router uses it: other APIs are unchanged.
    """

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handle = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await handle(request)
            except AppError as exc:
                response = await app_error_handler(request, exc)
            except RequestValidationError as exc:
                response = await validation_exception_handler(request, exc)
            except StarletteHTTPException as exc:
                response = await http_exception_handler(request, exc)
            response.headers.update(NO_STORE)
            return response

        return handler


router = APIRouter(
    prefix="/admin/charges", tags=["charges"], dependencies=[Depends(verify_origin)], route_class=NoStoreRoute
)

# Order matters: 401 without a session, 403 for other roles, then 403 CSRF_FAILED.
AdminDep = Annotated[AuthContext, Depends(require_roles(UserRole.ADMINISTRADOR))]
CsrfDep = Annotated[AuthContext, Depends(require_csrf)]
ChargeId = Annotated[int, Path(ge=1, le=ID_MAX)]


def _actor(auth: AuthContext, request: Request) -> Actor:
    return Actor(user_id=auth.user.id, ip_address=client_ip(request))


@router.get("/monthly/preview", response_model=MonthlyPreviewRead)
async def preview_monthly(_auth: AdminDep, query: Annotated[MonthlyPreviewQuery, Query()], db: DbDep) -> MonthlyPreviewRead:
    params = MonthlyParams(query.billing_period, query.amount, query.due_date)
    return MonthlyPreviewRead.from_preview(await charge_service.preview_monthly(db, params))


@router.post("/monthly", response_model=MonthlyGenerationRead)
async def generate_monthly(
    data: MonthlyGenerationCreate, request: Request, auth: AdminDep, _csrf: CsrfDep, db: DbDep
) -> MonthlyGenerationRead | JSONResponse:
    command = charge_service.MonthlyGenerationInput(
        MonthlyParams(data.billing_period, data.amount, data.due_date), data.expected_to_create, data.preview_fingerprint
    )
    try:
        result = await charge_service.generate_monthly(db, command, _actor(auth, request))
    except MonthlyPreviewOutdatedError as exc:
        # The standard 409 error plus the current preview computed by the service (nothing was created).
        body = {"error": {"code": exc.code, "message": exc.message, "details": [],
                          "preview": MonthlyPreviewRead.from_preview(exc.preview).model_dump(mode="json")}}  # type: ignore[arg-type]
        return JSONResponse(status_code=exc.status_code, content=body)
    return MonthlyGenerationRead.from_result(result)


@router.post("", response_model=ChargeCreated, status_code=status.HTTP_201_CREATED)
async def create_charge(data: ChargeCreate, request: Request, auth: AdminDep, _csrf: CsrfDep, db: DbDep) -> ChargeCreated:
    command = charge_service.ChargeInput(
        unit_id=data.unit_id, concept=data.concept, amount=data.amount, due_date=data.due_date,
        description=data.description, billing_period=data.billing_period, source_charge_id=data.source_charge_id,
    )
    # The author is the session's administrator, never a value from the client.
    return ChargeCreated.from_created(await charge_service.create_charge(db, command, _actor(auth, request)))


@router.get("", response_model=ChargePage)
async def list_charges(_auth: AdminDep, query: Annotated[ChargeListParams, Query()], db: DbDep) -> ChargePage:
    page = await charge_service.list_charges(
        db,
        charge_service.ChargeListQuery(
            page=query.page, page_size=query.page_size, unit_id=query.unit_id, block_id=query.block_id,
            tower_id=query.tower_id, concept=query.concept, status=query.status, billing_period=query.billing_period,
            due_from=query.due_from, due_to=query.due_to, sort_by=query.sort_by, sort_order=query.sort_order,
        ),
        bogota_today(),
    )
    return ChargePage(items=[ChargeRead.from_summary(i) for i in page.items], page=page.page,
                      page_size=page.page_size, total=page.total, total_pages=page.total_pages)


@router.get("/{charge_id}", response_model=ChargeDetailRead)
async def get_charge(charge_id: ChargeId, _auth: AdminDep, db: DbDep) -> ChargeDetailRead:
    return ChargeDetailRead.from_detail(await charge_service.get_charge(db, charge_id, bogota_today()))
