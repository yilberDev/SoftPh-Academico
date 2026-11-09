# Guion de verificación de la arquitectura

Este guion muestra, para cada componente y cada comunicación de la Figura 9 del informe, el archivo y las líneas del código que lo demuestran, junto con el comando para enseñarlo en vivo. La Figura 9 es el diagrama de componentes resumido (`arquitectura/componentes-resumen.puml`).

- **Versión:** líneas comprobadas el 2026-11-09 sobre la rama `main`. Su código de aplicación (`backend/`, `frontend/`, `database/`) es idéntico al de la etiqueta `v1.1-academico`: `git diff v1.1-academico main -- backend frontend database` no muestra diferencias.
- **Dónde ejecutar:** todos los comandos se ejecutan desde la raíz del repositorio.
- **Comandos:** `grep -n` muestra el número de línea y `sed -n 'A,Bp' archivo` muestra el fragmento citado.

```bash
cd SoftPh-Academico
git checkout v1.1-academico        # opcional: la misma versión del informe
```

## 1. Componentes

| Componente (Figura 9) | Capa | Archivo | Líneas | Qué se ve |
|---|---|---|---|---|
| Navegador web | — | (cliente externo) | — | Recibe la cookie `softph_session` que fija la API (comunicación 1) |
| Control de acceso | Presentación | `frontend/src/middleware.ts` | 11–36 | `onRequest`: decide por rol antes de entregar cada página |
| Control de acceso (reglas) | Presentación | `frontend/src/lib/auth/roles.ts` | 22–25 | `/admin` solo ADMINISTRADOR; `/resident` solo RESIDENTE |
| Páginas SSR | Presentación | `frontend/src/pages/admin/payments/index.astro` | 1–19 | Página Astro renderizada en el servidor (ejemplo: pagos) |
| Lógica de interfaz | Presentación | `frontend/src/lib/payments/payment-form.ts` | (módulo) | Constantes y validaciones del formulario de pago, sin acceso a la red |
| Cliente de la API (navegador) | Presentación | `frontend/src/lib/api/client.ts` | 14–25 | `apiFetch`: `fetch` a `/api/v1` con `X-CSRF-Token` |
| Cliente de la API (SSR) | Presentación | `frontend/src/lib/api/server.ts` | 8–19 | `serverApiGet`: solo `GET`, a `API_INTERNAL_URL`, reenviando la cookie |
| Routers FastAPI | API | `backend/app/api/v1/router.py` | 22–49 | `include_router` de cada módulo |
| Routers FastAPI (montaje) | API | `backend/app/main.py` | 15, 67, 86 | `API_PREFIX = "/api/v1"`; `create_app` monta `api_router` |
| Esquemas Pydantic | API | `backend/app/schemas/payment.py` | 149–216 | `PaymentCreate`: `extra="forbid"` y validadores de cada campo |
| Servicios | Negocio | `backend/app/services/payments.py` | 1–24, 223–353 | Transacción del pago manual (descrita en el encabezado del módulo) |
| Recibos PDF | Negocio | `backend/app/services/receipts.py` | 22–26, 85–90 | Importa reportlab; `build_receipt_pdf` dibuja el recibo |
| Correo | Negocio | `backend/app/services/mail.py` | 38–61 | `SmtpMailSender` con `smtplib` |
| Modelos SQLAlchemy | Persistencia | `backend/app/models/finance.py` | 137–153 | Clase `Payment`, tabla `payments` |
| Modelos (base y sesión) | Persistencia | `backend/app/db/base.py` (14); `backend/app/db/session.py` (10–27) | — | `DeclarativeBase`; motor asíncrono y `get_db` |
| Alembic | Persistencia | `backend/alembic/env.py` | 18, 21–24, 47 | `target_metadata = Base.metadata`; URL de la configuración; motor asíncrono |
| MySQL 8 | Persistencia | `backend/app/core/config.py` | 82–90 | `database_url`: `mysql+asyncmy`, `utf8mb4` |
| Autorización | Transversal | `backend/app/api/deps.py` | 28–70 | `verify_origin`, `get_current_auth` (401), `require_csrf`, `require_roles` (403) |
| Núcleo | Transversal | `backend/app/core/` (`sessions.py`, `csrf.py`, `passwords.py`, `money.py`, `clock.py`, `exceptions.py`, `config.py`) | ver comunicaciones 9 y 12 | Utilidades que usan las demás capas |
| Servidor SMTP (Mailpit) | Externo | `.env.example` (`SMTP_HOST`, `SMTP_PORT`) | — | `127.0.0.1:1025` en desarrollo y pruebas |

```bash
ls frontend/src/pages frontend/src/lib frontend/src/lib/api
ls backend/app/api/v1 backend/app/schemas backend/app/services backend/app/models backend/app/core
ls backend/alembic/versions | wc -l          # 16 migraciones
```

