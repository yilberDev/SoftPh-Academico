# SoftPh-Académico

Prototipo académico de gestión administrativa de propiedad horizontal para la Urbanización San Joaquín (Pereira).

> **Guía de ejecución verificada:** [docs/entrega-objetivos/guia-ejecucion.md](docs/entrega-objetivos/guia-ejecucion.md) recoge, paso a paso, la instalación, la creación de las bases, las migraciones, el administrador, el arranque de cada servicio y las pruebas, con los resultados de su última verificación completa.

| Capa | Tecnología |
|---|---|
| Frontend | Astro 7 (SSR, `@astrojs/node`) + Tailwind CSS 4 (`@tailwindcss/vite`) + TypeScript |
| Backend | FastAPI (Python 3.13) + SQLAlchemy 2 async + `asyncmy` |
| Migraciones | Alembic (única fuente de verdad del esquema) |
| Base de datos | MySQL 8 (versión objetivo 8.4 LTS; verificado con 8.0.46) |
| Correo local | Mailpit |

Frontend y API se sirven desde **un único origen**: en desarrollo, Astro reenvía `/api/v1` a FastAPI con el proxy de Vite; en producción, un proxy inverso enruta `/api/v1` a FastAPI y el resto a Astro.

```text
SoftPh-Academico/
├── frontend/   # Astro SSR
├── backend/    # FastAPI, Alembic y pruebas
├── database/   # Preparación de MySQL local (sin DDL del esquema)
├── docs/       # Documentación académica y trazabilidad
├── .claude/    # Skills del proyecto para Claude Code
├── .env.example
└── README.md
```

## Requisitos

- Node.js 24 LTS y npm
- Python 3.13
- MySQL 8 en local: la versión objetivo es 8.4 LTS y el proyecto se verificó completo con **MySQL 8.0.46**
- Mailpit (SMTP en `127.0.0.1:1025`, interfaz en `http://localhost:8025`) para ver los correos de invitación

