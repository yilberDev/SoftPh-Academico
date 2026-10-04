"""HU-005: pure rules (schemas, transitions, notification, list query construction)."""

import re
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects import mysql

from app.core.exceptions import FeedbackAlreadyResolvedError, FeedbackVersionConflictError
from app.core.validation import error_details
from app.models import AuditAction, Feedback, FeedbackStatus
from app.schemas.feedback import FeedbackListQuery, FeedbackResolution, FeedbackResponse
from app.services import feedback as service
from app.services.feedback_email import FeedbackNotice, build_feedback_email


def errors(model: Any, **fields: Any) -> list[dict[str, str]]:
    with pytest.raises(ValidationError) as error:
        model.model_validate(fields)
    return error_details(error.value.errors())


# --- Schemas -----------------------------------------------------------------------------------------------


def test_valid_response_keeps_inner_line_breaks() -> None:
    data = FeedbackResponse.model_validate({"response": "  Hola,\n\nya revisamos.  ", "version": 3})
    assert (data.response, data.version) == ("Hola,\n\nya revisamos.", 3)
    assert FeedbackResponse.model_validate({"response": "x" * 2000, "version": 1}).response == "x" * 2000


@pytest.mark.parametrize(("value", "message"), [
    ("", "Escribe la respuesta para el residente."), ("  \n\t ", "Escribe la respuesta para el residente."),
    (None, "Escribe la respuesta para el residente."), ("x" * 2001, "La respuesta no puede superar 2000 caracteres."),
    (12, "La respuesta debe ser un texto."),
])
def test_invalid_response(value: Any, message: str) -> None:
    assert errors(FeedbackResponse, response=value, version=1) == [{"field": "response", "message": message}]


@pytest.mark.parametrize(("value", "message"), [
    ("", "Escribe la nota de resolución."), ("   ", "Escribe la nota de resolución."),
    (None, "Escribe la nota de resolución."), ("x" * 2001, "La nota de resolución no puede superar 2000 caracteres."),
])
def test_resolution_note_is_required(value: Any, message: str) -> None:
    assert errors(FeedbackResolution, resolution_note=value, version=1) == [{"field": "resolution_note", "message": message}]
    assert errors(FeedbackResolution, version=1)[0]["field"] == "resolution_note"


def test_valid_resolution() -> None:
    assert FeedbackResolution.model_validate({"resolution_note": " Se llamó al vecino. ", "version": 2}).resolution_note == "Se llamó al vecino."


@pytest.mark.parametrize("version", [0, -1, "1", 1.0, True, None, 2**32])
def test_invalid_version(version: Any) -> None:
    for model, text_field in ((FeedbackResponse, "response"), (FeedbackResolution, "resolution_note")):
        assert [d["field"] for d in errors(model, **{text_field: "ok", "version": version})] == ["version"]


@pytest.mark.parametrize("field", ["status", "message", "subject", "resident_id", "admin_response", "resolved_at", "id"])
def test_unknown_fields_are_rejected(field: str) -> None:
    assert errors(FeedbackResponse, response="ok", version=1, **{field: "x"}) == [
        {"field": field, "message": "Este campo no está permitido."}]
    assert errors(FeedbackResolution, resolution_note="ok", version=1, **{field: "x"})[0]["field"] == field


def test_list_query_whitelists() -> None:
    query = FeedbackListQuery()
    assert (query.page, query.page_size, query.status, query.search, query.sort_by, query.sort_order) == (
        1, 10, None, None, "created_at", "desc")
    assert FeedbackListQuery(search="  ruido ").search == "ruido"
    assert FeedbackListQuery(search="   ").search is None
    for fields in ({"status": "OPEN"}, {"status": "REVIEWED"}, {"sort_by": "message"}, {"sort_order": "DESC"},
                   {"page_size": 101}, {"page": 0}, {"search": "x" * 101}, {"resident_id": 1}):
        with pytest.raises(ValidationError):
            FeedbackListQuery.model_validate(fields)


# --- Transitions ---------------------------------------------------------------------------------------------


def test_pending_can_change_on_the_same_version() -> None:
    service.ensure_changeable(FeedbackStatus.PENDING, current_version=3, sent_version=3)


def test_resolved_is_final() -> None:
    with pytest.raises(FeedbackAlreadyResolvedError):
        service.ensure_changeable(FeedbackStatus.RESOLVED, current_version=4, sent_version=4)


