# Gestión de configuración del repositorio

Datos tomados de Git y del propio repositorio el 2026-10-27.

## Versión final

| Dato | Valor |
|---|---|
| Rama | `main` (única), igual a `origin/main` |
| Repositorio remoto | `https://github.com/yilberDev/SoftPh-Academico.git` (privado) |
| HEAD | `f543c741fcc23e4a4154ce17e3ac8fe761480c7c` (`f543c74`, 2026-10-26, «test(e2e): cover manual and full demo payments end to end») |
| Commits | 57, del 2026-09-30 al 2026-10-26, todos del autor `yilberDev` |
| Etiquetas (tags) | Ninguna; no hay versiones etiquetadas |
| Archivos versionados | 493 |

## Estructura principal

| Carpeta | Contenido |
|---|---|
| `backend/app/` | API FastAPI: `api/` (rutas y dependencias), `core/` (configuración, sesiones, CSRF, contraseñas), `db/`, `models/`, `schemas/`, `services/`, `cli.py` |
| `backend/alembic/` | Migraciones del esquema (`env.py` y `versions/`) |
| `backend/tests/` | `unit/`, `integration/` (sobre `softph_academico_test`) y `e2e/` (semilla y servidor de la pila Playwright) |
| `frontend/src/` | Astro: `pages/`, `components/`, `layouts/`, `lib/`, `middleware.ts`, `styles/` |
| `frontend/tests/` | Pruebas unitarias del frontend (`node --test`) |
| `frontend/e2e/` | Pruebas E2E con Playwright y su soporte (`support/`) |
| `database/` | `README.md` y `scripts/create-local-databases.sh` |
| `docs/` | Historias de usuario, requisitos funcionales, casos de uso, matriz de trazabilidad, extensiones de diseño, diagramas y capturas |
| `.claude/skills/` | Guías de desarrollo versionadas (7 archivos) |

## Convención de mensajes de commit

Todos los commits siguen `tipo(ámbito): descripción`, con ámbito opcional. Ningún commit queda fuera del formato.

| Tipo | Commits |
|---|---|
| `feat` | 39 |
| `test` | 8 |
| `docs` | 6 |
| `fix` | 3 |
| `chore` | 1 |

Ámbitos más usados: `payments` (9), `finance` (9), `feedback` (8), `e2e` (6), `auth` (4). Cinco commits llevan la línea `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`: `a126838`, `9f7d5e4`, `8d4845a`, `6d3b3ad` y `f543c74`.

## Versionado del esquema con Alembic

- 16 revisiones en una cadena lineal, de `633f6e19d008` (HU-001) a `d33ea6140186` (HU-017, *head*), según `alembic history`.
- Plantilla de nombres (`backend/alembic.ini`): `AAAA_MM_DD_HHMM-<revisión>_<descripción>.py`. La descripción indica la HU (por ejemplo `hu004_charges_payments_allocations`) o `ext_obligations` para la extensión sin HU.
- `env.py` usa `Base.metadata` de los modelos y un motor asíncrono; la URL de la base viene de la configuración, o la indican las pruebas.
- La base de pruebas `softph_academico_test` está en `d33ea6140186`, y `alembic check` no detecta diferencias entre los modelos y el esquema (comprobado el 2026-10-27). Según la [matriz de trazabilidad](../matriz-trazabilidad.md), la base de desarrollo también está en esa revisión; el esquema de producción no está verificado.

## Archivos excluidos (`.gitignore`)

- Entorno: `.env` y `.env.*`, salvo `.env.example`, que sí se versiona.
- Node y Astro: `node_modules/`, `dist/`, `.astro/`, `npm-debug.log*`.
- Evidencia de Playwright: `test-results/`, `playwright-report/`.
- Python: `.venv/`, `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.coverage`, `htmlcov/`.
- Editores y sistema: `.idea/`, `.DS_Store`.
- Configuración local de Claude Code: `.claude/settings.local.json`.

Observaciones:

- `.vscode/` en la raíz y `database/exploration.sql` no están versionados, pero tampoco están ignorados: aparecen como archivos sin seguimiento.
- `frontend/.vscode/` sí se versiona (`extensions.json` y `launch.json`).
- `docs/diagramas/` y `docs/capturas/` son nuevos y todavía no tienen commit.
