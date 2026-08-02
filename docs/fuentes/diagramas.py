# -*- coding: utf-8 -*-
"""Genera los diagramas de la documentación técnica de Iron Progress."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle
from pathlib import Path

SAL = Path(__file__).resolve().parent / "img"
SAL.mkdir(parents=True, exist_ok=True)

# Paleta sobria, legible impresa en blanco y negro también
C = {
    "borde":    "#2B3A55",
    "texto":    "#1A1A1A",
    "entrada":  "#DCE7F5",   # azul claro — entrada/salida externa
    "logica":   "#E5DCF5",   # violeta — orquestación
    "python":   "#D8F0DE",   # verde — determinista (Python decide)
    "modelo":   "#FDE8CE",   # ámbar — LLM
    "datos":    "#F5E6E6",   # rojizo — persistencia
    "guardia":  "#FAD4D4",   # rojo — validadores
    "neutro":   "#EFEFEF",
}
FUENTE = "DejaVu Sans"


def _fig(w, h):
    f = Figure(figsize=(w, h), dpi=200)
    ax = f.add_subplot(111)
    ax.set_xlim(-7, 100); ax.set_ylim(0, 100)
    ax.axis("off")
    f.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
    return f, ax


def caja(ax, x, y, w, h, texto, color="neutro", fs=7.2, bold=False, radio=1.6):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle=f"round,pad=0.35,rounding_size={radio}",
        linewidth=1.0, edgecolor=C["borde"], facecolor=C.get(color, color), zorder=2))
    ax.text(x + w / 2, y + h / 2, texto, ha="center", va="center",
            fontsize=fs, family=FUENTE, color=C["texto"], zorder=3,
            fontweight="bold" if bold else "normal", linespacing=1.45)


def cilindro(ax, x, y, w, h, texto, color="datos", fs=7.0):
    """Nodo de persistencia."""
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.3,rounding_size=0.6",
        linewidth=1.0, edgecolor=C["borde"], facecolor=C[color], zorder=2))
    ax.plot([x, x + w], [y + h - 1.4, y + h - 1.4], color=C["borde"], lw=0.7, zorder=3)
    ax.text(x + w / 2, y + h / 2 - 0.6, texto, ha="center", va="center",
            fontsize=fs, family=FUENTE, color=C["texto"], zorder=3, linespacing=1.4)


def flecha(ax, p1, p2, texto="", estilo="-|>", curva=0.0, fs=6.3, ls="-", dx=0, dy=1.6):
    ax.add_patch(FancyArrowPatch(
        p1, p2, arrowstyle=estilo, mutation_scale=11, linewidth=1.0,
        color=C["borde"], linestyle=ls,
        connectionstyle=f"arc3,rad={curva}", zorder=1,
        shrinkA=2, shrinkB=3))
    if texto:
        mx, my = (p1[0] + p2[0]) / 2 + dx, (p1[1] + p2[1]) / 2 + dy
        ax.text(mx, my, texto, ha="center", va="center", fontsize=fs,
                family=FUENTE, color="#40506B", zorder=4,
                path_effects=[pe.withStroke(linewidth=2.6, foreground="white")])


def banda(ax, y, h, etiqueta, color="#F7F9FC"):
    """La etiqueta va ROTADA en el margen izquierdo: puesta arriba a la izquierda
    la pisaba la primera caja de la banda."""
    ax.add_patch(Rectangle((-6, y), 106, h, facecolor=color, edgecolor="#D5DCE6",
                           linewidth=0.7, zorder=0))
    ax.text(-3.2, y + h / 2, etiqueta, ha="center", va="center", fontsize=6.2,
            family=FUENTE, color="#7A879B", fontweight="bold", zorder=1, rotation=90)


def guardar(f, nombre):
    ruta = SAL / nombre
    f.savefig(ruta, bbox_inches="tight", facecolor="white", pad_inches=0.12)
    print("  ✓", ruta.name)


# =============================================================================
# D1 — Arquitectura en capas
# =============================================================================
def d1():
    f, ax = _fig(9.6, 7.4)
    banda(ax, 84, 16, "CANAL")
    banda(ax, 63, 20, "ENTRADA / API")
    banda(ax, 33, 29, "ORQUESTACIÓN")
    banda(ax, 12, 20, "MOTORES DE DOMINIO")
    banda(ax, 0, 11.5, "PERSISTENCIA")

    caja(ax, 30, 87.5, 40, 9, "WhatsApp Cloud API (Meta)\ntexto · nota de voz · vídeo · botones",
         "entrada", 7.6, True)

    caja(ax, 5, 71, 26, 8.5, "api/rutas_whatsapp.py\nwebhook · firma HMAC\nidempotencia · BackgroundTasks", "entrada")
    caja(ax, 37, 71, 24, 8.5, "services/audio_service.py\nWhisper small int8", "entrada")
    caja(ax, 66, 71, 28, 8.5, "vision_service.py + vbt_service.py\nMediaPipe · YOLO · métricas VBT", "entrada")

    caja(ax, 18, 42, 64, 12.5,
         "services/llm_service.py  —  ORQUESTADOR DEL TURNO\n"
         "interceptores deterministas · telemetría · bucle de herramientas · validadores de salida",
         "logica", 8.0, True)
    caja(ax, 18, 35, 30, 5.4, "core/prompt.json\nidentidad + 17 reglas", "neutro", 6.8)
    caja(ax, 52, 35, 30, 5.4, "core/tools.json\ncontrato de 23 tools", "neutro", 6.8)
    caja(ax, 84, 42, 13, 12.5, "Ollama\nQwen3.6\n27B\n32k ctx", "modelo", 7.4, True)

    caja(ax, 3, 21, 27, 8.5, "scripts/Entrenamiento.py\nMOTOR DE PLANIFICACIÓN\nDUP · splits · volumen", "python", 6.9)
    caja(ax, 34, 21, 30, 8.5, "services/motor_predictivo.py\nMOTOR PREDICTIVO\ne1RM · ACWR · MRV · autorregulación", "python", 6.9)
    caja(ax, 68, 21, 29, 8.5, "services/proactividad.py\nreadiness 6:30 · gráficas\nAPScheduler", "python", 6.9)

    cilindro(ax, 4, 2, 20, 8, "SQLite\niron_progress_telemetria.db\nseries · readiness · eventos", "datos", 6.5)
    cilindro(ax, 27, 2, 18, 8, "SQLite\niron_progress.db\nperfil · marcas RM", "datos", 6.5)
    cilindro(ax, 48, 2, 20, 8, "ChromaDB\nexpediente · biblioteca\nmemoria histórica", "datos", 6.5)
    cilindro(ax, 71, 2, 25, 8, "JSON en disco\nplanes · estado · historial\nde conversación", "datos", 6.5)

    flecha(ax, (50, 87.5), (50, 80.5), "webhook POST", dy=1.4)
    flecha(ax, (18, 71), (18, 55), "", curva=0)
    flecha(ax, (49, 71), (49, 55))
    flecha(ax, (80, 71), (74, 55), curva=-0.1)
    flecha(ax, (82, 48), (84, 48), "prompt + tools", dy=2.0)
    flecha(ax, (84, 45), (82, 45), estilo="-|>", curva=0.0)
    flecha(ax, (30, 42), (18, 30), curva=-0.05)
    flecha(ax, (50, 42), (49, 30))
    flecha(ax, (70, 42), (82, 30), curva=0.05)
    for x0, x1 in ((16, 14), (44, 36), (50, 58), (80, 84)):
        flecha(ax, (x0, 21), (x1, 10), estilo="<|-|>", curva=0.0)
    flecha(ax, (30, 91), (5, 91), "respuesta", dy=1.5)
    guardar(f, "d1_arquitectura.png")


# =============================================================================
# D2 — Ciclo de vida de un mensaje
# =============================================================================
def d2():
    f, ax = _fig(9.6, 8.2)
    y = 92
    pasos = [
        ("1. POST /api/v1/webhook", "firma_valida() verifica X-Hub-Signature-256 (HMAC-SHA256)", "entrada"),
        ("2. Descarte temprano", "_mensaje_ya_visto() · antigüedad > 300 s · usuarios_procesando", "entrada"),
        ("3. Normalización", "normalizar_numero_mexicano(): 521XXXXXXXXXX → 52XXXXXXXXXX", "entrada"),
        ("4. Enrutado por tipo", "botón readiness · vídeo · audio · texto → BackgroundTasks", "entrada"),
        ("5. generar_respuesta_coach()", "lock por atleta + turno_exclusivo_llm() (v13)", "logica"),
        ("6. Estado y calendario", "self-heal de generación colgada · DESCANSO / AL DÍA / ATRASADO", "logica"),
        ("7. Máquina de onboarding", "Python decide el ÚNICO campo pendiente; el modelo solo redacta", "python"),
        ("8. Interceptores deterministas", "series · peso · sustitución · RPE de alarma · recuperación", "python"),
        ("9. Telemetría del turno", "construir_telemetria(): fecha · calendario · rutina de hoy · últimas series", "logica"),
        ("10. RAG", "construir_contexto_enriquecido(): expediente + memoria + literatura", "logica"),
        ("11. Inferencia principal", "chat_llm() con 23 tools · thinking selectivo", "modelo"),
        ("12. Bucle de herramientas", "máx. 3 vueltas · dedupe intra-batch y cross-loop", "modelo"),
        ("13. Validadores de salida", "KG inventados · rutina fantasma · claim falso · estado del plan", "guardia"),
        ("14. Post-proceso", "limpiar_texto_para_whatsapp(): borra tecnicismos y normaliza negritas", "guardia"),
        ("15. Persistencia y envío", "historial en disco (sin podar) → fragmentado a 4000 car. → Meta", "entrada"),
    ]
    h = 5.0
    for i, (t, sub, col) in enumerate(pasos):
        yy = y - i * 6.1
        caja(ax, 6, yy, 88, h, "", col, radio=1.2)
        ax.text(9, yy + h / 2 + 1.05, t, ha="left", va="center", fontsize=7.4,
                family=FUENTE, fontweight="bold", color=C["texto"], zorder=4)
        ax.text(9, yy + h / 2 - 1.35, sub, ha="left", va="center", fontsize=6.3,
                family=FUENTE, color="#42536E", zorder=4)
        if i < len(pasos) - 1:
            flecha(ax, (50, yy), (50, yy - 1.1), estilo="-|>")
    guardar(f, "d2_ciclo_mensaje.png")


# =============================================================================
# D3 — Reparto de responsabilidad: Python decide, el modelo redacta
# =============================================================================
def d3():
    f, ax = _fig(9.6, 5.6)
    caja(ax, 2, 78, 96, 12,
         "PRINCIPIO DE DISEÑO:  el LLM no calcula ni decide sobre datos duros — sólo comunica\n"
         "Todo número que llega al atleta viene de Python o de una herramienta. Nunca del modelo.",
         "logica", 8.2, True)

    caja(ax, 3, 40, 44, 32, "", "python", radio=1.4)
    ax.text(25, 68, "PYTHON  (determinista)", ha="center", fontsize=8.4,
            family=FUENTE, fontweight="bold", zorder=4)
    items_py = [
        "Parseo de la serie reportada (peso, reps, RPE)",
        "Herencia del ejercicio entre turnos (TTL 30 min)",
        "Cálculo del e1RM y de las cargas del plan",
        "Selección del campo de onboarding pendiente",
        "Resolución de qué semana/día pide el atleta",
        "Autorregulación al detectar RPE ≥ 9.5",
        "Validación de sustituciones contra el plan real",
        "Redondeo al incremento real de su barra",
    ]
    for i, t in enumerate(items_py):
        ax.text(5.5, 63.5 - i * 3.0, "▪  " + t, ha="left", fontsize=6.5,
                family=FUENTE, color=C["texto"], zorder=4)

    caja(ax, 53, 40, 44, 32, "", "modelo", radio=1.4)
    ax.text(75, 68, "LLM  (lenguaje)", ha="center", fontsize=8.4,
            family=FUENTE, fontweight="bold", zorder=4)
    items_llm = [
        "Elegir qué herramienta encaja con la intención",
        "Redactar con tono de entrenador de élite",
        "Explicar el porqué técnico de un ajuste",
        "Traducir resultados crudos a lenguaje humano",
        "Sostener la conversación y la empatía",
        "Detectar cifras implausibles y confirmarlas",
        "Adaptar el registro al estado de ánimo",
        "Nunca: multiplicar, estimar ni deducir",
    ]
    for i, t in enumerate(items_llm):
        ax.text(55.5, 63.5 - i * 3.0, "▪  " + t, ha="left", fontsize=6.5,
                family=FUENTE, color=C["texto"], zorder=4)

    caja(ax, 12, 6, 76, 24,
         "CONSECUENCIA MEDIBLE\n\n"
         "El atleta escribe:  «la primera de banca me salió durísima, 100x5 y rpe 9.5»\n\n"
         "Python registra la serie → consulta la prescripción del día (RPE objetivo 7,5 · 5 series)\n"
         "→ ejecuta autorregular_dia → el motor calcula overshoot 2,0 → −6 % → 95 kg · 2 series\n\n"
         "El modelo sólo escribe:  «bajamos a 95 kg y solo harás 2 series más»",
         "neutro", 7.2)
    flecha(ax, (25, 40), (35, 30), curva=-0.08)
    flecha(ax, (75, 40), (65, 30), curva=0.08)
    guardar(f, "d3_reparto.png")


# =============================================================================
# D4 — Redes de validación de salida
# =============================================================================
def d4():
    f, ax = _fig(9.6, 6.4)
    caja(ax, 24, 89, 52, 8, "Borrador de respuesta del modelo", "modelo", 8.0, True)

    redes = [
        ("KG INVENTADOS", "_verificar_kgs_salida()", "Toda cifra en «kg» debe existir en la whitelist del turno:\n"
                                                     "plan · dashboard · payloads de tools · historial · perfil"),
        ("RUTINA FANTASMA", "_parece_rutina_inventada()", "Texto con forma de rutina (N series de M) cuando el plan\n"
                                                          "está vacío y no hay generación en curso"),
        ("CLAIM FALSO", "_CLAIM_ACCION_RE", "Afirma «registré / guardé / apliqué» sin que ninguna\n"
                                            "escritura se haya completado en el turno"),
        ("ESTADO DEL PLAN", "_claims_plan_desmentidos()", "Da por aplicada una descarga, pausa, reanudación o avance\n"
                                                          "de día: se contrasta contra el DISCO, no contra el turno"),
    ]
    for i, (nom, fn, desc) in enumerate(redes):
        yy = 71 - i * 15.5
        caja(ax, 4, yy, 92, 12.4, "", "guardia", radio=1.3)
        ax.text(7, yy + 9.2, f"{i+1}.  {nom}", ha="left", fontsize=7.8,
                family=FUENTE, fontweight="bold", zorder=4)
        ax.text(7, yy + 6.3, fn, ha="left", fontsize=6.4, family=FUENTE,
                color="#8A3A3A", style="italic", zorder=4)
        ax.text(7, yy + 3.0, desc, ha="left", va="center", fontsize=6.4,
                family=FUENTE, color="#42536E", zorder=4, linespacing=1.5)
        ax.text(93, yy + 6.2, "↓", ha="center", fontsize=13, color=C["borde"], zorder=4)

    caja(ax, 4, 1, 43, 7.5, "Si salta → 1 reescritura forzada\ncon la verdad del turno inyectada", "neutro", 6.8)
    caja(ax, 53, 1, 43, 7.5, "Si reincide → plantilla honesta\nescrita en Python. Cero bucles.", "neutro", 6.8)
    flecha(ax, (50, 89), (50, 84))
    guardar(f, "d4_validadores.png")


# =============================================================================
# D5 — Modelo de datos
# =============================================================================
def d5():
    f, ax = _fig(9.6, 6.4)
    ax.text(46.5, 97, "Cuatro almacenes, cada uno con una razón distinta de existir",
            ha="center", fontsize=8.2, family=FUENTE, fontweight="bold")

    PASO = 2.35          # alto de una línea, en unidades del eje

    def bloque(x, y, w, h, titulo, entradas):
        """Un almacén. `entradas` = [(tabla, [líneas de columnas]), ...].
        El cursor avanza por LÍNEAS REALES; calcularlo a ojo solapaba el texto."""
        caja(ax, x, y, w, h, "", "datos", radio=1.4)
        ax.text(x + w / 2, y + h - 3.2, titulo, ha="center", fontsize=7.5,
                family=FUENTE, fontweight="bold", zorder=4)
        cur = y + h - 7.4
        for tabla, lineas in entradas:
            ax.text(x + 2.2, cur, "▸ " + tabla, ha="left", va="center", fontsize=6.6,
                    family=FUENTE, fontweight="bold", color="#7A3030", zorder=4)
            cur -= PASO * 1.1
            for ln in lineas:
                ax.text(x + 4.2, cur, ln, ha="left", va="center", fontsize=5.8,
                        family="DejaVu Sans Mono", color="#43536E", zorder=4)
                cur -= PASO
            cur -= PASO * 0.5

    bloque(0, 54, 45, 40, "iron_progress_telemetria.db   (SQLite)", [
        ("series_log", ["id · numero_usuario · fecha_hora · ejercicio",
                        "peso_kg · repeticiones · rpe_reportado",
                        "vbt_velocidad_media/pico · perdida_velocidad",
                        "rom_cm · dolor_articular · calidad_tecnica"]),
        ("readiness", ["fecha · sueno_horas · sueno_calidad",
                       "estres · dolor · score · fuente"]),
        ("eventos_autorregulacion", ["fecha · tipo · ejercicio · detalle_json"]),
        ("alertas_enviadas", ["tipo · enviada_en · semana_iso"]),
    ])

    bloque(49, 54, 45, 40, "iron_progress.db   (SQLite)", [
        ("atletas", ["peso_kg · altura_m · edad · genero",
                     "porcentaje_grasa · dias_semana · meta",
                     "nivel · incremento_barra_kg"]),
        ("marcas_rm", ["ejercicio · rm · peso_origen · reps_origen",
                       "fuente (onboarding · auto_serie · tool)"]),
        ("historial_perfil", ["campo · valor_anterior · valor_nuevo · ts",
                              "auditoría de cada cambio de ficha"]),
    ])

    bloque(0, 19, 45, 33, "ChromaDB   (vectorial · nomic-embed-text)", [
        ("perfil_dinamico_atleta", ["molestias · material · horarios · hábitos",
                                    "lo que un coach no pregunta dos veces"]),
        ("biblioteca_cientifica", ["≈3.850 fragmentos de literatura indexada",
                                   "regenerable con crear_base_conocimiento.py"]),
        ("memoria_historica_atletas", ["mesociclos cerrados · lesiones antiguas"]),
    ])

    bloque(49, 19, 45, 33, "JSON en disco", [
        ("rutinas_generadas/{num}/*.json", ["el plan: 3 mesociclos × semanas × sesiones"]),
        ("estado_usuarios.json", ["cursor semana/día · fecha programada · pausa",
                                  "onboarding_activo · campo_pendiente"]),
        ("historial_{num}.json", ["conversación completa, telemetría incluida"]),
        ("overrides_{num}.json", ["sustituciones vigentes · descargas"]),
    ])

    caja(ax, 0, 1, 94, 16,
         "POR QUÉ ESTÁ REPARTIDO ASÍ\n\n"
         "series_log es un log de eventos que se consulta por ventana temporal → SQL.   El plan es un documento\n"
         "inmutable que se lee entero y se cachea por firma de archivo → JSON.   El expediente se consulta por\n"
         "SIGNIFICADO («¿le duele algo?»), no por clave → vectorial.   El estado del calendario cambia varias veces\n"
         "por turno y es diminuto → JSON en memoria con sincronización al escribir.",
         "neutro", 6.8)
    guardar(f, "d5_datos.png")


# =============================================================================
# D6 — Pipeline de generación del plan
# =============================================================================
def d6():
    f, ax = _fig(9.6, 5.2)
    ax.text(50, 96, "Del onboarding al plan en disco: quién decide en cada paso",
            ha="center", fontsize=8.0, family=FUENTE, fontweight="bold")

    caja(ax, 0, 68, 22, 22,
         "1. RECOLECCIÓN\n\ncalcular_faltantes()\ndevuelve el ÚNICO\ncampo pendiente.\n"
         "El modelo solo\nredacta la pregunta.", "python", 6.1)
    caja(ax, 24, 68, 22, 22,
         "2. PARSEO\n\nprocesar_respuesta_\nonboarding() +\n_parsear_campos_\nanclados() capturan\n"
         "datos fuera de orden.", "python", 6.1)
    caja(ax, 48, 68, 22, 22,
         "3. DISPARO\n\nSin faltantes,\n_disparar_pipeline_\ngeneracion() lanza\n"
         "la tarea de fondo.\nEl atleta no espera.", "logica", 6.1)
    caja(ax, 72, 68, 22, 22,
         "4. PUENTE\n\nconexion_entrenamiento\n_llm_orquestacion.py\nnormaliza meta y nivel,\n"
         "ajusta días al split\ncientífico más cercano.", "logica", 6.4)

    caja(ax, 6, 30, 88, 34, "", "python", radio=1.4)
    ax.text(50, 60, "5.  scripts/Entrenamiento.py — Distribucion_Torso_Pierna.generar_programa()",
            ha="center", fontsize=7.6, family=FUENTE, fontweight="bold", zorder=4)
    reglas = [
        "SPLITS_CIENTIFICOS — 3 a 7 días, con 48-72 h entre sesiones del mismo grupo muscular",
        "MATRIZ_FUSION_DUP — proporción de días por bloque: Acumulamiento 2H:1F · Fuerza 2F:1H · Realización 2F:1P",
        "VOLUMEN_SEMANAL_POR_PATRON — asimetría MEV-MAV por patrón (empuje ≠ tracción)",
        "PESO_VOLUMEN_SEMANAL_DUP — reparto de series: hipertrofia 50-60 % · fuerza 20-25 % · potencia 15-20 %",
        "Guardia SNC — prohibido programar dos días de fuerza pesada en días de calendario consecutivos",
        "Factor de estiramiento 1.0 / 0.5 — las series físicas se calculan para cubrir el volumen EFECTIVO",
        "Complementarios — inyectados al final de la sesión, 75 % del volumen medio, siempre a RPE 9.0",
    ]
    for i, r in enumerate(reglas):
        ax.text(9, 55.5 - i * 3.4, "▪  " + r, ha="left", fontsize=6.3,
                family=FUENTE, color=C["texto"], zorder=4)

    caja(ax, 6, 12, 40, 13,
         "6. EXPORTACIÓN\n\nresources/rutinas_generadas/{num}/\n"
         "1_acumulamiento.json · 2_fuerza.json\n3_realizacion.json", "datos", 6.5)
    caja(ax, 54, 12, 40, 13,
         "7. LECTURA EN CALIENTE\n\nobtener_rutina_del_json() aplica RM,\n"
         "sustituciones y descargas, y redondea\nal incremento real de su barra", "logica", 6.5)

    caja(ax, 16, 1, 68, 8,
         "El plan guarda PORCENTAJES de 1RM, no kilos. Los kg se calculan en cada lectura contra las marcas\n"
         "vigentes: así una marca nueva reajusta el mesociclo entero sin regenerar nada.", "neutro", 6.6)

    for x in (22, 46, 70):
        flecha(ax, (x, 79), (x + 2, 79), estilo="-|>")
    flecha(ax, (50, 68), (50, 64.5))
    flecha(ax, (26, 30), (26, 25.5))
    flecha(ax, (46, 18.5), (54, 18.5), estilo="-|>")
    guardar(f, "d6_pipeline_plan.png")


# =============================================================================
# D7 — Mapa de acoplamiento entre ficheros
# =============================================================================
def d7():
    f, ax = _fig(9.6, 6.8)
    ax.text(50, 97, "Quién importa a quién: el orquestador es el único punto de acoplamiento",
            ha="center", fontsize=8.0, family=FUENTE, fontweight="bold")

    caja(ax, 34, 47, 32, 11, "services/llm_service.py\nORQUESTADOR\n≈5.200 líneas", "logica", 8.0, True)

    nodos = [
        (3, 84, 26, 8, "main.py\nlifespan · APScheduler · CORS", "entrada"),
        (36, 84, 28, 8, "api/rutas_whatsapp.py\nwebhook · media · idempotencia", "entrada"),
        (71, 84, 26, 8, "data/meta_service.py\nenvío · troceo a 4000 car.", "entrada"),
        (3, 68, 26, 8, "core/config.py\nsettings · faltantes()", "neutro"),
        (36, 68, 28, 8, "core/prompt.json + tools.json\ncontrato del modelo", "neutro"),
        (71, 68, 26, 8, "core/ejercicios.py\ncanonizar() · detectar()", "neutro"),
        (2, 30, 27, 10, "services/handlers_v9.py\nDISPATCHER_V9 · tríada obligatoria\nguarda de ejercicio (TTL 30 min)", "python"),
        (71, 30, 27, 10, "services/motor_predictivo.py\ne1RM · ACWR · MRV\npredicción bayesiana", "python"),
        (2, 15, 27, 10, "services/db_service.py\nesquema · guardar_serie_sql\ndashboard analítico", "datos"),
        (36, 15, 28, 10, "services/repositorio_atleta.py\nperfil · marcas · auditoría\ndataset de progresión", "datos"),
        (71, 15, 27, 10, "services/calculadora_rm.py\ncalcular_1rm · TipoEjercicio", "python"),
        (2, 1, 27, 9, "services/proactividad.py\nreadiness · gráficas e1RM", "python"),
        (36, 1, 28, 9, "conexion_entrenamiento_llm_\norquestacion.py → Entrenamiento.py", "logica"),
        (71, 1, 27, 9, "vision_service · vbt_service\naudio_service", "entrada"),
    ]
    for x, y, w, h, t, c in nodos:
        caja(ax, x, y, w, h, t, c, 6.3)

    flecha(ax, (50, 84), (50, 76.5), "include_router", dy=1.5)
    flecha(ax, (50, 68), (50, 58.5), "generar_respuesta_coach()", dy=1.5)
    flecha(ax, (29, 88), (36, 88), estilo="-|>")
    flecha(ax, (64, 88), (71, 88), estilo="-|>")
    # motores de dominio <-> orquestador
    flecha(ax, (29, 37), (34, 49), estilo="<|-|>", curva=0.06)
    flecha(ax, (71, 37), (66, 49), estilo="<|-|>", curva=-0.06)
    # capa de datos <-> su consumidor inmediato
    flecha(ax, (16, 25), (16, 30), estilo="<|-|>", ls=":")
    flecha(ax, (85, 25), (85, 30), estilo="<|-|>", ls=":")
    flecha(ax, (50, 25), (44, 47), estilo="<|-|>", ls=":", curva=0.08)
    # fila inferior: servicios satélite que el orquestador invoca
    flecha(ax, (16, 10), (16, 15), estilo="-|>", ls=":")
    flecha(ax, (50, 10), (50, 15), estilo="-|>", ls=":")
    flecha(ax, (85, 10), (85, 15), estilo="-|>", ls=":")
    ax.text(50, 44.2, "toda dependencia de dominio pasa por el orquestador: los motores no se conocen entre sí",
            ha="center", fontsize=6.2, family=FUENTE, color="#7A879B", style="italic",
            path_effects=[pe.withStroke(linewidth=3.0, foreground="white")], zorder=5)
    guardar(f, "d7_acoplamiento.png")


if __name__ == "__main__":
    print("Generando diagramas…")
    d1(); d2(); d3(); d4(); d5(); d6(); d7()
    print("Listo.")
