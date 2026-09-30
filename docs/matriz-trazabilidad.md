# Matriz de trazabilidad

Cadena: HU → RF → CU → implementación → prueba. Se actualiza al completar cada HU.

| HU | RF | CU | Nombre | Implementación | Pruebas | Estado |
|---|---|---|---|---|---|---|
| [HU-001](historias-usuario/HU-001.md) | [RF-001](requisitos-funcionales/RF-001.md) | [CU-001](casos-uso/CU-001.md) | Registro de administrador | `backend/app/cli.py` (`create-admin`), `app/services/users.py`, `app/services/audit.py`, `app/schemas/user.py`, `app/core/passwords.py`, `app/models/user.py`, `app/models/audit_log.py`, migraciones `633f6e19d008` y `925c4947c7a3` | `tests/unit/test_passwords.py`, `tests/unit/test_admin_schema.py`, `tests/integration/test_users_service.py`, `tests/integration/test_cli_create_admin.py`, `tests/integration/test_no_public_admin_registration.py`, `tests/integration/test_migrations.py`, `tests/integration/test_single_active_administrator.py` | Implementada |
| HU-002 | RF-002 | CU-002 | Inicio de sesión del administrador | — | — | Pendiente |
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
| HU-013 | RF-013 | CU-013 | Gestión de unidades residenciales | — | — | Pendiente |
| HU-014 | RF-014 | CU-014 | Estado de cuenta propio del residente | — | — | Pendiente |
| HU-015 | RF-015 | CU-015 | Gestión de información del residente | — | — | Pendiente |
| HU-016 | RF-016 | CU-016 | Restablecimiento de contraseña | — | — | Pendiente |
| HU-017 | RF-017 | CU-017 | Notificaciones al residente | — | — | Pendiente |
| HU-018 | RF-018 | CU-018 | Control de acceso según rol | — | — | Pendiente |
| HU-019 | RF-019 | CU-019 | Activación de cuenta de residente | — | — | Pendiente |
