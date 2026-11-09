"""Divide modelo-datos-completo.puml en dos partes sin reescribir columnas ni relaciones.

Uso, desde la raíz del repositorio:

    backend/.venv/bin/python docs/entrega-objetivos/scripts/dividir_modelo_datos.py docs/entrega-objetivos/modelo-datos
"""
import re
import sys
from pathlib import Path

d = Path(sys.argv[1])
src = (d / "modelo-datos-completo.puml").read_text()
header = src.split('entity "', 1)[0]
header = header.replace("skinparam linetype ortho\n", "")
header = header.replace("top to bottom direction",
                        "top to bottom direction\nskinparam nodesep 12\nskinparam ranksep 30")
header = header.replace("@startuml\n", "@startuml\n" + ("' Parte de modelo-datos-completo.puml, generada por división (mismas columnas y relaciones,\n"
          "' leídas de information_schema). Líneas curvas para que cada etiqueta quede junto a su relación.\n"
          ), 1)
header = re.sub(r"^title .*$", "", header, flags=re.M)

entities = dict(re.findall(r'(entity "(\w+)" as \w+ \{\n.*?\n\})', src, re.S)[i][::-1]
                for i in range(len(re.findall(r'entity "(\w+)"', src))))
rels = re.findall(r"^(\w+) [|o}{]+--[|o}{]+ (\w+) : .*$", src, re.M)
rel_lines = re.findall(r"^\w+ [|o}{]+--[|o}{]+ \w+ : .*$", src, re.M)
generated = re.findall(r"^  - (\w+)\.(\w+)$", src.split("note as generadas")[1].split("end note")[0], re.M)
legend = "legend bottom left" + src.split("legend bottom left")[1].split("endlegend")[0] + "endlegend\n"

PARTS = {
    "parte1": ("Parte 1: usuarios, seguridad, estructura residencial e invitaciones",
               ["users", "sessions", "login_throttles", "password_reset_tokens", "audit_logs",
                "blocks", "towers", "units", "resident_invitations", "resident_units"]),
    "parte2": ("Parte 2: finanzas y comunicación",
               ["charges", "payments", "payment_allocations", "demo_payment_transactions",
                "feedback", "notifications"]),
}
assert sum(len(t) for _, t in PARTS.values()) == 16 == len(entities)

for key, (subtitle, tables) in PARTS.items():
    out = [header.rstrip() + "\n"]
    out.append(f"title Modelo lógico de datos de SoftPh · {subtitle}\\n"
               "MySQL 8 (InnoDB) · esquema Alembic d33ea6140186 (head)\n")
    for t in tables:
        out.append(entities[t] + "\n")
    if key == "parte1":
        used = [(a, b, line) for (a, b), line in zip(rels, rel_lines) if a in tables and b in tables]
        incoming = [(a, b, re.search(r': "(.*)"', line).group(1))
                    for (a, b), line in zip(rels, rel_lines) if a in tables and b not in tables]
    else:
        used = [(a, b, line) for (a, b), line in zip(rels, rel_lines) if a in tables or b in tables]
        incoming = []
    stubs = sorted({x for a, b, _ in used for x in (a, b) if x not in tables})
    for t in stubs:
        out.append(f'entity "{t}" as {t} #EEEEEE {{\n  *id : bigint <<PK>>\n  --\n'
                   f"  .. (ver la parte 1) ..\n}}\n")
    for _, _, line in used:
        out.append(line + "\n")
    gen = [f"{t}.{c}" for t, c in generated if t in tables]
    if gen:
        out.append("note as generadas\n  Columnas generadas omitidas (unicidad parcial\n"
                   "  y enlace de la mora):\n" + "".join(f"  - {g}\n" for g in gen) + "end note\n")
    if incoming:
        out.append("note as externas\n  Referenciadas desde la parte 2:\n" + "".join(
            f"  - {b}.{c} → {a}\n" for a, b, c in sorted(incoming)) + "end note\n")
    if stubs:
        out.append("note as externas\n  En gris: tablas de la parte 1,\n"
                   "  solo con su clave primaria.\nend note\n")
    out.append(legend)
    out.append("@enduml\n")
    (d / f"modelo-datos-completo-{key}.puml").write_text("".join(out))
    print(key, len(tables), "tablas;", len(used), "relaciones; externas:", stubs, "; generadas:", len(gen))
print("relaciones totales:", len(rel_lines))
