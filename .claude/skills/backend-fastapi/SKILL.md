---
description: Guide backend development in SoftPh-Academico using FastAPI on Python 3.13, async SQLAlchemy 2.x, asyncmy, and Alembic. Use when creating or reviewing endpoints, services, models, schemas, sessions, authentication, authorization, validation, email, audit, or backend tests.
---

# SoftPh-Académico — Backend FastAPI

## Entorno

- Python 3.13 con `venv` + `pip`. Dependencias declaradas en un archivo versionado (`requirements.txt`).
- Configuración leída del **único `.env` de la raíz del repositorio** mediante un módulo de configuración central en `core/`. Documentar cada variable en el `.env.example` de la raíz. No crear `backend/.env`.

## Arquitectura interna

```text
backend/
├── app/
│   ├── api/v1/     # Routers HTTP bajo /api/v1
│   ├── core/       # Configuración, seguridad, sesiones, CSRF, manejo de errores
│   ├── db/         # Engine async, sesión async, base declarativa
│   ├── models/     # Modelos SQLAlchemy
│   ├── schemas/    # Esquemas Pydantic de entrada/salida
│   ├── services/   # Lógica de negocio, auditoría, correo
│   ├── cli.py      # Comandos administrativos controlados (p. ej., crear primer administrador)
│   └── main.py
├── alembic/        # Migraciones (única fuente de verdad del esquema)
└── tests/
```

- Las rutas solo traducen HTTP ↔ servicio; la lógica de negocio va en `services/`.
- Código, nombres y rutas en inglés.

## API

- Todas las rutas bajo `/api/v1`. FastAPI no sirve páginas; el origen único lo resuelve el proxy (Vite en desarrollo, proxy inverso en producción).
- Usar métodos HTTP y códigos de estado semánticamente correctos.
- Validar entradas con esquemas Pydantic tipados y con límites de longitud/formato.
- No habilitar CORS: el frontend consume la API desde el mismo origen. Cualquier excepción requiere autorización explícita.
- No exponer excepciones internas, SQL, trazas ni secretos al cliente.

### Formato de error estándar

Toda respuesta de error, incluidas las de validación (422) y las no controladas (500), usa este formato:

```json
{
  "error": {
    "code": "MACHINE_READABLE_CODE",
    "message": "Mensaje comprensible para el usuario, en español.",
    "details": []
  }
}
```

- `code`: identificador estable en inglés y mayúsculas (p. ej., `VALIDATION_ERROR`, `NOT_AUTHENTICATED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `CSRF_FAILED`, `INTERNAL_ERROR`).
- `details`: lista opcional con errores por campo (`field`, `message`).
- Implementar con manejadores de excepciones globales registrados en `main.py`.

## Base de datos

- SQLAlchemy 2.x en modo async (`AsyncEngine`, `AsyncSession`) con el driver `asyncmy`.
- Una sesión de base de datos por petición, inyectada como dependencia.
- Alembic con plantilla async para migraciones. No crear tablas con `Base.metadata.create_all`, ni siquiera en pruebas: el esquema de pruebas también se crea con Alembic (ver `testing`).
- No ejecutar DDL destructivo desde código de aplicación.
- Evitar N+1 cargando relaciones de forma explícita.
- Montos con `Numeric(12, 2)` y `decimal.Decimal` en Python; nunca `float`.

## Autenticación, sesiones y autorización

Aplicar las reglas de la skill `security`. En resumen:

- Única ruta de login; el backend determina el rol y lo devuelve en la respuesta.
- Sesiones almacenadas en base de datos, identificadas por una cookie HttpOnly.
- Contraseñas con Argon2id.
- CSRF: token CSRF + comprobación de `Origin` en operaciones de modificación con sesión; solo `Origin` (más el token de un solo uso cuando aplique) en las anónimas.
- Verificar autenticación y rol mediante dependencias reutilizables en cada endpoint protegido. No confiar nunca en un rol enviado por el cliente.
- El primer administrador se crea con un comando de `app/cli.py`; no existe endpoint público de registro de administradores.

## Correo y notificaciones

- En desarrollo, el correo se envía por SMTP a Mailpit local; host y puerto por variables de entorno.
- Se envían correos **únicamente en los flujos que definen los requisitos** (HU/RF/CU). No agregar notificaciones por iniciativa propia; un correo nuevo requiere que un requisito lo defina o una extensión de diseño aprobada.
- El envío de correo se encapsula en un servicio en `services/`; las rutas no construyen correos directamente.
- Un fallo de correo no debe dejar datos inconsistentes; registrar el error en el servidor sin datos sensibles.
- Patrón aprobado (E-1, HU-003): confirmar los datos (COMMIT) **antes** de enviar; nunca enviar SMTP dentro de una transacción ni con bloqueos abiertos; registrar el resultado (`SENT`/`FAILED`) en otra transacción y permitir el reenvío. Usar `MailSender` de `services/mail.py` (dependencia `get_mail_sender`, que las pruebas sustituyen por un emisor falso). En endpoints anónimos donde el tiempo de respuesta podría revelar si una cuenta existe (HU-016), el envío y su registro van en `BackgroundTasks`, con su propia sesión (`get_session_factory`).

## Pago electrónico simulado (HU-010)

- Es una simulación interna implementada en `services/`: no llama a ninguna pasarela externa ni a su sandbox, ni usa SDK o credenciales de terceros.
- No solicita, recibe ni almacena datos de tarjetas.
- El pago simulado se persiste como un pago más, con un origen que lo identifica como simulado, y afecta el saldo igual que cualquier pago válido.
- Se registra en `audit_logs`.

## Auditoría

- Las acciones relevantes se registran en la tabla `audit_logs` (ver `database-mysql`) desde la capa de servicios, dentro de la misma transacción que la acción auditada.
- Nunca registrar contraseñas, tokens ni secretos en auditoría ni en logs.

## Manejo de errores

- Usar excepciones controladas para errores esperados, traducidas al formato de error estándar.
- Registrar información útil en el servidor sin contraseñas ni secretos.
- No devolver trazas internas al usuario final.

## Pruebas

Seguir la skill `testing`. Los endpoints críticos (autenticación, autorización, residentes, pagos, estados de cuenta y operaciones administrativas) requieren pruebas de comportamiento, de errores y de control de acceso.
