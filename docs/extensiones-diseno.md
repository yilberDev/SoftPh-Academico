# Extensiones de diseño aprobadas

Decisiones aprobadas que interpretan o precisan el documento académico y que aún no están reflejadas en él.

| HU afectadas | Decisión |
|---|---|
| HU-001 | No hay registro público de administradores; el primer administrador se crea con un mecanismo controlado del backend. |
| HU-002, HU-007, HU-018 | Una única interfaz de login; el backend determina el rol (`ADMINISTRADOR` / `RESIDENTE`). Ambas HU se cumplen con el mismo flujo. |
| HU-004, HU-008, HU-011, HU-012, HU-014 | Las cuotas/cargos mensuales forman parte del modelo financiero; los saldos se calculan a partir de cargos y pagos. |
| HU-010 | El "pago electrónico demo/sandbox" se implementa como una simulación interna del backend, sin pasarela externa. |
| HU-017 y demás HU con notificaciones | Correo solo en los flujos definidos por los requisitos; Mailpit en desarrollo. |
| HU-013 | Una única copropiedad (Urbanización San Joaquín); unidades únicas dentro de ella. |

## HU-001 — Registro de administrador (detalle)

| Tema | Decisión |
|---|---|
| Mecanismo | El "formulario de registro" de HU-001 se implementa como el comando interactivo `python -m app.cli create-admin`, que se ejecuta en el servidor. No existe endpoint HTTP ni página de registro. El comando cumple los tres criterios de aceptación: confirma el registro exitoso, rechaza correos ya registrados y muestra las validaciones incumplidas. |
| Alcance | El comando solo crea el **primer** administrador: si ya existe un administrador activo, rechaza la operación. |
| Datos | Obligatorios: nombre completo, correo electrónico y contraseña. Opcionales: número de documento y teléfono. |
| Contraseña | Entre 12 y 128 caracteres, no solo espacios, distinta del correo, igual a la confirmación. Sin clases de caracteres obligatorias. Se guarda con Argon2id. |
| CU-001 | El documento no incluye la descripción detallada de CU-001; se redactó en [casos-uso/CU-001.md](casos-uso/CU-001.md) siguiendo la plantilla aprobada, sin funcionalidades adicionales. |
| Auditoría | La creación se registra en `audit_logs` (`ADMIN_CREATED`, origen `cli`). |
| Garantía ante concurrencia | La base de datos garantiza que exista como máximo un administrador activo: la columna generada `active_administrator_marker` vale 1 solo para un administrador activo y no eliminado (NULL en los demás casos), y el índice `UNIQUE` `uq_users_active_administrator_marker` admite un único 1. Si dos ejecuciones simultáneas del comando pasan la verificación previa, MySQL rechaza la segunda inserción y el comando informa que ya existe un administrador activo (migración `925c4947c7a3`). Reactivar un administrador deshabilitado mientras otro está activo también se rechaza. |

## Desviaciones temporales del entorno

| Tema | Situación | Resolución prevista |
|---|---|---|
| MySQL | El entorno local usa MySQL 8.0.46; la versión decidida es 8.4 LTS. La configuración es compatible con 8.4. | Actualizar el servidor local a 8.4 LTS. |
| Python 3.13 | El sistema solo trae Python 3.12; el intérprete 3.13 se obtuvo con `uv` (solo el intérprete). El entorno y las dependencias siguen usando `venv` + `pip`. | Opcional: instalar Python 3.13 en el sistema. |
