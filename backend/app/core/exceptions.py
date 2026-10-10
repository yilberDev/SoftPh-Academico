"""Expected application errors, translated to the standard error format."""


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"
    message = "La solicitud no es válida."
    # Optional per-field errors ([{"field", "message"}]) of the standard format.
    details: list[dict[str, str]] | None = None

    def __init__(self, message: str | None = None, headers: dict[str, str] | None = None) -> None:
        if message is not None:
            self.message = message
        self.headers = headers
        super().__init__(self.message)


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "La operación entra en conflicto con el estado actual."


class EmailAlreadyRegisteredError(ConflictError):
    code = "EMAIL_ALREADY_REGISTERED"
    message = "El correo electrónico ya está registrado."


class DocumentAlreadyRegisteredError(ConflictError):
    code = "DOCUMENT_ALREADY_REGISTERED"
    message = "El número de documento ya está registrado."


class AdministratorAlreadyExistsError(ConflictError):
    code = "ADMINISTRATOR_ALREADY_EXISTS"
    message = "Ya existe un administrador activo. Este comando solo permite crear el primer administrador."


class InvalidCredentialsError(AppError):
    # Same response for every failure cause, so accounts cannot be enumerated.
    status_code = 401
    code = "INVALID_CREDENTIALS"
    message = "Correo electrónico o contraseña no válidos."


