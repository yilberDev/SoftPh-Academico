# Entrega de los objetivos 2 y 3

Artefactos generados a partir del código de la rama `main`. Ninguno modifica la aplicación. Cada diagrama tiene su fuente `.puml` y, al lado, su `.png` (300 ppp, fondo blanco) y su `.svg`. Todos se dibujan en blanco y negro o escala de grises (`skinparam monochrome true`).

## Diagramas y figura del informe a la que corresponden

| Archivo | Figura del informe | Contenido |
|---|---|---|
| [casos-uso/cu-01-autenticacion-acceso.puml](casos-uso/cu-01-autenticacion-acceso.puml) | Figura 2 | Casos de uso: autenticación y control de acceso (CU-001, 002, 007, 016, 018) |
| [casos-uso/cu-02-residentes-unidades.puml](casos-uso/cu-02-residentes-unidades.puml) | Figura 3 | Casos de uso: gestión de residentes y unidades (CU-003, 006, 013, 015, 019) |
| [casos-uso/cu-03-pagos-estado-financiero.puml](casos-uso/cu-03-pagos-estado-financiero.puml) | Figura 4 | Casos de uso: gestión de pagos y estado financiero (CU-004, 008, 010, 011, 012, 014) |
| [casos-uso/cu-04-feedback.puml](casos-uso/cu-04-feedback.puml) | Figura 5 | Casos de uso: gestión de feedback (CU-005, 009) |
| [casos-uso/cu-05-notificaciones.puml](casos-uso/cu-05-notificaciones.puml) | Figura 6 | Casos de uso: notificaciones (CU-017 y los casos que la incluyen o extienden) |
| [actividades/actividades-administrador-parte1.puml](actividades/actividades-administrador-parte1.puml) y [parte2](actividades/actividades-administrador-parte2.puml) | Figura 7 (DA-01), en dos partes | Actividades del administrador: 1) acceso y navegación por el panel; 2) operación en un módulo, con sus validaciones y su resultado |
| [actividades/actividades-residente-parte1.puml](actividades/actividades-residente-parte1.puml) y [parte2](actividades/actividades-residente-parte2.puml) | Figura 8 (DA-02), en dos partes | Actividades del residente: 1) activación, acceso y navegación; 2) pago de demostración, feedback y consultas |
| [arquitectura/componentes-resumen.puml](arquitectura/componentes-resumen.puml) | Figura 9 | Diagrama de componentes resumido: capas de presentación, API, lógica de negocio y persistencia, de arriba abajo, y componentes transversales a un lado; nombres y etiquetas cortos |
| [arquitectura/despliegue.puml](arquitectura/despliegue.puml) | Figura 10 | Diagrama de despliegue UML: un nodo «device» (el equipo de desarrollo) con seis «executionEnvironment», cada uno con su nombre y su puerto; sin cruces y con protocolo y puerto en cada relación (los detalles, en la sección 9.1 del informe) |
| [modelo-datos/modelo-datos-usuarios-seguridad.puml](modelo-datos/modelo-datos-usuarios-seguridad.puml) | Figura 11 | Modelo lógico, área de usuarios y seguridad (todas las columnas) |
| [modelo-datos/modelo-datos-estructura-invitaciones.puml](modelo-datos/modelo-datos-estructura-invitaciones.puml) | Figura 12 | Modelo lógico, área de estructura residencial e invitaciones (todas las columnas) |
| [modelo-datos/modelo-datos-finanzas-comunicacion.puml](modelo-datos/modelo-datos-finanzas-comunicacion.puml) | Figura 13 | Modelo lógico, área de finanzas y comunicación (todas las columnas) |
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
- **Modelos de datos.** Se generaron leyendo `information_schema` de `softph_academico_test`, en la revisión de Alembic `d33ea6140186`, comprobada con `alembic check`. Columnas, tipos, nulos, claves y relaciones salen de la base, no se escribieron a mano; solo la agrupación por áreas es una decisión editorial. Las dos partes del Anexo B las genera `scripts/dividir_modelo_datos.py` a partir de `modelo-datos-completo.puml`: cada tabla muestra solo sus columnas clave, sin su tipo, y su encabezado indica cuántas columnas tiene (`users (11)`, sin las columnas generadas). Las 24 relaciones se copian con su cardinalidad (11 en la parte 1 y 13 en la parte 2). En la parte 2, `users` y `units` aparecen en gris, solo con su clave primaria. La mora (`charges.source_charge_id`) se dibuja hacia un recuadro gris «charges (la misma tabla)»: PlantUML 1.2020 dibuja la relación de una tabla consigo misma como un bucle diminuto con las marcas de cardinalidad superpuestas.

## Generar los PNG y los SVG

Con PlantUML (verificado con la versión 1.2020.02 del paquete de Ubuntu/Linux Mint, que instala también Java y Graphviz):

```bash
sudo apt install plantuml
cd docs/entrega-objetivos
python3 -I scripts/dividir_modelo_datos.py modelo-datos          # solo si cambia modelo-datos-completo.puml
DIAGRAMAS="arquitectura/*.puml modelo-datos/*.puml actividades/*.puml casos-uso/*.puml"
plantuml -DPLANTUML_LIMIT_SIZE=16384 -tsvg $DIAGRAMAS
plantuml -DPLANTUML_LIMIT_SIZE=16384 -tpng $DIAGRAMAS
../../backend/.venv/bin/python scripts/finalizar_png.py */*.png    # 300 ppp, RGB, grises, sin recorte
python3 -I scripts/verificar_diagramas.py */*.svg                  # letra efectiva y cruces
```

