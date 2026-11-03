# Correspondencia entre los modelos y el código

Compara cada elemento de [arquitectura.mmd](arquitectura.mmd) y de [modelo-datos.mmd](modelo-datos.mmd) con el repositorio, sobre el commit `f543c74` (HEAD de `main`, 2026-10-26).

Verificaciones ejecutadas el 2026-10-27 sobre la base de pruebas `softph_academico_test`:

- `alembic check`: «No new upgrade operations detected»; los modelos de `backend/app/models` y el esquema de la base coinciden.
- Revisión de la base: `d33ea6140186`, igual a la única *head* del repositorio.
- `information_schema`: 16 tablas de la aplicación más `alembic_version`, todas InnoDB.
- Versión del servidor: MySQL 8.0.46.

El modelo de datos se generó leyendo `information_schema` de esa base, no a mano.

## Arquitectura

| Elemento del modelo | Archivo de código / migración | ¿Coincide? | Observación |
|---|---|---|---|
| Navegador | — | Sí | Cliente externo. Usa HTTP con la cookie `softph_session`. |
| Astro 7 SSR (`@astrojs/node` standalone) y Tailwind CSS 4 | `frontend/astro.config.mjs` (`output: 'server'`, `adapter: node({ mode: 'standalone' })`), `frontend/package.json` | Sí | `session: false`: Astro no guarda sesiones; viven en FastAPI y MySQL. |
| Middleware de acceso a páginas | `frontend/src/middleware.ts`, `frontend/src/lib/auth/page-access.ts`, `frontend/src/lib/auth/roles.ts` | Sí | Consulta la sesión con `getSession()` (`lib/api/session.ts`). Responde 403, redirige al login o 503 si la API no responde. Solo mejora la navegación: la API autoriza cada petición. |
| Páginas | `frontend/src/pages/` (32 archivos `.astro`) | Sí | `/login`, `/admin/**`, `/resident/**`, `/activate-account`, `/forgot-password`, `/reset-password` y `/`. |
| Cliente del navegador | `frontend/src/lib/api/client.ts` y `lib/api/*.ts` | Sí | `fetch` a rutas relativas `/api/v1` (`credentials: 'same-origin'`) con la cabecera `X-CSRF-Token`. |
| Cliente SSR | `frontend/src/lib/api/server.ts` y `lib/api/*-server.ts` | Sí | Solo `GET`. Llama a `API_INTERNAL_URL` y reenvía la cookie del usuario. |
| Proxy `/api/v1` | `frontend/astro.config.mjs` (`vite.server.proxy`) | **Parcial** | Solo existe en el servidor de desarrollo. El comentario dice «Production uses a reverse proxy», pero **el repositorio no contiene configuración de proxy inverso ni de despliegue** (no hay archivos de nginx, Docker ni compose). El mecanismo de producción no es determinable. |
| Aplicación FastAPI | `backend/app/main.py` | Sí | `create_app` registra los manejadores de error y monta los routers bajo `/api/v1`. |
| Routers | `backend/app/api/v1/router.py` | Sí | 16 `include_router` en 15 módulos (`account_statements` aporta los routers de administrador y de residente). `demo_payments` se monta aparte en `main.py`, solo con `DEMO_PAYMENTS_ENABLED` y nunca con `APP_ENV=production`. |
| Dependencias de autorización | `backend/app/api/deps.py` | Sí | `verify_origin` (Origin o Referer, con `core/csrf.py`), `get_current_auth` (401), `require_roles` (403) y `require_csrf`. |
| Sesión y CSRF | `backend/app/core/sessions.py` | Sí | Token de sesión guardado como SHA-256; token CSRF = HMAC-SHA256 de la sesión; cookie HttpOnly, `SameSite=Lax`, `Secure` configurable. |
| Contraseñas | `backend/app/core/passwords.py` | Sí | `argon2-cffi` (Argon2id). |
| Servicios | `backend/app/services/` (28 módulos) | Sí | Reglas de negocio. El diagrama lista solo los principales. |
| Modelos y sesión de BD | `backend/app/models/` (10 módulos, 16 tablas), `backend/app/db/session.py` | Sí | SQLAlchemy 2 asíncrono (`create_async_engine`, `AsyncSession`). |
| MySQL | `backend/app/core/config.py` (`drivername="mysql+asyncmy"`) | Sí, con diferencia de versión | El servidor local es **MySQL 8.0.46**; la versión decidida es 8.4 LTS, desviación ya registrada en [extensiones-diseno.md](../extensiones-diseno.md) («Desviaciones temporales del entorno»). Bases: `softph_academico` (desarrollo) y `softph_academico_test` (pruebas). |
| Alembic | `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/` (16 revisiones) | Sí | `target_metadata = Base.metadata`; motor asíncrono. *Head* `d33ea6140186`. El esquema de producción no se verificó. |
| Correo SMTP | `backend/app/services/mail.py` (`SmtpMailSender`, `smtplib`), `backend/app/core/config.py` (`smtp_host=127.0.0.1`, `smtp_port=1025`) | Sí | En desarrollo y pruebas, Mailpit. La configuración no incluye TLS ni autenticación SMTP. El servidor de producción no está definido. |
| PDF con reportlab | `backend/app/services/receipts.py`, `backend/requirements.txt` (`reportlab==5.0.1`) | Sí | Recibo del pago manual, enviado por correo como adjunto, y comprobante del pago de demostración, que se descarga y nunca se envía por correo. |
| Comando `create-admin` | `backend/app/cli.py` | Sí | Crea el primer administrador desde la terminal; no hay registro público. |

