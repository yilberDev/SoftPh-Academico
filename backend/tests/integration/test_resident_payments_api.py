"""HU-008: GET /api/v1/resident/payments and /{payment_id}, on the test database.

The world of the service tests (tests/integration/test_resident_payments_query.py):
Laura (B1-TO1-APTO-101) with p1 APPLIED, p2 PARTIALLY_APPLIED, p3 CREDIT (cash,
reference and notes) and p4 CREDIT (same date as p3); Pedro with p5; Ana (p6,
then moved out of B1-TO1-APTO-103) and Bruno (p7, living there now).
"""

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import event, func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Payment, User, UserRole
from app.services.allocation import receipt_number
from app.services.resident_payments import SEARCH_MESSAGE
from tests.integration import test_resident_payments_query as service_tests
from tests.integration.conftest import sign_in
from tests.integration.test_resident_payments_query import World, ago

pytestmark = pytest.mark.anyio

API = "/api/v1/resident/payments"
FORBIDDEN = {"error": {"code": "FORBIDDEN", "message": "No tienes permiso para realizar esta acción.", "details": []}}
NOT_FOUND = {"error": {"code": "PAYMENT_NOT_FOUND", "message": "El pago no existe.", "details": []}}
LIST_FIELDS = {"id", "receipt_number", "payment_date", "concept", "concept_description", "amount", "method", "status",
               "transaction_reference"}
DETAIL_FIELDS = LIST_FIELDS | {"registered_at", "notes", "unit", "applied_amount", "credit_amount", "allocations"}

world = service_tests.world  # the same fixture


async def as_user(client: AsyncClient, db: AsyncSession, user_id: int) -> dict[str, str]:
    user = await db.get(User, user_id)
    assert user is not None
    return await sign_in(client, db, user)


async def get(client: AsyncClient, path: str = "", **params: Any) -> Response:
    return await client.get(f"{API}{path}", params=params)


