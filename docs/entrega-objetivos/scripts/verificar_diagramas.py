"""Verifica los SVG de PlantUML de los diagramas del informe: letra efectiva y cruces de líneas.

Letra efectiva (pt) = font-size del SVG × escala, con escala = min(ancho útil / ancho del SVG,
alto útil / alto del SVG): el SVG se dibuja en las mismas unidades que su font-size, así que la
cifra es el tamaño real de la letra impresa al ajustar el diagrama a la página. Área útil de una
página carta con márgenes de 2,5 cm, descontando el título y la fuente de la figura:
horizontal 22,9 × 14,5 cm; vertical 16,6 × 20,9 cm. Se informa la letra más pequeña del dibujo.

Cruces: cada <path> de relación (sin relleno) se muestrea (rectas y Bézier cúbicas) y se buscan
intersecciones entre pares de líneas distintas, sin contar las que están a menos de 40 unidades
del extremo de alguna de las dos (dos relaciones que llegan al mismo elemento se tocan ahí sin
cruzarse).

Uso: python3 -I docs/entrega-objetivos/scripts/verificar_diagramas.py archivo.svg [...]
"""

import re
import sys
from itertools import combinations
from xml.etree import ElementTree

CM = 72 / 2.54
AREAS = {"horizontal": (22.9 * CM, 14.5 * CM), "vertical": (16.6 * CM, 20.9 * CM)}
MARGIN = 40.0


def number(value: str) -> float:
    return float(re.match(r"[0-9.]+", value).group(0))


def sample(d: str) -> list[tuple[float, float]]:
    tokens = re.findall(r"[MLCmlc]|-?[0-9]*\.?[0-9]+(?:e-?[0-9]+)?", d)
    points: list[tuple[float, float]] = []
    i, command, current = 0, None, (0.0, 0.0)
    while i < len(tokens):
        if tokens[i] in "MLCmlc":
            command = tokens[i]
            i += 1
        elif command in ("M", "L"):
            current = (float(tokens[i]), float(tokens[i + 1]))
            points.append(current)
            i += 2
        elif command == "C":
            p1, p2, p3 = [(float(tokens[i + k]), float(tokens[i + k + 1])) for k in (0, 2, 4)]
            for n in range(1, 21):
                t = n / 20
                a, b, c, e = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
                points.append((a * current[0] + b * p1[0] + c * p2[0] + e * p3[0],
                               a * current[1] + b * p1[1] + c * p2[1] + e * p3[1]))
            current = p3
            i += 6
        else:
            i += 1
    return points


def intersection(p, q, r, s):
    d = (q[0] - p[0]) * (s[1] - r[1]) - (q[1] - p[1]) * (s[0] - r[0])
    if abs(d) < 1e-9:
        return None
    u = ((r[0] - p[0]) * (s[1] - r[1]) - (r[1] - p[1]) * (s[0] - r[0])) / d
    v = ((r[0] - p[0]) * (q[1] - p[1]) - (r[1] - p[1]) * (q[0] - p[0])) / d
    return (p[0] + u * (q[0] - p[0]), p[1] + u * (q[1] - p[1])) if 0 <= u <= 1 and 0 <= v <= 1 else None


def crossings(root) -> int:
    lines = [sample(el.get("d")) for el in root.iter()
             if el.tag.endswith("path") and el.get("fill") in ("none", None) and el.get("d")
             and ("stroke" in el.get("style", "") or el.get("stroke"))]
    lines = [line for line in lines if len(line) >= 2]
    found = 0
    for a, b in combinations(lines, 2):
        ends = (a[0], a[-1], b[0], b[-1])
        hit = False
        for k in range(len(a) - 1):
            for m in range(len(b) - 1):
                x = intersection(a[k], a[k + 1], b[m], b[m + 1])
                if x and all((x[0] - e[0]) ** 2 + (x[1] - e[1]) ** 2 >= MARGIN ** 2 for e in ends):
                    hit = True
                    break
            if hit:
                break
        found += hit
    return found


print(f"{'archivo':46} {'ancho×alto':>11}  {'horizontal':>10}  {'vertical':>8}  cruces")
for path in sys.argv[1:]:
    root = ElementTree.parse(path).getroot()
    width, height = number(root.get("width")), number(root.get("height"))
    smallest = min(float(t.get("font-size")) for t in root.iter() if t.tag.endswith("text") and t.get("font-size"))
    letters = [smallest * min(w / width, h / height) for w, h in AREAS.values()]
    print(f"{path.rsplit('/', 1)[-1]:46} {width:5.0f}×{height:<5.0f}  {letters[0]:7.1f} pt  {letters[1]:5.1f} pt  {crossings(root):3d}")
