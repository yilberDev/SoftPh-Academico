# Revisión técnica profunda de SoftPh-Académico (noviembre de 2026)

- **Fecha:** 13 de noviembre de 2026.
- **Versión revisada:** rama `main` (`3056977`). Entre la etiqueta `v1.1-academico` (`c673c86`) y `main` no cambia nada en `backend/` ni en `frontend/` (`git diff --quiet v1.1-academico main -- frontend backend`), así que todo lo encontrado aplica a la versión evaluada en el informe.
- **Rama de trabajo:** `revision-profunda`. No se modificó nada en `backend/app` ni en `frontend/src`. Solo se añadieron pruebas en `backend/tests/revision/` y `frontend/e2e/revision/`, y este documento.
- **Datos:** solo la base `softph_academico_test`, con datos ficticios. Al terminar, la base quedó vacía.
- **Informe contrastado:** `Informe_SoftPh_v3.pdf`, del 10 de noviembre de 2026, leído desde una copia.

## 1. Resumen

1. La línea base coincide con el informe en todas las cifras: 2648 pruebas de backend, 504 de frontend, E2E con 133 aprobadas y 2 omitidas, 16 tablas, 24 FK, 16 routers, 28 servicios, 18 schemas y 32 páginas. `alembic check` no detecta diferencias.
2. No hay hallazgos **críticos**: en las 236 combinaciones de ruta × actor no aparecieron accesos indebidos ni IDOR. Tampoco se encontró pérdida ni corrupción de datos, ni siquiera con pagos, obligaciones, mensualidades y pagos de demostración concurrentes.
3. **Riesgo 1. Sin corrección financiera (R-01, R-16).** Un pago o una obligación registrados por error no se pueden anular. Un residente con saldo a favor no se puede eliminar, salvo que se invente una obligación ficticia.
4. **Riesgo 2. Bloqueo del administrador (R-05).** Basta con conocer el correo del único administrador para dejarlo fuera del sistema de forma indefinida: cinco intentos cada 15 minutos lo mantienen bloqueado aunque use la contraseña correcta.
5. **Riesgo 3. Ciclo de vida del residente incompleto (R-06, R-07, R-09, R-10).** Hay 25 hallazgos en total (4 altos, 12 medios y 9 bajos). Un residente eliminado no puede volver, porque su correo y su documento quedan reservados. Tampoco se puede trasladar a otro apartamento. El filtro «Inactivo» de HU-006 siempre sale vacío, y un residente nuevo hereda la mora generada mientras el apartamento estaba vacío. Recomiendo pasar HU-006 c2.2 a «Parcial».

## 2. Línea base frente a las cifras del informe

La validación de la sección 8 de la guía se ejecutó en secuencia, sin dos suites a la vez, el 13 de noviembre de 2026 entre las 10:35 y las 10:50 (hora de Colombia), con MySQL 8.0.46 y Mailpit 1.31.0.

| Verificación | Informe (3/11/2026) | Resultado de hoy | ¿Coincide? |
|---|---|---|---|
| `pytest` (backend) | 2648 aprobadas, 0 fallidas | **2648 aprobadas**, 0 fallidas, 0 omitidas (4 min 15 s) | Sí |
| `npm test` | 504 aprobadas | **504 aprobadas**, 0 fallidas, 0 omitidas | Sí |
| `npx tsc --noEmit` | Sin errores | Sin errores | Sí |
| `npm run check` (astro check) | 172 archivos, 0/0/0 | 172 archivos: 0 errores, 0 advertencias, 0 sugerencias | Sí |
| `npm run build` | Completada | Completada | Sí |
| `npm run test:e2e` | 135 casos: 133 aprobados, 2 omitidos | **135 casos: 133 aprobados, 2 omitidos**, 0 fallidos (7,6 min) | Sí |
| `alembic current` / `heads` | `d33ea6140186 (head)` | `d33ea6140186 (head)`, una sola head; 16 migraciones | Sí |
| `alembic check` | «No new upgrade operations detected.» | Igual | Sí |
| Tablas (`information_schema`) | 16 | 16, todas InnoDB, más `alembic_version` | Sí |
| Claves foráneas | 24 | 24 (22 `ON DELETE RESTRICT` y 2 `NO ACTION`; ninguna en cascada) | Sí |
| Routers | 16 | 16 módulos en `api/v1` (sin contar `router.py` ni `__init__.py`). Aparte, hay 18 objetos `APIRouter`: `account_statements.py` define dos | Sí (cuenta por módulos) |
| Servicios | 28 | 28 módulos en `app/services` | Sí |
| Schemas | 18 | 18 módulos en `app/schemas` | Sí |
| Modelos | 10 | 10 módulos en `app/models` | Sí |
| Páginas | 32 | 32 archivos en `frontend/src/pages` | Sí |

Notas:

- `alembic current`, `heads` y `check` se ejecutaron sobre `softph_academico_test`, no sobre la base de desarrollo, para cumplir la regla de usar solo la base de pruebas. La guía indica ejecutarlos sobre `DB_NAME`.
- La suite completa del backend, incluidas las pruebas nuevas de `tests/revision/`, se ejecutó de nuevo al final (sección 7).

## 3. Tabla de hallazgos

