"""Genera los diagramas del modelo de datos por área (Figuras 11 a 13) a partir de modelo-datos-completo.puml.

Cada diagrama muestra sus tablas con todas las columnas y sus tipos, copiadas tal cual del modelo
completo (leído de information_schema). Cada relación se dibuja en el diagrama de la tabla que tiene
la clave foránea; si la tabla referenciada es de otra parte o de otra figura, se repite en gris, solo
con su clave primaria, como referencia. Así, las 24 relaciones del modelo se dibujan una vez cada una.
La mora (charges.source_charge_id) va hacia un recuadro gris «charges: origen de la mora», con la
etiqueta «source_charge_id», igual que en el Anexo B: PlantUML 1.2020 dibuja la relación de una tabla
consigo misma como un bucle con las marcas superpuestas.
Dos datos se calculan del modelo: las columnas de otras partes o figuras que apuntan a las tablas
del diagrama («Referencias») y las columnas generadas que el modelo omite. Van en la leyenda o en
notas sueltas, según lo que dé mayor letra en cada figura (clave «notes» de FIGURES).

Cada figura se lee a 8 pt o más en una hoja carta (comprobado con verificar_diagramas.py); la
disposición de cada una (orientación, letra en px de PlantUML, nodesep y ranksep) se fija en FIGURES;
con «split», una tabla gris se repite sobre cada tabla que la referencia (para evitar cruces).

Uso, desde la raíz del repositorio:
    python3 -I docs/entrega-objetivos/scripts/generar_areas.py docs/entrega-objetivos/modelo-datos
Opciones para probar disposiciones (escriben en el directorio indicado, sin tocar FIGURES):
    --only NOMBRE --font N --direction tb|lr --nodesep N --ranksep N
"""
import argparse
import re
from pathlib import Path

FIGURES = {
    "modelo-datos-usuarios-seguridad": {
        "title": "Modelo lógico de datos: usuarios y seguridad",
        "tables": ["users", "sessions", "login_throttles", "password_reset_tokens", "audit_logs"],
        "layout": ("tb", 16, 12, 15),
        "notes": "notas",
    },
    "modelo-datos-estructura-invitaciones-parte1": {
        "title": "Modelo lógico de datos: estructura residencial e invitaciones (1 de 2): estructura",
        "tables": ["blocks", "towers", "units"],
        "layout": ("lr", 16, 12, 15),
        "notes": "leyenda",
    },
    "modelo-datos-estructura-invitaciones-parte2": {
        "title": "Modelo lógico de datos: estructura residencial e invitaciones (2 de 2): invitaciones y residencias",
        "tables": ["resident_invitations", "resident_units"],
        "layout": ("lr", 16, 12, 15),
        "notes": "leyenda",
    },
    "modelo-datos-finanzas-comunicacion-parte1": {
        "title": "Modelo lógico de datos: finanzas y comunicación (1 de 2): obligaciones y pagos",
        "tables": ["charges", "payments"],
        "layout": ("tb", 16, 12, 45),
        "notes": "leyenda",
        # units is repeated (one grey box over charges, another over payments): with a single box the
        # relations to payments met at one corner, with overlapping marks, and one of them crossed.
        "split": ["units"],
    },
    "modelo-datos-finanzas-comunicacion-parte2": {
        "title": "Modelo lógico de datos: finanzas y comunicación (2 de 2): aplicaciones, pagos de demostración y comunicación",
        "tables": ["payment_allocations", "demo_payment_transactions", "feedback", "notifications"],
        "layout": ("tb", 12, 12, 15),
        "notes": "leyenda",
    },
}

HEADER = """@startuml
' {name}: generado por scripts/generar_areas.py a partir de modelo-datos-completo.puml
' (columnas, tipos, nulos, claves y relaciones leídos de information_schema; no se editan a mano).
skinparam dpi 300
skinparam monochrome true
skinparam backgroundColor white
skinparam shadowing false
skinparam defaultFontName Helvetica
skinparam defaultFontSize {font}
skinparam class {{
  BackgroundColor White
  BorderColor Black
  ArrowColor Black
}}
hide circle
hide methods
{direction}
skinparam nodesep {nodesep}
skinparam ranksep {ranksep}
' Título (no se dibuja; lo pone el pie de figura del informe): {title}
"""
NBSP = "\u00a0"
LEGEND = [
    "MySQL 8 (InnoDB) · esquema Alembic d33ea6140186 (head)",
    "* = NOT NULL · <<PK>> clave primaria · <<FK>> clave foránea · <<UK>> única",
    "Pata de gallo: || uno · |o cero o uno · o{ cero o muchos · o| cero o uno",
    "En gris: tablas de otra parte o figura, solo con su clave primaria.",
]

parser = argparse.ArgumentParser()
parser.add_argument("directory")
parser.add_argument("--only")
parser.add_argument("--font", type=int)
parser.add_argument("--direction", choices=("tb", "lr"))
parser.add_argument("--nodesep", type=int)
parser.add_argument("--ranksep", type=int)
parser.add_argument("--out")
args = parser.parse_args()

