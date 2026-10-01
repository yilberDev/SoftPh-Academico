"""HU-013: gestión de unidades residenciales (Bloque → Torre → Apartamento).

Criteria: register (1), list (2), modify (3), delete with confirmation (4,
the confirmation is in the UI) and validate the information (5).
"""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditAction, AuditLog

pytestmark = pytest.mark.anyio

API = "/api/v1"


class Structure:
    """Small helper over the API with the administrator's headers."""

    def __init__(self, client: AsyncClient, headers: dict[str, str]) -> None:
        self.client = client
        self.headers = headers

    async def post(self, path: str, json: Any) -> Response:
        return await self.client.post(f"{API}{path}", json=json, headers=self.headers)

    async def patch(self, path: str, json: Any) -> Response:
        return await self.client.patch(f"{API}{path}", json=json, headers=self.headers)

    async def delete(self, path: str, version: int | None) -> Response:
        params = {} if version is None else {"version": version}
        return await self.client.delete(f"{API}{path}", params=params, headers=self.headers)

    async def get(self, path: str) -> Response:
        return await self.client.get(f"{API}{path}")

    async def block(self, number: int) -> dict[str, Any]:
        response = await self.post("/blocks", {"number": number})
        assert response.status_code == 201, response.text
        return response.json()

    async def tower(self, block_id: int, number: int) -> dict[str, Any]:
        response = await self.post(f"/blocks/{block_id}/towers", {"number": number})
        assert response.status_code == 201, response.text
        return response.json()

    async def unit(self, tower_id: int, number: int) -> dict[str, Any]:
        response = await self.post(f"/towers/{tower_id}/units", {"number": number})
        assert response.status_code == 201, response.text
        return response.json()


@pytest.fixture
def api(client: AsyncClient, admin_headers: dict[str, str]) -> Structure:
    return Structure(client, admin_headers)