- Sin `-DPLANTUML_LIMIT_SIZE=16384`, PlantUML recorta sin aviso a 4096 píxeles los diagramas más grandes (los de actividades, por ejemplo).
- Cada `.puml` fija `skinparam dpi 300`. PlantUML no escribe la resolución en el PNG: `finalizar_png.py` la añade, deja la imagen en RGB sobre fondo blanco, comprueba que no tenga ningún píxel de color y que el dibujo no toque el borde inferior ni el derecho (donde PlantUML recorta). Termina con error si algo falla.
- `verificar_diagramas.py` calcula, a partir del SVG, la letra más pequeña al ajustar el diagrama a una página carta, y cuenta los cruces entre las líneas de las relaciones.

## Resultado de la verificación (2026-11-13)

| Diagrama | Figura | PNG (px, 300 ppp) | Letra mínima, carta horizontal | Letra mínima, carta vertical | Cruces |
|---|---|---|---|---|---|
| `cu-01-autenticacion-acceso` | 2 | 1940 × 1399 | 11,9 pt | 9,8 pt | 0 |
| `cu-02-residentes-unidades` | 3 | 1375 × 1714 | 9,7 pt | 13,9 pt | 0 |
| `cu-03-pagos-estado-financiero` | 4 | 1450 × 2061 | 8,1 pt | 11,7 pt | 0 |
| `cu-04-feedback` | 5 | 1115 × 784 | 21,2 pt | 17,1 pt | 0 |
| `cu-05-notificaciones` | 6 | 2025 × 858 | 13,0 pt | 9,4 pt | 0 |
| `actividades-administrador-parte1` | 7 (1/2) | 2393 × 4557 | 5,6 pt | **8,1 pt** | 0 |
| `actividades-administrador-parte2` | 7 (2/2) | 3612 × 4508 | 5,7 pt | **8,1 pt** | 0 |
| `actividades-residente-parte1` | 8 (1/2) | 3615 × 6405 | 5,6 pt | **8,1 pt** | 0 |
| `actividades-residente-parte2` | 8 (2/2) | 3978 × 3931 | 7,8 pt | **8,9 pt** | 0 |
| `componentes-resumen` | 9 | 1943 × 3486 | 5,9 pt | **8,5 pt** | 0 |
| `despliegue` | 10 | 2578 × 2000 | **9,0 pt** | 8,0 pt | 0 |
| `modelo-datos-usuarios-seguridad` | 11 | 2523 × 1493 | 7,2 pt | 5,2 pt | 0 |
| `modelo-datos-estructura-invitaciones` | 12 | 1970 × 3340 | 3,5 pt | 5,0 pt | 0 |
| `modelo-datos-finanzas-comunicacion` | 13 | 3385 × 2325 | 5,0 pt | 3,9 pt | 1 |
| `componentes-parte1` | B.1 (1/2) | 1984 × 1700 | **9,1 pt** | 8,9 pt | 0 |
| `componentes-parte2` | B.1 (2/2) | 2540 × 1660 | **9,3 pt** | 6,9 pt | 0 |
| `modelo-datos-completo-parte1` | B.2 | 2693 × 1665 | **9,0 pt** | 6,5 pt | 0 |
| `modelo-datos-completo-parte2` | B.3 | 2710 × 1381 | **9,0 pt** | 6,5 pt | 1 |
| `componentes` | referencia | 3787 × 4382 | 2,6 pt | 3,5 pt | 0 |
| `modelo-datos-completo` | referencia | 6204 × 4028 | 2,9 pt | 2,1 pt | 11 |

En negrita, la orientación prevista para cada figura. Las Figuras 11 a 13 no se rehicieron (solo pasaron a blanco y negro).

- **Letra mínima:** tamaño real de la letra más pequeña del diagrama al ajustarlo al área útil de una página carta con márgenes de 2,5 cm (horizontal: 22,9 × 14,5 cm; vertical: 16,6 × 20,9 cm, descontando el título y la fuente de la figura). La versión anterior de esta tabla tomaba el tamaño de letra de PlantUML como si fueran puntos; PlantUML mide en píxeles a 96 ppp (12 px = 9 pt), así que aquellas cifras eran un tercio más altas que la letra impresa.
- **El cruce de la parte 2 del modelo** es inevitable: `users` y `units` se relacionan las dos con `charges` y con `payments`. Ese par de tablas, en la misma fila, siempre produce un cruce; evitarlo exigiría repetir una tabla en el dibujo.
- **Tamaño de letra de PlantUML:** varía entre diagramas (de 12 a 28 px) porque la separación entre filas de PlantUML es fija: una letra mayor gana espacio frente a esa separación. Lo que importa es la letra impresa, que es la que mide la tabla.

En PlantUML, `**` dentro de un texto se interpreta como negrita y desaparece de la imagen; por eso las rutas se escriben como `/admin/…` y no `/admin/**`.
