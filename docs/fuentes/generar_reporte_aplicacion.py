# -*- coding: utf-8 -*-
"""
generar_reporte_aplicacion.py — Reporte de Aplicación de Conocimientos (.docx)

Modalidad de titulación. Secciones exigidas:
  portada · importancia del problema · solución/propuesta · revisión documental
  · cronograma · referencias bibliográficas · originalidad de la propuesta

Todo el contenido procede del material verificado en docs/fuentes/capitulos/*.md
y de docs/fuentes/bibliografia.bib (plan F0-F12, cerrado el 2026-08-27).

    python3 docs/fuentes/generar_reporte_aplicacion.py
"""
import re
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BASE = Path(__file__).resolve().parent
IMG = BASE / "img"
BIB = BASE / "bibliografia.bib"
SALIDA = BASE.parent / "Iron_Progress_Reporte_Aplicacion_Conocimientos.docx"

AZUL = RGBColor(0x1F, 0x3A, 0x5F)
GRIS = RGBColor(0x55, 0x5F, 0x6E)
ROJO = RGBColor(0x8A, 0x2B, 0x2B)
NEGRO = RGBColor(0x1A, 0x1A, 0x1A)
RELLENO = RGBColor(0xB0, 0x30, 0x30)   # placeholders que el autor debe completar

doc = Document()

# ─────────────────────────────────────────────────────────────── estilos ────
def _estilos():
    n = doc.styles["Normal"]
    n.font.name = "Calibri"
    n.font.size = Pt(11)
    n.font.color.rgb = NEGRO
    n.paragraph_format.space_after = Pt(8)
    n.paragraph_format.line_spacing = 1.5          # interlineado académico
    n.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    for nivel, tam in (("Heading 1", 16), ("Heading 2", 13), ("Heading 3", 11.5)):
        st = doc.styles[nivel]
        st.font.name = "Calibri"
        st.font.size = Pt(tam)
        st.font.bold = True
        st.font.color.rgb = AZUL
        st.paragraph_format.space_before = Pt(18 if nivel == "Heading 1" else 12)
        st.paragraph_format.space_after = Pt(8)
        st.paragraph_format.keep_with_next = True
        st.paragraph_format.line_spacing = 1.15

    cq = doc.styles.add_style("Codigo", 1)
    cq.font.name = "Consolas"
    cq.font.size = Pt(9)
    cq.font.color.rgb = RGBColor(0x24, 0x35, 0x4A)
    cq.paragraph_format.left_indent = Cm(0.8)
    cq.paragraph_format.line_spacing = 1.0
    cq.paragraph_format.space_after = Pt(2)
    cq.paragraph_format.space_before = Pt(2)
    cq.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT

    pie = doc.styles.add_style("PieFigura", 1)
    pie.font.name = "Calibri"
    pie.font.size = Pt(9)
    pie.font.italic = True
    pie.font.color.rgb = GRIS
    pie.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pie.paragraph_format.space_after = Pt(14)
    pie.paragraph_format.line_spacing = 1.0

    cit = doc.styles.add_style("CitaLarga", 1)
    cit.font.name = "Calibri"
    cit.font.size = Pt(10.5)
    cit.paragraph_format.left_indent = Cm(1.27)     # sangría APA para citas >40 palabras
    cit.paragraph_format.right_indent = Cm(0.5)
    cit.paragraph_format.space_after = Pt(10)
    cit.paragraph_format.line_spacing = 1.5
    cit.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    ref = doc.styles.add_style("Referencia", 1)
    ref.font.name = "Calibri"
    ref.font.size = Pt(11)
    ref.paragraph_format.left_indent = Cm(1.27)
    ref.paragraph_format.first_line_indent = Cm(-1.27)   # sangría francesa APA
    ref.paragraph_format.space_after = Pt(10)
    ref.paragraph_format.line_spacing = 1.5
    ref.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


_estilos()

sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21.59), Cm(27.94)   # carta
sec.left_margin = Cm(3.0)      # margen de encuadernación
sec.right_margin = Cm(2.5)
sec.top_margin = sec.bottom_margin = Cm(2.5)
ANCHO = sec.page_width - sec.left_margin - sec.right_margin


# ──────────────────────────────────────────────────────────── utilidades ────
def p(texto="", estilo=None, negrita=False, tam=None, color=None,
      alineacion=None, antes=None, despues=None, cursiva=False):
    par = doc.add_paragraph(style=estilo)
    if texto:
        r = par.add_run(texto)
        r.bold = negrita
        r.italic = cursiva
        if tam:
            r.font.size = Pt(tam)
        if color:
            r.font.color.rgb = color
    if alineacion is not None:
        par.paragraph_format.alignment = alineacion
    if antes is not None:
        par.paragraph_format.space_before = Pt(antes)
    if despues is not None:
        par.paragraph_format.space_after = Pt(despues)
    return par


def rico(*trozos, estilo=None, vinetas=False, numerada=False):
    """Párrafo con tramos (texto, fmt). fmt: '' | 'b' | 'i' | 'c' | 'r' (rojo)."""
    est = "List Bullet" if vinetas else ("List Number" if numerada else estilo)
    par = doc.add_paragraph(style=est)
    for texto, fmt in trozos:
        r = par.add_run(texto)
        if "b" in fmt:
            r.bold = True
        if "i" in fmt:
            r.italic = True
        if "r" in fmt:
            r.font.color.rgb = RELLENO
            r.bold = True
        if "c" in fmt:
            r.font.name = "Consolas"
            r.font.size = Pt(9.5)
            r.font.color.rgb = RGBColor(0x24, 0x35, 0x4A)
    if vinetas or numerada:
        par.paragraph_format.space_after = Pt(4)
        par.paragraph_format.line_spacing = 1.3
        par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    return par


def codigo(lineas):
    for ln in lineas.rstrip("\n").split("\n"):
        par = doc.add_paragraph(style="Codigo")
        par.add_run(ln if ln else " ")
    doc.add_paragraph().paragraph_format.space_after = Pt(6)


def sombrear(celda, hexcol):
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")
    el.set(qn("w:fill"), hexcol)
    celda._tc.get_or_add_tcPr().append(el)


_CONT_TAB = [0]
_IDX_TAB = []
_CONT_FIG = [0]
_IDX_FIG = []