Severidad: **Crítica**: pérdida o corrupción de datos, o acceso indebido. **Alta**: bloquea una operación sin salida desde la aplicación. **Media**: función incompleta o resultado confuso. **Baja**: mejora.

Las pruebas marcadas con `xfail(strict=True)` (backend) o `test.fail()` (E2E) describen el comportamiento esperado y fallan hoy por el defecto. Para ver la causa real del fallo: `pytest --runxfail tests/revision`. Todas se comprobaron así, y cada una falla por la razón declarada.

### 3.1 Fase 2: confirmación de la revisión estática

| ID | Área | Severidad | Evidencia | Cómo reproducirlo | Corrección propuesta | Esfuerzo (h) | Afecta al informe |
|---|---|---|---|---|---|---|---|
| R-01 | Finanzas | **Alta** | No existe ninguna ruta de anulación, reverso o edición: las únicas escrituras financieras son `POST /admin/charges`, `POST /admin/charges/monthly` y `POST /admin/payments` (OpenAPI). Pruebas `test_r01_*` en `backend/tests/revision/test_rev_phase2_gaps.py` | Registrar un pago con un monto equivocado: la aplicación no ofrece forma de deshacerlo. Las FK `RESTRICT` y las comprobaciones de saldo (`UNIT_HAS_BALANCE`, `RESIDENT_DELETION_BLOCKED`) dejan el error fijo | Anulación con contrapartida auditada, sin borrar filas: `payments.voided_at` y `charges.voided_at`, o un movimiento de reverso; liberar las aplicaciones y volver a aplicar FIFO bajo el bloqueo del apartamento. Requiere migración | 16–24 | 10.5 y 10.3 (declarar la limitación); 12–13 (trabajos futuros) |
| R-02 | Finanzas / correo | Media | Solo existe la descarga del comprobante de un pago **de demostración**: `backend/app/api/v1/resident_payments.py:79` (`get_demo_receipt`). No hay ninguna ruta de administración para el recibo. Prueba `test_r02_*` | `receipt_email_status = FAILED`, por ejemplo con Mailpit apagado: el administrador ve «fallido», pero no puede reenviar ni descargar el PDF | `POST /admin/payments/{id}/receipt/resend` y `GET /admin/payments/{id}/receipt` (el PDF ya se genera con `build_receipt_pdf`). Botón en `/admin/payments` | 6 | 9.4 (el resultado queda registrado, pero no se puede corregir) |
| R-03 | Invitaciones | Media | No hay ruta de cancelación (`backend/app/api/v1/invitations.py`: solo crear y reenviar). La vigencia va de 1 a 30 días (`backend/app/models/invitation.py:34`) y el apartamento sale de `/units/available` mientras la invitación está pendiente. Pruebas `test_r03_*` | Invitar con un correo equivocado y 30 días de vigencia: una segunda invitación al mismo apartamento responde `409 UNIT_NOT_AVAILABLE` hasta que la primera vence | Estado `CANCELLED` (migración del CHECK) y `POST /resident-invitations/{id}/cancel`, con auditoría | 6 | 10.5 (HU-003) |
| R-04 | Cuentas | Media | No hay ninguna ruta de cambio de contraseña con sesión iniciada (OpenAPI). Prueba `test_r04_*` | El administrador quiere cambiar su contraseña: solo puede hacerlo por SQL. (Que no tenga recuperación ya está declarado; el **cambio** es otra función) | `POST /auth/password`: pide la contraseña actual, aplica la política de HU-001 y revoca las demás sesiones | 6 | 10.3 / 13 |
| R-05 | Seguridad | **Alta** | Durante el bloqueo, el login rechaza también la contraseña correcta: `backend/app/services/auth.py:91-95`. El contador se reinicia al vencer el bloqueo: `backend/app/services/login_throttle.py:52`. La clave es solo el correo. Pruebas `test_r05_*` | Un atacante que solo conoce el correo hace 5 intentos fallidos y el administrador recibe `429 LOGIN_TEMPORARILY_LOCKED` con su contraseña correcta. Con una ráfaga de 5 intentos cada 15 minutos (480 al día), el correo queda bloqueado el 100 % del día; con un intento por minuto, el ~79 % | Limitar por (correo, IP) además de por correo; aplicar un límite global por IP; dejar que la contraseña correcta entre desde una IP sin fallos (o retraso progresivo en vez de bloqueo duro); comando `app.cli unlock` | 6–8 | 9.6 (describe el bloqueo como protección; añadir el riesgo); Anexo A RF-002 |
| R-06 | Residentes (HU-006) | Media | Nada escribe `UserStatus.DISABLED` (`test_r06_no_code_writes_the_disabled_status`). El filtro lo traduce en `backend/app/services/residents.py:86`. `ResidentUpdate` rechaza `status` (`extra="forbid"`, `backend/app/schemas/resident.py:117`) | `GET /admin/residents?status=INACTIVE` siempre devuelve `total = 0`. Intentar inhabilitar a un residente con `PATCH` responde 422 | Una de dos: (a) acción «Inhabilitar/Habilitar» en HU-015 (`DISABLED` + revocación de sesiones + auditoría); o (b) quitar «Inactivo» y declararlo | (a) 6 / (b) 1 | **Figura 30 y 10.2** (HU-006 c2.2), **10.5**, Anexo A RF-006 |
| R-07 | Residentes | **Alta** | El correo de una cuenta eliminada sigue reservado: `backend/app/services/invitations.py:270-271`. Además, el **documento** también queda reservado: la activación exige documento (`backend/app/schemas/activation.py:45`) y su unicidad no excluye cuentas eliminadas (`backend/app/services/activation.py:243`, `uq_users_document_number`). Pruebas `test_r07_*` | Eliminar a un residente que se mudó y volver a invitarlo: `409 EMAIL_ALREADY_REGISTERED`. Con otro correo, la activación responde `409 DOCUMENT_ALREADY_REGISTERED`. La persona no puede volver por ninguna vía | Reactivar la cuenta eliminada (`deleted_at = NULL`, `PENDING_ACTIVATION`, invitación nueva) conservando el historial, o liberar correo y documento al eliminar (ver R-25) | 6–8 | 10.5 (HU-015: hoy solo dice «el correo sigue reservado»; añadir el documento y la consecuencia) |
| R-08 | Residentes | Media | `ResidentUpdate` solo acepta nombre, documento y teléfono (`backend/app/schemas/resident.py:117`). Prueba `test_r08_*` | Corregir un correo mal escrito después de la activación: 422 | Cambio de correo con verificación (enlace de un solo uso al correo nuevo) y revocación de sesiones | 8 | 10.5 (HU-015 c1.2: ver la sección 3.3) |
| R-09 | Residentes | Media (Alta junto con R-07) | No hay ninguna ruta de traslado (`test_r09_*`). La única vía sería eliminar y volver a invitar, y eso lo impide R-07 | Un residente cambia de apartamento dentro de la copropiedad: no hay forma de reflejarlo | `POST /admin/residents/{id}/move`: termina la residencia, crea la nueva y aplica la regla de saldo de la unidad de origen | 8–10 | 13 (trabajos futuros) |
| R-10 | Finanzas | Media | La generación mensual incluye todo apartamento no eliminado, tenga o no residente: `backend/app/services/charges.py:546`. El estado de cuenta muestra **todas** las obligaciones del apartamento actual: `backend/app/services/account_statement.py:182-185`. Pruebas `test_r10_*` | Generar agosto con el apartamento vacío y activar un residente en noviembre: su estado de cuenta muestra $180.000 pendientes y **«En mora»** desde el primer día | Decisión de negocio. (a) Generar solo para apartamentos con residencia vigente, o (b) mantener la regla de Ley 675 (la cuota es del inmueble) y mostrar al residente solo las obligaciones desde el inicio de su residencia, con un aviso de «deuda anterior del inmueble» para el administrador | 4–8 | 9.4 y 10.5 (declarar la regla); HU-011/HU-014 (ver 3.3) |
| R-11 | Residente | Baja | No hay ruta para marcar notificaciones como leídas, ni perfil del residente (`test_r11_*`) | — | `PATCH /resident/notifications/{id}` (`read_at`, requiere migración); `PATCH /resident/profile` (teléfono) y el cambio de contraseña de R-04 | 8 | 13 |
| R-12 | Despliegue | Media (**ya declarado**) | El proxy `/api/v1` solo está en `vite.server.proxy` (`frontend/astro.config.mjs:44`). Con la build (`node dist/server/entry.mjs`), `GET /api/v1/health` y `POST /api/v1/auth/login` responden **404** desde Astro | Ejecutar `npm run build` y `API_INTERNAL_URL=… node dist/server/entry.mjs`: el login del navegador no llega a la API | Proxy inverso de producción (nginx o Caddy) o un endpoint de reenvío en Astro; documentarlo | 4 | No: ya declarado («sin configuración de producción»). Ver también R-21 |
| R-13 | Seguridad HTTP | Baja | Ni la API ni las páginas envían `Content-Security-Policy`, `X-Frame-Options`/`frame-ancestors` ni `X-Content-Type-Options` (solo el PDF de demostración envía `nosniff`). Se comprobó con `curl` sobre la build y con la prueba `test_r13_*` | `curl -D - http://127.0.0.1:4556/` y `GET /api/v1/health`: no aparecen esas cabeceras | Middleware en FastAPI y en Astro: `nosniff`, `frame-ancestors 'none'`, una CSP base (`default-src 'self'`) y `Referrer-Policy`. Riesgo bajo: la cookie es `SameSite=Lax`, así que una página enmarcada desde otro sitio se ve sin sesión | 2 | 9.6 (opcional) |
| R-14 | Auditoría | Baja (**decisión documentada**) | `backend/app/api/deps.py:23-25` usa `request.client.host`. Prueba `test_r14_*` | Detrás de un proxy, todas las IP auditadas serían la del proxy | Confiar en `X-Forwarded-For` solo cuando venga del proxy definido (`--forwarded-allow-ips` de uvicorn) al configurar producción | 1 | No (ya está en `extensiones-diseno.md`, HU-002 «IP en auditoría») |
| R-15 | Documentación | Baja | `docs/extensiones-diseno.md`: **línea 11** («su reevaluación está pendiente»: ya se reevaluó el 2026-11-03); **línea 125** («aún no existe ningún flujo que termine una residencia (HU-015)»: HU-015 la termina al eliminar); **línea 228** («llegará con HU-015»: HU-015 no añadió la inhabilitación); **línea 237** (reevaluación «pendiente»). También están desactualizadas la **línea 44** («hoy, el logout»: el CSRF se exige en 22 rutas de escritura) y la **línea 47** («panel mínimo»: el panel se rediseñó en `a126838`) | Leer esas líneas frente a `docs/matriz-trazabilidad.md:12` y el código | Actualizar las seis filas | 1 | Solo `docs/extensiones-diseno.md` |

