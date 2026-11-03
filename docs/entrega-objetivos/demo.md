# Guion de demostración (unos 10 minutos)

Objetivo: mostrar que cada acción de la interfaz deja filas persistentes en MySQL. Se recorre el ciclo completo con una base vacía: administrador → estructura → invitación → activación → obligación → pago manual con recibo → estado de cuenta → pago de demostración → feedback y notificación.

Las consultas de cada paso están en [demo-consultas.sql](demo-consultas.sql), marcadas con `@paso N`; aquí se repiten las principales.

> **Verificación (2026-11-03).** El recorrido completo y las nueve consultas se ejecutaron sobre el commit `f543c74`, con la interfaz real (Playwright) contra una API apuntada a `softph_academico_test`, mediante el mismo mecanismo `DB_NAME=…` que usa este guion. Las columnas «Resultado esperado» copian lo que devolvieron. Lo único que **no** se ejecutó es la creación de la base `softph_academico_demo` (paso 0), porque requiere `sudo`; después, esa base se limpió con `python -m tests.e2e.seed clean`.

## 0. Preparación (antes de la reunión, unos 5 minutos)

1. **Base vacía para la demo.** El CLI solo crea el *primer* administrador y la base de desarrollo ya tiene uno, así que la demo usa su propia base. Con permisos de administrador de MySQL:

   ```bash
   sudo mysql -e "CREATE DATABASE IF NOT EXISTS softph_academico_demo CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
                  GRANT ALL PRIVILEGES ON softph_academico_demo.* TO 'softph_academico'@'127.0.0.1';"
   ```

   Si tu `DB_USER` no es `softph_academico`, cambia el nombre. El `.env` no se modifica: la base se elige con la variable `DB_NAME` en cada comando, porque las variables de entorno tienen prioridad sobre el `.env`.

2. **Esquema** (desde `backend/`):

   ```bash
   DB_NAME=softph_academico_demo .venv/bin/alembic upgrade head
   DB_NAME=softph_academico_demo .venv/bin/alembic current    # d33ea6140186 (head)
   ```

3. **Pago de demostración activado:** `DEMO_PAYMENTS_ENABLED=true` en el `.env` de la raíz (lo leen la API y Astro).

4. **Servicios.** Mailpit en marcha (`http://localhost:8025`). En dos terminales:

   ```bash
   cd backend && DB_NAME=softph_academico_demo .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
   cd frontend && npm run dev        # queda en segundo plano; se detiene con: npx astro dev stop
   ```

5. **Ventanas abiertas:** el navegador en `http://localhost:4321` (exactamente `localhost`, el `APP_ORIGIN`), una ventana privada para el residente, Mailpit, y una terminal con MySQL:

   ```bash
   mysql -h 127.0.0.1 -u softph_academico -p softph_academico_demo
   ```

## 1. Crear el administrador (1 min)

```bash
cd backend && DB_NAME=softph_academico_demo .venv/bin/python -m app.cli create-admin
```

Datos: «Directora Demo», `admin.demo@example.com`, documento y teléfono vacíos, una contraseña de 12 o más caracteres (no se muestra al escribirla).

```sql
SELECT id, email, full_name, role, status, created_at FROM users;
SELECT id, action, entity_type, entity_id, created_at FROM audit_logs ORDER BY id;
```

| Resultado esperado |
|---|
| `users`: una fila `ADMINISTRADOR`, `ACTIVE`. `audit_logs`: `ADMIN_CREATED` sobre `user`. |

Qué decir: no existe registro público de administradores; el comando exige una terminal y se niega si ya hay uno activo.

## 2. Estructura residencial (1 min)

Inicia sesión en `/login` como administrador. En «Unidades residenciales» (`/admin/blocks`): agrega el bloque `1`, entra a **B1**, agrega la torre `1`, entra a **TO1** y agrega el apartamento `101`.

```sql
SELECT b.number AS bloque, t.number AS torre, u.id AS unit_id, u.number AS apartamento, u.created_at
FROM units u JOIN towers t ON t.id = u.tower_id JOIN blocks b ON b.id = t.block_id
WHERE u.deleted_at IS NULL;
```