def tabla(cabeceras, filas, titulo=None, anchos=None, tam=9.5):
    if titulo:
        _CONT_TAB[0] += 1
        etiqueta = f"Tabla {_CONT_TAB[0]}"
        par = doc.add_paragraph()
        par.paragraph_format.space_after = Pt(3)
        par.paragraph_format.line_spacing = 1.0
        r = par.add_run(f"{etiqueta}. ")
        r.bold = True
        r.font.size = Pt(9.5)
        r2 = par.add_run(titulo)
        r2.italic = True
        r2.font.size = Pt(9.5)
        _IDX_TAB.append((etiqueta, titulo))

    t = doc.add_table(rows=1, cols=len(cabeceras))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    # Word ignora los anchos de celda salvo que el layout sea FIJO. Sin esto
    # reparte las columnas por contenido y las proporciones se pierden.
    tblPr = t._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    for i, h in enumerate(cabeceras):
        c = t.rows[0].cells[i]
        c.text = ""
        pr = c.paragraphs[0]
        pr.paragraph_format.line_spacing = 1.0
        pr.paragraph_format.space_after = Pt(3)
        pr.paragraph_format.space_before = Pt(3)
        r = pr.add_run(h)
        r.bold = True
        r.font.size = Pt(tam)
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        sombrear(c, "2B3A55")
    for n, fila in enumerate(filas):
        celdas = t.add_row().cells
        for i, val in enumerate(fila):
            celdas[i].text = ""
            pr = celdas[i].paragraphs[0]
            pr.paragraph_format.line_spacing = 1.0
            pr.paragraph_format.space_after = Pt(3)
            pr.paragraph_format.space_before = Pt(3)
            mono = val.startswith("`") and val.endswith("`")
            neg = val.startswith("**") and val.endswith("**")
            limpio = val.strip("`").strip("*")
            r = pr.add_run(limpio)
            r.font.size = Pt(tam - 0.5)
            r.bold = neg
            if mono:
                r.font.name = "Consolas"
                r.font.size = Pt(tam - 1.2)
                r.font.color.rgb = RGBColor(0x24, 0x35, 0x4A)
            if n % 2 == 1:
                sombrear(celdas[i], "F4F6FA")
    if anchos:
        for i, w in enumerate(anchos):
            t.columns[i].width = Cm(w)
        for fila in t.rows:
            for i, w in enumerate(anchos):
                fila.cells[i].width = Cm(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(8)
    return t


def figura(nombre, pie_texto, ancho=None):
    ruta = IMG / nombre
    if not ruta.exists():
        return
    _CONT_FIG[0] += 1
    etiqueta = f"Figura {_CONT_FIG[0]}"
    doc.add_picture(str(ruta), width=ancho or ANCHO)
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.paragraphs[-1].paragraph_format.space_before = Pt(10)
    doc.paragraphs[-1].paragraph_format.space_after = Pt(3)
    par = doc.add_paragraph(style="PieFigura")
    r = par.add_run(f"{etiqueta}. ")
    r.bold = True
    par.add_run(pie_texto)
    _IDX_FIG.append((etiqueta, pie_texto))


def aviso(titulo, cuerpo, color="FFF4E5", borde=ROJO):
    t = doc.add_table(rows=1, cols=1)
    t.style = "Table Grid"
    c = t.rows[0].cells[0]
    c.text = ""
    par = c.paragraphs[0]
    par.paragraph_format.line_spacing = 1.2
    par.paragraph_format.space_before = Pt(5)
    par.paragraph_format.space_after = Pt(5)
    par.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    r = par.add_run(titulo + "  ")
    r.bold = True
    r.font.size = Pt(10)
    r.font.color.rgb = borde
    r2 = par.add_run(cuerpo)
    r2.font.size = Pt(10)
    sombrear(c, color)
    doc.add_paragraph().paragraph_format.space_after = Pt(8)


def salto():
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


# ─────────────────────────────────────────────────── bibliografía / citas ────
def _cargar_bib():
    if not BIB.exists():
        return {}
    txt = BIB.read_text(encoding="utf-8")
    out = {}
    for bloque in re.split(r"\n(?=@)", txt):
        m = re.match(r"@(\w+)\{\s*([^,\s]+)\s*,", bloque)
        if not m:
            continue
        campos = {"tipo": m.group(1)}
        for fm in re.finditer(r"(\w+)\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}", bloque):
            campos[fm.group(1).lower()] = re.sub(r"\s+", " ", fm.group(2)).strip()
        out[m.group(2)] = campos
    return out


_BIB = _cargar_bib()
_USADAS = {}


def _apellido(campo):
    primero = campo.split(" and ")[0].strip()
    if primero.startswith("{"):
        return primero.strip("{}")
    return primero.split(",")[0].strip()


def cita(clave, pagina=None):
    if clave not in _BIB:
        return f"[CITA-NO-ENCONTRADA: {clave}]"
    _USADAS.setdefault(clave, None)
    e = _BIB[clave]
    autor = _apellido(e.get("author", "s.a."))
    if " and " in e.get("author", "") or "others" in e.get("author", ""):
        autor += " et al."
    extra = f", p. {pagina}" if pagina else ""
    return f"({autor}, {e.get('year', 's.f.')}{extra})"


def _limpiar_latex(s):
    """BibTeX conserva marcado que no debe llegar al documento final:
    llaves de protección de mayúsculas ({BlazePose}), guiones dobles de
    rango (199--210), ampersands escapados (\\&) y comandos como
    \\texttt{...}. Sin esta limpieza el PDF muestra el marcado en crudo."""
    s = re.sub(r"\\texttt\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\emph\{([^{}]*)\}", r"\1", s)
    s = s.replace("\\&", "&").replace("\\_", "_").replace("\\%", "%")
    s = s.replace("--", "\u2013")          # rango de páginas con raya corta
    s = s.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()


def _fmt_apa(e):
    autores = e.get("author", "s.a.")
    if autores.startswith("{"):
        autores = autores.strip("{}")
    else:
        partes = [a.strip() for a in autores.split(" and ")]
        fmt = []
        for a in partes:
            if a == "others":
                fmt.append("et al.")
            elif "," in a:
                ap, nom = a.split(",", 1)
                iniciales = " ".join(f"{x.strip()[0]}." for x in nom.split() if x.strip())
                fmt.append(f"{ap.strip()}, {iniciales}")
            else:
                fmt.append(a)
        if len(fmt) == 1:
            autores = fmt[0]
        elif fmt[-1] == "et al.":
            autores = ", ".join(fmt[:-1]) + ", et al."
        else:
            autores = ", ".join(fmt[:-1]) + " & " + fmt[-1]
    partes = [f"{autores} ({e.get('year', 's.f.')}). {e.get('title', '')}."]
    if "journal" in e:
        vol = e.get("volume", "")
        num = f"({e['number']})" if e.get("number") else ""
        pags = f", {e['pages']}" if e.get("pages") else ""
        partes.append(f"{e['journal']}, {vol}{num}{pags}.")
    elif "booktitle" in e:
        partes.append(f"En {e['booktitle']}.")
    elif "publisher" in e:
        partes.append(f"{e['publisher']}.")
    if e.get("isbn"):
        partes.append(f"ISBN {e['isbn']}.")
    if e.get("note"):
        partes.append(f"[{e['note']}]")
    return _limpiar_latex(" ".join(partes))


def referencias():
    orden = sorted(_USADAS, key=lambda k: _apellido(_BIB[k].get("author", k)).lower())
    for clave in orden:
        e = _BIB[clave]
        par = doc.add_paragraph(style="Referencia")
        texto = _fmt_apa(e)
        pendiente = any(x in e.get("note", "").upper()
                        for x in ("VERIFICAR", "CITAR SOLO SI"))
        r = par.add_run(texto)
        if pendiente:
            r.font.color.rgb = ROJO


# ══════════════════════════════════════════════════════════════ PORTADA ════
def portada():
    """Portada compacta: interlineado 1.0 y espaciado explícito, para que el
    bloque completo quepa en UNA sola página (con interlineado 1.5 heredado
    del estilo Normal se desbordaba a la segunda)."""
    def _pt(texto, tam, negrita=False, color=None, antes=0, despues=0):
        par = doc.add_paragraph()
        par.paragraph_format.line_spacing = 1.0
        par.paragraph_format.space_before = Pt(antes)
        par.paragraph_format.space_after = Pt(despues)
        par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = par.add_run(texto)
        r.bold = negrita
        r.font.size = Pt(tam)
        if color:
            r.font.color.rgb = color
        return par

    _pt("[NOMBRE DE LA INSTITUCIÓN]", 14, negrita=True, color=RELLENO,
        antes=18, despues=4)
    _pt("[NOMBRE DE LA FACULTAD / DIVISIÓN]", 12, color=RELLENO, despues=4)
    _pt("[NOMBRE DEL PROGRAMA EDUCATIVO]", 12, color=RELLENO, despues=0)

    _pt("REPORTE DE APLICACIÓN DE CONOCIMIENTOS", 12.5, negrita=True,
        color=GRIS, antes=90, despues=18)

    _pt("IRON PROGRESS", 26, negrita=True, color=AZUL, despues=8)
    _pt("Agente conversacional de entrenamiento de fuerza sobre un modelo de "
        "lenguaje local, con garantías arquitectónicas contra la alucinación",
        12.5, despues=0)

    _pt("Que para obtener el título de", 11, antes=72, despues=5)
    _pt("[TÍTULO A OBTENER]", 12.5, negrita=True, color=RELLENO, despues=0)

    _pt("PRESENTA", 10.5, color=GRIS, antes=54, despues=5)
    _pt("Abimael Guadarrama", 13, negrita=True, despues=0)

    _pt("ASESOR", 10.5, color=GRIS, antes=26, despues=5)
    _pt("[GRADO Y NOMBRE DEL ASESOR]", 12, color=RELLENO, despues=0)

    _pt("[CIUDAD, ESTADO] · Agosto de 2026", 11, color=RELLENO,
        antes=60, despues=0)
    salto()


# ════════════════════════════════════════════════════════ RESUMEN E ÍNDICE ══
def resumen_e_indice():
    p("Resumen", estilo="Heading 1")
    p("Las aplicaciones de entrenamiento de fuerza registran datos; un entrenador "
      "humano, además, recuerda, interpreta y decide. Este reporte documenta el "
      "diseño, la implementación y la evaluación de Iron Progress, un agente "
      "conversacional que ejerce de entrenador de fuerza a través de WhatsApp, "
      "sobre un modelo de lenguaje de 27 000 millones de parámetros ejecutado "
      "íntegramente en hardware local. El problema central que aborda es "
      "estructural: en un dominio donde el usuario no puede verificar lo que el "
      "sistema le dice, una alucinación numérica del modelo —una carga inventada, "
      "una descarga que nunca se aplicó— no degrada la experiencia, degrada el "
      "entrenamiento físico de una persona. La solución propuesta separa de forma "
      "explícita el cálculo del lenguaje: código determinista interpreta el "
      "mensaje, ejecuta las escrituras y calcula toda cifra que llega al atleta; "
      "el modelo de lenguaje nunca calcula ni decide sobre datos numéricos, solo "
      "elige la herramienta pertinente y comunica el resultado ya producido. "
      "Sobre esa base se implementan seis redes de validación que auditan la "
      "respuesta generada contra el estado real del sistema antes de enviarla. El "
      "sistema integra además un motor de planificación con periodización "
      "ondulante diaria, un motor predictivo que estima repetición máxima y "
      "volumen recuperable, un motor de visión que extrae métricas de "
      "entrenamiento basado en velocidad a partir de vídeo sin hardware "
      "adicional, y un clasificador de ejercicios entrenado sobre un conjunto de "
      "datos propio. La aportación defendida es un patrón de diseño "
      "generalizable: en cualquier dominio donde el usuario no pueda verificar la "
      "respuesta de un modelo de lenguaje, la fuente de verdad debe vivir fuera "
      "del modelo.")
    p()
    rico(("Palabras clave: ", "b"),
         ("agente conversacional, modelo de lenguaje local, mitigación de "
          "alucinaciones, entrenamiento de fuerza, periodización, entrenamiento "
          "basado en velocidad.", "i"))
    salto()

    p("Índice", estilo="Heading 1")
    entradas = [
        ("1.", "Importancia del problema", 1),
        ("1.1", "Contexto y antecedentes", 2),
        ("1.2", "Planteamiento del problema", 2),
        ("1.3", "Justificación", 2),
        ("1.4", "Objetivos", 2),
        ("2.", "Revisión documental", 1),
        ("2.1", "Fundamentos de la ciencia del entrenamiento de fuerza", 2),
        ("2.2", "Modelos de lenguaje, agentes y llamada a herramientas", 2),
        ("2.3", "Alucinación en modelos de lenguaje y su mitigación", 2),
        ("2.4", "Visión por computadora, habla y aprendizaje automático", 2),
        ("2.5", "Trabajos previos y soluciones existentes", 2),
        ("3.", "Solución y propuesta", 1),
        ("3.1", "Arquitectura general del sistema", 2),
        ("3.2", "El principio de diseño central: separar el cálculo del lenguaje", 2),
        ("3.3", "Los interceptores deterministas", 2),
        ("3.4", "Las seis redes de validación de salida", 2),
        ("3.5", "Los motores de dominio", 2),
        ("3.6", "Capacidades multimodales: voz y visión", 2),
        ("3.7", "Modelo de datos y persistencia", 2),
        ("3.8", "Concurrencia y arquitectura de colas", 2),
        ("3.9", "Estrategia de verificación", 2),
        ("3.10", "Consideraciones éticas, legales y de protección de datos", 2),
        ("4.", "Originalidad de la propuesta", 1),
        ("4.1", "Comparativa frente a los trabajos previos", 2),
        ("4.2", "Aportaciones originales de este trabajo", 2),
        ("5.", "Resultados de la aplicación", 1),
        ("6.", "Cronograma", 1),
        ("7.", "Conclusiones", 1),
        ("8.", "Referencias bibliográficas", 1),
    ]
    for num, titulo, nivel in entradas:
        par = doc.add_paragraph()
        par.paragraph_format.line_spacing = 1.15
        par.paragraph_format.space_after = Pt(1.5)
        par.paragraph_format.space_before = Pt(4 if nivel == 1 else 0)
        par.paragraph_format.left_indent = Cm(0.0 if nivel == 1 else 0.9)
        r = par.add_run(f"{num}\t{titulo}")
        r.bold = (nivel == 1)
        r.font.size = Pt(10.5 if nivel == 1 else 10)
    p()
    p("Nota: el índice se muestra sin números de página. Al abrir el documento en "
      "Word, sustitúyalo por un índice automático (Referencias → Tabla de "
      "contenido) para que la paginación se genere y actualice sola.",
      tam=9.5, color=GRIS, cursiva=True)
    salto()


# ═══════════════════════════════════════════ 1. IMPORTANCIA DEL PROBLEMA ════
def importancia_del_problema():
    p("1. Importancia del problema", estilo="Heading 1")

    p("1.1 Contexto y antecedentes", estilo="Heading 2")
    p("Las aplicaciones de seguimiento de entrenamiento de fuerza resuelven un "
      "problema de registro: el usuario introduce peso, repeticiones y series, y "
      "la aplicación las almacena, las grafica y, en el mejor de los casos, "
      "calcula una repetición máxima estimada con una fórmula fija. Ese es el "
      "techo de la categoría: registrar lo que el usuario ya decidió hacer.")
    p("Un entrenador humano hace algo cualitativamente distinto. No solo anota: "
      "recuerda que a un atleta le molestó el hombro hace tres semanas y evita "
      "cargarlo sin preguntar; detecta un estancamiento en un movimiento concreto "
      "aunque el resto del cuerpo progrese con normalidad; y decide, con datos y "
      "sin que se lo pidan, reducir la carga el día que el atleta llega "
      "visiblemente fatigado. Esa capacidad de interpretar, recordar y decidir es "
      "lo que separa acompañar un entrenamiento de solo registrarlo, y es el hueco "
      "que las aplicaciones convencionales no cubren porque no está en su alcance "
      "de diseño: no interpretan lenguaje natural, no mantienen estado "
      "interpretativo sobre el atleta más allá de una tabla de series, y no toman "
      "decisiones prescriptivas de forma proactiva.")
    p("Los modelos de lenguaje de gran escala hacen técnicamente posible cerrar "
      "ese hueco por primera vez: interpretan lenguaje natural con soltura "
      "suficiente para sostener una conversación de entrenamiento, y pueden "
      "orquestar herramientas externas para actuar sobre un sistema real "
      f"{cita('Yao2023')}; {cita('Schick2023')}. Iron Progress es la "
      "materialización de esa posibilidad como sistema en producción: un agente "
      "que gestiona la relación completa del entrenamiento de fuerza "
      "—planificación, registro, adaptación y análisis de fatiga— a través del "
      "canal de mensajería que el atleta ya usa a diario.")

    p("1.2 Planteamiento del problema", estilo="Heading 2")
    p("Construir ese sistema sobre un modelo de lenguaje introduce un riesgo que "
      "las aplicaciones convencionales no tienen: un modelo generativo produce "
      "texto plausible, no texto verdadero "
      f"{cita('Ji2023')}; {cita('Huang2025')}. En la mayoría de las "
      "aplicaciones de un modelo de lenguaje, una alucinación degrada la "
      "experiencia del usuario: una respuesta extraña, una cita inventada que "
      "puede verificarse en otra fuente. En este dominio degrada directamente el "
      "entrenamiento físico de una persona, y el atleta no tiene forma de auditar "
      "la cifra que recibe: no sabe si «sube a 95 kg» procede de su marca real o "
      "de una invención del modelo.")
    p("El riesgo no es hipotético. Durante el desarrollo del sistema se registró "
      "el siguiente incidente real:")
    aviso("Incidente registrado el 29 de julio de 2026.",
          "Ante el mensaje del atleta «llevo toda la semana molido, ¿me toca "
          "descarga?», el sistema —en una versión previa, sin la red de validación "
          "que este incidente motivó— consultó el análisis de fatiga, NO ejecutó "
          "ninguna aplicación de descarga, y sin embargo respondió: «Ajuste "
          "aplicado: reducción del 10 % en la carga, una serie menos». No existía "
          "ninguna descarga real. Los cinco turnos siguientes de la conversación "
          "continuaron refiriéndose a «la descarga que aplicamos», reforzando la "
          "alucinación en cada turno. De no haberse detectado, el atleta habría "
          "entrenado un mesociclo completo con menos carga de la que le "
          "correspondía, sin que nada en la conversación lo delatara.")
    p("De este planteamiento se deriva la pregunta que estructura el trabajo:")
    par = doc.add_paragraph(style="CitaLarga")
    r = par.add_run("¿Puede un modelo de lenguaje ejecutado en local sostener el "
                    "rol de entrenador de fuerza —interpretando lenguaje natural, "
                    "decidiendo cargas y comunicando resultados— sin que sus "
                    "alucinaciones se traduzcan en decisiones de entrenamiento "
                    "incorrectas para el atleta, y sin enviar los datos de salud "
                    "del usuario fuera de su propia máquina?")
    r.bold = True

    p("1.3 Justificación", estilo="Heading 2")
    p("La importancia de resolver este problema se sostiene sobre tres ejes:")
    rico(("Eje clínico-deportivo. ", "b"),
         ("Una cifra de carga mal calculada no es un error cosmético: se traduce "
          "en semanas de entrenamiento con un estímulo distinto del pretendido, "
          "sea por exceso —con el consiguiente riesgo de lesión o "
          f"sobreentrenamiento {cita('Gabbett2016')}; {cita('Hulin2016')}— o por "
          "defecto, con pérdida de progreso. En un sistema que además detecta y "
          "registra molestias articulares, una afirmación falsa sobre el estado de "
          "una lesión tiene consecuencias potencialmente más serias que una cifra "
          "de peso equivocada.", ""), vinetas=True)
    rico(("Eje de privacidad. ", "b"),
         ("El sistema procesa datos de salud identificables —peso, composición "
          "corporal, lesiones, rendimiento físico, asociados a un número de "
          "teléfono— y los procesa íntegramente en local: ningún dato del atleta "
          "sale de la máquina que ejecuta el modelo. Esta decisión de "
          "arquitectura, poco común en productos de inteligencia artificial "
          "conversacional comerciales, tiene implicaciones directas de "
          f"cumplimiento normativo {cita('LFPDPPP2010')}; {cita('RGPD2016')}.", ""),
         vinetas=True)
    rico(("Eje de ingeniería de software. ", "b"),
         ("El patrón central del proyecto —código determinista como única fuente "
          "de cálculo, el modelo de lenguaje restringido a comunicar resultados ya "
          "producidos, y redes de validación que auditan la salida contra el "
          "estado real del sistema— no es específico del entrenamiento de fuerza. "
          "Es aplicable a cualquier agente conversacional que opere sobre un "
          "dominio donde el usuario no puede verificar por sí mismo lo que se le "
          "dice: asesoría financiera, salud, sistemas legales.", ""), vinetas=True)

    p("1.4 Objetivos", estilo="Heading 2")
    p("Objetivo general", estilo="Heading 3")
    p("Diseñar, implementar y evaluar un agente conversacional de entrenamiento de "
      "fuerza sobre un modelo de lenguaje ejecutado en local, cuya arquitectura "
      "garantice que ninguna cifra o afirmación de estado comunicada al atleta se "
      "origine en el modelo de lenguaje sin haber sido calculada o verificada por "
      "código determinista, y medir el coste de esa garantía en latencia, "
      "complejidad y cobertura de casos.")
    p("Objetivos específicos", estilo="Heading 3")
    tabla(
        ["#", "Objetivo específico", "Evidencia de cumplimiento"],
        [
            ["1", "Diseñar una arquitectura que separe el cálculo determinista de "
                  "la generación de lenguaje en cada turno de conversación",
             "Secciones 3.1 y 3.2"],
            ["2", "Implementar interceptores deterministas que resuelvan del "
                  "lenguaje natural las acciones de registro, sin depender de que "
                  "el modelo decida ejecutarlas", "Sección 3.3; 32 suites de prueba"],
            ["3", "Diseñar y medir la eficacia de redes de validación de salida "
                  "que detecten alucinaciones específicas del dominio",
             "Secciones 3.4 y 5"],
            ["4", "Fundamentar las decisiones de los motores de dominio en marcos "
                  "establecidos de la ciencia del entrenamiento",
             "Secciones 2.1 y 3.5"],
            ["5", "Extender la interpretación multimodal (voz y vídeo) para que el "
                  "registro no dependa de texto tecleado", "Sección 3.6"],
            ["6", "Medir el coste de la garantía de veracidad en latencia y "
                  "concurrencia", "Sección 5"],
            ["7", "Situar el patrón resultante frente al estado del arte",
             "Secciones 2.5 y 4"],
        ],
        titulo="Objetivos específicos y sección donde se evidencia su cumplimiento.",
        anchos=[1.0, 8.5, 5.5], tam=9.5)
    salto()


# ═══════════════════════════════════════════════ 2. REVISIÓN DOCUMENTAL ════
def revision_documental():
    p("2. Revisión documental", estilo="Heading 1")
    p("Esta sección reúne los fundamentos teóricos sobre los que se apoya cada "
      "decisión de diseño del sistema, y cierra con una revisión de las soluciones "
      "existentes frente a las que este trabajo se posiciona.")

    p("2.1 Fundamentos de la ciencia del entrenamiento de fuerza", estilo="Heading 2")

    p("2.1.1 Repetición máxima y su estimación indirecta", estilo="Heading 3")
    p("La repetición máxima (1RM) —la carga con la que un atleta puede completar "
      "exactamente una repetición con técnica válida— es la unidad de referencia "
      "estándar para prescribir intensidad relativa. Medirla directamente implica "
      "un riesgo de lesión y un coste de fatiga que no se justifican en el uso "
      "rutinario, por lo que la práctica habitual es estimarla a partir de series "
      "submáximas mediante ecuaciones de regresión ajustadas empíricamente.")
    p("El sistema implementa seis de estas ecuaciones: "
      f"{cita('Brzycki1993')}, {cita('Epley1985')}, {cita('Lombardi1989')}, "
      f"{cita('Mayhew1992')}, {cita('OConner1989')} y {cita('Wathan1994')}. "
      "Ninguna es precisa en todo el rango de repeticiones: la literatura que las "
      f"compara {cita('LeSuer1997')} muestra que el error de cada ecuación varía "
      "con el rango evaluado y con el ejercicio. Esta constatación fundamenta una "
      "de las decisiones originales del sistema, descrita en la sección 4.2.")

    p("2.1.2 Esfuerzo percibido y repeticiones en reserva", estilo="Heading 3")
    p("La escala de esfuerzo percibido específica de fuerza (RPE) y su métrica "
      "complementaria de repeticiones en reserva (RIR) permiten autorregular la "
      "carga sin depender de un porcentaje fijo de 1RM: el atleta reporta cuánto "
      "esfuerzo le costó la serie y de ahí se infiere cuántas repeticiones más "
      f"habría podido completar {cita('Zourdos2016')}; {cita('Helms2016')}. Es una "
      "escala validada específicamente para entrenamiento de fuerza, a diferencia "
      f"de la escala de Borg, de origen cardiovascular {cita('Hackett2012')}.")

    p("2.1.3 Volumen de entrenamiento: MEV, MAV y MRV", estilo="Heading 3")
    p("El marco de volumen mínimo efectivo (MEV), volumen máximo adaptativo (MAV) "
      f"y máximo volumen recuperable (MRV) {cita('Israetel2015')}, con evidencia "
      "de respaldo en revisiones sistemáticas sobre la relación dosis-respuesta "
      f"entre volumen semanal e hipertrofia {cita('Schoenfeld2017')}; "
      f"{cita('BazValle2022')}; {cita('Schoenfeld2016')}, gobierna tanto la "
      "generación del plan como el análisis de si el volumen ejecutado ha superado "
      "lo recuperable. Es importante precisar su estatus: se trata de un marco "
      "práctico derivado de la síntesis de evidencia y experiencia de campo, no de "
      "un conjunto de umbrales fijado por consenso experimental cerrado.")

    p("2.1.4 Periodización ondulante diaria", estilo="Heading 3")
    p("La periodización ondulante diaria (DUP) varía el énfasis de intensidad y "
      "volumen de sesión a sesión, en contraste con la periodización lineal "
      "clásica que lo varía de bloque a bloque; existe evidencia de que iguala o "
      "supera a la lineal en ganancias de fuerza a igualdad de volumen e "
      f"intensidad totales {cita('Rhea2002')}. El sistema implementa una variante "
      "propia que combina bloques macro con una proporción de días ondulantes por "
      "bloque, sintetizando el principio de gestión de fatiga de la periodización "
      f"en bloques {cita('Issurin2010')} con la alternancia de énfasis de la DUP.")

    p("2.1.5 Carga de entrenamiento y riesgo: la ratio aguda/crónica", estilo="Heading 3")
    p("La ratio de carga aguda/crónica (ACWR) compara la carga reciente (ventana "
      "de 7 días) contra la carga acumulada de fondo (28 días), con el objetivo de "
      "detectar picos asociados a mayor riesgo de lesión "
      f"{cita('Gabbett2016')}; {cita('Hulin2016')}. El sistema la calcula con "
      "medias móviles exponenciales siguiendo la formulación de "
      f"{cita('Williams2017')}, que corrige el sesgo de las medias móviles simples "
      "originales.")
    p("El ACWR es, sin embargo, una métrica activamente cuestionada: "
      f"{cita('Impellizzeri2020')} documentan problemas conceptuales de fondo, "
      "entre ellos que la ratio no distingue si el riesgo proviene de carga alta "
      "reciente o de carga crónica baja, y que su poder predictivo se ha "
      "sobreestimado en varios de los estudios que la popularizaron. El sistema "
      "adopta explícitamente esa cautela: emplea el ACWR como una señal más entre "
      "varias —junto al esfuerzo a cargas emparejadas y los reportes de dolor "
      "articular— y nunca como umbral de decisión autónomo.")

    p("2.1.6 Entrenamiento basado en velocidad", estilo="Heading 3")
    p("El entrenamiento basado en velocidad (VBT) parte de que la velocidad de "
      "ejecución de una repetición está inversamente relacionada con el porcentaje "
      "de 1RM que representa la carga, de forma razonablemente estable entre "
      f"sesiones para un mismo atleta y ejercicio {cita('GonzalezBadillo2010')}. "
      "Esto permite estimar el %1RM del día a partir de la velocidad, y usar la "
      "pérdida de velocidad intra-serie como indicador directo de fatiga "
      f"neuromuscular sin depender del reporte subjetivo {cita('SanchezMedina2011')}. "
      "La pérdida de velocidad como criterio de parada de serie modula además el "
      "balance entre ganancia de rendimiento y fatiga acumulada del mesociclo "
      f"{cita('ParejaBlanco2017')}; {cita('Weakley2021')}.")

    p("2.2 Modelos de lenguaje, agentes y llamada a herramientas", estilo="Heading 2")
    p("Los modelos de lenguaje de gran escala modernos se basan en la arquitectura "
      f"transformer {cita('Vaswani2017')}, escalada a miles de millones de "
      "parámetros y entrenada sobre corpus masivos con capacidad emergente de "
      f"seguir instrucciones {cita('Brown2020')}. Ejecutar un modelo de 27 000 "
      "millones de parámetros en una tarjeta gráfica de consumo de 24 GB exige "
      "cuantización: reducir la precisión numérica de los pesos a costa de una "
      f"pérdida acotada {cita('Dettmers2022')}; {cita('Frantar2023')}. El modelo "
      f"empleado pertenece a la familia Qwen {cita('Qwen2025')}.")
    p("El rendimiento del sistema depende críticamente de la caché de prefijo: el "
      "motor de inferencia reutiliza el cómputo de atención de los tokens que dos "
      "peticiones consecutivas comparten al inicio del prompt, en lugar de "
      f"recalcularlo —técnica de la misma familia que {cita('Kwon2023')} y "
      f"{cita('Zheng2024')}—. Para que la caché acierte, el prompt del turno N "
      "debe ser prefijo textual exacto del prompt del turno N+1, restricción que "
      "condiciona decisiones de diseño descritas en la sección 3.")
    p("Un agente basado en un modelo de lenguaje que puede invocar funciones "
      "externas combina la comprensión de lenguaje con la capacidad de actuar "
      "sobre un sistema real, típicamente mediante un ciclo de razonar y actuar "
      f"{cita('Yao2023')} o mediante modelos entrenados explícitamente para decidir "
      f"cuándo invocar una herramienta {cita('Schick2023')}; {cita('Qin2024')}. La "
      "capacidad de razonamiento intermedio del modelo puede además explicitarse "
      f"para mejorar la calidad de la decisión {cita('Wei2022')}; una revisión "
      f"del panorama completo de agentes autónomos se encuentra en {cita('Wang2024')}.")

    p("2.3 Alucinación en modelos de lenguaje y su mitigación", estilo="Heading 2")
    p("La alucinación —contenido generado que no está respaldado por la entrada ni "
      "por conocimiento verificable— es un problema estructural de los modelos "
      "generativos, no un defecto de implementación corregible con más datos de "
      f"entrenamiento {cita('Ji2023')}; {cita('Huang2025')}; {cita('Maynez2020')}. "
      "Las estrategias de mitigación documentadas se agrupan en tres familias:")
    rico(("Anclaje (grounding): ", "b"),
         ("proveer al modelo el contexto factual correcto y restringirlo a usarlo; "
          "es la base de la generación aumentada por recuperación "
          f"{cita('Lewis2020')}; {cita('Gao2023')}.", ""), vinetas=True)
    rico(("Verificación posterior: ", "b"),
         ("auditar la respuesta generada contra una fuente externa, con métodos "
          "que van desde la consistencia entre múltiples muestreos "
          f"{cita('Manakul2023')} hasta medidas de incertidumbre semántica del "
          f"propio modelo {cita('Farquhar2024')}.", ""), vinetas=True)
    rico(("Restricción de la generación: ", "b"),
         ("limitar el espacio de salidas posibles mediante gramáticas o esquemas "
          "forzados.", ""), vinetas=True)
    p("Las seis redes de validación de este sistema (sección 3.4) pertenecen a la "
      "segunda familia, con una particularidad que la sección 4 desarrolla como "
      "aportación: verifican contra una fuente de verdad determinista y objetiva "
      "—el estado real del sistema en disco— y no contra otra pasada del propio "
      "modelo generando una segunda opinión.")

    p("2.4 Visión por computadora, habla y aprendizaje automático", estilo="Heading 2")
    p("La estimación de pose humana a partir de vídeo permite extraer cinemática "
      "articular sin sensores. El sistema emplea MediaPipe Pose "
      f"{cita('Lugaresi2019')}, basado en la arquitectura BlazePose "
      f"{cita('Bazarevsky2020')}, y un detector YOLO {cita('Redmon2016')}; "
      f"{cita('Ultralytics_YOLO')} para el seguimiento del disco de la barra. La "
      f"transcripción de notas de voz usa Whisper {cita('Radford2023')}. El "
      "clasificador de ejercicios propio enfrenta cinco algoritmos de "
      f"clasificación supervisada, entre ellos Gradient Boosting {cita('Friedman2001')}, "
      f"implementados con scikit-learn {cita('Pedregosa2011')}. El motor "
      "predictivo incorpora un componente de regresión bayesiana que modela la "
      f"incertidumbre de la predicción {cita('MacKay1992')}; {cita('Bishop2006')}.")
    p("Por último, la literatura de ingeniería de software para sistemas de "
      "aprendizaje automático documenta que la deuda técnica y el comportamiento "
      "de estos sistemas rara vez se anticipan por completo en el diseño y con "
      f"frecuencia se descubren en producción {cita('Sculley2015')}; "
      f"{cita('Amershi2019')}, lo que fundamenta el enfoque metodológico descrito "
      "en la sección 6.")

    p("2.5 Trabajos previos y soluciones existentes", estilo="Heading 2")
    p("Las aplicaciones comerciales de entrenamiento de fuerza pueden clasificarse "
      "según cuatro ejes: si planifican o solo registran, si autorregulan por "
      "esfuerzo, si emplean lenguaje natural, y dónde residen los datos.")
    tabla(
        ["Producto", "¿Planifica?", "¿Autorregula?", "¿Lenguaje natural?", "Datos"],
        [
            ["Strong / Hevy", "Solo plantillas fijas", "No", "No", "Nube"],
            ["JuggernautAI", "Sí, por reglas", "Parcial, por formulario", "No", "Nube"],
            ["RP Hypertrophy App", "Sí, marco MEV/MAV/MRV", "Sí, por formulario", "No", "Nube"],
            ["Fitbod", "Sí, por disponibilidad", "Parcial", "No", "Nube"],
            ["Boostcamp", "Programas de terceros", "No", "No", "Nube"],
            ["**Iron Progress**", "**Sí, DUP adaptativa**", "**Sí, por lenguaje libre**",
             "**Sí**", "**Local**"],
        ],
        titulo="Comparativa de soluciones existentes de entrenamiento de fuerza.",
        anchos=[3.6, 3.2, 3.4, 2.6, 2.2], tam=9)
    p("Ningún producto de esta categoría combina planificación adaptativa real con "
      "una interfaz de lenguaje natural conversacional. La autorregulación, cuando "
      "existe, se captura mediante formularios estructurados, no mediante "
      "interpretación de texto libre como «me quedaron dos en el tanque».")
    p("En cuanto a los sistemas de entrenamiento basado en velocidad, la "
      "tecnología de referencia son los encoders lineales y los acelerómetros "
      "adheridos a la barra, contra los cuales se validan por concordancia otros "
      f"métodos de medición {cita('GonzalezBadillo2010')}; {cita('Weakley2021')}. "
      "El motor de este proyecto toma un enfoque distinto —estimar velocidad y "
      "trayectoria a partir del vídeo de un teléfono, sin sensor alguno— cuya "
      "contrapartida honesta se declara en la sección 5.")
    aviso("Nota metodológica sobre esta subsección.",
          "Las descripciones de productos comerciales de la tabla anterior se "
          "elaboraron a partir del conocimiento general del dominio y no se "
          "verificaron contra la documentación oficial vigente de cada producto "
          "durante la redacción. Antes de la presentación final del reporte se "
          "recomienda contrastar cada celda con la fuente oficial correspondiente "
          "y añadir la fecha de consulta.", color="EAF1FA", borde=AZUL)
    salto()


# ═════════════════════════════════════════════ 3. SOLUCIÓN Y PROPUESTA ════
def solucion_y_propuesta():
    p("3. Solución y propuesta", estilo="Heading 1")

    p("3.1 Arquitectura general del sistema", estilo="Heading 2")
    p("El sistema se organiza en cinco capas con dependencias unidireccionales: "
      "entrada y API, orquestación, motores de dominio y persistencia, con el "
      "canal de WhatsApp como frontera de entrada y salida. El orquestador es "
      "deliberadamente el único punto de acoplamiento: los motores de dominio no "
      "se conocen entre sí ni conocen la existencia del canal de mensajería, de "
      "modo que cada uno permanece aislado y comprobable por separado.")
    figura("d1_arquitectura.png",
           "Arquitectura en capas del sistema. Las flechas indican dependencia de "
           "invocación.")
    p("La elección de cada tecnología responde al patrón de acceso, no a "
      "preferencia:")
    tabla(
        ["Decisión", "Alternativa descartada", "Razón"],
        [
            ["SQLite", "PostgreSQL / MySQL",
             "Concurrencia de escritura baja por diseño y un solo proceso de "
             "trabajo; el cuello de botella medido está en la inferencia, no en la "
             "base de datos. Simplifica el despliegue local exigido por la "
             "restricción de privacidad."],
            ["ChromaDB", "FAISS / Milvus",
             "FAISS es una librería de índice sin persistencia ni gestión de "
             "colecciones; Milvus es un sistema distribuido sobredimensionado para "
             "tres colecciones de bajo volumen en una sola máquina."],
            ["Ollama", "vLLM / TGI / llama.cpp directo",
             "vLLM y TGI optimizan el servicio de lotes grandes en GPU de centro "
             "de datos; con una única inferencia activa a la vez esa ventaja no "
             "aplica. Ollama aporta gestión de modelos y caché de prefijo con una "
             "API simple."],
            ["FastAPI", "Flask / Django",
             "El webhook exige responder de inmediato y procesar en segundo plano; "
             "FastAPI ofrece tareas de fondo y soporte asíncrono nativo."],
        ],
        titulo="Justificación de las principales decisiones tecnológicas.",
        anchos=[2.8, 3.6, 8.6], tam=9)

    p("Atributos de calidad", estilo="Heading 3")
    tabla(
        ["Atributo", "Cómo se sostiene en la arquitectura"],
        [
            ["Privacidad", "Inferencia, embeddings, transcripción y visión "
             "corren en local; sin llamadas salientes salvo la API del canal "
             "de mensajería"],
            ["Auditabilidad", "Toda cifra que llega al atleta es trazable a "
             "código determinista o a una herramienta con resultado registrado "
             "(secciones 3.2-3.4)"],
            ["Disponibilidad ante fallos parciales",
             "El webhook responde de inmediato y encola el procesamiento; un "
             "fallo en una etapa no bloquea la recepción de nuevos mensajes"],
            ["Recuperabilidad del estado",
             "El sistema asume que el estado en disco puede estar corrupto y "
             "lo repara en el arranque de cada turno, no solo del proceso"],
            ["Coste operativo bajo",
             "Todos los componentes corren en el mismo proceso o en procesos "
             "locales, sin infraestructura de red adicional que operar"],
        ],
        titulo="Atributos de calidad de la arquitectura y su mecanismo de "
               "sostén.",
        anchos=[4.0, 11.0], tam=9.5)
    p("Manejo de errores por etapa del ciclo de vida de un mensaje", estilo="Heading 3")
    p("Un sistema que asume que nada falla no es una arquitectura robusta, es "
      "una arquitectura sin probar. La siguiente tabla documenta qué ocurre "
      "cuando cada etapa del procesamiento de un mensaje entrante falla, no "
      "solo cuando todo sale bien.")
    tabla(
        ["Etapa que puede fallar", "Comportamiento ante el fallo"],
        [
            ["Validación de firma criptográfica del webhook",
             "Si el secreto no está configurado, se acepta cualquier "
             "petición —válido en desarrollo, riesgo explícito en "
             "producción—; si el secreto existe y la firma no coincide, se "
             "rechaza antes de tocar ningún dato del atleta"],
            ["Descarga de medios (audio o vídeo) desde la plataforma",
             "Encapsulada en su propio bloque de manejo de excepciones; un "
             "fallo de red no propaga una excepción no controlada al bucle "
             "de eventos, y el atleta recibe un mensaje de error explícito "
             "en vez de quedarse sin respuesta"],
            ["Transcripción de audio",
             "Se lanza como tarea adelantada para solapar la descarga con "
             "el cómputo; un fallo del modelo de audio no bloquea el resto "
             "del turno"],
            ["Clasificación de vídeo o motor de análisis de velocidad",
             "Un cerrojo de serialización aísla el fallo de un atleta sin "
             "corromper el análisis de otro (sección 3.6.2)"],
            ["Inferencia principal del modelo de lenguaje",
             "Un límite explícito de tiempo (180 s) impide que una llamada "
             "cuelgue el orquestador indefinidamente; ante un tiempo "
             "agotado o una respuesta truncada, un mecanismo de rescate "
             "descrito más abajo reintenta sin herramientas ni razonamiento"],
            ["Envío de la respuesta final al canal de mensajería",
             "Es la última etapa; si falla el envío después de haber "
             "persistido ya la respuesta en el historial, existe una "
             "divergencia potencial entre lo que el sistema cree haber "
             "dicho y lo que el atleta realmente recibió. No se encontró en "
             "el código un mecanismo de reconciliación para este caso "
             "concreto — se reporta como hallazgo, no como algo resuelto"],
        ],
        titulo="Comportamiento del sistema ante el fallo de cada etapa del "
               "ciclo de vida de un mensaje.",
        anchos=[5.5, 9.5], tam=8.5)

    p("Anatomía de un turno de conversación", estilo="Heading 3")
    p("El módulo orquestador —7 792 líneas, el componente central del sistema— no "
      "resuelve un turno en una única función lineal: reparte la lógica entre la "
      "función de entrada, con un cerrojo por atleta, y una veintena de funciones "
      "auxiliares a las que delega en secuencia. La siguiente tabla documenta esa "
      "secuencia real, verificada línea por línea contra el código, agrupada en "
      "cinco bloques funcionales.")
    tabla(
        ["Bloque", "Fase", "Mecanismo"],
        [
            ["Preparación", "Carga de historial con detección de cambio externo",
             "Comparación de firma de fichero"],
            ["", "Espera de compresión de historial en curso",
             "`_esperar_compresion_en_curso`"],
            ["", "Reparación de estados corruptos",
             "`_actualizar_estado_json` (8+ puntos de guarda)"],
            ["Máquina de\nestados", "Cálculo del campo de onboarding pendiente",
             "`calcular_faltantes`"],
            ["", "Disparo de la generación del plan en segundo plano",
             "`_disparar_pipeline_generacion`"],
            ["", "Sello del ejercicio en curso (doble ventana temporal)",
             "`fijar_ejercicio_confirmado`"],
            ["Contexto", "Construcción de la telemetría del turno",
             "`construir_telemetria`"],
            ["", "Recuperación vectorial (expediente, bitácora, biblioteca)",
             "`construir_contexto_enriquecido`"],
            ["", "Lectura de la rutina resuelta (porcentaje → kilogramos)",
             "`obtener_rutina_del_json`"],
            ["", "Clasificación de la intención del turno",
             "`_clasificar_meta`"],
            ["Ejecución", "Inferencia principal, con las 23 herramientas disponibles",
             "`chat_llm(...)`"],
            ["", "Bucle de herramientas, máximo tres vueltas",
             "segunda llamada a `chat_llm` dentro del bucle"],
            ["", "Rescate ante respuesta truncada o vacía",
             "`_forzar_respuesta_texto`, sin herramientas ni razonamiento"],
            ["Validación", "Post-proceso del texto para el canal de WhatsApp",
             "`limpiar_texto_para_whatsapp`"],
            ["", "Redes 1-6 de validación de salida (sección 3.4)",
             "cadena de verificaciones tras la redacción"],
            ["", "Persistencia del orquestador (mensajes tal como se enviaron)",
             "requisito de la caché de prefijo, sección 2.2"],
            ["", "Compresión diferida del historial cuando corresponde",
             "`_lanzar_compresion_diferida`"],
        ],
        titulo="Anatomía verificada de un turno de conversación, 17 fases con "
               "ancla directa en el código fuente.",
        anchos=[2.6, 8.0, 4.4], tam=8.5)
    p("Esta tabla documenta 17 fases con función y evidencia de código "
      "verificables, agrupadas en los mismos cinco bloques funcionales que "
      "describía la documentación técnica previa del proyecto bajo el nombre de "
      "«23 fases» — la diferencia numérica no es una corrección de un error, "
      "sino un cambio de método: contar funciones con ancla verificable en el "
      "código, no un número transcrito de memoria.")

    p("3.2 El principio de diseño central: separar el cálculo del lenguaje",
      estilo="Heading 2")
    p("La arquitectura reparte la responsabilidad de forma explícita. Código "
      "Python determinista interpreta el mensaje, ejecuta las escrituras y calcula "
      "los números. El modelo recibe el resultado ya consumado y su única tarea es "
      "comunicarlo con tono de entrenador. Cuando el modelo necesita un dato que "
      "no tiene, no lo estima: invoca una herramienta.")
    figura("d3_reparto.png",
           "Reparto de responsabilidad entre el código determinista y el modelo de "
           "lenguaje.")
    p("Esta separación tiene un corolario incómodo pero necesario: cuando el "
      "sistema carece de una capacidad, hay que construirla en lugar de dejar que "
      "el modelo improvise. Durante la verificación se detectó que el sistema "
      "inventaba el volumen semanal de su propio plan; la causa no era negligencia "
      "del modelo sino la ausencia de cualquier fuente que respondiera esa "
      "pregunta, y contar las series leyendo el texto del plan es precisamente la "
      "aritmética que el diseño le prohíbe. La solución fue añadir una herramienta "
      "que responde con exactitud.")
    p("El plan generado refuerza el mismo principio: almacena porcentajes de 1RM, "
      "nunca kilogramos absolutos. Las cargas se calculan en el momento de la "
      "lectura multiplicando el porcentaje por la marca vigente del atleta y "
      "redondeando al incremento real de su barra. La consecuencia práctica es "
      "notable: cuando el atleta firma un récord, todo el mesociclo se reajusta "
      "sin regenerar nada, porque el plan nunca contuvo cifras absolutas.")

    p("3.3 Los interceptores deterministas", estilo="Heading 2")
    p("Son el mecanismo que materializa el principio anterior. Antes de que el "
      "modelo intervenga, código Python analiza el mensaje, ejecuta las escrituras "
      "que correspondan e inyecta el resultado en el contexto acompañado de una "
      "instrucción de redacción. El modelo no decide si la serie se guarda: se "
      "guardó ya, y su tarea es confirmarlo.")
    figura("d2_ciclo_mensaje.png",
           "Las quince etapas del ciclo de vida de un mensaje entrante.")
    tabla(
        ["Interceptor", "Disparador", "Efecto"],
        [
            ["Series", "Notación de peso y repeticiones",
             "Persiste la serie, calcula el 1RM y sella el ejercicio"],
            ["Marca histórica", "«mi PR», «mi récord», «el año pasado»",
             "Actualiza el 1RM sin tocar el diario de entrenamiento"],
            ["RPE de alarma", "Esfuerzo percibido ≥ 9,5",
             "Ejecuta la autorregulación del día en curso"],
            ["Peso corporal", "Peso entre 35 y 300 kg sin unidad de carga",
             "Registra el peso y actualiza la ficha"],
            ["Equipamiento", "Mención de discos disponibles",
             "Ajusta el incremento de barra para redondear cargas"],
            ["Sustitución", "Petición de cambio de ejercicio",
             "Valida contra el plan real; rechaza los inexistentes"],
            ["Recuperación", "«ya no me duele», «se me quitó»",
             "Marca la lesión resuelta y prohíbe mencionarla"],
            ["Consulta de rutina", "Referencia a un día o semana concretos",
             "Resuelve el cursor e inyecta la rutina real"],
            ["Charla casual", "Mensaje trivial sin datos",
             "Desactiva herramientas, recuperación y razonamiento"],
        ],
        titulo="Inventario de interceptores deterministas.",
        anchos=[3.4, 5.4, 6.2], tam=9)
    p("Un detalle de diseño ilustra el nivel de cuidado que exige interpretar "
      "lenguaje real: nadie repite el nombre del ejercicio en cada serie. Tras "
      "reportar una serie de banca, la siguiente llega como «otra igual, 100 por "
      "4». El sistema sella el ejercicio en curso con dos ventanas temporales "
      "distintas: treinta minutos para heredarlo cuando el mensaje no lo nombra, "
      "pero solo tres minutos para que el nombre que traiga el modelo pueda "
      "sobrescribir el sello. Pasado ese margen se asume que el atleta cambió de "
      "ejercicio en el gimnasio y el sello previo deja de ser fiable.")
    p("El tokenizador de series: anclaje por unidad, no por posición", estilo="Heading 3")
    p("El interceptor de series no asume que el peso vaya antes que las "
      "repeticiones ni que el primer número de la frase sea el relevante. Una "
      "versión anterior sí lo asumía, y el propio código conserva el incidente "
      "real que motivó la corrección, con la cifra exacta del error:")
    par = doc.add_paragraph(style="CitaLarga")
    par.add_run("«‘dominadas con 20 kilos de lastre saqué 6’ → (20, 1) → 1RM = "
                "20 kg. El 6 estaba ahí, en el mismo mensaje, y se descartaba en "
                "silencio devolviendo éxito. El atleta quedaba registrado ~4 kg "
                "por debajo de su fuerza real y todas sus cargas de dominadas "
                "salían bajas, sin un solo error en ningún log.»").italic = True
    p("La versión anterior tomaba el primer número de la frase y asumía una "
      "repetición; el «6» de «saqué 6» quedaba huérfano y se perdía sin generar "
      "ninguna excepción —el caso más peligroso de fallo silencioso, porque el "
      "sistema reporta éxito—. La corrección ancla cada número por la unidad o "
      "el verbo que lo acompaña: «20 kilos» se reconoce como peso por la unidad "
      "explícita, «saqué 6» se reconoce como repeticiones por el verbo de "
      "ejecución que lo precede, y «3 series de 10 reps» descarta el 3 "
      "explícitamente para no confundirlo con las repeticiones reales. Cuando un "
      "número no encaja con ninguna de estas anclas, el sistema no lo estima ni "
      "lo descarta en silencio: devuelve el motivo concreto para que el coach "
      "pregunte solo el dato que falta, la misma disciplina de declarar la "
      "incertidumbre en vez de inventarla que gobierna el resto del sistema.")

    p("3.4 Las seis redes de validación de salida", estilo="Heading 2")
    p("El borrador que produce el modelo no se envía directamente. Atraviesa seis "
      "redes independientes, cada una diseñada a partir de un fallo observado en "
      "conversaciones reales. Si alguna detecta un problema, se fuerza una "
      "reescritura inyectando la verdad del turno; si el modelo reincide, responde "
      "una plantilla honesta escrita en Python, lo que garantiza la terminación "
      "sin bucles en un máximo de dos pasadas.")
    figura("d4_validadores.png",
           "Las redes de validación de salida y el mecanismo de reescritura.")
    tabla(
        ["#", "Red", "Qué detecta", "Fuente de verdad contra la que audita"],
        [
            ["1", "Kilajes inventados",
             "Cifras en kg que no aparecen en la lista blanca del turno",
             "Plan, panel de series, resultados de herramientas, historial"],
            ["2", "Rutina fantasma",
             "Texto con forma de prescripción sin plan generado",
             "Existencia del plan en disco"],
            ["3", "Reparador de onboarding",
             "Pregunta por un campo inexistente o ya respondido",
             "Ficha del atleta"],
            ["4", "Claim falso",
             "Afirmar «ya lo registré» sin escritura completada",
             "Registro de herramientas ejecutadas en el turno"],
            ["5", "Estado del plan",
             "Dar por aplicada una descarga, pausa o avance de día",
             "Estado real del plan en disco"],
            ["6", "Anti-eco",
             "Respuesta idéntica a la anterior",
             "Historial de conversación"],
        ],
        titulo="Las seis redes de validación de salida y su fuente de verdad.",
        anchos=[0.9, 3.2, 5.4, 5.5], tam=9)
    p("La cuarta red merece mención aparte porque revela un problema de detección "
      "específico del español. La expresión regular que reconoce afirmaciones de "
      "acción consumada ancla los verbos a sus terminaciones de pretérito y "
      "participio, con tilde obligatoria en la primera persona del singular. La "
      "razón es que el español distingue «aplique» (subjuntivo, un ofrecimiento) "
      "de «apliqué» (pretérito, una afirmación de hecho) únicamente por la tilde. "
      "Admitir la terminación sin acentuar convertía cada propuesta del sistema en "
      "un falso positivo y le obligaba a reescribir mensajes que eran honestos.")

    p("3.5 Los motores de dominio", estilo="Heading 2")
    p("3.5.1 Motor de planificación", estilo="Heading 3")
    p("Genera el mesociclo completo de forma determinista en todo lo que decide "
      "series, repeticiones, intensidades y orden de los días. El modelo de "
      "lenguaje interviene en un único punto acotado —filtrar del catálogo la "
      "lista de ejercicios compatibles con el material y las preferencias del "
      "atleta— y su salida se valida contra el catálogo antes de usarse.")
    figura("d6_pipeline_plan.png",
           "Del onboarding al plan en disco, con el reparto de decisión en cada paso.")
    p("El reparto de volumen semanal interpola desde el volumen máximo adaptativo "
      "hacia el mínimo efectivo a lo largo del bloque, y no divide ese volumen por "
      "igual entre las sesiones: lo pondera según el tipo de día, de modo que los "
      "días de hipertrofia absorben entre el 50 y el 60 por ciento de las series "
      "semanales, los de fuerza entre el 20 y el 25, y los de potencia entre el 15 "
      "y el 20. Una guardia adicional prohíbe programar dos días de fuerza pesada "
      "en fechas de calendario consecutivas, y su estado se comparte entre los "
      "grupos musculares porque la fatiga central es sistémica.")

    p("3.5.2 Motor predictivo", estilo="Heading 3")
    p("Encapsula la analítica de entrenamiento: estima la repetición máxima, "
      "calcula la ratio de carga aguda/crónica, determina si el volumen ha "
      "superado el máximo recuperable y autorregula la sesión en curso. Combina "
      "cálculos deterministas con un modelo bayesiano que solo entra en juego "
      "cuando hay historial suficiente y cuya predicción se descarta si su "
      "incertidumbre excede un umbral relativo.")
    p("Formulación matemática del suavizado exponencial", estilo="Heading 3")
    p("Tanto el 1RM suavizado como la ratio de carga aguda/crónica se calculan con "
      "una media móvil ponderada exponencialmente (EWMA) de forma recursiva:")
    codigo(
        "  α = 1 − 0,5^(1/h)              (h = semivida, en número de observaciones)\n"
        "  EWMA₀ = valor₀\n"
        "  EWMAₜ = α·valorₜ + (1−α)·EWMAₜ₋₁")
    p("Para la ratio aguda/crónica, la señal de entrada es el tonelaje diario total "
      "del atleta (peso por repeticiones, sumado sobre todas las series del día, "
      "incluidos los días sin entrenar como tonelaje cero), y se calculan dos EWMA "
      "en paralelo con semividas que corresponden a las ventanas clásicas de la "
      f"literatura {cita('Williams2017')}:")
    codigo(
        "  λ_agudo   = 2 / (7+1)  = 0,25       (ventana de 7 días)\n"
        "  λ_crónico = 2 / (28+1) = 0,069      (ventana de 28 días)\n\n"
        "  ACWR = EWMA_agudo(tonelaje) / EWMA_crónico(tonelaje)")
    p("El cálculo requiere un mínimo de seis series registradas; por debajo de ese "
      "umbral devuelve un valor nulo explícito —«datos insuficientes»— en lugar de "
      "una cifra que aparente precisión sin tenerla.")

    p("El piso de progresión determinista: un error real, corregido y documentado",
      estilo="Heading 3")
    p("Un error real detectado durante el desarrollo ilustra por qué el componente "
      "determinista sigue siendo necesario incluso con un modelo estadístico "
      "encima. El 1RM estimado se suaviza con una media móvil exponencial de "
      "semivida de cuatro sesiones, lo que la hace perseguir al atleta con "
      "retraso. El propio comentario del código, conservado tal cual por su valor "
      "documental, describe el problema con precisión:")
    par = doc.add_paragraph(style="CitaLarga")
    par.add_run("«El e1RM va por EWMA de halflife 4 sesiones, o sea que persigue al "
                "atleta con retraso: con una progresión lineal limpia (100 → 102.5 → "
                "105 → 107.5 kg x5 @RPE8) el EWMA se quedaba en 126 kg de e1RM y la "
                "recomendación salía 100 kg — mandarle MENOS de lo que acababa de "
                "levantar.»").italic = True
    p("Es decir: un atleta que progresa de forma perfectamente saludable recibía, "
      "por el simple retraso estadístico inherente a una media móvil, una "
      "recomendación de carga inferior a la que acababa de completar con éxito. "
      "La corrección no fue confiar más en el modelo estadístico, sino añadir un "
      "piso determinista: si la última carga de referencia se completó a un "
      "esfuerzo igual o menor que el objetivo y la disponibilidad reportada es "
      "suficiente (≥ 0,6 en una escala de 0 a 1), esa carga pasa a ser el suelo de "
      "la recomendación —nunca el techo—; el componente estadístico puede sugerir "
      "subir por encima de ese suelo, pero nunca bajar de él salvo que el atleta "
      "reporte fatiga real.")

    p("El modelo bayesiano: cuándo interviene y cuándo se descarta", estilo="Heading 3")
    p("Cuando hay historial suficiente (ocho sesiones registradas o más), el motor "
      "complementa el piso determinista con un modelo de regresión bayesiana "
      "entrenado sobre las sesiones históricas del propio atleta, con seis "
      "variables de entrada: el 1RM suavizado, la pendiente de la tendencia "
      "reciente, los días de descanso desde la última sesión del ejercicio "
      "(acotados a 14), el esfuerzo percibido de la última serie de referencia, la "
      "ratio de carga aguda/crónica y la disponibilidad autorreportada. El modelo "
      "devuelve una media y una desviación estándar; el sistema mezcla esa "
      "predicción con el piso determinista a partes iguales únicamente si la "
      "incertidumbre relativa es aceptable, y la descarta por completo si no lo "
      "es. Por debajo de ocho sesiones, el componente bayesiano no se entrena en "
      "absoluto y el piso determinista opera solo.")
    p("Una limitación de canonización: grupos musculares opuestos con la misma "
      "clave", estilo="Heading 3")
    p("El análisis de volumen recuperable necesita agrupar ejercicios por grupo "
      "muscular. La canonización de nombres del sistema pierde información en "
      "casos concretos: tanto el curl de bíceps como el curl femoral —isquios y "
      "bíceps, grupos musculares opuestos— se reducen a la misma clave canónica "
      "de «curl» si se resuelven solo por esa clave. La función que agrupa por "
      "grupo muscular consulta primero el nombre completo del ejercicio, donde "
      "una palabra discriminante como «femoral» desambigua correctamente, y solo "
      "cae a la clave canónica genérica cuando esa palabra no está presente. Es "
      "una solución parcial —cualquier forma de nombrar el ejercicio que no "
      "incluya la palabra discriminante sigue perdiendo la distinción— y se "
      "declara así, sin presentarla como una corrección de fondo del esquema de "
      "canonización.")

    p("3.5.3 Motor de proactividad", estilo="Heading 3")
    p("Un sistema que solo reacciona a lo que el atleta escribe no ejerce "
      "plenamente de entrenador. El motor de proactividad cubre tres frentes "
      "mediante un planificador que arranca junto con la aplicación, en el mismo "
      "proceso —una decisión deliberada: acoplar un cron externo o una cola de "
      "tareas habría añadido infraestructura que resolver un solo disparo diario "
      "no justifica—.")
    p("Cuestionario matutino de disponibilidad.", negrita=True)
    p("A las 6:30 de la mañana (hora configurable, zona horaria de Ciudad de "
      "México por defecto) el sistema abre la conversación con una plantilla "
      "aprobada por la plataforma de mensajería, preguntando cómo durmió y cómo "
      "se siente el atleta — el único punto del sistema donde el coach inicia la "
      "conversación sin que el atleta haya escrito primero, posible únicamente "
      "porque es una plantilla pre-aprobada y no texto libre generado por el "
      "modelo (sección 3.10). La respuesta, por botón o por texto libre, se "
      "convierte en una puntuación de disponibilidad entre 0 y 1: las respuestas "
      "por botón mapean a un valor fijo, y las de texto libre pasan por una "
      "heurística que parte de un valor base derivado de las horas de sueño "
      "mencionadas y lo ajusta con patrones de lenguaje —penaliza menciones de "
      "fatiga, estrés o dolor, bonifica expresiones como «al 100» o «como "
      "nuevo»—. Esa puntuación alimenta directamente la telemetría del turno "
      "que ve el motor predictivo (sección 3.5.2).")
    p("Gráficas de progresión.", negrita=True)
    p("Se generan con la API orientada a objetos de la librería de graficación "
      "en lugar de su interfaz de estado global, una decisión explícita para "
      "evitar una condición de carrera real: el gestor global de figuras no es "
      "seguro entre hilos, y dos atletas solicitando su gráfica al mismo tiempo "
      "podían pisarse la una a la otra. Con la API orientada a objetos cada "
      "gráfica es una figura independiente que no necesita liberarse "
      "explícitamente del estado compartido.")
    p("Filtrado de destinatarios y aislamiento de fallos.", negrita=True)
    p("El envío matutino excluye explícitamente a los atletas con el plan "
      "pausado y a los que siguen en proceso de recolección de la ficha inicial "
      "—a ambos, preguntarles por su disponibilidad de hoy no tiene sentido—. "
      "Cada envío individual está aislado: si falla el de un atleta, el bucle "
      "continúa con el resto en lugar de abortar el envío completo, un patrón "
      "de aislamiento de fallos consistente con el resto de la capa de entrada "
      "descrita en la sección 3.1.")
    figura("d5_datos.png",
           "Los cuatro almacenes de persistencia y su contenido.")

    p("3.6 Capacidades multimodales: voz y visión", estilo="Heading 2")
    p("3.6.1 Notas de voz", estilo="Heading 3")
    p("La transcripción emplea un modelo Whisper de tamaño reducido cuantizado a "
      "ocho bits sobre CPU. Esta configuración no es la de mayor calidad "
      "disponible, y la razón es una restricción de recursos: la tarjeta gráfica "
      "está ocupada casi en su totalidad por el modelo de lenguaje principal, sin "
      "margen para un segundo modelo.")
    p("Sobre el transcrito —habitualmente caótico, grabado entre series, con "
      "muletillas y sin puntuación— opera primero un normalizador de números "
      "hablados, necesario porque el modelo de transcripción en español escribe "
      "los números como palabras aproximadamente la mitad de las veces, y los "
      "interceptores deterministas de la sección 3.3 no reconocen texto en esa "
      "forma. El propio código documenta el caso de prueba de referencia: "
      "«ciento dos y medio kilos por ocho» se normaliza a «102.5 kilos por 8» "
      "antes de que el tokenizador de series lo procese. Sobre el texto ya "
      "normalizado opera el mismo extractor que busca la tríada de ejercicio, "
      "peso y repeticiones con su esfuerzo percibido, cubierto por 46 casos de "
      "prueba deterministas centrados específicamente en formas habladas del "
      "esfuerzo percibido, y devuelve un eco de confirmación al atleta para que "
      "pueda corregir una transcripción mal entendida antes de que se persista.")

    p("3.6.2 Motor de análisis de vídeo", estilo="Heading 3")
    p("El vídeo de una serie produce dos resultados: un clasificador propio "
      "identifica el ejercicio a partir de la cinemática, y un análisis de "
      "velocidad calcula las métricas de entrenamiento basado en velocidad. "
      "Ninguno de los dos resultados los redacta el modelo de lenguaje.")
    p("Calibración robusta del seguimiento del disco.", negrita=True)
    p("El seguimiento del disco de la barra combina un detector YOLO con un "
      "algoritmo de seguimiento por fotogramas, y calibra la escala de píxeles a "
      "metros con un procedimiento en dos pasos: primero siembra la escala con la "
      "mediana —no la media, menos sensible a un valor atípico aislado— de los "
      "primeros discos detectados, y a partir de ahí la suaviza con una media "
      "móvil exponencial que rechaza los saltos de tamaño de caja físicamente "
      "imposibles entre fotogramas consecutivos. No usar el primer disco detectado "
      "como referencia es deliberado: si ese fotograma trae la caja de detección "
      "recortada o a medio aparecer, toda la serie queda escalada con un error "
      "sistemático.")
    p("Una corrección documentada y medida sobre grabaciones propias ilustra el "
      "coste de un filtro mal calibrado. El filtro original, diseñado para "
      "descartar discos apilados de calentamiento y no confundirlos con el disco "
      "de trabajo, también descartaba en el ejercicio de peso muerto el propio "
      "disco de trabajo durante el primer tirón: el detector identificaba "
      "correctamente el disco en el 99,6 % de los fotogramas evaluados, pero el "
      "motor, antes de la corrección, solo aprovechaba el 55,7 % de esas "
      "detecciones válidas. Es la evidencia experimental más concreta que el "
      "proyecto reporta sobre la calidad de su propio componente de visión.")
    p("Máquina de estados y separación de fases.", negrita=True)
    p("Una máquina de estados explícita —calibrando, reposo, excéntrica, "
      "concéntrica, finalizado— cuenta las repeticiones y cronometra tres fases: "
      "excéntrica, amortiguación en el punto más bajo y concéntrica. Una decisión "
      "de diseño no evidente separa el mínimo absoluto de la trayectoria de la "
      "barra —necesario para detectar cuándo invierte de dirección— del «tope del "
      "empuje», una variable que se congela en cuanto la barra deja de ganar "
      "altura útil: sin esa separación, el bloqueo final de la repetición, donde "
      "la barra ya no sube pero el atleta sigue en tensión, se contaba "
      "erróneamente como parte de la fase concéntrica e inflaba su duración "
      "medida.")
    p("Reconciliación honesta de estimadores en desacuerdo.", negrita=True)
    p("El sistema mantiene dos estimadores independientes de repeticiones en "
      "reserva —uno por la velocidad de la última repetición, otro por la pérdida "
      "de velocidad intra-serie— y los reconcilia tomando su intersección cuando "
      "coinciden. La versión anterior, ante un desacuerdo sin solape entre ambos, "
      "promediaba sus extremos y producía un rango que ningún estimador sostenía "
      "realmente, reportado con la misma confianza que un consenso genuino. La "
      "corrección aplicada devuelve en ese caso la envolvente de ambos rangos "
      "marcada explícitamente como incierta, siguiendo la misma filosofía que "
      "gobierna las redes de validación de la sección 3.4: declarar la "
      "incertidumbre es preferible a disfrazarla de precisión.")

    p("3.6.3 Clasificador de ejercicios", estilo="Heading 3")
    p("Para identificar el movimiento sin preguntárselo al atleta, se entrenó un "
      "modelo desde cero sobre un conjunto de datos propio: cada vídeo se pasa por "
      "un estimador de pose y su serie temporal completa se comprime en 23 "
      "variables —mínimo, máximo, rango, media y desviación de los ángulos de "
      "rodilla, cadera, codo y hombro, más tres estadísticos de distancia "
      "muñeca-cadera—.")
    tabla(
        ["Clase", "Vídeos", "% del total"],
        [
            ["Press de banca", "19", "38,8 %"],
            ["Sentadilla", "12", "24,5 %"],
            ["Peso muerto", "11", "22,4 %"],
            ["Dominadas", "7", "14,3 %"],
            ["**Total**", "**49**", "100 %"],
        ],
        titulo="Composición real del conjunto de datos de entrenamiento del "
               "clasificador, verificada directamente sobre el fichero de datos.",
        anchos=[6.0, 4.5, 4.5], tam=9.5)
    p("El conjunto está desbalanceado: la clase mejor representada tiene 2,7 veces "
      "más ejemplos que la peor. Cinco algoritmos de clasificación supervisada "
      f"—entre ellos Gradient Boosting {cita('Friedman2001')}, implementados con "
      f"scikit-learn {cita('Pedregosa2011')}— compiten sobre la misma partición "
      "80/20, con un umbral de confianza por debajo del cual la predicción se "
      "descarta como «ejercicio desconocido» en lugar de forzar una etiqueta. Los "
      "resultados completos de este torneo, reejecutado durante la elaboración de "
      "este reporte, se presentan en la sección 5.3.")

    p("3.7 Modelo de datos y persistencia", estilo="Heading 2")
    p("El estado del sistema se reparte entre cuatro almacenes, cada uno elegido "
      "por su patrón de acceso: un registro de eventos consultado por ventana "
      "temporal pide una base relacional; un documento inmutable que se lee "
      "completo pide un fichero; una consulta por significado semántico pide un "
      "índice vectorial. Esta sección documenta el esquema verificado "
      "directamente contra las bases del sistema en ejecución, no reconstruido de "
      "memoria.")
    codigo(
        "CREATE TABLE atletas (\n"
        "    numero_usuario   TEXT PRIMARY KEY,\n"
        "    peso_kg REAL, altura_m REAL, edad INTEGER, genero TEXT,\n"
        "    porcentaje_grasa REAL, dias_semana INTEGER, meta TEXT,\n"
        "    creado_en TEXT NOT NULL, actualizado_en TEXT NOT NULL,\n"
        "    incremento_kg REAL\n"
        ");\n"
        "CREATE TABLE historial_perfil (   -- auditoría de cambios de ficha\n"
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "    numero_usuario TEXT NOT NULL, campo TEXT NOT NULL,\n"
        "    valor TEXT, fecha TEXT NOT NULL\n"
        ");\n"
        "CREATE TABLE marcas_rm (\n"
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "    numero_usuario TEXT NOT NULL, ejercicio TEXT NOT NULL,\n"
        "    rm_estimado REAL NOT NULL, peso_origen REAL, reps_origen INTEGER,\n"
        "    fuente TEXT DEFAULT 'chat', fecha TEXT NOT NULL\n"
        ");\n"
        "CREATE TABLE series_log (          -- el diario de entrenamiento\n"
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "    numero_usuario TEXT NOT NULL, fecha_hora TEXT NOT NULL,\n"
        "    ejercicio TEXT NOT NULL, peso_kg REAL NOT NULL,\n"
        "    repeticiones INTEGER NOT NULL, rpe_reportado REAL,\n"
        "    vbt_velocidad_media REAL, vbt_velocidad_pico REAL,\n"
        "    vbt_perdida_velocidad REAL, rom_cm REAL,\n"
        "    dolor_articular TEXT, calidad_tecnica TEXT\n"
        ");\n"
        "CREATE TABLE readiness (\n"
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "    numero_usuario TEXT NOT NULL, fecha TEXT NOT NULL,\n"
        "    sueno_horas REAL, sueno_calidad INTEGER, estres INTEGER,\n"
        "    dolor TEXT, score REAL NOT NULL, fuente TEXT DEFAULT 'botones',\n"
        "    UNIQUE (numero_usuario, fecha)\n"
        ");\n"
        "CREATE TABLE eventos_autorregulacion (\n"
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "    numero_usuario TEXT NOT NULL, fecha TEXT NOT NULL,\n"
        "    tipo TEXT NOT NULL,             -- dup_ajuste | dup_descarga | mrv_alerta\n"
        "    ejercicio TEXT, detalle_json TEXT\n"
        ");\n"
        "CREATE TABLE alertas_enviadas (\n"
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "    numero_usuario TEXT NOT NULL, tipo TEXT NOT NULL,\n"
        "    enviada_en TEXT DEFAULT (datetime('now')), semana_iso TEXT\n"
        ");")
    p("Las siete tablas se reparten en dos bases SQLite (tres para perfil y "
      "marcas, cuatro para telemetría de entrenamiento), verificadas mediante "
      "consulta directa a `sqlite_master` sobre el sistema en ejecución. Ninguna "
      "declara claves foráneas explícitas —SQLite no las impone por defecto—, así "
      "que la integridad referencial entre `atletas` y las tablas dependientes es "
      "responsabilidad exclusiva del código de la aplicación; se declara aquí "
      "como una limitación de robustez del esquema, no señalada en versiones "
      "previas de la documentación del proyecto.")
    p("A las siete tablas relacionales se suman tres colecciones vectoriales en "
      "ChromaDB con `nomic-embed-text` como modelo de embeddings: el expediente "
      "dinámico del atleta (molestias, material, horarios), la bitácora narrativa "
      "de mesociclos cerrados, y la biblioteca científica indexada —3 811 "
      "fragmentos de 997 páginas únicas, cifra reindexada y verificada durante la "
      "auditoría documental de este proyecto—. El plan de entrenamiento en sí y "
      "el estado del calendario viven en JSON sobre disco, no en base de datos: "
      "son documentos que se leen completos y con poca frecuencia de escritura, "
      "el patrón de acceso que justifica un fichero frente a una tabla.")
    p("Composición verificada de la biblioteca científica.", negrita=True)
    p("Auditar el contenido real de los cuatro ficheros que alimentan esta "
      "colección —no solo su nombre de archivo— reveló una composición distinta "
      "de la que documentación anterior del proyecto describía como «cuatro "
      "libros científicos». Dos de los cuatro ficheros son en efecto literatura "
      f"primaria de ciencia del ejercicio con autoría e ISBN verificables "
      f"{cita('Schoenfeld2016')}; {cita('Helms_MuscleStrengthPyramid')}; un "
      "tercero es un libro sobre nutrición deportiva, fuera del dominio de "
      "entrenamiento que el sistema atiende activamente; y el cuarto —cuyo "
      "nombre de archivo sugería el libro de referencia sobre programación de "
      "fuerza de Rippetoe y Kilgore— resultó ser, verificado por sus metadatos "
      "internos y su contenido, un artículo de blog de un tercer autor "
      f"comentando ese libro, no el libro en sí {cita('Jovanovic_PracticalProgrammingReview')}. "
      "Este hallazgo se reporta porque ilustra un riesgo general de los sistemas "
      "de recuperación aumentada: el nombre de un fichero no garantiza su "
      "contenido, y una fuente mal identificada puede propagarse como cita "
      "indirecta —a través del propio sistema respondiendo preguntas del "
      "atleta— sin que nadie la verifique.")

    p("3.8 Concurrencia y arquitectura de colas", estilo="Heading 2")
    p("Dos mecanismos de concurrencia distintos, en capas distintas del sistema, "
      "resuelven problemas complementarios. El primero garantiza que las "
      "inferencias de un mismo atleta no se intercalen con las de otro frente al "
      "modelo de lenguaje, evitando que ambos desalojen mutuamente la caché de "
      "prefijo descrita en la sección 2.2. El segundo es una cola de tipo FIFO "
      "independiente por atleta, con fusión de mensajes en ráfaga, que resuelve un "
      "problema real de mensajería documentado durante el desarrollo: en "
      "WhatsApp la gente parte una idea en varios mensajes seguidos, y sin este "
      "mecanismo el sistema arrancaba a procesar el primero antes de que llegaran "
      "los siguientes, generando dos respuestas del coach a lo que era un solo "
      "pensamiento del atleta.")
    p("El mecanismo de fusión opera en dos capas: primero incorpora, al sacar el "
      "siguiente trabajo de la cola, hasta cinco mensajes de texto consecutivos "
      "que ya estuvieran esperando, cortando la fusión en cuanto aparece un audio "
      "o un vídeo para no alterar el orden real de una conversación multimodal. "
      "Segundo, una ventana de dos segundos espera a que lleguen mensajes "
      "adicionales cuando hay exactamente un texto fusionable pendiente —la "
      "situación que la primera capa no puede cubrir, porque solo fusiona lo que "
      "ya está en cola en el instante en que el turno anterior termina—. Por "
      "encima de veinte mensajes en espera de un mismo atleta, el sistema asume "
      "que no hay conversación que salvar y descarta el resto con aviso.")

    p("3.9 Estrategia de verificación", estilo="Heading 2")
    p("El comportamiento del sistema se comprueba en tres niveles, cada uno "
      "diseñado para un riesgo distinto: 32 suites deterministas sobre "
      "componentes aislados (un parser, una expresión regular, una fórmula), un "
      "banco de 74 casos de interacción en 22 bloques temáticos que conversa con "
      "el orquestador completo —incluido el modelo real— y compara qué "
      "herramientas se dispararon y qué expresiones aparecen o no en la "
      "respuesta, y 72 aserciones de humo que cargan el módulo real con sus "
      "dependencias sustituidas para comprobar que ni siquiera se rompe un "
      "contrato básico entre componentes.")
    p("Ningún nivel es redundante con los otros: una prueba unitaria clásica de "
      "entrada fija y salida exacta es inaplicable al comportamiento del modelo "
      "en sí, porque su salida no es reproducible byte a byte entre ejecuciones; "
      "por eso el banco de interacción evalúa condiciones verificables por "
      "código —qué herramientas se invocaron, qué expresiones deben o no "
      "aparecer— en lugar de comparar texto literal. El propio proyecto "
      "documenta y asume como principio metodológico que este banco tiene ruido "
      "inherente —una variación de ±1 acierto entre corridas es normal, "
      "atribuible al muestreo del modelo en los turnos que razonan— y que la "
      "respuesta correcta ante esa variación es repetir la ejecución antes de "
      "atribuirla a un cambio de código, no eliminar el ruido fijando la "
      "temperatura a cero y evaluar así un sistema distinto del que corre en "
      "producción.")
    p("El propio instrumental de prueba no está exento de fallar: durante el "
      "desarrollo se detectaron dos defectos del banco de casos en sí, no del "
      "sistema evaluado —un detector de incidencias que marcaba una señal "
      "presente en el cien por ciento de los turnos, vaciándola de significado, y "
      "varios casos mal planteados que pedían un ejercicio ajeno a la sesión del "
      "día, de modo que una respuesta correcta del sistema se contabilizaba como "
      "fallo del caso—. Ambos hallazgos se corrigieron y se documentan aquí "
      "porque son evidencia de la misma disciplina de verificación cruzada "
      "aplicada al resto de este trabajo: un instrumento de medición puede tener "
      "sus propios defectos, y descubrirlos exige reconciliar contra un segundo "
      "método, no confiar en el resultado de la primera pasada.")

    p("3.10 Consideraciones éticas, legales y de protección de datos", estilo="Heading 2")
    p("El sistema almacena, asociados a un número de teléfono —identificador "
      "directo de una persona física—, datos que constituyen una categoría "
      "especial bajo el marco normativo de protección de datos: peso, "
      "composición corporal, marcas de fuerza, y molestias o lesiones reportadas "
      "en lenguaje libre. Bajo el Reglamento General de Protección de Datos de la "
      f"Unión Europea {cita('RGPD2016')} los datos de salud requieren condiciones "
      "de tratamiento más estrictas que los datos personales ordinarios; bajo la "
      f"legislación mexicana equivalente {cita('LFPDPPP2010')} se clasifican "
      "igualmente como datos sensibles. La decisión de arquitectura más "
      "relevante frente a este riesgo es que la inferencia, los embeddings, la "
      "transcripción de audio y el análisis de vídeo corren íntegramente en "
      "local: ningún dato de salud del atleta se envía a un tercero para su "
      "procesamiento por inteligencia artificial. Esto reduce sustancialmente la "
      "superficie de exposición frente a un sistema equivalente que dependiera de "
      "una API de modelo de lenguaje en la nube, aunque no elimina la necesidad "
      "de una base legal para el tratamiento ni de medidas de seguridad sobre los "
      "ficheros que sí contienen estos datos en texto plano en disco.")
    p("El límite entre acompañar el entrenamiento y practicar medicina está "
      "codificado explícitamente en el prompt del sistema, verificado de forma "
      "textual y no parafraseada:")
    par = doc.add_paragraph(style="CitaLarga")
    par.add_run("«Cuando el atleta mencione una molestia o lesión, reporta "
                "ÚNICAMENTE lo que él dijo. Tienes PROHIBIDO inventar o exagerar "
                "gravedad médica: nunca digas que está ‘en el hospital’, "
                "que necesita ‘el alta del médico’ ni que debe "
                "‘reposar’ si él no lo dijo. Sugiere ver a un "
                "profesional solo si él describe algo serio, y hazlo una sola "
                "vez, sin dramatizar.»").italic = True
    p("Esta es una salvaguarda de instrucción al modelo, no de código "
      "determinista: a diferencia de las seis redes de validación de la sección "
      "3.4, no existe hoy un mecanismo que verifique después de generada la "
      "respuesta si el modelo violó esta regla concreta. Es una brecha de "
      "cobertura real entre lo que el prompt prohíbe y lo que el sistema audita, "
      "que se declara aquí como limitación abierta.")
    p("Verificación empírica de esta regla, ejecutada en vivo durante la "
      "elaboración de este reporte: ante el mensaje «me duele el hombro cuando "
      "hago press militar», el sistema respondió «El press militar es un "
      "movimiento exigente para el hombro, y si te está dando problemas ahora "
      "mismo, no lo toques hasta que estés al 100 %», guardó el dato en el "
      "expediente del atleta y preguntó si el dolor era agudo o difuso — sin "
      "mencionar hospitales, altas médicas ni reposo prescrito, y sin que el "
      "atleta hubiera descrito nada de eso. Es una sola observación, no una "
      "medición estadística de la tasa de cumplimiento de la regla, pero es "
      "evidencia real de que se sostiene en un caso concreto, capturada el mismo "
      "día que se redactó esta sección.")
    tabla(
        ["Riesgo", "Mecanismo de mitigación", "Estado"],
        [
            ["Alucinación de cargas de entrenamiento",
             "Separación cálculo/lenguaje y red 1 de validación", "Mitigado"],
            ["Exageración de gravedad médica",
             "Regla de prompt citada arriba", "Mitigado solo a nivel de prompt"],
            ["Prescripción sobre lesión no declarada",
             "Estructural: el sistema solo conoce lo que el atleta reporta",
             "No mitigable sin evaluación médica real"],
            ["Exposición de datos de salud en reposo",
             "Ninguno — los ficheros no están cifrados", "Sin mitigar"],
            ["Sesgo del clasificador por conjunto de datos desbalanceado",
             "Ninguno — el desbalance descrito en 3.6.3 no se corrige",
             "Sin mitigar"],
        ],
        titulo="Riesgos identificados y su estado de mitigación.",
        anchos=[4.6, 6.4, 4.0], tam=9)
    p("Un hallazgo adicional, detectado al auditar los documentos que alimentan la "
      "biblioteca científica del sistema, se reporta aquí por su gravedad: uno de "
      "los libros indexados resultó ser una copia de distribución personalizada "
      "de una obra con derechos de autor, con una marca de agua que identifica "
      "por nombre completo y dirección de correo electrónico a un comprador "
      "ajeno por completo a este proyecto. El fichero estaba versionado en el "
      "repositorio de control de versiones. Este hallazgo no se resuelve en el "
      "código como parte de este trabajo académico —es una decisión de "
      "producto y de cumplimiento legal que corresponde al propietario del "
      "repositorio, no una decisión técnica—, y se documenta explícitamente "
      "porque omitirlo habría sido contrario a la disciplina de verificación que "
      "el resto de este reporte defiende. Se recomienda sustituir el fichero por "
      "una copia sin datos personales de terceros y auditar el historial de "
      "control de versiones antes de que el repositorio adquiera visibilidad "
      "pública, si no la tiene ya.")
    p("Finalmente, el canal de mensajería impone restricciones que no son "
      "técnicas sino contractuales: la política de mensajería de la plataforma "
      f"utilizada {cita('MetaWhatsAppPolicy')} prohíbe iniciar una conversación "
      "con un usuario fuera de una ventana de veinticuatro horas desde su último "
      "mensaje, salvo mediante una plantilla pre-aprobada — la razón por la que "
      "el cuestionario matutino de disponibilidad del sistema debe implementarse "
      "necesariamente como una plantilla y no como texto libre generado por el "
      "modelo.")
    salto()


# ═══════════════════════════════════ 4. ORIGINALIDAD DE LA PROPUESTA ════
def originalidad():
    p("4. Originalidad de la propuesta", estilo="Heading 1")
    p("Esta sección precisa en qué se aparta este trabajo de los enfoques "
      "reportados en trabajos previos, distinguiendo entre lo que es aplicación de "
      "técnicas conocidas y lo que constituye una aportación propia.")

    p("4.1 Comparativa frente a los trabajos previos", estilo="Heading 2")
    p("El patrón habitual en la literatura de agentes basados en modelos de "
      f"lenguaje {cita('Yao2023')}; {cita('Schick2023')}; {cita('Qin2024')} delega "
      "en el modelo tanto la decisión de qué herramienta invocar como la síntesis "
      "del resultado. Este trabajo invierte parcialmente ese orden para las "
      "acciones de escritura: código determinista detecta la intención, ejecuta la "
      "acción y solo entonces entrega al modelo el resultado ya producido. El "
      "modelo conserva la decisión de herramienta únicamente donde el código no "
      "puede resolverla sin comprensión de lenguaje natural.")
    tabla(
        ["Capacidad", "Apps de registro", "Sistemas VBT comerciales",
         "Agentes LLM en salud", "Iron Progress"],
        [
            ["Planificación adaptativa real", "Parcial o nula", "No aplica",
             "No aplica", "**Sí**"],
            ["Interfaz de lenguaje natural", "No", "No", "Sí", "**Sí**"],
            ["Autorregulación por lenguaje libre", "No", "No aplica", "Variable",
             "**Sí**"],
            ["Métricas VBT", "No", "Sí, con hardware dedicado", "No aplica",
             "**Sí, sin hardware**"],
            ["Defensas explícitas contra alucinación",
             "No aplica", "No aplica", "Poco documentado", "**Seis redes**"],
            ["Inferencia y datos en local", "No", "Parcial", "Generalmente no",
             "**Sí, modelo incluido**"],
        ],
        titulo="Posicionamiento del sistema frente a las categorías de solución "
               "existentes.",
        anchos=[4.0, 2.7, 3.3, 2.5, 2.5], tam=8.5)
    p("Ningún producto o sistema revisado combina las seis filas de la tabla "
      "anterior. El hueco que este trabajo ocupa no es ninguna capacidad aislada "
      "—planificación adaptativa, análisis de velocidad sin hardware o "
      "conversación en lenguaje natural existen por separado en otros sistemas— "
      "sino su combinación, sostenida sobre una arquitectura que hace explícita y "
      "auditable la separación entre lo que calcula el código y lo que comunica el "
      "modelo.")

    p("4.2 Aportaciones originales de este trabajo", estilo="Heading 2")

    p("4.2.1 El patrón de veracidad por construcción", estilo="Heading 3")
    p("La aportación principal se formula así:")
    par = doc.add_paragraph(style="CitaLarga")
    r = par.add_run("En cualquier dominio conversacional donde el usuario no pueda "
                    "verificar por sí mismo la respuesta de un modelo de lenguaje, "
                    "la arquitectura correcta no es confiar en que el modelo no "
                    "alucine, ni pedirle que se autoverifique: es hacer que el "
                    "modelo nunca sea la fuente de la cifra o la afirmación de "
                    "estado en primer lugar, y auditar lo que sí genera contra una "
                    "fuente de verdad externa y determinista que exista con "
                    "independencia del propio modelo.")
    r.bold = True
    p("Frente a las técnicas de verificación posterior reportadas en la literatura "
      f"—consistencia entre muestreos del propio modelo {cita('Manakul2023')} o "
      f"medidas de incertidumbre semántica {cita('Farquhar2024')}—, este trabajo "
      "verifica contra el estado real del sistema en disco. La diferencia es "
      "sustantiva: un método que compara al modelo consigo mismo hereda los mismos "
      "sesgos que produjeron la alucinación original, mientras que contrastar "
      "contra el plan, el historial de series y el registro de herramientas "
      "efectivamente ejecutadas es una comprobación independiente. Esta elección "
      "es viable aquí porque el dominio ofrece una fuente de verdad computable, "
      "condición que no todo dominio cumple y que se declara explícitamente como "
      "el límite de generalización de la propuesta.")

    p("4.2.2 Aportaciones de menor alcance pero verificables", estilo="Heading 3")
    rico(("Ponderación dinámica de fórmulas de 1RM. ", "b"),
         ("En lugar de adoptar una única ecuación de estimación, el sistema "
          "pondera seis según el rango de repeticiones de la serie reportada, "
          "siguiendo el hallazgo de que el error de cada fórmula varía con ese "
          f"rango {cita('LeSuer1997')}. Es una síntesis práctica de esa evidencia, "
          "no una fórmula publicada como tal.", ""), vinetas=True)
    rico(("Detección de afirmaciones falsas sensible a la morfología del español. ", "b"),
         ("Las técnicas de detección de alucinación de la literatura consultada se "
          "desarrollan y evalúan mayoritariamente sobre el inglés. La distinción "
          "entre «aplique» y «apliqué» —subjuntivo frente a pretérito, separados "
          "solo por la tilde— es una fuente de falsos positivos que no tiene "
          "equivalente directo en inglés, y su tratamiento explícito constituye "
          "una adaptación original al idioma.", ""), vinetas=True)
    rico(("Reconciliación honesta de estimadores en desacuerdo. ", "b"),
         ("Cuando los dos estimadores independientes de repeticiones en reserva no "
          "se solapan, el sistema devuelve la envolvente de ambos marcada como "
          "incierta, en lugar de promediar sus extremos y presentar con apariencia "
          "de consenso un valor que ningún estimador sostiene.", ""), vinetas=True)
    rico(("Análisis de velocidad de barra sin hardware dedicado. ", "b"),
         ("Frente a los encoders lineales y acelerómetros que constituyen el "
          f"estándar del campo {cita('Weakley2021')}, el sistema deriva las "
          "métricas del vídeo de un teléfono. La contrapartida —la ausencia de "
          "validación de concordancia contra un instrumento de referencia— se "
          "declara en la sección 5 como limitación, no se omite.", ""), vinetas=True)
    rico(("Plan almacenado en porcentajes, resuelto a kilos en la lectura. ", "b"),
         ("Permite que un récord reajuste el mesociclo completo sin regenerarlo, y "
          "elimina por construcción la clase de error del plan desactualizado que "
          "sigue prescribiendo cargas basadas en una marca antigua.", ""),
         vinetas=True)

    p("4.2.3 Qué NO es original en este trabajo", estilo="Heading 3")
    p("Por honestidad metodológica conviene delimitar también lo contrario. No son "
      "aportaciones de este trabajo: las seis ecuaciones de estimación de 1RM, que "
      "son de dominio público desde hace décadas; el marco MEV/MAV/MRV ni la "
      "periodización ondulante, tomados de la literatura citada en la sección 2.1; "
      "la arquitectura transformer, la cuantización ni la caché de prefijo, que "
      "son técnicas establecidas; ni los modelos preentrenados de visión y habla "
      "empleados, que son de terceros. La contribución reside en la arquitectura "
      "que los integra bajo la garantía descrita en 4.2.1, y en las adaptaciones "
      "enumeradas en 4.2.2.")
    salto()


# ═══════════════════════════════════════ 5. RESULTADOS DE LA APLICACIÓN ════
def resultados():
    p("5. Resultados de la aplicación", estilo="Heading 1")
    p("Todas las cifras de esta sección proceden de la ejecución real del sistema "
      "o de la lectura directa del código en el momento de redactar el reporte. Se "
      "distingue explícitamente entre las mediciones reproducidas durante la "
      "elaboración de este documento y las heredadas de la fase de desarrollo.")

    p("5.1 Dimensiones del sistema construido", estilo="Heading 2")
    tabla(
        ["Métrica", "Valor verificado", "Método de verificación"],
        [
            ["Líneas de código Python del producto", "27 528",
             "Recuento programático, excluyendo las herramientas de "
             "documentación del propio repositorio"],
            ["Orquestador (módulo central)", "7 792 líneas", "Recuento directo"],
            ["Herramientas expuestas al modelo", "23",
             "Lectura del catálogo de herramientas"],
            ["Reglas de decisión del prompt", "17",
             "Análisis de la sección correspondiente del prompt"],
            ["Secciones del prompt del sistema", "13", "Análisis estructural"],
            ["Redes de validación de salida", "6", "Localización en el código"],
            ["Tablas relacionales", "7 en dos bases SQLite",
             "Consulta al esquema de las bases"],
            ["Colecciones vectoriales", "3", "Consulta al almacén vectorial"],
            ["Fragmentos de literatura indexada", "3 811 de 997 páginas",
             "Reindexación ejecutada durante la elaboración del reporte"],
            ["Suites de prueba deterministas", "32", "Recuento de ficheros"],
            ["Casos de prueba de interacción", "74 en 22 bloques",
             "Carga del banco de pruebas"],
            ["Aserciones de humo", "72 correctas, 0 fallos",
             "Ejecución real de la suite"],
        ],
        titulo="Dimensiones y cobertura de prueba del sistema, verificadas.",
        anchos=[5.2, 4.4, 5.4], tam=9)
    p("Estas cifras se obtienen mediante un script de recuento incorporado al "
      "repositorio, de modo que puedan regenerarse en cualquier momento en lugar "
      "de transcribirse a mano y quedar desactualizadas. La necesidad de esa "
      "automatización se demostró dos veces durante la elaboración del reporte. "
      "La documentación técnica previa del proyecto contenía siete cifras que ya "
      "no coincidían con el código, entre ellas la versión del modelo de "
      "inferencia y el número de secciones del prompt. Y, en una revisión "
      "posterior del propio script de recuento, se detectó que este contaba "
      "como parte del sistema las herramientas de documentación del repositorio "
      "(1 489 líneas solo del generador de este reporte): la cifra de «29 286 "
      "líneas» de una versión anterior de este mismo documento ya arrastraba ese "
      "error, heredado sin saberlo de la medición original del proyecto. La "
      "cifra de esta tabla —27 528— excluye esas herramientas y corresponde "
      "únicamente al código que se ejecuta en producción.")
    aviso("Nota sobre el estado de control de versiones.",
          "El nombre del modelo de inferencia («qwen3.8:27b») y algunos otros "
          "valores de esta tabla se verificaron contra el sistema en ejecución, "
          "que en el momento de escribir este reporte difiere del último cambio "
          "confirmado en el historial de control de versiones: el fichero de "
          "configuración del modelo (Modelfile) tiene una actualización sin "
          "confirmar que cambia el modelo servido. Se reporta el estado "
          "realmente en ejecución por ser la fuente de verdad más directa, y se "
          "señala esta discrepancia para que quede constancia de que código "
          "confirmado y sistema en ejecución no son siempre lo mismo.",
          color="EAF1FA", borde=AZUL)

    p("5.2 Rendimiento y latencia", estilo="Heading 2")
    tabla(
        ["Fase", "Antes de la optimización", "Después"],
        [
            ["Preprocesado del prompt", "17,6 s", "1,2 – 3,5 s"],
            ["Acierto de la caché de prefijo", "Prácticamente nulo", "99,9 %"],
            ["Turno de confirmación simple", "20 – 30 s", "4 – 8 s"],
        ],
        titulo="Impacto de la optimización de la caché de prefijo (medición de la "
               "fase de desarrollo).",
        anchos=[5.2, 4.9, 4.9], tam=9.5)
    p("La causa del cuello de botella resultó ser que el sistema podaba la "
      "telemetría del historial antes de enviarlo al modelo, lo que reescribía la "
      "cola del prompt y rompía la condición de prefijo exacto que la caché "
      "necesita. Conservarla íntegra situó el preprocesado en el rango indicado. "
      "El coste es que el modelo recibe datos caducados, y el prompt le instruye "
      "explícitamente a considerar válido únicamente el bloque más reciente.")
    p("Evidencia fresca del mismo mecanismo, capturada durante la elaboración de "
      "este reporte al ejecutar dos turnos consecutivos en vivo contra el sistema "
      "real:")
    tabla(
        ["Turno", "Prefill", "Velocidad de prefill", "Carga del modelo"],
        [
            ["1.º de la sesión (arranque en frío)", "30 659 tokens en 51,4 s",
             "596 tok/s", "67,6 s"],
            ["2.º, mismo turno (caché caliente)", "34 458 tokens en 8,1 s",
             "4 266 tok/s", "0,2 s"],
        ],
        titulo="Tiempos de prefill medidos en vivo, primer turno frío frente a "
               "turno con caché de prefijo ya caliente.",
        anchos=[6.0, 4.5, 3.0, 1.5], tam=9)
    p("El segundo turno procesa 3 799 tokens más que el primero en la séptima "
      "parte del tiempo, porque la mayor parte de ese prompt ya estaba en la "
      "caché de prefijo del motor de inferencia. Esta es precisamente la "
      "condición de prefijo exacto que exige la sección 2.2, medida sobre el "
      "sistema real, no simulada.")

    p("5.3 Evaluación del clasificador de ejercicios", estilo="Heading 2")
    p("El torneo de algoritmos se reejecutó íntegramente durante la elaboración de "
      "este reporte, sobre el conjunto de datos real:")
    tabla(
        ["Algoritmo", "Precisión", "Marcados como desconocido"],
        [
            ["**Gradient Boosting (ganador)**", "**80,00 %**", "0 de 10"],
            ["Random Forest", "70,00 %", "3 de 10"],
            ["Regresión Logística", "70,00 %", "2 de 10"],
            ["Máquinas de vectores de soporte", "30,00 %", "3 de 10"],
            ["K vecinos más cercanos", "30,00 %", "5 de 10"],
        ],
        titulo="Comparativa de los cinco algoritmos sobre la misma partición.",
        anchos=[6.1, 4.3, 4.6], tam=9.5)
    p("El conjunto de datos consta de 49 vídeos repartidos de forma desigual entre "
      "cuatro clases: 19 de press de banca, 12 de sentadilla, 11 de peso muerto y "
      "7 de dominadas. La partición de prueba es de 10 vídeos, por lo que un solo "
      "vídeo mal clasificado desplaza el resultado diez puntos porcentuales. La "
      "cifra del 80 % debe leerse con esa salvedad y no como una precisión "
      "estimada con robustez estadística.")

    p("5.4 Un hallazgo relevante durante la verificación", estilo="Heading 2")
    p("Durante la elaboración de este reporte se ejecutaron en vivo dos bloques "
      "del banco de pruebas de interacción contra el sistema completo —siete "
      "casos en total, sobre el modelo de lenguaje real, no simulado—. El "
      "resultado agregado matiza la lectura de lo que sigue: seis de los siete "
      "casos se comportaron exactamente como se esperaba, incluida una "
      "verificación empírica de la regla de salud citada en la sección 3.10 que "
      "se sostuvo sin exagerar gravedad médica. Uno de los siete reveló un "
      "comportamiento que ninguna prueba automática había detectado:")
    aviso("Falso positivo observado el 27 de agosto de 2026.",
          "Ante el mensaje «llevo toda la semana molido, ¿me toca descarga?», el "
          "sistema produjo una respuesta correcta y honesta, indicando que no "
          "disponía de datos suficientes para evaluar la fatiga con rigor. La "
          "cuarta red de validación la marcó erróneamente como afirmación falsa y "
          "forzó una reescritura. El texto reescrito resultó peor que el original: "
          "presentaba errores gramaticales y filtraba instrucciones internas del "
          "sistema al canal del atleta, violando la regla de secreto profesional. "
          "El banco de pruebas registró el caso como correcto porque sus "
          "aserciones automáticas solo verifican qué herramientas se invocaron, no "
          "la calidad ni la seguridad del texto final.")
    p("Este hallazgo se reporta por tres razones. Primera, demuestra que la tasa de "
      "falsos positivos de las redes de validación no es despreciable y que el "
      "sistema carece hoy de instrumentación para cuantificarla. Segunda, revela "
      "que el mecanismo de reescritura garantiza la terminación del turno pero no "
      "que la respuesta reescrita sea mejor que la original. Tercera, y más "
      "relevante desde el punto de vista metodológico, confirma que un banco de "
      "pruebas solo detecta aquello que se le pidió comprobar: el caso pasó en "
      "verde pese a contener un fallo real y visible para cualquier lector humano.")
    p("Una proporción de uno en siete no debe leerse como una tasa de fallo del "
      "sistema: la muestra es demasiado pequeña para sostener cualquier "
      "inferencia estadística, y el propio banco de pruebas documenta un ruido "
      "esperado de ±1 acierto entre corridas (sección 3.9) atribuible al "
      "muestreo del modelo. Lo que sí sostiene esta muestra, precisamente por "
      "tratarse de observación directa y no de una cifra agregada sin trazar, es "
      "la existencia demostrada del fenómeno —hay al menos un camino real por el "
      "que una red de validación se equivoca y una reescritura empeora la "
      "respuesta— y la evidencia de que ese camino no es el modo dominante de "
      "fallo del sistema, dado que los seis casos restantes se comportaron "
      "correctamente.")

    p("5.5 Limitaciones", estilo="Heading 2")
    tabla(
        ["Limitación", "Alcance"],
        [
            ["Sin ensayo controlado con múltiples participantes independientes",
             "Las cifras de comportamiento proceden de un banco de casos simulados "
             "y de uso real limitado; no admiten inferencia estadística"],
            ["Sin validación de concordancia del motor de velocidad",
             "No se contrastó contra un encoder lineal de referencia, el estándar "
             "del campo; solo se reporta evidencia indirecta de detección"],
            ["Redes de validación sin telemetría de activación",
             "Impide cuantificar su eficacia y su tasa de falsos positivos en "
             "producción"],
            ["Falso positivo de la cuarta red observado y no corregido",
             "Documentado en 5.4; pendiente de diagnóstico y corrección"],
            ["Conjunto de datos del clasificador pequeño y desbalanceado",
             "49 vídeos, con una clase 2,7 veces más representada que otra"],
            ["Retroalimentación de error en la recolección continua",
             "Los vídeos se archivan bajo la etiqueta que el propio clasificador "
             "predijo; un error se incorpora al entrenamiento siguiente"],
            ["Un solo idioma y un solo canal",
             "No se evaluó el comportamiento en otros idiomas ni sobre otros "
             "canales de mensajería"],
            ["El patrón propuesto exige una fuente de verdad computable",
             "Límite estructural de generalización, declarado en 4.2.1"],
            ["Esquema relacional sin claves foráneas explícitas",
             "La integridad referencial depende del código de la aplicación, no "
             "del motor de base de datos (sección 3.7)"],
            ["Fichero de la biblioteca científica con datos personales de un "
             "tercero", "Hallazgo de cumplimiento legal sin resolver a nivel de "
             "producto; ver sección 3.10 para el detalle completo"],
            ["Salvaguarda de salud del atleta sin verificación posterior",
             "La regla existe en el prompt pero ninguna red la audita después de "
             "generada la respuesta; ver sección 3.10"],
            ["Sin reconciliación tras un fallo de envío al canal",
             "Puede existir divergencia entre lo que el sistema registra como "
             "enviado y lo que el atleta recibió realmente; ver sección 3.1"],
            ["Canonización de grupo muscular pierde distinción en algunos casos",
             "Depende de una palabra discriminante en el nombre del ejercicio; "
             "ver sección 3.5.2"],
        ],
        titulo="Limitaciones reconocidas del trabajo.",
        anchos=[5.6, 9.4], tam=9)
    salto()


# ══════════════════════════════════════════════════════════ 6. CRONOGRAMA ════
def cronograma():
    p("6. Cronograma", estilo="Heading 1")
    p("El cronograma que sigue se reconstruyó a partir del historial de control de "
      "versiones del proyecto (38 registros de cambio) y de las fechas consignadas "
      "en los propios comentarios del código, no de una estimación retrospectiva.")

    p("6.1 Enfoque metodológico del desarrollo", estilo="Heading 2")
    p("El desarrollo no siguió un diseño experimental clásico de hipótesis fijada "
      "de antemano, sino un ciclo iterativo guiado por fallos observados en "
      "conversaciones reales:")
    codigo(
        "  1. INCIDENTE      → se observa un fallo real en conversación\n"
        "  2. DIAGNÓSTICO    → se localiza la causa en el código\n"
        "  3. PRUEBA         → se escribe un caso que falla con el comportamiento\n"
        "                      anterior y pasa con la corrección\n"
        "  4. CORRECCIÓN     → se implementa el mecanismo (interceptor, red de\n"
        "                      validación, ajuste de prompt)\n"
        "  5. REGRESIÓN      → el caso queda en el banco para que una regresión\n"
        "                      futura lo vuelva a detectar automáticamente")
    p("Este enfoque es coherente con la literatura de ingeniería de software para "
      "sistemas de aprendizaje automático, que documenta que el comportamiento de "
      "estos sistemas rara vez se anticipa por completo en el diseño y con "
      f"frecuencia se descubre en producción {cita('Sculley2015')}; "
      f"{cita('Amershi2019')}. Su contrapartida, declarada como limitación en la "
      "sección 5.5, es que las defensas implementadas cubren los incidentes que "
      "efectivamente ocurrieron y se observaron, no el espacio completo de fallos "
      "posibles.")

    p("6.2 Cronograma de ejecución", estilo="Heading 2")
    tabla(
        ["Fase", "Periodo", "Actividades", "Producto"],
        [
            ["1. Análisis y arranque", "15 – 18 jul 2026",
             "Definición del alcance; primera versión del orquestador; catálogo "
             "inicial de herramientas y prompt del sistema",
             "Prototipo conversacional funcional"],
            ["2. Motores de dominio", "21 – 23 jul 2026",
             "Implementación del motor de análisis de vídeo y del motor "
             "predictivo; recuperación del entorno de trabajo",
             "Motores VBT y predictivo operativos"],
            ["3. Primeras versiones estables", "26 – 28 jul 2026",
             "Integración de componentes; resolución de conflictos de "
             "consolidación; tres iteraciones marcadas como producción",
             "Sistema integrado de extremo a extremo"],
            ["4. Verificación y corrección", "29 – 31 jul 2026",
             "Banco de pruebas de interacción; corrección del parseo de series, "
             "persistencia del historial, canonización de ejercicios y cálculo de "
             "1RM; siete correcciones críticas previas a demostración",
             "Banco de 74 casos; sistema estabilizado"],
            ["5. Instrumentación y demostración", "1 – 3 ago 2026",
             "Scripts de siembra de datos de demostración; suites de prueba "
             "faltantes; iteración sobre la interfaz superpuesta del vídeo",
             "Entorno de demostración reproducible"],
            ["6. Documentación técnica", "31 jul 2026",
             "Redacción de la memoria técnica de arquitectura",
             "Documento técnico de 33 páginas"],
            ["7. Auditoría y documentación académica", "27 ago 2026",
             "Auditoría de discrepancias entre documentación y código; "
             "reconstrucción de la base bibliográfica; revisión documental; "
             "verificación de resultados por reejecución",
             "Este reporte; 64 referencias; script de métricas reproducibles"],
        ],
        titulo="Cronograma de ejecución del proyecto.",
        anchos=[3.2, 2.5, 5.5, 3.8], tam=8.5)

    p("6.3 Distribución temporal", estilo="Heading 2")
    codigo(
        "                    JULIO 2026                    AGOSTO 2026\n"
        "              15  18  21  23  26  28  31    01  03  ...  27\n"
        "  Fase 1      ████████\n"
        "  Fase 2              ████████\n"
        "  Fase 3                      ████████\n"
        "  Fase 4                              ████████\n"
        "  Fase 5                                    ████\n"
        "  Fase 6                              ██\n"
        "  Fase 7                                              ████")
    p("El desarrollo activo del sistema abarcó 19 días naturales; la fase de "
      "documentación académica se realizó 24 días después del último cambio "
      "registrado en el código, lo que explica que la auditoría de la fase 7 "
      "encontrara discrepancias entre la documentación técnica y el estado real "
      "del sistema.")
    salto()


# ════════════════════════════════════════════════════════ 7. CONCLUSIONES ════
def conclusiones():
    p("7. Conclusiones", estilo="Heading 1")
    p("Este trabajo demuestra que es posible construir un agente conversacional "
      "sobre un modelo de lenguaje ejecutado en local que sostenga el rol de "
      "entrenador de fuerza sin que el modelo sea nunca la fuente de una cifra o "
      "de una afirmación de estado. La garantía no es un principio de diseño "
      "declarado y luego incumplido en la práctica: se verificó sobre la base de "
      "código completa, y su cumplimiento se apoya en dos mecanismos "
      "complementarios —los interceptores que actúan antes que el modelo y las "
      "seis redes que auditan lo que produce— documentados en la sección 3.")
    p("El trabajo demuestra igualmente, con la misma disciplina que exige su "
      "propio objeto de estudio, que la capa de verificación no es infalible. "
      "Durante la elaboración de este reporte se capturó en vivo un falso positivo "
      "que degradó una respuesta correcta en una incorrecta y filtró información "
      "interna al canal del usuario. Esa doble conclusión —el patrón funciona en "
      "lo estructural y falla parcialmente en lo puntual, de forma diagnosticable— "
      "es el resultado más honesto que este trabajo puede ofrecer, y es preferible "
      "a cualquiera de las dos afirmaciones más simples y menos verdaderas: que el "
      "sistema nunca alucina, o que la ingeniería contra la alucinación no sirve.")
    p("Respecto a los objetivos planteados en la sección 1.4, los objetivos 1, 2, "
      "4 y 5 se cumplen con evidencia directa de código y de pruebas. El objetivo "
      "6 se apoya en mediciones de la fase de desarrollo no reproducidas durante "
      "la redacción. El objetivo 3 es el que queda con evidencia más débil y de "
      "forma reveladora: la primera ejecución en vivo realizada encontró un falso "
      "positivo, lo que confirma la necesidad de la instrumentación que la sección "
      "5.5 identifica como pendiente.")

    p("7.1 Trabajo futuro", estilo="Heading 2")
    p("Corto plazo, sin investigación nueva requerida:", estilo=None)
    rico(("Instrumentar un contador persistente de activaciones por red de "
          "validación, requisito previo para cuantificar su eficacia real.", ""),
         vinetas=True)
    rico(("Diagnosticar y corregir el falso positivo documentado en la sección "
          "5.4, el hallazgo más urgente de la verificación.", ""), vinetas=True)
    rico(("Extender el banco de pruebas para verificar la calidad y la seguridad "
          "del texto final, no solo qué herramientas se invocaron.", ""),
         vinetas=True)
    rico(("Mitigar la retroalimentación de error en la recolección continua de "
          "vídeos, descartando o marcando para revisión los de baja confianza.", ""),
         vinetas=True)
    p("Medio y largo plazo:", estilo=None)
    rico(("Ampliar el conjunto de datos del clasificador con un reparto más "
          "equilibrado entre clases.", ""), vinetas=True)
    rico(("Validar el motor de velocidad por concordancia contra un encoder "
          "lineal de referencia.", ""), vinetas=True)
    rico(("Evaluar la transferencia del patrón de veracidad por construcción a un "
          "segundo dominio con las mismas características, para comprobar si es "
          "efectivamente generalizable o si depende de particularidades del "
          "entrenamiento de fuerza.", ""), vinetas=True)
    salto()


# ═══════════════════════════════════════════════════════════ 8. REFERENCIAS ══
def seccion_referencias():
    p("8. Referencias bibliográficas", estilo="Heading 1")
    p("Las referencias siguen el estilo APA, séptima edición. Se incluyen "
      "únicamente las obras efectivamente citadas en el cuerpo del reporte.",
      tam=10, color=GRIS, cursiva=True)
    p()
    referencias()
    p()
    aviso("Verificación pendiente.",
          "Las entradas resaltadas en color requieren una comprobación adicional "
          "antes de la entrega definitiva: confirmar la edición y el identificador "
          "exactos contra la fuente original. Se recomienda además resolver el "
          "DOI o el ISBN de cada referencia contra una base bibliográfica antes de "
          "presentar el documento.", color="FFF4E5", borde=ROJO)


# ══════════════════════════════════════════════════════════════════ MAIN ════
def pie_de_pagina():
    for s in doc.sections:
        par = s.footer.paragraphs[0]
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = par.add_run("Iron Progress · Reporte de Aplicación de Conocimientos")
        r.font.size = Pt(8)
        r.font.color.rgb = GRIS


def main():
    portada()
    resumen_e_indice()
    importancia_del_problema()
    revision_documental()
    solucion_y_propuesta()
    originalidad()
    resultados()
    cronograma()
    conclusiones()
    seccion_referencias()
    pie_de_pagina()
    doc.save(str(SALIDA))
    print(f"Documento generado: {SALIDA}")
    print(f"Tamaño: {SALIDA.stat().st_size // 1024} KB")
    print(f"Referencias citadas: {len(_USADAS)} de {len(_BIB)} disponibles")
    print(f"Figuras: {_CONT_FIG[0]} · Tablas: {_CONT_TAB[0]}")


if __name__ == "__main__":
    main()
