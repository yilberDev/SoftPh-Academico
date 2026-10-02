---
description: Enforce security practices for SoftPh-Academico. Use when implementing or reviewing authentication, server-side sessions, CSRF, authorization, passwords, account tokens, forms, API endpoints, database access, cookies, audit, or user data handling.
---

# SoftPh-Académico — Seguridad

## Principios

Mínimo privilegio, defensa en profundidad, validación en el servidor y separación clara entre autenticación y autorización.

## Contraseñas

- Hash con **Argon2id**, usando una librería mantenida y los parámetros recomendados por ella.
- Volver a calcular el hash al iniciar sesión si los parámetros cambiaron.
- Nunca almacenar ni registrar contraseñas en texto plano.

## Autenticación

- **Una única ruta de login** para todos los roles. El backend determina el rol y lo devuelve en la respuesta.
- Mensajes de error genéricos: no revelar si una cuenta existe (login y recuperación de contraseña).
- Solo pueden iniciar sesión las cuentas activas (no eliminadas y con la activación completada).

## Sesiones (en servidor)

- Sesiones almacenadas en base de datos. La cookie solo contiene un identificador aleatorio criptográficamente seguro; en la base de datos se guarda su hash, no el valor en claro.
- Atributos de la cookie: `HttpOnly`, `SameSite=Lax`, `Path=/`; `Secure` configurable por variable de entorno (activado fuera del entorno local).
- Expiración configurable por variable de entorno.
- Generar una sesión nueva al iniciar sesión.
- Invalidar la sesión al cerrar sesión, e invalidar todas las sesiones del usuario al cambiar o restablecer la contraseña y al desactivar la cuenta.
- No usar JWT para la sesión del navegador.

## CSRF

Toda operación de modificación (POST, PUT, PATCH, DELETE) **con sesión** exige **ambas** comprobaciones:

1. **Origin:** la cabecera `Origin` debe coincidir con el origen público único (configurado en `.env`), que es el origen del frontend. No derivarlo de la cabecera `Host`, que el proxy puede reescribir. Si falta `Origin`, usar `Referer`; si faltan ambas, rechazar.
2. **Token CSRF:** un token asociado a la sesión, que el backend entrega al frontend y que este envía en una cabecera propia. Se compara en tiempo constante.

Las operaciones de modificación **anónimas** (login, activación de cuenta, solicitud y confirmación de recuperación de contraseña) no tienen sesión y, por tanto, tampoco token CSRF: exigen la comprobación de `Origin`, y las de activación y recuperación además exigen su token de un solo uso. El logout tiene sesión y exige ambas comprobaciones.

Un fallo de CSRF devuelve `403` con código `CSRF_FAILED` en el formato de error estándar.

Las llamadas servidor a servidor desde Astro (SSR, middleware) son solo de lectura y por eso no pasan por CSRF. Ningún endpoint de modificación puede eximirse de CSRF por venir de la URL interna.

## Autorización

- Roles iniciales: `ADMINISTRADOR` y `RESIDENTE`.
- El backend comprueba autenticación y rol en cada endpoint protegido mediante dependencias reutilizables.
- Un residente solo accede a sus propios datos (unidad, pagos, estado de cuenta, feedback). Verificar la propiedad del recurso, no solo el rol.
- Nunca confiar en controles de interfaz ni en un rol enviado por el cliente.

## Creación del primer administrador

- No existe registro público de administradores ni ningún endpoint anónimo que cree administradores.
- El primer administrador se crea con un comando controlado del backend (`app/cli.py`), ejecutado por quien opera el servidor. La contraseña se pide de forma interactiva; nunca se pasa como argumento visible ni se hardcodea.
- La creación queda registrada en auditoría.

## Tokens de un solo uso (invitación, activación, recuperación)

- Generados con un generador aleatorio criptográficamente seguro; en base de datos solo se guarda su hash.
- Con expiración y de un solo uso. Se invalidan al usarse o al emitirse uno nuevo del mismo tipo.
- Se envían únicamente por correo (Mailpit en desarrollo) y nunca aparecen en logs.
- En el enlace, el token va en el **fragmento** (`/ruta#token=…`), nunca en la query (`?token=`): el navegador no envía el fragmento al servidor. La API lo recibe solo en el cuerpo de un POST.
- La página que lo recibe (p. ej., `/activate-account`, HU-019) lo retira de la URL con `history.replaceState` al leerlo, lo mantiene solo en memoria (nunca en `localStorage`, `sessionStorage` ni cookies) y se sirve con `Cache-Control: no-store` y `Referrer-Policy: no-referrer`. Un token inexistente, alterado, mal formado o ya rotado recibe la misma respuesta (404), sin pistas.
- Solicitudes anónimas que envían correo (p. ej., recuperación de contraseña, HU-016): misma respuesta, código y cabeceras exista o no la cuenta; el correo se envía en segundo plano (después de responder) para que el tiempo de respuesta no delate la cuenta; el límite de abuso es silencioso (no 429) y por cuenta.
- El login bloquea la fila del usuario (por clave primaria) antes de verificar la contraseña, y cualquier cambio de contraseña bloquea la misma fila y revoca las sesiones: así ninguna sesión creada con la contraseña anterior sobrevive.

## Entrada y datos

- Validar y normalizar entradas en el servidor.
- Consultas mediante ORM o parametrizadas; nunca concatenar SQL con entradas del usuario.
- Limitar longitudes y formatos.
- No devolver al cliente campos internos (hashes, tokens, datos de otros usuarios).

## Mismo origen

No habilitar CORS: frontend y API comparten origen. Cualquier excepción requiere autorización explícita.

## Secretos

- Nunca incluir secretos en el repositorio.
- `.env` fuera de Git; `.env.example` sin valores reales.
- No registrar secretos, contraseñas ni tokens en logs ni en auditoría.

## Pagos

El pago electrónico (HU-010) es una simulación interna. No integrar pasarelas externas (ni en modo sandbox), no manejar credenciales de terceros, no solicitar ni almacenar datos de tarjetas y no procesar dinero real.

## Auditoría

Registrar en `audit_logs` las acciones relevantes, como mínimo:
- inicio de sesión (correcto y fallido), cierre de sesión;
- creación del administrador, invitación y activación de residentes;
- recuperación y cambio de contraseña;
- registro, anulación o modificación de pagos y cargos, incluidos los pagos simulados;
- creación, edición o borrado lógico de residentes y unidades.

Conservar lo necesario para reconstruir qué ocurrió, sin secretos ni datos sensibles innecesarios.
