---
description: Maintain academic traceability in SoftPh-Academico between user stories, functional requirements, use cases, implementation, and tests, and record approved design extensions in docs/. Use when planning or reviewing any academic requirement.
---

# SoftPh-Académico — Trazabilidad académica

## Cadena obligatoria

Cada funcionalidad de negocio debe poder rastrearse mediante:

```text
HU → RF → CU → implementación → prueba
```

## Documentación en `docs/`

La documentación académica se escribe en español y vive en `docs/`:

```text
docs/
├── historias-usuario/       # HU-001 … HU-019
├── requisitos-funcionales/  # RF-001 … RF-019
├── casos-uso/               # CU-001 … CU-019
├── matriz-trazabilidad.md   # HU ↔ RF ↔ CU ↔ código ↔ pruebas
└── extensiones-diseno.md    # Decisiones de diseño aprobadas aún no reflejadas en el documento académico
```

- **El documento académico original no se modifica.** `docs/` lo transcribe o referencia; cualquier diferencia se registra como extensión de diseño.
- La matriz se actualiza al completar cada HU, indicando los módulos de código y las pruebas que la cubren.

## Catálogo vigente

El proyecto trabaja con HU-001 a HU-019, RF-001 a RF-019 y CU-001 a CU-019, con correspondencia 1:1 según la matriz de trazabilidad aprobada.

No renumerar ni crear identificadores arbitrarios sin actualizar la matriz y la documentación asociada.

## Mapeo

- HU-001 → RF-001 → CU-001 — Registro de administrador
- HU-002 → RF-002 → CU-002 — Inicio de sesión del administrador
- HU-003 → RF-003 → CU-003 — Registro de un residente mediante invitación
- HU-004 → RF-004 → CU-004 — Registro de pagos de residentes
- HU-005 → RF-005 → CU-005 — Revisar y gestionar feedback de residentes
- HU-006 → RF-006 → CU-006 — Visualización de residentes registrados
- HU-007 → RF-007 → CU-007 — Inicio de sesión del residente
- HU-008 → RF-008 → CU-008 — Visualización de pagos registrados por el residente
- HU-009 → RF-009 → CU-009 — Realización de feedback por el residente
- HU-010 → RF-010 → CU-010 — Realización de pagos por el residente
- HU-011 → RF-011 → CU-011 — Estado de cuenta del residente para administrador
- HU-012 → RF-012 → CU-012 — Reportes financieros
- HU-013 → RF-013 → CU-013 — Gestión de unidades residenciales
- HU-014 → RF-014 → CU-014 — Estado de cuenta propio del residente
- HU-015 → RF-015 → CU-015 — Gestión de información del residente
- HU-016 → RF-016 → CU-016 — Restablecimiento de contraseña del residente
- HU-017 → RF-017 → CU-017 — Notificaciones al residente
- HU-018 → RF-018 → CU-018 — Control de acceso según rol
- HU-019 → RF-019 → CU-019 — Invitación y activación de cuenta del residente

## Extensiones de diseño aprobadas

Estas decisiones interpretan o precisan el documento académico y deben registrarse en `docs/extensiones-diseno.md`:

| HU afectadas | Decisión |
|---|---|
| HU-001 | No hay registro público de administradores; el primer administrador se crea con un mecanismo controlado del backend. |
| HU-002, HU-007, HU-018 | Una única interfaz de login; el backend determina el rol (`ADMINISTRADOR` / `RESIDENTE`). Ambas HU se cumplen con el mismo flujo. |
| HU-003, HU-019, HU-007 | HU-003 = invitación (administrador); HU-019 = activación de la cuenta (residente, sin crear sesión); HU-007 = inicio de sesión del residente con el login único. Los criterios 1–3 de HU-019 se cumplen con HU-003 y su criterio 6.2 con HU-007. La recuperación de contraseña de residentes es HU-016 (cubre el criterio 3 de HU-007); la del administrador (criterio 3 de HU-002) no está cubierta. |
| HU-004, HU-008, HU-011, HU-012, HU-014 | Las cuotas/cargos mensuales forman parte del modelo financiero; los saldos se calculan a partir de cargos y pagos. |
| HU-010 | La pasarela de pagos de demostración de HU-010 (transcrita en `HU-010.md`, `RF-010.md` y `CU-010.md`) se implementa como una **simulación interna** del backend, sin pasarela ni proveedor externo: un checkout independiente (`/resident/demo-payments/checkout?transaction=<id>`) donde el residente elige un instrumento de prueba ficticio del catálogo del backend, y el backend determina la respuesta simulada. Implementación técnica y aceptación académica se registran por separado: el criterio 2.2 (la pasarela retorna el resultado) sigue **pendiente de validación académica** y no se declara cerrado. |
| HU-017 y demás HU con notificaciones | Correo solo en los flujos definidos por los requisitos; Mailpit en desarrollo. La lista exacta de correos sale de las HU/RF/CU al documentarlas en `docs/`. |
| HU-013 | Una única copropiedad (Urbanización San Joaquín) con estructura Bloque → Torre → Apartamento; la unidad residencial es el apartamento (ver `docs/extensiones-diseno.md`). |

## Reglas

- No implementar una HU sin identificar su RF y CU.
- No cambiar el comportamiento funcional sin comprobar su impacto en HU/RF/CU.
- Al completar una HU, actualizar la matriz con el código y las pruebas que la cubren.
- Mantener en la documentación los nombres del documento académico; en el código, los equivalentes técnicos en inglés.
- Registrar en `docs/extensiones-diseno.md` toda nueva decisión aprobada que no esté explícita en el documento académico.
