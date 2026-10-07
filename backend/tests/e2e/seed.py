"""Data of the Playwright E2E tests (frontend/e2e), always on the dedicated *_test database.

Usage (from backend/, run by frontend/e2e/support/data.ts):
    E2E_ADMIN_PASSWORD=... E2E_RESIDENT_PASSWORD=... .venv/bin/python -m tests.e2e.seed reset
    .venv/bin/python -m tests.e2e.seed clean

"reset" migrates the test database (Alembic, as the integration tests do), empties
it and creates the scenario below; it prints only ids and labels as JSON (never a
password). "clean" empties it, leaving it as the integration tests expect it.

The financial history is created with the real services (create_charge,
register_payment), so every invariant holds. The only direct change is ending a
residence (ended_at): the application has no move-out flow yet, and a former
resident is the only way an apartment without a resident has a payment history.

Scenario (Bogotá "today" = T):
- ADMINISTRADOR "Administradora Pruebas" and RESIDENTE "Laura Gómez" (B1-TO1-APTO-101),
  both able to sign in; RESIDENTE "Pedro Ruiz" (B1-TO1-APTO-102).
- B1-TO1 apartments 101-106 and B2-TO1 apartments 201-206 (12 active).
- B2-TO1-APTO-204: an "Otro" obligation fully paid by a former resident: balance 0.
- B2-TO1-APTO-205: a former resident's payment with nothing to cover: credit 50.000.
- B2-TO1-APTO-206: an unpaid penalty of 30.000 (no resident): pending balance.
"""

import asyncio
import json
import os
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import bogota_today, utc_now
from app.core.config import get_settings
from app.core.passwords import hash_password
from app.core.sessions import generate_session_token, hash_token
from app.db.session import create_engine
from app.models import Block, Tower, Unit, User, UserRole, UserStatus
from app.models.invitation import InvitationStatus, ResidentInvitation, ResidentUnit
from app.models.finance import FinancialConcept, PaymentMethod
from app.schemas.payment import PaymentCreate
from app.schemas.user import AdminCreate
from app.services import charges as charge_service
from app.services import payments as payment_service
from app.services.mail import OutgoingEmail
from app.services.structure import Actor
from app.services.users import create_first_administrator

BACKEND_DIR = Path(__file__).resolve().parents[2]
ADMIN_EMAIL = "admin.e2e@example.com"
RESIDENT_EMAIL = "laura.e2e@example.com"

# Children before parents (foreign keys): the same order as tests/integration/conftest.py.
TABLES = (
    "feedback", "payment_allocations", "payments", "charges",
    "password_reset_tokens", "resident_units", "resident_invitations", "units", "towers", "blocks",
    "sessions", "login_throttles", "audit_logs", "users",
)


class NoMail:
    """The seed's receipts are not emailed (the E2E payments use the configured sandbox)."""

    async def send(self, email: OutgoingEmail) -> None:
        return None


def test_database_url() -> Any:
    url = get_settings().test_database_url()  # raises unless TEST_DB_NAME ends in "_test" and differs from DB_NAME
    assert url.database is not None and url.database.endswith("_test")
    return url


