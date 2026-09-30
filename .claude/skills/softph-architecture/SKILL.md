---
description: Define and enforce the architecture and development rules for SoftPh-Academico. Use when planning, creating, reviewing, or changing project structure, boundaries, dependencies, or cross-layer behavior.
---

# SoftPh-Académico — Arquitectura

## Identidad del proyecto

SoftPh-Académico es un proyecto académico independiente orientado a la gestión administrativa de una propiedad horizontal.

El prototipo modela **una única copropiedad: Urbanización San Joaquín, Pereira**. No se diseña para múltiples copropiedades (sin multi-tenancy ni identificadores de copropiedad en las tablas) salvo autorización explícita.

Este repositorio es independiente del SoftPh comercial. No reutilizar, copiar ni importar código, secretos, configuraciones, migraciones, datos o dependencias del proyecto comercial salvo autorización explícita del usuario.

## Stack definitivo

| Capa | Decisión |
|---|---|
| Runtime de Node | Node.js 24 LTS |
| Frontend | Astro 7.x estable en modo **SSR** (`output: "server"`) |
| Adaptador de Astro | `@astrojs/node` |
| Estilos | Tailwind CSS 4.x mediante la integración oficial vigente de Astro (`astro add tailwind`); no usar el paquete obsoleto `@astrojs/tailwind` |
| Lenguaje de interfaz | TypeScript |
| Backend | FastAPI sobre Python 3.13 |
| ORM | SQLAlchemy 2.x en modo **async** |
| Driver MySQL | `asyncmy` |
| Migraciones | Alembic (única fuente de verdad del esquema) |
| Base de datos | MySQL 8.4 LTS local, instalación nativa (sin Docker inicialmente) |
| Correo en desarrollo | Mailpit local |
| Gestor de paquetes Node | npm |
| Entorno Python | `venv` + `pip` |

No introducir otras herramientas de infraestructura (Docker, gestores alternativos, colas, cachés) sin autorización.

## Separación de responsabilidades

- Astro: páginas, layouts, componentes, presentación, navegación, protección de páginas en el servidor (middleware) y consumo de la API.
- FastAPI: API HTTP, autenticación, sesiones, autorización, validación, lógica de negocio, auditoría, envío de correo y acceso a datos.
- SQLAlchemy: persistencia y consultas desde el backend.
- MySQL: almacenamiento persistente.
- Tailwind CSS: estilos de interfaz.

Reglas:
- No colocar reglas de negocio en componentes visuales.
- No acceder directamente a MySQL desde Astro.
- No duplicar reglas de negocio entre frontend y backend. El frontend solo valida **formato** (campo requerido, tipo, longitud) para mejorar la experiencia; las reglas de negocio y de autorización viven únicamente en el backend.
- Ocultar opciones de la interfaz según el rol es solo una ayuda visual, nunca un control de seguridad.

## Estrategia de mismo origen

El navegador consume la interfaz y la API desde **un único origen**. No habilitar CORS; cualquier excepción requiere autorización explícita.

- La API se expone bajo el prefijo `/api/v1`.
- **Desarrollo:** el servidor de desarrollo de Astro reenvía `/api/v1` a FastAPI mediante el proxy de Vite (`vite.server.proxy`). El navegador solo conoce el origen de Astro.
- **Producción:** frontend y API se publican bajo el mismo origen; un proxy inverso delante de ambos servicios enruta `/api/v1` a FastAPI y el resto al servidor de Astro. Su configuración concreta se documenta al preparar el despliegue, sin cambiar este contrato.
- La cookie de sesión la emite FastAPI a través del proxy, sin atributo `Domain`, así que queda asociada al origen único.
- Las llamadas que Astro hace desde el servidor (SSR, middleware) van directamente a FastAPI mediante una URL interna configurada en `.env`, reenviando la cookie de sesión del usuario. Estas llamadas son **solo de lectura** (p. ej., validar la sesión o cargar datos de una página); toda operación de modificación la inicia el navegador contra `/api/v1`.

