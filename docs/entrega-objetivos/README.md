# Entrega de los objetivos 2 y 3

Artefactos generados a partir del código de la rama `main`. Ninguno modifica la aplicación. Cada diagrama tiene su fuente `.puml` y su `.png` al lado.

| Archivo | Contenido |
|---|---|
| [arquitectura/componentes-resumen.puml](arquitectura/componentes-resumen.puml) | Diagrama de componentes resumido: solo los componentes principales y sus comunicaciones, para una página carta (cuerpo del informe) |
| [arquitectura/componentes.puml](arquitectura/componentes.puml) | Diagrama de componentes UML completo: frontend Astro, backend FastAPI, MySQL, Alembic, SMTP y reportlab, con interfaces y protocolos (anexo) |
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

## Generar los PNG

Los PNG se generan con PlantUML (verificado con la versión 1.2020.02 del paquete de Ubuntu/Linux Mint, que instala también Java y Graphviz):

```bash
sudo apt install plantuml
cd docs/entrega-objetivos
plantuml -DPLANTUML_LIMIT_SIZE=16384 -tpng arquitectura/*.puml modelo-datos/*.puml
```

Cada PNG queda junto a su `.puml`. Tras generarlos se comprobó que ninguno está recortado: el dibujo no toca ningún borde de la imagen y el título, todos los componentes y tablas, sus atributos y las etiquetas de las relaciones aparecen en el texto renderizado.

| PNG | Tamaño (px) | Uso sugerido |
|---|---|---|
| `arquitectura/componentes-resumen.png` | 1929 × 2647 (200 dpi) | Cuerpo del informe: cabe en carta vertical |
| `arquitectura/componentes.png` | 7517 × 4684 | Anexo, en página horizontal |
| `arquitectura/despliegue.png` | 2784 × 2522 | Cuerpo o anexo |
| `modelo-datos/modelo-datos-completo.png` | 6168 × 4028 | Anexo, en página horizontal |
| `modelo-datos/modelo-datos-usuarios-seguridad.png` | 2523 × 1493 | Cuerpo del informe |
| `modelo-datos/modelo-datos-estructura-invitaciones.png` | 1970 × 3340 | Cuerpo del informe |
| `modelo-datos/modelo-datos-finanzas-comunicacion.png` | 3442 × 2325 | Cuerpo del informe |

En PlantUML, `**` dentro de un texto se interpreta como negrita y desaparece de la imagen; por eso las rutas se escriben como `/admin/…` y no `/admin/**`.
