"""Deep review, phase 3 G: list and report timings with a seeded volume (not collected by pytest).

    cd backend && .venv/bin/python -m tests.revision.volume [--out FILE]

Only the dedicated *_test database (refuses anything else). Seeds fictitious data in bulk:
1 administrator, 1000 apartments with 1000 residents (each with an invitation and a residence),
12 monthly fees per apartment (12 000), 20 payments per resident (20 000) allocated FIFO with
allocation.allocate_fifo, 2 feedback per resident and one notification per payment. Then calls
the API in-process (httpx + ASGI) on that database and records, per request: the time (median of
5 runs) and the number of SQL statements (to spot N+1 queries). The database is emptied at the end.
Never run together with pytest or the E2E suite (they share and empty the *_test database).
"""

import argparse
import asyncio
import hashlib
import json
import statistics
import time
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, pool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.sessions import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, derive_csrf_token
from app.db.session import create_engine, get_db
from app.main import app
from app.models import User
from app.services.allocation import PendingCharge, allocate_fifo
from app.services.sessions import create_session

RESIDENTS = 1000
FEES_PER_UNIT = 12
PAYMENTS_PER_RESIDENT = 20
FEE = Decimal("180000.00")
NAMES = ["Laura Gómez", "Pedro Ruiz", "María José Peña", "Andrés Núñez", "Sofía Ramírez", "Iván Muñoz",
         "Lucía Fernández", "Óscar Díaz", "Valentina Ríos", "Tomás Álvarez"]
TABLES = ("notifications", "feedback", "payment_allocations", "payments", "charges", "resident_units",
          "resident_invitations", "units", "towers", "blocks", "sessions", "login_throttles", "audit_logs", "users")


