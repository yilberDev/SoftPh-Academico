# Guía de ejecución de SoftPh

Instalación, configuración, ejecución y pruebas en un equipo de desarrollo. Todos los comandos se ejecutan desde la raíz del repositorio salvo que se indique otra carpeta.

> **Verificación (2026-11-03).** Esta guía se siguió de principio a fin sobre un clon limpio del commit `f543c74`, en Linux Mint 22.3. La sección [9](#9-resultados-de-la-verificación) resume qué se ejecutó y con qué resultado. El único paso no ejecutado es la creación de las bases (paso 4), porque requiere `sudo`.

## 1. Requisitos

| Software | Versión exigida | Versión verificada | Notas |
|---|---|---|---|
| Git | — | — | Para clonar el repositorio |
| Node.js y npm | Node.js 24 LTS | Node.js 24.20.0, npm 11.19.0 | Frontend Astro 7 y Playwright |
| Python | 3.13 | 3.13.15 | Si el sistema no lo trae: `uv python install 3.13` (solo el intérprete) |
| MySQL | 8.4 LTS | 8.0.46 | Funciona con 8.0.46; la versión decidida en el proyecto es 8.4 LTS |
| Mailpit | — | 1.31.0 | Servidor SMTP de pruebas: captura los correos sin entregarlos |
| Chromium para Playwright | El de Playwright 1.63.0 | chromium-1243 | Solo para las pruebas E2E |

## 2. Obtener el código e instalar las dependencias

```bash
git clone <URL del repositorio> SoftPh-Academico
cd SoftPh-Academico

# Frontend
cd frontend
npm install
npx playwright install chromium     # navegador de las pruebas E2E (una sola vez)
cd ..

# Backend (usa el intérprete de Python 3.13)
python3.13 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt
backend/.venv/bin/pip check          # «No broken requirements found.»
```

`requirements.txt` tiene las dependencias de ejecución (FastAPI 0.142.2, uvicorn 0.54.0, SQLAlchemy 2.1.1, asyncmy 0.2.15, Alembic 1.20.0, argon2-cffi 25.1.0, reportlab 5.0.1, entre otras) y `requirements-dev.txt` añade las de pruebas (pytest 9.1.1).

## 3. Variables de entorno

Hay **un único `.env` en la raíz**, compartido por backend y frontend. Nunca se versiona.

```bash
cp .env.example .env
```

Edita `.env` y cambia al menos `DB_PASSWORD`. Para la demostración, pon `DEMO_PAYMENTS_ENABLED=true`.

| Variable | Valor de `.env.example` | Uso |
|---|---|---|
| `APP_ENV` | `development` | `development`, `test` o `production` |
| `DEMO_PAYMENTS_ENABLED` | `false` | Activa el pago de demostración (HU-010). Prohibido con `APP_ENV=production` |
| `API_INTERNAL_URL` | `http://127.0.0.1:8000` | URL de FastAPI para el SSR de Astro y destino del proxy `/api/v1` |
| `APP_ORIGIN` | `http://localhost:4321` | Origen público único. Navega exactamente con este host: `127.0.0.1:4321` es otro origen y la API rechazaría los cambios |
| `SESSION_COOKIE_SECURE` | `false` | Solo admitido en desarrollo y pruebas con origen local |
| `SESSION_ABSOLUTE_TTL_MINUTES`, `SESSION_IDLE_TTL_MINUTES` | `480`, `30` | Duración de la sesión |
| `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_FAILURE_WINDOW_MINUTES`, `LOGIN_LOCKOUT_MINUTES` | `5`, `15`, `15` | Bloqueo tras intentos fallidos |
| `PASSWORD_HASH_MAX_CONCURRENCY` | `2` | Operaciones Argon2 simultáneas (1–4) |
| `SMTP_HOST`, `SMTP_PORT` | `127.0.0.1`, `1025` | Servidor SMTP (Mailpit) |
| `SMTP_FROM`, `SMTP_TIMEOUT_SECONDS` | `SoftPh-Académico <no-reply@softph.local>`, `10` | Remitente y tiempo límite |
| `DB_HOST`, `DB_PORT` | `127.0.0.1`, `3306` | Servidor MySQL |
| `DB_NAME` | `softph_academico` | Base de desarrollo |
| `DB_USER`, `DB_PASSWORD` | `softph_academico`, `change-me` | Usuario de la aplicación: **cambia la contraseña** |
| `TEST_DB_NAME` | `softph_academico_test` | Base de pruebas: debe terminar en `_test` y ser distinta de `DB_NAME` |

Las variables de entorno del sistema tienen prioridad sobre el `.env`. Por eso se puede usar otra base sin editar el archivo, por ejemplo `DB_NAME=softph_academico_demo .venv/bin/alembic upgrade head` (lo usa el [guion de demostración](demo.md)).

## 4. Crear las bases de datos

Con MySQL en marcha y el `.env` ya configurado:

```bash
sudo bash database/scripts/create-local-databases.sh
```

El script lee `DB_NAME`, `TEST_DB_NAME`, `DB_USER` y `DB_PASSWORD` del `.env`. Crea las dos bases (`utf8mb4`, `utf8mb4_0900_ai_ci`) y el usuario en `localhost` y `127.0.0.1`, con todos los privilegios solo sobre esas dos bases. No borra nada ni crea tablas.

> **No ejecutado en la verificación** (requiere `sudo`). En el equipo de verificación las dos bases ya existían y los permisos del usuario coincidían con los que concede el script (`SHOW GRANTS`: todos los privilegios sobre `softph_academico` y `softph_academico_test`, nada más).

## 5. Aplicar y verificar las migraciones

El esquema lo crea solo Alembic (16 revisiones; head `d33ea6140186`):

```bash
cd backend
.venv/bin/alembic upgrade head     # crea o actualiza las tablas de DB_NAME
.venv/bin/alembic current          # debe mostrar: d33ea6140186 (head)
.venv/bin/alembic heads            # una sola head: d33ea6140186 (head)
.venv/bin/alembic check            # «No new upgrade operations detected.»
```

`alembic check` compara los modelos de `backend/app/models` con la base: si responde otra cosa, el esquema y el código no coinciden.

La base de pruebas no necesita este paso: pytest y Playwright le aplican `alembic upgrade head` antes de usarla.

## 6. Crear el administrador

No hay registro público de administradores. El primero se crea en el servidor con un comando interactivo:

```bash
cd backend
.venv/bin/python -m app.cli create-admin
```

Pide nombre completo, correo, documento (opcional), teléfono (opcional), contraseña (12 a 128 caracteres) y su confirmación; la contraseña no se muestra.

| Situación | Mensaje | Código de salida |
|---|---|---|
| Creado | «Administrador creado correctamente: … (id N).» | 0 |
| Ya existe un administrador activo | «Ya existe un administrador activo. Este comando solo permite crear el primer administrador.» | 1 |
| Datos inválidos | «No se creó el administrador. Revisa los siguientes datos: …» | 1 |
| Sin terminal (por ejemplo, con la entrada redirigida) | «Este comando requiere una terminal interactiva.» | 2 |
| MySQL no disponible | «No fue posible conectarse a la base de datos…» | 2 |

## 7. Levantar Mailpit, el backend y el frontend

### Mailpit

Si no está instalado, sigue las instrucciones de instalación de <https://mailpit.axllent.org/>. Se ejecuta así:

```bash
mailpit --smtp 127.0.0.1:1025 --listen 127.0.0.1:8025
```

En el equipo de verificación funciona como servicio de systemd (`mailpit.service`) con esos mismos parámetros, más `--database /var/lib/mailpit/mailpit.db` para conservar los correos. Comprobación:

```bash
curl -s http://127.0.0.1:8025/api/v1/info      # responde con "Version"
```

La interfaz está en `http://localhost:8025`.

### Backend (FastAPI + uvicorn)

```bash
cd backend
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Comprobación: `curl http://127.0.0.1:8000/api/v1/health` → `{"status":"ok","database":"ok"}`. La documentación interactiva está en `http://127.0.0.1:8000/api/v1/docs` (fuera de producción).

### Frontend (Astro)

Con el backend en marcha, en otra terminal:

```bash
cd frontend
npm run dev
```

Con Astro 7.3.5, `npm run dev` **deja el servidor en segundo plano** y devuelve la terminal («Dev server running at http://localhost:4321 (pid …)»). Para administrarlo, desde `frontend/`:

```bash
npx astro dev status
npx astro dev logs
npx astro dev stop
```

Abre `http://localhost:4321`. La raíz es la página pública de SoftPh; el acceso está en `http://localhost:4321/login`. Sin sesión, `/admin` redirige al login.

## 8. Ejecutar las pruebas

Las pruebas de integración y las E2E usan la base `softph_academico_test` y la vacían. **Nunca ejecutes pytest y Playwright al mismo tiempo.**

### Backend

```bash
cd backend
.venv/bin/pytest tests/unit          # sin base de datos
.venv/bin/pytest tests/integration   # requiere MySQL y la base *_test
.venv/bin/pytest                     # todas
```

`tests/integration/test_mailpit.py` envía y borra un correo de prueba en Mailpit; se omite si Mailpit no está en marcha.

### Frontend

```bash
cd frontend
npm test                             # lógica del frontend (node --test)
npx tsc --noEmit -p tsconfig.json    # tipos
npm run check                        # astro check
npm run build                        # compilación SSR
```

### E2E (Playwright)

```bash
cd frontend
npm run test:e2e                                    # todas
npm run test:e2e -- e2e/manual-payment-journey.spec.ts   # un archivo
```

Playwright levanta su propia pila y no reutiliza la de desarrollo: la API en `127.0.0.1:8100` sobre `softph_academico_test` (`backend/tests/e2e/serve.py`) y Astro en `localhost:4400`, ambos con el pago de demostración activado. Los puertos 8100 y 4400 deben estar libres. Cada caso que usa los datos de prueba los recarga desde `backend/tests/e2e/seed.py` (fixture `seed` de `frontend/e2e/support/fixtures.ts`), y al terminar la ejecución `global-teardown.ts` deja la base vacía.

- Mailpit debe estar en marcha: `manual-payment-journey.spec.ts` falla sin él. `invitation-activation.spec.ts` y `password-reset.spec.ts` se omiten (y lo indican).
- Los dos casos de capturas (`admin-dashboard.spec.ts` y `resident-dashboard.spec.ts`) solo corren con la variable `SHOTS_DIR`; sin ella se omiten.
- El informe HTML queda en `frontend/playwright-report/` (ignorado por Git).

## 9. Resultados de la verificación

Ejecutado el 2026-11-03 sobre un `git clone` limpio del commit `f543c74`, en Linux Mint 22.3, con MySQL 8.0.46 y Mailpit 1.31.0. Las suites corrieron en secuencia, nunca dos a la vez.

| Paso | Comando | Resultado |
|---|---|---|
| 2 | `npm install` | Correcto; `package-lock.json` sin cambios |
| 2 | `python3.13 -m venv` + `pip install -r requirements-dev.txt` + `pip check` | Correcto; «No broken requirements found.» |
| 2 | `npx playwright install chromium` | No ejecutado: el navegador ya estaba en la caché del equipo |
| 3 | `.env` | Se usó el `.env` existente del equipo (no se muestra: contiene la contraseña) |
| 4 | `create-local-databases.sh` | **No ejecutado** (requiere `sudo`); las bases y los permisos ya existían |
| 5 | `alembic current` / `upgrade head` / `heads` / `check` | `d33ea6140186 (head)`; `upgrade head` sin cambios (ya estaba en la head); una sola head; «No new upgrade operations detected.» |
| 6 | `create-admin` sin terminal | «Este comando requiere una terminal interactiva.», salida 2 |
| 6 | `create-admin` en la base de desarrollo | «Ya existe un administrador activo…», salida 1 (no pide datos) |
| 6 | `create-admin` en una base vacía (`DB_NAME=softph_academico_test`) | «Administrador creado correctamente…», salida 0 |
| 7 | `uvicorn` en 8000 | `/api/v1/health` → `{"status":"ok","database":"ok"}` |
| 7 | `npm run dev` en 4321 | Servidor en segundo plano; `/login` → 200; `/admin` sin sesión → 302 a `/login`; `npx astro dev stop` lo detiene |
| 7 | Mailpit | `/api/v1/info` responde (v1.31.0) |
| 8 | `.venv/bin/pytest` | **2648 aprobadas**, 0 fallidas, 0 omitidas (3 min 52 s); incluye `test_mailpit.py` |
| 8 | `npm test` | **503 aprobadas**, 0 fallidas |
| 8 | `npx tsc --noEmit -p tsconfig.json` | Sin errores |
| 8 | `npm run check` | 172 archivos: 0 errores, 0 advertencias, 0 sugerencias |
| 8 | `npm run build` | Completado |
| 8 | `npm run test:e2e` | **135 casos: 133 aprobados, 2 omitidos, 0 fallidos** (6,3 min). Los omitidos son las capturas de `admin-dashboard.spec.ts:139` y `resident-dashboard.spec.ts:127` (sin `SHOTS_DIR`). Los casos con Mailpit se ejecutaron |

Al terminar, la base `softph_academico_test` quedó vacía y el clon sin cambios versionados.

## 10. Diferencias con el README del repositorio

La verificación del 2026-11-03 encontró cinco puntos en los que el `README.md` no coincidía con la ejecución real. Cuatro se corrigieron en el propio README (commit `docs: update traceability…`, punto 4 de esta revisión):

1. `npm run dev` deja el servidor en segundo plano y se detiene con `npx astro dev stop`: ahora lo indica.
2. La página inicial es la presentación pública, no la página de estado de la API: ahora lo indica.
3. La versión de MySQL: ahora indica que la objetivo es 8.4 LTS y que el proyecto se verificó con 8.0.46.
4. La instalación incluye `npx playwright install chromium`.

Sigue vigente una precisión que el README solo da de forma indirecta (en los códigos de salida): **`create-admin` exige una terminal interactiva** y sale con código 2 si la entrada no es una terminal (sección 6).