## 2. Comunicaciones

Numeradas en el orden en que se leen en la Figura 9, de arriba abajo.

### 1. Navegador → Control de acceso: HTTP :4321 con la cookie `softph_session`

| Qué | Archivo | Líneas |
|---|---|---|
| La API fija la cookie al iniciar sesión | `backend/app/api/v1/auth.py` | 27–33 |
| Nombre y atributos de la cookie: `HttpOnly`, `SameSite=Lax` | `backend/app/core/sessions.py` | 15, 55–63 |
| El middleware consulta la sesión con la cookie de cada petición | `frontend/src/middleware.ts` | 11–12 |

```bash
grep -n "set_cookie" backend/app/api/v1/auth.py
sed -n '55,63p' backend/app/core/sessions.py
sed -n '11,12p' frontend/src/middleware.ts
```

### 2. Control de acceso → Páginas SSR: acceso por rol

| Qué | Archivo | Líneas |
|---|---|---|
| Rutas protegidas y rol de cada una | `frontend/src/lib/auth/roles.ts` | 22–25, 31 |
| Decisión: público, redirección al login, 403 o 503 | `frontend/src/lib/auth/page-access.ts` | 35–63 |
| Aplicación de la decisión: `rewrite('/forbidden')` (403) o `next()` | `frontend/src/middleware.ts` | 14–35 |

```bash
sed -n '22,25p' frontend/src/lib/auth/roles.ts
sed -n '35,63p' frontend/src/lib/auth/page-access.ts
```

Este control solo mejora la navegación. La API vuelve a autorizar cada petición (comunicación 8).

### 3. Páginas SSR → Lógica de interfaz: import TypeScript

| Qué | Archivo | Líneas |
|---|---|---|
| La página de pagos importa constantes y validaciones del formulario | `frontend/src/pages/admin/payments/index.astro` | 5–15 |

```bash
grep -n "lib/payments" frontend/src/pages/admin/payments/index.astro
```

### 4. Páginas SSR → Cliente de la API: import TypeScript

| Qué | Archivo | Líneas |
|---|---|---|
| Script de la página: importa `registerPayment` (cliente del navegador) | `frontend/src/pages/admin/payments/index.astro` | 276, 465 |
| Página SSR del feedback: importa `loadFeedback` (cliente SSR) | `frontend/src/pages/admin/feedback/[id].astro` | 5, 15 |

```bash
grep -n "lib/api" frontend/src/pages/admin/payments/index.astro "frontend/src/pages/admin/feedback/[id].astro"
```

### 5. Cliente de la API → Routers FastAPI: HTTP/JSON `/api/v1` con la cookie y `X-CSRF-Token`

**Desde el navegador** (escrituras):

| Qué | Archivo | Líneas |
|---|---|---|
| Token CSRF entregado por la sesión y puesto en la página | `frontend/src/pages/admin/payments/index.astro` | 19, 42, 305 |
| `registerPayment`: `POST /admin/payments` con el token | `frontend/src/lib/api/payments.ts` | 59–60 |
| `apiFetch`: cabecera `X-CSRF-Token` y `fetch('/api/v1…', credentials: 'same-origin')` | `frontend/src/lib/api/client.ts` | 3, 20–23 |
| Proxy de desarrollo: `/api/v1` del mismo origen hacia FastAPI | `frontend/astro.config.mjs` | 44–48 |

**Desde el servidor Astro** (SSR, solo lectura):

| Qué | Archivo | Líneas |
|---|---|---|
| `serverApiGet`: reenvía la cookie, `GET` a `API_INTERNAL_URL` | `frontend/src/lib/api/server.ts` | 8–19 |
| Uso: el middleware pide `GET /auth/session` | `frontend/src/lib/api/session.ts` | 9–10 |
| Uso: detalle de un feedback | `frontend/src/lib/api/feedback-server.ts` | 5–6 |

```bash
sed -n '14,25p' frontend/src/lib/api/client.ts
sed -n '8,19p'  frontend/src/lib/api/server.ts
sed -n '44,48p' frontend/astro.config.mjs
grep -rn "serverApiGet\|apiFetch" frontend/src/lib/api | wc -l    # todas las llamadas a la API
```

### 6. Routers → Esquemas: validación Pydantic

| Qué | Archivo | Líneas |
|---|---|---|
| El endpoint recibe `data: PaymentCreate` | `backend/app/api/v1/payments.py` | 16, 32–34 |
| `PaymentCreate`: campos extra prohibidos y un validador por campo | `backend/app/schemas/payment.py` | 149–216 |

```bash
sed -n '32,44p' backend/app/api/v1/payments.py
grep -n "class PaymentCreate\|field_validator" backend/app/schemas/payment.py
```

