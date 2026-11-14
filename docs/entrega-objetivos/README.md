# Entrega de los objetivos 2 y 3

Artefactos generados a partir del código de la rama `main`. Ninguno modifica la aplicación. Cada diagrama tiene su fuente `.puml` y, al lado, su `.png` (300 ppp, fondo blanco) y su `.svg`. Todos se dibujan en blanco y negro o escala de grises (`skinparam monochrome true`) y sin título: el título lo pone el pie de figura del informe (cada `.puml` lo conserva como comentario).

## Diagramas y figura del informe a la que corresponden

| Archivo | Figura del informe | Contenido |
|---|---|---|
| [casos-uso/cu-01-autenticacion-acceso.puml](casos-uso/cu-01-autenticacion-acceso.puml) | Figura 2 | Casos de uso: autenticación y control de acceso (CU-001, 002, 007, 016, 018) |
| [casos-uso/cu-02-residentes-unidades.puml](casos-uso/cu-02-residentes-unidades.puml) | Figura 3 | Casos de uso: gestión de residentes y unidades (CU-003, 006, 013, 015, 019) |
| [casos-uso/cu-03-pagos-estado-financiero.puml](casos-uso/cu-03-pagos-estado-financiero.puml) | Figura 4 | Casos de uso: gestión de pagos y estado financiero (CU-004, 008, 010, 011, 012, 014) |
| [casos-uso/cu-04-feedback.puml](casos-uso/cu-04-feedback.puml) | Figura 5 | Casos de uso: gestión de feedback (CU-005, 009) |
| [casos-uso/cu-05-notificaciones.puml](casos-uso/cu-05-notificaciones.puml) | Figura 6 | Casos de uso: notificaciones (CU-017 y los casos que la incluyen o extienden) |
| [actividades/actividades-administrador-parte1.puml](actividades/actividades-administrador-parte1.puml) y [parte2](actividades/actividades-administrador-parte2.puml) | Figura 7 (DA-01), en dos partes | Actividades del administrador: 1) acceso y navegación por el panel; 2) operación en un módulo, con sus validaciones (incluida la confirmación «¿Confirma?», cuyo [no] cancela sin cambios) y su resultado |
| [actividades/actividades-residente-parte1.puml](actividades/actividades-residente-parte1.puml) y [parte2](actividades/actividades-residente-parte2.puml) | Figura 8 (DA-02), en dos partes | Actividades del residente: 1) activación, acceso y navegación; 2) pago de demostración, feedback y consultas |
| [arquitectura/componentes-resumen.puml](arquitectura/componentes-resumen.puml) | Figura 9 | Diagrama de componentes resumido: capas Presentación, API, Lógica de negocio y Persistencia (los nombres de la Tabla 59), de arriba abajo, y Transversales a un lado; nombres y etiquetas cortos |
| [arquitectura/despliegue.puml](arquitectura/despliegue.puml) | Figura 10 | Diagrama de despliegue UML: un nodo «device» (el equipo de desarrollo) con seis «executionEnvironment», cada uno con su nombre y su puerto; protocolo y puerto en cada relación (los detalles, en la sección 9.1 del informe). Navegador arriba; Astro y FastAPI en el medio; MySQL y Mailpit abajo; las pruebas a un lado, a la izquierda de MySQL. Sin cruces y sin etiquetas atravesadas |
| [modelo-datos/modelo-datos-usuarios-seguridad.puml](modelo-datos/modelo-datos-usuarios-seguridad.puml) | Figura 11 | Modelo lógico, área de usuarios y seguridad: `users`, `sessions`, `login_throttles`, `password_reset_tokens` y `audit_logs`, con todas las columnas y sus tipos |
| [modelo-datos/modelo-datos-estructura-invitaciones-parte1.puml](modelo-datos/modelo-datos-estructura-invitaciones-parte1.puml) y [parte2](modelo-datos/modelo-datos-estructura-invitaciones-parte2.puml) | Figura 12, en dos partes | Área de estructura residencial e invitaciones: 1) `blocks`, `towers` y `units`; 2) `resident_invitations` y `resident_units` (con `units` y `users` en gris). Todas las columnas y sus tipos |
| [modelo-datos/modelo-datos-finanzas-comunicacion-parte1.puml](modelo-datos/modelo-datos-finanzas-comunicacion-parte1.puml) y [parte2](modelo-datos/modelo-datos-finanzas-comunicacion-parte2.puml) | Figura 13, en dos partes | Área de finanzas y comunicación: 1) `charges` y `payments` (con `users` en gris y `units` en gris repetida sobre cada una, para que sus relaciones no se crucen); 2) `payment_allocations`, `demo_payment_transactions`, `feedback` y `notifications` (con `charges`, `payments` y `users` en gris). Todas las columnas y sus tipos |
| [arquitectura/componentes-parte1.puml](arquitectura/componentes-parte1.puml) y [parte2](arquitectura/componentes-parte2.puml) | Figura B.1 (Anexo B), ahora en dos partes | Componentes detallados: 1) capa de presentación; 2) API, lógica de negocio, persistencia y transversales. Textos abreviados para leerse a ≥ 9 pt en una hoja horizontal |
| [modelo-datos/modelo-datos-completo-parte1.puml](modelo-datos/modelo-datos-completo-parte1.puml) y [parte2](modelo-datos/modelo-datos-completo-parte2.puml) | Figuras B.2 y B.3 (Anexo B) | Las 16 tablas y las 24 relaciones en dos partes, solo con las columnas clave (PK, FK, UK), legibles a ≥ 9 pt en una hoja horizontal |
| [arquitectura/componentes.puml](arquitectura/componentes.puml) | Referencia (antes, Figura B.1) | Componentes detallados en una sola imagen, con todos los módulos; no se lee en una página |
| [modelo-datos/modelo-datos-completo.puml](modelo-datos/modelo-datos-completo.puml) | Referencia | Modelo lógico completo (16 tablas, todas las columnas) en una sola imagen; fuente de las dos partes |