| Resultado esperado |
|---|
| Una fila: bloque 1, torre 1, apartamento 101. El identificador `B1-TO1-APTO-101` que muestra la pantalla **no se guarda**: se deriva. |

## 3. Invitar a un residente (1 min)

«Invitar residente» (`/admin/residents/invite`): correo `ana.torres@example.com`, nombre «Ana Torres», apartamento `B1-TO1-APTO-101`, vigencia 7 días. Muestra el correo de invitación en Mailpit.

```sql
SELECT u.id AS user_id, u.email, u.status, u.password_hash IS NULL AS sin_contrasena,
       i.id AS invitacion, i.unit_id, i.status AS estado_invitacion, i.delivery_status, i.expires_at
FROM resident_invitations i JOIN users u ON u.id = i.user_id;
```

| Resultado esperado |
|---|
| Usuario `PENDING_ACTIVATION` sin contraseña (`sin_contrasena = 1`); invitación `PENDING`, envío `SENT`, vence en 7 días. En la base solo se guarda el SHA-256 del enlace. |

## 4. Activar la cuenta (1 min)

En la ventana privada, abre el enlace del correo de Mailpit. Completa documento `1098765432`, contraseña y confirmación, y pulsa «Activar mi cuenta».

```sql
SELECT u.email, u.status, u.document_number, i.status AS invitacion, i.used_at,
       r.unit_id, r.started_at, r.ended_at
FROM users u
JOIN resident_invitations i ON i.user_id = u.id
JOIN resident_units r ON r.invitation_id = i.id;
```

| Resultado esperado |
|---|
| Usuario `ACTIVE` con su documento; invitación `USED` con `used_at`; residencia en el apartamento, `ended_at` nulo. Abrir el mismo enlace otra vez muestra «Este enlace ya fue utilizado». |

## 5. Registrar una obligación (1 min)

Como administrador, en «Obligaciones» (`/admin/obligations`) → «Nueva obligación»: B1 / TO1 / `B1-TO1-APTO-101`, concepto **Mensualidad**, periodo del mes en curso, monto `180000`, vencimiento dentro de 10 días.

```sql
SELECT id, unit_id, concept, billing_period, amount, due_date, created_by_user_id, created_at FROM charges;
```

| Resultado esperado |
|---|
| Una fila `MONTHLY_FEE`, 180000.00, periodo = primer día del mes, `created_by_user_id` = el administrador. |

## 6. Registrar un pago y ver el recibo (1 min)

En «Pagos» (`/admin/payments`): residente «Ana», monto `100000`, Transferencia, Mensualidad, referencia `TRF-DEMO-001`. Muestra la confirmación (saldo pendiente de la unidad: $ 80.000) y en Mailpit el correo «Recibo de pago RC-…» con el PDF adjunto.

```sql
SELECT id, CONCAT('RC-', LPAD(id, 8, '0')) AS recibo, resident_id, unit_id, amount, method, source,
       transaction_reference, receipt_email_status, payment_date, registered_at
FROM payments ORDER BY id;
SELECT payment_id, charge_id, amount, created_at FROM payment_allocations ORDER BY id;
SELECT id, event_type, entity_type, entity_id, email_status, sent_at FROM notifications ORDER BY id;
```

| Resultado esperado |
|---|
| Pago 100000.00, `TRANSFER`, `MANUAL`, `TRF-DEMO-001`, recibo `SENT`. Una aplicación de 100000 a la mensualidad. Notificación `PAYMENT_REGISTERED` con correo `SENT`. El número de recibo no se guarda: es `RC-` más el id. |

## 7. Estado de cuenta del residente (1 min)

En la ventana privada, inicia sesión como Ana y abre «Estado de cuenta».

```sql
SELECT c.id AS obligacion, c.concept, c.amount AS valor,
       COALESCE(SUM(a.amount), 0) AS abonado,
       c.amount - COALESCE(SUM(a.amount), 0) AS saldo
FROM charges c LEFT JOIN payment_allocations a ON a.charge_id = c.id
GROUP BY c.id, c.concept, c.amount;
```