### 3.2 Fase 3: hallazgos nuevos

| ID | Área | Severidad | Evidencia | Cómo reproducirlo | Corrección propuesta | Esfuerzo (h) | Afecta al informe |
|---|---|---|---|---|---|---|---|
| R-16 | Finanzas / residentes | **Alta** | `backend/app/services/residents.py:326`: un saldo a favor distinto de cero bloquea la eliminación, y no existe devolución ni anulación (R-01). Pruebas `test_r16_*` | Registrar un pago sin obligaciones (saldo a favor) y eliminar al residente: `409 RESIDENT_DELETION_BLOCKED`. La única salida es crear una obligación ficticia («Ajuste para devolver saldo»), que queda en el historial (`test_r16_the_only_way_out_is_a_fictitious_charge`). Lo mismo ocurre con un apartamento con saldo a favor (`UNIT_HAS_BALANCE`) | Devolución del saldo a favor como movimiento propio (por ejemplo, `payments.refunded_amount` o un reverso), junto con R-01 | 6 (con R-01) | 9.4 y 10.5 (la política aprobada no tiene salida para el saldo a favor) |
| R-17 | Entradas | Baja | `backend/app/services/charges.py:194`: la descripción de una obligación solo se recorta. Acepta `\x00`, secuencias ESC, saltos de línea y U+202E (texto invertido). En cambio, la referencia y las notas del pago sí los rechazan. Prueba `test_r17_*` en `test_rev_phase3_rules.py` | `POST /admin/charges` con `description = "Multa\u0000\n‮…"`: se guarda tal cual y luego se muestra en listados, estados de cuenta y el comprobante PDF | Mismo validador que `clean_transaction_reference` (una línea, `isprintable()`) | 1 | No |
| R-18 | Entradas | Baja | `backend/app/schemas/user.py:15`: el patrón de nombre no admite marcas combinantes y el nombre no se normaliza a NFC. Prueba `test_r18_*` | «José Pérez» escrito en NFD (`unicodedata.normalize('NFD', …)`) se rechaza con «solo puede contener letras…» | `unicodedata.normalize("NFC", …)` en `clean_full_name` antes de validar | 1 | No |
| R-19 | Recibo PDF (HU-004) | Media | `backend/app/services/receipts.py:110` dibuja cada valor en una sola línea, sin ajuste (el comprobante de demostración sí ajusta con `_wrap`). Caben unos 57 caracteres. Prueba `test_r19_*` | Un nombre de 149 caracteres ocupa 761 pt de los 314 disponibles; una descripción de «Otro» de 198 caracteres queda cortada en el borde del PDF que recibe el residente | Reutilizar `_wrap` en `build_receipt_pdf` | 1–2 | No (Figura 19 no cambia con valores cortos) |
| R-21 | Despliegue | Baja | La build no lee el `.env` raíz: `API_INTERNAL_URL` es obligatoria en `process.env` (`frontend/astro.config.mjs:28`) | `HOST=127.0.0.1 PORT=4555 node dist/server/entry.mjs` sin la variable: **todas** las páginas responden 500 con el cuerpo vacío, incluida la pública `/` (log: `EnvInvalidVariables: API_INTERNAL_URL is missing`) | Documentar `node --env-file=../.env dist/server/entry.mjs` o un servicio con `Environment=`; que `/` no dependa de la API | 1 | No (complementa la limitación ya declarada) |
| R-22 | Rendimiento (HU-012) | Media | `GET /admin/reports/payments` devuelve todos los pagos del rango, sin paginación ni límite de rango (`backend/app/api/v1/reports.py:35`) | Con 20.000 pagos (sección 4.2): enero a noviembre responde en **818 ms** con **8,9 MB** de JSON, que la página debe dibujar entera | Paginar la tabla (los totales en una consulta aparte), limitar el rango (por ejemplo, 366 días) o exportar a CSV | 4 | 10.3 / 9.9 («no se evaluó la carga»: ahora hay un dato) |
| R-23 | Rendimiento | Baja | La generación mensual procesa los apartamentos uno por uno dentro de la petición HTTP (`backend/app/services/charges.py:691`) | Con 1.000 apartamentos: **26,1 s** y **17.004 sentencias SQL** en un solo `POST /admin/charges/monthly`. Detrás de un proxy con 60 s de tiempo de espera, el límite estaría en unos 2.300 apartamentos | Ejecutar en segundo plano con avance consultable, o por torres; documentar el límite | 4–8 | 10.3 (opcional) |
| R-24 | Dependencias | Media | `pip-audit` sobre el entorno instalado: **46 avisos** (42 de `pillow` 12.3.0, que llega por `reportlab` aunque no se usan imágenes; 2 de `certifi`, 1 de `dnspython` y 1 de `opentelemetry-api`). `npm audit`: **4** (3 altos: `vite` 8.3.1, `undici` 8.11.2 y `zod` 4.6.5; 1 moderado: `fast-uri`), todos en herramientas de desarrollo o compilación. `requirements.txt` fija solo las dependencias directas: `starlette`, `pydantic`, `anyio`, etc. no están fijadas | `uvx pip-audit --path backend/.venv/lib/python3.13/site-packages`; `npm audit` | Archivo de restricciones (`pip-compile` o `uv pip compile`) con hashes; `npm audit fix` y volver a validar. Los rangos `^` de `package.json` **no** cambian lo instalado mientras se use `package-lock.json` (que fija astro 7.3.5, @astrojs/node 11.1.6, tailwind 4.3.3, typescript 6.0.3); el riesgo real está en Python | 2–3 | 9.8 (gestión de configuración) |
| R-25 | Datos personales | Media | La eliminación es solo lógica: `backend/app/services/residents.py:366` (`deleted_at`). Correo, nombre, documento y teléfono se conservan indefinidamente y no hay ningún proceso de supresión ni de anonimización | Eliminar a un residente: la fila conserva todos sus datos personales | Política de retención: anonimizar los datos de contacto al eliminar y conservar los *snapshots* financieros; documentar la base legal (Ley 1581, derecho de supresión) | 4 | **9.9** (consideraciones normativas) |
| R-26 | Navegación | Baja | El menú superior del administrador no incluye «Reportes» (`frontend/src/components/AdminHeader.astro:14-24`); solo se llega desde una tarjeta del panel | — | Añadir «Reportes» al menú | 0,5 | No (la Figura 15 muestra el mismo menú) |

