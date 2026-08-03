# -*- coding: utf-8 -*-
"""Genera la documentación técnica de Iron Progress en .docx."""
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BASE = Path(__file__).resolve().parent
IMG = BASE / "img"
SALIDA = BASE.parent / "Iron_Progress_Documentacion_Tecnica.docx"
SALIDA.parent.mkdir(parents=True, exist_ok=True)

AZUL = RGBColor(0x1F, 0x3A, 0x5F)
GRIS = RGBColor(0x55, 0x5F, 0x6E)
ROJO = RGBColor(0x8A, 0x2B, 0x2B)
VERDE = RGBColor(0x1E, 0x5F, 0x3A)
NEGRO = RGBColor(0x1A, 0x1A, 0x1A)

doc = Document()


# ── Estilos base ────────────────────────────────────────────────────────────
def _estilos():
    n = doc.styles["Normal"]
    n.font.name = "Calibri"
    n.font.size = Pt(10.5)
    n.font.color.rgb = NEGRO
    n.paragraph_format.space_after = Pt(7)
    n.paragraph_format.line_spacing = 1.14
    n.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    for nivel, tam, color in (("Heading 1", 19, AZUL), ("Heading 2", 14, AZUL),
                              ("Heading 3", 11.5, RGBColor(0x2E, 0x4A, 0x70))):
        st = doc.styles[nivel]
        st.font.name = "Calibri Light" if nivel == "Heading 1" else "Calibri"
        st.font.size = Pt(tam)
        st.font.bold = True
        st.font.color.rgb = color
        st.paragraph_format.space_before = Pt(16 if nivel == "Heading 1" else 12)
        st.paragraph_format.space_after = Pt(6)
        st.paragraph_format.keep_with_next = True

    cq = doc.styles.add_style("Codigo", 1)
    cq.font.name = "Consolas"
    cq.font.size = Pt(8.8)
    cq.font.color.rgb = RGBColor(0x24, 0x35, 0x4A)
    cq.paragraph_format.space_before = Pt(4)
    cq.paragraph_format.space_after = Pt(8)
    cq.paragraph_format.left_indent = Cm(0.5)
    cq.paragraph_format.line_spacing = 1.05
    cq.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT

    pie = doc.styles.add_style("PieFigura", 1)
    pie.font.name = "Calibri"
    pie.font.size = Pt(8.5)
    pie.font.italic = True
    pie.font.color.rgb = GRIS
    pie.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pie.paragraph_format.space_after = Pt(14)
    pie.paragraph_format.space_before = Pt(3)


_estilos()

sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
for m in ("left_margin", "right_margin"):
    setattr(sec, m, Cm(2.3))
sec.top_margin, sec.bottom_margin = Cm(2.2), Cm(2.0)
ANCHO = sec.page_width - sec.left_margin - sec.right_margin


# ── Utilidades ──────────────────────────────────────────────────────────────
def p(texto="", estilo=None, negrita=False, tam=None, color=None, antes=None, despues=None):
    par = doc.add_paragraph(style=estilo)
    if texto:
        r = par.add_run(texto)
        r.bold = negrita
        if tam:
            r.font.size = Pt(tam)
        if color:
            r.font.color.rgb = color
    if antes is not None:
        par.paragraph_format.space_before = Pt(antes)
    if despues is not None:
        par.paragraph_format.space_after = Pt(despues)
    return par


def rico(*trozos, estilo=None, vinetas=False):
    """Párrafo con tramos (texto, formato). formato: '' | 'b' | 'i' | 'c' | 'bc'."""
    par = doc.add_paragraph(style="List Bullet" if vinetas else estilo)
    for texto, fmt in trozos:
        r = par.add_run(texto)
        if "b" in fmt:
            r.bold = True
        if "i" in fmt:
            r.italic = True
        if "c" in fmt:
            r.font.name = "Consolas"
            r.font.size = Pt(9.3)
            r.font.color.rgb = RGBColor(0x24, 0x35, 0x4A)
    if vinetas:
        par.paragraph_format.space_after = Pt(3)
        par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    return par


def codigo(lineas):
    for ln in lineas.rstrip("\n").split("\n"):
        par = doc.add_paragraph(style="Codigo")
        par.add_run(ln if ln else " ")
        par.paragraph_format.space_after = Pt(0)
        par.paragraph_format.space_before = Pt(0)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def figura(nombre, pie, ancho=None):
    doc.add_picture(str(IMG / nombre), width=ancho or ANCHO)
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.paragraphs[-1].paragraph_format.space_before = Pt(8)
    doc.paragraphs[-1].paragraph_format.space_after = Pt(2)
    p(pie, estilo="PieFigura")


def sombrear(celda, hexcol):
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")
    el.set(qn("w:fill"), hexcol)
    celda._tc.get_or_add_tcPr().append(el)