## Convenciones de idioma

- Código, identificadores, nombres de tablas y columnas, rutas de la API, variables de entorno, mensajes de commit y comentarios técnicos: **inglés**.
- Documentación académica (`docs/`) y textos visibles para el usuario final: **español**.
- Excepción: los valores de rol del dominio son `ADMINISTRADOR` y `RESIDENTE`, tal como figuran en el documento académico.

## Configuración

- Existe **un único `.env` en la raíz del repositorio** y un único `.env.example` junto a él. No crear archivos `.env` dentro de `frontend/` ni `backend/`.
- El backend lee el `.env` de la raíz desde su módulo de configuración; el frontend lo carga apuntando `envDir` de Vite a la raíz. Solo las variables con prefijo `PUBLIC_` llegan al navegador, y ninguna de ellas puede ser secreta.
- `.env` nunca se versiona; `.env.example` se versiona con todas las variables necesarias y valores ficticios o vacíos.
- Nunca hardcodear secretos, contraseñas, credenciales ni URLs dependientes del entorno.

## Estructura base esperada

```text
SoftPh-Academico/
├── frontend/          # Proyecto Astro (SSR)
├── backend/           # Proyecto FastAPI, incluye alembic/ y tests/
├── database/          # Scripts de preparación local (crear BD/usuario), diagramas y datos ficticios. Nunca DDL del esquema.
├── docs/              # Documentación académica en español (ver academic-traceability)
├── .claude/
│   └── skills/
├── .env.example
├── .gitignore
└── README.md
```

Alembic es la única fuente de verdad del esquema. No crear `database/schema.sql` ni scripts equivalentes que definan tablas en paralelo a las migraciones.

## Decisiones de dominio transversales

- Roles iniciales: `ADMINISTRADOR` y `RESIDENTE`.
- No existe registro público de administradores; el primer administrador se crea mediante un mecanismo controlado del backend (ver `security`).
- Existe una única interfaz de login; el backend determina el rol del usuario autenticado.
- Las cuotas/cargos mensuales forman parte del modelo financiero; los saldos se calculan a partir de cargos y pagos.
- Valores monetarios: `DECIMAL(12,2)`, moneda COP.
- El pago electrónico (HU-010) es una **simulación interna**; no se integra ninguna pasarela externa.
- Correo (Mailpit en local) únicamente para los flujos que definen los requisitos (ver `academic-traceability`).

## Reglas de desarrollo

1. Trabajar en incrementos pequeños y verificables.
2. Antes de implementar una funcionalidad, identificar HU, RF y CU relacionados.
3. No implementar funcionalidades fuera del alcance académico sin autorización.
4. No crear dependencias innecesarias; toda dependencia nueva debe justificarse.
5. No realizar cambios destructivos en la base de datos sin autorización explícita.
6. Nunca hardcodear secretos, contraseñas o credenciales.
7. Mantener configuración mediante variables de entorno.
8. Cada cambio funcional debe terminar con pruebas y build según la skill `testing`.
9. No crear commits automáticamente; el usuario decide cuándo hacer commit.
10. Si existe ambigüedad en requisitos, detener la implementación de esa parte y señalarla.

## Flujo obligatorio

```text
Requisito (HU/RF/CU) → diseño → implementación → verificación (según skill testing) → revisión → commit manual
```

La verificación sigue el orden definido en la skill `testing`.

## Pagos

El pago electrónico de HU-010 es una simulación interna del backend: no se conecta a ninguna pasarela externa (ni siquiera en modo sandbox), no procesa dinero real y no solicita ni almacena datos de tarjetas. Integrar una pasarela externa requiere una decisión nueva y explícita.

## Criterio de finalización

Una tarea no se considera terminada solo porque el código fue escrito. Debe existir evidencia de que la funcionalidad funciona y no rompe el sistema existente, según la definición de terminado de la skill `testing`.
