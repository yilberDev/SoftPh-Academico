"""HU-009: the rules of a new feedback (new_feedback), without a database."""

from typing import Any

import pytest

from app.core.exceptions import FieldValidationError
from app.models import FeedbackCategory, FeedbackUrgency
from app.services.feedback import NewFeedback, new_feedback

CATEGORY_MESSAGE = "Selecciona una categoría: queja, sugerencia, solicitud u otros."
URGENCY_MESSAGE = "Selecciona la urgencia: alta, media o baja."
VALID = {"subject": "Ruido", "message": "Hay ruido después de las 10 p. m.", "category": "COMPLAINT", "urgency": "HIGH"}


def checked(**changes: Any) -> NewFeedback:
    return new_feedback(**{**VALID, **changes})


def error_of(**changes: Any) -> tuple[str, str]:
    with pytest.raises(FieldValidationError) as error:
        checked(**changes)
    [detail] = error.value.details  # type: ignore[misc]
    return detail["field"], detail["message"]


# --- Category and urgency (criteria 2.2 and 2.3) -----------------------------------------------------------


@pytest.mark.parametrize("category", list(FeedbackCategory))
def test_every_category(category: FeedbackCategory) -> None:
    assert checked(category=category.value).category is category
    assert checked(category=category).category is category


@pytest.mark.parametrize("urgency", list(FeedbackUrgency))
def test_every_urgency(urgency: FeedbackUrgency) -> None:
    assert checked(urgency=urgency.value).urgency is urgency


def test_the_approved_values() -> None:
    assert [c.value for c in FeedbackCategory] == ["COMPLAINT", "SUGGESTION", "REQUEST", "OTHER"]
    assert [u.value for u in FeedbackUrgency] == ["HIGH", "MEDIUM", "LOW"]


@pytest.mark.parametrize("value", [None, "", "Queja", "complaint", "PENDING", "HIGH", 1, True, ["COMPLAINT"]])
def test_category_is_required_and_from_the_list(value: Any) -> None:
    assert error_of(category=value) == ("category", CATEGORY_MESSAGE)


@pytest.mark.parametrize("value", [None, "", "Alta", "high", "URGENT", "COMPLAINT", 2])
def test_urgency_is_required_and_from_the_list(value: Any) -> None:
    assert error_of(urgency=value) == ("urgency", URGENCY_MESSAGE)


# --- Subject (criterion 2.1: optional) ---------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   ", "\n\t"])
def test_subject_is_optional(value: Any) -> None:
    assert checked(subject=value).subject is None


def test_subject_is_one_line_of_at_most_150_characters() -> None:
    assert checked(subject="  Ruido   en la\ntorre 2 ").subject == "Ruido en la torre 2"
    assert checked(subject="a" * 150).subject == "a" * 150
    assert error_of(subject="a" * 151) == ("subject", "El asunto no puede superar 150 caracteres.")
    assert error_of(subject=150) == ("subject", "El asunto debe ser un texto.")


# --- Message (criteria 2.1 and 3.1: required, at most the established maximum) -----------------------------


@pytest.mark.parametrize("value", [None, "", "   ", "\n \t\n"])
def test_message_is_required(value: Any) -> None:
    assert error_of(message=value) == ("message", "Escribe el mensaje.")


def test_message_keeps_its_line_breaks_and_has_at_most_2000_characters() -> None:
    assert checked(message="  Hola.\n\nHay ruido.  ").message == "Hola.\n\nHay ruido."  # clean_text of HU-005
    assert checked(message="a" * 2000).message == "a" * 2000
    assert error_of(message="a" * 2001) == ("message", "El mensaje no puede superar 2000 caracteres.")
    assert error_of(message=["Hola"]) == ("message", "El mensaje debe ser un texto.")


def test_the_first_invalid_field_is_reported() -> None:
    assert error_of(subject="a" * 151, message="", category=None, urgency=None)[0] == "subject"
    assert error_of(message="", category=None)[0] == "message"
