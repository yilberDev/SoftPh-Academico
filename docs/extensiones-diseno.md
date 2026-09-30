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

## Desviaciones temporales del entorno

| Tema | Situación | Resolución prevista |
|---|---|---|
| MySQL | El entorno local usa MySQL 8.0.46; la versión decidida es 8.4 LTS. La configuración es compatible con 8.4. | Actualizar el servidor local a 8.4 LTS. |
| Python 3.13 | El sistema solo trae Python 3.12; el intérprete 3.13 se obtuvo con `uv` (solo el intérprete). El entorno y las dependencias siguen usando `venv` + `pip`. | Opcional: instalar Python 3.13 en el sistema. |
