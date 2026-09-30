---
description: Define the verification workflow for SoftPh-Academico (pytest + httpx on a MySQL test database, astro check and build for the frontend). Use after implementing or modifying functionality to run focused tests, builds, integration checks, and regression checks.
---

# SoftPh-Académico — Testing y verificación

## Herramientas

| Ámbito | Herramienta |
|---|---|
| Backend (Python 3.13) | `pytest` + `httpx` (`AsyncClient` con `ASGITransport` contra la app FastAPI) |
| Pruebas async | plugin `anyio` de pytest (ya incluido con FastAPI/Starlette) |
| Base de datos de pruebas | MySQL 8.4 local, base de datos dedicada |
| Frontend (Node.js 24 LTS) | `astro check` + `npm run build` (build SSR con `@astrojs/node`) |

Agregar herramientas específicas (p. ej., pruebas end-to-end o de componentes) solo cuando una HU lo justifique y con aprobación.

## Base de datos de pruebas

- Base de datos MySQL separada de la de desarrollo, configurada por su propia variable en el `.env` de la raíz. No usar SQLite.
- Su nombre debe terminar en `_test`; la configuración de pruebas se niega a ejecutarse si no es así.
- El esquema se crea aplicando las migraciones de Alembic (`upgrade head`), lo que además valida las migraciones. Nunca con `create_all`.
- Cada prueba parte de un estado aislado y conocido.
- El correo se captura en las pruebas (servicio falso o sustituido); las pruebas no dependen de que Mailpit esté en ejecución.

## Definición de terminado

Una tarea funcional está terminada cuando:

1. **Backend:** la aplicación importa y arranca sin errores y las migraciones se aplican sobre una base limpia.
2. **Frontend:** `astro check` no reporta errores y `npm run build` termina correctamente.
3. Los tests relevantes pasan.
4. Los flujos principales fueron verificados manualmente (correos revisados en Mailpit cuando aplique).
5. No quedan errores conocidos sin registrar.
6. La relación HU → RF → CU → implementación → prueba está actualizada en `docs/` (ver `academic-traceability`).

## Orden de verificación

```text
Test unitario / de servicio
↓
Test de integración (API + MySQL de pruebas)
↓
astro check + build del frontend
↓
Prueba manual/funcional
↓
Revisión de regresión
```

Empezar por la prueba focalizada que detecta antes un error evidente; ejecutar la suite completa antes de dar la tarea por terminada.

## Endpoints críticos

Requieren pruebas de comportamiento correcto, de errores y de control de acceso (sin sesión, rol incorrecto, recurso ajeno):

- autenticación (login, logout, sesión persistida, expiración e invalidación, CSRF por token y por `Origin`, activación, recuperación de contraseña);
- autorización por rol y por propiedad del recurso;
- residentes;
- pagos (registro manual y pago simulado de HU-010; comprobar que no hay llamadas externas ni datos de tarjeta);
- estados de cuenta (cálculo de saldos a partir de cargos y pagos);
- operaciones administrativas.

## Backend: verificar como mínimo

- rutas bajo `/api/v1` y métodos correctos;
- validaciones y formato de error estándar;
- códigos HTTP;
- autenticación, CSRF y autorización;
- persistencia y auditoría cuando apliquen;
- montos exactos con `Decimal`;
- casos de error.

## Frontend: verificar como mínimo

- `astro check` y build;
- rutas relevantes y redirecciones del middleware según sesión y rol;
- formularios y estados de error;
- consumo correcto de la API en el mismo origen (a través del proxy de desarrollo, sin CORS);
- comportamiento responsive cuando corresponda.

## Base de datos: después de una migración

- aplicar la migración en local;
- verificar el `downgrade` cuando sea viable;
- comprobar que la aplicación arranca;
- comprobar relaciones y restricciones relevantes;
- comprobar los seeds si fueron modificados.

## Prohibiciones

- No marcar una tarea como terminada basándose solo en una lectura del código.
- No ocultar ni desactivar tests fallidos.
- No saltar errores de build o de `astro check` sin documentarlos.
- No ejecutar pruebas contra la base de datos de desarrollo.