**Descartado:** R-20. La encuesta a 375 px marcó en `/admin/residents` un `scrollWidth` 517 px mayor que la ventana. Se comprobó que la tabla se desplaza **dentro** de su contenedor y que la página no se desplaza (`window.scrollX` sigue en 0). La prueba quedó como verificación positiva en `frontend/e2e/revision/pages.spec.ts` («ruled out…», también para pagos, obligaciones y feedback).

### 3.3 Criterios de aceptación (punto I)

| Criterio | Estado en el informe | Estado propuesto | Motivo |
|---|---|---|---|
| **HU-006 c2.2** («opciones de filtrado por estado… activo o inactivo») | Cumple | **Parcial** | La opción existe, pero ningún flujo produce `DISABLED` (R-06): «Inactivo» siempre devuelve 0 filas. En el Anexo A, RF-006 lista «filtro por estado» como aprobado |
| HU-015 c1.2 («modificar los datos que correspondan al registro») | Cumple | Cumple o Parcial, **a decidir** | El correo forma parte del registro (HU-003) y no se puede corregir (R-08). Si se considera un identificador de acceso fuera del alcance, se mantiene «Cumple» y se declara en 10.5 |
| HU-015 c5.2 («realizar la operación») | Cumple (política aprobada) | Sin cambio; **declarar** | Con saldo a favor no hay salida sin una obligación ficticia (R-16) |
| HU-003 c4.2 (duplicados) | Cumple | Sin cambio; **declarar** | La regla real es más estricta que «cuenta o invitación activa»: también rechaza correos y documentos de cuentas eliminadas (R-07) |
| HU-011 / HU-014 (estado de cuenta) | Cumple | Sin cambio; **declarar** | El estado de cuenta incluye la deuda del apartamento anterior a la residencia (R-10) |
| HU-002 c3 | Parcial | Parcial | R-05 agrava la parte ya declarada (no hay recuperación) |
| HU-004 c3.2 | Cumple | Sin cambio | El resultado del envío se informa; R-02 es una mejora |

