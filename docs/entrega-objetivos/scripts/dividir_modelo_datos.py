"""Divide modelo-datos-completo.puml en dos partes para el Anexo B, con solo las columnas clave.

Cada parte se lee en una hoja carta horizontal (22,9 × 14,5 cm útiles) con letra de al menos 9 pt
(comprobado con verificar_diagramas.py). Para lograrlo:

- cada tabla muestra solo sus columnas clave (<<PK>>, <<FK>>, <<UK>>) del modelo completo (leído
  de information_schema), sin su tipo, y su encabezado indica cuántas columnas tiene: «users (11)»,
  sin contar las columnas generadas (que el modelo completo también omite);
- las 24 relaciones se copian con su cardinalidad (11 en la parte 1 y 13 en la parte 2); solo se
  rotulan con su columna FK cuando hay dos relaciones entre las mismas tablas, y la relación de la
  mora consigo misma (la columna FK ya aparece en la tabla). La mora se dibuja hacia un recuadro
  gris «charges: origen de la mora», porque PlantUML 1.2020 dibuja la relación de una tabla consigo
  misma como un bucle diminuto con las marcas de cardinalidad superpuestas;
- la disposición se fija por parte (orden de las tablas, relaciones en la misma fila y enlaces
  ocultos que solo colocan elementos): es una decisión editorial, no cambia ninguna relación.

El detalle de todas las columnas está en las Figuras 11 a 13 y en modelo-datos-completo.puml.

Uso, desde la raíz del repositorio:

    python3 -I docs/entrega-objetivos/scripts/dividir_modelo_datos.py docs/entrega-objetivos/modelo-datos
"""
import re
import sys
from collections import Counter
from pathlib import Path

d = Path(sys.argv[1])
src = (d / "modelo-datos-completo.puml").read_text()

HEADER = """@startuml
' Parte {n} de 2 del modelo lógico completo (Anexo B), generada por scripts/dividir_modelo_datos.py
' a partir de modelo-datos-completo.puml: solo las columnas clave (PK, FK, UK), sin su tipo, y
' todas las relaciones de sus tablas con su cardinalidad. Pensada para una hoja carta horizontal.
skinparam dpi 300
skinparam monochrome true
skinparam backgroundColor white
skinparam shadowing false
skinparam defaultFontName Helvetica
skinparam defaultFontSize 12
skinparam class {{
  BackgroundColor White
  BorderColor Black
  ArrowColor Black
}}
hide circle
hide methods
top to bottom direction
skinparam nodesep {nodesep}
skinparam ranksep 10
' Título (no se dibuja; lo pone el pie de figura del informe): {title}
"""

LEGEND = [
    "Solo columnas clave, sin su tipo",
    "(todas las columnas: Figuras 11 a 13).",
    "(N): columnas de la tabla, sin las generadas.",
    "MySQL 8 · Alembic d33ea6140186.",
    "● NOT NULL · <<PK>> primaria · <<FK>> foránea",
    "<<UK>> única · Etiqueta: la columna FK, cuando hay",
    "dos relaciones entre las mismas tablas.",
    "|| uno · |o cero o uno · o{ cero o muchos",
]
NBSP = "\u00a0"
GAP = '"      "'  # blank label: separates two tables of the same row so their cardinality marks do not touch

PARTS = {
    "parte1": {
        "title": "Modelo lógico de datos de SoftPh · Parte 1 de 2: usuarios, seguridad, estructura e invitaciones "
                 "(solo columnas clave)",
        "nodesep": 10,
        # Declaration order decides the left-to-right order of each row.
        "tables": ["login_throttles", "blocks", "towers", "units", "resident_invitations", "resident_units",
                   "users", "sessions", "password_reset_tokens", "audit_logs"],
        # Relation → how it is drawn: "-" same row (with GAP), "---" two rows down; default "--".
        "length": {("blocks", "towers"): "-", ("towers", "units"): "-", ("users", "audit_logs"): "---"},
        "hidden": ["password_reset_tokens -[hidden]-> leyenda", "resident_units -[hidden]-> login_throttles"],
        "legend_extra": [],
    },
    "parte2": {
        "title": "Modelo lógico de datos de SoftPh · Parte 2 de 2: finanzas y comunicación (solo columnas clave)",
        "nodesep": 6,
        "tables": ["units", "charges", "payment_allocations", "users", "payments", "demo_payment_transactions",
                   "feedback", "notifications"],
        "length": {},
        "hidden": ["payment_allocations -[hidden]> leyenda"],
        "legend_extra": ["En gris: tablas de la parte 1 (solo su PK) y charges", "como origen de su mora (la misma tabla)."],
    },
}
OWN = {"parte1": ["users", "sessions", "login_throttles", "password_reset_tokens", "audit_logs",
                  "blocks", "towers", "units", "resident_invitations", "resident_units"],
       "parte2": ["charges", "payments", "payment_allocations", "demo_payment_transactions",
                  "feedback", "notifications"]}