async def ids_of(client: AsyncClient, **params: Any) -> list[int]:
    response = await get(client, **params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def details(response: Response) -> list[dict[str, str]]:
    return response.json()["error"]["details"]


@pytest.fixture
async def laura(client: AsyncClient, db_session: AsyncSession, world: World) -> dict[str, int]:
    await as_user(client, db_session, world.ids["laura"])
    return world.ids


# --- 1-4. Authentication and role -------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["", "/1"])
async def test_1_without_session_is_401(client: AsyncClient, path: str) -> None:
    response = await get(client, path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"
    assert response.headers["cache-control"] == "no-store"


async def test_2_resident_is_200(client: AsyncClient, laura: dict[str, int]) -> None:
    assert (await get(client)).status_code == 200
    assert (await get(client, f"/{laura['p1']}")).status_code == 200


async def test_3_administrator_is_403(client: AsyncClient, db_session: AsyncSession, world: World) -> None:
    admin_id = await db_session.scalar(select(User.id).where(User.role == UserRole.ADMINISTRADOR))
    await as_user(client, db_session, admin_id)  # type: ignore[arg-type]
    # The same 403 for the list, a real payment and a missing one: nothing is read.
    for response in (await get(client), await get(client, f"/{world.ids['p1']}"), await get(client, "/999999"),
                     await get(client, status="PAID")):
        assert response.status_code == 403
        assert response.json() == FORBIDDEN
        assert response.headers["cache-control"] == "no-store"


async def test_4_any_other_role_is_403(client: AsyncClient, db_session: AsyncSession, make_user: Any) -> None:
    resident = await make_user("residente@example.com", role=UserRole.RESIDENTE, password_hash="not-a-real-hash")
    await sign_in(client, db_session, resident)
    assert (await get(client)).status_code == 200
    # The role is read from the database on every request: the same session, another role → 403.
    await db_session.execute(update(User).where(User.id == resident.id).values(role=UserRole.ADMINISTRADOR))
    forged = {"X-Role": "RESIDENTE", "X-User-Role": "RESIDENTE"}
    client.cookies.set("role", "RESIDENTE")
    for path in ("", "/1"):
        response = await client.get(f"{API}{path}", params={"role": "RESIDENTE"}, headers=forged)
        assert response.status_code == 403
        assert response.json() == FORBIDDEN


# --- 5-15. List ------------------------------------------------------------------------------------------------


async def test_5_only_the_signed_in_residents_payments(client: AsyncClient, db_session: AsyncSession, world: World) -> None:
    i = world.ids
    for resident, expected in (("laura", ["p4", "p3", "p2", "p1"]), ("pedro", ["p5"]), ("bruno", ["p7"]),
                               ("ana", ["p6"])):
        await as_user(client, db_session, i[resident])
        assert await ids_of(client) == [i[p] for p in expected], resident


async def test_5_a_resident_sent_by_the_client_is_never_used(client: AsyncClient, laura: dict[str, int]) -> None:
    for name in ("resident_id", "user_id", "unit_id"):
        response = await get(client, **{name: laura["pedro"]})
        assert response.status_code == 422
        assert details(response) == [{"field": f"query.{name}", "message": "Este campo no está permitido."}]
    # Headers and cookies select nothing either.
    client.cookies.set("resident_id", str(laura["pedro"]))
    response = await client.get(API, headers={"X-Resident-Id": str(laura["pedro"]), "X-User-Id": str(laura["pedro"])})
    assert [item["id"] for item in response.json()["items"]] == [laura["p4"], laura["p3"], laura["p2"], laura["p1"]]


async def test_6_pagination(client: AsyncClient, laura: dict[str, int]) -> None:
    first = (await get(client, page_size=3)).json()
    assert ([item["id"] for item in first["items"]], first["page"], first["page_size"], first["total"],
            first["total_pages"]) == ([laura["p4"], laura["p3"], laura["p2"]], 1, 3, 4, 2)
    second = (await get(client, page=2, page_size=3)).json()
    assert ([item["id"] for item in second["items"]], second["page"]) == ([laura["p1"]], 2)
    beyond = (await get(client, page=3, page_size=3)).json()
    assert (beyond["items"], beyond["total"], beyond["total_pages"]) == ([], 4, 2)


async def test_7_page_size_maximum(client: AsyncClient, laura: dict[str, int]) -> None:
    body = (await get(client, page_size=100)).json()
    assert (len(body["items"]), body["page_size"], body["total_pages"]) == (4, 100, 1)
    assert (await get(client)).json()["page_size"] == 10  # default
    assert (await get(client, page_size=101)).status_code == 422


async def test_8_status_filter(client: AsyncClient, laura: dict[str, int]) -> None:
    assert await ids_of(client, status="APPLIED") == [laura["p1"]]
    assert await ids_of(client, status="PARTIALLY_APPLIED") == [laura["p2"]]
    assert await ids_of(client, status="CREDIT") == [laura["p4"], laura["p3"]]
    assert await ids_of(client, status="") == [laura["p4"], laura["p3"], laura["p2"], laura["p1"]]


async def test_9_date_from(client: AsyncClient, laura: dict[str, int]) -> None:
    assert await ids_of(client, date_from=ago(20).isoformat()) == [laura["p4"], laura["p3"], laura["p2"]]


async def test_10_date_to(client: AsyncClient, laura: dict[str, int]) -> None:
    assert await ids_of(client, date_to=ago(20).isoformat()) == [laura["p2"], laura["p1"]]


async def test_11_combined_filters(client: AsyncClient, laura: dict[str, int]) -> None:
    assert await ids_of(client, status="CREDIT", date_from=ago(25).isoformat(), date_to=ago(5).isoformat()) == [
        laura["p4"], laura["p3"]]
    assert await ids_of(client, status="CREDIT", search=receipt_number(laura["p3"])) == [laura["p3"]]
    assert await ids_of(client, status="APPLIED", search=ago(10).isoformat()) == []
    assert await ids_of(client, date_from=ago(25).isoformat(), date_to=ago(15).isoformat(), page_size=1) == [laura["p2"]]


async def test_12_search_by_receipt(client: AsyncClient, laura: dict[str, int]) -> None:
    p2 = laura["p2"]
    for text in (receipt_number(p2), f"rc-{p2}", str(p2), f" {p2} "):
        assert await ids_of(client, search=text) == [p2], text


async def test_13_search_by_date(client: AsyncClient, laura: dict[str, int]) -> None:
    assert await ids_of(client, search=ago(10).isoformat()) == [laura["p4"], laura["p3"]]
    assert await ids_of(client, search=ago(30).isoformat()) == [laura["p1"]]


async def test_14_no_results(client: AsyncClient, db_session: AsyncSession, laura: dict[str, int], make_user: Any) -> None:
    body = (await get(client, status="APPLIED", date_from=ago(5).isoformat())).json()
    assert body == {"items": [], "page": 1, "page_size": 10, "total": 0, "total_pages": 0}
    assert await ids_of(client, search=ago(1).isoformat()) == []
    # A resident without payments.
    newcomer = await make_user("nuevo@example.com", role=UserRole.RESIDENTE, password_hash="not-a-real-hash")
    await sign_in(client, db_session, newcomer)
    assert (await get(client)).json() == {"items": [], "page": 1, "page_size": 10, "total": 0, "total_pages": 0}


async def test_15_every_answer_is_no_store(client: AsyncClient, laura: dict[str, int]) -> None:
    for response in (await get(client), await get(client, f"/{laura['p1']}"), await get(client, f"/{laura['p5']}"),
                     await get(client, "/0"), await get(client, page=0), await get(client, search="Laura")):
        assert response.headers["cache-control"] == "no-store", response.status_code


async def test_list_items_carry_only_the_list_fields(client: AsyncClient, laura: dict[str, int]) -> None:
    items = {item["id"]: item for item in (await get(client)).json()["items"]}
    assert all(set(item) == LIST_FIELDS for item in items.values())  # no notes, resident or administrator data
    assert items[laura["p3"]] == {
        "id": laura["p3"], "receipt_number": receipt_number(laura["p3"]), "payment_date": ago(10).isoformat(),
        "concept": "MONTHLY_FEE", "concept_description": None, "amount": "40000.00", "method": "CASH",
        "status": "CREDIT", "transaction_reference": "TRF-2026-0042",
    }
    assert (items[laura["p2"]]["concept"], items[laura["p2"]]["status"]) == ("PENALTY", "PARTIALLY_APPLIED")


# --- 16-26. Detail ---------------------------------------------------------------------------------------------


async def test_16_a_resident_reads_their_payment(client: AsyncClient, laura: dict[str, int]) -> None:
    response = await get(client, f"/{laura['p1']}")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == DETAIL_FIELDS
    assert (body["id"], body["receipt_number"], body["payment_date"], body["amount"], body["method"], body["status"]) == (
        laura["p1"], receipt_number(laura["p1"]), ago(30).isoformat(), "100000.00", "TRANSFER", "APPLIED")


async def test_17_another_residents_payment_is_404(client: AsyncClient, db_session: AsyncSession, world: World) -> None:
    i = world.ids
    for resident, payment in (("laura", "p5"), ("pedro", "p1"), ("bruno", "p6"), ("ana", "p7")):
        await as_user(client, db_session, i[resident])
        response = await get(client, f"/{i[payment]}")
        assert (response.status_code, response.json()) == (404, NOT_FOUND), (resident, payment)


async def test_18_a_missing_payment_is_404(client: AsyncClient, db_session: AsyncSession, laura: dict[str, int]) -> None:
    missing = int(await db_session.scalar(select(func.max(Payment.id))) or 0) + 1000
    response = await get(client, f"/{missing}")
    assert (response.status_code, response.json()) == (404, NOT_FOUND)


async def test_19_foreign_and_missing_look_exactly_the_same(
    client: AsyncClient, db_session: AsyncSession, laura: dict[str, int]
) -> None:
    missing = int(await db_session.scalar(select(func.max(Payment.id))) or 0) + 1000
    foreign, absent = await get(client, f"/{laura['p5']}"), await get(client, f"/{missing}")
    assert (foreign.status_code, foreign.content) == (absent.status_code, absent.content)
    assert {k: v for k, v in foreign.headers.items() if k != "content-length"} == {
        k: v for k, v in absent.headers.items() if k != "content-length"}


async def test_20_transaction_reference(client: AsyncClient, laura: dict[str, int]) -> None:
    assert (await get(client, f"/{laura['p3']}")).json()["transaction_reference"] == "TRF-2026-0042"
    assert (await get(client, f"/{laura['p1']}")).json()["transaction_reference"] is None


async def test_21_notes(client: AsyncClient, laura: dict[str, int]) -> None:
    assert (await get(client, f"/{laura['p3']}")).json()["notes"] == "Pagó en la portería.\nTrajo el soporte."
    assert (await get(client, f"/{laura['p1']}")).json()["notes"] is None


async def test_22_allocations(client: AsyncClient, laura: dict[str, int]) -> None:
    fee = (await get(client, f"/{laura['p1']}")).json()["allocations"]
    assert fee[0].pop("allocated_at").endswith(("Z", "+00:00"))
    assert fee == [{"charge_id": laura["fee"], "concept": "MONTHLY_FEE", "description": None,
                    "billing_period": "2026-01", "due_date": "2026-01-05", "charge_amount": "100000.00",
                    "allocated_amount": "100000.00"}]
    penalty = (await get(client, f"/{laura['p2']}")).json()["allocations"]
    assert penalty[0].pop("allocated_at").endswith(("Z", "+00:00"))
    assert penalty == [{"charge_id": laura["penalty"], "concept": "PENALTY", "description": "Ruido",
                        "billing_period": None, "due_date": "2026-02-10", "charge_amount": "50000.00",
                        "allocated_amount": "50000.00"}]
    assert (await get(client, f"/{laura['p3']}")).json()["allocations"] == []


async def test_23_applied_amount(client: AsyncClient, laura: dict[str, int]) -> None:
    applied = [(await get(client, f"/{laura[p]}")).json()["applied_amount"] for p in ("p1", "p2", "p3")]
    assert applied == ["100000.00", "50000.00", "0.00"]


async def test_24_credit_amount(client: AsyncClient, laura: dict[str, int]) -> None:
    body = (await get(client, f"/{laura['p2']}")).json()
    assert (body["amount"], body["applied_amount"], body["credit_amount"], body["status"]) == (
        "80000.00", "50000.00", "30000.00", "PARTIALLY_APPLIED")
    assert (await get(client, f"/{laura['p3']}")).json()["credit_amount"] == "40000.00"


async def test_25_unit(client: AsyncClient, db_session: AsyncSession, world: World) -> None:
    i = world.ids
    await as_user(client, db_session, i["laura"])
    assert (await get(client, f"/{i['p1']}")).json()["unit"] == {"id": i["laura_unit"], "label": "B1-TO1-APTO-101"}
    # Ana moved out: her payment keeps the apartment of its receipt.
    await as_user(client, db_session, i["ana"])
    assert (await get(client, f"/{i['p6']}")).json()["unit"] == {"id": i["shared_unit"], "label": "B1-TO1-APTO-103"}


async def test_26_registered_at(client: AsyncClient, db_session: AsyncSession, laura: dict[str, int]) -> None:
    body = (await get(client, f"/{laura['p1']}")).json()
    stored = await db_session.scalar(select(Payment.registered_at).where(Payment.id == laura["p1"]))
    assert body["registered_at"].endswith(("Z", "+00:00"))  # always UTC
    assert body["registered_at"].replace("Z", "+00:00").startswith(stored.isoformat())  # type: ignore[union-attr]


async def test_detail_carries_no_other_data(client: AsyncClient, db_session: AsyncSession, laura: dict[str, int]) -> None:
    dump = (await get(client, f"/{laura['p3']}")).text + (await get(client)).text
    admin_email = await db_session.scalar(select(User.email).where(User.role == UserRole.ADMINISTRADOR))
    for absent in ("Laura", "Pedro", "Bruno", "Ana", admin_email, "resident", "registered_by", "receipt_email",
                   "snapshot", "password", "token"):
        assert absent not in dump, absent  # type: ignore[operator]


# --- 27-32. Validation -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "field", "message"),
    [
        ({"page": 0}, "query.page", "El valor es menor que el mínimo permitido."),
        ({"page": "abc"}, "query.page", "Debe ser un número entero."),
        ({"page": 10_001}, "query.page", "El valor es demasiado grande para el sistema."),
        ({"page_size": 0}, "query.page_size", "El valor es menor que el mínimo permitido."),
        ({"page_size": 101}, "query.page_size", "El valor es demasiado grande para el sistema."),
        ({"status": "PAID"}, "query.status", "Selecciona un estado válido."),
        ({"status": "Aplicado"}, "query.status", "Selecciona un estado válido."),
        ({"date_from": "2026-02-30"}, "query.date_from", "La fecha inicial no es válida."),
        ({"date_from": "05/10/2026"}, "query.date_from", "La fecha inicial no es válida."),
        ({"date_to": "2026-13-01"}, "query.date_to", "La fecha final no es válida."),
        ({"date_from": "2026-10-02", "date_to": "2026-10-01"}, "query.date_to",
         "La fecha final no puede ser anterior a la inicial."),
        ({"search": "Laura"}, "query.search", SEARCH_MESSAGE),
        ({"search": "2026-02-30"}, "query.search", "La fecha buscada no es válida."),
        ({"search": "RC-00000000"}, "query.search", "El número de recibo no es válido."),
        ({"search": "%"}, "query.search", SEARCH_MESSAGE),
        ({"search": "x" * 101}, "query.search", "La búsqueda no puede superar 100 caracteres."),
    ],
)
async def test_27_32_invalid_queries_are_422(
    client: AsyncClient, laura: dict[str, int], params: dict[str, Any], field: str, message: str
) -> None:
    response = await get(client, **params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert details(response) == [{"field": field, "message": message}]
    assert response.headers["cache-control"] == "no-store"


async def test_invalid_payment_ids_are_422(client: AsyncClient, laura: dict[str, int]) -> None:
    for invalid in ("0", "-1", "abc", str(2**64)):
        response = await get(client, f"/{invalid}")
        assert response.status_code == 422, invalid
        assert details(response)[0]["field"] == "path.payment_id"


# --- Security: no IDOR, no leak through the search -------------------------------------------------------------


async def test_another_residents_payment_is_unreachable_by_url_or_search(
    client: AsyncClient, db_session: AsyncSession, world: World
) -> None:
    i = world.ids
    await as_user(client, db_session, i["laura"])
    own = [i["p4"], i["p3"], i["p2"], i["p1"]]
    # Direct URL: Pedro's payment and the payment of the apartment's other resident.
    for foreign in (i["p5"], i["p6"], i["p7"]):
        response = await get(client, f"/{foreign}")
        assert (response.status_code, response.json()) == (404, NOT_FOUND)
    # Search by Pedro's receipt number, in every accepted form: nothing, not even a count.
    for text in (receipt_number(i["p5"]), str(i["p5"]), f"rc{i['p5']}"):
        assert (await get(client, search=text)).json() == {
            "items": [], "page": 1, "page_size": 10, "total": 0, "total_pages": 0}
    # Search by the date of Pedro's payment (same day as p3 and p4): only Laura's.
    assert await ids_of(client, search=ago(10).isoformat()) == [i["p4"], i["p3"]]
    # Filters never widen the ownership.
    assert set(await ids_of(client, page_size=100, date_from=ago(400).isoformat())) == set(own)
    # Bruno lives where Ana lived: he sees his payment only, and never hers.
    await as_user(client, db_session, i["bruno"])
    assert await ids_of(client) == [i["p7"]]
    assert (await get(client, f"/{i['p6']}")).status_code == 404
    assert await ids_of(client, search=receipt_number(i["p6"])) == []


# --- Queries ---------------------------------------------------------------------------------------------------


@pytest.fixture
def statements() -> Iterator[list[str]]:
    seen: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if not statement.lstrip().upper().startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
            seen.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", record)


async def test_the_api_adds_no_query_per_row(
    client: AsyncClient, db_session: AsyncSession, world: World, statements: list[str]
) -> None:
    await as_user(client, db_session, world.ids["pedro"])  # 1 payment
    statements.clear()
    await get(client)
    with_one = len(statements)
    await as_user(client, db_session, world.ids["laura"])  # 4 payments
    statements.clear()
    await get(client)
    assert len(statements) == with_one