Si solo cambia HU-006 c2.2, el recuento queda en **135 cumplen (93,75 %, frente al 94,4 % que dice 10.2), 3 parciales, 4 difieren y 2 no determinables** (144). Si además cambia HU-015 c1.2: 134, 4, 4 y 2. Habría que actualizar la Figura 30, el texto de 10.2, la Tabla 63 (objetivo 4, «136 se cumplen»), las conclusiones (sección 11), 10.5, el Anexo A (RF-006) y la fila de HU-006 en `docs/matriz-trazabilidad.md`.

## 4. Revisión profunda por área: lo que se verificó sin hallazgos

### 4.1 A. Lógica financiera y concurrencia

- **Montos:** se rechazan 0, negativos, 3 decimales, `NaN`, `Infinity` y `MONEY_MAX + 0,01`, tanto en pagos como en obligaciones. `MONEY_MAX` (9.999.999.999,99) se acepta sin redondeo. FIFO con pagos parciales y saldo a favor exacto al centavo; el saldo a favor se consume del más antiguo al más nuevo (`test_rev_phase3_rules.py`).
- **Concurrencia:** `test_rev_phase3_concurrency.py` mezcla, en 4 rondas sobre el mismo apartamento y con *commits* reales, dos ejecuciones simultáneas de la misma mensualidad, 5 pagos manuales, un pago de un vecino, 3 obligaciones y 2 pagos de demostración (inicio y aprobación). Tras cada ronda se comprueba lo siguiente:
  - ningún pago ni obligación tiene más aplicado que su monto;
  - **ningún apartamento tiene a la vez saldo a favor y deuda**;
  - hay una mensualidad por periodo;
  - hay un pago por transacción aprobada.

  Se ejecutó 4 veces sin fallos, *deadlocks* ni errores 500. Ya estaban cubiertos por las pruebas existentes: doble *complete* (`test_two_approvals_create_one_payment`), pago manual y de demostración a la vez (`test_approval_and_a_manual_payment_on_the_same_charge`), generación repetida o en paralelo (`test_two_runs_of_the_same_period`, `test_partial_run_then_a_new_run`) y el vencimiento de 15 minutos (`test_expiry_is_exact`, `test_reading_resolves_the_expiry`, `test_an_expired_transaction_is_never_paid`).