class LoginTemporarilyLockedError(AppError):
    status_code = 429
    code = "LOGIN_TEMPORARILY_LOCKED"

    def __init__(self, retry_after_seconds: int) -> None:
        minutes = max(1, -(-retry_after_seconds // 60))
        unit = "minuto" if minutes == 1 else "minutos"
        super().__init__(
            "Demasiados intentos fallidos. El acceso está bloqueado temporalmente; "
            f"intenta de nuevo en {minutes} {unit}.",
            headers={"Retry-After": str(retry_after_seconds)},
        )


class NotAuthenticatedError(AppError):
    status_code = 401
    code = "NOT_AUTHENTICATED"
    message = "Debes iniciar sesión para continuar."


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"
    message = "No tienes permiso para realizar esta acción."


class CsrfFailedError(AppError):
    status_code = 403
    code = "CSRF_FAILED"
    message = "No se pudo verificar la solicitud. Recarga la página e inténtalo de nuevo."


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "El recurso solicitado no existe."


# --- HU-013: physical structure (Bloque → Torre → Apartamento) ----------------


class BlockNotFoundError(NotFoundError):
    message = "El bloque no existe o fue eliminado."


class TowerNotFoundError(NotFoundError):
    message = "La torre no existe o fue eliminada."


class UnitNotFoundError(NotFoundError):
    message = "El apartamento no existe o fue eliminado."


class VersionConflictError(ConflictError):
    code = "VERSION_CONFLICT"
    message = "El registro fue modificado por otra persona. Recarga la página e inténtalo de nuevo."


class BlockNumberExistsError(ConflictError):
    code = "BLOCK_NUMBER_EXISTS"

    def __init__(self, label: str) -> None:
        super().__init__(f"Ya existe el bloque {label}.")


class TowerNumberExistsError(ConflictError):
    code = "TOWER_NUMBER_EXISTS"

    def __init__(self, tower: str, block: str) -> None:
        super().__init__(f"Ya existe la torre {tower} en el bloque {block}.")


class UnitNumberExistsError(ConflictError):
    code = "UNIT_NUMBER_EXISTS"

    def __init__(self, unit: str, tower: str) -> None:
        super().__init__(f"Ya existe el apartamento {unit} en {tower}.")


class BlockHasActiveTowersError(ConflictError):
    code = "BLOCK_HAS_ACTIVE_TOWERS"

    def __init__(self, label: str) -> None:
        super().__init__(f"No se puede eliminar {label} porque tiene torres activas. Elimínalas primero.")


class TowerHasActiveUnitsError(ConflictError):
    code = "TOWER_HAS_ACTIVE_UNITS"

    def __init__(self, label: str) -> None:
        super().__init__(f"No se puede eliminar {label} porque tiene apartamentos activos. Elimínalos primero.")


class UnitInUseError(ConflictError):
    code = "UNIT_IN_USE"

    def __init__(self, label: str) -> None:
        super().__init__(
            f"No se puede eliminar {label} porque tiene un residente o una invitación pendiente."
        )


class UnitHasBalanceError(ConflictError):
    """Obligations extension (D10): pending balance or credit (saldo a favor) other than zero."""

    code = "UNIT_HAS_BALANCE"

    def __init__(self, label: str) -> None:
        super().__init__(
            f"No se puede eliminar {label} porque tiene obligaciones pendientes o saldo a favor. "
            "Su saldo debe quedar en cero."
        )


# --- HU-003: resident invitations ----------------------------------------------


class UnitNotAvailableError(ConflictError):
    code = "UNIT_NOT_AVAILABLE"
    message = "El apartamento seleccionado no está disponible. Actualiza la lista y elige otro."


class InvitationAlreadyPendingError(ConflictError):
    code = "INVITATION_ALREADY_PENDING"
    message = (
        "Este correo ya tiene una invitación vigente. Puedes reenviarla desde la lista de "
        "invitaciones pendientes."
    )


class InvitationNotFoundError(NotFoundError):
    message = "La invitación no existe."


class InvitationNotPendingError(ConflictError):
    code = "INVITATION_NOT_PENDING"
    message = "La invitación ya fue utilizada y no se puede reenviar."


class InvitationExpiredError(InvitationNotPendingError):
    message = "La invitación venció y no se puede reenviar. Para invitar de nuevo, crea una nueva invitación."


class FieldValidationError(AppError):
    """422 for a rule that needs data the schema does not have (e.g. the invitation email)."""

    status_code = 422
    code = "VALIDATION_ERROR"
    message = "Los datos enviados no son válidos."

    def __init__(self, field: str, message: str) -> None:
        super().__init__()
        self.details = [{"field": field, "message": message}]


class GoneError(AppError):
    status_code = 410
    code = "GONE"
    message = "El recurso ya no está disponible."


# --- HU-019: account activation by the resident ------------------------------------
# The same answers for every caller: an anonymous user only learns something
# about an invitation by holding its 256-bit token.


class InvitationInvalidError(NotFoundError):
    code = "INVITATION_INVALID"
    message = "El enlace de invitación no es válido. Revisa que lo copiaste completo o solicita una nueva invitación."


class InvitationUsedError(GoneError):
    code = "INVITATION_USED"
    message = "Este enlace ya fue utilizado. Tu cuenta ya fue activada."


class InvitationLinkExpiredError(GoneError):
    code = "INVITATION_EXPIRED"
    message = "La invitación ya no se encuentra vigente. Solicita a la administración una nueva invitación."


# --- HU-016: password reset of residents ----------------------------------------------
# Only the holder of a 256-bit token can see these answers; the request step
# itself always answers the same (no enumeration).


class PasswordResetInvalidError(NotFoundError):
    code = "PASSWORD_RESET_INVALID"
    message = "El enlace para restablecer la contraseña no es válido. Solicita uno nuevo."


class PasswordResetExpiredError(GoneError):
    code = "PASSWORD_RESET_EXPIRED"
    message = "El enlace para restablecer la contraseña venció. Solicita uno nuevo."


class PasswordResetUsedError(GoneError):
    code = "PASSWORD_RESET_USED"
    message = "Este enlace ya fue utilizado. Si necesitas cambiar tu contraseña otra vez, solicita uno nuevo."


class PasswordResetRevokedError(GoneError):
    code = "PASSWORD_RESET_REVOKED"
    message = "Este enlace fue reemplazado por uno más reciente. Usa el último correo que recibiste o solicita uno nuevo."


# --- HU-004: manual payments ----------------------------------------------------------


class ResidenceChangedError(ConflictError):
    code = "RESIDENCE_CHANGED"
    message = "La residencia del residente cambió mientras se registraba el pago. Intenta de nuevo."


# --- HU-005: feedback management ------------------------------------------------------


class FeedbackNotFoundError(NotFoundError):
    message = "El feedback no existe."


class FeedbackAlreadyResolvedError(ConflictError):
    code = "FEEDBACK_ALREADY_RESOLVED"
    message = "Este feedback ya fue marcado como resuelto."


class FeedbackVersionConflictError(VersionConflictError):
    message = "El feedback fue actualizado en otra sesión. Vuelve a cargar la información antes de realizar cambios."


class FeedbackResidentNotValidError(AppError):
    """A feedback can only belong to an activated, non-deleted RESIDENTE (domain rule, HU-009 creates them)."""

    status_code = 422
    code = "VALIDATION_ERROR"
    message = "El feedback solo puede pertenecer a un residente activo."


# --- Obligations extension: charges ----------------------------------------------------


class MonthlyFeeExistsError(ConflictError):
    code = "MONTHLY_FEE_EXISTS"

    def __init__(self, label: str, period: str) -> None:
        super().__init__(f"{label} ya tiene la mensualidad de {period}.")


class LateFeeExistsError(ConflictError):
    code = "LATE_FEE_EXISTS"
    message = "La obligación de origen ya tiene una mora."


class MonthlyPreviewOutdatedError(ConflictError):
    """The apartments or the existing monthly fees changed since the preview: nothing was created."""

    code = "MONTHLY_PREVIEW_OUTDATED"
    message = "Los datos cambiaron desde la vista previa. Revisa y confirma de nuevo."

    def __init__(self, preview: object) -> None:
        super().__init__()
        self.preview = preview  # the current preview, for the client to show


class ChargeNotFoundError(NotFoundError):
    code = "CHARGE_NOT_FOUND"
    message = "La obligación no existe."


# --- HU-008: payments of the resident ------------------------------------------------------------


class PaymentNotFoundError(NotFoundError):
    """A payment that does not exist and a payment of another resident look exactly the same."""

    code = "PAYMENT_NOT_FOUND"
    message = "El pago no existe."


# --- HU-010: demo payment transactions of the resident ----------------------------------------------


class ReceiptUnavailableError(AppError):
    """HU-010 (D5): the persisted payment, demo transaction and allocation do not agree, so no receipt
    is generated. The client sees only the generic 500 (no detail); the server logs which payment."""

    status_code = 500
    code = "INTERNAL_ERROR"
    message = "Ocurrió un error interno. Intenta de nuevo más tarde."


class ChargeNotPayableError(ConflictError):
    code = "CHARGE_NOT_PAYABLE"
    message = "La obligación no tiene saldo pendiente por pagar."


class DemoPaymentInProgressError(ConflictError):
    code = "DEMO_PAYMENT_IN_PROGRESS"
    message = "Ya hay un pago en curso para esta obligación. Termínalo o cancélalo antes de iniciar otro."


class DemoTransactionNotFoundError(NotFoundError):
    """A transaction that does not exist and one of another resident look exactly the same."""

    code = "DEMO_TRANSACTION_NOT_FOUND"
    message = "La transacción no existe."


class DemoTransactionExpiredError(ConflictError):
    code = "DEMO_TRANSACTION_EXPIRED"
    message = "La transacción venció. El pago no fue aplicado; inicia uno nuevo si aún quieres pagar."


class DemoTransactionFinishedError(ConflictError):
    code = "DEMO_TRANSACTION_FINISHED"
    message = "La transacción ya terminó y no admite cambios."


class DemoPaymentNotAppliedError(ConflictError):
    """The demo gateway approved, but the payment could not be applied: the transaction ended FAILED."""

    code = "DEMO_PAYMENT_NOT_APPLIED"
    message = "No fue posible completar la transacción. El pago no fue aplicado y tu cuenta no cambió."
