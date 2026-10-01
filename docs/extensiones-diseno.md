# Extensiones de diseño aprobadas

Decisiones aprobadas que interpretan o precisan el documento académico y que aún no están reflejadas en él.

| HU afectadas | Decisión |
|---|---|
| HU-001 | No hay registro público de administradores; el primer administrador se crea con un mecanismo controlado del backend. |
| HU-002, HU-007, HU-018 | Una única interfaz de login; el backend determina el rol (`ADMINISTRADOR` / `RESIDENTE`). Ambas HU se cumplen con el mismo flujo. |
| HU-004, HU-008, HU-011, HU-012, HU-014 | Las cuotas/cargos mensuales forman parte del modelo financiero; los saldos se calculan a partir de cargos y pagos. |
| HU-010 | El "pago electrónico demo/sandbox" se implementa como una simulación interna del backend, sin pasarela externa. |
| HU-017 y demás HU con notificaciones | Correo solo en los flujos definidos por los requisitos; Mailpit en desarrollo. |
| HU-013 | Una única copropiedad (Urbanización San Joaquín) con la estructura física Bloque → Torre → Apartamento; la unidad residencial es el apartamento y su número es único dentro de su torre (ver detalle). |

## HU-001 — Registro de administrador (detalle)

| Tema | Decisión |
|---|---|
| Mecanismo | El "formulario de registro" de HU-001 se implementa como el comando interactivo `python -m app.cli create-admin`, que se ejecuta en el servidor. No existe endpoint HTTP ni página de registro. El comando cumple los tres criterios de aceptación: confirma el registro exitoso, rechaza correos ya registrados y muestra las validaciones incumplidas. |
| Alcance | El comando solo crea el **primer** administrador: si ya existe un administrador activo, rechaza la operación. |
| Datos | Obligatorios: nombre completo, correo electrónico y contraseña. Opcionales: número de documento y teléfono. |
| Correo solo ASCII (nuevos registros) | Desde la corrección de seguridad A-1, el correo de una cuenta nueva solo puede contener caracteres ASCII (sin tildes, eñes ni caracteres de ancho completo). Evita cuentas con correos visualmente parecidos a otros (homógrafos Unicode). Endurece la regla de validación de HU-001; el inicio de sesión no rechaza correos Unicode, simplemente no coinciden con ninguna cuenta. |
| Contraseña | Entre 12 y 128 caracteres, no solo espacios, distinta del correo, igual a la confirmación. Sin clases de caracteres obligatorias. Se guarda con Argon2id. |
| CU-001 | El documento no incluye la descripción detallada de CU-001; se redactó en [casos-uso/CU-001.md](casos-uso/CU-001.md) siguiendo la plantilla aprobada, sin funcionalidades adicionales. |
| Auditoría | La creación se registra en `audit_logs` (`ADMIN_CREATED`, origen `cli`). |
| Garantía ante concurrencia | La base de datos garantiza que exista como máximo un administrador activo: la columna generada `active_administrator_marker` vale 1 solo para un administrador activo y no eliminado (NULL en los demás casos), y el índice `UNIQUE` `uq_users_active_administrator_marker` admite un único 1. Si dos ejecuciones simultáneas del comando pasan la verificación previa, MySQL rechaza la segunda inserción y el comando informa que ya existe un administrador activo (migración `925c4947c7a3`). Reactivar un administrador deshabilitado mientras otro está activo también se rechaza. |

## HU-002 — Inicio de sesión del administrador (detalle)

| Tema | Decisión |
|---|---|
| Login único | `POST /api/v1/auth/login` es el único inicio de sesión para todos los roles y el backend determina el rol. Mientras HU-007 no esté implementada, solo el rol ADMINISTRADOR obtiene sesión; un residente recibe la misma respuesta genérica que cualquier fallo. |
| Respuesta indistinguible | Correo inexistente, contraseña incorrecta, cuenta deshabilitada, pendiente de activación o eliminada, y rol no permitido responden igual: 401 "Correo electrónico o contraseña no válidos.". Si el correo no existe se verifica contra un hash ficticio para igualar el tiempo de respuesta. El motivo real solo queda en `audit_logs`. |
| Sesión | Guardada en la tabla `sessions`. La cookie `softph_session` (HttpOnly, SameSite=Lax, Path=/, Secure configurable) contiene solo un token aleatorio de 256 bits; la base de datos guarda solo su SHA-256. Vence a las 8 horas o tras 30 minutos de inactividad (configurable en `.env`). Un nuevo login revoca la sesión previa de la cookie. Deshabilitar o eliminar la cuenta invalida sus sesiones. |
| CSRF | Token derivado: HMAC-SHA256 del token de sesión (no se almacena). Se exige junto con la comprobación de `Origin` en las modificaciones con sesión (hoy, el logout). El login solo exige `Origin`. |
| Control de intentos (criterio 3) | 5 fallos en 15 minutos bloquean el correo normalizado durante 15 minutos (configurable). El contador se guarda por hash del correo, exista o no la cuenta, así el bloqueo no revela qué correos están registrados. Los intentos del mismo correo se serializan con un bloqueo de fila, de modo que los intentos concurrentes se cuentan todos. Consecuencia aceptada: un tercero que conozca el correo puede bloquear temporalmente el acceso. |
| **Criterio 3 — pendiente** | **"Solicitará el restablecimiento de la contraseña" no está implementado.** No existe un flujo de restablecimiento para el administrador (HU-016 es del residente) y su implementación está aplazada. HU-002 no se considera completamente satisfecha hasta cubrirlo. |
| Panel principal | `/admin` es un panel mínimo de validación (saludo y cierre de sesión), sin funcionalidades de administración. |
| Protección de rutas (base de HU-018) | `get_current_auth` y `require_roles` (FastAPI) y el middleware SSR de Astro protegen `/admin`. HU-018 los ampliará. |
| IP en auditoría | Se registra `request.client.host`; no se confía en `X-Forwarded-For` hasta definir el proxy de producción. Detrás del proxy de desarrollo la IP registrada es `127.0.0.1`. |