## Otros documentos

| Archivo | Contenido |
|---|---|
| [arquitectura/capas.md](arquitectura/capas.md) | Tabla capa → carpetas y archivos → responsabilidad |
| [correspondencia.md](correspondencia.md) | Anexo C: correspondencia de cada componente y tabla con el código y las migraciones |
| [verificacion-arquitectura.md](verificacion-arquitectura.md) | Guion para la asesoría: archivo y líneas de cada componente y comunicación de la Figura 9, con los comandos para mostrarlo en vivo |
| `Anexos_SoftPh.docx` y `Anexos_SoftPh.pdf` | Anexos A a F generados por `scripts/generar_anexos.py` el 2026-11-04. No se regeneran: el Anexo B se integra directamente en el informe con las figuras de esta carpeta, así que su Figura B.1 y las partes del modelo son las anteriores |
| [implementacion.md](implementacion.md) | Matriz de implementación por funcionalidad |
| [guia-ejecucion.md](guia-ejecucion.md) | Guía de instalación, ejecución y pruebas, verificada de principio a fin |
| [demo.md](demo.md) y [demo-consultas.sql](demo-consultas.sql) | Guion de demostración de unos 10 minutos y sus consultas SQL, verificados |

## De dónde sale cada diagrama

- **Casos de uso (Figuras 2 a 6).** Nombres y actores de las fichas oficiales (`docs/casos-uso/`); los mismos cinco módulos que los diagramas D01 a D05 del documento académico. Los `<<include>>` de CU-002 y CU-007 hacia CU-018 vienen de D01; las demás relaciones se apoyan en la ficha y en el código, citado en el comentario de cada `.puml`: `<<extend>>` de CU-016 a CU-007, de CU-015 a CU-006, de CU-011 a CU-012 y de CU-017 a CU-010 (solo un pago de demostración aprobado notifica), e `<<include>>` de CU-004 y de CU-005 a CU-017 (todo pago manual y toda respuesta o resolución de un feedback notifican; HU-017, criterio 1).
- **Actividades (Figuras 7 y 8).** Cada figura se divide en dos partes para leerse a 8 pt o más en una página vertical: la parte 1 termina en «operar en el módulo (parte 2)» y la parte 2 empieza ahí y vuelve a la parte 1. Flujos tomados de `frontend/src/middleware.ts`, `lib/auth/page-access.ts`, `lib/auth/roles.ts`, `lib/auth/login-form.ts` y las páginas de `/admin` y `/resident`, más los servicios del backend que deciden cada resultado (citados en el comentario de cada `.puml`). Cada decisión tiene sus guardas `[sí]` y `[no]`.
- **Modelos de datos.** Se generaron leyendo `information_schema` de `softph_academico_test`, en la revisión de Alembic `d33ea6140186`, comprobada con `alembic check`. Columnas, tipos, nulos, claves y relaciones salen de la base, no se escribieron a mano; solo la agrupación por áreas es una decisión editorial. Las dos partes del Anexo B las genera `scripts/dividir_modelo_datos.py` a partir de `modelo-datos-completo.puml`: cada tabla muestra solo sus columnas clave, sin su tipo, y su encabezado indica cuántas columnas tiene (`users (11)`, sin las columnas generadas). Las 24 relaciones se copian con su cardinalidad (11 en la parte 1 y 13 en la parte 2). En la parte 2, `users` y `units` aparecen en gris, solo con su clave primaria. La mora (`charges.source_charge_id`) se dibuja hacia un recuadro gris de la misma tabla (el mismo en el Anexo B y en la Figura 13: «charges: origen de la mora», con la etiqueta «source_charge_id»): PlantUML 1.2020 dibuja la relación de una tabla consigo misma como un bucle diminuto con las marcas de cardinalidad superpuestas. Su etiqueta es solo la columna, con un margen de espacios no separables, para que no toque el borde de `charges` ni las marcas de las relaciones que llegan a ella. Las Figuras 11 a 13 las genera `scripts/generar_areas.py` a partir del mismo `modelo-datos-completo.puml`, con todas las columnas y sus tipos: cada relación se dibuja en la figura de la tabla que tiene la clave foránea (las 24, una vez cada una), y la tabla referenciada, si es de otra parte o figura, se repite en gris solo con su clave primaria. Cada figura indica las columnas de otras partes o figuras que apuntan a sus tablas («Referencias») y las columnas generadas que el modelo omite.

