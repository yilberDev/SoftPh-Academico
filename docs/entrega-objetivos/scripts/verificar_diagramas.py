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

Etiquetas cruzadas: textos cuyo recuadro atraviesa una línea (relación o flecha, <path> o <line>).
Texto sobre borde: textos que tocan el borde de una caja (<rect>): quedan a medias dentro y fuera, o
fuera pero a menos de un sexto de su altura del borde (pegados a él).
El recuadro de un texto se toma de su x, y, textLength y font-size, sin el relleno de espacios no
separables; se reduce 2 unidades por lado para no contar los textos que solo rozan una línea.

Uso: python3 -I docs/entrega-objetivos/scripts/verificar_diagramas.py archivo.svg [...]
Con -v, lista los textos de las dos últimas columnas.
"""

import re
import sys
from itertools import combinations
from xml.etree import ElementTree

CM = 72 / 2.54
AREAS = {"horizontal": (22.9 * CM, 14.5 * CM), "vertical": (16.6 * CM, 20.9 * CM)}
MARGIN = 40.0
NBSP = "\u00a0"


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


def segments(root) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Every straight piece of every line: sampled <path> without fill and <line>."""
    out = []
    for el in root.iter():
        if el.tag.endswith("path") and el.get("fill") in ("none", None) and el.get("d") \
                and ("stroke" in el.get("style", "") or el.get("stroke")):
            pts = sample(el.get("d"))
            out += list(zip(pts, pts[1:]))
        elif el.tag.endswith("line"):
            out.append(((float(el.get("x1")), float(el.get("y1"))), (float(el.get("x2")), float(el.get("y2")))))
    return out


def texts(root) -> list[tuple[str, tuple[float, float, float, float], float]]:
    out = []
    for el in root.iter():
        if el.tag.endswith("text") and el.get("textLength") and (el.text or "").strip():
            x, y, length, size = (float(el.get(k)) for k in ("x", "y", "textLength", "font-size"))
            # No-break spaces used as padding (to move a label off its line) are not ink: about 0.278 em each.
            lead = len(el.text) - len(el.text.lstrip(NBSP))
            trail = len(el.text) - len(el.text.rstrip(NBSP))
            x, length = x + lead * 0.278 * size, length - (lead + trail) * 0.278 * size
            out.append((el.text.strip(), (x + 2, y - 0.75 * size + 2, x + length - 2, y + 0.2 * size - 2), size))
    return out


def rects(root) -> list[tuple[float, float, float, float]]:
    return [(float(el.get("x")), float(el.get("y")), float(el.get("x")) + float(el.get("width")),
             float(el.get("y")) + float(el.get("height")))
            for el in root.iter() if el.tag.endswith("rect") and el.get("width") and float(el.get("width")) > 0]


def inside(p, box) -> bool:
    return box[0] < p[0] < box[2] and box[1] < p[1] < box[3]


def segment_hits_box(a, b, box) -> bool:
    if inside(a, box) or inside(b, box):
        return True
    x0, y0, x1, y1 = box
    edges = (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0)))
    return any(intersection(a, b, c, d) for c, d in edges)


def crossed_labels(root) -> list[str]:
    segs = segments(root)
    return [t for t, box, _ in texts(root) if box[2] > box[0] and any(segment_hits_box(a, b, box) for a, b in segs)]


def labels_on_borders(root) -> list[str]:
    boxes = rects(root)
    found = []
    for t, (a, b, c, d), size in texts(root):
        gap = size / 6 - 2  # the text box was already reduced by 2 units per side
        for x0, y0, x1, y1 in boxes:
            contained = a >= x0 and c <= x1 and b >= y0 and d <= y1
            near = a < x1 + gap and c > x0 - gap and b < y1 + gap and d > y0 - gap
            if near and not contained:
                found.append(t)
                break
    return found


verbose = "-v" in sys.argv
print(f"{'archivo':46} {'ancho×alto':>11}  {'horizontal':>10}  {'vertical':>8}  cruces  etiquetas cruzadas  texto sobre borde")
for path in [a for a in sys.argv[1:] if a != "-v"]:
    root = ElementTree.parse(path).getroot()
    width, height = number(root.get("width")), number(root.get("height"))
    smallest = min(float(t.get("font-size")) for t in root.iter() if t.tag.endswith("text") and t.get("font-size"))
    letters = [smallest * min(w / width, h / height) for w, h in AREAS.values()]
    crossed, borders = crossed_labels(root), labels_on_borders(root)
    print(f"{path.rsplit('/', 1)[-1]:46} {width:5.0f}×{height:<5.0f}  {letters[0]:7.1f} pt  {letters[1]:5.1f} pt"
          f"  {crossings(root):5d}  {len(crossed):17d}  {len(borders):16d}")
    if verbose:
        for t in crossed:
            print(f"    cruzada: {t}")
        for t in borders:
            print(f"    borde:   {t}")