## Correcciones de seguridad de HU-002 (A-1, M-1, M-2)

| Tema | Decisión |
|---|---|
| Identidad del correo: canonicalización de aplicación | La regla es `normalize_email()` (`backend/app/core/emails.py`): quitar espacios de los extremos (`strip`, que incluye tabuladores y espacios Unicode), normalizar a **NFC** y pasar a **minúsculas**. Se aplica antes de guardar, de buscar y de calcular el identificador del control de intentos. Son el mismo correo: mayúsculas/minúsculas, espacios en los extremos y formas Unicode compuesta/descompuesta (é = e + ◌́). Son correos distintos: con o sin tilde, de ancho completo (ＡＢＣ), `ı` sin punto y espacios internos. |
| Identidad del correo: base de datos | `users.email` compara byte a byte (`utf8mb4_0900_bin`, NO PAD). Antes usaba `utf8mb4_0900_ai_ci`, que igualaba variantes con tilde o de ancho completo y permitía eludir el bloqueo de intentos. MySQL y el control de intentos consideran ahora "el mismo correo" exactamente lo mismo. El identificador del contador se calcula siempre con el correo enviado por el cliente, nunca con el guardado. |
| CHECK de la base de datos | `ck_users_email_canonical` (`email = LOWER(email) AND email = TRIM(email)`) y `ck_users_email_ascii` (un byte por carácter) son una **defensa adicional, no un sustituto** de `normalize_email()`: MySQL no normaliza NFC, y `TRIM()` solo quita el espacio U+0020, no reproduce `strip()` de Python. |
| Migración `6236c5b88ac2` | Valida antes de modificar la estructura y aplica el cambio en una única sentencia `ALTER TABLE` (atómica en InnoDB). El `upgrade` aborta sin cambios si algún correo no es canónico o no es ASCII (no los reescribe, para no fusionar identidades). El `downgrade` aborta sin cambios si algún correo quedaría duplicado bajo `utf8mb4_0900_ai_ci` o si faltan las CHECK; solo después elimina las CHECK y restaura la colación, en la misma sentencia. |
| Argon2 fuera del bucle de eventos (M-1) | Verificar y calcular hashes Argon2 (64 MiB y ~65 ms cada uno) se ejecuta en hilos (`anyio.to_thread.run_sync`) con un `CapacityLimiter` de `PASSWORD_HASH_MAX_CONCURRENCY` operaciones simultáneas (por defecto 2, rango 1–4). El hash ficticio del login se calcula al arrancar la API. El comportamiento funcional no cambia. |
| Cookie `Secure` obligatoria fuera de local (M-2) | `SESSION_COOKIE_SECURE=false` solo se admite con `APP_ENV` `development` o `test` y `APP_ORIGIN` `http://localhost`, `http://127.0.0.1` o `http://[::1]`. En cualquier otro caso, y siempre con `APP_ENV=production`, la API y el comando `create-admin` no arrancan. El mensaje de error no muestra valores de configuración. |

## HU-013 — Gestión de unidades residenciales (detalle)