## Generar los PNG y los SVG

Con PlantUML (verificado con la versión 1.2020.02 del paquete de Ubuntu/Linux Mint, que instala también Java y Graphviz):

```bash
sudo apt install plantuml
cd docs/entrega-objetivos
python3 -I scripts/dividir_modelo_datos.py modelo-datos          # Anexo B; solo si cambia modelo-datos-completo.puml
python3 -I scripts/generar_areas.py modelo-datos                  # Figuras 11 a 13; ídem
DIAGRAMAS="arquitectura/*.puml modelo-datos/*.puml actividades/*.puml casos-uso/*.puml"
plantuml -DPLANTUML_LIMIT_SIZE=16384 -tsvg $DIAGRAMAS
plantuml -DPLANTUML_LIMIT_SIZE=16384 -tpng $DIAGRAMAS
../../backend/.venv/bin/python scripts/finalizar_png.py */*.png    # 300 ppp, RGB, grises, sin recorte
python3 -I scripts/verificar_diagramas.py */*.svg                  # letra, cruces y etiquetas (-v: detalle)
```

- Sin `-DPLANTUML_LIMIT_SIZE=16384`, PlantUML recorta sin aviso a 4096 píxeles los diagramas más grandes (los de actividades, por ejemplo).
- Cada `.puml` fija `skinparam dpi 300`. PlantUML no escribe la resolución en el PNG: `finalizar_png.py` la añade, deja la imagen en RGB sobre fondo blanco, comprueba que no tenga ningún píxel de color y que el dibujo no toque el borde inferior ni el derecho (donde PlantUML recorta). Termina con error si algo falla.
- `verificar_diagramas.py` calcula, a partir del SVG, la letra más pequeña al ajustar el diagrama a una página carta; cuenta los cruces entre las líneas de las relaciones; las etiquetas atravesadas por una línea, y los textos que tocan el borde de una caja (a medias dentro y fuera, o fuera a menos de un sexto de su altura).
- Donde Graphviz centra una etiqueta sobre su propia línea (las relaciones verticales cortas de los casos de uso), la etiqueta lleva espacios no separables a un lado, que desplazan el texto fuera de la línea; el verificador descuenta ese relleno.

## Resultado de la verificación (2026-11-13, sin títulos)

