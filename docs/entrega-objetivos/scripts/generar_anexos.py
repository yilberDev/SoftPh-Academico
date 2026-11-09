"""Genera docs/entrega-objetivos/Anexos_SoftPh.docx (anexos A a F del informe).

Uso, desde la raíz del repositorio (python-docx y Pillow en el entorno del backend):

    backend/.venv/bin/pip install python-docx
    backend/.venv/bin/python docs/entrega-objetivos/scripts/generar_anexos.py .

Contenido tomado de: matriz-pruebas-rf.md (A), los PNG de arquitectura/ y modelo-datos/ (B),
correspondencia.md (C), docs/capturas/ (D) y guia-ejecucion.md, secciones 1 a 9 (F).
Los títulos de los anexos usan estilos propios, no los «Título N» de Word (que suelen estar
numerados en la plantilla del informe); los títulos de las figuras usan el estilo integrado
«Caption», que Word en español muestra como «Descripción».
"""
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
ENT = ROOT / "docs/entrega-objetivos"
CAP = ROOT / "docs/capturas"
OUT = ENT / "Anexos_SoftPh.docx"

PAGE_W, PAGE_H, MARGIN = 21.59, 27.94, 2.5
ZWSP = "​"

doc = Document()


# ---------- estilos ----------
def rfonts(rpr, name):
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.insert(0, rf)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rf.set(qn(a), name)
    for a in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
        if rf.get(qn(a)) is not None:
            del rf.attrib[qn(a)]


normal = doc.styles["Normal"]
normal.font.name = "Arial"
normal.font.size = Pt(12)
rfonts(normal.element.get_or_add_rPr(), "Arial")
lang = OxmlElement("w:lang")
lang.set(qn("w:val"), "es-CO")
lang.set(qn("w:eastAsia"), "es-CO")
normal.element.get_or_add_rPr().append(lang)
pf = normal.paragraph_format
pf.line_spacing = 1.15
pf.space_before = Pt(0)
pf.space_after = Pt(6)
pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def paragraph_style(name, *, bold, align, before, after, outline=None, size=12, italic=False):
    st = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    st.base_style = normal
    st.font.name = "Arial"
    st.font.size = Pt(size)
    st.font.bold = bold
    st.font.italic = italic
    st.font.color.rgb = RGBColor(0, 0, 0)
    rfonts(st.element.get_or_add_rPr(), "Arial")
    f = st.paragraph_format
    f.alignment = align
    f.space_before = Pt(before)
    f.space_after = Pt(after)
    f.keep_with_next = True
    f.line_spacing = 1.15
    if outline is not None:  # nivel de esquema (tabla de contenido) sin numeración
        lvl = OxmlElement("w:outlineLvl")
        lvl.set(qn("w:val"), str(outline))
        st.element.get_or_add_pPr().append(lvl)
    return st


paragraph_style("Título de anexo", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, before=0, after=18, outline=0)
SUB = {
    2: paragraph_style("Subtítulo de anexo", bold=True, align=WD_ALIGN_PARAGRAPH.LEFT, before=12, after=6),
    3: paragraph_style("Subtítulo de anexo 2", bold=True, align=WD_ALIGN_PARAGRAPH.LEFT, before=10, after=6),
}

caption = doc.styles["Caption"]  # «Descripción» en Word en español
caption.font.name = "Arial"
caption.font.size = Pt(12)
caption.font.bold = False
caption.font.italic = False
caption.font.color.rgb = RGBColor(0, 0, 0)
rfonts(caption.element.get_or_add_rPr(), "Arial")
caption.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
caption.paragraph_format.space_before = Pt(0)
caption.paragraph_format.space_after = Pt(4)
caption.paragraph_format.keep_with_next = True
caption.paragraph_format.line_spacing = 1.15
for tag in ("w:semiHidden", "w:unhideWhenUsed"):
    el = caption.element.find(qn(tag))
    if el is not None:
        caption.element.remove(el)


