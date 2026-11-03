# Entrega de los objetivos 2 y 3

Artefactos generados a partir del código del commit `f543c74` (rama `main`). Ninguno modifica la aplicación.

| Archivo | Contenido |
|---|---|
| [arquitectura/componentes.puml](arquitectura/componentes.puml) | Diagrama de componentes UML: frontend Astro, backend FastAPI, MySQL, Alembic, SMTP y reportlab, con interfaces y protocolos |
| [arquitectura/despliegue.puml](arquitectura/despliegue.puml) | Diagrama de despliegue UML del entorno de desarrollo y pruebas, con puertos y protocolos |
| [arquitectura/capas.md](arquitectura/capas.md) | Tabla capa → carpetas y archivos → responsabilidad |
| [modelo-datos/modelo-datos-completo.puml](modelo-datos/modelo-datos-completo.puml) | Modelo lógico completo (16 tablas, pata de gallo), para el anexo |
| [modelo-datos/modelo-datos-usuarios-seguridad.puml](modelo-datos/modelo-datos-usuarios-seguridad.puml) | Área: usuarios y seguridad |
| [modelo-datos/modelo-datos-estructura-invitaciones.puml](modelo-datos/modelo-datos-estructura-invitaciones.puml) | Área: estructura residencial e invitaciones |
| [modelo-datos/modelo-datos-finanzas-comunicacion.puml](modelo-datos/modelo-datos-finanzas-comunicacion.puml) | Área: finanzas y comunicación |
| [correspondencia.md](correspondencia.md) | Anexo C: correspondencia de cada componente y tabla con el código y las migraciones |
| [implementacion.md](implementacion.md) | Matriz de implementación por funcionalidad |
| [guia-ejecucion.md](guia-ejecucion.md) | Guía de instalación, ejecución y pruebas, verificada de principio a fin |
| [demo.md](demo.md) y [demo-consultas.sql](demo-consultas.sql) | Guion de demostración de unos 10 minutos y sus consultas SQL, verificados |

## Cómo se generaron los modelos de datos

Los cuatro diagramas del modelo de datos se generaron leyendo `information_schema` de `softph_academico_test`, en la revisión de Alembic `d33ea6140186`, comprobada con `alembic check`. Columnas, tipos, nulos, claves y relaciones salen de la base, no se escribieron a mano. Solo la agrupación por áreas es una decisión editorial.

## Exportar los PNG (pendiente)

Los PNG **todavía no están generados**: en el equipo donde se prepararon estos archivos no hay Java ni PlantUML, y su descarga no se autorizó. Por la misma razón, la sintaxis de los `.puml` no se ha comprobado con un renderizador. Para generarlos a 300 dpi (`skinparam dpi 300` ya está en cada archivo):

```bash
sudo apt install plantuml          # instala también Java y Graphviz
cd docs/entrega-objetivos
plantuml -tpng arquitectura/*.puml modelo-datos/*.puml
```

Cada PNG queda junto a su `.puml`. Para que quepa en una página carta vertical (21,59 × 27,94 cm, unos 2550 × 3300 px a 300 dpi), revisa el ancho de cada imagen. Si el diagrama completo de datos resulta demasiado ancho, insértalo en el anexo con la página en horizontal o usa los tres diagramas por área en el cuerpo del informe.