blocks = re.findall(r'entity "(\w+)" as \w+ \{\n(.*?)\n\}', src, re.S)
entities = {name: body.splitlines() for name, body in blocks}
assert sum(len(t) for t in OWN.values()) == 16 == len(entities)
RELATION = re.compile(r'^(\w+) ([|o}{]+)--([|o}{]+) (\w+) : "(.*)"$', re.M)
relations = RELATION.findall(src)
assert len(relations) == 24
pairs = Counter((a, b) for a, _, _, b, _ in relations)
KEY = re.compile(r"<<(PK|FK|UK|FK,UK)>>")


def keys_only(name: str) -> str:
    """The table with its key columns only, without their type; the heading gives its number of columns."""
    lines = entities[name]
    columns = [line for line in lines if re.match(r"^\s*\*?\w+ :", line)]
    kept = [re.sub(r" : [^<]+ <<", " <<", line) for line in lines if KEY.search(line)]
    body = [kept[0], "  --", *kept[1:]] if len(kept) > 1 else kept
    return f'entity "{name} ({len(columns)})" as {name} {{\n' + "\n".join(body) + "\n}\n"


total = 0
for n, (key, part) in enumerate(PARTS.items(), start=1):
    own = OWN[key]
    assert sorted(t for t in part["tables"] if t in own) == sorted(own)
    # Part 1: the relations among its tables; part 2: every relation that touches one of its tables
    # (the ones to users and units, which belong to part 1, too).
    if key == "parte1":
        used = [r for r in relations if r[0] in own and r[3] in own]
    else:
        used = [r for r in relations if r[0] in own or r[3] in own]
    stubs = {x for r in used for x in (r[0], r[3]) if x not in own}
    out = [HEADER.format(n=n, nodesep=part["nodesep"], title=part["title"])]
    self_related = {a for a, _, _, b, _ in used if a == b}
    for t in part["tables"]:
        if t in own:
            out.append(keys_only(t))
        elif t in stubs:
            out.append(f'entity "{t} (parte 1)" as {t} #DDDDDD {{\n  *id <<PK>>\n}}\n')
        if t in self_related:
            # The late fee and its origin. PlantUML 1.2020 draws a relation of a table with itself as a
            # tiny loop whose cardinality marks overlap, so its other end is a grey box of the same
            # table, declared right after it and drawn in the same row (horizontal relation).
            out.append(f'entity "{t}: origen\\nde la mora" as {t}_origen #DDDDDD {{\n  *id <<PK>>\n}}\n')
    for a, left, right, b, label in used:
        dashes = part["length"].get((a, b), "--")
        if a == b:
            # Only the column, with three no-break spaces on each side: the label then keeps clear of
            # the border of the table and of the marks of the relations that reach its right side.
            column = label.split(" (")[0]
            out.append(f'{a} {left}-{right} {a}_origen : "{NBSP * 3}{column}{NBSP * 3}"\n')
            continue
        if pairs[(a, b)] > 1:
            text = f' : "{label}"'
        elif dashes == "-":
            text = f" : {GAP}"
        else:
            text = ""
        out.append(f"{a} {left}{dashes}{right} {b}{text}\n")
    legend = LEGEND[:4] + part["legend_extra"] + LEGEND[4:]
    out.append('entity "Leyenda" as leyenda #FFFFFF {\n' + "".join(f"  {line}\n" for line in legend) + "}\n")
    out += [line + "\n" for line in part["hidden"]]
    out.append("@enduml\n")
    total += len(used)
    (d / f"modelo-datos-completo-{key}.puml").write_text("".join(out))
    print(key, len(own), "tablas;", len(used), "relaciones; en gris:", sorted(stubs))
print("relaciones totales:", total, "de", len(relations))