| Tema | Decisión |
|---|---|
| Estructura del dominio | La Urbanización San Joaquín se organiza en **Bloque → Torre → Apartamento**. Bloques y torres no aparecen en el documento académico: son la estructura necesaria para registrar correctamente las unidades. No se crean HU nuevas ni se cambian los criterios. La unidad residencial de HU-013 es el **apartamento** (tabla `units`; en la interfaz, "Apartamento"). |
| Nomenclatura | Bloques `B1`, `B2`…; torres `TO1`, `TO2`…; apartamentos `APTO-1`, `APTO-104`… Identificador oficial completo: **`B1-TO2-APTO-104`**. |
| Persistencia | Cada nivel guarda solo su número entero (`blocks.number`, `towers.number`, `units.number`). El identificador completo **no se almacena**: el backend lo deriva siempre de la jerarquía guardada (`app/core/unit_labels.py`) y la API lo devuelve en `label` (y el nombre del nivel en `name`). Todas las relaciones usan ids estables. |
| Sin rangos de negocio | Los números son enteros positivos, sin máximo de dominio. La columna `INT UNSIGNED` tiene un límite técnico (4 294 967 295) que se informa como "demasiado grande para el sistema", no como regla del negocio. |
| Unicidad | Un bloque activo por número; una torre activa por número dentro de su bloque (`TO2` puede existir en `B1` y en `B2`); un apartamento activo por número dentro de su torre. La garantía final son índices `UNIQUE` sobre columnas generadas `VIRTUAL` (`active_number_marker`: el número mientras no está eliminado, NULL después). |
| Edición | Se puede corregir el número de un bloque, una torre o un apartamento; si el nuevo número ya existe en su contexto, se rechaza (409). `block_id` y `tower_id` son inmutables: no existe un flujo de traslado. Control optimista con `version` (también en el borrado). Al renumerar un padre no se reescribe ninguna fila: solo cambia el identificador derivado de sus descendientes (`B1-TO2-APTO-104` → `B4-TO2-APTO-104`). La interfaz lo advierte antes de confirmar. |
| Borrado | Lógico (`deleted_at`), sin cascadas. Un bloque con torres activas o una torre con apartamentos activos no se puede eliminar (409). Una entidad eliminada no aparece en los listados y responde 404. Su número se puede reutilizar (nuevo registro con otro id). Restaurar queda fuera del alcance. Las claves foráneas usan `ON DELETE RESTRICT`. |
| Concurrencia | Orden fijo de bloqueos bloque → torre → apartamento con lecturas con bloqueo. Crear un hijo bloquea el padre en modo compartido; eliminar un padre lo bloquea en exclusiva y busca hijos activos, de modo que nunca queda un hijo activo bajo un padre eliminado. Los duplicados simultáneos los rechaza el `UNIQUE`. |
| Vista previa en formularios | La API entrega `child_label_prefix` (p. ej., `B1-TO2-APTO-`) y el formulario muestra ese prefijo + el número escrito. Es solo una vista previa: el identificador oficial es el `label` que devuelve el backend al guardar. |
| Búsqueda y paginación | No forman parte de HU-013. La interfaz usa navegación jerárquica con migas de pan (`Bloques › B1 › TO2`). |
| API | `GET`/`POST /api/v1/blocks`; `GET`/`PATCH`/`DELETE /api/v1/blocks/{id}`; `GET`/`POST /api/v1/blocks/{block_id}/towers`; `GET`/`PATCH`/`DELETE /api/v1/towers/{id}`; `GET`/`POST /api/v1/towers/{tower_id}/units`; `GET`/`PATCH`/`DELETE /api/v1/units/{id}`. Todas exigen sesión de ADMINISTRADOR; las modificaciones exigen además `Origin` y token CSRF. `DELETE` recibe `?version=`. |
| Auditoría | `BLOCK_CREATED`/`BLOCK_UPDATED`/`BLOCK_DELETED`, `TOWER_*` y `UNIT_*` en `audit_logs`, en la misma transacción, con el número y el identificador (anterior y nuevo en las ediciones). |
| Migración `522bf629af33` | Escrita a mano (Alembic no compara CHECK y solo maneja en parte las columnas generadas); la estructura real se verifica en `information_schema` en las pruebas. El `upgrade` aborta sin cambios si alguna de las tablas ya existe. El `downgrade` aborta sin cambios si hay filas (incluidas las eliminadas lógicamente) y luego borra las tres tablas en una sola sentencia atómica. |
| Relación con HU futuras | HU-003 implementará la disponibilidad de unidades (`/units/available`) y el rechazo del borrado de un apartamento en uso. Residentes, cargos y pagos referencian `units.id`. Los documentos emitidos (p. ej., recibos) deberán guardar una copia del identificador vigente al emitirse; lo decidirá la HU correspondiente. |

## Desviaciones temporales del entorno

| Tema | Situación | Resolución prevista |
|---|---|---|
| MySQL | El entorno local usa MySQL 8.0.46; la versión decidida es 8.4 LTS. La configuración es compatible con 8.4. | Actualizar el servidor local a 8.4 LTS. |
| Python 3.13 | El sistema solo trae Python 3.12; el intérprete 3.13 se obtuvo con `uv` (solo el intérprete). El entorno y las dependencias siguen usando `venv` + `pip`. | Opcional: instalar Python 3.13 en el sistema. |