def error(response: Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code
    return body


# --- Register and list (criteria 1, 2) -----------------------------------------------


async def test_full_hierarchy_and_official_identifier(api: Structure) -> None:
    block = await api.block(1)
    tower = await api.tower(block["id"], 2)
    unit = await api.unit(tower["id"], 104)

    assert block == {
        "id": block["id"], "number": 1, "name": "B1", "label": "B1", "version": 1,
        "active_towers": 0, "active_units": 0,
    }
    assert tower == {
        "id": tower["id"], "block_id": block["id"], "number": 2, "name": "TO2", "label": "B1-TO2",
        "version": 1, "active_units": 0,
        "block": {"id": block["id"], "number": 1, "name": "B1", "label": "B1"},
    }
    assert unit == {
        "id": unit["id"], "block_id": block["id"], "tower_id": tower["id"], "number": 104, "name": "APTO-104",
        "label": "B1-TO2-APTO-104", "version": 1,
        "block": {"id": block["id"], "number": 1, "name": "B1", "label": "B1"},
        "tower": {"id": tower["id"], "number": 2, "name": "TO2", "label": "B1-TO2"},
    }
    assert (await api.get(f"/units/{unit['id']}")).json() == unit


async def test_list_blocks_sorted_by_number_with_counts(api: Structure) -> None:
    b10 = await api.block(10)
    b2 = await api.block(2)
    b1 = await api.block(1)
    to1 = await api.tower(b1["id"], 1)
    await api.tower(b1["id"], 2)
    await api.unit(to1["id"], 101)
    await api.unit(to1["id"], 102)

    body = (await api.get("/blocks")).json()

    assert body["child_label_prefix"] == "B"
    assert [(b["label"], b["active_towers"], b["active_units"]) for b in body["items"]] == [
        ("B1", 2, 2), ("B2", 0, 0), ("B10", 0, 0)
    ]
    assert [b["id"] for b in body["items"]] == [b1["id"], b2["id"], b10["id"]]


async def test_list_towers_of_a_block(api: Structure) -> None:
    b1 = await api.block(1)
    await api.tower(b1["id"], 10)
    to2 = await api.tower(b1["id"], 2)
    await api.unit(to2["id"], 104)

    body = (await api.get(f"/blocks/{b1['id']}/towers")).json()

    assert body["block"]["label"] == "B1"
    assert body["block"]["active_towers"] == 2
    assert body["child_label_prefix"] == "B1-TO"
    assert [(t["name"], t["label"], t["active_units"]) for t in body["items"]] == [
        ("TO2", "B1-TO2", 1), ("TO10", "B1-TO10", 0)
    ]


async def test_list_units_of_a_tower(api: Structure) -> None:
    b1 = await api.block(1)
    to2 = await api.tower(b1["id"], 2)
    await api.unit(to2["id"], 1104)
    await api.unit(to2["id"], 104)

    body = (await api.get(f"/towers/{to2['id']}/units")).json()

    assert body["block"] == {"id": b1["id"], "number": 1, "name": "B1", "label": "B1"}
    assert body["tower"]["label"] == "B1-TO2"
    assert body["child_label_prefix"] == "B1-TO2-APTO-"
    assert [u["label"] for u in body["items"]] == ["B1-TO2-APTO-104", "B1-TO2-APTO-1104"]


async def test_lists_only_contain_their_own_children(api: Structure) -> None:
    b1, b2 = await api.block(1), await api.block(2)
    t1, t2 = await api.tower(b1["id"], 1), await api.tower(b2["id"], 1)
    await api.unit(t1["id"], 1)
    await api.unit(t2["id"], 2)

    assert [t["id"] for t in (await api.get(f"/blocks/{b2['id']}/towers")).json()["items"]] == [t2["id"]]
    assert [u["label"] for u in (await api.get(f"/towers/{t2['id']}/units")).json()["items"]] == ["B2-TO1-APTO-2"]


async def test_no_business_maximum(api: Structure) -> None:
    block = await api.block(4_294_967_295)
    assert block["label"] == "B4294967295"


# --- Uniqueness ------------------------------------------------------------------------


async def test_duplicate_block(api: Structure) -> None:
    await api.block(1)
    body = error(await api.post("/blocks", {"number": 1}), 409, "BLOCK_NUMBER_EXISTS")
    assert body["message"] == "Ya existe el bloque B1."


async def test_same_tower_number_in_different_blocks_is_valid(api: Structure) -> None:
    b1, b2 = await api.block(1), await api.block(2)
    assert (await api.tower(b1["id"], 2))["label"] == "B1-TO2"
    assert (await api.tower(b2["id"], 2))["label"] == "B2-TO2"


async def test_duplicate_tower_in_the_same_block(api: Structure) -> None:
    b1 = await api.block(1)
    await api.tower(b1["id"], 2)
    body = error(await api.post(f"/blocks/{b1['id']}/towers", {"number": 2}), 409, "TOWER_NUMBER_EXISTS")
    assert body["message"] == "Ya existe la torre TO2 en el bloque B1."


async def test_same_apartment_number_in_different_towers_is_valid(api: Structure) -> None:
    b1 = await api.block(1)
    to2, to3 = await api.tower(b1["id"], 2), await api.tower(b1["id"], 3)
    assert (await api.unit(to2["id"], 104))["label"] == "B1-TO2-APTO-104"
    assert (await api.unit(to3["id"], 104))["label"] == "B1-TO3-APTO-104"


async def test_duplicate_apartment_in_the_same_tower(api: Structure) -> None:
    b1 = await api.block(1)
    to2 = await api.tower(b1["id"], 2)
    await api.unit(to2["id"], 104)
    body = error(await api.post(f"/towers/{to2['id']}/units", {"number": 104}), 409, "UNIT_NUMBER_EXISTS")
    assert body["message"] == "Ya existe el apartamento APTO-104 en B1-TO2."


# --- Modify (criterion 3) -------------------------------------------------------------


async def test_renumber_block_changes_only_derived_identifiers(api: Structure, db_session: AsyncSession) -> None:
    b1 = await api.block(1)
    to2 = await api.tower(b1["id"], 2)
    apto = await api.unit(to2["id"], 104)
    before = await _rows(db_session)

    response = await api.patch(f"/blocks/{b1['id']}", {"number": 4, "version": 1})

    assert response.status_code == 200
    assert response.json() | {"id": None} == {
        "id": None, "number": 4, "name": "B4", "label": "B4", "version": 2, "active_towers": 1, "active_units": 1
    }
    # Same ids, same foreign keys, children untouched; only the label changes.
    unit = (await api.get(f"/units/{apto['id']}")).json()
    assert (unit["id"], unit["tower_id"], unit["block_id"]) == (apto["id"], to2["id"], b1["id"])
    assert unit["label"] == "B4-TO2-APTO-104"
    assert (await api.get(f"/towers/{to2['id']}")).json()["label"] == "B4-TO2"
    after = await _rows(db_session)
    assert after["towers"] == before["towers"]
    assert after["units"] == before["units"]


async def test_renumber_block_onto_an_existing_block(api: Structure) -> None:
    b1 = await api.block(1)
    await api.block(4)
    body = error(await api.patch(f"/blocks/{b1['id']}", {"number": 4, "version": 1}), 409, "BLOCK_NUMBER_EXISTS")
    assert body["message"] == "Ya existe el bloque B4."
    assert (await api.get(f"/blocks/{b1['id']}")).json()["label"] == "B1"


async def test_renumber_tower(api: Structure) -> None:
    b1, b2 = await api.block(1), await api.block(2)
    to2 = await api.tower(b1["id"], 2)
    await api.tower(b2["id"], 3)  # TO3 in another block does not matter.

    response = await api.patch(f"/towers/{to2['id']}", {"number": 3, "version": 1})

    assert response.status_code == 200
    assert (response.json()["label"], response.json()["version"]) == ("B1-TO3", 2)


async def test_renumber_tower_onto_an_existing_tower_of_the_block(api: Structure) -> None:
    b1 = await api.block(1)
    to2 = await api.tower(b1["id"], 2)
    await api.tower(b1["id"], 3)
    body = error(await api.patch(f"/towers/{to2['id']}", {"number": 3, "version": 1}), 409, "TOWER_NUMBER_EXISTS")
    assert body["message"] == "Ya existe la torre TO3 en el bloque B1."


async def test_renumber_apartment(api: Structure) -> None:
    b1 = await api.block(1)
    to2 = await api.tower(b1["id"], 2)
    apto = await api.unit(to2["id"], 104)

    response = await api.patch(f"/units/{apto['id']}", {"number": 105, "version": 1})

    assert response.status_code == 200
    assert response.json()["label"] == "B1-TO2-APTO-105"


async def test_renumber_apartment_onto_an_existing_one(api: Structure) -> None:
    b1 = await api.block(1)
    to2 = await api.tower(b1["id"], 2)
    apto = await api.unit(to2["id"], 104)
    await api.unit(to2["id"], 105)
    body = error(await api.patch(f"/units/{apto['id']}", {"number": 105, "version": 1}), 409, "UNIT_NUMBER_EXISTS")
    assert body["message"] == "Ya existe el apartamento APTO-105 en B1-TO2."


async def test_same_number_is_a_no_op(api: Structure, db_session: AsyncSession) -> None:
    b1 = await api.block(1)
    response = await api.patch(f"/blocks/{b1['id']}", {"number": 1, "version": 1})
    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert await _actions(db_session) == [AuditAction.BLOCK_CREATED]


@pytest.mark.parametrize("level", ["blocks", "towers", "units"])
async def test_stale_version_is_rejected(api: Structure, level: str) -> None:
    path = await _entity_path(api, level)
    assert (await api.patch(path, {"number": 7, "version": 1})).status_code == 200

    body = error(await api.patch(path, {"number": 8, "version": 1}), 409, "VERSION_CONFLICT")
    assert "modificado por otra persona" in body["message"]
    assert (await api.get(path)).json()["number"] == 7


async def test_tower_cannot_move_to_another_block(api: Structure) -> None:
    b1, b2 = await api.block(1), await api.block(2)
    to2 = await api.tower(b1["id"], 2)
    body = error(
        await api.patch(f"/towers/{to2['id']}", {"number": 2, "version": 1, "block_id": b2["id"]}), 422, "VALIDATION_ERROR"
    )
    assert body["details"] == [{"field": "block_id", "message": "El bloque de una torre no se puede cambiar."}]
    assert (await api.get(f"/towers/{to2['id']}")).json()["block_id"] == b1["id"]


async def test_apartment_cannot_move_to_another_tower(api: Structure) -> None:
    b1 = await api.block(1)
    to2, to3 = await api.tower(b1["id"], 2), await api.tower(b1["id"], 3)
    apto = await api.unit(to2["id"], 104)
    body = error(
        await api.patch(f"/units/{apto['id']}", {"number": 104, "version": 1, "tower_id": to3["id"]}),
        422,
        "VALIDATION_ERROR",
    )
    assert body["details"] == [{"field": "tower_id", "message": "La torre de un apartamento no se puede cambiar."}]
    assert (await api.get(f"/units/{apto['id']}")).json()["tower_id"] == to2["id"]


# --- Delete (criterion 4) ---------------------------------------------------------------


async def test_block_with_active_towers_cannot_be_deleted(api: Structure) -> None:
    b1 = await api.block(1)
    await api.tower(b1["id"], 1)
    body = error(await api.delete(f"/blocks/{b1['id']}", 1), 409, "BLOCK_HAS_ACTIVE_TOWERS")
    assert body["message"] == "No se puede eliminar B1 porque tiene torres activas. Elimínalas primero."
    assert (await api.get(f"/blocks/{b1['id']}")).status_code == 200


async def test_tower_with_active_apartments_cannot_be_deleted(api: Structure) -> None:
    b1 = await api.block(1)
    to2 = await api.tower(b1["id"], 2)
    await api.unit(to2["id"], 104)
    body = error(await api.delete(f"/towers/{to2['id']}", 1), 409, "TOWER_HAS_ACTIVE_UNITS")
    assert body["message"] == "No se puede eliminar B1-TO2 porque tiene apartamentos activos. Elimínalos primero."


async def test_delete_bottom_up_is_soft_and_keeps_rows(api: Structure, db_session: AsyncSession) -> None:
    b1 = await api.block(1)
    to2 = await api.tower(b1["id"], 2)
    apto = await api.unit(to2["id"], 104)

    assert (await api.delete(f"/units/{apto['id']}", 1)).status_code == 204
    assert (await api.delete(f"/towers/{to2['id']}", 1)).status_code == 204
    assert (await api.delete(f"/blocks/{b1['id']}", 1)).status_code == 204

    rows = await _rows(db_session)
    # Rows and ids are kept (history), marked as deleted, version incremented.
    assert [(r[0], r[-1] is not None, r[-2]) for r in rows["blocks"]] == [(b1["id"], True, 2)]
    assert [(r[0], r[-1] is not None, r[-2]) for r in rows["towers"]] == [(to2["id"], True, 2)]
    assert [(r[0], r[-1] is not None, r[-2]) for r in rows["units"]] == [(apto["id"], True, 2)]
    assert (await api.get("/blocks")).json()["items"] == []


async def test_deleted_entities_respond_404(api: Structure) -> None:
    b1 = await api.block(1)
    to2 = await api.tower(b1["id"], 2)
    apto = await api.unit(to2["id"], 104)
    await api.delete(f"/units/{apto['id']}", 1)
    await api.delete(f"/towers/{to2['id']}", 1)
    await api.delete(f"/blocks/{b1['id']}", 1)

    for path, message in [
        (f"/blocks/{b1['id']}", "El bloque no existe o fue eliminado."),
        (f"/blocks/{b1['id']}/towers", "El bloque no existe o fue eliminado."),
        (f"/towers/{to2['id']}", "La torre no existe o fue eliminada."),
        (f"/towers/{to2['id']}/units", "La torre no existe o fue eliminada."),
        (f"/units/{apto['id']}", "El apartamento no existe o fue eliminado."),
    ]:
        assert error(await api.get(path), 404, "NOT_FOUND")["message"] == message
    error(await api.patch(f"/blocks/{b1['id']}", {"number": 9, "version": 2}), 404, "NOT_FOUND")
    error(await api.patch(f"/towers/{to2['id']}", {"number": 9, "version": 2}), 404, "NOT_FOUND")
    error(await api.patch(f"/units/{apto['id']}", {"number": 9, "version": 2}), 404, "NOT_FOUND")
    error(await api.delete(f"/blocks/{b1['id']}", 2), 404, "NOT_FOUND")
    error(await api.delete(f"/towers/{to2['id']}", 2), 404, "NOT_FOUND")
    error(await api.delete(f"/units/{apto['id']}", 2), 404, "NOT_FOUND")
    error(await api.post(f"/blocks/{b1['id']}/towers", {"number": 1}), 404, "NOT_FOUND")
    error(await api.post(f"/towers/{to2['id']}/units", {"number": 1}), 404, "NOT_FOUND")


async def test_unknown_ids_respond_404(api: Structure) -> None:
    for path in ("/blocks/999999", "/blocks/999999/towers", "/towers/999999", "/towers/999999/units", "/units/999999"):
        error(await api.get(path), 404, "NOT_FOUND")
    error(await api.post("/blocks/999999/towers", {"number": 1}), 404, "NOT_FOUND")
    error(await api.post("/towers/999999/units", {"number": 1}), 404, "NOT_FOUND")


async def test_numbers_can_be_reused_after_deletion(api: Structure) -> None:
    b1 = await api.block(1)
    to2 = await api.tower(b1["id"], 2)
    apto = await api.unit(to2["id"], 104)
    await api.delete(f"/units/{apto['id']}", 1)
    new_apto = await api.unit(to2["id"], 104)
    await api.delete(f"/units/{new_apto['id']}", 1)
    await api.delete(f"/towers/{to2['id']}", 1)
    new_to2 = await api.tower(b1["id"], 2)
    await api.delete(f"/towers/{new_to2['id']}", 1)
    await api.delete(f"/blocks/{b1['id']}", 1)
    new_b1 = await api.block(1)

    assert new_apto["id"] != apto["id"] and new_apto["label"] == "B1-TO2-APTO-104"
    assert new_to2["id"] != to2["id"] and new_to2["label"] == "B1-TO2"
    assert new_b1["id"] != b1["id"] and new_b1["label"] == "B1"


async def test_counts_ignore_deleted_children(api: Structure) -> None:
    b1 = await api.block(1)
    to1, to2 = await api.tower(b1["id"], 1), await api.tower(b1["id"], 2)
    apto = await api.unit(to1["id"], 101)
    await api.unit(to1["id"], 102)
    await api.delete(f"/units/{apto['id']}", 1)
    await api.delete(f"/towers/{to2['id']}", 1)

    block = (await api.get(f"/blocks/{b1['id']}")).json()
    assert (block["active_towers"], block["active_units"]) == (1, 1)


@pytest.mark.parametrize("level", ["blocks", "towers", "units"])
async def test_delete_with_stale_version(api: Structure, level: str) -> None:
    path = await _entity_path(api, level)
    await api.patch(path, {"number": 7, "version": 1})
    error(await api.delete(path, 1), 409, "VERSION_CONFLICT")
    assert (await api.get(path)).status_code == 200


async def test_delete_requires_version(api: Structure) -> None:
    b1 = await api.block(1)
    body = error(await api.delete(f"/blocks/{b1['id']}", None), 422, "VALIDATION_ERROR")
    assert body["details"] == [{"field": "query.version", "message": "Este campo es obligatorio."}]


# --- Validation (criterion 5) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("number", "message"),
    [
        (0, "El número debe ser un entero positivo (1 o mayor)."),
        (-5, "El número debe ser un entero positivo (1 o mayor)."),
        ("104", "Debe ser un número entero."),
        (1.5, "Debe ser un número entero."),
        (True, "Debe ser un número entero."),
        (None, "Debe ser un número entero."),
        (4_294_967_296, "El número es demasiado grande para el sistema."),
    ],
)
async def test_invalid_number_on_every_level(api: Structure, number: Any, message: str) -> None:
    b1 = await api.block(1)
    to1 = await api.tower(b1["id"], 1)
    apto = await api.unit(to1["id"], 1)
    for response in (
        await api.post("/blocks", {"number": number}),
        await api.post(f"/blocks/{b1['id']}/towers", {"number": number}),
        await api.post(f"/towers/{to1['id']}/units", {"number": number}),
        await api.patch(f"/units/{apto['id']}", {"number": number, "version": 1}),
    ):
        body = error(response, 422, "VALIDATION_ERROR")
        assert body["details"] == [{"field": "number", "message": message}]