Si el sistema no trae Python 3.13, se puede obtener solo el intérprete con [uv](https://docs.astral.sh/uv/) (`uv python install 3.13`) y seguir usando `venv` + `pip`.

## Instalación

Desde la raíz del repositorio:

```bash
# Frontend
cd frontend
npm install
npx playwright install chromium   # navegador de las pruebas E2E (una sola vez por equipo)
cd ..

# Backend (usa el intérprete de Python 3.13 disponible, p. ej. python3.13
# o la ruta que devuelve `uv python find 3.13`)
python3.13 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt
```

`requirements.txt` contiene las dependencias de ejecución y `requirements-dev.txt` añade las de pruebas.

## Configuración

Hay **un único `.env` en la raíz**, compartido por frontend y backend:

```bash
cp .env.example .env
```

Edita `.env` y asigna una contraseña propia a `DB_PASSWORD`. Variables:

| Variable | Uso |
|---|---|
| `APP_ENV` | `development`, `test` o `production` (en producción se desactiva `/api/v1/docs`) |
| `API_INTERNAL_URL` | URL interna de FastAPI para las llamadas SSR de Astro y destino del proxy de desarrollo |
| `DB_HOST`, `DB_PORT` | Servidor MySQL |
| `DB_NAME` | Base de datos de desarrollo |
| `DB_USER`, `DB_PASSWORD` | Usuario de la aplicación |
| `TEST_DB_NAME` | Base de datos de pruebas; debe terminar en `_test` y ser distinta de `DB_NAME` |
| `APP_ORIGIN` | Origen público único (frontend + API). Las solicitudes que modifican datos deben venir de él. Navega con ese host exacto: `http://127.0.0.1:4321` es otro origen que `http://localhost:4321` |
| `SESSION_COOKIE_SECURE` | `false` solo con `APP_ENV` `development`/`test` y `APP_ORIGIN` en `http://localhost`, `http://127.0.0.1` o `http://[::1]`. Con `APP_ENV=production` debe ser `true`: si no, la API y `create-admin` no arrancan |
| `SESSION_ABSOLUTE_TTL_MINUTES`, `SESSION_IDLE_TTL_MINUTES` | Duración máxima de la sesión (480) e inactividad permitida (30) |
| `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_FAILURE_WINDOW_MINUTES`, `LOGIN_LOCKOUT_MINUTES` | Control de intentos fallidos: 5 fallos en 15 minutos bloquean 15 minutos |
| `PASSWORD_HASH_MAX_CONCURRENCY` | Operaciones Argon2 simultáneas (64 MiB cada una), fuera del bucle de eventos. Por defecto 2, rango 1–4 |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_TIMEOUT_SECONDS` | Correo saliente (HU-003). Por defecto, Mailpit en `127.0.0.1:1025` (ver [Invitación de residentes](#invitación-de-residentes-hu-003)) |

`.env` nunca se versiona.

## Configuración de MySQL

Crea las bases de desarrollo y de pruebas y el usuario de la aplicación a partir de los valores del `.env`. El script no borra nada ni crea tablas:

```bash
sudo bash database/scripts/create-local-databases.sh
```

Después aplica las migraciones (el esquema se crea con Alembic a medida que se implementan las HU):

```bash
cd backend
.venv/bin/alembic upgrade head
```

Crear una migración nueva (revisa siempre el archivo generado):

```bash
.venv/bin/alembic revision --autogenerate -m "describe the change"
```

## Crear el primer administrador (HU-001)

No existe registro público de administradores. El primer administrador se crea en el servidor con un comando interactivo (la contraseña se pide sin mostrarla en pantalla):

```bash
cd backend
.venv/bin/python -m app.cli create-admin
```

- Obligatorios: nombre completo, correo electrónico (solo caracteres ASCII) y contraseña (12 a 128 caracteres). Opcionales: número de documento y teléfono.
- Si ya existe un administrador activo, el comando rechaza la operación.
- Códigos de salida: `0` creado, `1` datos inválidos o conflicto, `2` sin terminal interactiva o sin conexión a la base de datos.

## Inicio de sesión (HU-002, HU-007)

Con backend y frontend en marcha, abre `http://localhost:4321/login`. Es el mismo formulario para todos los roles y el backend decide el rol:

- el administrador (creado con `create-admin`) llega al panel `/admin`;
- el residente (con la cuenta activada en HU-019) llega a su panel `/resident`.

Cada área solo admite su rol (con otro rol se muestra "Acceso no autorizado"), y la API rechaza con 403 las operaciones de otro rol, aunque se invoquen directamente (HU-018). Sin sesión, la API responde 401 y las páginas protegidas llevan al login. El rol siempre sale de la sesión del servidor, nunca de un dato enviado por el navegador. "Cerrar sesión" invalida la sesión.

- La sesión se guarda en MySQL; la cookie `softph_session` (HttpOnly) solo contiene un identificador aleatorio.
- Tras 5 intentos fallidos en 15 minutos, el acceso con ese correo se bloquea 15 minutos.
- Credenciales incorrectas o cuentas no activas reciben siempre el mismo mensaje genérico.
- "¿Olvidaste tu contraseña?" lleva a la recuperación de contraseña de residentes (HU-016, ver abajo). La recuperación del administrador no existe todavía (criterio 3 de HU-002, pendiente).

| Endpoint | Uso |
|---|---|
| `POST /api/v1/auth/login` | Inicio de sesión (requiere `Origin` del origen público) |
| `GET /api/v1/auth/session` | Usuario actual y token CSRF |
| `POST /api/v1/auth/logout` | Cierre de sesión (requiere `Origin` y cabecera `X-CSRF-Token`) |

## Feedback de residentes (HU-005)

En `http://localhost:4321/admin/feedback` (solo administrador) se consulta el feedback de los residentes, con su categoría y urgencia (HU-009): búsqueda por residente o asunto, filtro por estado (Pendiente o Resuelto), orden y paginación. En el detalle se ve el mensaje original y el residente, se guarda una respuesta (se envía por correo al residente y este la ve en el detalle de su feedback) y se marca como resuelto con una nota interna de gestión (no se envía), con confirmación previa. El historial de acciones sale de la auditoría. Si dos administradores cambian el mismo feedback a la vez, solo un cambio se guarda y el otro ve un aviso para recargar.

El feedback lo envían los residentes (HU-009); las pruebas también crean datos con la función de dominio `create_feedback`.

| Endpoint | Uso |
|---|---|
| `GET /api/v1/admin/feedback?page=1&page_size=10&status=PENDING&search=&sort_by=created_at&sort_order=desc` | Listado (`sort_by`: `created_at`, `resident`, `subject`, `status`) |
| `GET /api/v1/admin/feedback/{id}` | Detalle con el residente y el historial |
| `POST /api/v1/admin/feedback/{id}/response` | Respuesta: `{response, version}` (requiere `Origin` y `X-CSRF-Token`) |
| `POST /api/v1/admin/feedback/{id}/resolve` | Resolución: `{resolution_note, version}` (requiere `Origin` y `X-CSRF-Token`) |

## Pagos de residentes (HU-004)

En `http://localhost:4321/admin/payments` (solo administrador) se registran los pagos manuales: residente (búsqueda por nombre o documento), monto, método (efectivo, tarjeta o transferencia), concepto (mensualidad, mora, penalización u otro con descripción) y fecha del pago. El backend determina la unidad vigente del residente, aplica el pago a los cargos pendientes de la unidad del más antiguo al más reciente (FIFO) y conserva el excedente como saldo a favor. Después envía el recibo en PDF al correo del residente (Mailpit en desarrollo: `http://localhost:8025`); si el correo falla, el pago queda registrado y se avisa aparte. Debajo está el resumen de pagos, con filtros por residente y fechas, orden y paginación.

No hay dinero real ni pasarelas externas. HU-004 no genera cargos (mensualidades, mora); cuando se implementó, se previó que llegarían con su HU. Hoy las obligaciones las registra y genera el administrador en `/admin/obligations`, una extensión técnica transversal descrita a continuación.

| Endpoint | Uso |
|---|---|
| `POST /api/v1/admin/payments` | Registra un pago: `{resident_id, amount, method, concept, concept_description, payment_date}` (requiere `Origin` y `X-CSRF-Token`) |
| `GET /api/v1/admin/payments?page=1&page_size=10&search=&date_from=&date_to=&sort_by=date&sort_order=desc` | Resumen de pagos (`sort_by`: `date`, `resident`, `amount`, `concept`, `method`, `status`) |

## Obligaciones (extensión técnica transversal)

En `http://localhost:4321/admin/obligations` (solo administrador) se generan las mensualidades de un periodo para los apartamentos activos que aún no la tienen (con vista previa), se registran obligaciones (mensualidad, mora, penalización u otro) y se consultan con filtros y su detalle. Al crear una obligación, el saldo a favor del apartamento se aplica a sus obligaciones pendientes. Un apartamento con saldo distinto de cero no se puede eliminar (`UNIT_HAS_BALANCE`). No es una historia de usuario del documento académico: su asociación a una HU está pendiente de decisión documental (ver [extensiones de diseño](docs/extensiones-diseno.md#extensión-técnica-transversal-de-gestión-de-cargos-y-obligaciones-detalle)).

| Endpoint | Uso |
|---|---|
| `GET /api/v1/admin/charges` | Listado paginado de obligaciones (filtros por apartamento, bloque, torre, concepto, estado, periodo y vencimiento; orden por columna) |
| `POST /api/v1/admin/charges` | Crea una obligación (requiere `Origin` y `X-CSRF-Token`) |
| `GET /api/v1/admin/charges/{charge_id}` | Detalle de una obligación con los pagos aplicados |
| `GET /api/v1/admin/charges/monthly/preview?billing_period=&amount=&due_date=` | Vista previa de la generación mensual (solo lectura) |
| `POST /api/v1/admin/charges/monthly` | Genera las mensualidades confirmadas en la vista previa (requiere `Origin` y `X-CSRF-Token`; 409 `MONTHLY_PREVIEW_OUTDATED` si los datos cambiaron) |

## Pagos del residente (HU-008)

En `http://localhost:4321/resident/payments` (solo residente, "Mis pagos" en su panel) el residente consulta los pagos registrados en su cuenta, del más reciente al más antiguo: fecha, concepto, monto, método, estado (Aplicado, Aplicado parcialmente o Saldo a favor) y número de recibo. Filtra por estado y fechas, busca por número de recibo (`RC-00000012` o `12`) o por fecha (`AAAA-MM-DD`) y pagina; las opciones quedan en la URL. "Ver detalle" muestra la referencia de transacción, la nota de la administración, el monto aplicado, el saldo a favor y las obligaciones que cubrió el pago. Solo consulta: los pagos los registra el administrador (HU-004), que puede indicar una referencia y una nota visible para el residente.

| Endpoint | Uso |
|---|---|
| `GET /api/v1/resident/payments?page=1&page_size=10&status=&date_from=&date_to=&search=` | Pagos del residente de la sesión (`status`: `APPLIED`, `PARTIALLY_APPLIED`, `CREDIT`; `page_size` de 1 a 100) |
| `GET /api/v1/resident/payments/{id}` | Detalle de un pago propio; un pago de otro residente o inexistente responde 404 `PAYMENT_NOT_FOUND` |

## Feedback del residente (HU-009)

En `http://localhost:4321/resident/feedback/new` (solo residente, "Enviar feedback" en su panel) el residente envía un feedback a la administración: asunto opcional (hasta 150 caracteres), mensaje obligatorio (hasta 2000), categoría (Queja, Sugerencia, Solicitud u Otros) y urgencia (Alta, Media o Baja). Queda registrado como Pendiente; "Cancelar" no envía nada y, si hay texto, pide confirmar. En `/resident/feedback` ("Mis feedback") consulta los que ha enviado, del más reciente al más antiguo, con su categoría, urgencia y estado (Pendiente o Resuelto), y en el detalle la fecha de envío y el mensaje. La respuesta y la resolución las registra el administrador (HU-005), que notifica al residente por correo; el detalle muestra al residente la respuesta y la fecha de resolución cuando existen, nunca la nota interna de resolución.

| Endpoint | Uso |
|---|---|
| `POST /api/v1/resident/feedback` | Envía un feedback: `{subject, message, category, urgency}` (`category`: `COMPLAINT`, `SUGGESTION`, `REQUEST`, `OTHER`; `urgency`: `HIGH`, `MEDIUM`, `LOW`; requiere `Origin` y `X-CSRF-Token`) |
| `GET /api/v1/resident/feedback?page=1&page_size=10` | Feedback del residente de la sesión (`page_size` de 1 a 100) |
| `GET /api/v1/resident/feedback/{id}` | Detalle de un feedback propio; uno de otro residente o inexistente responde 404 `NOT_FOUND` |

## Pago electrónico de demostración (HU-010)

Simulación interna de una pasarela de pagos para la demostración académica: **no hay dinero real, proveedores ni pasarelas externas**, no se piden datos de tarjetas, cuentas ni claves y no se envían correos automáticos. Solo existe con `DEMO_PAYMENTS_ENABLED=true` en el `.env` de la raíz (desactivada por defecto en `.env.example`; la API se niega a arrancar con ella activa y `APP_ENV=production`). Desactivada, las rutas de la API no se publican y las páginas responden 404 sin controles. Usa la tabla `demo_payment_transactions`: la base de desarrollo debe estar en el head de Alembic (`.venv/bin/alembic upgrade head`, que incluye la migración `41602195f69f`) antes de habilitarla.

1. En `http://localhost:4321/resident/demo-payments` (solo residente, «Pago de demostración» en su panel) el residente elige una obligación pendiente, el monto (abono parcial hasta el saldo pendiente) y lo confirma; «Iniciar pago de demostración» crea la transacción, que vence a los 15 minutos.
2. La transacción continúa en `/resident/demo-payments/checkout?transaction=<id>`: resumen, aviso de simulación y los instrumentos de prueba ficticios del catálogo del backend (tarjeta terminada en 0001: aprobación; terminada en 0002: rechazo por fondos insuficientes; cuenta de prueba: error técnico). «Procesar pago de demostración» envía solo `{instrument_id}` y **el backend determina el resultado**. Recargar la página recupera la transacción del servidor.
3. Una aprobación registra un pago «Pasarela de demostración» aplicado solo a esa obligación; aparece en «Mis pagos» (HU-008) y en `/admin/payments` (HU-004). Su recibo PDF de demostración se genera bajo demanda en cada descarga (no se guarda ningún archivo). Rechazos, errores, cancelaciones y vencimientos no registran pagos.

| Endpoint | Uso |
|---|---|
| `GET /api/v1/resident/demo-payments/obligations` | Obligaciones del residente con saldo pendiente (y su transacción en curso, si la hay) |
| `GET /api/v1/resident/demo-payments/instruments` | Catálogo de instrumentos de prueba: `{items: [{id, label, kind, ending, expected_result, description}]}` |
| `POST /api/v1/resident/demo-payments` | Inicia una transacción: `{charge_id, amount}` (requiere `Origin` y `X-CSRF-Token`) |
| `GET /api/v1/resident/demo-payments/{transaction_id}` | Transacción propia, con `status`, `test_instrument` y `result_reason` |
| `POST /api/v1/resident/demo-payments/{transaction_id}/complete` | Procesa con un instrumento: exactamente `{instrument_id}` (el antiguo `{outcome}` responde 422; requiere `Origin` y `X-CSRF-Token`) |
| `POST /api/v1/resident/demo-payments/{transaction_id}/cancel` | Cancela una transacción en curso (requiere `Origin` y `X-CSRF-Token`) |
| `GET /api/v1/resident/payments/{payment_id}/receipt` | Recibo PDF de demostración de un pago aprobado propio (no depende de la bandera) |

Pruebas pertinentes (siempre sobre la base `*_test`; nunca ejecutes pytest y Playwright a la vez, porque ambos usan esa base):

```bash
cd backend
.venv/bin/pytest tests/unit/test_demo_*.py tests/integration/test_demo_*.py tests/integration/test_access_control.py

cd frontend
npm test                                    # controladores de obligaciones y checkout (node --test)
npx tsc --noEmit -p tsconfig.json
npm run check                               # astro check
npm run test:e2e -- e2e/demo-payments.spec.ts   # Playwright: levanta su propia API (puerto 8100, base *_test) y Astro (4400) con la simulación habilitada
```

Detalle de diseño y estado de cumplimiento: [extensiones de diseño](docs/extensiones-diseno.md#hu-010--realización-de-pagos-por-el-residente-detalle) y [HU-010](docs/historias-usuario/HU-010.md) (el criterio 2.2 está pendiente de validación académica).

## Residentes registrados (HU-006)

En `http://localhost:4321/admin/residents` (solo administrador) se consultan los residentes registrados: nombre, documento, apartamento (`B1-TO2-APTO-104`), teléfono, correo y estado (Activo o Inactivo). Búsqueda por nombre o documento, filtro por estado, orden por columna y paginación, todo resuelto en el servidor. Las opciones quedan en la URL. Solo consulta: la edición de residentes es HU-015.

| Endpoint | Uso |
|---|---|
| `GET /api/v1/admin/residents?page=1&page_size=10&search=&status=ACTIVE&sort_by=name&sort_order=asc` | Página de residentes (`status`: `ACTIVE`/`INACTIVE`; `sort_by`: `name`, `document`, `unit`, `phone`, `email`, `status`; `page_size` de 1 a 100) |

## Unidades residenciales (HU-013)

En el panel de administración, "Unidades residenciales" (`/admin/blocks`) gestiona la estructura **Bloque → Torre → Apartamento** con navegación jerárquica: `/admin/blocks` → `/admin/blocks/[blockId]` (torres) → `/admin/towers/[towerId]` (apartamentos).

- Cada nivel guarda solo su número (entero positivo, sin máximo de negocio). El identificador oficial lo genera el backend: `B1-TO2-APTO-104`. No se almacena.
- Un bloque activo por número, una torre activa por número dentro del bloque y un apartamento activo por número dentro de la torre.
- Se puede corregir el número; no se puede mover una torre a otro bloque ni un apartamento a otra torre.
- Borrado lógico. Un bloque con torres activas, una torre con apartamentos activos o un apartamento con residente o invitación vigente (HU-003) no se puede eliminar. Los números eliminados se pueden reutilizar.

| Endpoint | Uso |
|---|---|
| `GET`, `POST /api/v1/blocks` | Bloques activos / registrar bloque `{number}` |
| `GET`, `PATCH`, `DELETE /api/v1/blocks/{id}` | Consultar / renumerar `{number, version}` / eliminar `?version=` |
| `GET`, `POST /api/v1/blocks/{block_id}/towers` | Torres del bloque / registrar torre |
| `GET`, `PATCH`, `DELETE /api/v1/towers/{id}` | Consultar / renumerar / eliminar torre |
| `GET`, `POST /api/v1/towers/{tower_id}/units` | Apartamentos de la torre / registrar apartamento |
| `GET`, `PATCH`, `DELETE /api/v1/units/{id}` | Consultar / renumerar / eliminar apartamento |

Todas exigen sesión de administrador; las que modifican datos exigen además `Origin` y la cabecera `X-CSRF-Token`.

## Invitación de residentes (HU-003)

En el panel de administración, "Invitar residente" (`/admin/residents/invite`) registra a un residente, le asigna un apartamento disponible y le envía por correo un enlace para activar su cuenta en `/activate-account` (HU-019, ver la sección siguiente).

- Datos: correo electrónico (mismas reglas que HU-001), nombre completo, apartamento (solo los disponibles) y vigencia de 1 a 30 días (7 por defecto).
- Se crea un usuario RESIDENTE pendiente de activación, sin contraseña, y una invitación que reserva el apartamento. Un apartamento con residente activo o invitación vigente no se ofrece ni se puede eliminar.
- Un correo ya registrado o con una invitación vigente se rechaza. Si la invitación anterior venció, se reinvita al mismo residente.
- El enlace lleva el token en el fragmento (`/activate-account#token=…`); en la base de datos solo se guarda su SHA-256.
- Si el correo falla, la invitación se conserva y se puede **reenviar** (desde la confirmación o la lista de invitaciones pendientes). Reenviar genera un enlace nuevo, invalida el anterior y no amplía la vigencia.

### Correo en desarrollo (Mailpit)

El backend envía los correos por SMTP a [Mailpit](https://mailpit.axllent.org/), que los captura sin entregarlos. Ábrelo en `http://localhost:8025` para ver las invitaciones. Nunca se envía correo real desde desarrollo.

| Variable | Uso (valor por defecto) |
|---|---|
| `SMTP_HOST`, `SMTP_PORT` | Servidor SMTP (`127.0.0.1`, `1025`: Mailpit) |
| `SMTP_FROM` | Remitente (`SoftPh-Académico <no-reply@softph.local>`) |
| `SMTP_TIMEOUT_SECONDS` | Tiempo límite de cada operación SMTP (10) |

TLS y credenciales para un servidor SMTP de producción no están configurados (se definirán en el despliegue).

| Endpoint | Uso |
|---|---|
| `GET /api/v1/units/available` | Apartamentos disponibles para invitar `{items: [{unit_id, label, …}]}` |
| `POST /api/v1/resident-invitations` | Invitar `{email, full_name, unit_id, validity_days}` |
| `GET /api/v1/resident-invitations?status=PENDING` | Invitaciones vigentes (para reenviar) |
| `POST /api/v1/resident-invitations/{id}/resend` | Reenviar con un enlace nuevo |

Todos exigen sesión de administrador; los `POST` exigen además `Origin` y la cabecera `X-CSRF-Token`.

## Activación de la cuenta del residente (HU-019)

El residente abre el enlace del correo (`/activate-account#token=…`). La página es pública:

1. Lee el código del enlace, lo retira de la barra de direcciones y lo guarda solo en memoria (nunca en el almacenamiento del navegador ni en cookies).
2. Verifica la invitación y muestra el nombre, el correo, el apartamento y el vencimiento (no editables).
3. El residente ingresa su documento (obligatorio), teléfono (opcional) y contraseña (12 a 128 caracteres) con su confirmación.
4. Al confirmar, la cuenta queda **activa**, asociada al apartamento, y la invitación queda **utilizada**: el mismo enlace ya no sirve.

Estados de la página: verificando, válida (formulario), inválida, vencida, utilizada, activando, activada y error. Un enlace vencido no permite activar; la administración puede enviar una nueva invitación.

**La activación no inicia sesión:** al terminar, el residente entra con su correo y su nueva contraseña en `/login` (HU-007).

| Endpoint | Uso |
|---|---|
| `POST /api/v1/resident-invitations/verify` | `{token}` → datos de la invitación. 404 `INVITATION_INVALID`, 410 `INVITATION_EXPIRED` / `INVITATION_USED` |
| `POST /api/v1/resident-invitations/activate` | `{token, document_number, phone, password, password_confirmation}` → `{"status": "ACTIVATED"}`. También 409 `DOCUMENT_ALREADY_REGISTERED` / `UNIT_NOT_AVAILABLE` y 422 |

Son públicos (sin sesión ni token CSRF): exigen `Origin` del origen público y el token en el cuerpo, nunca en la URL.

**Prueba local:** con el backend y el frontend en marcha, invita a un residente desde `/admin/residents/invite`, abre el correo en Mailpit (`http://localhost:8025`) y sigue el enlace. Navega con el mismo host de `APP_ORIGIN` (p. ej., `http://localhost:4321`), o la API rechazará la solicitud por `Origin`.

## Recuperación de contraseña del residente (HU-016)

1. En `/login`, "¿Olvidaste tu contraseña?" lleva a `/forgot-password`: el residente ingresa su correo.
2. La respuesta es siempre la misma, exista o no la cuenta. Si el correo es de un residente activo, recibe un enlace `/reset-password#token=…` (Mailpit en desarrollo: `http://localhost:8025`).
3. El enlace vale **1 hora y una sola vez**; pedir otro invalida el anterior. Máximo 3 enlaces por residente por hora (los demás se ignoran en silencio).
4. En `/reset-password` crea su contraseña nueva (12 a 128 caracteres). Se cierran todas sus sesiones abiertas y vuelve a iniciar sesión con la contraseña nueva.

| Endpoint | Uso |
|---|---|
| `POST /api/v1/auth/password-reset/request` | `{email}` → 202 `{"status": "ACCEPTED"}` (siempre igual) |
| `POST /api/v1/auth/password-reset/verify` | `{token}` → `{expires_at}`. 404 `PASSWORD_RESET_INVALID`, 410 `PASSWORD_RESET_EXPIRED` / `_USED` / `_REVOKED` |
| `POST /api/v1/auth/password-reset/confirm` | `{token, password, password_confirmation}` → `{"status": "PASSWORD_UPDATED"}`. También 422 |

Son públicos (sin sesión ni token CSRF): exigen `Origin` del origen público y el token en el cuerpo, nunca en la URL. Solo para residentes.

## Ejecución del backend

```bash
cd backend
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- Health check: `GET http://127.0.0.1:8000/api/v1/health` → `{"status": "ok", "database": "ok"}`
- Documentación interactiva (fuera de producción): `http://127.0.0.1:8000/api/v1/docs`

## Ejecución del frontend

Con el backend en marcha:

```bash
cd frontend
npm run dev
```

`npm run dev` deja el servidor de desarrollo en segundo plano; se administra desde `frontend/` con `npx astro dev status`, `npx astro dev logs` y `npx astro dev stop`.

Abre `http://localhost:4321`: la página inicial es la presentación pública de SoftPh y el acceso está en `/login`. Las peticiones del navegador a `/api/v1/*` se reenvían a FastAPI a través del mismo origen.

## Pruebas

### Backend

Las pruebas están separadas:

- `tests/unit/`: no necesitan MySQL.
- `tests/integration/`: usan la base de datos de pruebas (`TEST_DB_NAME`). Nunca usan la base de desarrollo: se niegan a ejecutarse si `TEST_DB_NAME` no termina en `_test`. El esquema se crea con `alembic upgrade head` y cada prueba se revierte al terminar.

```bash
cd backend
.venv/bin/pytest tests/unit          # sin base de datos
.venv/bin/pytest tests/integration   # requiere MySQL y la base *_test
.venv/bin/pytest                     # todas
```

Las pruebas nunca envían correo: sustituyen el emisor SMTP por uno falso. La única excepción es `tests/integration/test_mailpit.py`, que envía un correo de prueba a Mailpit, lo verifica y lo borra (se omite si Mailpit no está en marcha).

### Frontend

```bash
cd frontend
npm test        # lógica pura (node --test, sin dependencias)
npm run check   # astro check
```

## Build

```bash
cd frontend
npm run build
```

Para ejecutar el servidor SSR compilado (lee el `.env` de la raíz en tiempo de ejecución):

```bash
cd frontend
node --env-file=../.env dist/server/entry.mjs
```

En producción, sirve Astro y FastAPI detrás de un proxy inverso con un único origen (`/api/v1` → FastAPI; el resto → Astro).

## Documentación académica y trazabilidad

Ver [docs/](docs/README.md): historias de usuario, requisitos funcionales, casos de uso, matriz de trazabilidad y extensiones de diseño aprobadas.

## Skills de Claude Code

Las reglas de arquitectura y desarrollo del proyecto están en `.claude/skills/` (`softph-architecture`, `frontend-astro`, `backend-fastapi`, `database-mysql`, `security`, `testing`, `academic-traceability`) y se versionan con el repositorio.
