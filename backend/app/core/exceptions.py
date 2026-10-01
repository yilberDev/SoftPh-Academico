"""Expected application errors, translated to the standard error format."""


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"
    message = "La solicitud no es válida."

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