async def test_body_must_be_an_object(api: Structure) -> None:
    body = error(await api.post("/blocks", [1]), 422, "VALIDATION_ERROR")
    assert body["details"][0]["message"] == "El cuerpo de la solicitud debe ser un objeto JSON."


async def test_invalid_json(api: Structure) -> None:
    response = await api.client.post(
        f"{API}/blocks", content=b"{", headers={**api.headers, "Content-Type": "application/json"}
    )
    error(response, 422, "VALIDATION_ERROR")


async def test_extra_fields_are_rejected(api: Structure) -> None:
    body = error(await api.post("/blocks", {"number": 1, "label": "B9"}), 422, "VALIDATION_ERROR")
    assert body["details"] == [{"field": "label", "message": "Este campo no está permitido."}]


@pytest.mark.parametrize("block_id", ["0", "-1", "abc", str(2**63)])
async def test_invalid_path_ids(api: Structure, block_id: str) -> None:
    error(await api.get(f"/blocks/{block_id}"), 422, "VALIDATION_ERROR")


async def test_invalid_data_changes_nothing(api: Structure, db_session: AsyncSession) -> None:
    await api.post("/blocks", {"number": 0})
    assert (await _rows(db_session))["blocks"] == []
    assert await _actions(db_session) == []


# --- Audit ------------------------------------------------------------------------------------


