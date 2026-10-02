"""HU-006: GET /api/v1/admin/residents (registered residents, administrator only)."""

import re
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.sessions import generate_session_token, hash_token
from app.models import Block, ResidentInvitation, ResidentUnit, Tower, Unit, User, UserRole, UserStatus
from app.models.invitation import InvitationStatus
from tests.integration.conftest import sign_in

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]
URL = "/api/v1/admin/residents"


class Community:
    """Builds the structure, accounts and residences directly in the test transaction."""

    def __init__(self, db: AsyncSession, admin_id: int) -> None:
        self.db = db
        self.admin_id = admin_id
        self._blocks: dict[int, int] = {}
        self._towers: dict[tuple[int, int], int] = {}
        self._count = 0

    async def unit(self, block: int, tower: int, number: int) -> int:
        if block not in self._blocks:
            row = Block(number=block)
            self.db.add(row)
            await self.db.flush()
            self._blocks[block] = row.id
        if (block, tower) not in self._towers:
            row = Tower(block_id=self._blocks[block], number=tower)
            self.db.add(row)
            await self.db.flush()
            self._towers[(block, tower)] = row.id
        unit = Unit(tower_id=self._towers[(block, tower)], number=number)
        self.db.add(unit)
        await self.db.flush()
        return unit.id

    async def residence(self, user_id: int, unit_id: int, *, ended: bool = False) -> None:
        now = utc_now()
        invitation = ResidentInvitation(
            token_hash=hash_token(generate_session_token()), user_id=user_id, unit_id=unit_id,
            invited_by_user_id=self.admin_id, validity_days=7, status=InvitationStatus.USED,
            created_at=now - timedelta(days=2), expires_at=now + timedelta(days=5), used_at=now - timedelta(days=1),
        )
        self.db.add(invitation)
        await self.db.flush()
        self.db.add(ResidentUnit(user_id=user_id, unit_id=unit_id, invitation_id=invitation.id,
                                 started_at=now - timedelta(days=1), ended_at=now if ended else None))
        await self.db.flush()

    async def resident(
        self,
        full_name: str,
        document: str | None,
        unit: tuple[int, int, int] | None = (1, 1, 101),
        *,
        phone: str | None = None,
        email: str | None = None,
        status: UserStatus = UserStatus.ACTIVE,
        role: UserRole = UserRole.RESIDENTE,
        ended: bool = False,
        deleted: bool = False,
    ) -> int:
        self._count += 1
        user = User(
            email=email or f"residente{self._count}@example.com", full_name=full_name, document_number=document,
            phone=phone, role=role, status=status,
            password_hash=None if status == UserStatus.PENDING_ACTIVATION else "not-a-real-hash",
            deleted_at=utc_now() if deleted else None,
        )
        self.db.add(user)
        await self.db.flush()
        if unit is not None:
            await self.residence(user.id, await self.unit(*unit), ended=ended)
        return user.id


@pytest.fixture
async def admin(make_user: MakeUser) -> User:
    return await make_user("admin@example.com", password_hash="not-a-real-hash")


@pytest.fixture
async def community(db_session: AsyncSession, admin: User) -> Community:
    return Community(db_session, admin.id)


@pytest.fixture
async def signed_in(client: AsyncClient, db_session: AsyncSession, admin: User) -> None:
    await sign_in(client, db_session, admin)