| Resultado esperado |
|---|
| Pantalla: total pendiente $ 80.000,00, aplicado $ 100.000,00, estado «Pendiente». SQL: valor 180000, abonado 100000, saldo 80000. Qué decir: el saldo no se almacena en ninguna columna; se calcula con las obligaciones y los pagos aplicados, así que no puede desincronizarse. |

## 8. Pago de demostración (1,5 min)

Como Ana, en «Pago de demostración»: «Pagar esta obligación» (propone los $ 80.000 pendientes) → «Continuar» → «Iniciar pago de demostración» → elige «Tarjeta de prueba ficticia terminada en 0001» → «Procesar pago de demostración» → «Pago aprobado y registrado».

```sql
SELECT id, reference, status, amount, test_instrument, payment_id, created_at, finished_at
FROM demo_payment_transactions ORDER BY id;
SELECT id, CONCAT('RC-', LPAD(id, 8, '0')) AS recibo, amount, method, source, receipt_email_status
FROM payments ORDER BY id;
SELECT id, event_type, entity_id, email_status FROM notifications ORDER BY id;
```

| Resultado esperado |
|---|
| Transacción `APPROVED`, referencia `DEMO-…`, 80000, `TEST_CARD_APPROVE`, con su `payment_id`. Un segundo pago 80000 `DEMO_GATEWAY` cuyo `receipt_email_status` queda en `PENDING`. Su notificación tiene `email_status = NOT_REQUIRED`. Si vuelves al estado de cuenta, la mensualidad aparece «Pagada» con saldo 0. |

Qué decir: es una simulación interna (sin dinero real ni pasarela externa) y, por decisión de diseño, el pago de demostración no envía correo: el comprobante se descarga desde el detalle del pago. Esa decisión está pendiente de validación académica (HU-010 criterio 2.2 y HU-017 criterio 3).

## 9. Feedback, respuesta y notificación (1,5 min)

Como Ana, «Enviar feedback»: asunto «Iluminación del parqueadero», un mensaje, categoría **Solicitud**, urgencia **Media**. Como administrador, en «Feedback» abre ese feedback, escribe una respuesta y pulsa «Guardar respuesta». Como Ana, abre «Notificaciones»: aparecen tres (respuesta, pago de demostración y pago).

```sql
SELECT id, resident_id, subject, category, urgency, status, admin_response, version, created_at, updated_at
FROM feedback;
SELECT id, event_type, entity_type, entity_id, title, email_status, sent_at
FROM notifications WHERE entity_type = 'feedback';
SELECT action, entity_type, entity_id, created_at FROM audit_logs
WHERE action IN ('FEEDBACK_CREATED', 'FEEDBACK_RESPONDED', 'FEEDBACK_NOTIFICATION_SENT') ORDER BY id;
```

| Resultado esperado |
|---|
| Feedback `REQUEST`, `MEDIUM`, estado `PENDING` (responder no lo resuelve), con `admin_response` y `version = 2`. Notificación `FEEDBACK_RESPONDED` con correo `SENT`. Auditoría: `FEEDBACK_CREATED`, `FEEDBACK_RESPONDED`, `FEEDBACK_NOTIFICATION_SENT`. |

## Cierre opcional: la persistencia sobrevive a un reinicio (30 s)

Detén la API (Ctrl+C en su terminal) y vuelve a lanzarla con el mismo comando del paso 0. Recarga el estado de cuenta del residente: los datos siguen ahí, porque viven en MySQL y no en la memoria del servidor.

## Después de la reunión

- Detén Astro con `npx astro dev stop` (desde `frontend/`) y la API con Ctrl+C.
- La base `softph_academico_demo` queda con los datos de la demostración. Borrarla es irreversible; si quieres hacerlo: `sudo mysql -e "DROP DATABASE softph_academico_demo;"`.
- Los correos quedan en Mailpit; no hace falta borrarlos.
