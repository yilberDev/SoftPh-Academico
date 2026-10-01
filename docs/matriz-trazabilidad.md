# Matriz de trazabilidad

Cadena: HU → RF → CU → implementación → prueba. Se actualiza al completar cada HU.

| HU | RF | CU | Nombre | Implementación | Pruebas | Estado |
|---|---|---|---|---|---|---|
| [HU-001](historias-usuario/HU-001.md) | [RF-001](requisitos-funcionales/RF-001.md) | [CU-001](casos-uso/CU-001.md) | Registro de administrador | `backend/app/cli.py` (`create-admin`), `app/services/users.py`, `app/services/audit.py`, `app/schemas/user.py`, `app/core/passwords.py`, `app/models/user.py`, `app/models/audit_log.py`, migraciones `633f6e19d008` y `925c4947c7a3` | `tests/unit/test_passwords.py`, `tests/unit/test_admin_schema.py`, `tests/integration/test_users_service.py`, `tests/integration/test_cli_create_admin.py`, `tests/integration/test_no_public_admin_registration.py`, `tests/integration/test_migrations.py`, `tests/integration/test_single_active_administrator.py` | Implementada |
| [HU-002](historias-usuario/HU-002.md) | [RF-002](requisitos-funcionales/RF-002.md) | [CU-002](casos-uso/CU-002.md) | Inicio de sesión del administrador | `backend/app/api/v1/auth.py`, `app/api/deps.py`, `app/services/auth.py`, `app/services/sessions.py`, `app/services/login_throttle.py`, `app/core/sessions.py`, `app/core/csrf.py`, `app/schemas/auth.py`, `app/models/session.py`, `app/models/login_throttle.py`, migración `5cde3a1e780c`; `frontend/src/pages/login.astro`, `src/pages/admin/index.astro`, `src/middleware.ts`, `src/components/LogoutButton.astro`, `src/lib/api/auth.ts`, `src/lib/api/session.ts`; corrección de seguridad: `app/core/emails.py`, migración `6236c5b88ac2` | `tests/unit/test_session_tokens.py`, `tests/unit/test_csrf.py`, `tests/unit/test_login_policy.py`, `tests/unit/test_login_schema.py`, `tests/unit/test_passwords.py`, `tests/integration/test_auth_login.py`, `tests/integration/test_login_throttle.py`, `tests/integration/test_auth_session.py`, `tests/integration/test_auth_logout.py`, `tests/integration/test_require_roles.py`, `tests/integration/test_auth_no_leaks.py`, `tests/unit/test_email_identity.py`, `tests/unit/test_passwords_async.py`, `tests/unit/test_settings.py`, `tests/integration/test_email_identity.py`, `tests/integration/test_email_migration.py`, `tests/integration/test_argon2_off_loop.py` | **Parcial**: criterios 1 y 2 cumplidos; criterio 3 cumplido en el bloqueo temporal, **pendiente** "solicitar el restablecimiento de la contraseña" |
| HU-003 | RF-003 | CU-003 | Invitación de residente | — | — | Pendiente |
| HU-004 | RF-004 | CU-004 | Registro de pagos | — | — | Pendiente |
| HU-005 | RF-005 | CU-005 | Gestión de feedback | — | — | Pendiente |
| HU-006 | RF-006 | CU-006 | Visualización de residentes | — | — | Pendiente |
| HU-007 | RF-007 | CU-007 | Inicio de sesión del residente | — | — | Pendiente |
| HU-008 | RF-008 | CU-008 | Historial de pagos del residente | — | — | Pendiente |
| HU-009 | RF-009 | CU-009 | Registro y consulta de feedback | — | — | Pendiente |
| HU-010 | RF-010 | CU-010 | Pago electrónico demo/sandbox | — | — | Pendiente |
| HU-011 | RF-011 | CU-011 | Estado de cuenta del residente para administrador | — | — | Pendiente |
| HU-012 | RF-012 | CU-012 | Reportes financieros | — | — | Pendiente |
| [HU-013](historias-usuario/HU-013.md) | [RF-013](requisitos-funcionales/RF-013.md) | [CU-013](casos-uso/CU-013.md) | Gestión de unidades residenciales | `backend/app/api/v1/structure.py`, `app/services/structure.py`, `app/schemas/structure.py`, `app/models/structure.py`, `app/core/unit_labels.py`, migración `522bf629af33`; `frontend/src/pages/admin/blocks/index.astro`, `src/pages/admin/blocks/[blockId].astro`, `src/pages/admin/towers/[towerId].astro`, `src/components/StructureLevel.astro`, `src/lib/api/structure.ts`, `src/lib/api/structure-server.ts` | `tests/unit/test_unit_labels.py`, `tests/unit/test_structure_schema.py`, `tests/integration/test_structure_api.py`, `tests/integration/test_structure_security.py`, `tests/integration/test_structure_concurrency.py`, `tests/integration/test_structure_database.py`, `tests/integration/test_structure_migration.py` | Implementada |
| HU-014 | RF-014 | CU-014 | Estado de cuenta propio del residente | — | — | Pendiente |
| HU-015 | RF-015 | CU-015 | Gestión de información del residente | — | — | Pendiente |
| HU-016 | RF-016 | CU-016 | Restablecimiento de contraseña | — | — | Pendiente |
| HU-017 | RF-017 | CU-017 | Notificaciones al residente | — | — | Pendiente |
| HU-018 | RF-018 | CU-018 | Control de acceso según rol | — | — | Pendiente |
| HU-019 | RF-019 | CU-019 | Activación de cuenta de residente | — | — | Pendiente |