async def test_every_change_is_audited(api: Structure, db_session: AsyncSession) -> None:
    b1 = await api.block(1)
    to2 = await api.tower(b1["id"], 2)
    apto = await api.unit(to2["id"], 104)
    await api.patch(f"/units/{apto['id']}", {"number": 105, "version": 1})
    await api.patch(f"/towers/{to2['id']}", {"number": 3, "version": 1})
    await api.patch(f"/blocks/{b1['id']}", {"number": 4, "version": 1})
    await api.delete(f"/units/{apto['id']}", 2)
    await api.delete(f"/towers/{to2['id']}", 2)
    await api.delete(f"/blocks/{b1['id']}", 2)

    entries = (await db_session.scalars(select(AuditLog).order_by(AuditLog.id))).all()
    b, t, u = b1["id"], to2["id"], apto["id"]
    assert [(e.action, e.entity_type, e.entity_id, e.context) for e in entries] == [
        (AuditAction.BLOCK_CREATED, "block", b, {"number": 1, "label": "B1"}),
        (AuditAction.TOWER_CREATED, "tower", t, {"block_id": b, "number": 2, "label": "B1-TO2"}),
        (AuditAction.UNIT_CREATED, "unit", u, {"block_id": b, "tower_id": t, "number": 104, "label": "B1-TO2-APTO-104"}),
        (
            AuditAction.UNIT_UPDATED, "unit", u,
            {
                "block_id": b, "tower_id": t,
                "number": {"from": 104, "to": 105}, "label": {"from": "B1-TO2-APTO-104", "to": "B1-TO2-APTO-105"},
            },
        ),
        (
            AuditAction.TOWER_UPDATED, "tower", t,
            {"block_id": b, "number": {"from": 2, "to": 3}, "label": {"from": "B1-TO2", "to": "B1-TO3"}},
        ),
        (AuditAction.BLOCK_UPDATED, "block", b, {"number": {"from": 1, "to": 4}, "label": {"from": "B1", "to": "B4"}}),
        (AuditAction.UNIT_DELETED, "unit", u, {"block_id": b, "tower_id": t, "number": 105, "label": "B4-TO3-APTO-105"}),
        (AuditAction.TOWER_DELETED, "tower", t, {"block_id": b, "number": 3, "label": "B4-TO3"}),
        (AuditAction.BLOCK_DELETED, "block", b, {"number": 4, "label": "B4"}),
    ]
    admin_id = await db_session.scalar(text("SELECT id FROM users WHERE email = 'admin@example.com'"))
    assert {e.actor_user_id for e in entries} == {admin_id}
    assert {e.ip_address for e in entries} == {"127.0.0.1"}