def letters(number: int) -> str:
    """A distinct surname made of letters (names only accept letters): 0 → Aa, 1 → Ab, ..."""
    first, second = divmod(number, 26)
    return chr(ord("A") + first % 26) + chr(ord("a") + second) + ("z" * (first // 26))


async def chunks(session: AsyncSession, sql: str, rows: list[dict[str, Any]], size: int = 2000) -> None:
    for start in range(0, len(rows), size):
        await session.execute(text(sql), rows[start:start + size])


async def empty(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        await session.execute(text("DELETE FROM demo_payment_transactions"))
        await session.execute(text("DELETE FROM charges WHERE source_charge_id IS NOT NULL"))
        for table in TABLES:
            await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()


async def seed(factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    now = utc_now()
    async with factory() as s:
        await s.execute(text(
            "INSERT INTO users (email, password_hash, full_name, role, status) "
            "VALUES ('admin.volumen@example.com', 'x', 'Administradora Volumen', 'ADMINISTRADOR', 'ACTIVE')"))
        admin = int((await s.execute(text("SELECT LAST_INSERT_ID()"))).scalar_one())
        await chunks(s, "INSERT INTO users (email, password_hash, full_name, document_number, role, status) "
                        "VALUES (:email, 'x', :name, :doc, 'RESIDENTE', 'ACTIVE')",
                     [{"email": f"residente{i}@example.com", "name": f"{NAMES[i % 10]} {letters(i)}",
                       "doc": str(10_000_000 + i)} for i in range(RESIDENTS)])
        residents = [r[0] for r in (await s.execute(text(
            "SELECT id FROM users WHERE role = 'RESIDENTE' ORDER BY id"))).all()]
        units: list[tuple[int, str]] = []
        for block in range(1, 11):
            await s.execute(text("INSERT INTO blocks (number) VALUES (:n)"), {"n": block})
            block_id = int((await s.execute(text("SELECT LAST_INSERT_ID()"))).scalar_one())
            await s.execute(text("INSERT INTO towers (block_id, number) VALUES (:b, 1)"), {"b": block_id})
            tower_id = int((await s.execute(text("SELECT LAST_INSERT_ID()"))).scalar_one())
            await chunks(s, "INSERT INTO units (tower_id, number) VALUES (:t, :n)",
                         [{"t": tower_id, "n": n} for n in range(101, 201)])
            units += [(r[0], f"B{block}-TO1-APTO-{r[1]}") for r in (await s.execute(text(
                "SELECT id, number FROM units WHERE tower_id = :t ORDER BY number"), {"t": tower_id})).all()]
        await chunks(s, "INSERT INTO resident_invitations (token_hash, user_id, unit_id, invited_by_user_id, "
                        "validity_days, status, delivery_status, send_attempts, created_at, expires_at, used_at, "
                        "last_sent_at) VALUES (:h, :u, :unit, :a, 7, 'USED', 'SENT', 1, :c, :e, :c, :c)",
                     [{"h": hashlib.sha256(f"v{i}".encode()).hexdigest(), "u": residents[i], "unit": units[i][0],
                       "a": admin, "c": now - timedelta(days=400), "e": now - timedelta(days=393)}
                      for i in range(RESIDENTS)])
        invitations = {r[0]: r[1] for r in (await s.execute(text(
            "SELECT user_id, id FROM resident_invitations"))).all()}
        await chunks(s, "INSERT INTO resident_units (user_id, unit_id, invitation_id, started_at) "
                        "VALUES (:u, :unit, :inv, :c)",
                     [{"u": residents[i], "unit": units[i][0], "inv": invitations[residents[i]],
                       "c": now - timedelta(days=400)} for i in range(RESIDENTS)])
        charge_rows = [{"unit": unit_id, "amount": FEE, "due": date(2026, m, 10), "period": date(2026, m, 1),
                        "a": admin} for unit_id, _ in units for m in range(1, FEES_PER_UNIT + 1)]
        await chunks(s, "INSERT INTO charges (unit_id, concept, amount, due_date, billing_period, created_by_user_id) "
                        "VALUES (:unit, 'MONTHLY_FEE', :amount, :due, :period, :a)", charge_rows)
        charges: dict[int, list[PendingCharge]] = {}
        for row in (await s.execute(text(
                "SELECT id, unit_id, due_date, created_at, amount FROM charges ORDER BY unit_id, due_date, id"))).all():
            charges.setdefault(row.unit_id, []).append(PendingCharge(row.id, row.due_date, row.created_at, row.amount))
        payments = []
        for i in range(RESIDENTS):
            unit_id, label = units[i]
            amount = Decimal("90000.00") if i % 2 == 0 else Decimal("120000.00")  # owes / has credit
            for k in range(PAYMENTS_PER_RESIDENT):
                payments.append({"r": residents[i], "unit": unit_id, "a": admin, "amount": amount,
                                 "d": date(2026, 1, 1) + timedelta(days=15 * k), "reg": now - timedelta(days=300 - 15 * k),
                                 "name": f"{NAMES[i % 10]}", "label": label})
        await chunks(s, "INSERT INTO payments (resident_id, unit_id, registered_by_user_id, amount, method, concept, "
                        "source, payment_date, registered_at, resident_name_snapshot, unit_label_snapshot, "
                        "receipt_email_status, receipt_sent_at) VALUES (:r, :unit, :a, :amount, 'CASH', 'MONTHLY_FEE', "
                        "'MANUAL', :d, :reg, :name, :label, 'SENT', :reg)", payments)
        rows = (await s.execute(text("SELECT id, unit_id, amount FROM payments ORDER BY unit_id, payment_date, id"))).all()
        allocations, notifications = [], []
        for row in rows:
            result = allocate_fifo(row.amount, charges[row.unit_id])
            left = {a.charge_id: a.amount for a in result.allocations}
            charges[row.unit_id] = [PendingCharge(c.id, c.due_date, c.created_at, c.outstanding - left.get(c.id, 0))
                                    for c in charges[row.unit_id]]
            allocations += [{"p": row.id, "c": a.charge_id, "amount": a.amount} for a in result.allocations]
        await chunks(s, "INSERT INTO payment_allocations (payment_id, charge_id, amount) VALUES (:p, :c, :amount)",
                     allocations)
        for row in (await s.execute(text("SELECT id, resident_id FROM payments"))).all():
            notifications.append({"r": row.resident_id, "id": row.id, "key": f"payment:{row.id}"})
        await chunks(s, "INSERT INTO notifications (resident_id, event_type, entity_type, entity_id, event_key, title, "
                        "message, email_status, sent_at) VALUES (:r, 'PAYMENT_REGISTERED', 'payment', :id, :key, "
                        "'Pago registrado', 'Se registró tu pago.', 'SENT', UTC_TIMESTAMP(6))", notifications)
        await chunks(s, "INSERT INTO feedback (resident_id, subject, message, category, urgency) "
                        "VALUES (:r, :sub, :msg, 'COMPLAINT', 'MEDIUM')",
                     [{"r": r, "sub": f"Asunto {k} 100% _real_", "msg": "Mensaje de prueba con tildes: ñandú."}
                      for r in residents for k in range(2)])
        await s.commit()
        counts = {t: int(await s.scalar(text(f"SELECT COUNT(*) FROM {t}")) or 0)
                  for t in ("users", "units", "charges", "payments", "payment_allocations", "feedback", "notifications")}
    return {"admin": admin, "resident": residents[0], "resident_odd": residents[1], **counts}


async def measure(factory: async_sessionmaker[AsyncSession], engine: Any, ids: dict[str, int]) -> list[dict[str, Any]]:
    statements = {"n": 0}

    def count(*_: Any) -> None:
        statements["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count)

    async def override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    results = []
    try:
        async with factory() as s:
            admin = await s.get(User, ids["admin"])
            resident = await s.get(User, ids["resident"])
            admin_token = await create_session(s, admin, get_settings(), utc_now())  # type: ignore[arg-type]
            resident_token = await create_session(s, resident, get_settings(), utc_now())  # type: ignore[arg-type]
            await s.commit()
        today = date(2026, 11, 13)
        admin_paths = [
            "/admin/residents", "/admin/residents?search=Laura&sort_by=name", "/admin/residents?page=50",
            "/admin/residents?status=INACTIVE", "/admin/payments", "/admin/payments?search=100000",
            "/admin/payments?sort_by=status&sort_order=desc", "/admin/payments?page=200&page_size=100",
            "/admin/charges", "/admin/charges?status=OVERDUE&sort_by=outstanding&sort_order=desc",
            "/admin/charges?page=1000", f"/admin/residents/{ids['resident']}/account-statement",
            "/admin/feedback", "/admin/feedback?search=100%25", "/admin/reports/pending-balances",
            f"/admin/reports/payments?date_from=2026-01-01&date_to={today.isoformat()}",
            "/admin/reports/payments?date_from=2026-10-01&date_to=2026-10-31",
            "/admin/charges/monthly/preview?billing_period=2027-01&amount=180000&due_date=2027-01-10",
            "/units/available",
        ]
        resident_paths = ["/resident/payments", "/resident/account-statement", "/resident/notifications",
                          "/resident/feedback"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            for token, paths in ((admin_token, admin_paths), (resident_token, resident_paths)):
                client.cookies.set(SESSION_COOKIE_NAME, token)
                for path in paths:
                    times, counts, size, status = [], [], 0, 0
                    for _ in range(5):
                        statements["n"] = 0
                        started = time.perf_counter()
                        response = await client.get(f"/api/v1{path}")
                        times.append((time.perf_counter() - started) * 1000)
                        counts.append(statements["n"])
                        size, status = len(response.content), response.status_code
                    results.append({"path": path, "status": status, "median_ms": round(statistics.median(times), 1),
                                    "max_ms": round(max(times), 1), "sql_statements": max(counts), "bytes": size})
            # The monthly generation of a new period for the 1000 apartments: one transaction each.
            client.cookies.set(SESSION_COOKIE_NAME, admin_token)
            preview = (await client.get("/api/v1/admin/charges/monthly/preview?billing_period=2027-01"
                                        "&amount=180000&due_date=2027-01-10")).json()
            statements["n"] = 0
            started = time.perf_counter()
            generated = await client.post("/api/v1/admin/charges/monthly", headers={
                "Origin": get_settings().app_origin, CSRF_HEADER_NAME: derive_csrf_token(admin_token)}, json={
                "billing_period": "2027-01", "amount": "180000", "due_date": "2027-01-10",
                "expected_to_create": preview["to_create"], "preview_fingerprint": preview["preview_fingerprint"]})
            results.append({"path": "POST /admin/charges/monthly (1000 apartamentos)", "status": generated.status_code,
                            "median_ms": round((time.perf_counter() - started) * 1000, 1), "max_ms": None,
                            "sql_statements": statements["n"], "bytes": len(generated.content)})
    finally:
        app.dependency_overrides.clear()
        event.remove(engine.sync_engine, "before_cursor_execute", count)
    return results


async def main(out: str | None) -> None:
    url = get_settings().test_database_url()  # raises unless TEST_DB_NAME ends in _test and differs from DB_NAME
    assert url.database and url.database.endswith("_test")
    engine = create_engine(url, poolclass=pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await empty(factory)
        started = time.perf_counter()
        ids = await seed(factory)
        seeded_s = round(time.perf_counter() - started, 1)
        results = await measure(factory, engine, ids)
        report = {"seeded": ids, "seed_seconds": seeded_s, "measured_at": datetime.now().isoformat(), "results": results}
        text_report = json.dumps(report, indent=2, default=str)
        print(text_report)
        if out:
            with open(out, "w") as handle:
                handle.write(text_report)
    finally:
        await empty(factory)
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    asyncio.run(main(parser.parse_args().out))