- **Mora con FK compuesta:** la cubren `test_two_late_fees_for_one_origin_at_once` y el CHECK `source_only_for_late_fee`. No se encontró ninguna forma de crear mora de otro apartamento ni mora de una mora.

### 4.2 G. Datos, índices y volumen

`backend/tests/revision/volume.py` sembró en `softph_academico_test` 1.000 apartamentos, 1.000 residentes, 12.000 mensualidades, 20.000 pagos, 22.000 aplicaciones, 2.000 *feedback* y 20.000 notificaciones (17,9 s), midió la API dentro del proceso (mediana de 5 ejecuciones) y vació la base.

| Petición | Mediana | Sentencias SQL | Respuesta |
|---|---|---|---|
| `/admin/residents` (página 1 / búsqueda / página 50) | 19–23 ms | 3 | 1,9 KB |
| `/admin/payments` (página 1 / búsqueda / página 200 de 100) | 29–58 ms | 3 | 4,5–44 KB |
| `/admin/payments?sort_by=status` | 97–112 ms | 3–4 | 4,5 KB |
| `/admin/charges` (página 1 / vencidas por saldo / página 1000) | 137–155 ms | 3 | 3,3 KB |
| Estado de cuenta (administrador / residente) | 23–25 ms | 9 | 10,8 KB |
| `/admin/reports/pending-balances` | 268 ms | 7 | 151 KB |
| `/admin/reports/payments` (un mes) | 38 ms | 2 | 445 KB |
| `/admin/reports/payments` (enero–noviembre) | **818 ms** | 2 | **8,9 MB** (R-22) |
| `POST /admin/charges/monthly` (1.000 apartamentos) | **26,1 s** | **17.004** | (R-23) |
| Páginas del residente (pagos, notificaciones, feedback) | 9–10 ms | 3–4 | — |

- **No hay consultas N+1:** el número de sentencias no depende del tamaño de la página.
- Ordenar por estado (`sort_by=status`) recorre los 20.000 pagos con subconsultas dependientes (`EXPLAIN ANALYZE`: 79 ms). Es lineal, pero aceptable a este volumen.
- Las 24 FK son `RESTRICT`/`NO ACTION`: no hay efectos en cascada, y los borrados son lógicos.
- Los índices cubren los órdenes de los listados (`ix_charges_unit_id_due_date_created_at`, `ix_payments_payment_date`, `ix_feedback_status_created_at`, etc.). Las búsquedas `LIKE '%…%'` recorren la tabla, lo que a este volumen no es un problema.

### 4.3 B. Fechas

- El corte de «vencida» es la medianoche de Bogotá: a las 23:59 del día de vencimiento (04:59 UTC del día siguiente) la obligación sigue «Pendiente», y a las 00:00 pasa a «Vencida».
- Colombia no tiene horario de verano.
- Se aceptan el periodo `2028-02` con vencimiento el 29 de febrero.
- Se rechazan los periodos `2026-13`, `2026-00`, `2026-2`, `26-02`, `2026-02-01` y `" 2026-02"`, el vencimiento anterior al periodo y la fecha de pago posterior a hoy en Bogotá.
- Los rangos de reportes son inclusivos (`>=` / `<=`).

### 4.4 C. Autorización: matriz ruta × actor

`test_rev_phase3_access.py::test_matrix_route_by_actor` toma las rutas del OpenAPI (59 rutas, más `logout`, que se prueba aparte) y las prueba con cuatro actores: anónimo, administrador, residente A (dueño de los recursos) y residente B. Son **236 combinaciones, todas según la política**:

- 52 respuestas 401 para el anónimo en las rutas privadas;
- 14 respuestas 403 para el administrador en las rutas de residente;
- 37 respuestas 403 para cada residente en las rutas de administración;
- 7 respuestas **404** para el residente B sobre los pagos, comprobantes, transacciones de demostración (consultar, aprobar, cancelar) y el *feedback* del residente A. La respuesta es idéntica a la de un recurso inexistente (sin IDOR).

El rol enviado por el cliente se ignora, según las pruebas existentes de `test_access_control.py`. Para obtener la matriz completa en CSV: `REVISION_MATRIX_OUT=matriz.csv pytest tests/revision/test_rev_phase3_access.py`.

### 4.5 D. Sesión y CSRF

