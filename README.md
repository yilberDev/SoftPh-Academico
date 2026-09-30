# SoftPh-Académico

Prototipo académico de gestión administrativa de propiedad horizontal para la Urbanización San Joaquín (Pereira).

| Capa | Tecnología |
|---|---|
| Frontend | Astro 7 (SSR, `@astrojs/node`) + Tailwind CSS 4 (`@tailwindcss/vite`) + TypeScript |
| Backend | FastAPI (Python 3.13) + SQLAlchemy 2 async + `asyncmy` |
| Migraciones | Alembic (única fuente de verdad del esquema) |
| Base de datos | MySQL 8.4 LTS |
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
- MySQL 8.4 LTS en local
- Mailpit (opcional por ahora; se usará con los flujos de correo)

Si el sistema no trae Python 3.13, se puede obtener solo el intérprete con [uv](https://docs.astral.sh/uv/) (`uv python install 3.13`) y seguir usando `venv` + `pip`.

## Instalación

Desde la raíz del repositorio:

```bash
# Frontend
cd frontend
npm install
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

`.env` nunca se versiona.

## Configuración de MySQL

Crea las bases de desarrollo y de pruebas y el usuario de la aplicación a partir de los valores del `.env`. El script no borra nada ni crea tablas:

```bash
sudo bash database/scripts/create-local-databases.sh
```

Después aplica las migraciones (hoy no hay ninguna; el esquema se crea con Alembic a medida que se implementan las HU):

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

- Obligatorios: nombre completo, correo electrónico y contraseña (12 a 128 caracteres). Opcionales: número de documento y teléfono.
- Si ya existe un administrador activo, el comando rechaza la operación.
- Códigos de salida: `0` creado, `1` datos inválidos o conflicto, `2` sin terminal interactiva o sin conexión a la base de datos.

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

Abre `http://localhost:4321`. La página inicial muestra el estado de la API y de la base de datos. Las peticiones del navegador a `/api/v1/*` se reenvían a FastAPI a través del mismo origen.

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

### Frontend

```bash
cd frontend
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
