"""Controlled administrative commands, run by whoever operates the server.

Usage:
    python -m app.cli create-admin
"""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AdministratorAlreadyExistsError, AppError
from app.core.validation import error_details
from app.schemas.user import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, AdminCreate
from app.services.users import active_administrator_exists, create_first_administrator

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_UNAVAILABLE = 2

FIELD_LABELS = {
    "full_name": "Nombre completo",
    "email": "Correo electrónico",
    "password": "Contraseña",
    "password_confirmation": "Confirmación de contraseña",
    "document_number": "Número de documento",
    "phone": "Teléfono",
}

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
Prompt = Callable[[str], str]
Output = Callable[[str], None]


async def create_admin(
    session_factory: SessionFactory,
    prompt: Prompt,
    prompt_secret: Prompt,
    output: Output,
) -> int:
    """Interactive creation of the first administrator (HU-001 / CU-001)."""
    async with session_factory() as session:
        if await active_administrator_exists(session):
            output(AdministratorAlreadyExistsError.message)
            return EXIT_REJECTED

        output("Creación del primer administrador de SoftPh-Académico.")
        raw = {
            "full_name": prompt("Nombre completo: "),
            "email": prompt("Correo electrónico: "),
            "document_number": prompt("Número de documento (opcional): "),
            "phone": prompt("Teléfono (opcional): "),
            "password": prompt_secret(f"Contraseña ({PASSWORD_MIN_LENGTH} a {PASSWORD_MAX_LENGTH} caracteres): "),
            "password_confirmation": prompt_secret("Confirmar contraseña: "),
        }

        try:
            data = AdminCreate.model_validate(raw)
        except ValidationError as exc:
            output("No se creó el administrador. Revisa los siguientes datos:")
            for detail in error_details(exc.errors(include_input=False)):
                output(f"  - {FIELD_LABELS.get(detail['field'], detail['field'])}: {detail['message']}")
            return EXIT_REJECTED

        try:
            user = await create_first_administrator(session, data, audit_source="cli")
        except AppError as exc:
            output(f"No se creó el administrador. {exc.message}")
            return EXIT_REJECTED

    output(f"Administrador creado correctamente: {user.email} (id {user.id}).")
    return EXIT_OK


async def _run_create_admin() -> int:
    from app.db.session import SessionLocal, engine

    try:
        return await create_admin(SessionLocal, input, getpass.getpass, print)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Comandos administrativos de SoftPh-Académico.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create-admin", help="Crea el primer administrador (solo si no existe uno activo).")
    parser.parse_args(argv)

    # The password must be typed interactively, never piped or passed as an argument.
    if not sys.stdin.isatty():
        print("Este comando requiere una terminal interactiva.", file=sys.stderr)
        return EXIT_UNAVAILABLE

    try:
        return asyncio.run(_run_create_admin())
    except (KeyboardInterrupt, EOFError):
        print("\nOperación cancelada. No se creó el administrador.", file=sys.stderr)
        return EXIT_UNAVAILABLE
    except (SQLAlchemyError, OSError):
        print(
            "No fue posible conectarse a la base de datos. Verifica el .env y que MySQL esté en ejecución.",
            file=sys.stderr,
        )
        return EXIT_UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main())