## Modelo de datos (16 tablas)

Todas coinciden según `alembic check`. Las relaciones del diagrama son exactamente las 24 claves foráneas de `information_schema`, que también aparecen declaradas en los modelos y en las migraciones.

| Elemento del modelo | Archivo de código / migración | ¿Coincide? | Observación |
|---|---|---|---|
| `users` | `models/user.py`; `633f6e19d008`, `925c4947c7a3`, `6236c5b88ac2` | Sí | `email` único con comparación binaria (`utf8mb4_0900_bin`, migración `6236c5b88ac2`); `active_administrator_marker` (columna generada) impide un segundo administrador activo. |
| `audit_logs` | `models/audit_log.py`; `633f6e19d008` | Sí | `actor_user_id` → `users` (admite nulos). `entity_type`/`entity_id` es una referencia polimórfica **sin clave foránea**, por diseño. |
| `sessions` | `models/session.py`; `5cde3a1e780c` | Sí | `user_id` → `users`; `token_hash` único. |
| `login_throttles` | `models/login_throttle.py`; `5cde3a1e780c` | Sí | **Sin claves foráneas.** La clave primaria es el hash del identificador (correo normalizado), así que también cuenta intentos de correos inexistentes. |
| `blocks` | `models/structure.py`; `522bf629af33` | Sí | Número único entre los bloques activos (`active_number_marker`); borrado lógico. |
| `towers` | `models/structure.py`; `522bf629af33` | Sí | `block_id` → `blocks`. |
| `units` | `models/structure.py`; `522bf629af33` | Sí | `tower_id` → `towers`. Solo guarda el número: el identificador `B1-TO1-APTO-101` se deriva y no se almacena. |
| `resident_invitations` | `models/invitation.py`; `4cfaeb7a0be8` | Sí | `user_id` y `invited_by_user_id` → `users`; `unit_id` → `units`; una invitación pendiente por unidad y por usuario (columnas generadas). |
| `resident_units` | `models/invitation.py`; `4cfaeb7a0be8` | Sí | `invitation_id` → `resident_invitations` (único: relación 1 a 0..1); una residencia vigente por unidad y por usuario. |
| `password_reset_tokens` | `models/password_reset.py`; `b1b38cfb7fe2` | Sí | `user_id` → `users`; un enlace pendiente por usuario. |
| `charges` | `models/finance.py`; `6afae8f829c4`, `f3c8fc28babe` | Sí | `unit_id` → `units`; `created_by_user_id` → `users`. Clave foránea **compuesta** sobre sí misma (`unit_id`, `source_charge_id`, `late_fee_source_link`) → (`unit_id`, `id`, `can_originate_late_fee`): una mora apunta a una obligación de origen del mismo apartamento. El diagrama la muestra como `source_charge_id`. |
| `payments` | `models/finance.py`; `6afae8f829c4`, `4385fd452231`, `f697625db563` | Sí | `resident_id` y `registered_by_user_id` → `users`; `unit_id` → `units`. `transaction_reference` y `notes` vienen de `4385fd452231`; el origen `DEMO_GATEWAY`, de `f697625db563`. |
| `payment_allocations` | `models/finance.py`; `6afae8f829c4` | Sí | `payment_id` → `payments`; `charge_id` → `charges`; único (`payment_id`, `charge_id`). |
| `feedback` | `models/feedback.py`; `00d8bbdd687b`, `3e565db40412` | Sí | `resident_id` → `users`. `category` y `urgency` vienen de `3e565db40412` y admiten nulos para el feedback anterior a HU-009. |
| `demo_payment_transactions` | `models/finance.py`; `82e81b7ab23d`, `41602195f69f` | Sí | `resident_id` → `users`; `charge_id` → `charges`; `payment_id` → `payments` (único y con nulos: 0..1). `test_instrument` viene de `41602195f69f`. |
| `notifications` | `models/notification.py`; `d33ea6140186` | Sí | `resident_id` → `users`; `event_key` único. `entity_type`/`entity_id` es polimórfica, **sin clave foránea**. |

## Diferencias encontradas

1. **No hay configuración de producción en el repositorio.** El proxy `/api/v1` solo existe en el servidor de desarrollo de Vite, y no hay archivos de proxy inverso, contenedores ni servidor SMTP de producción.
2. **La versión de MySQL difiere:** el servidor local es 8.0.46 y la decidida es 8.4 LTS. La desviación ya está documentada.
3. **Columnas generadas fuera del diagrama.** Hay 11 columnas generadas (`*_marker`, `can_originate_late_fee`, `late_fee_source_link`) que implementan reglas de unicidad parcial y la clave compuesta de la mora. Se listan en el comentario final de `modelo-datos.mmd`.
4. **Referencias polimórficas.** `audit_logs` y `notifications` señalan su entidad con `entity_type` y `entity_id`, sin clave foránea; no aparecen como relaciones en el diagrama.
5. **Correo sin TLS.** La configuración SMTP solo tiene host, puerto, remitente y timeout; es adecuada para Mailpit, pero no define TLS ni autenticación.
