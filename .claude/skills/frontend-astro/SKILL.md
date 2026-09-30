---
description: Guide frontend development in SoftPh-Academico using Astro 7 (SSR, @astrojs/node) and Tailwind CSS 4. Use when creating or reviewing pages, layouts, components, middleware, navigation, forms, client interactions, or frontend API calls.
---

# SoftPh-Académico — Frontend Astro

## Responsabilidad

El frontend presenta la interfaz y consume la API de FastAPI (`/api/v1`). No contiene lógica de negocio ni reglas de autorización.

## Configuración base

- Node.js 24 LTS y npm.
- Astro 7.x estable en modo SSR (`output: "server"`) con el adaptador `@astrojs/node`.
- Tailwind CSS 4.x mediante la integración oficial vigente de Astro (`astro add tailwind`). No usar `@astrojs/tailwind` ni `tailwind.config.js` al estilo de Tailwind 3.
- TypeScript en todo el código del frontend.
- Variables de entorno desde el **único `.env` de la raíz** (Vite `envDir` apuntando a la raíz); documentarlas en el `.env.example` de la raíz. Solo las variables `PUBLIC_` llegan al navegador y nunca contienen secretos.

## Estructura

```text
frontend/src/
├── pages/         # Rutas
├── layouts/       # Layouts compartidos
├── components/    # Componentes de presentación
├── lib/api/       # Cliente HTTP y servicios por recurso
└── middleware.ts  # Protección de páginas en el servidor
```

## Consumo de la API (mismo origen)

- Desde el navegador, llamar siempre con rutas relativas (`/api/v1/...`) y `credentials: "same-origin"`. No usar URLs absolutas hacia otro origen.
- En desarrollo, `/api/v1` se reenvía a FastAPI mediante `vite.server.proxy` en la configuración de Astro. En producción, el mismo origen lo garantiza el proxy inverso (ver `softph-architecture`); el código del frontend no cambia entre entornos.
- Desde el servidor de Astro (SSR, middleware), llamar a FastAPI mediante la URL interna definida en `.env`, reenviando la cookie de sesión del usuario. Estas llamadas son **solo de lectura**; las modificaciones se hacen desde el navegador contra `/api/v1`.
- No crear endpoints de Astro (`src/pages/api/`) que dupliquen o intermedien la API de FastAPI.
- Centralizar llamadas HTTP en `lib/api/`. Interpretar el formato de error estándar de la API (ver `backend-fastapi`) en un único lugar.
- En operaciones de modificación con sesión (POST, PUT, PATCH, DELETE), enviar el token CSRF en la cabecera definida por el backend. Login, activación y recuperación de contraseña no llevan token CSRF (ver `security`).

## Autenticación y páginas protegidas

- Existe una única página de login para todos los roles. Tras autenticarse, la redirección depende del rol que devuelve el backend.
- El middleware valida la sesión consultando al backend en cada petición a una página protegida; si no es válida, redirige al login.
- La restricción de páginas por rol en el middleware mejora la navegación, pero la autorización real la aplica el backend en cada endpoint.
- La sesión vive en una cookie HttpOnly gestionada por el backend. No leer ni guardar tokens de sesión en JavaScript, `localStorage` ni `sessionStorage`.

## Convenciones

- Nombres de archivos, componentes, variables y funciones en inglés; textos visibles para el usuario en español.
- Mantener componentes pequeños y reutilizables.
- Evitar lógica duplicada.
- Accesibilidad básica: etiquetas de formulario, foco visible, mensajes de error y navegación por teclado.

## Estilos

Tailwind CSS 4.x es el mecanismo principal de estilos, configurado desde CSS (`@import "tailwindcss"` y `@theme`) según la convención de Tailwind 4.

La interfaz debe ser:
- limpia;
- profesional;
- responsive desde móviles modernos;
- consistente en espaciado, tipografía, botones, formularios y estados.

No introducir tema oscuro salvo solicitud explícita.

## Formularios

- Validar en cliente solo el formato (requerido, tipo, longitud) para mejorar la experiencia; no replicar reglas de negocio.
- La validación del backend es la autoridad final.
- Mostrar errores claros y no técnicos, a partir del formato de error estándar.
- No enviar campos que el usuario no está autorizado a editar o que no necesita.

## Montos

- Los montos llegan desde la API como decimales serializados en texto; no hacer aritmética de negocio con ellos en el frontend.
- Mostrar montos en COP con `Intl.NumberFormat("es-CO", { style: "currency", currency: "COP" })`.

## No hacer

- No conectar directamente con MySQL.
- No usar las reglas de autorización del frontend como mecanismo de seguridad.
- No calcular saldos ni estados de cuenta en el frontend.
- No implementar pagos reales, no integrar pasarelas externas ni capturar datos de tarjeta; el pago de HU-010 es una simulación interna del backend.
- No habilitar llamadas entre orígenes (CORS) sin autorización.