def test_stale_version_is_checked_first() -> None:
    with pytest.raises(FeedbackVersionConflictError):
        service.ensure_changeable(FeedbackStatus.PENDING, current_version=4, sent_version=3)
    with pytest.raises(FeedbackVersionConflictError):  # even if it was resolved meanwhile
        service.ensure_changeable(FeedbackStatus.RESOLVED, current_version=4, sent_version=3)


def test_only_two_statuses_and_no_reopening() -> None:
    assert {s.value for s in FeedbackStatus} == {"PENDING", "RESOLVED"}
    assert not hasattr(service, "reopen")


def test_audited_actions() -> None:
    assert [a.value for a in service.HISTORY_ACTIONS] == [
        "FEEDBACK_RESPONDED", "FEEDBACK_RESOLVED", "FEEDBACK_NOTIFICATION_SENT", "FEEDBACK_NOTIFICATION_FAILED"]
    assert not hasattr(AuditAction, "FEEDBACK_REVIEWED")  # reading a feedback is not audited (approved)


# --- Notification -------------------------------------------------------------------------------------------------


def test_response_notification() -> None:
    email = build_feedback_email(notice=FeedbackNotice.RESPONSE, to="laura@example.com", resident_name="Laura Gómez",
                                 subject="Ruido en la torre 2", admin_response="Hablaremos con el vecino.")
    assert email.to == "laura@example.com"
    assert email.subject == "Respuesta a tu feedback — SoftPh-Académico"
    assert email.body.startswith("Hola, Laura Gómez:")
    assert "revisó tu feedback «Ruido en la torre 2» y te respondió:" in email.body
    assert "Hablaremos con el vecino." in email.body and email.attachments == ()


def test_resolution_notification_with_response() -> None:
    email = build_feedback_email(notice=FeedbackNotice.RESOLUTION, to="a@example.com", resident_name="Ana",
                                 subject=None, admin_response="Ya se arregló.")
    assert email.subject == "Tu feedback fue resuelto — SoftPh-Académico"
    assert "revisó tu feedback «Sin asunto» y lo marcó como resuelto." in email.body
    assert "Respuesta de la administración:\nYa se arregló." in email.body


def test_resolution_notification_without_response_never_invents_one() -> None:
    email = build_feedback_email(notice=FeedbackNotice.RESOLUTION, to="a@example.com", resident_name="Ana",
                                 subject="Piscina", admin_response=None)
    assert "realizó la gestión correspondiente y lo marcó como resuelto." in email.body
    assert "Respuesta" not in email.body and "respondió" not in email.body


def test_a_response_notification_needs_a_response() -> None:
    with pytest.raises(ValueError):
        build_feedback_email(notice=FeedbackNotice.RESPONSE, to="a@example.com", resident_name="Ana", subject=None,
                             admin_response=None)


def test_notification_has_no_internal_data() -> None:
    signature = build_feedback_email.__code__.co_varnames[: build_feedback_email.__code__.co_kwonlyargcount]
    assert "resolution_note" not in signature  # the internal note cannot even be passed
    email = build_feedback_email(notice=FeedbackNotice.RESOLUTION, to="a@example.com", resident_name="Ana",
                                 subject="X", admin_response="Y")
    for leak in ("token", "contraseña", "password", "version", "resident_id"):
        assert leak not in email.body.lower()


# --- List query construction (filters, sort, pagination, search) -------------------------------------------------


def sql(query: FeedbackListQuery) -> str:
    statement = service._filtered(select(func.count(Feedback.id)).select_from(Feedback), query)
    return re.sub(r"\s+", " ", str(statement.compile(dialect=mysql.dialect())))


def test_status_filter_and_search_by_resident_or_subject() -> None:
    text_sql = sql(FeedbackListQuery(status="PENDING", search="ruido"))
    assert "feedback.status = %s" in text_sql
    assert "users.full_name LIKE concat('%%', %s, '%%') ESCAPE '/'" in text_sql
    assert "feedback.subject LIKE concat('%%', %s, '%%') ESCAPE '/'" in text_sql
    assert "LIKE" not in sql(FeedbackListQuery())


@pytest.mark.parametrize(("field", "column"), [("created_at", "feedback.created_at"), ("resident", "users.full_name"),
                                               ("subject", "feedback.subject"), ("status", "feedback.status")])
def test_sort_whitelist_maps_to_columns(field: str, column: str) -> None:
    [key] = service._sort_keys(FeedbackListQuery(sort_by=field).sort_by)
    assert str(key.compile(dialect=mysql.dialect())) == column