- **Inactividad:** con 29 minutos la sesión sigue válida; con 30 min 1 s responde 401.
- **Expiración absoluta:** pasado `expires_at` responde 401.
- **Cierre de sesión:** el token copiado deja de servir.
- **Eliminación de la cuenta:** revoca la sesión del residente.
- **Cookie:** `HttpOnly; SameSite=Lax; Path=/; Max-Age=28800`; `Secure` según la configuración (falso solo en HTTP local), con `Cache-Control: no-store`.
- **CSRF y Origin:** en las **22** rutas privadas de escritura, la petición sin token, con `Origin` ajeno o sin `Origin` responde `403 CSRF_FAILED`. Las escrituras públicas (login, activación, restablecimiento) exigen `Origin`.

### 4.6 E. Entradas

- **Nombres:** se rechazan los de 1 y 2 caracteres, se aceptan los de 150 y se rechazan los de 151, así como emoji y `<script>`.
- **Búsquedas:** `%` y `_` se buscan literalmente (`contains(..., autoescape=True)` en residentes, pagos y *feedback*).
- **Inyección de HTML:**
  - No hay `innerHTML` ni `insertAdjacentHTML` en `frontend/src`.
  - Los dos `set:html` (`admin/residents/[id].astro:28`, `admin/feedback/[id].astro:24`) escapan `<` como `<` dentro de `<script type="application/json">`.
  - El tercero (`landing/Icon.astro`) usa constantes.
  - El resto del texto pasa por el escape de Astro.
- **Correos:** son de texto plano y sus asuntos son constantes. Las notas del pago normalizan los saltos de línea.
- Excepciones: R-17, R-18 y R-19.

### 4.7 F. Frontend

`frontend/e2e/revision/pages.spec.ts` recorrió, a 375 px, las 32 páginas: 6 públicas, 16 de administración y 10 de residente.

- **Por rol:**
  - Sin sesión, las 17 protegidas redirigen a `/login`.
  - El administrador recibe 403 en `/resident`, y el residente, 403 en `/admin`.
  - Un *feedback* ajeno responde 404.
  - Las demás páginas responden 200 con un único `<h1>`.
- **Desplazamiento y teclado:** ninguna página se desplaza en horizontal (R-20 descartado). El primer Tab llega al botón «Abrir menú» (no hay enlace «Saltar al contenido» fuera de la portada: mejora menor de accesibilidad).
- **API caída:** con la API respondiendo 503 al iniciar sesión, aparece la alerta y el botón se vuelve a habilitar. Si la API está caída, las páginas protegidas responden 503 con texto (verificado sobre la build).
- **Doble envío:** un doble clic en «Registrar pago» registra **un** pago.
- **Figuras 14 a 27:** salen de `docs/capturas`. Las capturas 01–19 se tomaron el 27/10 (`5bb3a54`). El único cambio de interfaz posterior (`7191762`, detalle del *feedback* del residente) quedó recogido en la captura 20 (Figura 26). El menú del administrador coincide con la Figura 15: Inicio, Unidades residenciales, Residentes, Invitar residente, Obligaciones, Pagos y Feedback.

## 5. Lo que no se pudo verificar

1. **Producción.** No existe configuración de producción (proxy inverso, TLS, SMTP real ni servicio), así que no se probaron las cabeceras, el reenvío de IP (R-14) ni el comportamiento detrás de un proxy. R-12 y R-21 se comprobaron solo con la build local.
2. **Un pico aislado de 76 s** en `/admin/payments?sort_by=status` durante la primera medición de volumen (sospecha). No se reprodujo en 15 ejecuciones posteriores (máximo 117 ms). Probablemente fue actividad interna de InnoDB tras la carga masiva, pero no se pudo confirmar: faltaría el registro de consultas lentas de MySQL durante esa medición.
3. **Aplicabilidad de los avisos de seguridad (R-24).** No se analizó si el código de SoftPh llega a las rutas vulnerables. Por ejemplo, `reportlab` usa `pillow` solo para imágenes, que los recibos no incluyen.
4. **Otros navegadores y tecnologías de apoyo.** Solo se usó Chromium (Playwright). No se probaron Firefox, Safari ni dispositivos reales, ni lectores de pantalla. La navegación por teclado se limitó al primer Tab y a los menús que ya cubren las pruebas existentes.
5. **Comparación píxel a píxel de las figuras 14 a 27.** La correspondencia se estableció por la cronología de los commits y por la revisión visual de la Figura 15.
6. **Tiempos.** Se midieron en el equipo de desarrollo, con MySQL 8.0.46 (no 8.4 LTS) y la API dentro del proceso (sin uvicorn ni la red). Son orientativos.
7. **Concurrencia entre varios procesos de uvicorn.** Las pruebas usan conexiones independientes desde un solo proceso. El bloqueo es de MySQL, así que el resultado debería ser el mismo, pero no se ejecutó con varios *workers*.
8. **Los 144 criterios uno por uno.** Solo se reevaluaron los afectados por los hallazgos (sección 3.3). El resto mantiene el estado del informe.

## 6. Plan propuesto

### 6.1 Antes de la sustentación

Ninguno de estos puntos toca la versión evaluada (`v1.1-academico`). Cualquier cambio de código exigiría repetir la validación completa y crear una etiqueta nueva.

