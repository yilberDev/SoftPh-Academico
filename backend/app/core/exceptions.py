"""Expected application errors, translated to the standard error format."""


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"
    message = "La solicitud no es válida."

    def __init__(self, message: str | None = None) -> None:
        if message is not None:
            self.message = message
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
