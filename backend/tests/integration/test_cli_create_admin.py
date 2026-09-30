from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import EXIT_OK, EXIT_REJECTED, create_admin
from app.models import AuditLog, User, UserRole

pytestmark = pytest.mark.anyio

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
PASSWORD = "correct horse battery"


class Terminal:
    """Scripted answers for the CLI prompts; captures everything printed."""

    def __init__(self, **answers: str) -> None:
        self.answers = {
            "Nombre completo": "Ana María Pérez",
            "Correo electrónico": "admin@example.com",
            "Número de documento": "",
            "Teléfono": "",
            "Contraseña": PASSWORD,
            "Confirmar contraseña": PASSWORD,
            **answers,
        }
        self.lines: list[str] = []

    def prompt(self, label: str) -> str:
        self.lines.append(label)
        key = next(k for k in sorted(self.answers, key=len, reverse=True) if label.startswith(k))
        return self.answers[key]

    def output(self, line: str) -> None:
        self.lines.append(line)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


async def run(session_factory: SessionFactory, **answers: Any) -> tuple[int, Terminal]:
    terminal = Terminal(**answers)
    code = await create_admin(session_factory, terminal.prompt, terminal.prompt, terminal.output)
    return code, terminal


async def count_admins(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(User).where(User.role == UserRole.ADMINISTRADOR)) or 0


async def test_creates_first_administrator(session_factory: SessionFactory, db_session: AsyncSession) -> None:
    code, terminal = await run(session_factory, **{"Número de documento": "1234567", "Teléfono": "3001234567"})

    assert code == EXIT_OK
    assert "Administrador creado correctamente: admin@example.com" in terminal.text
    assert await count_admins(db_session) == 1
    assert await db_session.scalar(select(func.count()).select_from(AuditLog)) == 1


async def test_shows_validation_messages_and_creates_nothing(session_factory: SessionFactory, db_session: AsyncSession) -> None:
    code, terminal = await run(
        session_factory,
        **{"Nombre completo": "", "Correo electrónico": "no-es-correo", "Confirmar contraseña": "otra contraseña larga"},
    )

    assert code == EXIT_REJECTED
    assert "No se creó el administrador. Revisa los siguientes datos:" in terminal.text
    assert "Nombre completo: El nombre completo es obligatorio." in terminal.text
    assert "Correo electrónico: Ingresa un correo electrónico válido." in terminal.text
    assert "Confirmación de contraseña: La confirmación no coincide con la contraseña." in terminal.text
    assert await count_admins(db_session) == 0


async def test_rejects_short_password(session_factory: SessionFactory) -> None:
    code, terminal = await run(session_factory, **{"Contraseña": "corta", "Confirmar contraseña": "corta"})

    assert code == EXIT_REJECTED
    assert "Contraseña: La contraseña debe tener entre 12 y 128 caracteres." in terminal.text


async def test_rejects_duplicate_email(session_factory: SessionFactory, db_session: AsyncSession) -> None:
    db_session.add(User(email="admin@example.com", full_name="Residente Uno", role="RESIDENTE", status="PENDING_ACTIVATION"))
    await db_session.flush()

    code, terminal = await run(session_factory)

    assert code == EXIT_REJECTED
    assert "El correo electrónico ya está registrado." in terminal.text
    assert await count_admins(db_session) == 0


async def test_refuses_when_active_administrator_exists_before_asking_anything(session_factory: SessionFactory) -> None:
    assert (await run(session_factory))[0] == EXIT_OK

    code, terminal = await run(session_factory, **{"Correo electrónico": "second@example.com"})

    assert code == EXIT_REJECTED
    assert "Ya existe un administrador activo" in terminal.text
    assert "Contraseña" not in terminal.text


async def test_password_is_never_printed(session_factory: SessionFactory) -> None:
    for answers in ({}, {"Confirmar contraseña": PASSWORD + " distinta"}):
        _, terminal = await run(session_factory, **answers)
        assert PASSWORD not in terminal.text
