# Anexo C. Correspondencia entre los diagramas y el código

Compara cada elemento de los diagramas de [arquitectura](arquitectura/) y del [modelo de datos](modelo-datos/) con el repositorio (rama `main`; código de la aplicación del commit `7191762`).

El diagrama de componentes tiene dos versiones: [componentes.puml](arquitectura/componentes.puml), con todos los módulos, y [componentes-resumen.puml](arquitectura/componentes-resumen.puml), con solo los componentes principales y sus comunicaciones, legible en una página carta. Ambas se contrastan con la tabla C.1: la resumida agrupa en un solo bloque varios de sus elementos (por ejemplo, «Cliente de la API» reúne el cliente del navegador y el cliente SSR, y el núcleo `core/` no detalla sus módulos).

Verificado el 2026-11-03:

- Las bases `softph_academico` y `softph_academico_test` están en la revisión `d33ea6140186`, la única *head* del repositorio.
- `alembic check` responde «No new upgrade operations detected»: los modelos de `backend/app/models` y el esquema coinciden.
- `information_schema` de `softph_academico_test` contiene 16 tablas de la aplicación, todas InnoDB, más `alembic_version`, con 24 claves foráneas.
- Los diagramas del modelo de datos se generaron leyendo ese `information_schema` (no se escribieron a mano).

## C.1 Componentes de la arquitectura

| Componente del diagrama | Archivo de código | ¿Coincide? | Observaciones |
|---|---|---|---|
| Navegador web | — | Sí | Cliente externo; usa la cookie `softph_session` (HttpOnly, `SameSite=Lax`, `Secure` configurable; `backend/app/core/sessions.py`). |
| `middleware.ts` + `page-access.ts` + `roles.ts` | `frontend/src/middleware.ts`, `frontend/src/lib/auth/page-access.ts`, `frontend/src/lib/auth/roles.ts` | Sí | Consulta la sesión con `getSession()` (`lib/api/session.ts`); responde 403, redirige al login o 503. |
| Páginas SSR | `frontend/src/pages/` (32 archivos `.astro`) | Sí | `astro.config.mjs`: `output: 'server'`, adaptador `@astrojs/node` standalone, `session: false`. |
| `components/` y `layouts/` | `frontend/src/components/`, `frontend/src/layouts/Layout.astro` | **Parcial** | `components/ApiStatus.astro` y `lib/api/health.ts` ya no se usan desde que la landing reemplazó la página de estado (commit `23f592f`). |
| Módulos de dominio de `lib/` | `frontend/src/lib/{auth,residents,structure,obligations,payments,account-statement,reports,feedback,resident,admin,lists,landing}` | Sí | Lógica pura probada en `frontend/tests/`. |
| Cliente del navegador | `frontend/src/lib/api/client.ts` y `lib/api/*.ts` | Sí | `fetch` a `/api/v1` con `credentials: 'same-origin'` y cabecera `X-CSRF-Token`. |
| Cliente SSR | `frontend/src/lib/api/server.ts` y `lib/api/*-server.ts` | Sí | Solo `GET`, contra `API_INTERNAL_URL`, reenviando la cookie. |
| Proxy `/api/v1` de Vite | `frontend/astro.config.mjs` | **Parcial** | Existe solo en el servidor de desarrollo. El README y el comentario del archivo hablan de un proxy inverso en producción, pero el repositorio no contiene ninguna configuración de producción (ni nginx, ni contenedores, ni servicio). |
| `main.py` | `backend/app/main.py`, `backend/app/core/errors.py` | Sí | `create_app` registra los manejadores de error y monta `api_router` bajo `/api/v1`; `include_demo_payments` monta `demo_payments` solo con `DEMO_PAYMENTS_ENABLED`. |
| Routers `api/v1/` | `backend/app/api/v1/router.py` y 16 módulos | Sí | 16 `include_router` en `router.py` (`account_statements` aporta dos routers) más `demo_payments` montado en `main.py`. |
| Dependencias de autorización | `backend/app/api/deps.py` | Sí | `verify_origin`, `get_current_auth` (401), `require_roles` (403), `require_csrf`. |
| Esquemas Pydantic | `backend/app/schemas/` (18 módulos) | Sí | — |
| Servicios | `backend/app/services/` (28 módulos) | Sí | El diagrama nombra los principales; el resto aparece en la tabla de capas. |
| `services/receipts.py` → reportlab | `backend/app/services/receipts.py`; `backend/requirements.txt` (`reportlab==5.0.1`) | Sí | Recibo del pago manual (adjunto por correo) y comprobante del pago de demostración (descarga en `GET /api/v1/resident/payments/{id}/receipt`, nunca por correo). |
| `services/mail.py` → SMTP | `backend/app/services/mail.py` (`SmtpMailSender`, `smtplib`); `backend/app/core/config.py` | Sí | Configuración: `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_TIMEOUT_SECONDS`. Sin TLS ni autenticación: suficiente para Mailpit, no para un servidor real. |
| Modelos SQLAlchemy + `db/` | `backend/app/models/` (10 módulos), `backend/app/db/session.py`, `backend/app/db/base.py` | Sí | `create_async_engine` con `mysql+asyncmy`. |
| Núcleo `core/` | `backend/app/core/` (`sessions.py`, `csrf.py`, `passwords.py`, `config.py`, `exceptions.py`, `errors.py`, `validation.py` y otros) | Sí | El diagrama agrupa además `emails.py`, `money.py`, `clock.py` y `unit_labels.py` en la tabla de capas. |
| CLI `create-admin` | `backend/app/cli.py` | Sí | Exige terminal interactiva; rechaza si ya hay un administrador activo. |
| Alembic | `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/` | Sí | `target_metadata = Base.metadata` (importa `app.models`); 16 revisiones lineales. |
| MySQL 8 | `backend/app/core/config.py` (`drivername="mysql+asyncmy"`); `database/scripts/create-local-databases.sh` | **Parcial** | Servidor instalado: **MySQL 8.0.46**; versión decidida en el README: 8.4 LTS. Desviación ya registrada en `docs/extensiones-diseno.md`. |
| Servidor SMTP (Mailpit) | `/etc/systemd/system/mailpit.service` (fuera del repositorio); `.env.example` | Sí | Mailpit v1.31.0: SMTP `127.0.0.1:1025`, interfaz y API `127.0.0.1:8025`. |
| Despliegue: puertos 4321 / 8000 | `.env.example` (`API_INTERNAL_URL=http://127.0.0.1:8000`, `APP_ORIGIN=http://localhost:4321`), `README.md` | Sí | — |
| Despliegue: puertos 4400 / 8100 | `frontend/playwright.config.ts`, `backend/tests/e2e/serve.py` | Sí | La pila E2E usa siempre `softph_academico_test`. |