# ---------- secciones ----------
def setup_section(sec, landscape):
    if landscape:
        sec.orientation = WD_ORIENT.LANDSCAPE
        sec.page_width, sec.page_height = Cm(PAGE_H), Cm(PAGE_W)
    else:
        sec.orientation = WD_ORIENT.PORTRAIT
        sec.page_width, sec.page_height = Cm(PAGE_W), Cm(PAGE_H)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Cm(MARGIN))
    sec.header_distance = Cm(1.25)
    sec.footer_distance = Cm(1.25)


first_section = [True]


def new_section(landscape):
    if first_section[0]:
        sec = doc.sections[0]
        first_section[0] = False
    else:
        sec = doc.add_section(WD_SECTION.NEW_PAGE)
    setup_section(sec, landscape)
    return sec


def usable_width(landscape):
    return (PAGE_H if landscape else PAGE_W) - 2 * MARGIN


# ---------- texto en línea ----------
TOKEN = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|<https?://[^>]+>)")


def breakable(s):
    return re.sub(r"([/_,])", r"\1" + ZWSP, s)


def add_inline(par, text, size, bold=False, italic=False, link_map=None, code_break=False):
    for tok in TOKEN.split(text):
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**"):
            add_inline(par, tok[2:-2], size, True, italic, link_map, code_break)
            continue
        font, t = "Arial", tok
        if tok.startswith("`"):
            font, t = "Consolas", tok[1:-1]
            if code_break:
                t = breakable(t)
        elif tok.startswith("["):
            m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", tok)
            t = m.group(1)
            if link_map and t in link_map:
                t = link_map[t]
        elif tok.startswith("<http"):
            t = tok[1:-1]
        r = par.add_run(t)
        r.bold = bold or None
        r.italic = italic or None
        r.font.size = Pt(size - 1 if font == "Consolas" else size)
        if font != "Arial":
            r.font.name = font
            rfonts(r._element.get_or_add_rPr(), font)


def body(text, link_map=None, size=12, bold=False, align=None):
    p = doc.add_paragraph()
    add_inline(p, text, size, bold=bold, link_map=link_map)
    if align is not None:
        p.alignment = align
    return p


def fuente():
    p = doc.add_paragraph()
    r = p.add_run("Fuente: elaboración propia.")
    r.italic = True
    r.font.size = Pt(10)
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(10)
    return p


def annex_title(text):
    return doc.add_paragraph(text.upper(), style="Título de anexo")


# ---------- tablas ----------
def shade(el, fill):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    el.append(shd)


def table_borders(tbl):
    b = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{side}")
        e.set(qn("w:val"), "single")
        e.set(qn("w:sz"), "4")
        e.set(qn("w:space"), "0")
        e.set(qn("w:color"), "808080")
        b.append(e)
    tbl._tbl.tblPr.append(b)


def auto_widths(rows, total):
    w = []
    for c in range(len(rows[0])):
        lens = [len(re.sub(r"[`*]", "", r[c])) for r in rows]
        w.append(max(4, min(60, sum(lens) / len(lens))) ** 0.8)
    s = sum(w)
    return [total * x / s for x in w]