### 7. Routers → Servicios: llamada Python (async)

| Qué | Archivo | Líneas |
|---|---|---|
| El router delega en el servicio, sin reglas de negocio propias | `backend/app/api/v1/payments.py` | 17, 43 |

```bash
grep -n "payment_service" backend/app/api/v1/payments.py
```

### 8. Routers → Autorización: `Depends()` (sesión, rol y CSRF)

| Qué | Archivo | Líneas |
|---|---|---|
| Origen verificado en todo el router | `backend/app/api/v1/payments.py` | 22 |
| Solo ADMINISTRADOR y con token CSRF | `backend/app/api/v1/payments.py` | 25–26, 37–38 |
| `verify_origin`: Origin/Referer en POST, PUT, PATCH y DELETE | `backend/app/api/deps.py` | 20, 28–33 |
| `get_current_auth`: sin sesión, 401 | `backend/app/api/deps.py` | 36–43 |
| `require_csrf`: origen y token CSRF | `backend/app/api/deps.py` | 46–58 |
| `require_roles`: rol no permitido, 403 | `backend/app/api/deps.py` | 61–70 |

```bash
sed -n '20,70p' backend/app/api/deps.py
grep -n "require_roles\|require_csrf\|verify_origin" backend/app/api/v1/payments.py
grep -rln "Depends(require_roles" backend/app/api/v1     # 12 routers protegidos por rol
```

### 9. Autorización → Núcleo: sesión y CSRF

| Qué | Archivo | Líneas |
|---|---|---|
| Imports de `core.csrf` y `core.sessions` | `backend/app/api/deps.py` | 10, 12 |
| Sesión buscada por el SHA-256 del token, nunca por el token en claro | `backend/app/core/sessions.py` (25–28); `backend/app/services/sessions.py` (41–55) | — |
| Token CSRF = HMAC-SHA256 de la sesión, comparado en tiempo constante | `backend/app/core/sessions.py` | 45–52 |
| Origen permitido: solo `APP_ORIGIN` | `backend/app/core/csrf.py` | 11–21 |

```bash
sed -n '45,52p' backend/app/core/sessions.py
sed -n '11,21p' backend/app/core/csrf.py
```

### 10. Servicios → Recibos PDF: llamada Python

| Qué | Archivo | Líneas |
|---|---|---|
| Tras confirmar el pago se genera el PDF, fuera de la transacción | `backend/app/services/payments.py` | 81, 401 |
| `build_receipt_pdf` (reportlab, tamaño carta) | `backend/app/services/receipts.py` | 85–90 |

### 11. Servicios → Correo: llamada Python

| Qué | Archivo | Líneas |
|---|---|---|
| Envío del recibo; un fallo de PDF o SMTP nunca deshace el pago | `backend/app/services/payments.py` | 401–405 |

```bash
sed -n '391,405p' backend/app/services/payments.py
```

### 12. Servicios → Núcleo: llamada Python

| Qué | Archivo | Líneas |
|---|---|---|
| Hora UTC, dinero, identificador del apartamento y errores | `backend/app/services/payments.py` | 37–40 |
| Contraseñas con Argon2id | `backend/app/services/auth.py` | 18 |

```bash
grep -n "^from app.core" backend/app/services/payments.py backend/app/services/auth.py
```

### 13. Servicios → Modelos: AsyncSession (ORM)

| Qué | Archivo | Líneas |
|---|---|---|
| Sesión de base de datos inyectada en cada petición | `backend/app/api/deps.py` (13, 18); `backend/app/db/session.py` (25–27) | — |
| Bloqueo del apartamento y sus padres | `backend/app/services/payments.py` | 231 |
| Lectura de las obligaciones pendientes | `backend/app/services/payments.py` | 183–200, 252 |
| Inserción del pago y de sus aplicaciones | `backend/app/services/payments.py` | 256–277 |
| Confirmación de la transacción | `backend/app/services/payments.py` | 353 |

**Regla de negocio: aplicación del pago a las obligaciones.** Las obligaciones se cubren de la más antigua a la más nueva y el sobrante queda como saldo a favor. Los saldos nunca se guardan: se derivan de cargos menos aplicaciones.

| Qué | Archivo | Líneas |
|---|---|---|
| Orden: fecha de vencimiento, fecha de creación, id | `backend/app/services/allocation.py` | 54–55 |
| `allocate_fifo`: reparte el monto y devuelve el saldo a favor | `backend/app/services/allocation.py` | 58–72 |
| Uso en el registro del pago | `backend/app/services/payments.py` | 252–253 |
| Comprobación previa al COMMIT: nunca más aplicado que el pago o la obligación | `backend/app/services/payments.py` | 203–220, 278 |

```bash
sed -n '54,72p'   backend/app/services/allocation.py
sed -n '250,278p' backend/app/services/payments.py
```