1. **Informe** (2–3 h):
   - Pasar HU-006 c2.2 a «Parcial» y actualizar la Figura 30, 10.2, la Tabla 63, 10.5 y el Anexo A (RF-006).
   - Declarar en 10.3/10.5 las limitaciones R-01/R-16 (sin anulación ni devolución), R-05 (bloqueo por correo), R-07 (correo y documento reservados) y R-10 (deuda anterior del inmueble).
   - Añadir a 9.9 la nota sobre la conservación de datos personales (R-25).
2. **`docs/extensiones-diseno.md`:** actualizar las líneas 11, 44, 47, 125, 228 y 237 (R-15), 1 h. Actualizar `docs/matriz-trazabilidad.md` (HU-006) si se acepta el cambio.
3. **Respuestas preparadas para el jurado:**
   - Cómo se corrige hoy un pago erróneo (no se puede desde la aplicación) y por qué se optó por la inmutabilidad.
   - El riesgo del bloqueo del administrador y su mitigación propuesta.
   - La regla de la cuota del inmueble vacío (Ley 675).
4. **Opcional, solo si se decide publicar una v1.2** con nueva validación completa: R-19 (ajuste del recibo, 1–2 h), R-17 (1 h), R-18 (1 h), R-13 (cabeceras, 2 h) y R-26 (0,5 h). Son correcciones pequeñas y aisladas que no requieren migración.

### 6.2 Trabajos futuros

| Prioridad | Hallazgos | Trabajo | Esfuerzo (h) |
|---|---|---|---|
| 1 | R-01, R-16 | Anulación o reverso auditado de pagos y obligaciones, y devolución del saldo a favor (con migración) | 22–30 |
| 2 | R-05, R-04 | Limitación de intentos por correo e IP, desbloqueo administrativo, cambio de contraseña y recuperación para el administrador | 12–14 |
| 3 | R-07, R-09, R-25 | Reactivación de residentes, traslado entre apartamentos y política de retención o anonimización | 18–22 |
| 4 | R-06, R-08 | Inhabilitar o habilitar residentes (o retirar el filtro) y cambio de correo con verificación | 14 |
| 5 | R-10 | Decisión de negocio sobre las cuotas de inmuebles vacíos y el alcance del estado de cuenta por periodo de residencia | 4–8 |
| 6 | R-02, R-03 | Reenvío y descarga del recibo manual; cancelación de invitaciones | 12 |
| 7 | R-12, R-21, R-13, R-14 | Configuración de producción: proxy inverso, variables de entorno, cabeceras, IP real, TLS y SMTP | 8–12 |
| 8 | R-22, R-23, R-24 | Paginación del reporte de pagos, generación mensual en segundo plano y dependencias fijadas y auditadas | 10–14 |
| 9 | R-11, R-26 | Notificaciones leídas, perfil del residente y menú de reportes | 8–9 |

## 7. Pruebas añadidas y cómo ejecutarlas

| Archivo | Contenido | Resultado |
|---|---|---|
| `backend/tests/revision/test_rev_phase2_gaps.py` | Fase 2 (R-01 a R-16) | Aprobadas las de confirmación; las de defecto, como `xfail` estricto |
| `backend/tests/revision/test_rev_phase3_rules.py` | Montos, fechas, textos y recibo PDF (sin base de datos) | Ídem |
| `backend/tests/revision/test_rev_phase3_access.py` | Matriz ruta × actor, CSRF, sesiones y cookie | Aprobadas |
| `backend/tests/revision/test_rev_phase3_concurrency.py` | Estrés mixto con *commits* reales | Aprobada (4 de 4 ejecuciones) |
| `backend/tests/revision/volume.py` | Volumen y tiempos (no lo recoge pytest): `.venv/bin/python -m tests.revision.volume --out FILE` | Ver 4.2 |
| `frontend/e2e/revision/pages.spec.ts` | 32 páginas × rol a 375 px, API caída en el login, doble envío y R-20 descartado | Aprobadas |

```bash
cd backend && .venv/bin/pytest tests/revision                 # nunca a la vez que el E2E
cd backend && .venv/bin/pytest --runxfail tests/revision      # muestra la causa real de cada defecto
cd frontend && npx playwright test e2e/revision
```

### 7.1 Suites completas con las pruebas añadidas (13/11/2026)

| Suite | Resultado |
|---|---|
| `cd backend && .venv/bin/pytest` (11:00–11:04) | **2686 aprobadas, 16 xfail, 0 fallidas**: las 2648 originales más 38 de revisión. La prueba `test_r07_a_deleted_resident_comes_back_with_another_email` se añadió después; `pytest tests/revision` da 38 aprobadas y 17 xfail |
| `cd frontend && npm run test:e2e` (11:04–11:11) | **139 casos: 137 aprobados, 2 omitidos, 0 fallidos**: los 133 originales más 4 de revisión; las 2 omitidas son las mismas capturas sin `SHOTS_DIR` |
| `npx tsc --noEmit` | Sin errores |
| `ruff check tests/revision` | Quedan 9 avisos de estilo: `DTZ001`/`DTZ005` (fechas *naive* en UTC, la convención del proyecto) y `ASYNC230` (escritura del CSV o JSON opcional). `tests/integration` ya tiene 197 avisos con la misma configuración |

Al terminar, `softph_academico_test` quedó vacía: la limpieza de las propias suites y de `volume.py`.