def md_table(lines, size, landscape, widths=None):
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    rows = [rows[0]] + rows[2:]
    widths = widths or auto_widths(rows, usable_width(landscape))
    tbl = doc.add_table(rows=len(rows), cols=len(rows[0]))
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = False
    table_borders(tbl)
    tblPr = tbl._tbl.tblPr
    old = tblPr.find(qn("w:tblW"))
    if old is not None:
        tblPr.remove(old)
    tw = OxmlElement("w:tblW")
    tw.set(qn("w:w"), str(int(sum(widths) / 2.54 * 1440)))
    tw.set(qn("w:type"), "dxa")
    tblPr.append(tw)
    lay = OxmlElement("w:tblLayout")
    lay.set(qn("w:type"), "fixed")
    tblPr.append(lay)
    for gc, wcm in zip(tbl._tbl.tblGrid.findall(qn("w:gridCol")), widths):
        gc.set(qn("w:w"), str(int(wcm / 2.54 * 1440)))
    for i, r in enumerate(rows):
        row = tbl.rows[i]
        trPr = row._tr.get_or_add_trPr()
        trPr.append(OxmlElement("w:cantSplit"))
        if i == 0:
            trPr.append(OxmlElement("w:tblHeader"))  # encabezado repetido en cada página
        for j, text in enumerate(r):
            cell = row.cells[j]
            cell.width = Cm(widths[j])
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.line_spacing = 1.0
            p.paragraph_format.space_after = Pt(0)
            add_inline(p, text, size, bold=(i == 0), code_break=True)
            if i == 0:
                shade(cell._tc.get_or_add_tcPr(), "D9D9D9")
    fuente()
    return tbl


# ---------- bloques de código ----------
def code_block(lines):
    for k, ln in enumerate(lines):
        p = doc.add_paragraph()
        f = p.paragraph_format
        f.line_spacing = 1.0
        f.space_before = Pt(4 if k == 0 else 0)
        f.space_after = Pt(4 if k == len(lines) - 1 else 0)
        f.left_indent = Cm(0.2)
        f.right_indent = Cm(0.2)
        f.keep_with_next = k < len(lines) - 1
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        shade(p._p.get_or_add_pPr(), "F2F2F2")
        r = p.add_run(ln if ln else " ")
        r.font.name = "Consolas"
        r.font.size = Pt(9)
        rfonts(r._element.get_or_add_rPr(), "Consolas")
    sp = doc.add_paragraph()
    sp.paragraph_format.space_after = Pt(0)
    sp.paragraph_format.line_spacing = 1.0
    sp.add_run().font.size = Pt(4)


def callout(text, link_map):
    p = body(text, link_map)
    pPr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "12")
    left.set(qn("w:space"), "6")
    left.set(qn("w:color"), "A6A6A6")
    bdr.append(left)
    pPr.append(bdr)
    p.paragraph_format.left_indent = Cm(0.5)
    return p


# ---------- markdown ----------
def render_md(text, landscape, table_size, link_map=None, widths_for=None, heading_offset=0):
    lines = text.splitlines()
    i, tcount = 0, 0
    para = []

    def flush():
        if para:
            body(" ".join(para), link_map)
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            flush()
            j = i + 1
            while not lines[j].startswith("```"):
                j += 1
            code_block(lines[i + 1:j])
            i = j + 1
            continue
        if ln.startswith("|"):
            flush()
            j = i
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            md_table(lines[i:j], table_size, landscape, widths_for(tcount) if widths_for else None)
            tcount += 1
            i = j
            continue
        m = re.match(r"^(#{2,3}) (.*)", ln)
        if m:
            flush()
            h = doc.add_paragraph(style=SUB[len(m.group(1)) + heading_offset])
            add_inline(h, m.group(2), 12, bold=True, link_map=link_map)
            i += 1
            continue
        if ln.startswith("> "):
            flush()
            callout(ln[2:], link_map)
            i += 1
            continue
        m = re.match(r"^(- |\d+\. )(.*)", ln)
        if m:
            flush()
            p = doc.add_paragraph(style="List Bullet" if m.group(1) == "- " else "List Number")
            add_inline(p, m.group(2), 12, link_map=link_map)
            i += 1
            continue
        if not ln.strip():
            flush()
            i += 1
            continue
        if re.fullmatch(r"\*\*[^*]+\*\*", ln.strip()):
            flush()
            p = body(ln.strip(), link_map)
            p.paragraph_format.keep_with_next = True
            p.paragraph_format.space_before = Pt(6)
            i += 1
            continue
        para.append(ln.strip())
        i += 1
    flush()


def strip_h1(text):
    return "\n".join(l for l in text.splitlines() if not l.startswith("# "))


def must_replace(text, old, new):
    assert old in text, f"no encontrado: {old!r}"
    return text.replace(old, new)


