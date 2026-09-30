# database/

Soporte para la base de datos local. **No define el esquema**: la única fuente
de verdad del esquema son las migraciones de Alembic en `backend/alembic/`.

Contenido permitido:

- `scripts/`: preparación del entorno local (crear bases de datos y usuario, sin tablas).
- Diagramas del modelo, derivados de las migraciones.
- Datos ficticios de ejemplo.

No crear aquí `schema.sql` ni scripts que definan tablas.