| Diagrama | Figura | PNG (px, 300 ppp) | Letra mínima, carta horizontal | Letra mínima, carta vertical | Cruces | Etiquetas atravesadas | Texto sobre borde |
|---|---|---|---|---|---|---|---|
| `cu-01-autenticacion-acceso` | 2 | 1940 × 1343 | **12,4 pt** | 9,8 pt | 0 | 0 | 0 |
| `cu-02-residentes-unidades` | 3 | 1375 × 1659 | 10,0 pt | **13,9 pt** | 0 | 0 | 0 |
| `cu-03-pagos-estado-financiero` | 4 | 1450 × 2006 | 8,3 pt | **12,0 pt** | 0 | 0 | 0 |
| `cu-04-feedback` | 5 | 1115 × 729 | **22,8 pt** | 17,1 pt | 0 | 0 | 0 |
| `cu-05-notificaciones` | 6 | 2025 × 803 | **13,0 pt** | 9,4 pt | 0 | 0 | 0 |
| `actividades-administrador-parte1` | 7 (1/2) | 2165 × 4472 | 5,7 pt | **8,3 pt** | 0 | 0 | 0 |
| `actividades-administrador-parte2` | 7 (2/2) | 3465 × 4856 | 5,8 pt | **8,4 pt** | 0 | 0 | 0 |
| `actividades-residente-parte1` | 8 (1/2) | 3234 × 5291 | 5,8 pt | **8,4 pt** | 0 | 0 | 0 |
| `actividades-residente-parte2` | 8 (2/2) | 3978 × 3726 | 8,3 pt | **8,9 pt** | 0 | 0 | 0 |
| `componentes-resumen` | 9 | 1943 × 3418 | 6,0 pt | **8,7 pt** | 0 | 0 | 0 |
| `despliegue` | 10 | 2284 × 1859 | **9,7 pt** | 9,0 pt | 0 | 0 | 0 |
| `modelo-datos-usuarios-seguridad` | 11 | 3973 × 2063 | **8,2 pt** | 5,9 pt | 0 | 0 | 0 |
| `modelo-datos-estructura-invitaciones-parte1` | 12 (1/2) | 2540 × 1245 | **12,8 pt** | 9,2 pt | 0 | 0 | 0 |
| `modelo-datos-estructura-invitaciones-parte2` | 12 (2/2) | 2943 × 1946 | **10,6 pt** | 8,0 pt | 0 | 0 | 0 |
| `modelo-datos-finanzas-comunicacion-parte1` | 13 (1/2) | 3581 × 2472 | **8,3 pt** | 6,6 pt | 0 | 0 | 0 |
| `modelo-datos-finanzas-comunicacion-parte2` | 13 (2/2) | 2540 × 1410 | **9,6 pt** | 6,9 pt | 0 | 0 | 0 |
| `componentes-parte1` | B.1 (1/2) | 1984 × 1649 | **9,3 pt** | 8,9 pt | 0 | 0 | 0 |
| `componentes-parte2` | B.1 (2/2) | 2540 × 1609 | **9,6 pt** | 6,9 pt | 0 | 0 | 0 |
| `modelo-datos-completo-parte1` | B.2 | 2693 × 1614 | **9,0 pt** | 6,5 pt | 0 | 0 | 0 |
| `modelo-datos-completo-parte2` | B.3 | 2607 × 1330 | **9,3 pt** | 6,8 pt | 1 | 0 | 0 |
| `componentes` | referencia | 3787 × 4343 | 2,7 pt | 3,5 pt | 0 | 3 | 0 |
| `modelo-datos-completo` | referencia | 6204 × 3951 | 2,9 pt | 2,1 pt | 11 | 3 | 0 |

En negrita, la orientación prevista para cada figura; en los casos de uso, que caben a 8 pt o más en las dos, la que da mayor letra. Los dos diagramas de referencia no son figuras del informe y no se ajustaron.

- **Letra mínima:** tamaño real de la letra más pequeña del diagrama al ajustarlo al área útil de una página carta con márgenes de 2,5 cm (horizontal: 22,9 × 14,5 cm; vertical: 16,6 × 20,9 cm, descontando el título y la fuente de la figura). La versión anterior de esta tabla tomaba el tamaño de letra de PlantUML como si fueran puntos; PlantUML mide en píxeles a 96 ppp (12 px = 9 pt), así que aquellas cifras eran un tercio más altas que la letra impresa.
- **El cruce de la Figura B.3** es inevitable: `users` y `units` se relacionan las dos con `charges` y con `payments`. Ese par de tablas, en la misma fila, siempre produce un cruce; evitarlo exige repetir una tabla en el dibujo, que es lo que se hizo en la parte 1 de la Figura 13 (`units` en gris sobre `charges` y sobre `payments`), donde además las cuatro relaciones que llegan a `payments` desde arriba quedan separadas.
- **Tamaño de letra de PlantUML:** varía entre diagramas (de 12 a 24 px) porque la separación entre filas de PlantUML es fija: una letra mayor gana espacio frente a esa separación. Lo que importa es la letra impresa, que es la que mide la tabla.

En PlantUML, `**` dentro de un texto se interpreta como negrita y desaparece de la imagen; por eso las rutas se escriben como `/admin/…` y no `/admin/**`.
