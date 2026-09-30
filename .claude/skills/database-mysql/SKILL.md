---
description: Guide MySQL 8.4 schema design, async SQLAlchemy models, Alembic migrations, indexes, constraints, financial modeling, audit, soft delete, and seed data for SoftPh-Academico. Use when designing or changing persistence.
---

# SoftPh-Académico — Base de datos MySQL

## Reglas generales

- MySQL 8.4 LTS, instalación local nativa (sin Docker inicialmente).
- La base de datos es exclusiva del proyecto académico. No reutilizar el esquema ni datos del SoftPh comercial sin autorización explícita.
- Bases separadas para desarrollo y pruebas (la de pruebas se define en la skill `testing`). Credenciales solo en el `.env` de la raíz.
- Conjunto de caracteres `utf8mb4`.

## Fuente de verdad del esquema

**Alembic es la única fuente de verdad del esquema.**

- Las migraciones viven en `backend/alembic/`.
- No usar `Base.metadata.create_all` ni DDL manual para crear el esquema, en ningún entorno (tampoco en pruebas).
- No crear `database/schema.sql` ni otros scripts que definan tablas en paralelo a las migraciones.
- `database/` solo puede contener: scripts de preparación local (crear base de datos y usuario, sin tablas), diagramas del modelo y datos ficticios de ejemplo.
- Si se genera un diagrama o un volcado del esquema, es documentación derivada de las migraciones, nunca su origen.

## Convenciones de nombres

- Tablas y columnas en inglés, `snake_case`; tablas en plural (`users`, `units`, `charges`, `payments`).
- Claves primarias `id`; claves foráneas `<entity>_id`.
- Nombres explícitos para restricciones e índices mediante una convención de nombres en la metadata de SQLAlchemy, para que Alembic genere migraciones estables.

## Diseño

- Claves primarias estables.
- Claves foráneas para relaciones reales.
- Restricciones `NOT NULL`, `UNIQUE` y `CHECK` cuando sean útiles.
- Índices para columnas filtradas, buscadas o usadas en joins con frecuencia.
- Fechas y horas almacenadas en UTC; la conversión a hora local (America/Bogota) se hace al presentar los datos.
- Evitar guardar datos derivados cuando puedan calcularse de forma segura.

## Decisiones de modelo

- **Copropiedad única:** el modelo representa solo la Urbanización San Joaquín. No agregar tablas ni columnas de copropiedad (`property_id`, `tenant_id`).
- **Unidades:** el identificador de cada unidad es único en toda la base de datos (restricción `UNIQUE`).
- **Roles:** `ADMINISTRADOR` y `RESIDENTE`.
- **Sesiones:** se almacenan en una tabla propia (ver `security`).
- **Modelo financiero:**
  - Las cuotas/cargos mensuales son entidades del modelo, asociadas a una unidad y a un periodo.
  - Los pagos se asocian a la unidad o cargo que correspondan y conservan fecha, monto, estado y origen (registro manual del administrador o pago electrónico simulado internamente, HU-010). No existen columnas ni tablas para datos de tarjetas ni para identificadores de pasarelas externas.
  - **Los saldos no se almacenan**: se calculan a partir de cargos y pagos válidos. Guardar un saldo requiere una extensión de diseño aprobada.
  - Montos en `DECIMAL(12,2)`, moneda COP, con `CHECK` de monto positivo donde corresponda. Nunca `FLOAT` ni `DOUBLE`.
- **Auditoría:** la tabla `audit_logs` para acciones relevantes: quién (usuario), qué acción, sobre qué entidad e identificador, cuándo y un contexto mínimo sin datos sensibles.
- **Borrado lógico:** en entidades cuyo historial deba conservarse (usuarios/residentes, unidades, cargos, pagos), usar borrado lógico (p. ej., `deleted_at` o un estado) en lugar de `DELETE`. Las consultas por defecto excluyen los registros eliminados. Tener en cuenta el efecto del borrado lógico sobre las restricciones `UNIQUE`.

El detalle de columnas y tablas se diseña junto con cada HU. No crear tablas antes de que su HU esté en desarrollo.

## Migraciones

Antes de una migración:
1. identificar la HU y las tablas afectadas;
2. revisar claves y dependencias;
3. considerar los datos existentes;
4. revisar manualmente la migración autogenerada;
5. aplicarla en local;
6. verificar la reversión (`downgrade`) cuando sea viable.

No usar comandos destructivos automáticamente.
No borrar tablas o columnas sin autorización explícita.

## Integridad

La integridad se protege en el backend (servicios) y en la base de datos (restricciones), no en el frontend.

Ejemplos:
- correo de usuario único;
- unidad única dentro de la copropiedad;
- cargos y pagos con fechas, montos y estados válidos;
- relaciones residente–unidad con claves foráneas definidas.

## Datos de prueba

- Seeds con datos claramente ficticios.
- No introducir credenciales reales, tarjetas, tokens reales ni información personal real.