d = Path(args.directory)
src = (d / "modelo-datos-completo.puml").read_text()
entities = dict(re.findall(r'entity "(\w+)" as \w+ \{\n(.*?)\n\}', src, re.S))
relations = re.findall(r'^((\w+) [|o}{]+--[|o}{]+ (\w+) : "(.*)")$', src, re.M)
generated = re.findall(r"^  - (\w+)\.(\w+)$", src.split("note as generadas")[1].split("end note")[0], re.M)
assert len(entities) == 16 and len(relations) == 24
placed = {t for f in FIGURES.values() for t in f["tables"]}
assert placed == set(entities), set(entities) - placed

drawn = []
for name, figure in FIGURES.items():
    if args.only and name != args.only:
        continue
    direction, font, nodesep, ranksep = figure["layout"]
    direction = args.direction or direction
    font, nodesep, ranksep = args.font or font, args.nodesep or nodesep, args.ranksep or ranksep
    own = figure["tables"]
    # A relation is drawn in the figure of the table that holds the foreign key (the child, right
    # side); the ones from tables of other figures are listed in the note «Referencias».
    used = [r for r in relations if r[2] in own]
    stubs = sorted({t for _, a, b, _ in used for t in (a, b) if t not in own})
    out = [HEADER.format(name=name, font=font, nodesep=nodesep, ranksep=ranksep, title=figure["title"],
                         direction="left to right direction" if direction == "lr" else "top to bottom direction")]
    for t in own:
        out.append(f'entity "{t}" as {t} {{\n{entities[t]}\n}}\n')
    split = figure.get("split", [])
    for t in stubs:
        children = sorted({b for _, a, b, _ in used if a == t}) if t in split else [None]
        for child in children:
            alias = f"{t}_{child}" if child else t
            out.append(f'entity "{t}" as {alias} #DDDDDD {{\n  *id : bigint <<PK>>\n}}\n')
    for line, a, b, label in used:
        if a == b:  # the late fee and its origin, drawn towards a grey box of the same table
            # The same name and label as in Annex B (dividir_modelo_datos.py). Same row (one dash), so the
            # grey box sits beside the table; the label is the column alone, with three no-break spaces
            # on each side to keep it clear of the border of the table.
            out.append(f'entity "{a}: origen\\nde la mora" as {a}_origen #DDDDDD {{\n  *id : bigint <<PK>>\n}}\n')
            left, right = re.match(r"^\w+ (\S+?)--(\S+) ", line).groups()
            out.append(f'{a} {left}-{right} {a}_origen : "{NBSP * 3}{label.split(" (")[0]}{NBSP * 3}"\n')
        elif a in split:
            out.append(line.replace(f"{a} ", f"{a}_{b} ", 1) + "\n")
        else:
            out.append(line + "\n")
    drawn += [r[0] for r in used]
    # «Referencias» and «Columnas generadas» go into the legend (text under the drawing) or, for
    # «notas», into free notes: whichever gives the larger print size for that figure.
    legend = LEGEND if stubs or any(a == b for _, a, b, _ in used) else LEGEND[:3]
    legend = list(legend)
    for t in split:
        legend.append(f"{t} se repite en gris sobre cada tabla que la referencia, para que sus relaciones no se crucen.")
    incoming = sorted(f"{b}.{label.split(' ')[0]} → {a}" for _, a, b, label in relations
                      if a in own and b not in own and a != b)
    gen = [f"{t}.{c}" for t, c in generated if t in own]
    if figure["notes"] == "notas":
        if incoming:
            out.append("note as externas\n  Referencias desde otras partes o figuras:\n"
                       + "".join(f"  - {line}\n" for line in incoming) + "end note\n")
        if gen:
            out.append("note as generadas\n  Columnas generadas omitidas:\n"
                       + "".join(f"  - {g}\n" for g in gen) + "end note\n")
    else:
        for start in range(0, len(incoming), 2):
            prefix = "Referencias desde otras partes o figuras: " if start == 0 else "    "
            legend.append(prefix + " · ".join(incoming[start:start + 2]))
        for start in range(0, len(gen), 2):
            prefix = "Columnas generadas omitidas: " if start == 0 else "    "
            legend.append(prefix + " · ".join(gen[start:start + 2]))
    out.append("legend bottom left\n" + "".join(f"  {line}\n" for line in legend) + "endlegend\n@enduml\n")
    target = Path(args.out) if args.out else d
    (target / f"{name}.puml").write_text("".join(out))
    print(f"{name}: {len(own)} tablas, {len(used)} relaciones, en gris: {stubs}")
if not args.only:
    assert sorted(drawn) == sorted(r[0] for r in relations), "every relation is drawn exactly once"
    print("relaciones dibujadas:", len(drawn), "de", len(relations))