# ---------- figuras ----------
def figure(path, label, title, width_cm=None, height_cm=None, new_page=False):
    cp = doc.add_paragraph(style="Caption")
    cp.paragraph_format.page_break_before = new_page
    r = cp.add_run(f"{label}.")
    r.bold = True
    add_inline(cp, f" {title}", 12)
    ip = doc.add_paragraph()
    ip.alignment = WD_ALIGN_PARAGRAPH.CENTER
    ip.paragraph_format.keep_with_next = True
    ip.paragraph_format.space_after = Pt(0)
    ip.paragraph_format.line_spacing = 1.0
    kw = {}
    if width_cm:
        kw["width"] = Cm(width_cm)
    if height_cm:
        kw["height"] = Cm(height_cm)
    ip.add_run().add_picture(str(path), **kw)
    fuente()


def fit(path, max_w, max_h):
    w, h = Image.open(path).size
    scale = min(max_w / w, max_h / h)
    return w * scale, h * scale


# =====================================================================
# ANEXO A (horizontal)
# =====================================================================
new_section(landscape=True)
annex_title("Anexo A. Matriz de pruebas por requisito funcional")
a = strip_h1((ENT / "matriz-pruebas-rf.md").read_text())
a = must_replace(a, "RF-001 Registro de administrador",
                 "RF-001 Creación controlada de la cuenta del administrador")
a = must_replace(a, "## Fallos y funciones no implementadas", "### Fallos y funciones no implementadas")
render_md(a, True, 8, {"guía de ejecución": "guía de ejecución (Anexo F)"},
          widths_for=lambda k: [2.4, 2.6, 3.0, 3.3, 4.7, 1.7, 5.2] if k == 0 else [5.5, 17.4],
          heading_offset=-1)

# =====================================================================
# ANEXO B: B.1 y B.2 en vertical, B.3 en horizontal
# =====================================================================
new_section(landscape=False)
annex_title("Anexo B. Diagramas completos de arquitectura y del modelo de datos")
body("Este anexo presenta los diagramas completos de la arquitectura y del modelo de datos de "
     "SoftPh, que complementan las versiones resumidas de las secciones 9.1 y 9.2. Para que se "
     "lea en una página carta, el modelo de datos completo se divide en dos partes: usuarios, "
     "seguridad, estructura residencial e invitaciones (Figura B.2), y finanzas y comunicación "
     "(Figura B.3). En la segunda, las tablas en gris pertenecen a la primera.")
comp = ENT / "arquitectura/componentes.png"
w, h = fit(comp, usable_width(False), 20.4)
figure(comp, "Figura B.1", "Diagrama de componentes detallado de SoftPh.", width_cm=w, new_page=True)
p1 = ENT / "modelo-datos/modelo-datos-completo-parte1.png"
w, h = fit(p1, usable_width(False), 20.4)
figure(p1, "Figura B.2", "Modelo lógico de datos completo de SoftPh, parte 1: usuarios, seguridad, "
       "estructura residencial e invitaciones (10 de 16 tablas).", width_cm=w, new_page=True)
new_section(landscape=True)
p2 = ENT / "modelo-datos/modelo-datos-completo-parte2.png"
w, h = fit(p2, usable_width(True), 14.0)
figure(p2, "Figura B.3", "Modelo lógico de datos completo de SoftPh, parte 2: finanzas y "
       "comunicación (6 de 16 tablas).", width_cm=w)

# =====================================================================
# ANEXO C (vertical)
# =====================================================================
new_section(landscape=False)
annex_title("Anexo C. Correspondencia entre los diagramas y la implementación")
c = strip_h1((ENT / "correspondencia.md").read_text())
c = must_replace(c, "con el repositorio (rama `main`; código de la aplicación del commit `7191762`)",
                 "con el repositorio, sobre la versión v1.1-academico")
