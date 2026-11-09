# Entrega de los objetivos 2 y 3

Artefactos generados a partir del código de la rama `main`. Ninguno modifica la aplicación. Cada diagrama tiene su fuente `.puml` y, al lado, su `.png` (300 ppp) y su `.svg`.

| Archivo | Contenido |
|---|---|
| [arquitectura/componentes-resumen.puml](arquitectura/componentes-resumen.puml) | Diagrama de componentes resumido (Figura 9 del informe): capas de presentación, API, lógica de negocio y persistencia, de arriba abajo, y componentes transversales a un lado; flechas solo hacia abajo o hacia los transversales, sin cruces, con su protocolo |
| [arquitectura/componentes.puml](arquitectura/componentes.puml) | Diagrama de componentes detallado, con la misma organización por capas y todos los módulos (anexo) |
| [arquitectura/despliegue.puml](arquitectura/despliegue.puml) | Diagrama de despliegue UML: un nodo «device» (el equipo de desarrollo) con seis «executionEnvironment» (navegador, Node.js, uvicorn, ejecutores de pruebas, MySQL y Mailpit), con puertos y protocolos |
| [arquitectura/capas.md](arquitectura/capas.md) | Tabla capa → carpetas y archivos → responsabilidad |
| [modelo-datos/modelo-datos-completo.puml](modelo-datos/modelo-datos-completo.puml) | Modelo lógico completo (16 tablas, pata de gallo) en una sola imagen, como referencia |
| [modelo-datos/modelo-datos-completo-parte1.puml](modelo-datos/modelo-datos-completo-parte1.puml) y [parte2](modelo-datos/modelo-datos-completo-parte2.puml) | El modelo completo dividido en dos partes legibles en una página carta (anexo): usuarios, seguridad, estructura e invitaciones (10 tablas); finanzas y comunicación (6 tablas) |
| [modelo-datos/modelo-datos-usuarios-seguridad.puml](modelo-datos/modelo-datos-usuarios-seguridad.puml) | Área: usuarios y seguridad |
| [modelo-datos/modelo-datos-estructura-invitaciones.puml](modelo-datos/modelo-datos-estructura-invitaciones.puml) | Área: estructura residencial e invitaciones |
| [modelo-datos/modelo-datos-finanzas-comunicacion.puml](modelo-datos/modelo-datos-finanzas-comunicacion.puml) | Área: finanzas y comunicación |
| [correspondencia.md](correspondencia.md) | Anexo C: correspondencia de cada componente y tabla con el código y las migraciones |
| [verificacion-arquitectura.md](verificacion-arquitectura.md) | Guion para la asesoría: archivo y líneas de cada componente y comunicación de la Figura 9, con los comandos para mostrarlo en vivo |
| `Anexos_SoftPh.docx` y `Anexos_SoftPh.pdf` | Anexos A a F del informe, generados a partir de estos archivos |
| [implementacion.md](implementacion.md) | Matriz de implementación por funcionalidad |
| [guia-ejecucion.md](guia-ejecucion.md) | Guía de instalación, ejecución y pruebas, verificada de principio a fin |
| [demo.md](demo.md) y [demo-consultas.sql](demo-consultas.sql) | Guion de demostración de unos 10 minutos y sus consultas SQL, verificados |

## Cómo se generaron los modelos de datos

Los diagramas del modelo de datos se generaron leyendo `information_schema` de `softph_academico_test`, en la revisión de Alembic `d33ea6140186`, comprobada con `alembic check`. Columnas, tipos, nulos, claves y relaciones salen de la base, no se escribieron a mano. Solo la agrupación por áreas es una decisión editorial. Las dos partes del modelo completo se obtienen dividiendo `modelo-datos-completo.puml`, sin reescribir columnas ni relaciones: la parte 1 tiene 11 relaciones y la parte 2, 13, y suman las 24 del modelo. En la parte 2, `users` y `units` aparecen en gris, solo con su clave primaria.

## Generar los PNG y los SVG

Los PNG se generan con PlantUML (verificado con la versión 1.2020.02 del paquete de Ubuntu/Linux Mint, que instala también Java y Graphviz):

```bash
sudo apt install plantuml
cd docs/entrega-objetivos
plantuml -DPLANTUML_LIMIT_SIZE=16384 -tpng arquitectura/*.puml modelo-datos/*.puml
plantuml -DPLANTUML_LIMIT_SIZE=16384 -tsvg arquitectura/*.puml modelo-datos/*.puml
```

Cada `.puml` fija `skinparam dpi 300`. PlantUML no escribe la resolución en el PNG, así que tras generarlos se le añadió el metadato de 300 ppp (con Pillow, sin recomprimir la imagen) para que Word los inserte a su tamaño real.

Cada PNG y cada SVG quedan junto a su `.puml`. Tras generarlos se comprobó que ninguno está recortado: el dibujo no toca ningún borde de la imagen y el título, todos los componentes y tablas, sus atributos y las etiquetas de las relaciones aparecen en el texto del SVG. Cada imagen se revisó además a la vista.

Los diagramas del modelo de datos usan líneas curvas. Con líneas ortogonales, PlantUML 1.2020.02 omite la etiqueta de la relación de una tabla consigo misma (`source_charge_id`, la mora) y separa otras etiquetas de su línea.

| PNG | Tamaño (px, 300 ppp) | Uso sugerido | Letra resultante* |
|---|---|---|---|
| `arquitectura/componentes-resumen.png` | 2268 × 3042 | Cuerpo del informe, carta vertical | 8,1 pt |
| `arquitectura/componentes.png` | 3787 × 4382 | Anexo, carta vertical | 4,7 pt |
| `arquitectura/despliegue.png` | 3050 × 2663 | Cuerpo o anexo | 5,8 pt |
| `modelo-datos/modelo-datos-completo-parte1.png` | 2340 × 3196 | Anexo, carta vertical | 7,0 pt |
| `modelo-datos/modelo-datos-completo-parte2.png` | 3050 × 2137 | Anexo, carta horizontal | 7,2 pt |
| `modelo-datos/modelo-datos-completo.png` | 6204 × 4028 | Referencia (no cabe legible en una página) | 3,8 pt |
| `modelo-datos/modelo-datos-usuarios-seguridad.png` | 2523 × 1493 | Cuerpo del informe | 7,0 pt |
| `modelo-datos/modelo-datos-estructura-invitaciones.png` | 1970 × 3340 | Cuerpo del informe | 6,6 pt |
| `modelo-datos/modelo-datos-finanzas-comunicacion.png` | 3385 × 2325 | Cuerpo del informe, carta horizontal | 6,6 pt |

\* Tamaño de la letra del diagrama al ajustarlo al área útil de una página carta con márgenes de 2,5 cm, en la orientación indicada (vertical: 16,6 × 20,9 cm; horizontal: 22,9 × 14,5 cm, descontando el título y la fuente de la figura).

**`left to right direction` en el modelo completo.** Se probó en el modelo completo y en sus dos partes, y no mejora la lectura. En una página horizontal, la letra quedaría en 2,5 pt en el modelo completo y en 4,7 y 4,9 pt en las partes 1 y 2. Por eso las dos partes se dibujan de arriba abajo: la parte 1 en página vertical y la parte 2 en página horizontal.

En PlantUML, `**` dentro de un texto se interpreta como negrita y desaparece de la imagen; por eso las rutas se escriben como `/admin/…` y no `/admin/**`.