## C.2 Tablas del modelo de datos

| Tabla | Archivo del modelo | Clase | Migración que la crea | Migraciones que la modifican | ¿Coincide? | Observaciones |
|---|---|---|---|---|---|---|
| `users` | `models/user.py` | `User` | `633f6e19d008` | `925c4947c7a3` (marca de administrador activo único), `6236c5b88ac2` (correo con comparación binaria y CHECK) | Sí | `email` único (`utf8mb4_0900_bin`); `document_number` único. |
| `audit_logs` | `models/audit_log.py` | `AuditLog` | `633f6e19d008` | — | Sí | `entity_type`/`entity_id` es una referencia polimórfica **sin clave foránea**; no se dibuja como relación. |
| `sessions` | `models/session.py` | `UserSession` | `5cde3a1e780c` | — | Sí | El nombre de la clase (`UserSession`) difiere del de la tabla. |
| `login_throttles` | `models/login_throttle.py` | `LoginThrottle` | `5cde3a1e780c` | — | Sí | Sin claves foráneas: la PK es el hash del correo normalizado, también para correos inexistentes. |
| `blocks` | `models/structure.py` | `Block` | `522bf629af33` | — | Sí | Borrado lógico; número único entre los activos (columna generada). |
| `towers` | `models/structure.py` | `Tower` | `522bf629af33` | — | Sí | — |
| `units` | `models/structure.py` | `Unit` | `522bf629af33` | — | Sí | Solo guarda el número; `B1-TO1-APTO-101` se deriva (`core/unit_labels.py`). |
| `resident_invitations` | `models/invitation.py` | `ResidentInvitation` | `4cfaeb7a0be8` | — | Sí | Solo guarda el SHA-256 del enlace. |
| `resident_units` | `models/invitation.py` | `ResidentUnit` | `4cfaeb7a0be8` | — | Sí | `invitation_id` único: relación 1 a 0..1 con la invitación. |
| `password_reset_tokens` | `models/password_reset.py` | `PasswordResetToken` | `b1b38cfb7fe2` | — | Sí | — |
| `charges` | `models/finance.py` | `Charge` | `6afae8f829c4` | `f3c8fc28babe` (periodo, autor, mora y su FK compuesta) | Sí | La FK de la mora es **compuesta** (`unit_id`, `source_charge_id`, `late_fee_source_link`) → (`unit_id`, `id`, `can_originate_late_fee`); los diagramas la muestran como `source_charge_id`. |
| `payments` | `models/finance.py` | `Payment` | `6afae8f829c4` | `4385fd452231` (referencia y notas), `f697625db563` (origen y método `DEMO_GATEWAY`) | Sí | El número de recibo no se guarda: `RC-` + id con 8 dígitos (`services/allocation.py`). |
| `payment_allocations` | `models/finance.py` | `PaymentAllocation` | `6afae8f829c4` | — | Sí | Única por (`payment_id`, `charge_id`). |
| `feedback` | `models/feedback.py` | `Feedback` | `00d8bbdd687b` | `3e565db40412` (categoría y urgencia) | Sí | Categoría y urgencia admiten nulos (feedback anterior a HU-009). |
| `demo_payment_transactions` | `models/finance.py` | `DemoPaymentTransaction` | `82e81b7ab23d` | `41602195f69f` (`test_instrument`) | Sí | `payment_id` único y con nulos: 0..1. |
| `notifications` | `models/notification.py` | `Notification` | `d33ea6140186` | — | Sí | `entity_type`/`entity_id` polimórfica, sin clave foránea. |

## C.3 Diferencias entre los diagramas y el código

1. **Sin configuración de producción.** El proxy `/api/v1` solo existe en el servidor de desarrollo; el repositorio no define el proxy inverso, el servidor SMTP real ni la forma de desplegar. El diagrama de despliegue se limita, por eso, a desarrollo y pruebas.
2. **Versión de MySQL.** Instalada 8.0.46; decidida 8.4 LTS.
3. **Componentes sin uso.** `ApiStatus.astro` y `lib/api/health.ts` siguen en el repositorio aunque ninguna página los importa.
4. **Columnas generadas fuera de los diagramas.** 11 columnas generadas (`*_marker`, `can_originate_late_fee`, `late_fee_source_link`) implementan unicidades parciales y la FK compuesta de la mora; cada diagrama las lista en una nota.
5. **Referencias polimórficas.** `audit_logs` y `notifications` señalan su entidad sin clave foránea; no aparecen como relaciones.
