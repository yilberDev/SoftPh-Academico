-- Consultas del guion de demostración (demo.md). Solo lectura (SELECT).
-- Conéctate a la base de la demostración, por ejemplo:
--   mysql -h 127.0.0.1 -u softph_academico -p softph_academico_demo
-- Cada bloque «@paso N» se ejecuta después del paso N del guion.

-- @paso 1: el administrador creado con el CLI y su auditoría
SELECT id, email, full_name, role, status, created_at FROM users;
SELECT id, action, entity_type, entity_id, created_at FROM audit_logs ORDER BY id;

-- @paso 2: la estructura residencial (el identificador B1-TO1-APTO-101 no se guarda: se deriva)
SELECT b.number AS bloque, t.number AS torre, u.id AS unit_id, u.number AS apartamento, u.created_at
FROM units u JOIN towers t ON t.id = u.tower_id JOIN blocks b ON b.id = t.block_id
WHERE u.deleted_at IS NULL;

-- @paso 3: la invitación (usuario pendiente de activación, sin contraseña; solo se guarda el hash del enlace)
SELECT u.id AS user_id, u.email, u.status, u.password_hash IS NULL AS sin_contrasena,
       i.id AS invitacion, i.unit_id, i.status AS estado_invitacion, i.delivery_status, i.expires_at
FROM resident_invitations i JOIN users u ON u.id = i.user_id;

-- @paso 4: la activación (cuenta activa, invitación usada, residencia en el apartamento)
SELECT u.email, u.status, u.document_number, i.status AS invitacion, i.used_at,
       r.unit_id, r.started_at, r.ended_at
FROM users u
JOIN resident_invitations i ON i.user_id = u.id
JOIN resident_units r ON r.invitation_id = i.id;

-- @paso 5: la obligación registrada por el administrador
SELECT id, unit_id, concept, billing_period, amount, due_date, created_by_user_id, created_at FROM charges;

-- @paso 6: el pago manual, su aplicación a la obligación y la notificación con el recibo
SELECT id, CONCAT('RC-', LPAD(id, 8, '0')) AS recibo, resident_id, unit_id, amount, method, source,
       transaction_reference, receipt_email_status, payment_date, registered_at
FROM payments ORDER BY id;
SELECT payment_id, charge_id, amount, created_at FROM payment_allocations ORDER BY id;
SELECT id, event_type, entity_type, entity_id, email_status, sent_at FROM notifications ORDER BY id;

-- @paso 7: el estado de cuenta no se almacena: se deriva de las obligaciones y los pagos aplicados
SELECT c.id AS obligacion, c.concept, c.amount AS valor,
       COALESCE(SUM(a.amount), 0) AS abonado,
       c.amount - COALESCE(SUM(a.amount), 0) AS saldo
FROM charges c LEFT JOIN payment_allocations a ON a.charge_id = c.id
GROUP BY c.id, c.concept, c.amount;

-- @paso 8: el pago de demostración (transacción, pago DEMO_GATEWAY y notificación sin correo)
SELECT id, reference, status, amount, test_instrument, payment_id, created_at, finished_at
FROM demo_payment_transactions ORDER BY id;
SELECT id, CONCAT('RC-', LPAD(id, 8, '0')) AS recibo, amount, method, source, receipt_email_status
FROM payments ORDER BY id;
SELECT id, event_type, entity_id, email_status FROM notifications ORDER BY id;

-- @paso 9: el feedback, su respuesta y la notificación al residente
SELECT id, resident_id, subject, category, urgency, status, admin_response, version, created_at, updated_at
FROM feedback;
SELECT id, event_type, entity_type, entity_id, title, email_status, sent_at
FROM notifications WHERE entity_type = 'feedback';
SELECT action, entity_type, entity_id, created_at FROM audit_logs
WHERE action IN ('FEEDBACK_CREATED', 'FEEDBACK_RESPONDED', 'FEEDBACK_NOTIFICATION_SENT') ORDER BY id;