def tabla(cabeceras, filas, anchos=None, tam=9.0):
    t = doc.add_table(rows=1, cols=len(cabeceras))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for i, h in enumerate(cabeceras):
        c = t.rows[0].cells[i]
        c.text = ""
        r = c.paragraphs[0].add_run(h)
        r.bold = True
        r.font.size = Pt(tam)
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        c.paragraphs[0].paragraph_format.space_after = Pt(2)
        c.paragraphs[0].paragraph_format.space_before = Pt(2)
        c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.LEFT
        sombrear(c, "2B3A55")
    for n, fila in enumerate(filas):
        celdas = t.add_row().cells
        for i, val in enumerate(fila):
            celdas[i].text = ""
            par = celdas[i].paragraphs[0]
            par.paragraph_format.space_after = Pt(2)
            par.paragraph_format.space_before = Pt(2)
            par.alignment = WD_ALIGN_PARAGRAPH.LEFT
            mono = val.startswith("`") and val.endswith("`")
            r = par.add_run(val.strip("`"))
            r.font.size = Pt(tam - 0.4)
            if mono:
                r.font.name = "Consolas"
                r.font.size = Pt(tam - 1.0)
                r.font.color.rgb = RGBColor(0x24, 0x35, 0x4A)
            if n % 2 == 1:
                sombrear(celdas[i], "F4F6FA")
    if anchos:
        for fila in t.rows:
            for i, w in enumerate(anchos):
                fila.cells[i].width = Cm(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(6)
    return t


def aviso(titulo, cuerpo, color="FFF4E5", borde=ROJO):
    t = doc.add_table(rows=1, cols=1)
    t.style = "Table Grid"
    c = t.rows[0].cells[0]
    c.text = ""
    par = c.paragraphs[0]
    r = par.add_run(titulo + "  ")
    r.bold = True
    r.font.size = Pt(9.5)
    r.font.color.rgb = borde
    r2 = par.add_run(cuerpo)
    r2.font.size = Pt(9.5)
    par.paragraph_format.space_before = Pt(4)
    par.paragraph_format.space_after = Pt(4)
    par.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    sombrear(c, color)
    doc.add_paragraph().paragraph_format.space_after = Pt(6)


def salto():
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def pie_de_pagina():
    for s in doc.sections:
        par = s.footer.paragraphs[0]
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = par.add_run("Iron Progress · Documentación técnica · v9.1")
        r.font.size = Pt(8)
        r.font.color.rgb = GRIS


# ═══════════════════════════════════════════════════════════════════════════
# PORTADA
# ═══════════════════════════════════════════════════════════════════════════
for _ in range(4):
    doc.add_paragraph()

t = p("IRON PROGRESS", negrita=True, tam=34, color=AZUL)
t.alignment = WD_ALIGN_PARAGRAPH.CENTER
t.paragraph_format.space_after = Pt(2)

t = p("Documentación técnica de arquitectura", tam=15, color=GRIS)
t.alignment = WD_ALIGN_PARAGRAPH.CENTER
t.paragraph_format.space_after = Pt(24)

t = p("Agente conversacional de entrenamiento de fuerza\nsobre WhatsApp con modelo de lenguaje local",
      tam=12.5, color=NEGRO)
t.alignment = WD_ALIGN_PARAGRAPH.CENTER
t.paragraph_format.space_after = Pt(30)

meta = doc.add_table(rows=0, cols=2)
meta.alignment = WD_TABLE_ALIGNMENT.CENTER
for k, v in (("Versión del sistema", "9.1.0"),
             ("Repositorio", "github.com/AbiGuadarrama/Agente-Iron-Progress"),
             ("Autor", "Abimael Guadarrama"),
             ("Modelo de inferencia", "Qwen3.6 27B (Q4_K_M) sobre Ollama"),
             ("Base de código", "≈28.500 líneas de Python"),
             ("Fecha del documento", "31 de julio de 2026")):
    celdas = meta.add_row().cells
    r = celdas[0].paragraphs[0].add_run(k)
    r.bold = True
    r.font.size = Pt(9.5)
    r.font.color.rgb = AZUL
    celdas[0].width = Cm(5.0)
    r2 = celdas[1].paragraphs[0].add_run(v)
    r2.font.size = Pt(9.5)
    celdas[1].width = Cm(9.5)
    for c in celdas:
        c.paragraphs[0].paragraph_format.space_after = Pt(3)

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("Índice", level=1)
for n, s in enumerate([
    "Resumen ejecutivo",
    "El problema y la decisión de diseño central",
    "Arquitectura del sistema",
    "Ciclo de vida de un mensaje",
    "El orquestador: anatomía de un turno",
    "Los interceptores deterministas",
    "El contrato con el modelo: prompt y herramientas",
    "Las redes de validación de salida",
    "El motor de planificación",
    "El motor predictivo",
    "Modelo de datos y persistencia",
    "Entradas multimodales: voz y vídeo",
    "Proactividad y planificación temporal",
    "Concurrencia y rendimiento",
    "Estrategia de verificación",
    "Deuda técnica y hoja de ruta",
    "Apéndice A · Mapa de ficheros",
    "Apéndice B · Catálogo de herramientas",
], 1):
    par = doc.add_paragraph()
    par.paragraph_format.space_after = Pt(4)
    par.paragraph_format.left_indent = Cm(0.4)
    r = par.add_run(f"{n}.  ")
    r.bold = True
    r.font.color.rgb = AZUL
    par.add_run(s)

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("1.  Resumen ejecutivo", level=1)

p("Iron Progress es un agente conversacional que ejerce de entrenador personal de fuerza a "
  "través de WhatsApp. El atleta le escribe en lenguaje natural —por texto, nota de voz o vídeo "
  "de una serie— y el sistema planifica sus mesociclos, registra su entrenamiento, calcula sus "
  "marcas y ajusta la carga en función de la fatiga que detecta. La inferencia ocurre "
  "íntegramente en local: ningún dato del atleta sale de la máquina.")

p("El sistema se apoya en una decisión de diseño que atraviesa toda la base de código: "
  "el modelo de lenguaje no calcula ni decide sobre datos numéricos. Su función es entender la "
  "intención, elegir la herramienta adecuada y comunicar con el tono de un entrenador. Cada "
  "cifra que llega al atleta procede de código determinista o del resultado de una herramienta. "
  "Esta separación no es cosmética: en un dominio donde el usuario no puede verificar lo que se "
  "le dice, una cifra inventada se traduce en semanas de entrenamiento mal ejecutado.")

doc.add_heading("Capacidades funcionales", level=2)
tabla(["Dominio", "Capacidad"],
      [["Planificación", "Genera mesociclos con periodización ondulante diaria (DUP), splits de 3 a 7 días "
                         "y reparto de volumen por patrón de movimiento, interpolado del volumen mínimo eficaz al "
                         "máximo adaptativo a lo largo del bloque."],
       ["Registro", "Interpreta series reportadas en lenguaje natural, nota de voz o vídeo, y las persiste "
                    "con peso, repeticiones, RPE, métricas de velocidad y dolor articular."],
       ["Analítica", "Estima 1RM, sigue la progresión por ejercicio, calcula la ratio de carga "
                     "aguda/crónica y determina si el volumen ha superado el máximo recuperable."],
       ["Adaptación", "Sustituye ejercicios, aplica descargas y reajusta la sesión en curso cuando el "
                      "esfuerzo reportado se desvía de lo prescrito."],
       ["Visión", "Extrae métricas de entrenamiento basado en velocidad a partir del vídeo de una serie "
                  "y clasifica el ejercicio ejecutado."],
       ["Memoria", "Mantiene tres índices vectoriales consultables por significado: el expediente del "
                   "atleta (molestias, material, horarios), la bitácora de mesociclos cerrados y la "
                   "biblioteca científica."],
       ["Proactividad", "Abre la conversación cada mañana con un cuestionario de disponibilidad y envía "
                        "gráficas de progresión."]],
      anchos=[3.2, 12.2])

doc.add_heading("Cifras del sistema", level=2)
tabla(["Métrica", "Valor"],
      [["Líneas de Python", "≈28.500 en total; ≈19.400 sin el banco de pruebas"],
       ["Orquestador (llm_service.py)", "7.749 líneas — el componente central"],
       ["Herramientas expuestas al modelo", "23, declaradas en core/tools.json"],
       ["Reglas de decisión del prompt", "17, en la sección <herramientas>"],
       ["Interceptores deterministas", "≈14 bloques que actúan antes que el modelo"],
       ["Redes de validación de salida", "6, encadenadas tras la redacción"],
       ["Ventana de contexto", "40.960 tokens (num_ctx)"],
       ["Tablas relacionales", "7 en dos bases SQLite"],
       ["Colecciones vectoriales", "3 en ChromaDB (expediente, bitácora, literatura)"],
       ["Fragmentos de literatura indexada", "3.852, de 1.007 páginas de PDF"],
       ["Cobertura de pruebas", "32 suites deterministas + 74 casos de interacción + 72 aserciones de humo"]],
      anchos=[6.4, 9.0])

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("2.  El problema y la decisión de diseño central", level=1)

p("Una aplicación de gimnasio convencional registra lo que el usuario introduce. Un entrenador "
  "hace algo cualitativamente distinto: recuerda que al atleta le molesta el hombro, advierte que "
  "lleva tres semanas estancado en un movimiento y le baja la carga el día que llega agotado. "
  "Iron Progress persigue lo segundo, y ahí aparece el riesgo estructural de construirlo sobre un "
  "modelo de lenguaje.")

doc.add_heading("El riesgo: la alucinación como daño físico", level=2)

p("Un modelo generativo produce texto plausible, no texto verdadero. En la mayoría de las "
  "aplicaciones eso degrada la experiencia; aquí tiene consecuencias materiales. Si el coach afirma "
  "haber aplicado una descarga que nunca se aplicó, el atleta entrena un mesociclo entero con menos "
  "carga de la que le corresponde. Si estima un 1RM por su cuenta y se equivoca un diez por ciento, "
  "todas las cargas derivadas quedan mal calibradas. Y como el atleta no tiene forma de auditar al "
  "sistema, la confianza que deposita en él amplifica el error en lugar de corregirlo.")

aviso("Caso real registrado.",
      "El 29 de julio de 2026, ante el mensaje «llevo toda la semana molido, ¿me toca descarga?», el "
      "coach consultó el análisis de fatiga, no ejecutó la aplicación de la descarga y respondió "
      "«Ajuste aplicado: reducción del 10 % en la carga, una serie menos». No existía ninguna "
      "descarga. Los cinco turnos siguientes siguieron refiriéndose a «la descarga que aplicamos». "
      "Este incidente motivó la cuarta red de validación descrita en el capítulo 8.")

doc.add_heading("La respuesta: separar el cálculo del lenguaje", level=2)

p("La arquitectura reparte la responsabilidad de forma explícita. Código Python determinista "
  "interpreta el mensaje, ejecuta las escrituras y calcula los números. El modelo recibe el "
  "resultado ya consumado y su única tarea es comunicarlo. Cuando el modelo necesita un dato que "
  "no tiene, no lo estima: invoca una herramienta.")

figura("d3_reparto.png",
       "Figura 1. Reparto de responsabilidad entre el código determinista y el modelo de lenguaje.")

p("Esta separación tiene un corolario incómodo pero necesario: cuando el sistema carece de una "
  "capacidad, hay que construirla en lugar de dejar que el modelo improvise. Durante la "
  "verificación se detectó que el coach inventaba el volumen semanal de su propio plan. La causa "
  "no era negligencia del modelo, sino la ausencia de cualquier fuente: el análisis de máximo "
  "volumen recuperable mide el volumen ejecutado, no el prescrito, y contar las series leyendo el "
  "texto del plan es precisamente la aritmética que el prompt le prohíbe. La solución fue añadir "
  "una herramienta que responde esa pregunta con exactitud.")

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("3.  Arquitectura del sistema", level=1)

p("El sistema se organiza en cinco capas con dependencias unidireccionales. La capa de entrada "
  "traduce el protocolo de WhatsApp a llamadas internas; la de orquestación decide qué ocurre en "
  "cada turno; los motores de dominio encapsulan el conocimiento de entrenamiento; y la de "
  "persistencia guarda el estado. Ningún motor de dominio conoce a otro: toda coordinación pasa "
  "por el orquestador.")

figura("d1_arquitectura.png",
       "Figura 2. Arquitectura en capas. Las flechas indican dependencia de invocación.")

doc.add_heading("Pila tecnológica", level=2)
tabla(["Capa", "Componentes"],
      [["API y servidor", "FastAPI · Uvicorn · httpx con pool compartido · webhook con validación HMAC-SHA256"],
       ["Inferencia", "Ollama sirviendo Qwen3.6 27B cuantizado a Q4_K_M · 32.768 tokens de contexto · "
                      "keep_alive indefinido para evitar recargas"],
       ["Recuperación", "ChromaDB con embeddings nomic-embed-text · tres colecciones con propósitos distintos"],
       ["Visión", "MediaPipe Pose para cinemática · Ultralytics YOLO para detección · OpenCV · "
                  "clasificador propio de ejercicios con scikit-learn"],
       ["Audio", "faster-whisper, modelo small en int8 sobre CPU"],
       ["Persistencia", "SQLite para series y perfil · JSON en disco para planes, estado y conversación"],
       ["Planificación temporal", "APScheduler · Matplotlib con API orientada a objetos para las gráficas"],
       ["Hardware de referencia", "AMD Radeon RX 7900 XTX (24 GB) con PyTorch ROCm 6.2"]],
      anchos=[4.0, 11.4])

aviso("Restricción de hardware.",
      "El modelo ocupa 18,6 de los 24 GB de memoria de vídeo disponibles. Esto descarta ejecutar "
      "dos instancias en paralelo y obliga a serializar la inferencia, lo que condiciona el diseño "
      "de concurrencia descrito en el capítulo 14.",
      color="EAF2FB", borde=AZUL)

doc.add_heading("Acoplamiento entre módulos", level=2)

p("El orquestador es deliberadamente el único punto de acoplamiento del sistema. Concentra las "
  "dependencias de dominio para que los motores permanezcan aislados y comprobables por separado: "
  "el motor de planificación no sabe que existe un modelo de lenguaje, y el motor predictivo "
  "desconoce que hay una conversación de WhatsApp.")

figura("d7_acoplamiento.png",
       "Figura 3. Mapa de acoplamiento. Las líneas discontinuas indican acceso a persistencia.")

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("4.  Ciclo de vida de un mensaje", level=1)

p("Desde que Meta entrega el webhook hasta que el atleta recibe la respuesta, un mensaje recorre "
  "quince etapas. Las cuatro primeras son defensivas: verifican autenticidad, descartan "
  "duplicados y mensajes caducados, y normalizan el identificador del remitente. Las restantes "
  "constituyen el turno propiamente dicho.")

figura("d2_ciclo_mensaje.png",
       "Figura 4. Las quince etapas del ciclo de vida de un mensaje entrante.")

doc.add_heading("Defensas de la capa de entrada", level=2)

rico(("Validación de firma. ", "b"),
     ("La cabecera ", ""), ("X-Hub-Signature-256", "c"),
     (" se compara con un HMAC-SHA256 del cuerpo crudo usando el secreto de la aplicación. "
      "Si el secreto no está configurado, el webhook acepta cualquier petición: es una "
      "configuración válida en desarrollo y un riesgo en producción.", ""),
     vinetas=True)
rico(("Idempotencia. ", "b"),
     ("Meta reintenta los webhooks que no reciben respuesta rápida. Un registro circular de mil "
      "identificadores de mensaje evita procesar el mismo dos veces.", ""),
     vinetas=True)
rico(("Descarte por antigüedad. ", "b"),
     ("Los mensajes con más de 300 segundos se descartan: proceden de reintentos acumulados y "
      "responderlos generaría una conversación desincronizada.", ""),
     vinetas=True)
rico(("Normalización del número. ", "b"),
     ("México introduce un dígito adicional en los identificadores de WhatsApp. Sin unificar "
      "el formato, el mismo atleta generaría dos expedientes distintos.", ""),
     vinetas=True)
rico(("Trabajo en segundo plano. ", "b"),
     ("El webhook responde de inmediato y encola el procesamiento. La inferencia tarda entre "
      "quince y ciento treinta segundos —el decodificado domina el turno—; mantener la "
      "petición abierta provocaría reintentos.", ""),
     vinetas=True)

doc.add_heading("Enrutado según el tipo de mensaje", level=2)

p("El tipo de contenido determina la ruta. Los botones del cuestionario matutino se interceptan "
  "antes de llegar al modelo. Los vídeos siguen un flujo de dos fases: si el pie de foto no "
  "incluye el peso, el sistema clasifica el ejercicio y solicita el dato que falta, guardando el "
  "vídeo en una caché temporal hasta que el atleta responde.")

codigo("""if tipo_mensaje == "video":
    if not contiene_peso(texto_recibido):
        videos_pendientes[numero_usuario] = {"id": media_id, "timestamp": time.time()}
        background_tasks.add_task(procesar_prediccion_rapida, numero_usuario, media_id)
        return
    background_tasks.add_task(tarea_en_segundo_plano_video, ...)""")

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("5.  El orquestador: anatomía de un turno", level=1)

p("La función que gobierna cada turno concentra la complejidad del sistema. Recibe el número del "
  "atleta y su mensaje, y devuelve el texto que se le enviará. Entre ambos extremos se suceden "
  "veintitrés fases agrupadas en cinco bloques funcionales.")

doc.add_heading("Estructura por bloques", level=2)
tabla(["Bloque", "Fases", "Responsabilidad"],
      [["Preparación", "A–D", "Carga del historial con detección de modificaciones externas por firma de "
                              "archivo, reparación de estados corruptos, cálculo del estatus de calendario."],
       ["Máquinas de estado", "E–F", "Recolección de datos del onboarding y flujos destructivos —borrado de "
                                     "plan y cambio de días— con confirmación explícita."],
       ["Contexto", "G–K", "Clasificación del turno, construcción de la telemetría, recuperación vectorial "
                           "e inyección de la rutina solicitada resuelta en Python."],
       ["Ejecución", "L–Q", "Interceptores de escritura, llamada al modelo, bucle de herramientas y "
                            "mecanismos de rescate."],
       ["Validación", "R–W", "Post-proceso para WhatsApp, cuatro redes de validación, control de eco y "
                             "persistencia del historial."]],
      anchos=[3.0, 1.8, 10.6])

doc.add_heading("La telemetría del turno", level=2)

p("Cada mensaje del atleta viaja al modelo acompañado de un bloque de datos que describe el "
  "instante presente: fecha con el día de la semana calculado en Python, estatus del calendario, "
  "semana del mesociclo, rutina del día con las cargas ya resueltas y las últimas catorce "
  "series registradas.")

p("El bloque tiene tres modos mutuamente excluyentes. En onboarding, sustituye toda la telemetría "
  "por la instrucción del único dato pendiente. En conversación casual, se reduce a fecha y "
  "calendario marcados como contexto interno. En modo normal, se emite completo.")

aviso("El historial conserva la telemetría de turnos anteriores.",
      "Es una decisión deliberada. La caché de prompts de Ollama solo acierta si el prompt de un "
      "turno es prefijo exacto del siguiente; podar la telemetría del historial reescribía la cola "
      "del prompt y forzaba reprocesar los ~20.600 tokens completos en cada turno. Conservarla "
      "redujo el tiempo de preprocesado de 17,6 segundos a un rango de 1,2 a 3,5 segundos. El "
      "coste es que el modelo ve datos caducados, y el prompt le instruye explícitamente a que "
      "solo valga el bloque más reciente.",
      color="EAF2FB", borde=AZUL)

doc.add_heading("El bucle de herramientas", level=2)

p("Tras la llamada principal, si el modelo solicitó herramientas, se entra en un bucle de como "
  "máximo tres vueltas. En la última se invoca al modelo sin herramientas disponibles, lo que "
  "garantiza que el turno termine en texto.")

rico(("Deduplicación intra-lote. ", "b"),
     ("Se firma cada llamada con su nombre y argumentos normalizados; las repetidas dentro del "
      "mismo lote se descartan.", ""), vinetas=True)
rico(("Deduplicación entre vueltas. ", "b"),
     ("Las herramientas de escritura no se ejecutan dos veces en el mismo turno, aunque el modelo "
      "insista.", ""), vinetas=True)
rico(("Normalización de nombres y argumentos. ", "b"),
     ("El modelo deforma ocasionalmente los identificadores; una tabla de alias los reconduce y "
      "registra la corrección.", ""), vinetas=True)
rico(("Rescate de llamadas escritas como texto. ", "b"),
     ("Si el modelo emite la invocación como cadena en lugar de usar el canal nativo, se extrae y "
      "se ejecuta.", ""), vinetas=True)
rico(("Contabilidad de veracidad. ", "b"),
     ("Cada ejecución alimenta dos listas —acciones completadas y acciones fallidas— que las "
      "redes de validación consultan después para impedir afirmaciones falsas.", ""), vinetas=True)

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("6.  Los interceptores deterministas", level=1)

p("Son el mecanismo que materializa la decisión de diseño central. Antes de que el modelo "
  "intervenga, código Python analiza el mensaje, ejecuta las escrituras que correspondan e inyecta "
  "el resultado en el contexto acompañado de una instrucción de redacción. El modelo no decide si "
  "la serie se guarda: se guardó ya, y su tarea es confirmarlo.")

doc.add_heading("Registro de series", level=2)

p("El interceptor de series es el más elaborado. Un tokenizador ancla los valores por unidad para "
  "distinguir el peso de las repeticiones y del RPE, admitiendo las formas en que la gente "
  "realmente escribe: «100x5», «100 por 5», «saqué 6 con 20 de lastre». El esfuerzo percibido se "
  "extrae también del lenguaje corriente: «me quedaron dos en el tanque» equivale a un RPE de 8.")

doc.add_heading("Herencia del ejercicio entre turnos", level=3)

p("Nadie repite el nombre del ejercicio en cada serie. Tras reportar una serie de banca, la "
  "siguiente llega como «otra igual, 100 por 4» o simplemente «90x8». El sistema sella el "
  "ejercicio en curso con una vigencia de treinta minutos y lo hereda cuando el mensaje presenta "
  "señales de continuidad o consiste únicamente en la notación numérica.")

aviso("Hallazgo durante la verificación.",
      "El mecanismo de sellado existía en el módulo de handlers desde la versión 9, con una "
      "documentación que afirmaba que el orquestador lo invocaba. Ninguna llamada existía en toda "
      "la base de código: la función de lectura devolvía siempre un valor nulo. Conectarlo resolvió "
      "la pérdida silenciosa de series que se atribuía al modelo.")

p("Cuando el ejercicio se hereda, la instrucción que acompaña al resultado exige que el coach lo "
  "nombre al confirmar. Registrar el dato es preferible a perderlo, pero el atleta debe poder "
  "corregir la atribución en el acto.")

doc.add_heading("Distinción entre marca histórica y serie ejecutada", level=3)

p("«Mi récord en banca son 110 por 3» no describe una serie hecha ahora. Sin distinguirlo, la "
  "marca entraba en el diario de entrenamiento con la fecha del día y sin esfuerzo percibido, "
  "contaminando el panel de últimas series y el análisis de fatiga, que compara ventanas "
  "temporales. El interceptor reconoce las formas de marca histórica, actualiza el 1RM y las "
  "cargas futuras, y no toca el diario.")

doc.add_heading("Autorregulación por esfuerzo excesivo", level=2)

p("Cuando una serie recién registrada supera un RPE de 9,5, el sistema no se limita a anotarla. "
  "Consulta la prescripción del día para ese ejercicio —RPE objetivo y número de series—, invoca "
  "el motor de autorregulación y entrega el resultado al modelo para que lo comunique.")

codigo("""Atleta:  «la primera de banca me salió durísima, 100x5 y rpe 9.5»

  1. Interceptor de series      → registra 100 kg × 5 @ RPE 9,5
  2. prescripcion_de_hoy()      → RPE objetivo 7,5 · 5 series prescritas
  3. autorregular_dia()         → overshoot 2,0 → ajuste −6 % → 95 kg · 2 series
  4. El modelo redacta          → «bajamos a 95 kg y solo harás 2 series más»""")

aviso("Por qué lo ejecuta Python y no el modelo.",
      "La primera implementación se limitaba a invitar al modelo a llamar la herramienta. No "
      "funcionó: en un turno donde Python ya ha ejecutado una acción, el razonamiento del modelo "
      "está desactivado por razones de latencia, de modo que no deliberó sobre la invocación y "
      "redactó el ajuste por su cuenta imitando el vocabulario del motor —«baja a 78 kg por "
      "overshoot»— sin llamar a nada. Las cifras eran inventadas.")

doc.add_heading("Inventario de interceptores", level=2)
tabla(["Interceptor", "Disparador", "Efecto"],
      [["Series", "Notación de peso y repeticiones", "Persiste la serie, calcula el 1RM y sella el ejercicio"],
       ["Marca histórica", "«mi PR», «mi récord», «el año pasado»", "Actualiza el 1RM sin tocar el diario"],
       ["RPE de alarma", "Esfuerzo percibido ≥ 9,5", "Ejecuta la autorregulación del día en curso"],
       ["Peso corporal", "Peso entre 35 y 300 kg sin unidad de carga", "Registra el peso y actualiza la ficha"],
       ["Equipamiento", "Mención de discos disponibles", "Ajusta el incremento de barra para redondear cargas"],
       ["Sustitución", "Petición de cambio de ejercicio", "Valida contra el plan real; rechaza los inexistentes"],
       ["Recuperación", "«ya no me duele», «se me quitó»", "Marca la lesión resuelta y lo prohíbe mencionarla"],
       ["Consulta de rutina", "Referencia a un día o semana concretos", "Resuelve el cursor e inyecta la rutina real"],
       ["Fin de sesión", "«ya terminé todo»", "Sugiere avanzar el calendario"],
       ["Charla casual", "Mensaje trivial sin datos", "Desactiva herramientas, recuperación y razonamiento"]],
      anchos=[3.4, 5.2, 6.8])

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("7.  El contrato con el modelo: prompt y herramientas", level=1)

p("Dos ficheros de configuración definen el comportamiento del modelo. El prompt establece "
  "identidad, límites y criterio de decisión. El catálogo de herramientas describe qué puede "
  "invocar y con qué argumentos. Ambos son contratos: el código que los consume debe respetarlos "
  "al pie de la letra.")

doc.add_heading("Estructura del prompt", level=2)
tabla(["Sección", "Función"],
      [["identidad", "Tono de entrenador de élite y formato compatible con WhatsApp"],
       ["capacidades", "Qué responder cuando el atleta pregunta para qué sirve el sistema"],
       ["secreto_profesional", "Prohibición absoluta de mencionar herramientas, bases de datos o "
                               "cualquier término técnico en el texto visible"],
       ["fuente_de_verdad", "El modelo no crea rutinas ni calcula cargas; transcribe lo que el sistema "
                            "le inyecta y usa la calculadora para toda operación"],
       ["telemetria_vigente", "Solo vale el bloque de datos del último mensaje; los anteriores han caducado"],
       ["herramientas", "Diecisiete reglas que asocian situación con herramienta"],
       ["criterio_ante_dudas", "Cinco principios ordenados para los casos que las reglas no cubren"],
       ["honestidad_con_resultados", "Nunca confirmar una acción sin un resultado del sistema que la respalde"],
       ["sentido_comun_numerico", "Detección de cifras implausibles y confirmación con naturalidad"],
       ["salud_del_atleta", "Prohibición de inventar o exagerar gravedad médica"],
       ["creacion_de_plan", "El onboarding lo gobierna Python; el modelo solo redacta la pregunta"],
       ["memoria_viva", "Uso del expediente para personalizar sin preguntar dos veces"]],
      anchos=[4.4, 11.0])

aviso("Una herramienta sin regla en el prompt no se utiliza nunca.",
      "El modelo elige según la guía de decisión de la sección <herramientas>, no según la "
      "descripción del catálogo. Declarar una herramienta sin añadir su regla correspondiente "
      "produce código muerto silencioso: se comprobó empíricamente antes de incorporar la "
      "consulta de volumen del plan.",
      color="EAF2FB", borde=AZUL)

doc.add_heading("El contrato de argumentos", level=2)

p("El nombre de cada argumento en el catálogo debe coincidir exactamente con el que lee el "
  "handler. Un desajuste no produce ningún error: la herramienta se ejecuta con el argumento "
  "vacío y falla en silencio o guarda datos incompletos. Existe una suite específica que verifica "
  "esta correspondencia, además de la cobertura de todos los nombres en el filtro de secreto "
  "profesional.")

codigo("""$ python -m services.testbed.test_contrato_tools

  Contrato de tools — 23 herramientas declaradas
    todo cuadra: cobertura, prompt, argumentos y alias.
  RESULTADO: contrato OK.""")

doc.add_heading("Razonamiento selectivo", level=2)

p("El razonamiento intermedio del modelo se activa solo cuando aporta valor: en turnos donde debe "
  "decidir qué herramienta usar o sintetizar sus resultados. Cuando Python ya ha resuelto todo "
  "—onboarding, charla casual, o cualquier turno con una acción ya ejecutada— el modelo únicamente "
  "redacta, y razonar sería latencia desperdiciada. Los parámetros de muestreo cambian entre ambos "
  "modos: temperatura 0,3 para redacción y 0,6 con un presupuesto de tokens mayor para razonamiento.")

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("8.  Las redes de validación de salida", level=1)

p("El borrador que produce el modelo no se envía directamente. Atraviesa cuatro redes "
  "independientes, cada una diseñada a partir de un fallo observado en conversaciones reales. Si "
  "alguna detecta un problema, se fuerza una reescritura inyectando la verdad del turno; si el "
  "modelo reincide, responde una plantilla honesta escrita en Python, lo que garantiza la "
  "terminación sin bucles.")

figura("d4_validadores.png",
       "Figura 5. Las cuatro redes de validación y el mecanismo de reescritura.")

doc.add_heading("Red de cifras inventadas", level=2)

p("Se construye una lista blanca con todas las cifras legítimas del turno: las del plan, del panel "
  "de series, de los resultados de herramientas, del historial de conversación, del perfil y del "
  "historial de marcas, más las derivadas por redondeo al incremento real de la barra del atleta. "
  "Cualquier número acompañado de la unidad de peso que no aparezca en esa lista, con una "
  "tolerancia de medio kilo, dispara la red.")

doc.add_heading("Red de rutina fantasma", level=2)

p("Detecta texto con la estructura de una rutina —líneas del tipo «cuatro series de ocho»— cuando "
  "no existe plan generado ni hay una generación en curso. Es el caso en que el modelo, ante un "
  "atleta sin plan, improvisa «algo provisional mientras tanto».")

doc.add_heading("Red de afirmación falsa", level=2)

p("Reconoce las formas en que el coach afirma haber completado una acción y comprueba que alguna "
  "escritura se haya efectuado realmente en el turno. La expresión regular resultó ser mucho más "
  "estrecha de lo que aparentaba: no cubría el cambio ortográfico de «apliqué», ni los tiempos "
  "compuestos como «he pausado», ni el participio de titular «Ajuste aplicado». Con esas tres "
  "puertas abiertas, la red no se activaba precisamente en los casos más dañinos.")

aviso("El subjuntivo español como fuente de falsos positivos.",
      "«Que te aplique una descarga» y «apliqué la descarga» se distinguen únicamente por la "
      "tilde, y el primero es un ofrecimiento, no una afirmación. Admitir la terminación sin "
      "acentuar convertía cada propuesta del coach en un falso positivo y le obligaba a reescribir "
      "mensajes honestos.")

doc.add_heading("Red de estado del plan", level=2)

p("Es la más reciente y la que cubre el incidente descrito en el capítulo 2. A diferencia de las "
  "anteriores, no examina qué herramientas se ejecutaron en el turno —una descarga aplicada ayer "
  "es una afirmación honesta hoy— sino que contrasta lo que el texto afirma contra el estado real "
  "en disco.")

tabla(["Afirmación detectada", "Comprobación contra el disco"],
      [["Descarga aplicada", "Existencia de una entrada de descarga para la semana correspondiente en el "
                             "fichero de anulaciones"],
       ["Plan pausado", "Indicador de pausa en el fichero de estado"],
       ["Plan reactivado", "Ausencia del indicador de pausa"],
       ["Calendario avanzado", "Comparación de la fecha programada antes y después del turno"]],
      anchos=[5.0, 10.4])

p("Las formas ambiguas se dejan pasar deliberadamente. En español, el presente y el pretérito de "
  "la primera persona del plural son idénticos: «cuando vuelvas reanudamos el plan» es "
  "indistinguible de una afirmación de hecho. Reescribir un mensaje honesto causa más daño que "
  "dejar escapar una construcción infrecuente.")

doc.add_heading("Post-proceso para el canal", level=2)

p("Antes del envío, el texto pasa por una limpieza que elimina el razonamiento intermedio, los "
  "bloques de invocación de herramientas y los marcadores internos; convierte la negrita de "
  "Markdown al asterisco simple que entiende WhatsApp; colapsa los saltos de línea excesivos; y "
  "sustituye cualquier nombre de herramienta por una expresión neutra. Finalmente, el mensaje se "
  "trocea en fragmentos de cuatro mil caracteres respetando los saltos de línea.")

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("9.  El motor de planificación", level=1)

p("La generación del plan es determinista en todo lo que decide series, repeticiones, "
  "intensidades y orden de los días: esas reglas están codificadas explícitamente. El modelo de "
  "lenguaje interviene en un único punto acotado —filtrar del catálogo la lista de ejercicios "
  "compatibles con el material y las preferencias del atleta—, y su salida se valida contra el "
  "catálogo antes de usarse. Recibe la ficha del atleta y produce tres ficheros de mesociclo.")

p("Medido el 31 de julio de 2026, la generación completa de los tres ficheros tarda unos quince "
  "segundos con el modelo ya cargado. El mensaje que el sistema le da al atleta —«estará listo en "
  "dos o tres minutos»— es conservador respecto a lo que tarda en realidad.")

figura("d6_pipeline_plan.png",
       "Figura 6. Del onboarding al plan en disco, con el reparto de decisión en cada paso.")

doc.add_heading("Reglas de periodización", level=2)
tabla(["Regla", "Descripción"],
      [["Splits científicos", "Configuraciones para 3, 4, 5, 6 y 7 días semanales, con un mínimo de 48 y un "
                              "máximo de 72 horas de descanso entre sesiones del mismo grupo muscular. Para "
                              "tres días, el patrón alterna entre semanas impares y pares."],
       ["Fusión lineal-ondulante", "La proporción de días por tipo depende del bloque: dos de hipertrofia por "
                                   "uno de fuerza en acumulación, dos de fuerza por uno de hipertrofia en el "
                                   "bloque de fuerza, dos de fuerza por uno de potencia en realización."],
       ["Asimetría por patrón", "El volumen semanal se define por patrón de movimiento, no de forma global: "
                                "empuje y tracción tienen umbrales distintos de volumen mínimo efectivo y "
                                "volumen adaptativo máximo."],
       ["Reparto de series", "Del volumen semanal, entre el 50 y el 60 por ciento se asigna a hipertrofia, "
                             "entre el 20 y el 25 a fuerza, y entre el 15 y el 20 a potencia, renormalizado "
                             "entre las fases presentes en cada semana."],
       ["Guardia del sistema nervioso", "Está prohibido programar dos días de fuerza pesada en fechas de "
                                        "calendario consecutivas. Si la cola de fases lo requiriera, la fuerza "
                                        "se posterga y se adelanta la siguiente fase no pesada, conservando el "
                                        "conteo total del bloque."],
       ["Factor de estiramiento", "Los ejercicios analíticos declaran un factor de 1,0 o 0,5. Las series "
                                  "físicas se calculan para cubrir el volumen efectivo, y la poda contabiliza "
                                  "series efectivas."],
       ["Complementarios", "Se inyectan al final de la sesión, con el 75 por ciento del volumen medio por "
                           "ejercicio principal y siempre a RPE 9,0."]],
      anchos=[3.8, 11.6])

doc.add_heading("Porcentajes frente a kilos", level=2)

p("El plan almacena porcentajes de 1RM, nunca kilos absolutos. Las cargas se calculan en cada "
  "lectura contra las marcas vigentes del atleta y se redondean al incremento real de su barra. "
  "La consecuencia práctica es notable: cuando el atleta firma un récord, todo el mesociclo se "
  "reajusta sin regenerar nada.")

p("La lectura aplica además las anulaciones activas. Las sustituciones cambian el nombre del "
  "ejercicio —y potencialmente su grupo muscular—, y las descargas reducen tanto el porcentaje de "
  "intensidad como el número de series.")

doc.add_heading("Ejecución asíncrona", level=2)

p("La generación completa tarda del orden de minutos y realiza varias inferencias propias. Se "
  "lanza como tarea de fondo: el coach informa al atleta de que su plan estará listo en unos "
  "minutos y la conversación continúa. Un mecanismo de reparación detecta generaciones que quedan "
  "colgadas más de quince minutos y libera el estado.")

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("10.  El motor predictivo", level=1)

p("Encapsula la analítica de entrenamiento. Opera sobre el diario de series y combina cálculos "
  "deterministas con un modelo bayesiano que solo entra en juego cuando hay historial suficiente.")

doc.add_heading("Estimación de repetición máxima", level=2)

p("El 1RM estimado de una serie se calcula a partir del peso, las repeticiones y el esfuerzo "
  "percibido, que permite inferir las repeticiones en reserva y por tanto las repeticiones "
  "efectivas equivalentes. Solo se consideran fiables las series de hasta doce repeticiones "
  "efectivas.")

doc.add_heading("Máximo volumen recuperable", level=2)

p("El análisis compara la ventana reciente con la anterior mediante tres señales clásicas: la "
  "variación porcentual del 1RM estimado por ejercicio, ponderada por número de series; la "
  "variación del esfuerzo percibido a cargas emparejadas con una tolerancia de 2,5 kilos; y los "
  "reportes de dolor articular. La tabla de decisión es explícita y auditable, sin aprendizaje "
  "automático.")

p("Cuando alguna ventana no alcanza cuatro series con la tríada completa, el análisis devuelve un "
  "veredicto de datos insuficientes. Esto no es un fallo, y el sistema debe comunicarlo como lo "
  "que es: falta de historial.")

aviso("Un fallo de clasificación de errores.",
      "El análisis señala la falta de datos mediante una clave distinta a la que usan las demás "
      "herramientas para los errores. El orquestador solo inspeccionaba la clave habitual, de modo "
      "que el caso más común en un atleta nuevo se contabilizaba como «fallo interno». El atleta "
      "leía una avería inexistente y, en la ruta de reescritura, el nombre de la herramienta se "
      "filtraba al mensaje, violando el secreto profesional.")

doc.add_heading("Autorregulación de la sesión", level=2)

p("A partir de la desviación entre el esfuerzo reportado y el prescrito, calcula un ajuste "
  "porcentual de la carga y una reducción de las series restantes. Cada ajuste queda registrado "
  "como un evento con su detalle completo, lo que permite auditar después por qué se modificó una "
  "sesión.")

codigo("""{"overshoot": 2.0, "ajuste_pct": 6.0, "carga_original": 100.0,
 "carga_nueva": 95.0, "series": [4, 2]}""")

doc.add_heading("Predicción de carga y carga acumulada", level=2)

p("La predicción de la carga óptima para una serie objetivo combina un modelo bayesiano sobre las "
  "sesiones históricas con la disponibilidad declarada por el atleta y la ratio de carga "
  "aguda/crónica, calculada mediante medias móviles exponenciales. Por debajo de ocho sesiones "
  "registradas opera solo el componente determinista.")

doc.add_heading("Clasificación por grupo muscular", level=2)

p("El motor necesita agrupar ejercicios por grupo muscular para filtrar el análisis de volumen. La "
  "canonización de nombres pierde información en algunos casos: tanto el curl de bíceps como el "
  "curl femoral se reducen a la misma clave, siendo grupos musculares opuestos. La resolución "
  "consulta primero el nombre completo, donde una palabra discriminante desambigua, y solo después "
  "la clave canónica.")

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("11.  Modelo de datos y persistencia", level=1)

p("El estado se reparte entre cuatro almacenes. La elección de cada tecnología responde al patrón "
  "de acceso, no a preferencia: un log de eventos consultado por ventanas temporales pide SQL, un "
  "documento inmutable que se lee completo pide un fichero, y una consulta por significado pide "
  "un índice vectorial.")

figura("d5_datos.png", "Figura 7. Los cuatro almacenes de persistencia y su contenido.")

doc.add_heading("Optimizaciones de acceso", level=2)

rico(("Caché por firma de archivo. ", "b"),
     ("Los planes y el historial se mantienen en memoria y solo se releen del disco cuando cambia "
      "la firma del fichero. Esto permite que herramientas externas modifiquen los datos sin dejar "
      "el proceso con una vista obsoleta.", ""), vinetas=True)
rico(("Estado en memoria con sincronización. ", "b"),
     ("El fichero de estado es diminuto pero se lee y escribe varias veces por turno; se mantiene "
      "cacheado y se sincroniza al escribir.", ""), vinetas=True)
rico(("Escrituras fuera del bucle de eventos. ", "b"),
     ("Toda escritura en SQLite o en el índice vectorial se delega a un hilo. Una escritura "
      "síncrona bloqueaba el bucle asíncrono completo y añadía segundos al turno.", ""), vinetas=True)
rico(("Auditoría de cambios de ficha. ", "b"),
     ("Cada modificación del perfil guarda el valor anterior y el nuevo con su marca temporal, lo "
      "que permite reconstruir la evolución del atleta.", ""), vinetas=True)

doc.add_heading("Integridad y reparación", level=2)

p("El sistema asume que el estado en disco puede estar corrupto: editado a mano, escrito por una "
  "versión anterior o interrumpido a media escritura. Varios mecanismos lo reparan sin intervención. "
  "Una fecha programada inválida se reprograma al día actual; un indicador de campo pendiente que "
  "apunta a un dato ya guardado se recalcula; y una inconsistencia entre el estado y los ficheros "
  "del plan se resuelve a favor de los ficheros.")

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("12.  Entradas multimodales: voz y vídeo", level=1)

doc.add_heading("Notas de voz", level=2)

p("La transcripción usa un modelo Whisper de tamaño reducido en cuantización de ocho bits sobre "
  "CPU, cargado una sola vez al arrancar. Sobre el transcrito —habitualmente caótico, grabado "
  "entre series— opera un extractor que busca la tríada de ejercicio, peso y repeticiones con el "
  "esfuerzo percibido, y devuelve un eco de confirmación al atleta.")

doc.add_heading("Análisis de vídeo", level=2)

p("El vídeo de una serie produce dos resultados. Un clasificador propio identifica el ejercicio a "
  "partir de la cinemática extraída con MediaPipe Pose, y un análisis de velocidad calcula las "
  "métricas de entrenamiento basado en velocidad: velocidad media y de pico, pérdida de velocidad "
  "entre repeticiones y rango de movimiento.")

aviso("El objeto de estimación de pose no es reentrante.",
      "MediaPipe mantiene estado interno de seguimiento entre fotogramas. La inferencia se invoca "
      "desde un hilo, de modo que dos atletas enviando vídeo simultáneamente lo usaban en paralelo "
      "y corrompían el grafo, produciendo predicciones absurdas en el mejor caso. Un cerrojo "
      "serializa la inferencia; al ser un trabajo limitado por CPU, no se pierde rendimiento real.")

p("El flujo conversacional del vídeo tiene dos fases porque el peso no puede inferirse de la "
  "imagen. Si el pie de foto no lo incluye, el sistema clasifica el ejercicio, guarda el vídeo en "
  "una caché con caducidad y pide el dato. La respuesta del atleta puede además corregir la "
  "clasificación, y esa corrección tiene prioridad sobre la detección automática.")

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("13.  Proactividad y planificación temporal", level=1)

p("Un agente que solo reacciona no ejerce de entrenador. El módulo de proactividad cubre tres "
  "frentes mediante un planificador que arranca con la aplicación.")

rico(("Cuestionario matutino. ", "b"),
     ("A las 6:30 el sistema abre la conversación con una plantilla aprobada por Meta. Fuera de la "
      "ventana de veinticuatro horas desde el último mensaje del usuario, es el único mecanismo "
      "legal para iniciar una conversación. Las respuestas por botón se interceptan antes de "
      "llegar al modelo y alimentan la tabla de disponibilidad.", ""), vinetas=True)
rico(("Gráficas de progresión. ", "b"),
     ("Se generan con Matplotlib como imagen y se envían mediante la carga de medios de la API. La "
      "interfaz orientada a objetos es obligatoria: el gestor global de figuras no es seguro entre "
      "hilos y dos gráficas simultáneas se pisaban.", ""), vinetas=True)
rico(("Filtrado de destinatarios. ", "b"),
     ("Se excluyen los atletas con el plan pausado y los que están en pleno onboarding. La ruta "
      "del fichero de estado es absoluta: siendo relativa, un arranque desde otro directorio la "
      "dejaba sin encontrar, el error se absorbía y el envío matutino no salía para nadie, en "
      "silencio.", ""), vinetas=True)

aviso("La recarga automática duplica el planificador.",
      "El servidor con recarga automática levanta dos procesos, de modo que el planificador se "
      "ejecuta por duplicado y cada atleta recibe el mensaje matutino dos veces. Por eso la "
      "recarga depende del entorno y en producción se fuerza un único proceso de trabajo: el "
      "estado vive en memoria y escalar horizontalmente significaría duplicar mensajes.")

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("14.  Concurrencia y rendimiento", level=1)

doc.add_heading("El cuello de botella y su resolución", level=2)

p("La latencia de un turno se descompone en preprocesado del prompt y generación de tokens. La "
  "instrumentación reveló que el preprocesado consumía 17,6 segundos porque la caché de prompts "
  "de Ollama fallaba en cada turno. La causa era que se podaba la telemetría del historial antes "
  "de enviarlo, lo que reescribía la cola del prompt y rompía la condición de prefijo exacto. "
  "Conservarla íntegra situó el preprocesado entre 1,2 y 3,5 segundos.")

tabla(["Fase", "Antes", "Después"],
      [["Preprocesado del prompt", "17,6 s", "1,2 – 3,5 s"],
       ["Acierto de caché de prefijo", "prácticamente nulo", "99,9 %"],
       ["Turno de confirmación simple", "20 – 30 s", "4 – 8 s"]],
      anchos=[6.4, 4.5, 4.5])

doc.add_heading("Exclusión por turno", level=2)

p("Con un solo atleta, tomar y liberar el semáforo de inferencia en cada llamada es inocuo. Con "
  "dos atletas conversando simultáneamente aparece un problema: sus llamadas se intercalan, y "
  "como la caché de prompts está limitada al tamaño del contexto, cada uno desaloja el prefijo "
  "del otro y ambos pagan el preprocesado completo.")

p("La solución no es más paralelismo —no hay memoria de vídeo disponible— sino hacer que el turno "
  "de un atleta sea atómico frente al modelo: sus dos a cuatro inferencias se ejecutan seguidas, "
  "sin que ninguna otra se cuele en medio. El mecanismo combina un cerrojo por atleta, que evita "
  "que se solape consigo mismo, con una guardia de exclusión que envuelve el turno completo y se "
  "libera aunque se produzca una excepción entre dos inferencias.")

aviso("Herencia de contexto en tareas de fondo.",
      "La guardia se propaga mediante una variable de contexto. La tarea de generación del plan "
      "nace dentro de un turno y hereda su guardia; sin desvincularla explícitamente, liberaría el "
      "semáforo dos veces, dejándolo abierto y devolviendo las inferencias solapadas.")

doc.add_heading("Techo de razonamiento", level=2)

p("Persiste una limitación conocida. Por encima de unos 2.500 tokens de razonamiento, el modelo "
  "degrada: produce respuestas de longitud excesiva que se truncan al agotar el presupuesto y "
  "falla al construir invocaciones de herramientas. Es la causa común de los turnos que superan "
  "los sesenta segundos y de una parte de los fallos de selección de herramienta. Existe un "
  "mecanismo de rescate que reintenta sin razonamiento cuando la respuesta llega truncada y vacía.")

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("15.  Estrategia de verificación", level=1)

p("Verificar un agente conversacional no se reduce a probar funciones puras. El proyecto combina "
  "tres niveles: suites deterministas sobre los componentes de parseo y decisión, un banco de "
  "casos de uso que conversa con el orquestador completo, y un conjunto de aserciones de humo que "
  "carga el módulo real con sus dependencias sustituidas.")

doc.add_heading("Suites deterministas", level=2)
tabla(["Suite", "Cobertura"],
      [["`test_parser_marcas`", "46 casos de notación de peso y repeticiones, incluidas las formas partidas "
                                "en dos mensajes"],
       ["`test_rpe_hablado`", "46 casos de esfuerzo percibido expresado en lenguaje natural"],
       ["`test_router_casual`", "111 casos de clasificación entre charla trivial y mensaje con datos"],
       ["`test_sustitucion_fantasma`", "24 casos de sustitución de ejercicios inexistentes en el plan"],
       ["`test_claims_estado`", "42 casos de afirmaciones sobre el estado del plan, con los positivos "
                                "tomados de transcripciones reales"],
       ["`test_contrato_tools`", "Correspondencia entre el catálogo, los handlers, el prompt y el filtro "
                                 "de secreto profesional"],
       ["`test_turno_exclusivo`", "Exclusión mutua de la inferencia y liberación de la guardia ante excepciones"],
       ["`smoke_v8`", "72 aserciones sobre el módulo real con dependencias sustituidas"]],
      anchos=[4.6, 10.8])

doc.add_heading("Banco de casos de uso", level=2)

p("El banco ejecuta bloques temáticos independientes contra el orquestador real, con un veredicto "
  "automático por caso que comprueba qué herramientas se ejecutaron y qué expresiones aparecen o "
  "no en la respuesta. Los setenta y cuatro casos, repartidos en veintidós bloques temáticos, "
  "cubren las diecisiete reglas de decisión del prompt.")

codigo("""$ SIM_NUMERO=... python -m services.testbed.qa_casos_uso

  [9/74] OK  (regla 10)
    ATLETA : la primera de banca me salió durísima, 100x5 y rpe 9.5
    espera : autorregular_dia con los datos exactos; NO es descarga semanal
    tools  : ['registrar_serie_completada*', 'autorregular_dia*']   8.3 s

  74 casos en 22 bloques; el veredicto es por caso y admite ±1 de ruido entre corridas""")

p("El asterisco distingue lo que resolvió Python de lo que invocó el modelo. La distinción es "
  "imprescindible: sin ella, casos que funcionaban correctamente aparecían como fallos porque los "
  "interceptores deterministas no emiten una invocación de herramienta.")

aviso("Un banco de pruebas que miente es peor que no tenerlo.",
      "Dos defectos del propio instrumental produjeron diagnósticos falsos. El detector de "
      "incidencias marcaba una señal que el registro emite en todas las llamadas, señalando el "
      "cien por cien de los turnos y vaciando el aviso de significado. Y varios casos estaban mal "
      "planteados por pedir un ejercicio que no correspondía a la sesión del día: el coach "
      "respondía correctamente que no tocaba ese grupo muscular, y el caso medía otra cosa.")

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("16.  Deuda técnica y hoja de ruta", level=1)

doc.add_heading("Limitaciones conocidas", level=2)
tabla(["Asunto", "Situación", "Impacto"],
      [["Techo de razonamiento", "Mitigado", "Cortar el razonamiento a un número fijo de tokens se probó y se "
                                             "descartó: los cinco intentos perdieron la invocación de herramienta y "
                                             "además salió más lento. Se atacó ampliando la ventana a 40.960 tokens "
                                             "y con un rescate que reintenta sin razonamiento."],
       ["Mensajes de herramientas no persistidos", "Resuelto", "El orquestador persiste las invocaciones en forma "
                                                               "plana y serializable, de modo que el prompt del turno "
                                                               "siguiente vuelve a ser prefijo del anterior."],
       ["Indicador de escritura", "Resuelto", "Se marca el mensaje como leído y se envía la señal de escritura "
                                              "durante la espera, con suite propia."],
       ["Bitácora narrativa sin escritor", "Abierto", "La colección de memoria histórica se lee pero ningún punto del "
                                                      "sistema la escribe: la herramienta que la consulta devuelve "
                                                      "siempre vacío salvo que se siembre a mano."],
       ["Series agrupadas en un mensaje", "Abierto", "La herramienta de registro admite una sola serie por llamada. "
                                                     "Si el atleta reporta cuatro series idénticas de una vez, se "
                                                     "persiste una y se pierde el resto del volumen."],
       ["Firma del webhook", "Resuelto", "El secreto está definido y la validación HMAC está activa; el arranque "
                                         "informa de su estado."],
       ["Confirmación de regeneración", "Latente", "Una variable de control se inicializa y nunca se asigna, "
                                                   "dejando una guarda sin efecto."],
       ["Lista blanca de cifras acumulativa", "Por diseño", "Incluye el historial completo, también los "
                                                            "mensajes del propio coach: una cifra que se cuele "
                                                            "una vez queda legitimada."]],
      anchos=[4.2, 2.6, 8.6])

doc.add_heading("Trabajo previsto", level=2)
for t in ["Cerrar los mesociclos automáticamente en la bitácora narrativa, de modo que la memoria "
          "de largo plazo se alimente sola en vez de depender del script de siembra.",
          "Admitir el registro de series agrupadas en un solo mensaje, añadiendo una cantidad "
          "opcional a la herramienta de registro.",
          "Añadir el cálculo inverso de repetición máxima, hoy ausente: al pedirlo, el modelo "
          "rompe la prohibición de calcular y dispara el guardia de afirmación falsa.",
          "Ampliar el catálogo de ejercicios reconocidos, que actualmente pierde movimientos "
          "habituales y descarta en silencio las series correspondientes.",
          "Panel web de seguimiento para consultar la progresión sin pasar por la conversación."]:
    rico((t, ""), vinetas=True)

salto()

# ═══════════════════════════════════════════════════════════════════════════
doc.add_heading("Apéndice A · Mapa de ficheros", level=1)

tabla(["Fichero", "Responsabilidad"],
      [["`main.py`", "Arranque, ciclo de vida de la aplicación, planificador proactivo, comprobación de salud"],
       ["`api/rutas_whatsapp.py`", "Webhook, validación de firma, idempotencia, descarga de medios, enrutado por tipo"],
       ["`api/cola_mensajes.py`", "Cola FIFO por atleta con trabajador propio, fusión de ráfagas y drenado ordenado"],
       ["`core/config.py`", "Configuración centralizada y diagnóstico de variables ausentes"],
       ["`core/prompt.json`", "Identidad del coach y diecisiete reglas de decisión"],
       ["`core/tools.json`", "Contrato de las veintitrés herramientas"],
       ["`core/ejercicios.py`", "Canonización y detección de nombres de ejercicios"],
       ["`services/llm_service.py`", "Orquestador del turno: interceptores, telemetría, bucle de herramientas, validadores"],
       ["`services/handlers_v9.py`", "Handlers del motor predictivo, tríada obligatoria, guardia de ejercicio confirmado"],
       ["`services/motor_predictivo.py`", "Estimación de 1RM, carga acumulada, volumen recuperable, autorregulación"],
       ["`services/db_service.py`", "Esquema de telemetría, registro de series, panel analítico"],
       ["`services/repositorio_atleta.py`", "Perfil, marcas, auditoría de cambios, exportación de datasets"],
       ["`services/calculadora_rm.py`", "Fórmulas de repetición máxima por tipo de ejercicio"],
       ["`services/vision_service.py`", "Estimación de pose y clasificación del ejercicio"],
       ["`services/vbt_service.py`", "Métricas de velocidad del levantamiento y envío del vídeo procesado"],
       ["`services/audio_service.py`", "Transcripción de notas de voz"],
       ["`services/proactividad.py`", "Cuestionario matutino, gráficas de progresión"],
       ["`services/conexion_entrenamiento_llm_orquestacion.py`", "Puente hacia el motor de planificación, con normalización de entradas"],
       ["`scripts/Entrenamiento.py`", "Motor de planificación: splits, periodización, reparto de volumen"],
       ["`scripts/crear_base_conocimiento.py`", "Ingesta de literatura científica al índice vectorial"],
       ["`scripts/sembrar_demo.py`", "Genera un atleta sintético con meses de historial creíble y su plan real"],
       ["`scripts/sembrar_conversacion.py`", "Siembra el hilo de WhatsApp previo y la bitácora narrativa del atleta"],
       ["`scripts/verificar_demo.py`", "Pasa lista a las veintiuna comprobaciones previas a una demostración"],
       ["`data/meta_service.py`", "Envío de mensajes y troceo por límite de caracteres"],
       ["`services/testbed/`", "Banco de casos de uso y suites deterministas"]],
      anchos=[6.0, 9.4], tam=8.6)

salto()

doc.add_heading("Apéndice B · Catálogo de herramientas", level=1)

p("Las veintitrés herramientas agrupadas por dominio. El asterisco indica que un interceptor "
  "determinista puede ejecutar la acción sin intervención del modelo.")

tabla(["Herramienta", "Dominio", "Función"],
      [["`generar_rutina`", "Plan", "Genera el mesociclo completo"],
       ["`consultar_rutina_futura` *", "Plan", "Extrae los ejercicios y cargas de una semana o día"],
       ["`consultar_volumen_plan`", "Plan", "Series prescritas por grupo muscular en una semana"],
       ["`avanzar_dia` *", "Plan", "Avanza el cursor del calendario"],
       ["`sustituir_ejercicio` *", "Plan", "Cambia un ejercicio validando que exista en el plan"],
       ["`aplicar_descarga`", "Plan", "Reduce intensidad y series de una semana"],
       ["`pausar_plan` / `reanudar_plan`", "Plan", "Detiene y reactiva el calendario"],
       ["`registrar_serie_completada` *", "Registro", "Persiste una serie con la tríada obligatoria"],
       ["`calcular_rm_exacto` *", "Registro", "Única calculadora de repetición máxima"],
       ["`registrar_peso_corporal` *", "Registro", "Actualiza el peso y la ficha"],
       ["`predecir_carga_optima`", "Analítica", "Carga sugerida según fatiga e historial"],
       ["`analizar_mrv`", "Analítica", "Determina si se excedió el volumen recuperable"],
       ["`autorregular_dia` *", "Analítica", "Reajusta la sesión en curso"],
       ["`consultar_progreso`", "Analítica", "Evolución de 1RM o peso corporal"],
       ["`enviar_grafica_progreso`", "Analítica", "Genera y envía la gráfica de progresión"],
       ["`evaluar_nivel_fuerza`", "Diagnóstico", "Nivel según ratios de fuerza"],
       ["`evaluar_perfil_fisico`", "Diagnóstico", "Composición corporal e índice de masa libre de grasa"],
       ["`consultar_expediente_personal`", "Memoria", "Lee el expediente vectorial del atleta"],
       ["`guardar_en_expediente_personal` *", "Memoria", "Persiste un dato nuevo o modificado"],
       ["`consultar_historial_narrativo`", "Memoria", "Recupera el pasado lejano del atleta"],
       ["`consultar_libro_cientifico`", "Conocimiento", "Consulta la literatura indexada"],
       ["`explicar_tecnica_ejercicio`", "Conocimiento", "Explica la ejecución de un movimiento"]],
      anchos=[6.2, 2.6, 6.6], tam=8.6)

pie_de_pagina()
doc.save(str(SALIDA))
print(f"Documento generado: {SALIDA}")
print(f"Tamaño: {SALIDA.stat().st_size / 1024:.0f} KB")