c = must_replace(c, "El archivo y las líneas de cada comunicación de la versión resumida están en "
                    "`verificacion-arquitectura.md`.",
                 "El archivo y las líneas de cada comunicación de la versión resumida están en el "
                 "guion de verificación de la arquitectura del repositorio "
                 "(`docs/entrega-objetivos/verificacion-arquitectura.md`).")
render_md(c, False, 9, None,
          widths_for=lambda k: [3.4, 4.6, 1.8, 6.7] if k == 0 else [2.2, 2.2, 2.3, 1.9, 3.2, 1.6, 3.1])

# =====================================================================
# ANEXO D (vertical, dos capturas por página)
# =====================================================================
new_section(landscape=False)
annex_title("Anexo D. Capturas del prototipo")
body("Las capturas se obtuvieron con Playwright (Chromium, 1280 px de ancho) sobre la pila de "
     "pruebas del proyecto, con datos ficticios de prueba: la administradora «Administradora "
     "Pruebas» y la residente Laura Gómez, del apartamento B1-TO1-APTO-101. Las figuras D.1 a "
     "D.19 se obtuvieron el 27 de octubre de 2026; la figura D.20, el 3 de noviembre de 2026, "
     "sobre la versión v1.1-academico. El correo que se muestra es el del buzón de pruebas "
     "(Mailpit).")

readme = (CAP / "README.md").read_text()
caps = []
for m in re.finditer(r"^\| `(\d\d-[^`]+\.png)` \| (.+?) \|$", readme, re.M):
    title = re.sub(r"\s*Generada el .*$", "", m.group(2)).strip()
    caps.append((m.group(1), title if title.endswith(".") else title + "."))
assert len(caps) == 20, len(caps)

AVAIL_FULL, AVAIL_FIRST, MAXW = 18.6, 14.4, 15.0


def fit_pair(paths, avail):
    nat = [MAXW * Image.open(p).size[1] / Image.open(p).size[0] for p in paths]
    if sum(nat) <= avail:
        return nat
    half = avail / len(nat)
    small = min(range(len(nat)), key=lambda k: nat[k])
    if nat[small] <= half:
        out = list(nat)
        out[1 - small] = min(nat[1 - small], avail - nat[small])
        return out
    return [half] * len(nat)


for k in range(0, 20, 2):
    pair = caps[k:k + 2]
    paths = [CAP / f for f, _ in pair]
    for idx, ((fname, title), path, hcm) in enumerate(
            zip(pair, paths, fit_pair(paths, AVAIL_FIRST if k == 0 else AVAIL_FULL))):
        w, h = Image.open(path).size
        figure(path, f"Figura D.{k + idx + 1}", title, width_cm=hcm * w / h, new_page=(k > 0 and idx == 0))

# =====================================================================
# ANEXO E
# =====================================================================
new_section(landscape=False)
annex_title("Anexo E. Autorización de la administración de la Urbanización San Joaquín")
box = doc.add_table(rows=1, cols=1)
box.alignment = WD_TABLE_ALIGNMENT.CENTER
table_borders(box)
cell = box.rows[0].cells[0]
cell.width = Cm(usable_width(False))
trh = OxmlElement("w:trHeight")
trh.set(qn("w:val"), str(int(8 / 2.54 * 1440)))
trh.set(qn("w:hRule"), "atLeast")
box.rows[0]._tr.get_or_add_trPr().append(trh)
va = OxmlElement("w:vAlign")
va.set(qn("w:val"), "center")
cell._tc.get_or_add_tcPr().append(va)
p = cell.paragraphs[0]
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.add_run("[Insertar aquí la imagen escaneada de la carta de autorización]")

# =====================================================================
# ANEXO F (secciones 1 a 9 de la guía)
# =====================================================================
new_section(landscape=False)
annex_title("Anexo F. Guía de instalación y ejecución")
g = (ENT / "guia-ejecucion.md").read_text().split("\n## 10. ")[0]
render_md(strip_h1(g), False, 9, None)

doc.save(OUT)
print("guardado", OUT)