### 14. Modelos → MySQL: TCP :3306, `mysql+asyncmy`

| Qué | Archivo | Líneas |
|---|---|---|
| URL `mysql+asyncmy` con `utf8mb4` | `backend/app/core/config.py` | 82–90 |
| Motor asíncrono con sesión en UTC; `get_db` | `backend/app/db/session.py` | 10–27 |
| Mapeo de la tabla `payments` | `backend/app/models/finance.py` | 137, 153 |

```bash
sed -n '82,90p' backend/app/core/config.py
sed -n '10,27p' backend/app/db/session.py
```

### 15. Alembic → MySQL: TCP :3306, DDL

| Qué | Archivo | Líneas |
|---|---|---|
| Metadatos de los modelos y URL de `config.py` | `backend/alembic/env.py` | 18, 21–24 |
| Motor asíncrono de las migraciones | `backend/alembic/env.py` | 47 |

```bash
cd backend && .venv/bin/alembic current && .venv/bin/alembic check && cd ..
# d33ea6140186 (head) · «No new upgrade operations detected.»
```

### 16. Correo → Servidor SMTP: SMTP :1025

| Qué | Archivo | Líneas |
|---|---|---|
| `smtplib.SMTP(host, port, timeout)` y `send_message` | `backend/app/services/mail.py` | 60–61 |
| Host y puerto desde la configuración | `backend/app/services/mail.py` | 41–45 |

```bash
sed -n '38,61p' backend/app/services/mail.py
grep -n "SMTP_" .env.example
```

## 3. Recorrido completo en vivo: registro de un pago manual

En la Figura 9 se recorre de arriba abajo:

1. **Página** `admin/payments/index.astro`: el formulario llama a `registerPayment(payload, csrfToken)` (línea 465).
2. **Cliente** `lib/api/payments.ts:59-60`: `POST /admin/payments`. Luego `lib/api/client.ts:20-23`: cabecera `X-CSRF-Token` y cookie del mismo origen.
3. **Proxy** `astro.config.mjs:44-48`: `/api/v1` llega a FastAPI.
4. **Router** `api/v1/payments.py:22-43`:
   - antes de ejecutar nada, `verify_origin`, `require_roles(ADMINISTRADOR)` y `require_csrf` (`api/deps.py`);
   - después, `PaymentCreate` valida el cuerpo.
5. **Servicio** `services/payments.py:223-353`:
   - bloquea el apartamento;
   - comprueba la residencia;
   - aplica las obligaciones en orden de vencimiento (`allocation.py:58-72`);
   - inserta y confirma.
6. **Modelos y MySQL** `models/finance.py`, `db/session.py`: tablas `payments` y `payment_allocations`.
7. **Recibo y correo** `services/payments.py:401-402`: `receipts.py` genera el PDF y `mail.py` lo envía a Mailpit (SMTP :1025).

Pruebas que demuestran el recorrido (requieren MySQL con la base `softph_academico_test`; Mailpit solo para la del recibo):

```bash
cd backend
.venv/bin/pytest tests/unit/test_payments.py -k fifo                                  # orden de aplicación
.venv/bin/pytest tests/integration/test_payments_api.py -k "registers_a_payment or fifo_example or receipt_pdf"
.venv/bin/pytest tests/integration/test_access_control.py -k "forbidden_on_every_administrator_route or role_sent_by_the_client"
.venv/bin/pytest tests/integration/test_charges_api.py -k "requires_origin_and_csrf"  # sin origen o token: rechazo
```

| Prueba | Qué demuestra |
|---|---|
| `test_fifo_example_150000`, `test_fifo_orders_by_due_date_then_created_at_then_id` (unitarias) | Orden y reparto de la aplicación de pagos |
| `test_administrator_registers_a_payment`, `test_fifo_example_of_the_design`, `test_receipt_pdf_and_email` (integración) | Registro, aplicación sobre MySQL, recibo PDF y correo |
| `test_resident_is_forbidden_on_every_administrator_route`, `test_role_sent_by_the_client_is_ignored` (integración) | 403 por rol en todas las rutas de administración; el rol que envía el cliente se ignora |
| `test_post_requires_origin_and_csrf` (integración) | Una escritura sin origen o sin token CSRF se rechaza |

## 4. Comprobación de que no hay atajos

```bash
# Las páginas nunca hablan con MySQL: el frontend no tiene cliente de base de datos.
grep -rn "mysql\|asyncmy\|sqlalchemy" frontend/src | wc -l            # 0
# Los routers no escriben SQL: el acceso a datos está en los servicios.
grep -rln "select(\|db.add(" backend/app/api/v1 | wc -l               # 0
# El cliente SSR solo hace GET.
grep -n "method:" frontend/src/lib/api/server.ts                       # method: 'GET'
```
