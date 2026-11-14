"""Prepara y comprueba los PNG que genera PlantUML para el informe.

PlantUML (skinparam dpi 300) no escribe la resolución en el PNG. Para cada archivo, este script:

1. lo deja en RGB sobre fondo blanco (sin canal alfa) y le añade el metadato de 300 ppp, para
   que Word lo inserte a su tamaño real (sin cambiar ningún píxel del dibujo);
2. comprueba que esté en escala de grises (ningún píxel con color: diferencia entre canales <= 8);
3. comprueba que no esté recortado. PlantUML recorta sin aviso por abajo y por la derecha lo que
   pasa de PLANTUML_LIMIT_SIZE: el dibujo no debe tocar los 2 píxeles del borde inferior ni del
   derecho, y ninguna dimensión debe llegar al límite (16384). Que toque el borde izquierdo o el
   superior no es un recorte (PlantUML deja ahí un margen de unos 3 píxeles; las pestañas del
   símbolo de componente llegan a él) y solo se informa.

Uso, con Pillow (por ejemplo, el entorno del backend):
    backend/.venv/bin/python docs/entrega-objetivos/scripts/finalizar_png.py docs/entrega-objetivos/*/*.png
Termina con código 1 si algún PNG no cumple.
"""

import sys

from PIL import Image

DPI = 300
failed = False
for path in sys.argv[1:]:
    image = Image.open(path)
    if image.mode != "RGB":
        background = Image.new("RGB", image.size, "white")
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[3])
        image = background
    width, height = image.size
    pixels = image.load()
    colored = sum(1 for y in range(0, height, 2) for x in range(0, width, 2)
                  if max(pixels[x, y]) - min(pixels[x, y]) > 8)
    def dark(points) -> int:
        return sum(1 for x, y in points if min(pixels[x, y]) < 200)

    cut = dark([(x, y) for x in range(width) for y in (height - 2, height - 1)]) + \
        dark([(x, y) for y in range(height) for x in (width - 2, width - 1)])
    near = dark([(x, y) for x in range(width) for y in (0, 1)]) + dark([(x, y) for y in range(height) for x in (0, 1)])
    cut += 1 if max(width, height) >= 16384 else 0
    image.save(path, dpi=(DPI, DPI), optimize=True)
    ok = colored == 0 and cut == 0
    failed |= not ok
    print(f"{'ok ' if ok else 'MAL'} {path.rsplit('/', 1)[-1]:46} {width:5d}×{height:<5d} px · "
          f"{width / DPI * 2.54:5.1f}×{height / DPI * 2.54:<5.1f} cm · color: {colored} · recorte: {cut}"
          + (f" · toca arriba/izquierda: {near}" if near else ""))
sys.exit(1 if failed else 0)