async def test_rejected_changes_are_not_audited(api: Structure, db_session: AsyncSession) -> None:
    b1 = await api.block(1)
    await api.tower(b1["id"], 1)
    await api.post("/blocks", {"number": 1})
    await api.patch(f"/blocks/{b1['id']}", {"number": 2, "version": 9})
    await api.delete(f"/blocks/{b1['id']}", 1)
    assert await _actions(db_session) == [AuditAction.BLOCK_CREATED, AuditAction.TOWER_CREATED]


# --- Helpers ----------------------------------------------------------------------------------


async def _entity_path(api: Structure, level: str) -> str:
    b1 = await api.block(1)
    if level == "blocks":
        return f"/blocks/{b1['id']}"
    to1 = await api.tower(b1["id"], 1)
    if level == "towers":
        return f"/towers/{to1['id']}"
    return f"/units/{(await api.unit(to1['id'], 1))['id']}"


async def _rows(db: AsyncSession) -> dict[str, list[tuple[Any, ...]]]:
    result = {}
    for table in ("blocks", "towers", "units"):
        result[table] = [
            tuple(row)
            for row in await db.execute(
                text(f"SELECT id, number, updated_at, version, deleted_at FROM {table} ORDER BY id")
            )
        ]
    return result


async def _actions(db: AsyncSession) -> list[str]:
    return list((await db.scalars(select(AuditLog.action).order_by(AuditLog.id))).all())