async def residents(client: AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get(URL, params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def names(page: dict[str, Any]) -> list[str]:
    return [item["full_name"] for item in page["items"]]


# --- Access (HU-018) -----------------------------------------------------------------------------


async def test_administrator_gets_the_list(client: AsyncClient, signed_in: None) -> None:
    response = await client.get(URL)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


async def test_without_session_is_401(client: AsyncClient) -> None:
    response = await client.get(URL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"


async def test_resident_is_403(client: AsyncClient, db_session: AsyncSession, make_user: MakeUser) -> None:
    resident = await make_user("residente@example.com", role=UserRole.RESIDENTE, password_hash="not-a-real-hash")
    await sign_in(client, db_session, resident)

    for params in ({}, {"role": "ADMINISTRADOR"}, {"page_size": "abc"}):
        response = await client.get(URL, params=params)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"


# --- Listing --------------------------------------------------------------------------------------


async def test_lists_registered_residents_with_their_data(
    client: AsyncClient, signed_in: None, community: Community
) -> None:
    laura = await community.resident("Laura Gómez", "1012345678", (1, 2, 104), phone="+573001234567",
                                     email="laura@example.com")

    page = await residents(client)

    assert page == {
        "items": [{
            "id": laura, "full_name": "Laura Gómez", "document_number": "1012345678", "phone": "+573001234567",
            "email": "laura@example.com", "unit": {"id": page["items"][0]["unit"]["id"], "label": "B1-TO2-APTO-104"},
            "status": "ACTIVE",
        }],
        "page": 1, "page_size": 10, "total": 1, "total_pages": 1,
    }


async def test_no_residents(client: AsyncClient, signed_in: None) -> None:
    assert await residents(client) == {"items": [], "page": 1, "page_size": 10, "total": 0, "total_pages": 0}


async def test_never_exposes_internal_data(client: AsyncClient, signed_in: None, community: Community) -> None:
    await community.resident("Laura Gómez", "1012345678")

    response = await client.get(URL)

    assert set(response.json()["items"][0]) == {"id", "full_name", "document_number", "phone", "email", "unit", "status"}
    for forbidden in ("password_hash", "not-a-real-hash", "token", "deleted_at", "role", "DISABLED"):
        assert forbidden not in response.text


# --- Who is a registered resident -------------------------------------------------------------------


async def test_only_registered_residents_with_a_current_residence(
    client: AsyncClient, signed_in: None, community: Community, admin: User
) -> None:
    await community.resident("Activa Vigente", "10000001", (1, 1, 101))
    await community.resident("Inactiva Vigente", "10000002", (1, 1, 102), status=UserStatus.DISABLED)
    # Invited, not activated: no residence yet (HU-019 creates it) ...
    await community.resident("Pendiente Invitada", None, None, status=UserStatus.PENDING_ACTIVATION)
    # ... and even with a residence row, PENDING_ACTIVATION is never a registered resident.
    await community.resident("Pendiente Con Residencia", "10000003", (1, 1, 103), status=UserStatus.PENDING_ACTIVATION)
    await community.resident("Residencia Terminada", "10000004", (1, 1, 104), ended=True)
    await community.resident("Sin Residencia", "10000005", None)
    await community.resident("Cuenta Eliminada", "10000006", (1, 1, 105), deleted=True)
    # Another role with a residence row is not a resident.
    await community.residence(admin.id, await community.unit(1, 1, 106))

    page = await residents(client)

    assert names(page) == ["Activa Vigente", "Inactiva Vigente"]
    assert page["total"] == 2


async def test_moved_resident_appears_once_with_the_current_unit(
    client: AsyncClient, signed_in: None, community: Community
) -> None:
    laura = await community.resident("Laura Gómez", "10000001", (1, 1, 101), ended=True)
    await community.residence(laura, await community.unit(2, 1, 201))

    page = await residents(client)

    assert [item["unit"]["label"] for item in page["items"]] == ["B2-TO1-APTO-201"]


# --- Pagination -------------------------------------------------------------------------------------


async def test_pagination(client: AsyncClient, signed_in: None, community: Community) -> None:
    for number in range(12):
        await community.resident(f"Residente {chr(65 + number)}", f"1000000{number:02d}", (1, 1, 101 + number))

    pages = [await residents(client, page=page, page_size=5) for page in (1, 2, 3)]

    assert [len(p["items"]) for p in pages] == [5, 5, 2]
    assert all((p["total"], p["total_pages"], p["page_size"]) == (12, 3, 5) for p in pages)
    listed = [name for p in pages for name in names(p)]
    assert listed == sorted(listed) and len(set(listed)) == 12


async def test_page_beyond_the_last_is_empty(client: AsyncClient, signed_in: None, community: Community) -> None:
    await community.resident("Laura Gómez", "10000001")

    page = await residents(client, page=5, page_size=10)

    assert page == {"items": [], "page": 5, "page_size": 10, "total": 1, "total_pages": 1}


@pytest.mark.parametrize(
    "params",
    [
        {"page_size": 0}, {"page_size": 101}, {"page_size": "abc"}, {"page_size": -1},
        {"page": 0}, {"page": -3}, {"page": "x"}, {"page": 10_001},
        {"sort_by": "password_hash"}, {"sort_by": "full_name"}, {"sort_by": "name; DROP TABLE users"}, {"sort_by": ""},
        {"sort_order": "ascending"}, {"sort_order": "ASC"}, {"sort_order": "1"},
        {"status": "PENDING_ACTIVATION"}, {"status": "DISABLED"}, {"status": "active"},
        {"search": "x" * 101},
        {"role": "ADMINISTRADOR"}, {"user_id": "1"},
    ],
    ids=lambda params: "&".join(f"{k}={str(v)[:20]}" for k, v in params.items()),
)
async def test_invalid_query_is_422(client: AsyncClient, signed_in: None, params: dict[str, Any]) -> None:
    response = await client.get(URL, params=params)

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR" and body["details"]


# --- Search -------------------------------------------------------------------------------------------


@pytest.fixture
async def several(community: Community) -> None:
    await community.resident("Laura Gómez Ruiz", "1012345678", (1, 1, 101))
    await community.resident("Andrés Gomez", "52345678", (1, 1, 102), status=UserStatus.DISABLED)
    await community.resident("María Ortiz", "1098765432", (1, 1, 103))
    await community.resident("Pedro Laurent", "AB12345", (1, 1, 104), status=UserStatus.DISABLED)


async def test_search_by_name_ignores_case_and_accents(client: AsyncClient, signed_in: None, several: None) -> None:
    assert names(await residents(client, search="GOMEZ")) == ["Andrés Gomez", "Laura Gómez Ruiz"]
    assert names(await residents(client, search="andres")) == ["Andrés Gomez"]
    assert names(await residents(client, search="  laur ")) == ["Laura Gómez Ruiz", "Pedro Laurent"]


async def test_search_by_document(client: AsyncClient, signed_in: None, several: None) -> None:
    assert names(await residents(client, search="345678")) == ["Andrés Gomez", "Laura Gómez Ruiz"]
    assert names(await residents(client, search="1098765432")) == ["María Ortiz"]
    assert names(await residents(client, search="ab123")) == ["Pedro Laurent"]


async def test_search_does_not_look_at_other_columns(
    client: AsyncClient, signed_in: None, community: Community
) -> None:
    await community.resident("Laura Gómez", "10000001", (1, 1, 104), phone="3001234567", email="zeta@example.com")

    for term in ("zeta", "example.com", "300123", "APTO-104", "B1"):
        assert (await residents(client, search=term))["total"] == 0, term


async def test_empty_search_lists_everyone(client: AsyncClient, signed_in: None, several: None) -> None:
    assert (await residents(client, search="   "))["total"] == 4


async def test_wildcards_are_literal(client: AsyncClient, signed_in: None, community: Community) -> None:
    # Stored directly: the registration validators would not accept these names.
    await community.resident("Ana_Ruiz", "10000001", (1, 1, 101))
    await community.resident("Ana Pérez", "10000002", (1, 1, 102))
    await community.resident("Cien 100% Real", "10000003", (1, 1, 103))

    assert names(await residents(client, search="_")) == ["Ana_Ruiz"]
    assert names(await residents(client, search="Ana_")) == ["Ana_Ruiz"]
    assert names(await residents(client, search="%")) == ["Cien 100% Real"]
    assert (await residents(client, search="%%%"))["total"] == 0
    for term in ("\\", "/", "'", '"', "' OR '1'='1", "--", ";"):
        assert (await residents(client, search=term))["total"] == 0, term


# --- Filter -----------------------------------------------------------------------------------------------


async def test_filter_active(client: AsyncClient, signed_in: None, several: None) -> None:
    page = await residents(client, status="ACTIVE")
    assert names(page) == ["Laura Gómez Ruiz", "María Ortiz"]
    assert {item["status"] for item in page["items"]} == {"ACTIVE"}


async def test_filter_inactive(client: AsyncClient, signed_in: None, several: None) -> None:
    page = await residents(client, status="INACTIVE")
    assert names(page) == ["Andrés Gomez", "Pedro Laurent"]
    assert {item["status"] for item in page["items"]} == {"INACTIVE"}


async def test_search_and_filter_together(client: AsyncClient, signed_in: None, several: None) -> None:
    assert names(await residents(client, search="laur", status="ACTIVE")) == ["Laura Gómez Ruiz"]
    assert names(await residents(client, search="laur", status="INACTIVE")) == ["Pedro Laurent"]
    page = await residents(client, search="gomez", status="INACTIVE", page_size=1)
    assert (names(page), page["total"], page["total_pages"]) == (["Andrés Gomez"], 1, 1)


# --- Sorting ------------------------------------------------------------------------------------------------


async def test_sort_by_name_asc_and_desc(client: AsyncClient, signed_in: None, several: None) -> None:
    ascending = names(await residents(client, sort_by="name", sort_order="asc"))
    assert ascending == ["Andrés Gomez", "Laura Gómez Ruiz", "María Ortiz", "Pedro Laurent"]
    assert names(await residents(client, sort_by="name", sort_order="desc")) == ascending[::-1]


async def test_sort_by_document_is_numeric_for_numbers(client: AsyncClient, signed_in: None, community: Community) -> None:
    for name, document, number in (("Uno", "99999", 101), ("Dos", "100000000", 102), ("Tres", "123456", 103)):
        await community.resident(name, document, (1, 1, number))

    page = await residents(client, sort_by="document")
    assert [item["document_number"] for item in page["items"]] == ["99999", "123456", "100000000"]
    page = await residents(client, sort_by="document", sort_order="desc")
    assert [item["document_number"] for item in page["items"]] == ["100000000", "123456", "99999"]


async def test_sort_by_unit_is_numeric(client: AsyncClient, signed_in: None, community: Community) -> None:
    # As text, "B10…" < "B2…" and "APTO-1001" < "APTO-99": the order must be numeric.
    for name, unit in (("A", (10, 1, 101)), ("B", (2, 1, 1001)), ("C", (2, 1, 99)), ("D", (2, 10, 1)), ("E", (2, 2, 5))):
        await community.resident(name, None, unit)

    expected = ["B2-TO1-APTO-99", "B2-TO1-APTO-1001", "B2-TO2-APTO-5", "B2-TO10-APTO-1", "B10-TO1-APTO-101"]
    page = await residents(client, sort_by="unit")
    assert [item["unit"]["label"] for item in page["items"]] == expected
    page = await residents(client, sort_by="unit", sort_order="desc")
    assert [item["unit"]["label"] for item in page["items"]] == expected[::-1]


async def test_sort_by_phone_email_and_status(client: AsyncClient, signed_in: None, community: Community) -> None:
    await community.resident("Uno", None, (1, 1, 101), phone="3200000000", email="c@example.com")
    await community.resident("Dos", None, (1, 1, 102), phone=None, email="a@example.com", status=UserStatus.DISABLED)
    await community.resident("Tres", None, (1, 1, 103), phone="3100000000", email="b@example.com")

    # Residents without a phone go last in both directions.
    assert names(await residents(client, sort_by="phone")) == ["Tres", "Uno", "Dos"]
    assert names(await residents(client, sort_by="phone", sort_order="desc")) == ["Uno", "Tres", "Dos"]
    assert names(await residents(client, sort_by="email")) == ["Dos", "Tres", "Uno"]
    assert names(await residents(client, sort_by="status")) == ["Uno", "Tres", "Dos"]  # Activo, then Inactivo
    assert names(await residents(client, sort_by="status", sort_order="desc"))[0] == "Dos"


async def test_equal_values_keep_a_stable_order_across_pages(
    client: AsyncClient, signed_in: None, community: Community
) -> None:
    for number in range(6):
        await community.resident("Mismo Nombre", f"2000000{number}", (1, 1, 101 + number))

    pages = [await residents(client, sort_by="name", page=page, page_size=2) for page in (1, 2, 3)]
    ids = [item["id"] for p in pages for item in p["items"]]
    assert ids == sorted(ids) and len(set(ids)) == 6


# --- The work is done by MySQL --------------------------------------------------------------------------------


async def test_filter_sort_and_pagination_run_in_the_database(
    client: AsyncClient, signed_in: None, community: Community, db_session: AsyncSession
) -> None:
    for number in range(8):
        await community.resident(f"Residente {number}", f"3000000{number}", (1, 1, 101 + number))
    statements: list[tuple[str, Any]] = []

    def capture(_conn: Any, _cursor: Any, statement: str, parameters: Any, _context: Any, _many: bool) -> None:
        statements.append((statement, parameters))

    engine = db_session.bind.engine.sync_engine  # type: ignore[union-attr]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        page = await residents(client, search="Residente", status="ACTIVE", sort_by="unit", sort_order="desc",
                               page=3, page_size=2)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert [item["unit"]["label"] for item in page["items"]] == ["B1-TO1-APTO-104", "B1-TO1-APTO-103"]
    queries = [(s, p) for s, p in statements if "FROM users" in s and "resident_units" in s]
    assert len(queries) == 2  # one COUNT, one page; nothing loads the whole collection
    count, rows = queries
    assert "count(" in count[0].lower() and "LIMIT" not in count[0]
    sql = re.sub(r"\s+", " ", rows[0])
    assert "LIKE" in sql and "ORDER BY blocks.number DESC, towers.number DESC, units.number DESC" in sql
    assert sql.endswith("LIMIT %s, %s")  # MySQL: LIMIT offset, row_count
    assert list(rows[1])[-2:] == [4, 2]  # skip 4 rows, return 2
    for column in ("password_hash", "users.created_at", "token_hash"):
        assert column not in sql


# --- Changes are reflected (criterion 4.1) -----------------------------------------------------------------------


async def test_changes_are_reflected_on_the_next_query(
    client: AsyncClient, signed_in: None, community: Community, db_session: AsyncSession, admin: User
) -> None:
    laura = await community.resident("Laura Gómez", "10000001", (1, 2, 104))
    unit_id = (await residents(client))["items"][0]["unit"]["id"]

    # The administrator renumbers the apartment (HU-013): the label is derived, never stored.
    version = (await client.get(f"/api/v1/units/{unit_id}")).json()["version"]
    headers = await sign_in(client, db_session, admin)  # Origin + CSRF token for the change
    assert (await client.patch(f"/api/v1/units/{unit_id}", json={"number": 204, "version": version}, headers=headers)).status_code == 200
    # The account is disabled (a future HU-015 operation; here directly in the database).
    await db_session.execute(update(User).where(User.id == laura).values(status=UserStatus.DISABLED, full_name="Laura G. Ruiz"))

    item = (await residents(client))["items"][0]
    assert (item["full_name"], item["unit"]["label"], item["status"]) == ("Laura G. Ruiz", "B1-TO2-APTO-204", "INACTIVE")


async def test_unit_label_is_not_stored(db_session: AsyncSession) -> None:
    columns = (await db_session.execute(text(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME IN ('users', 'resident_units', 'units') AND COLUMN_NAME LIKE '%label%'"
    ))).all()
    assert columns == []