async def clean(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as db:
        # Late fees before their origins (self-referencing foreign key of charges).
        await db.execute(text("DELETE FROM charges WHERE source_charge_id IS NOT NULL"))
        for table in TABLES:
            await db.execute(text(f"DELETE FROM {table}"))
        await db.commit()


def secret(name: str) -> str:
    value = os.environ.get(name, "")
    if len(value) < 12:
        raise SystemExit(f"{name} is required (at least 12 characters).")
    return value


async def resident(db: AsyncSession, admin_id: int, full_name: str, email: str, document: str, unit_id: int,
                   password: str) -> int:
    """A registered resident living in the apartment (the state HU-019 leaves after activation)."""
    user = User(email=email, full_name=full_name, document_number=document, role=UserRole.RESIDENTE,
                status=UserStatus.ACTIVE, password_hash=hash_password(password))
    db.add(user)
    await db.flush()
    now = utc_now()
    invitation = ResidentInvitation(
        token_hash=hash_token(generate_session_token()), user_id=user.id, unit_id=unit_id, invited_by_user_id=admin_id,
        validity_days=7, status=InvitationStatus.USED, created_at=now - timedelta(days=2),
        expires_at=now + timedelta(days=5), used_at=now - timedelta(days=1),
    )
    db.add(invitation)
    await db.flush()
    db.add(ResidentUnit(user_id=user.id, unit_id=unit_id, invitation_id=invitation.id, started_at=now - timedelta(days=1)))
    await db.commit()
    return user.id


async def end_residence(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        text("UPDATE resident_units SET ended_at = UTC_TIMESTAMP(6) WHERE user_id = :id AND ended_at IS NULL"),
        {"id": user_id},
    )
    await db.commit()


async def pay(db: AsyncSession, actor: Actor, resident_id: int, amount: str, description: str) -> None:
    data = PaymentCreate(resident_id=resident_id, amount=amount, method=PaymentMethod.CASH,
                         concept=FinancialConcept.OTHER, concept_description=description,
                         payment_date=bogota_today().isoformat())
    await payment_service.register_payment(db, data, actor, mailer=NoMail())


async def reset(factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    await clean(factory)
    today = bogota_today()
    async with factory() as db:
        password = secret("E2E_ADMIN_PASSWORD")
        admin = await create_first_administrator(
            db,
            AdminCreate(full_name="Administradora Pruebas", email=ADMIN_EMAIL, password=password,
                        password_confirmation=password),
            audit_source="e2e",
        )
        await db.commit()
        actor = Actor(user_id=admin.id, ip_address=None)

        blocks: dict[str, int] = {}
        towers: dict[str, int] = {}
        units: dict[str, int] = {}
        for block_number in (1, 2):
            block = Block(number=block_number)
            db.add(block)
            await db.flush()
            tower = Tower(block_id=block.id, number=1)
            db.add(tower)
            await db.flush()
            blocks[f"B{block_number}"] = block.id
            towers[f"B{block_number}-TO1"] = tower.id
            for number in range(block_number * 100 + 1, block_number * 100 + 7):
                unit = Unit(tower_id=tower.id, number=number)
                db.add(unit)
                await db.flush()
                units[f"B{block_number}-TO1-APTO-{number}"] = unit.id
        await db.commit()

        laura = await resident(db, admin.id, "Laura Gómez", RESIDENT_EMAIL, "1012345678", units["B1-TO1-APTO-101"],
                               secret("E2E_RESIDENT_PASSWORD"))
        await resident(db, admin.id, "Pedro Ruiz", "pedro.e2e@example.com", "1012345679", units["B1-TO1-APTO-102"],
                       generate_session_token())

        # B2-TO1-APTO-204: fully paid history, former resident: balance 0 (it can be deleted).
        jorge = await resident(db, admin.id, "Jorge León", "jorge.e2e@example.com", "1012345680",
                               units["B2-TO1-APTO-204"], generate_session_token())
        history = await charge_service.create_charge(
            db,
            charge_service.ChargeInput(unit_id=units["B2-TO1-APTO-204"], concept=FinancialConcept.OTHER,
                                       amount=Decimal("80000"), due_date=today - timedelta(days=30),
                                       description="Reparación de la puerta E2E"),
            actor,
        )
        await pay(db, actor, jorge, "80000", "Reparación de la puerta E2E")
        await end_residence(db, jorge)

        # B2-TO1-APTO-205: a payment of a former resident with nothing to cover: credit (saldo a favor).
        marta = await resident(db, admin.id, "Marta Díaz", "marta.e2e@example.com", "1012345681",
                               units["B2-TO1-APTO-205"], generate_session_token())
        await pay(db, actor, marta, "50000", "Anticipo E2E")
        await end_residence(db, marta)

        # B2-TO1-APTO-206: an unpaid penalty, nobody living there: pending balance.
        debt = await charge_service.create_charge(
            db,
            charge_service.ChargeInput(unit_id=units["B2-TO1-APTO-206"], concept=FinancialConcept.PENALTY,
                                       amount=Decimal("30000"), due_date=today + timedelta(days=10),
                                       description="Penalización por ruido E2E"),
            actor,
        )

    return {
        "today": today.isoformat(),
        "admin_email": ADMIN_EMAIL,
        "resident_email": RESIDENT_EMAIL,
        "resident_name": "Laura Gómez",
        "blocks": blocks,
        "towers": towers,
        "units": units,
        "charges": {"history": history.id, "debt": debt.id},
        "laura_id": laura,
    }


def migrate(url: Any) -> None:
    """Alembic, the only source of the schema (it runs its own event loop)."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")


async def main(action: str, url: Any) -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        if action == "reset":
            print(json.dumps(await reset(factory)))
        else:
            await clean(factory)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"migrate", "reset", "clean"}:
        raise SystemExit("usage: python -m tests.e2e.seed migrate|reset|clean")
    database = test_database_url()
    if sys.argv[1] == "migrate":
        migrate(database)
    else:
        asyncio.run(main(sys.argv[1], database))
