import ollama
import json
import os
import asyncio
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import aiofiles
from services.db_service import guardar_serie_sql, inicializar_db, obtener_dashboard_analitico
from services.conexion_entrenamiento_llm_orquestacion import recibir_peticion
from services.calculadora_rm import calcular_1rm, TipoEjercicio
from services.metricas_atleta import evaluar_perfil_completo
from services.repositorio_atleta import repositorio   # v4: SQLite reemplaza a perfil_*.json / rms_*.json
from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma

# ==============================================================================
# LOGGING A CONSOLA (v3)
# ==============================================================================
VERBOSE = True   # ponlo en False en producción si quieres silenciar el detalle

def _trunc(s, n: int = 220) -> str:
    s = str(s).replace("\n", " ⏎ ")
    return s if len(s) <= n else s[:n] + "…"

def log(usuario: str, etapa: str, detalle="") -> None:
    """Salida uniforme: [HH:MM:SS][usuario][ETAPA] detalle"""
    if VERBOSE:
        print(f"[{datetime.now().strftime('%H:%M:%S')}][{usuario}][{etapa}] {_trunc(detalle)}")

# ==============================================================================
# VARIABLES GLOBALES & REGEX
# ==============================================================================
historial_memoria = {}
cliente_ollama    = ollama.AsyncClient()
ORDEN_DIAS        = ["dia_1_fuerza", "dia_2_hipertrofia", "dia_3_potencia"]

# v2: verbos ampliados (antes "puedes hacerme una rutina?" NO disparaba el modo creación)
EN_CREACION_RE = re.compile(
    r"(?:\b(hazme|haz|hacerme|hacer|crea\w*|arma\w*|genera\w*|dise[ñn]a\w*|"
    r"quiero|necesito|empezar|comenzar|nuev[oa])\b"
    r".{0,50}\b(rutina|plan|programa|mesociclo)\b)"
    r"|(?:\b(rutina|plan|programa|mesociclo)\b.{0,50}\b(nuev[oa]|desde\s+cero)\b)",
    re.IGNORECASE)

CANCELAR_ONBOARDING_RE = re.compile(
    r"\b(cancela\w*|olv[ií]dalo|d[eé]jalo|mejor\s+luego|despu[eé]s\s+lo\s+hacemos|"
    r"ya\s+no\s+quiero|otro\s+d[ií]a)\b", re.IGNORECASE)

AFIRMACION_CORTA_RE = re.compile(
    r"^\s*(s[ií]+|sale|dale|va|claro|obvio|porfa|por\s*favor|hazla|h[aá]zmela|adelante|ok(ey)?)\s*[!.]*\s*$",
    re.IGNORECASE)

# v3 — Fallo 4 del informe: restricciones negativas ("no puedo con 215kg") que el
# modelo ignoraba por inercia. Se detectan en código y se inyectan con prioridad.
RESTRICCION_RE = re.compile(
    r"\bno\s+(?:puedo|logro|llego|aguanto|me\s+sale[n]?|me\s+da)\b|\bimposible\b|"
    r"\b(?:est[aá]|es|se\s+siente)\s+(?:muy|demasiado|bien)\s+pesad[oa]\b|"
    r"\bme\s+qued[ée]\b|\bfall[eé]\b|\bno\s+complet[ée]\b",
    re.IGNORECASE)

# v3 — Fallo 3 del informe: velocidades VBT (0.255, 0.270...) que el modelo
# "leía" con narrativa en vez de aritmética. Se analizan en código.
VELOCIDAD_RE = re.compile(r"\b0[.,]\d{2,3}\b")

# v4.1 — fin de sesión: pista suave para que el modelo considere avanzar_dia
FIN_SESION_RE = re.compile(
    r"\b(?:ya\s+)?(?:termin[ée]|acab[ée])\b(?:.*\b(?:entren\w*|sesi[oó]n|rutina|por\s+hoy|todo)\b)?"
    r"|\blisto\s+por\s+hoy\b|\bfin\s+de\s+la\s+sesi[oó]n\b",
    re.IGNORECASE)

PORCENTAJE_RE = re.compile(r"([\d.]+)\s*%")
_ACCESORIO_KEYWORDS = ("mancuerna", "copa", "goblet", "maquina", "máquina", "polea",
                       "vuelo", "curl", "extension", "extensión", "crunch", "remo",
                       "face pull", "hip thrust", "zancada", "prensa", "femoral",
                       "gemelo", "elevacion", "elevación", "jalon", "jalón")
_HEREDA_RM = [
    (re.compile(r"^press\s+banc[oa](\s+plano)?$"), "press banco plano"),
    (re.compile(r"^sentadillas?(\s+libre)?$"), "sentadilla"),
    (re.compile(r"^peso\s+muerto$"), "peso muerto"),
    (re.compile(r"^dominadas?(\s+lastradas?)?$"), "dominadas"),
]

# ==============================================================================
# RUTAS
# ==============================================================================
ruta_script_actual = os.path.dirname(os.path.abspath(__file__))
ruta_raiz_proyecto = os.path.dirname(ruta_script_actual)
carpeta_data       = os.path.join(ruta_raiz_proyecto, "data")
carpeta_core       = os.path.join(ruta_raiz_proyecto, "core")
carpeta_resources  = os.path.join(ruta_raiz_proyecto, "resources")

os.makedirs(carpeta_data,      exist_ok=True)
os.makedirs(carpeta_resources, exist_ok=True)

ruta_prompt_maestro = os.path.join(carpeta_core, "prompt.json")
ruta_estado         = os.path.join(carpeta_data, "estado_usuarios.json")
ruta_tools          = os.path.join(carpeta_core, "tools.json")

# ==============================================================================
# CONSTANTES DE MEMORIA INTELIGENTE
# ==============================================================================
RESUMEN_MARKER    = "[RESUMEN_SESIONES]:"
TELEMETRIA_INICIO = "\n\n--- TELEMETRÍA ---"
TELEMETRIA_FIN    = "\n--- FIN TELEMETRÍA ---"
VENTANA_RECIENTE  = 20
UMBRAL_COMPRESION = 30
LIMITE_EMERGENCIA = 40

OPCIONES_QWEN_32B = {
    "num_gpu": 99,
    "temperature": 0.3,
    "top_p": 0.85,
    "top_k": 20,
    "repeat_penalty": 1.1,
    "num_ctx": 16384,
    "num_predict": 2048,
}

# v3 — ANTI-FREEZE: en 24 GB de VRAM el 32B + KV de 16k ocupa ~22-23 GB.
# Si dos peticiones al modelo se solapan (chat + compresor, o chat + generador),
# Ollama intenta atender ambas y el sistema entra en swap/thrashing -> PC congelada.
# Este semáforo garantiza UNA sola inferencia a la vez en todo el proceso.
SEM_LLM = asyncio.Semaphore(1)
TIMEOUT_CHAT = 180.0   # ninguna llamada al modelo puede colgar el orquestador para siempre

async def chat_llm(mensajes, tools=None, opciones=None, quien="chat"):
    """Único punto de entrada al 32B: serializado, con timeout y con log."""
    t0 = time.monotonic()
    async with SEM_LLM:
        espera = time.monotonic() - t0
        if espera > 1.0:
            print(f"[LLM] '{quien}' esperó {espera:.1f}s el turno del modelo")
        kwargs = dict(model="qwen3:32b",
                      messages=mensajes,
                      keep_alive=-1,
                      think=False,
                      options=opciones or OPCIONES_QWEN_32B)
        if tools:
            kwargs["tools"] = tools
        resp = await asyncio.wait_for(cliente_ollama.chat(**kwargs), timeout=TIMEOUT_CHAT)
        print(f"[LLM] '{quien}' respondió en {time.monotonic()-t0:.1f}s "
              f"({len(mensajes)} msgs de entrada)")
        return resp

# ==============================================================================
# CHROMADB
# ==============================================================================
carpeta_chroma   = os.path.join(carpeta_data, "chroma_db")
motor_embeddings = OllamaEmbeddings(model="nomic-embed-text")

if os.path.exists(carpeta_chroma) and os.listdir(carpeta_chroma):
    try:
        db_cientifica = Chroma(persist_directory=carpeta_chroma,
                               embedding_function=motor_embeddings,
                               collection_name="biblioteca_cientifica")
        db_memoria_atleta = Chroma(persist_directory=carpeta_chroma,
                                   embedding_function=motor_embeddings,
                                   collection_name="memoria_historica_atletas")
        db_perfil_atleta  = Chroma(persist_directory=carpeta_chroma,
                                   embedding_function=motor_embeddings,
                                   collection_name="perfil_dinamico_atleta")
        print("[SISTEMA] ChromaDB: 3 colecciones cargadas.")
    except Exception as e:
        print(f"[ALERTA CRÍTICA] Fallo al cargar ChromaDB: {e}")
        db_cientifica = db_memoria_atleta = db_perfil_atleta = None
else:
    db_cientifica = db_memoria_atleta = db_perfil_atleta = None
    print("[SISTEMA] No se detectó ChromaDB.")

# ==============================================================================
# ALMACÉN CANÓNICO DE RM Y PERFILES
# v4: los datos viven en SQLite (services/repositorio_atleta.py). Estas funciones
# conservan EXACTAMENTE las mismas firmas y formas de retorno que la versión
# JSON, así que el resto del orquestador no cambia. El repositorio migra solo
# los perfil_*.json / rms_*.json existentes la primera vez (los renombra a
# *.json.migrado, no borra nada) y además guarda HISTORIAL fechado de cada
# marca y cada cambio de perfil: el dataset del futuro modelo de rendimiento.
# ==============================================================================
def ruta_rms(numero_usuario: str) -> str:
    """Legado: solo por compatibilidad con scripts viejos (reset_usuario, etc.)."""
    return os.path.join(carpeta_data, f"rms_{numero_usuario}.json")

def ruta_perfil(u):
    """Legado: solo por compatibilidad con scripts viejos."""
    return os.path.join(carpeta_data, f"perfil_{u}.json")

def _norm_ej(nombre) -> str:
    return re.sub(r"\s+", " ", str(nombre).strip().lower())

def cargar_rms(numero_usuario: str) -> dict:
    return repositorio.obtener_rms(numero_usuario)

def guardar_rm(numero_usuario: str, ejercicio: str, rm,
               peso_origen=None, reps_origen=None, fuente: str = "chat") -> None:
    """v4: cada guardado INSERTA una marca fechada (historial completo en SQL)."""
    try:
        repositorio.guardar_rm(numero_usuario, ejercicio, rm,
                               peso_origen=peso_origen, reps_origen=reps_origen,
                               fuente=fuente)
    except Exception as e:
        print(f"[REPO-ERROR] guardar_rm({ejercicio}): {e}")

def cargar_perfil(u) -> dict:
    return repositorio.obtener_perfil(u)

def actualizar_perfil(u, **campos):
    try:
        repositorio.actualizar_perfil(u, **campos)
    except Exception as e:
        print(f"[REPO-ERROR] actualizar_perfil: {e}")

def _actualizar_estado_json(numero_usuario: str, **campos):
    try:
        with open(ruta_estado, "r", encoding="utf-8") as f:
            estados = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        estados = {}
    est = estados.get(numero_usuario) or {
        "semana": 1, "dia": 1,
        "fecha_programada": datetime.now().strftime("%Y-%m-%d"),
        "ultima_fecha_completada": ""
    }
    est.update(campos)
    estados[numero_usuario] = est
    with open(ruta_estado, "w", encoding="utf-8") as f:
        json.dump(estados, f, indent=4, ensure_ascii=False)

# ==============================================================================
# v2: MÁQUINA DE ESTADOS DE ONBOARDING (determinista, el LLM solo redacta)
# ==============================================================================
# Orden fijo de recolección. clave -> (dónde vive, descripción para que el coach la pida)
CAMPOS_ONBOARDING = [
    ("peso_kg",          "perfil", "su peso corporal actual en kg"),
    ("altura_m",         "perfil", "su altura"),
    ("edad",             "perfil", "su edad"),
    ("genero",           "perfil", "si es hombre o mujer"),
    ("porcentaje_grasa", "perfil", "su porcentaje de grasa corporal aproximado"),
    ("dias_semana",      "perfil", "cuántos días por semana puede entrenar (1 a 7)"),
    ("meta",             "perfil", "su meta principal: powerlifting (fuerza), hipertrofia o powerbuilding"),
    ("rm_banca",         "rm",     "su mejor marca en press de banca plano (peso y repeticiones)"),
    ("rm_sentadilla",    "rm",     "su mejor marca en sentadilla libre (peso y repeticiones)"),
    ("rm_peso_muerto",   "rm",     "su mejor marca en peso muerto (peso y repeticiones)"),
    ("rm_dominadas",     "rm",     "su marca en dominadas: kg de lastre y repeticiones (o si las hace solo a peso corporal)"),
]
RM_CANONICO = {
    "rm_banca": "press banco plano",
    "rm_sentadilla": "sentadilla",
    "rm_peso_muerto": "peso muerto",
    "rm_dominadas": "dominadas",
}
_KW_EJERCICIO = [
    (("banca", "banco", "bench", "press plano"), "rm_banca"),
    (("sentadilla", "squat"),                    "rm_sentadilla"),
    (("muerto", "deadlift"),                     "rm_peso_muerto"),
    (("dominada", "lastre", "pull up", "pull-up"), "rm_dominadas"),
]
_PALABRA_NUM = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
                "cinco": 5, "seis": 6, "siete": 7}
_NUM_RE = re.compile(r"(\d+(?:[.,]\d+)?)")

def _primer_numero(texto: str):
    m = _NUM_RE.search(texto.replace(",", "."))
    return float(m.group(1)) if m else None

def calcular_faltantes(numero_usuario: str) -> list[str]:
    p = cargar_perfil(numero_usuario)
    r = cargar_rms(numero_usuario)
    faltan = []
    for campo, tipo, _desc in CAMPOS_ONBOARDING:
        if tipo == "rm":
            if RM_CANONICO[campo] not in r:
                faltan.append(campo)
        elif p.get(campo) in (None, ""):
            faltan.append(campo)
    return faltan

def _desc_campo(campo: str) -> str:
    for c, _t, desc in CAMPOS_ONBOARDING:
        if c == campo:
            return desc
    return campo

def _calcular_y_guardar_rm(numero_usuario: str, ejercicio_canonico: str,
                           peso: float, reps: int):
    """Calcula el e1RM en CÓDIGO y lo persiste. Devuelve el 1RM o None si es implausible."""
    if not (1 <= reps <= 15):
        return None
    try:
        if reps == 1:
            guardar_rm(numero_usuario, ejercicio_canonico, float(peso),
                       peso_origen=peso, reps_origen=1, fuente="onboarding")
            return round(float(peso), 1)
        # TODO: si tu enum tiene un tipo específico para dominadas lastradas, úsalo aquí.
        rm = calcular_1rm(TipoEjercicio("peso_libre"), reps, float(peso))
        guardar_rm(numero_usuario, ejercicio_canonico, rm,
                   peso_origen=peso, reps_origen=reps, fuente="onboarding")
        log(numero_usuario, "RM-GUARDADO", f"{ejercicio_canonico}: {peso}kg x {reps} -> 1RM {rm}")
        return round(float(rm), 1)
    except Exception as e:
        print(f"[ONBOARDING RM] error calculando {ejercicio_canonico}: {e}")
        return None

_MARCA_KG_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*kg\s*(?:por|x|×)\s*(\d+)", re.IGNORECASE)

def _parsear_marca(fragmento: str):
    """Devuelve (peso, reps) de un fragmento de texto, o None."""
    # 'kg' explícito ancla el peso: evita el swap erróneo en casos como '30kg por 78 reps'
    m = _MARCA_KG_RE.search(fragmento.replace(",", "."))
    if m:
        return float(m.group(1)), int(m.group(2))
    serie = detectar_serie_en_mensaje(fragmento)
    if serie:
        return serie["peso"], serie["reps"]
    n = _primer_numero(fragmento)
    if n is not None and 5 <= n <= 450:
        return n, 1   # un solo número = intento a 1 repetición
    return None

def parsear_batch_rms(numero_usuario: str, mensaje: str) -> list[str]:
    """Extrae varias marcas de un solo mensaje ('150x3 en banca, 215 en sentadilla...')."""
    guardados = []
    partes = re.split(r"[,;\n]|\s+y\s+", mensaje.lower())
    for parte in partes:
        campo_detectado = None
        for kws, campo in _KW_EJERCICIO:
            if any(k in parte for k in kws):
                campo_detectado = campo
                break
        if not campo_detectado:
            continue
        # dominadas solo a peso corporal (lastre 0)
        if campo_detectado == "rm_dominadas" and re.search(
                r"peso corporal|sin lastre|sin peso|solo mis", parte):
            guardar_rm(numero_usuario, "dominadas", 0, fuente="onboarding_default")
            guardados.append("dominadas: solo peso corporal (lastre 0 kg)")
            continue
        marca = _parsear_marca(parte)
        if not marca:
            continue
        peso, reps = marca
        rm = _calcular_y_guardar_rm(numero_usuario, RM_CANONICO[campo_detectado], peso, int(reps))
        if rm is not None:
            guardados.append(f"{RM_CANONICO[campo_detectado]}: 1RM estimado {rm:g} kg "
                             f"(a partir de {peso:g} kg x {int(reps)})")
    return guardados

def procesar_respuesta_onboarding(numero_usuario: str, campo: str, mensaje: str):
    """
    Intenta interpretar el mensaje del atleta como respuesta al campo pendiente.
    Devuelve (exito: bool, feedback: str para la telemetría del coach).
    """
    msg = mensaje.lower().strip()

    # 0) Si vienen varias marcas en un solo mensaje, se aprovechan TODAS.
    batch = parsear_batch_rms(numero_usuario, msg)
    if batch:
        return True, "Marcas guardadas: " + "; ".join(batch)

    if campo == "peso_kg":
        v = _primer_numero(msg)
        if v is not None and 35 <= v <= 250:
            actualizar_perfil(numero_usuario, peso_kg=round(v, 1))
            return True, f"peso corporal {v:g} kg"

    elif campo == "altura_m":
        v = _primer_numero(msg)
        if v is not None:
            if 120 <= v <= 230:
                v = v / 100.0
            if 1.20 <= v <= 2.30:
                actualizar_perfil(numero_usuario, altura_m=round(v, 2))
                return True, f"altura {v:.2f} m"

    elif campo == "edad":
        v = _primer_numero(msg)
        if v is not None and 12 <= v <= 90:
            actualizar_perfil(numero_usuario, edad=int(v))
            return True, f"edad {int(v)} años"

    elif campo == "genero":
        # Jerga ampliada para evitar bucles de rechazo
        if re.search(r"\b(hombre|masculin\w*|var[oó]n|chico|vato|wey|muchacho|ni[ñn]o)\b", msg):
            actualizar_perfil(numero_usuario, genero="hombre")
            return True, "género hombre"
        if re.search(r"\b(mujer|femenin\w*|chica|morra|ni[ñn]a)\b", msg):
            actualizar_perfil(numero_usuario, genero="mujer")
            return True, "género mujer"

    elif campo == "porcentaje_grasa":
        # Red de seguridad: si no sabe su porcentaje, 15% por defecto
        if re.search(r"no\s+(s[eé]|tengo\s+idea)|ni\s+idea", msg):
            actualizar_perfil(numero_usuario, porcentaje_grasa=15.0)
            log(numero_usuario, "ONB-DEFAULT", "grasa 15% por defecto (no sabía)")
            return True, "grasa 15% (estimado por defecto porque no sabía)"
        v = _primer_numero(msg)
        if v is not None and 3 <= v <= 60:
            actualizar_perfil(numero_usuario, porcentaje_grasa=round(v, 1))
            return True, f"{v:g}% de grasa corporal"

    elif campo == "dias_semana":
        v = _primer_numero(msg)
        if v is None:
            for palabra, num in _PALABRA_NUM.items():
                if re.search(rf"\b{palabra}\b", msg):
                    v = num
                    break
        if v is not None and 1 <= v <= 7:
            actualizar_perfil(numero_usuario, dias_semana=int(v))
            return True, f"{int(v)} días de entrenamiento por semana"

    elif campo == "meta":
        if re.search(r"powerbuild|fuerte\s+y\s+grande", msg):
            actualizar_perfil(numero_usuario, meta="powerbuilding")
            return True, "meta powerbuilding"
        if re.search(r"hipertrofia|masa|volumen|m[uú]sculo|crecer|mamado|grande|bajar|definir|flaco", msg):
            actualizar_perfil(numero_usuario, meta="hipertrofia")
            return True, "meta hipertrofia"
        if re.search(r"powerlifting|fuerza|pesado", msg):
            actualizar_perfil(numero_usuario, meta="powerlifting")
            return True, "meta powerlifting (fuerza)"

    elif campo in RM_CANONICO:
        # ORDEN CRÍTICO (v3): la marca real se parsea PRIMERO.
        # Tu red anterior con `|0|` hacía match con el dígito '0' de "150kg" o "30kg"
        # y guardaba barra vacía (20 kg) o lastre 0 en marcas perfectamente válidas.
        marca = _parsear_marca(msg)
        if marca:
            peso, reps = marca
            if reps > 15:
                log(numero_usuario, "ONB-RECHAZO", f"{campo}: {peso}kg x {reps} reps fuera de rango 1-15")
                return False, (f"Reportó {peso:g} kg x {int(reps)} reps: FUERA del rango confiable "
                               f"(1-15) para estimar un 1RM. NO se guardó. Pregúntale con "
                               f"naturalidad si la cifra es correcta (¿fueron {int(reps)} reps de verdad?).")
            rm = _calcular_y_guardar_rm(numero_usuario, RM_CANONICO[campo], peso, int(reps))
            if rm is not None:
                return True, (f"{RM_CANONICO[campo]}: 1RM estimado {rm:g} kg "
                              f"(a partir de {peso:g} kg x {int(reps)})")

        # Dominadas a peso corporal / no hace dominadas (solo palabras completas)
        if campo == "rm_dominadas" and re.search(
                r"peso corporal|sin lastre|sin peso|solo mis|\bcero\b|\bninguna\b|"
                r"no\s+hago|no\s+me\s+salen|no\s+puedo|\bnada\b|\bnunca\b", msg):
            guardar_rm(numero_usuario, "dominadas", 0, fuente="onboarding_default")
            log(numero_usuario, "ONB-DEFAULT", "dominadas lastre 0 (peso corporal / no hace)")
            return True, "dominadas a peso corporal (lastre 0 kg)"

        # Red de seguridad general: principiante que no conoce su marca -> barra vacía
        if re.search(r"no\s+(?:la\s+)?(?:s[eé]|tengo)|\bcero\b|\bprincipiante\b|"
                     r"primera\s+vez|nunca\s+h[ae]", msg):
            guardar_rm(numero_usuario, RM_CANONICO[campo], 20, fuente="onboarding_default")
            log(numero_usuario, "ONB-DEFAULT", f"{RM_CANONICO[campo]}: barra vacía 20kg (no sabe marca)")
            return True, f"{RM_CANONICO[campo]}: barra vacía (20 kg) por no saber su marca"

    return False, ""

def _parsear_campos_anclados(numero_usuario: str, mensaje: str, faltantes: list[str]) -> list[str]:
    """
    v3.1 — FIX del bug 're-pedir datos ya dados': el atleta muchas veces manda
    VARIOS datos en un mensaje ("peso 82kg, mido 1.73, 24 años...") o los incluye
    en el mensaje que dispara el onboarding. Antes solo se parseaba el campo
    pendiente y el resto se perdía -> el coach los volvía a pedir.

    Este parser captura cualquier campo FALTANTE que venga anclado a una palabra
    clave inequívoca (kg, cm, años, %, días, etc.). Devuelve la lista de feedbacks.
    """
    msg = str(mensaje).lower().strip()
    guardados: list[str] = []

    # RMs por keyword de ejercicio (solo si falta alguno; el guardado es idempotente)
    canon_faltantes = {RM_CANONICO[c] for c in faltantes if c in RM_CANONICO}
    if canon_faltantes:
        for fb in parsear_batch_rms(numero_usuario, msg):
            if fb.split(":")[0].strip() in canon_faltantes:
                guardados.append(fb)

    # Segmentos SIN keyword de ejercicio (para que "banca 150kg" no se lea como peso corporal)
    segs = [s for s in re.split(r"[,;\n]|\s+y\s+", msg)
            if not any(k in s for kws, _c in _KW_EJERCICIO for k in kws)]
    libre = " , ".join(segs)

    if "peso_kg" in faltantes:
        m = (re.search(r"(\d+(?:\.\d+)?)\s*(?:kg|kilos?)\b(?!\s*(?:por|x|×|de\s+lastre))", libre)
             or re.search(r"\bpes[oa]\w*\s*(?:es|de|:)?\s*(\d+(?:\.\d+)?)", libre))
        if m:
            v = float(m.group(1))
            if 35 <= v <= 250:
                actualizar_perfil(numero_usuario, peso_kg=round(v, 1))
                guardados.append(f"peso corporal {v:g} kg")

    if "altura_m" in faltantes:
        m = (re.search(r"(\d+(?:\.\d+)?)\s*(?:cm|cent[ií]metros?)\b", libre)
             or re.search(r"(\d\.\d+)\s*(?:m|mts?|metros?)\b", libre)
             or re.search(r"\b(?:mido|altura)\s*(?:es|de|:)?\s*(\d+(?:\.\d+)?)", libre))
        if m:
            v = float(m.group(1))
            if 120 <= v <= 230:
                v = v / 100.0
            if 1.20 <= v <= 2.30:
                actualizar_perfil(numero_usuario, altura_m=round(v, 2))
                guardados.append(f"altura {v:.2f} m")

    if "edad" in faltantes:
        m = (re.search(r"(\d{2})\s*a[ñn]os\b", libre)
             or re.search(r"\bedad\s*(?:es|de|:)?\s*(\d{2})", libre))
        if m:
            v = int(m.group(1))
            if 12 <= v <= 90:
                actualizar_perfil(numero_usuario, edad=v)
                guardados.append(f"edad {v} años")

    if "genero" in faltantes:
        if re.search(r"\b(hombre|masculin\w*|var[oó]n|chico|vato|wey|muchacho)\b", msg):
            actualizar_perfil(numero_usuario, genero="hombre")
            guardados.append("género hombre")
        elif re.search(r"\b(mujer|femenin\w*|chica|morra)\b", msg):
            actualizar_perfil(numero_usuario, genero="mujer")
            guardados.append("género mujer")

    if "porcentaje_grasa" in faltantes:
        m = (re.search(r"(\d+(?:\.\d+)?)\s*%", libre)
             or re.search(r"\bgrasa\s*(?:es|de|:)?\s*(\d+(?:\.\d+)?)", libre))
        if m:
            v = float(m.group(1))
            if 3 <= v <= 60:
                actualizar_perfil(numero_usuario, porcentaje_grasa=round(v, 1))
                guardados.append(f"{v:g}% de grasa corporal")

    if "dias_semana" in faltantes:
        m = (re.search(r"(\d)\s*d[ií]as?\b", libre)
             or re.search(r"\bentren[oa]r?\s*(\d)\b", libre)
             or re.search(r"(\d)\s*veces\s*(?:por|a\s+la)\s*semana", libre))
        if m:
            v = int(m.group(1))
            if 1 <= v <= 7:
                actualizar_perfil(numero_usuario, dias_semana=v)
                guardados.append(f"{v} días de entrenamiento por semana")

    if "meta" in faltantes:
        if re.search(r"powerbuild|fuerte\s+y\s+grande", msg):
            actualizar_perfil(numero_usuario, meta="powerbuilding")
            guardados.append("meta powerbuilding")
        elif re.search(r"hipertrofia|\bmasa\b|volumen|m[uú]sculo|mamado", msg):
            actualizar_perfil(numero_usuario, meta="hipertrofia")
            guardados.append("meta hipertrofia")
        elif re.search(r"powerlifting|\bfuerza\b", msg):
            actualizar_perfil(numero_usuario, meta="powerlifting")
            guardados.append("meta powerlifting (fuerza)")

    if guardados:
        log(numero_usuario, "ONB-EXTRAS", f"campos anclados capturados: {guardados}")
    return guardados


async def _disparar_pipeline_generacion(numero_usuario: str) -> str:
    """Con el perfil completo, ejecuta nivel de fuerza + generar_rutina SIN pasar por el LLM."""
    p = cargar_perfil(numero_usuario)
    rms = cargar_rms(numero_usuario)
    log(numero_usuario, "PIPELINE", f"perfil={p} | rms={rms}")
    try:
        analisis = evaluar_nivel_fuerza({
            "peso_kg": p.get("peso_kg", 80),
            "rm_banca_kg": rms.get("press banco plano", 0),
            "rm_sentadilla_kg": rms.get("sentadilla", 0),
            "rm_peso_muerto_kg": rms.get("peso muerto", 0),
            "genero": p.get("genero", "hombre"),
        })
        nivel = analisis["nivel_sugerido"]
        log(numero_usuario, "PIPELINE", f"nivel calculado: {nivel}")
    except Exception as e:
        print(f"[PIPELINE] evaluar_nivel_fuerza falló: {e}")
        nivel = "intermedio"

    contexto = consultar_expediente_atleta(numero_usuario,
                                           "lesiones molestias equipo limitaciones")
    if "Sin datos" in contexto or "Error" in contexto:
        contexto = ""

    args = {
        "meta": p.get("meta", "powerbuilding"),
        "nivel": nivel,
        "dias": p.get("dias_semana", 4),
        "peso_kg": p.get("peso_kg"),
        "porcentaje_grasa": p.get("porcentaje_grasa"),
        "rm_banca_kg": rms.get("press banco plano"),
        "rm_sentadilla_kg": rms.get("sentadilla"),
        "rm_peso_muerto_kg": rms.get("peso muerto"),
        "rm_dominadas_kg": rms.get("dominadas"),
        "contexto_usuario": contexto,
    }
    return await _handle_generar_rutina(args, numero_usuario, "")

# ==============================================================================
# GENERADOR BACKGROUND
# ==============================================================================
async def _generar_rutina_background(datos: dict, numero_usuario: str):
    t0 = time.monotonic()
    log(numero_usuario, "GEN-BG", f"inicio con datos={datos}")
    try:
        await asyncio.to_thread(recibir_peticion, datos, numero_usuario)

        carpeta = Path(ruta_raiz_proyecto) / "resources" / "rutinas_generadas" / str(numero_usuario)
        archivos = list(carpeta.glob("*.json")) if carpeta.exists() else []
        log(numero_usuario, "GEN-BG", f"terminó en {time.monotonic()-t0:.1f}s, archivos={[a.name for a in archivos]}")
        if not archivos:
            raise RuntimeError("El generador no produjo archivos")

        _actualizar_estado_json(
            numero_usuario,
            semana=1, dia=1,
            fecha_programada=datetime.now().strftime("%Y-%m-%d"),
            ultima_fecha_completada="",
            estado_generacion="lista",
            onboarding_activo=False,
            campo_pendiente="",
        )

        try:
            from api.rutas_whatsapp import enviar_mensaje_whatsapp
            await enviar_mensaje_whatsapp(
                numero_usuario,
                "🔥 ¡Listo bro! Tu nuevo plan ya está armado y guardado. Escríbeme *ver mi rutina* y arrancamos."
            )
        except Exception as e:
            print(f"[GENERACIÓN BG] Plan listo pero no pude notificar: {e}")

    except Exception as e:
        print(f"[GENERACIÓN BG] ERROR: {e}")
        _actualizar_estado_json(numero_usuario, estado_generacion="error")

def _normalizar_dia_a_indice(dia_actual) -> int | None:
    if dia_actual is None: return None
    if isinstance(dia_actual, int): return max(0, dia_actual - 1)
    if isinstance(dia_actual, str):
        match = re.search(r"(\d+)", dia_actual)
        if match: return max(0, int(match.group(1)) - 1)
    try:
        return max(0, int(dia_actual) - 1)
    except (ValueError, TypeError):
        return 0

def evaluar_nivel_fuerza(args: dict) -> dict:
    peso = float(args.get("peso_kg", 80))
    banca = float(args.get("rm_banca_kg", 0))
    sentadilla = float(args.get("rm_sentadilla_kg", 0))
    peso_muerto = float(args.get("rm_peso_muerto_kg", 0))
    genero = args.get("genero", "hombre").lower()

    ratio_banca = banca / peso if peso > 0 else 0
    ratio_sq = sentadilla / peso if peso > 0 else 0
    ratio_dl = peso_muerto / peso if peso > 0 else 0

    if genero == "hombre":
        niveles = {
            "banca": "Avanzado" if ratio_banca >= 1.5 else "Intermedio" if ratio_banca >= 1.2 else "Principiante",
            "sentadilla": "Avanzado" if ratio_sq >= 2.0 else "Intermedio" if ratio_sq >= 1.5 else "Principiante",
            "peso_muerto": "Avanzado" if ratio_dl >= 2.5 else "Intermedio" if ratio_dl >= 1.8 else "Principiante"
        }
    else:
        niveles = {
            "banca": "Avanzado" if ratio_banca >= 1.0 else "Intermedio" if ratio_banca >= 0.8 else "Principiante",
            "sentadilla": "Avanzado" if ratio_sq >= 1.5 else "Intermedio" if ratio_sq >= 1.2 else "Principiante",
            "peso_muerto": "Avanzado" if ratio_dl >= 2.0 else "Intermedio" if ratio_dl >= 1.5 else "Principiante"
        }

    puntos = sum([1 if n == "Principiante" else 2 if n == "Intermedio" else 3 for n in niveles.values()])
    promedio = puntos / 3
    nivel_global = "avanzado" if promedio >= 2.5 else "intermedio" if promedio >= 1.7 else "principiante"

    reporte = (
        f"ANÁLISIS DE FUERZA RELATIVA:\n"
        f"- Press Banca: {ratio_banca:.2f}x BW ({niveles['banca']})\n"
        f"- Sentadilla: {ratio_sq:.2f}x BW ({niveles['sentadilla']})\n"
        f"- Peso Muerto: {ratio_dl:.2f}x BW ({niveles['peso_muerto']})\n"
        f"=> NIVEL GLOBAL SUGERIDO PARA LA RUTINA: {nivel_global.upper()}"
    )
    return {"nivel_sugerido": nivel_global, "reporte_texto": reporte}

# v4.1 — el modelo a veces deforma el nombre de la herramienta ("calcular_rm",
# "registrar serie", "Consultar_Rutina"). Antes eso caía al manejador de
# desconocidas y costaba un ciclo entero; ahora se corrige en silencio.
_ALIAS_TOOLS = {
    "calcular_rm": "calcular_rm_exacto",
    "calcular_1rm": "calcular_rm_exacto",
    "calcular_rm_maximo": "calcular_rm_exacto",
    "registrar_serie": "registrar_serie_completada",
    "guardar_serie": "registrar_serie_completada",
    "registrar_set": "registrar_serie_completada",
    "consultar_rutina": "consultar_rutina_futura",
    "ver_rutina": "consultar_rutina_futura",
    "mostrar_rutina": "consultar_rutina_futura",
    "consultar_plan": "consultar_rutina_futura",
    "consultar_expediente": "consultar_expediente_personal",
    "guardar_expediente": "guardar_en_expediente_personal",
    "guardar_en_expediente": "guardar_en_expediente_personal",
    "guardar_dato": "guardar_en_expediente_personal",
    "consultar_libro": "consultar_libro_cientifico",
    "consultar_ciencia": "consultar_libro_cientifico",
    "consultar_historial": "consultar_historial_narrativo",
    "guardar_dato_personal": "guardar_en_expediente_personal",
    "calcular_rm_exact": "calcular_rm_exacto",
    "biblioteca_cientifica": "consultar_libro_cientifico",
    "evaluar_perfil": "evaluar_perfil_fisico",
    "evaluar_fuerza": "evaluar_nivel_fuerza",
    "avanzar": "avanzar_dia",
    "siguiente_dia": "avanzar_dia",
    "actualizar_rm": "actualizar_1rm",
    "generar_plan": "generar_rutina",
    "crear_rutina": "generar_rutina",
}

def _normalizar_nombre_tool(nombre: str, usuario: str = "") -> str:
    limpio = re.sub(r"[\s\-]+", "_", str(nombre).strip().lower())
    if limpio in _ALIAS_TOOLS:
        if usuario:
            log(usuario, "TOOL-ALIAS", f"'{nombre}' -> '{_ALIAS_TOOLS[limpio]}'")
        return _ALIAS_TOOLS[limpio]
    return limpio

def _num_seguro(v, defecto=None):
    """Convierte '150', '150kg', ' 3 reps ' o 150 a número; None si no hay dígitos."""
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(v or ""))
    return float(m.group(1).replace(",", ".")) if m else defecto

def _extraer_tool(tool) -> tuple[str, dict]:
    try:
        if hasattr(tool, "function"):
            nombre = tool.function.name
            args = tool.function.arguments or {}
        else:
            nombre = tool["function"]["name"]
            args = tool["function"].get("arguments", {})

        if not isinstance(args, dict):
            try:
                args = json.loads(args) if isinstance(args, str) else {}
            except json.JSONDecodeError:
                args = {}
        return nombre, args
    except Exception:
        return "", {}

# ==============================================================================
# COMPRESOR ASÍNCRONO DE HISTORIAL
# ==============================================================================
async def comprimir_historial_si_necesario(historial: list) -> list:
    system = historial[0]
    resto  = historial[1:]
    resumen_previo = ""
    base_historial = resto

    if resto and resto[0].get("content", "").startswith(RESUMEN_MARKER):
        resumen_previo = resto[0]["content"]
        base_historial = resto[2:] if len(resto) > 2 else []

    if len(base_historial) <= VENTANA_RECIENTE:
        return historial

    candidatos = base_historial[:-VENTANA_RECIENTE]
    recientes  = base_historial[-VENTANA_RECIENTE:]
    if not candidatos:
        return historial

    lineas = []
    for m in candidatos:
        rol   = "Atleta" if m.get("role") == "user" else "Coach"
        texto = m.get("content", "")[:300]
        lineas.append(f"{rol}: {texto}")

    prefijo_previo = (
        f"Resumen previo de sesiones:\n{resumen_previo}\n\nNueva conversación a incorporar:\n"
        if resumen_previo else "Conversación:\n"
    )

    prompt_compresion = (
            "ERES EL MOTOR DE COMPRESIÓN DE MEMORIA DE 'IRON PROGRESS'. "
            "Tu única función es reducir el historial a un bloque compacto de hechos duros. "
            "=== REGLAS GLOBALES (ANTI-AMNESIA) ===\n"
            "1. ESCUDO BIOMÉTRICO (CRÍTICO): Tienes ESTRICTAMENTE PROHIBIDO omitir números físicos. Extrae EXACTAMENTE EDAD, PESO (kg/lb), ALTURA (m/cm), % GRASA o LESIONES si se mencionan.\n"
            "2. ESCUDO DE RENDIMIENTO (CRÍTICO): Copia literalmente marcas, pesos levantados, repeticiones, RPE y nombres de ejercicios. No uses frases genéricas.\n"
            "3. ESCUDO DE ENTORNO: Conserva el objetivo principal, días disponibles y equipo.\n"
            "4. ESTADO DE LA CHARLA: Identifica con precisión la última intención del usuario (ej. 'el usuario insiste en que ya dio su altura').\n\n"
            "=== FORMATO DE SALIDA OBLIGATORIO ===\n"
            "Devuelve EXCLUSIVAMENTE este formato (escribe 'No especificado' si falta el dato):\n"
            "[BIOMETRÍA Y PERFIL]: Edad: [X], Peso: [X], Altura: [X], Meta: [X], Equipo: [X], Días: [X], Lesiones: [X].\n"
            "[MARCAS Y RENDIMIENTO]: [Lista exacta de ejercicios, kg y reps].\n"
            "[ESTADO DE LA CHARLA]: [Breve resumen de la última interacción].\n\n"
            + prefijo_previo + "\n".join(lineas) + "\n\nResumen compacto actualizado (formato obligatorio):"
    )

    try:
        resp = await chat_llm(
            [{"role": "user", "content": prompt_compresion}],
            opciones={**OPCIONES_QWEN_32B, "num_predict": 220, "temperature": 0.1},
            quien="compresor"
        )
        texto_resumen = resp.get("message", {}).get("content", "").strip()
        texto_resumen = re.sub(r"<think>.*?</think>\s*", "", texto_resumen, flags=re.DOTALL).strip()

        if not texto_resumen:
            raise ValueError("Resumen vacío")

        print(f"[COMPRESOR] ✅ {len(candidatos)} msgs → {len(texto_resumen)} chars de resumen")
        return [
            system,
            {"role": "user", "content": f"{RESUMEN_MARKER}\n{texto_resumen}"},
            {"role": "assistant", "content": "Contexto de sesiones anteriores cargado, sigo desde ahí."}
        ] + recientes
    except Exception as e:
        print(f"[COMPRESOR] ❌ Falló ({type(e).__name__}: {e}) — historial sin cambios")
        return historial

# ==============================================================================
# RAG Y CONTEXTO
# ==============================================================================
UMBRAL_RAG = 1.2

def consultar_literatura_cientifica(pregunta: str) -> str:
    if not db_cientifica: return "Error: Base de datos científica no disponible."
    try:
        # v4: k=3 con deduplicación — un solo fragmento de 400 chars era muy poco
        # para sustentar una recomendación; ahora se citan hasta 3 pasajes y el libro.
        resultados = db_cientifica.similarity_search_with_score(pregunta, k=3)
        vistos, textos = set(), []
        for doc, score in resultados:
            if score >= UMBRAL_RAG:
                continue
            frag = doc.page_content[:600].strip()
            clave = frag[:80]
            if clave in vistos:
                continue
            vistos.add(clave)
            meta = doc.metadata or {}
            fuente = meta.get("source") or meta.get("fuente") or ""
            sufijo = f"\n  [Fuente: {os.path.basename(str(fuente))}]" if fuente else ""
            textos.append(f"- {frag}{sufijo}")
        return "\n\n".join(textos) if textos else "No se encontró evidencia científica relevante."
    except Exception as e:
        return f"Error al leer la base de datos: {e}"

def consultar_memoria_historica_atleta(numero_usuario: str, pregunta: str) -> str:
    if not db_memoria_atleta: return "Error: Base de datos histórica no disponible."
    try:
        resultados = db_memoria_atleta.similarity_search_with_score(
            pregunta, k=3, filter={"numero_usuario": str(numero_usuario)})
        vistos, textos = set(), []
        for doc, score in resultados:
            if score >= UMBRAL_RAG or doc.page_content in vistos:
                continue
            vistos.add(doc.page_content)
            textos.append(f"- {doc.page_content}")
        return "\n\n".join(textos) if textos else "No hay registros narrativos previos."
    except Exception as e:
        return f"Error al leer el historial: {e}"

def consultar_expediente_atleta(numero_usuario: str, concepto: str) -> str:
    if not db_perfil_atleta: return "Error: base de datos no disponible."
    try:
        resultados = db_perfil_atleta.similarity_search_with_score(
            concepto, k=3, filter={"numero_usuario": {"$eq": str(numero_usuario)}})
        vistos, textos = set(), []
        for doc, score in resultados:
            if score >= UMBRAL_RAG or doc.page_content in vistos:
                continue
            vistos.add(doc.page_content)
            textos.append(f"- {doc.page_content}")
        return "\n".join(textos) if textos else "Sin datos en expediente."
    except Exception as e:
        return f"Error al leer expediente: {e}"

def guardar_dato_en_expediente(numero_usuario: str, texto_dato: str):
    if not db_perfil_atleta or not texto_dato.strip(): return
    try:
        # v4: anti-duplicados — la nota de la rodilla se guardaba una y otra vez
        # y luego el RAG la recuperaba en cada turno (el "loro"). Si ya existe un
        # dato casi idéntico, no se vuelve a insertar.
        previos = db_perfil_atleta.similarity_search_with_score(
            texto_dato, k=1, filter={"numero_usuario": {"$eq": str(numero_usuario)}})
        if previos and previos[0][1] < 0.25:
            log(numero_usuario, "EXPEDIENTE",
                f"dato casi idéntico ya existía; no se duplica: {_trunc(texto_dato, 90)}")
            return
    except Exception:
        pass
    try:
        db_perfil_atleta.add_texts(
            texts=[texto_dato],
            metadatas=[{"numero_usuario": str(numero_usuario), "fecha": datetime.now().strftime("%Y-%m-%d")}]
        )
        log(numero_usuario, "EXPEDIENTE", f"guardado: {_trunc(texto_dato, 90)}")
    except Exception as e:
        print(f"[ERROR RAG PERFIL]: {e}")

async def construir_contexto_enriquecido(numero_usuario: str, mensaje: str) -> str:
    loop = asyncio.get_event_loop()
    expediente, historial, ciencia = await asyncio.gather(
        loop.run_in_executor(None, consultar_expediente_atleta, numero_usuario, mensaje),
        loop.run_in_executor(None, consultar_memoria_historica_atleta, numero_usuario, mensaje),
        loop.run_in_executor(None, consultar_literatura_cientifica, mensaje)
    )
    bloques = []
    if "Sin datos" not in expediente and "Error" not in expediente:
        bloques.append(f"[EXPEDIENTE DEL ATLETA]:\n{expediente}")
    if "No hay registros" not in historial and "Error" not in historial:
        bloques.append(f"[HISTORIAL NARRATIVO]:\n{historial}")
    if "No se encontró" not in ciencia and "Error" not in ciencia:
        bloques.append(f"[EVIDENCIA CIENTÍFICA]:\n{ciencia}")
    if not bloques:
        return ""
    # v2: nota anti-loro — el bot repetía la nota de la rodilla en CADA mensaje
    return ("\n\n--- CONTEXTO ENRIQUECIDO (información de FONDO: úsala solo si viene al caso; "
            "NO la repitas al atleta en cada mensaje, y JAMÁS repitas algo que ya le dijiste "
            "en tu mensaje anterior) ---\n"
            + "\n\n".join(bloques) + "\n--- FIN DEL CONTEXTO ---")

# ==============================================================================
# INTERCEPTOR DE SERIES
# ==============================================================================
def detectar_serie_en_mensaje(mensaje: str) -> dict | None:
    patrones = [
        r'(\d+(?:[.,]\d+)?)\s*(?:kg)?\s*[xX×]\s*(\d+)',
        r'(\d+(?:[.,]\d+)?)\s*(?:kg)?\s*por\s*(\d+)',
        r'hice\s+(\d+)\s+(?:reps?|repeticiones?)\s+con\s+(\d+(?:[.,]\d+)?)',
        r'(\d+)\s+(?:reps?|repeticiones?)\s+(?:con|a)\s+(\d+(?:[.,]\d+)?)\s*kg',
        r'(\d+(?:[.,]\d+)?)\s*kg\s+(?:por|x|×)\s*(\d+)\s+(?:reps?|repeticiones?)',
    ]
    for patron in patrones:
        match = re.search(patron, mensaje, re.IGNORECASE)
        if match:
            a = float(match.group(1).replace(",", "."))
            b = float(match.group(2).replace(",", "."))
            peso, reps = (a, b) if a > b else (b, a)
            reps = int(reps)
            if 1 <= reps <= 30 and peso >= 1:
                return {"peso": peso, "reps": reps}
    return None

# ==============================================================================
# UTILIDADES
# ==============================================================================
_system_prompt_cache = None
def cargar_system_prompt() -> str:
    global _system_prompt_cache
    if _system_prompt_cache: return _system_prompt_cache
    try:
        with open(ruta_prompt_maestro, "r", encoding="utf-8") as f:
            _system_prompt_cache = str(json.load(f)["promt"])
            return _system_prompt_cache
    except FileNotFoundError:
        return "Eres un entrenador de élite."

_herramientas_cache = None
def cargar_herramientas() -> list:
    global _herramientas_cache
    if _herramientas_cache: return _herramientas_cache
    try:
        with open(ruta_tools, "r", encoding="utf-8") as f:
            crudas = json.load(f)
        normalizadas = []
        for t in crudas:
            if isinstance(t, dict) and t.get("type") == "function" and isinstance(t.get("function"), dict):
                normalizadas.append(t)
            elif isinstance(t, dict) and "name" in t:
                # v4.1 FIX CRÍTICO: una entrada sin el envoltorio {"type":"function",
                # "function":{...}} (así estaba avanzar_dia) corrompe/degrada el
                # registro de herramientas en Ollama. Se normaliza al vuelo.
                normalizadas.append({"type": "function", "function": t})
                print(f"[TOOLS] ⚠ Entrada sin envoltorio normalizada: '{t.get('name')}' "
                      f"(corrige tools.json para que todas usen type/function)")
        _herramientas_cache = normalizadas
        print(f"[TOOLS] {len(normalizadas)} herramientas registradas: "
              f"{[t['function'].get('name') for t in normalizadas]}")
        return _herramientas_cache
    except Exception as e:
        print(f"[ERROR tools.json]: {e}")
        return []


def historial_usuario(numero_usuario: str) -> str:
    return os.path.join(carpeta_data, f"historial_{numero_usuario}.json")

def limpiar_texto_para_whatsapp(texto: str) -> str:
    texto = re.sub(r"<think>.*?</think>\s*", "", texto, flags=re.DOTALL | re.IGNORECASE)
    texto = re.sub(r"\[INFO DE SISTEMA[^\n\]]*\]\s*", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"<tool_call>.*?</tool_call>\s*", "", texto, flags=re.DOTALL | re.IGNORECASE)
    texto = re.sub(r'```(?:json)?\s*\{[^`]*"(?:name|arguments)"[^`]*\}[^`]*```', '', texto, flags=re.DOTALL | re.IGNORECASE)
    texto = re.sub(r'\{\s*"name"\s*:.*?"arguments"\s*:\s*\{.*?\}\s*\}', '', texto, flags=re.DOTALL)
    texto = texto.replace("**", "*").replace("#", "")
    texto = re.sub(r'\n{3,}', '\n\n', texto)
    for tool in ["evaluar_perfil_fisico", "generar_rutina", "registrar_serie_completada",
                 "calcular_rm_exacto", "consultar_libro_cientifico", "avanzar_dia",
                 "consultar_expediente_personal", "consultar_historial_narrativo",
                 "guardar_en_expediente_personal"]:
        texto = re.sub(rf"[`'\"]?{tool}[`'\"]?", "mis métodos de coach", texto, flags=re.IGNORECASE)
    return texto.strip()

def detectar_consulta_rutina(mensaje: str) -> dict | None:
    msg = mensaje.lower()
    match_semana = re.search(r'(?:ver|mostrar|pasar a|semana)\s+(\d+)', msg)
    if match_semana:
        return {"tipo": "semana_futura", "semana_objetivo": int(match_semana.group(1))}
    patrones_semana_siguiente = [
        r'semana\s+(?:siguiente|próxima|prox|que\s+viene|futura)',
        r'(?:siguiente|próxima|prox)\s+semana',
        r'pasemos?\s+(?:a\s+)?(?:la\s+)?(?:siguiente|próxima|prox)?\s*semana',
        r'(?:avanzar?|ir|pasar)\s+(?:a\s+)?(?:la\s+)?(?:siguiente|próxima)\s+semana',
        r'(?:dame|muéstrame|ver|dime)\s+(?:la\s+)?(?:siguiente|próxima)\s+semana',
        r'(?:semana|plan|rutina)\s+\d+',
    ]
    patrones_ver_rutina = [
        r'(?:dame|muéstrame|dime|ver|enseña|muestra)\s+(?:mi\s+)?(?:rutina|plan|entrenamiento)',
        r'(?:qué|que)\s+(?:me\s+toca|hago|entreno)\b\s*(hoy|mañana)?\b',
        r'ya\s+(?:está|estuvo)\s+(?:list[ao]\s+)?(?:mi\s+)?(?:rutina|plan)',
        r'(?:mi\s+)?rutina\s+(?:completa|de\s+(?:hoy|mañana|esta\s+semana|los\s+\d+\s+días))',
        r'plan\s+(?:completo|de\s+(?:hoy|mañana|esta\s+semana|los\s+\d+\s+días))',
        r'(?:qué|que)\s+(?:ejercicios|entrenamiento)\s+(?:me\s+toca|tengo|hay)\b',
        r'\b(?:cual|cuál)\s+es\s+mi\s+(?:rutina|plan)\b'
    ]
    for patron in patrones_semana_siguiente:
        if re.search(patron, msg): return {"tipo": "semana_futura"}
    for patron in patrones_ver_rutina:
        if re.search(patron, msg): return {"tipo": "ver_rutina"}
    return None

def extraer_tool_call_escapado(texto: str):
    limpio = re.sub(r'```(?:json)?\s*', '', texto).replace('```', '').strip()
    try:
        data = json.loads(limpio)
        if "name" in data and "arguments" in data:
            return data["name"], data["arguments"]
    except json.JSONDecodeError:
        pass
    match = re.search(r'(\{.*\})', limpio, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if "name" in data and "arguments" in data:
                return data["name"], data["arguments"]
        except json.JSONDecodeError:
            pass
    return None, None

def construir_telemetria(fecha, hora, estatus, instruccion_de_hoy, dashboard_sql,
                         es_casual, estado_gen, semana_actual,
                         bloque_onboarding: str = "") -> str:
    """v2: el onboarding ya no manda una lista de instrucciones genéricas;
    manda EXACTAMENTE lo que el sistema acaba de hacer y la ÚNICA pregunta siguiente."""
    if bloque_onboarding:
        partes = [f"FECHA: {fecha} {hora}", bloque_onboarding]
        return TELEMETRIA_INICIO + "\n" + "\n\n".join(partes) + "\n" + TELEMETRIA_FIN

    if es_casual:
        return (f"{TELEMETRIA_INICIO}\nFECHA: {fecha} {hora} | CALENDARIO: {estatus}"
                f"\n{TELEMETRIA_FIN}")

    partes = [f"FECHA: {fecha} {hora}",
              f"CALENDARIO: {estatus} | SEMANA ACTUAL: {semana_actual}"]

    if estado_gen == "generando":
        partes.append("ESTADO: El plan se está generando AHORA MISMO. No muestres ni "
                      "inventes ejercicios; dile que le avisarás en unos minutos.")
    else:
        partes.append("RUTINA DEL DÍA (los *kg* ya vienen calculados y redondeados por el "
                      f"sistema — transcríbelos literales):\n{instruccion_de_hoy}")
        partes.append(f"ÚLTIMAS SERIES:\n{dashboard_sql}")

    return TELEMETRIA_INICIO + "\n" + "\n\n".join(partes) + "\n" + TELEMETRIA_FIN

def _resumen_perfil_humano(numero_usuario: str) -> str:
    p = cargar_perfil(numero_usuario)
    r = cargar_rms(numero_usuario)
    partes = []
    if p.get("peso_kg"): partes.append(f"{p['peso_kg']:g} kg")
    if p.get("altura_m"): partes.append(f"{p['altura_m']:.2f} m")
    if p.get("edad"): partes.append(f"{p['edad']} años")
    if p.get("genero"): partes.append(p["genero"])
    if p.get("porcentaje_grasa"): partes.append(f"{p['porcentaje_grasa']:g}% grasa")
    if p.get("dias_semana"): partes.append(f"{p['dias_semana']} días/semana")
    if p.get("meta"): partes.append(f"meta {p['meta']}")
    for nombre in ("press banco plano", "sentadilla", "peso muerto", "dominadas"):
        if nombre in r:
            partes.append(f"1RM {nombre}: {r[nombre]:g} kg")
    return ", ".join(partes) if partes else "nada todavía"

# ==============================================================================
# v3 — ANTÍDOTOS A LOS FALLOS DEL INFORME (todo calculado en CÓDIGO)
# ==============================================================================
def _semanas_disponibles(numero_usuario: str) -> list[int]:
    """Fallo 2 (alucinación de estado): en vez de dejar que el modelo invente
    reglas de bloqueo, el sistema sabe EXACTAMENTE qué semanas existen."""
    ruta_base = Path(__file__).resolve().parent.parent / "resources" / "rutinas_generadas" / str(numero_usuario).strip()
    semanas = set()
    for archivo in ["1_acumulamiento.json", "2_fuerza.json", "3_realizacion.json"]:
        ruta_f = ruta_base / archivo
        if not ruta_f.exists():
            continue
        try:
            with open(ruta_f, "r", encoding="utf-8") as f:
                for s in json.load(f):
                    semanas.add(int(s.get("Semana_Abs", 0)))
        except Exception:
            pass
    semanas.discard(0)
    return sorted(semanas)

def _analizar_velocidades(texto: str) -> str:
    """Fallo 3 (ceguera aritmética): si el atleta reporta velocidades VBT
    (0.255, 0.270, 0.206...), la comparación numérica la hace Python y el
    modelo solo la narra. Devuelve el bloque a inyectar o ''."""
    vals = [float(v.replace(",", ".")) for v in VELOCIDAD_RE.findall(texto)]
    vals = [v for v in vals if 0.05 <= v <= 2.0]
    if len(vals) < 2:
        return ""
    i_max = vals.index(max(vals))
    i_min = vals.index(min(vals))
    perdida = (max(vals) - vals[-1]) / max(vals) * 100 if max(vals) > 0 else 0.0
    tendencia = []
    for i in range(1, len(vals)):
        signo = "subió" if vals[i] > vals[i-1] else ("bajó" if vals[i] < vals[i-1] else "igual")
        tendencia.append(f"rep {i}->{i+1}: {signo}")
    return (f"\n\n[ANÁLISIS NUMÉRICO DEL SISTEMA — tu comentario DEBE coincidir con ESTOS "
            f"hechos, no con la narrativa típica de 'la primera rep es la más fuerte']:\n"
            f"Velocidades reportadas en orden: {vals}\n"
            f"Rep MÁS RÁPIDA: #{i_max+1} ({vals[i_max]}) | Rep MÁS LENTA: #{i_min+1} ({vals[i_min]})\n"
            f"Tendencia: {'; '.join(tendencia)}\n"
            f"Pérdida de velocidad vs. la mejor rep: {perdida:.1f}%")

def _clasificar_caracter_reps(reps_txt) -> float | None:
    nums = [int(x) for x in re.findall(r"\d+", str(reps_txt))]
    return sum(nums) / len(nums) if nums else None

_KG_EN_TEXTO_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:kg|kilos?)\b", re.IGNORECASE)

def _extraer_kgs(texto: str) -> set[float]:
    return {float(v.replace(",", ".")) for v in _KG_EN_TEXTO_RE.findall(texto or "")}

def _verificar_kgs_salida(numero_usuario: str, respuesta: str, fuentes: str) -> list[float]:
    """v3 — Bug '79% -> 15kg': devuelve la lista de kilajes que el modelo escribió
    en su respuesta pero que NO existen en ninguna fuente legítima (rutina calculada,
    telemetría, mensaje del usuario, RMs guardados). Si hay inventados, se rechaza."""
    kgs_respuesta = _extraer_kgs(respuesta)
    if not kgs_respuesta:
        return []
    permitidos = _extraer_kgs(fuentes)
    permitidos |= set(cargar_rms(numero_usuario).values())
    # tolerancia: números redondos chicos de conversación (0, 2.5 de redondeo) y duplicados ±0.1
    inventados = []
    for kg in kgs_respuesta:
        if any(abs(kg - p) < 0.11 for p in permitidos):
            continue
        if kg in (0.0, 2.5, 5.0):     # menciones triviales ("sube de 2.5 en 2.5")
            continue
        inventados.append(kg)
    return sorted(inventados)

# ==============================================================================
# ORQUESTADOR PRINCIPAL (lock por usuario)
# ==============================================================================
locks_usuario: dict[str, asyncio.Lock] = {}

async def generar_respuesta_coach(numero_usuario: str, mensaje_usuario: str):
    lock = locks_usuario.setdefault(numero_usuario, asyncio.Lock())
    if lock.locked():
        log(numero_usuario, "LOCK", "mensaje rechazado: hay otro en proceso")
        return "Dame unos segundos bro, sigo con tu mensaje anterior 🙏"
    async with lock:
        return await _generar_respuesta_coach_interno(numero_usuario, mensaje_usuario)


async def _forzar_respuesta_texto(mensajes: list, instruccion: str) -> str:
    """Último recurso: obliga al modelo a responder en texto plano, SIN herramientas."""
    try:
        resp = await chat_llm(
            mensajes + [{"role": "user", "content": f"[SISTEMA]: {instruccion}"}],
            tools=None,   # sin tools a propósito: no puede escaparse a otra tool call
            quien="fallback-texto"
        )
        return (resp.get("message", {}) or {}).get("content", "") or ""
    except Exception as e:
        print(f"[FALLBACK TEXTO] {e}")
        return ""


async def _generar_respuesta_coach_interno(numero_usuario: str, mensaje_usuario: str):
    t_inicio = time.monotonic()
    log(numero_usuario, "IN", f"mensaje: {mensaje_usuario!r}")
    mis_herramientas = cargar_herramientas()
    ahora        = datetime.now()
    fecha_actual = ahora.strftime("%Y-%m-%d")
    hora_actual  = ahora.strftime("%H:%M:%S")
    ruta_historial = historial_usuario(numero_usuario)

    if os.path.exists(ruta_historial):
        try:
            async with aiofiles.open(ruta_historial, mode="r", encoding="utf-8") as f:
                contenido = await f.read()
                historial_memoria[numero_usuario] = json.loads(contenido)
        except (json.JSONDecodeError, Exception) as e:
            log(numero_usuario, "HIST-ERROR", f"historial corrupto, se reinicia: {e}")
            historial_memoria[numero_usuario] = []
    else:
        historial_memoria[numero_usuario] = []

    estado_hoy           = obtener_estado_usuario(numero_usuario)
    fecha_programada_str = estado_hoy.get("fecha_programada", fecha_actual)
    estado_gen           = estado_hoy.get("estado_generacion", "")

    # v3.1 — SELF-HEAL: si el proceso murió a media generación, 'generando' quedaba
    # pegado para siempre y bloqueaba crear/rehacer planes eternamente.
    if estado_gen == "generando":
        ts = float(estado_hoy.get("generacion_ts") or 0)
        if not ts or (time.time() - ts) > 900:   # 15 minutos
            log(numero_usuario, "SELF-HEAL", "estado 'generando' colgado >15min; se libera como 'error'")
            _actualizar_estado_json(numero_usuario, estado_generacion="error")
            estado_gen = "error"
            estado_hoy["estado_generacion"] = "error"

    instruccion_de_hoy   = obtener_rutina_del_json(numero_usuario, estado_hoy["semana"], estado_hoy["dia"], solo_hoy=True)
    dashboard_sql        = obtener_dashboard_analitico(numero_usuario)
    rutina_vacia         = "ESTADO DE RUTINA: Vacío" in instruccion_de_hoy

    log(numero_usuario, "ESTADO",
        f"semana={estado_hoy['semana']} dia={estado_hoy['dia']} gen='{estado_gen}' "
        f"rutina_vacia={rutina_vacia} onboarding={bool(estado_hoy.get('onboarding_activo'))} "
        f"campo_pendiente='{estado_hoy.get('campo_pendiente', '')}' "
        f"historial={len(historial_memoria[numero_usuario])} msgs")

    fecha_prog = datetime.strptime(fecha_programada_str, "%Y-%m-%d")
    if ahora.date() < fecha_prog.date():
        estatus = f"DESCANSO: Próximo entreno el {fecha_programada_str}."
    elif ahora.date() == fecha_prog.date():
        estatus = f"AL DÍA: Hoy ({fecha_actual}) toca entrenamiento."
    else:
        estatus = f"ATRASADO: Era el {fecha_programada_str}, reponiendo hoy ({fecha_actual})."

    texto_puro = str(mensaje_usuario).lower().strip()

    # ==========================================================================
    # v2 — MÁQUINA DE ESTADOS DE ONBOARDING (Python manda, el LLM redacta)
    # ==========================================================================
    onboarding_activo = bool(estado_hoy.get("onboarding_activo"))
    campo_pendiente   = estado_hoy.get("campo_pendiente", "")
    bloque_onboarding = ""

    # Afirmación corta ("sí", "dale") justo después de que el coach ofreció crear plan
    ultimo_coach = next((m.get("content", "") for m in reversed(historial_memoria[numero_usuario])
                         if m.get("role") == "assistant"), "")
    oferta_previa = bool(re.search(r"\b(plan|rutina|mesociclo)\b", ultimo_coach.lower()))
    en_creacion = bool(EN_CREACION_RE.search(texto_puro)) or \
                  (rutina_vacia and oferta_previa and AFIRMACION_CORTA_RE.match(texto_puro))

    if onboarding_activo and CANCELAR_ONBOARDING_RE.search(texto_puro):
        _actualizar_estado_json(numero_usuario, onboarding_activo=False, campo_pendiente="")
        onboarding_activo = False
        log(numero_usuario, "ONB", "CANCELADO por el atleta (datos conservados)")
        bloque_onboarding = ("EL ATLETA CANCELÓ la creación del plan. Confírmaselo sin drama "
                             "(sus datos quedan guardados para cuando quiera retomar) y sigue "
                             "la conversación normal.")

    elif not onboarding_activo and en_creacion and estado_gen != "generando":
        # v3.1 — FIX: el mensaje que dispara puede TRAER datos
        # ("hazme una rutina, peso 82kg, mido 1.73, entreno 5 días..."). Antes se
        # ignoraban y el coach pedía el peso recién dado -> "me vuelve a pedir todo".
        extras_iniciales = _parsear_campos_anclados(
            numero_usuario, mensaje_usuario, calcular_faltantes(numero_usuario))
        faltantes = calcular_faltantes(numero_usuario)
        log(numero_usuario, "ONB", f"ACTIVADO por intención explícita. "
                                   f"Capturado del mensaje: {extras_iniciales or 'nada'}. Faltan: {faltantes}")
        nota_extras = (f"ACABAS DE GUARDAR (venían en su mensaje): {'; '.join(extras_iniciales)}.\n"
                       if extras_iniciales else "")
        if not faltantes:
            # Ya tenemos todo de una sesión anterior: disparar directo
            resultado_pipeline = await _disparar_pipeline_generacion(numero_usuario)
            _actualizar_estado_json(numero_usuario, onboarding_activo=False, campo_pendiente="")
            log(numero_usuario, "ONB", f"perfil completo previo -> pipeline: {resultado_pipeline!r}")
            if "[ERROR" in resultado_pipeline or "Faltan" in resultado_pipeline:
                bloque_onboarding = (f"El sistema intentó armar el plan pero: {resultado_pipeline}. "
                                     "Explícaselo con naturalidad.")
            else:
                bloque_onboarding = ("PLAN EN MARCHA: el sistema YA disparó la generación con sus "
                                     f"datos guardados ({_resumen_perfil_humano(numero_usuario)}). "
                                     "Dile que estará listo en 2-3 minutos y que TÚ le avisarás. "
                                     "NO describas ejercicios. NO le preguntes nada más.")
        else:
            campo_pendiente = faltantes[0]
            _actualizar_estado_json(numero_usuario, onboarding_activo=True,
                                    campo_pendiente=campo_pendiente)
            onboarding_activo = True
            conocido = _resumen_perfil_humano(numero_usuario)
            log(numero_usuario, "ONB", f"pidiendo primer campo: {campo_pendiente} | conocido: {conocido}")
            bloque_onboarding = (
                "MODO CREACIÓN DE PLAN — el sistema recolecta los datos; tú solo redactas.\n"
                + nota_extras +
                f"DATOS YA GUARDADOS (NO los vuelvas a preguntar): {conocido}.\n"
                f"PIDE AHORA, en UNA sola pregunta natural y variada: {_desc_campo(campo_pendiente)}. "
                "No enumeres la lista completa de datos, no repitas todo lo guardado en cada "
                "mensaje; como mucho confirma en 3-4 palabras el último dato.")

    elif onboarding_activo:
        # v3.1 — SELF-HEAL: si el flag venía de una versión anterior o el estado quedó
        # raro, campo_pendiente puede estar vacío o apuntar a un dato YA guardado.
        # Antes eso hacía que TODA respuesta "no parseara" y el coach re-preguntara
        # en bucle infinito. Ahora el campo se recalcula solo.
        faltantes_prev = calcular_faltantes(numero_usuario)
        if faltantes_prev and campo_pendiente not in faltantes_prev:
            log(numero_usuario, "ONB-SELFHEAL",
                f"campo_pendiente inválido ('{campo_pendiente}') -> '{faltantes_prev[0]}'")
            campo_pendiente = faltantes_prev[0]
            _actualizar_estado_json(numero_usuario, campo_pendiente=campo_pendiente)

        if not faltantes_prev:
            # Los datos ya estaban completos (p. ej. estado viejo): no re-preguntar nada.
            exito, feedback, rechazo = True, "todos los datos ya estaban completos", ""
        else:
            exito, feedback = procesar_respuesta_onboarding(numero_usuario, campo_pendiente, mensaje_usuario)
            rechazo = feedback if (not exito and feedback) else ""

            # v3.1 — FIX: capturar TAMBIÉN los demás datos que vengan en el mismo
            # mensaje ("82kg, mido 1.73 y tengo 24 años" ya no pierde altura y edad).
            extras = _parsear_campos_anclados(
                numero_usuario, mensaje_usuario,
                [c for c in calcular_faltantes(numero_usuario)
                 if c != campo_pendiente])
            if extras:
                partes_fb = ([feedback] if (exito and feedback) else []) + extras
                feedback = "; ".join(partes_fb)
                exito = True
                if rechazo:
                    rechazo += " ADEMÁS, esto sí se guardó bien: " + "; ".join(extras)

        faltantes = calcular_faltantes(numero_usuario)
        log(numero_usuario, "ONB-PARSE",
            f"campo='{campo_pendiente}' exito={exito} feedback={feedback!r} faltan={faltantes}")

        if not faltantes:
            resultado_pipeline = await _disparar_pipeline_generacion(numero_usuario)
            _actualizar_estado_json(numero_usuario, onboarding_activo=False, campo_pendiente="")
            onboarding_activo = False
            log(numero_usuario, "ONB", f"COMPLETO -> pipeline: {resultado_pipeline!r}")
            if "[ERROR" in resultado_pipeline or "Faltan" in resultado_pipeline:
                bloque_onboarding = (f"Último dato guardado ({feedback}), pero el sistema reportó: "
                                     f"{resultado_pipeline}. Explícaselo con naturalidad.")
            else:
                bloque_onboarding = (
                    f"GUARDADO: {feedback}. CON ESO ESTÁ COMPLETO: el sistema YA disparó la "
                    "generación del plan. Celebra brevemente, dile que estará listo en 2-3 "
                    "minutos y que TÚ le avisarás. NO describas ejercicios. NO preguntes nada más.")
        elif exito:
            campo_pendiente = faltantes[0]
            _actualizar_estado_json(numero_usuario, campo_pendiente=campo_pendiente)
            bloque_onboarding = (
                f"GUARDADO: {feedback}.\n"
                f"PIDE AHORA, en UNA sola pregunta natural: {_desc_campo(campo_pendiente)}. "
                "No repitas la lista de datos guardados; máximo confirma el último en 3-4 palabras.")
        elif rechazo:
            # p. ej. 78 reps: rechazado con instrucción concreta (+ lo que sí se guardó)
            bloque_onboarding = rechazo
        else:
            log(numero_usuario, "ONB", f"mensaje no responde a '{campo_pendiente}', se retoma")
            bloque_onboarding = (
                f"El mensaje del atleta NO parece responder a lo pendiente "
                f"({_desc_campo(campo_pendiente)}). Responde primero a lo que dijo con "
                "naturalidad (si pregunta qué eres: su entrenador personal de Iron Progress, "
                "un coach de fuerza que lo acompaña por WhatsApp) y luego retoma pidiendo "
                f"SOLO {_desc_campo(campo_pendiente)}.")

    # ==========================================================================
    # MODO NORMAL (rutina activa / charla) — señales e interceptores
    # ==========================================================================
    # v4.1: vocabulario ampliado — "ya terminé", "me duele el hombro" o "dormí mal"
    # eran clasificados como casuales y se quedaban SIN herramientas (avanzar_dia,
    # expediente...). Además, un mensaje largo nunca es casual aunque no matchee.
    PALABRAS_ENTRENAMIENTO = {
        "rutina", "plan", "serie", "ejercicio", "rm", "banca", "sentadilla",
        "muerto", "dominada", "rpe", "kg", "rep", "entreno", "entrena", "entrenar",
        "peso", "dolor", "duele", "molest", "lesion", "lesión", "semana", "dia", "día",
        "siguiente", "mañana", "hazme", "crear", "cambiar", "cambio", "sustitu",
        "mesociclo", "hoy", "toca", "gym", "gimnasio", "marca", "pr", "récord", "record",
        "termin", "acab", "descans", "dormí", "dormi", "sueño", "estres", "estrés",
        "fatiga", "cansad", "hombro", "rodilla", "espalda", "codo", "muñeca", "cadera",
        "cinturon", "cinturón", "straps", "magnesio", "barra", "mancuern", "polea",
        "maquina", "máquina", "video", "veloc", "prefiero", "odio", "calent", "deficit", "déficit"
    }
    es_casual = (not any(p in texto_puro for p in PALABRAS_ENTRENAMIENTO)
                 and len(texto_puro) <= 60)

    consulta_rutina = None
    serie = None
    if not onboarding_activo and not bloque_onboarding:
        consulta_rutina = detectar_consulta_rutina(mensaje_usuario) if not rutina_vacia else None
        serie = detectar_serie_en_mensaje(mensaje_usuario)

    log(numero_usuario, "MODO",
        f"casual={es_casual} onboarding={onboarding_activo or bool(bloque_onboarding)} "
        f"consulta={consulta_rutina} serie={serie}")

    contexto_dinamico = construir_telemetria(
        fecha_actual, hora_actual, estatus, instruccion_de_hoy, dashboard_sql,
        es_casual, estado_gen, estado_hoy["semana"], bloque_onboarding
    )

    system_prompt_estatico = cargar_system_prompt()
    if not historial_memoria[numero_usuario] or \
            historial_memoria[numero_usuario][0].get("role") != "system":
        historial_memoria[numero_usuario].insert(0, {"role": "system", "content": system_prompt_estatico})

    msgs_sin_system = len(historial_memoria[numero_usuario]) - 1
    if msgs_sin_system >= UMBRAL_COMPRESION:
        historial_memoria[numero_usuario] = await comprimir_historial_si_necesario(historial_memoria[numero_usuario])

    # v2: RAG solo en modo normal no-casual (era la causa del loro de la rodilla)
    contexto_extra = ""
    if not onboarding_activo and not bloque_onboarding and not es_casual:
        contexto_extra = await construir_contexto_enriquecido(numero_usuario, mensaje_usuario)

    contenido_mensaje = str(mensaje_usuario).strip()

    # v3 — Fallo 2 (alucinación de estado): la semana pedida se consulta EN PYTHON
    # y se inyecta ya resuelta. El modelo no puede inventar reglas de bloqueo ni
    # depende de acertar una tool call.
    if consulta_rutina:
        if consulta_rutina.get("tipo") == "semana_futura":
            semana_obj = consulta_rutina.get("semana_objetivo", estado_hoy["semana"] + 1)
        else:
            semana_obj = estado_hoy["semana"]
        rutina_pedida = obtener_rutina_del_json(numero_usuario, semana_obj, None, solo_hoy=False)
        if "ESTADO DE RUTINA: Vacío" in rutina_pedida:
            disponibles = _semanas_disponibles(numero_usuario)
            rutina_pedida = (
                f"LA SEMANA {semana_obj} NO EXISTE en el plan generado. Semanas que SÍ existen: "
                f"{disponibles if disponibles else 'ninguna'}. Díselo tal cual y ofrécele ver una "
                "semana disponible. PROHIBIDO inventar condiciones tipo 'primero debes completar "
                "la semana actual': el sistema NO impone esa regla.")
            log(numero_usuario, "CONSULTA-SEM", f"semana {semana_obj} inexistente; disponibles={disponibles}")
        else:
            log(numero_usuario, "CONSULTA-SEM", f"semana {semana_obj} inyectada ({len(rutina_pedida)} chars)")
        contenido_mensaje += ("\n\n[SISTEMA — RUTINA SOLICITADA, ya consultada por el sistema. "
                              "Transcríbela con los kg literales; NO llames herramientas para esto]:\n"
                              + rutina_pedida)

    if serie and not rutina_vacia:
        contenido_mensaje += f"\n\n[SEÑAL CRÍTICA]: Serie detectada. Peso: {serie['peso']}kg | Reps: {serie['reps']}. OBLIGATORIO: Llama 'registrar_serie_completada' y 'calcular_rm_exacto'."

    # v4.1 — pista de fin de sesión (no obligatoria: el modelo decide con su criterio)
    if (not onboarding_activo and not bloque_onboarding and not rutina_vacia
            and not serie and FIN_SESION_RE.search(texto_puro)):
        contenido_mensaje += ("\n\n[SEÑAL]: El atleta parece haber TERMINADO su sesión de hoy. "
                              "Si es así, felicítalo y llama 'avanzar_dia' para mover su calendario.")
        log(numero_usuario, "FIN-SESION", "pista de avanzar_dia inyectada")

    # v3 — Fallo 3 (ceguera aritmética): velocidades VBT analizadas por Python
    if not onboarding_activo and not bloque_onboarding:
        bloque_vel = _analizar_velocidades(texto_puro)
        if bloque_vel:
            contenido_mensaje += bloque_vel
            log(numero_usuario, "VBT", "velocidades detectadas y analizadas en código")

    # v3 — Fallo 4 (restricción negativa ignorada): se detecta, se persiste y se
    # inyecta con prioridad máxima para que la inercia del formato no la ahogue.
    if not onboarding_activo and not bloque_onboarding and RESTRICCION_RE.search(texto_puro):
        guardar_dato_en_expediente(
            numero_usuario,
            f"({fecha_actual}) El atleta reportó una limitación/fallo: \"{str(mensaje_usuario).strip()[:200]}\"")
        contenido_mensaje += (
            "\n\n[RESTRICCIÓN DEL ATLETA — PRIORIDAD MÁXIMA, POR ENCIMA DE CUALQUIER RUTINA "
            "MOSTRADA]: acaba de decir que NO puede con algo (peso, reps o ejercicio). Ya quedó "
            "anotado en su expediente. En tu respuesta DEBES: 1) reconocer la limitación "
            "explícitamente, 2) NO volver a prescribir esa carga tal cual, 3) proponer un ajuste "
            "concreto (menos peso, menos reps o sustituir el ejercicio). PROHIBIDO ignorarlo o "
            "repetir la prescripción original.")
        log(numero_usuario, "RESTRICCION", "detectada y anotada en expediente")

    if contexto_extra:
        contenido_mensaje += f"\n\n{contexto_extra}"

    contenido_mensaje += contexto_dinamico
    historial_memoria[numero_usuario].append({"role": "user", "content": contenido_mensaje})

    # v3 — herramientas solo cuando pueden hacer falta:
    #  - onboarding: nunca (el sistema recolecta)
    #  - charla casual: nunca (más fluidez, cero tool calls espurias, menos latencia)
    #  - resto: sí
    tools_de_turno = None if (onboarding_activo or bloque_onboarding or es_casual) else mis_herramientas
    log(numero_usuario, "TOOLS", "desactivadas este turno" if not tools_de_turno
        else f"{len(mis_herramientas)} herramientas disponibles")

    # v3 — num_predict dinámico: una charla normal no necesita 2048 tokens
    quiere_semana_completa = bool(consulta_rutina)
    opciones_turno = dict(OPCIONES_QWEN_32B)
    if not quiere_semana_completa:
        opciones_turno["num_predict"] = 768

    try:
        respuesta_ia = await chat_llm(
            historial_memoria[numero_usuario],
            tools=tools_de_turno,
            opciones=opciones_turno,
            quien="principal"
        )

        mensaje_salida = respuesta_ia.get("message", {})
        texto_final    = mensaje_salida.get("content", "") or ""

        max_loops = 4
        loop_count = 0
        mensajes_orquestador = list(historial_memoria[numero_usuario])

        # v4.1 — RED DE HERRAMIENTAS: si el interceptor detectó una serie con
        # [SEÑAL CRÍTICA] y el modelo respondió SIN tool_calls, se le recuerda UNA
        # vez. Evita que una serie reportada se pierda sin registrar.
        if (tools_de_turno and serie and not (mensaje_salida.get("tool_calls") or [])
                and "[SEÑAL CRÍTICA]" in contenido_mensaje):
            log(numero_usuario, "TOOL-NUDGE", "serie con señal ignorada; recordatorio único")
            mensajes_orquestador.append(mensaje_salida)
            mensajes_orquestador.append({"role": "user", "content":
                f"[SISTEMA]: Detecté una serie del atleta ({serie['peso']}kg x {serie['reps']}). "
                "Llama AHORA a registrar_serie_completada y calcular_rm_exacto con esos datos "
                "y después redacta tu respuesta."})
            resp_nudge = await chat_llm(mensajes_orquestador, tools=mis_herramientas,
                                        opciones=opciones_turno, quien="nudge-serie")
            mensaje_salida = resp_nudge.get("message", {})
            texto_final = mensaje_salida.get("content", "") or ""
            loop_count += 1

        while tools_de_turno and loop_count < max_loops:
            calls = mensaje_salida.get("tool_calls") or []
            if not calls:
                texto_final = mensaje_salida.get("content", "") or ""
                break

            texto_herramientas_agregado = ""
            necesita_reinvocacion = False
            log(numero_usuario, "TOOL-CALLS",
                f"loop {loop_count+1}/{max_loops}: {[_extraer_tool(t)[0] for t in calls]}")

            for tool in calls:
                nombre, args = _extraer_tool(tool)
                if not nombre: continue
                nombre = _normalizar_nombre_tool(nombre, numero_usuario)
                log(numero_usuario, "TOOL", f"{nombre}({_trunc(args, 160)})")

                # v4.1: una excepción dentro de UNA herramienta ya no aborta el
                # turno completo hacia el mensaje enlatado; se reporta y se sigue.
                try:
                    if nombre == "generar_rutina":
                        resultado_rutina = await _handle_generar_rutina(args, numero_usuario, "")
                        texto_herramientas_agregado += resultado_rutina
                        necesita_reinvocacion = True

                    elif nombre == "avanzar_dia":
                        if avanzar_estado_usuario(numero_usuario, forzar=bool(args.get("forzar"))):
                            nuevo_estado = obtener_estado_usuario(numero_usuario)
                            nueva_rutina = obtener_rutina_del_json(
                                numero_usuario, nuevo_estado["semana"], nuevo_estado["dia"], solo_hoy=True)
                            texto_herramientas_agregado += f"[SISTEMA: DÍA AVANZADO]\nSemana {nuevo_estado['semana']}, Día: {nuevo_estado['dia']}.\nNueva rutina:\n{nueva_rutina}\n"
                        else:
                            texto_herramientas_agregado += "[SISTEMA]: CANDADO ACTIVO — el calendario ya avanzó hoy. NO afirmes que avanzaste. Explícaselo con naturalidad y pregúntale si de verdad quiere forzar otro avance hoy (si insiste, vuelve a llamar avanzar_dia con forzar=true).\n"
                        necesita_reinvocacion = True

                    elif nombre == "evaluar_nivel_fuerza":
                        resultado = evaluar_nivel_fuerza(args)
                        actualizar_perfil(numero_usuario, genero=args.get("genero"), edad=args.get("edad"))
                        texto_herramientas_agregado += f"[LABORATORIO FUERZA]:\n{resultado['reporte_texto']}\n"
                        necesita_reinvocacion = True

                    elif nombre == "consultar_rutina_futura":
                        try:
                            semana_req = int(args.get("semana") or estado_hoy["semana"])
                            dia_req = args.get("dia")
                            resultado_consulta = obtener_rutina_del_json(
                                numero_usuario, semana_req, dia_req, solo_hoy=(dia_req is not None)
                            )
                            texto_herramientas_agregado += f"[SISTEMA - RUTINA]:\n{resultado_consulta}\n"
                            necesita_reinvocacion = True
                        except Exception as e:
                            texto_herramientas_agregado += f"[SISTEMA - ERROR]: {e}\n"
                            necesita_reinvocacion = True

                    elif nombre == "actualizar_1rm":
                        texto_herramientas_agregado += "[SISTEMA]: Esa vía quedó deshabilitada. Registra el PR con calcular_rm_exacto (ejercicio, peso y reps literales); eso ya lo guarda.\n"
                        necesita_reinvocacion = True

                    elif nombre == "calcular_rm_exacto":
                        try:
                            if serie:   # v4.1: backfill — si el modelo omitió args, usa el interceptor
                                args.setdefault("peso_levantado", serie["peso"])
                                args.setdefault("repeticiones", serie["reps"])
                            reps = int(_num_seguro(args.get("repeticiones"), 1) or 1)
                            peso = float(_num_seguro(args.get("peso_levantado"), 0.0) or 0.0)
                            ejercicio = args.get("nombre_ejercicio", "este ejercicio")
                            if not (1 <= reps <= 15):
                                texto_herramientas_agregado += f"[CALCULADORA 1RM — RECHAZADO]: {reps} repeticiones está fuera del rango confiable (1-15) para estimar un 1RM. NO se guardó nada. Pregúntale al atleta si la cifra es correcta antes de reintentar.\n"
                            else:
                                tipo_str = args.get("tipo_ejercicio") or "peso_libre"
                                resultado = calcular_1rm(TipoEjercicio(tipo_str), reps, peso)
                                guardar_rm(numero_usuario, ejercicio, resultado,
                                           peso_origen=peso, reps_origen=reps, fuente="pr_chat")
                                texto_herramientas_agregado += f"✅ [CÁLCULO EXITOSO]: 1RM de {ejercicio} = {resultado} kg. Quedó guardado en su ficha; no lo vuelvas a preguntar.\n"
                        except Exception as e:
                            texto_herramientas_agregado += f"[CALCULADORA 1RM — ERROR]: {repr(e)}.\n"
                        necesita_reinvocacion = True

                    elif nombre == "consultar_libro_cientifico":
                        resultado = consultar_literatura_cientifica(args.get("pregunta_teorica", ""))
                        texto_herramientas_agregado += f"[BIBLIOTECA CIENTÍFICA]:\n{resultado}\n"
                        necesita_reinvocacion = True

                    elif nombre == "evaluar_perfil_fisico":
                        resultado = evaluar_perfil_completo(args)
                        actualizar_perfil(numero_usuario, peso_kg=args.get("peso_kg"),
                                          altura_m=args.get("altura_m"), porcentaje_grasa=args.get("porcentaje_grasa"),
                                          genero=args.get("genero"))
                        texto_herramientas_agregado += f"[LABORATORIO FFMI]:\n{resultado['reporte_texto']}\n"
                        necesita_reinvocacion = True

                    elif nombre == "consultar_expediente_personal":
                        resultado = consultar_expediente_atleta(numero_usuario, args.get("concepto_a_buscar", ""))
                        texto_herramientas_agregado += f"[EXPEDIENTE]:\n{resultado}\n"
                        necesita_reinvocacion = True

                    elif nombre == "registrar_serie_completada":
                        if serie:   # v4.1: backfill desde el interceptor
                            args.setdefault("peso_kg", serie["peso"])
                            args.setdefault("repeticiones", serie["reps"])
                        guardar_serie_sql(numero_usuario, args)
                        texto_herramientas_agregado += "[SQL]: Serie guardada.\n"
                        necesita_reinvocacion = True

                    elif nombre == "guardar_en_expediente_personal":
                        dato = args.get("dato_a_guardar", "")
                        guardar_dato_en_expediente(numero_usuario, dato)
                        texto_herramientas_agregado += f"[EXPEDIENTE]: Dato guardado.\n"
                        necesita_reinvocacion = True

                    elif nombre == "consultar_historial_narrativo":
                        contexto = args.get("contexto_a_buscar", "")
                        resultado = consultar_memoria_historica_atleta(numero_usuario, contexto)
                        texto_herramientas_agregado += f"[HISTORIAL NARRATIVO]:\n{resultado}\n"
                        necesita_reinvocacion = True

                    else:
                        # v3 — caso no contemplado: el modelo inventó/deformó un nombre de
                        # herramienta. Antes esto caía en silencio -> respuesta vacía.
                        # Ahora se le devuelve el menú real para que se recupere solo.
                        log(numero_usuario, "TOOL-DESCONOCIDA", nombre)
                        texto_herramientas_agregado += (
                            f"[SISTEMA]: La herramienta '{nombre}' NO existe. Las válidas son: "
                            "generar_rutina, consultar_rutina_futura, avanzar_dia, "
                            "calcular_rm_exacto, registrar_serie_completada, evaluar_perfil_fisico, "
                            "evaluar_nivel_fuerza, consultar_libro_cientifico, "
                            "consultar_expediente_personal, guardar_en_expediente_personal, "
                            "consultar_historial_narrativo. Si ninguna aplica a lo que intentabas, "
                            "responde al atleta con texto normal usando lo que ya sabes.\n")
                        necesita_reinvocacion = True
                except Exception as e_tool:
                    log(numero_usuario, "TOOL-ERROR", f"{nombre}: {type(e_tool).__name__}: {e_tool}")
                    texto_herramientas_agregado += (
                        f"[SISTEMA - ERROR en {nombre}]: {e_tool}. Explícale al atleta que esa "
                        "acción falló; NO afirmes que se realizó.\n")
                    necesita_reinvocacion = True

            if necesita_reinvocacion and texto_herramientas_agregado:
                log(numero_usuario, "TOOL-RESULT", texto_herramientas_agregado)
                mensajes_orquestador.append(mensaje_salida)
                mensajes_orquestador.append(
                    {"role": "user", "content":
                        f"Resultados:\n\n{texto_herramientas_agregado}\n\n"
                        "[SISTEMA AL COACH]: Analiza los resultados y responde al atleta en "
                        "texto natural. Si un resultado dice RECHAZADO, ERROR o CANDADO, sé "
                        "honesto: NO afirmes que la acción se realizó."}
                )

                resp2 = await chat_llm(
                    mensajes_orquestador,
                    tools=mis_herramientas,
                    opciones=opciones_turno,
                    quien=f"post-tools-{loop_count+1}"
                )
                mensaje_salida = resp2.get("message", {})
                texto_final = mensaje_salida.get("content", "") or ""
                loop_count += 1
            else:
                break

        hay_tool_escapado = (texto_final and '"name"' in texto_final and '"arguments"' in texto_final)
        if hay_tool_escapado:
            nombre_escapado, args_escapados = extraer_tool_call_escapado(texto_final)
            log(numero_usuario, "TOOL-ESCAPADA", f"{nombre_escapado} en texto plano")
            if nombre_escapado == "generar_rutina":
                await _handle_generar_rutina(args_escapados, numero_usuario, "")
                texto_final = "¡Listo! Tu mesociclo está siendo calculado. En 2-3 minutos tendrás tu plan."
            elif nombre_escapado == "consultar_rutina_futura":
                semana_r = args_escapados.get("semana", estado_hoy["semana"])
                dia_r = args_escapados.get("dia")
                resultado_consulta = obtener_rutina_del_json(numero_usuario, semana_r, dia_r, solo_hoy=(dia_r is not None))
                mensajes_rescate = list(historial_memoria[numero_usuario])
                mensajes_rescate.append({"role": "user", "content": f"[SISTEMA]:\n{resultado_consulta}\nResponde natural."})
                resp_rescue = await chat_llm(mensajes_rescate, quien="rescate-consulta")
                texto_final = resp_rescue.get("message", {}).get("content", "") or texto_final
            else:
                texto_final = re.sub(r'```(?:json)?\s*\{.*?"arguments".*?\}\s*```', '', texto_final, flags=re.DOTALL).strip()

        # ======================================================================
        # v2 — RED DE SEGURIDAD 1: nunca responder vacío ("se me cruzaron los cables")
        # ======================================================================
        if not texto_final.strip():
            log(numero_usuario, "FALLBACK", "respuesta vacía tras el loop; forzando texto sin tools")
            base = mensajes_orquestador if loop_count > 0 else list(historial_memoria[numero_usuario])
            texto_final = await _forzar_respuesta_texto(
                base,
                "Responde AHORA al atleta en texto normal, en español, con la información "
                "que ya tienes en esta conversación. No llames herramientas.")
        if not texto_final.strip():
            log(numero_usuario, "FALLBACK", "el fallback también vino vacío; mensaje enlatado")
            texto_final = "Perdón bro, me trabé un segundo 😅 ¿en qué estábamos? Repíteme lo último."

        texto_final = limpiar_texto_para_whatsapp(texto_final)

        # ======================================================================
        # v3 — RED DE SEGURIDAD 2: kilajes inventados (bug "79% -> 15 kg").
        # Se comparan los kg de la respuesta contra las fuentes legítimas; si el
        # modelo escribió pesos que no existen en ningún lado, se le obliga a
        # corregir usando SOLO los números del sistema.
        # ======================================================================
        fuentes_numericas = (contenido_mensaje + " " + instruccion_de_hoy + " " +
                             str(dashboard_sql))
        kgs_inventados = _verificar_kgs_salida(numero_usuario, texto_final, fuentes_numericas)
        if kgs_inventados:
            log(numero_usuario, "KG-INVENTADOS", f"{kgs_inventados} no existen en las fuentes; reintento")
            correccion = await _forzar_respuesta_texto(
                list(historial_memoria[numero_usuario]) + [{"role": "assistant", "content": texto_final}],
                f"Tu borrador menciona estos pesos: {kgs_inventados} kg, que NO existen ni en la "
                "rutina calculada por el sistema ni en lo que dijo el atleta. Ese es un error grave. "
                "Reescribe tu respuesta usando EXCLUSIVAMENTE los kg que aparecen entre *asteriscos* "
                "en la telemetría o los que el atleta mencionó. Si un ejercicio dice 'peso por "
                "calibrar', dilo tal cual, sin inventarle un número.")
            if correccion.strip():
                correccion = limpiar_texto_para_whatsapp(correccion)
                if not _verificar_kgs_salida(numero_usuario, correccion, fuentes_numericas):
                    texto_final = correccion
                    log(numero_usuario, "KG-INVENTADOS", "corregido en el reintento")
                else:
                    log(numero_usuario, "KG-INVENTADOS", "el reintento también inventó; se conserva y se registra")

        # ======================================================================
        # v2 — RED DE SEGURIDAD 3: no repetir literalmente el mensaje anterior
        # ======================================================================
        if texto_final.strip() and texto_final.strip() == ultimo_coach.strip():
            log(numero_usuario, "DUPLICADO", "respuesta idéntica a la anterior; reformulando")
            reintento = await _forzar_respuesta_texto(
                list(historial_memoria[numero_usuario]),
                "Tu borrador repite EXACTAMENTE tu mensaje anterior. Reformúlalo por completo "
                "y haz avanzar la conversación tomando en cuenta el último mensaje del atleta.")
            if reintento.strip():
                texto_final = limpiar_texto_para_whatsapp(reintento)

        historial_memoria[numero_usuario].append({"role": "assistant", "content": texto_final})

        if len(historial_memoria[numero_usuario]) > LIMITE_EMERGENCIA:
            system_msg = historial_memoria[numero_usuario][0]
            resto      = historial_memoria[numero_usuario][1:]
            bloque_comprimido = []
            historial_core    = resto
            if resto and resto[0].get("content", "").startswith(RESUMEN_MARKER):
                bloque_comprimido = resto[:2]
                historial_core    = resto[2:]
            historial_memoria[numero_usuario] = [system_msg] + bloque_comprimido + historial_core[-14:]

        memoria_limpia = []
        for m in historial_memoria[numero_usuario]:
            rol = m.get("role")
            contenido = str(m.get("content") or "").strip()
            if rol == "assistant" and m.get("tool_calls") and not contenido: continue
            if not contenido or rol == "system" or "[SEÑAL INTERNA" in contenido: continue
            if rol == "assistant" and contenido in ["Procesando la telemetría...", "Procesando..."]: continue
            if rol == "user" and contenido.startswith("Resultados:"): continue

            if rol == "user":
                contenido = re.sub(r'\n\n--- TELEMETRÍA ---.*?--- FIN TELEMETRÍA ---', '', contenido, flags=re.DOTALL)
                contenido = re.sub(r'\n\n--- CONTEXTO ENRIQUECIDO.*?--- FIN DEL CONTEXTO ---', '', contenido, flags=re.DOTALL)
                contenido = re.sub(r'\n\n\[SEÑAL CRÍTICA\].*?(?=\n\n\[|$)', '', contenido, flags=re.DOTALL)
                contenido = re.sub(r'\n\n\[NOTA\].*?(?=\n\n\[|$)', '', contenido, flags=re.DOTALL)
                contenido = re.sub(r'\n\n\[SISTEMA — RUTINA SOLICITADA.*?(?=\n\n\[|$)', '', contenido, flags=re.DOTALL)
                contenido = re.sub(r'\n\n\[ANÁLISIS NUMÉRICO.*?(?=\n\n\[|$)', '', contenido, flags=re.DOTALL)
                contenido = re.sub(r'\n\n\[RESTRICCIÓN DEL ATLETA.*?(?=\n\n\[|$)', '', contenido, flags=re.DOTALL)
            contenido = contenido.strip()
            if contenido: memoria_limpia.append({"role": rol, "content": contenido})

        async with aiofiles.open(ruta_historial, mode="w", encoding="utf-8") as f:
            await f.write(json.dumps(memoria_limpia, indent=2, ensure_ascii=False))

        log(numero_usuario, "OUT",
            f"({time.monotonic()-t_inicio:.1f}s, {loop_count} loops de tools, "
            f"{len(texto_final)} chars) -> {texto_final!r}")
        return texto_final

    except Exception as e:
        import traceback
        log(numero_usuario, "ERROR-FATAL", f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return "Bro, tuve un error interno. ¿Me repites lo que necesitas?"
    finally:
        historial_memoria.pop(numero_usuario, None)

# ==============================================================================
# HELPER: GENERAR RUTINA
# ==============================================================================
async def _handle_generar_rutina(args: dict, numero_usuario: str, texto_base: str) -> str:
    if obtener_estado_usuario(numero_usuario).get("estado_generacion") == "generando":
        return ("[SISTEMA]: Ya hay un plan generándose para este atleta. "
                "Dile que espere el aviso; NO dispares otra generación.")

    meta_cruda = str(args.get("meta", "")).lower().strip()
    nivel = str(args.get("nivel", "intermedio")).lower().strip()
    ctx = args.get("contexto_usuario", "")
    dias = args.get("dias")
    peso_kg = args.get("peso_kg")
    grasa = args.get("porcentaje_grasa")

    rm_banca = args.get("rm_banca_kg") or args.get("rm_banca")
    rm_sq = args.get("rm_sentadilla_kg") or args.get("rm_sentadilla")
    rm_dl = args.get("rm_peso_muerto_kg") or args.get("rm_peso_muerto")
    rm_dom = args.get("rm_dominadas_kg", 0.0) or 0.0

    log(numero_usuario, "GENERAR", f"args recibidos: meta={meta_cruda} nivel={nivel} dias={dias} "
                                   f"peso={peso_kg} grasa={grasa} banca={rm_banca} sq={rm_sq} "
                                   f"dl={rm_dl} dom={rm_dom}")

    if not dias or not peso_kg or grasa is None:
        log(numero_usuario, "GENERAR-ERROR", "faltan días/peso/grasa")
        return "[ERROR DEL SISTEMA]: Faltan argumentos (días, peso, grasa). Pregúntale al atleta."

    if not rm_banca or not rm_sq or not rm_dl:
        log(numero_usuario, "GENERAR-ERROR", "faltan RMs básicos")
        return texto_base + "\n\nBro, para armar la rutina necesito que me confirmes tus marcas máximas (1RM) en Sentadilla, Banca y Peso Muerto."

    # v2 FIX: "powerbuilding" caía en el else y se generaba POWERLIFTING.
    if "powerbuild" in meta_cruda or any(p in meta_cruda for p in ["hipertrofia", "volumen", "masa"]):
        meta_backend = "powerbuilding"
    else:
        meta_backend = "powerlifting"
    log(numero_usuario, "GENERAR", f"meta '{meta_cruda}' -> backend '{meta_backend}'")

    for ej, val in (("press banco plano", rm_banca), ("sentadilla", rm_sq),
                    ("peso muerto", rm_dl), ("dominadas", rm_dom)):
        if val is not None:
            guardar_rm(numero_usuario, ej, val, fuente="generacion")

    _actualizar_estado_json(numero_usuario, estado_generacion="generando", meta=meta_backend,
                            dias=int(dias), generacion_ts=time.time())

    # Llaves alineadas con el contrato del generador (tools.json)
    datos = {
        "meta": meta_backend,
        "nivel": nivel,
        "dias": int(dias),
        "peso_kg": float(peso_kg),
        "porcentaje_grasa": float(grasa),
        "rm_banca_kg": float(rm_banca),
        "rm_sentadilla_kg": float(rm_sq),
        "rm_peso_muerto_kg": float(rm_dl),
        "rm_dominadas_kg": float(rm_dom),
        "tipo_periodizacion": "DUP",   # obligatorio para el prompt del generador
        "contexto_usuario": ctx
    }
    asyncio.create_task(_generar_rutina_background(datos, numero_usuario))
    return texto_base or "[SISTEMA]: Generación disparada en segundo plano."

# ==============================================================================
# RUTINA DEL JSON: CÁLCULOS AL VUELO
# ==============================================================================
def _redondear_2_5(x: float) -> float:
    return round(x / 2.5) * 2.5

def _resolver_rm(nombre_ej: str, rms: dict):
    n = _norm_ej(nombre_ej)
    if n in rms: return rms[n]
    if any(k in n for k in _ACCESORIO_KEYWORDS): return None
    for pat, clave in _HEREDA_RM:
        if pat.match(n): return rms.get(clave)
    return None

def obtener_rutina_del_json(numero_usuario: str, semana_actual, dia_actual=None, solo_hoy: bool = False) -> str:
    numero_usuario_limpio = str(numero_usuario).strip()
    if obtener_estado_usuario(numero_usuario_limpio).get("estado_generacion") == "generando":
        return "ESTADO DE RUTINA: GENERÁNDOSE AHORA MISMO.\nDile al atleta que su plan está en el horno."

    try:
        semana_int = int(str(semana_actual).strip())
    except Exception as e:
        return f"Error interno: {e}"

    ruta_base = Path(__file__).resolve().parent.parent / "resources" / "rutinas_generadas" / numero_usuario_limpio
    rms = cargar_rms(numero_usuario_limpio)
    sesiones = []

    for archivo in ["1_acumulamiento.json", "2_fuerza.json", "3_realizacion.json"]:
        ruta_f = ruta_base / archivo
        if not ruta_f.exists(): continue
        try:
            with open(ruta_f, "r", encoding="utf-8") as f:
                plan = json.load(f)
                sesiones = [s for s in plan if int(s.get("Semana_Abs", 0)) == semana_int]
                if sesiones: break
        except json.JSONDecodeError:
            return f"Error: {archivo} corrupto."

    if not sesiones:
        return "ESTADO DE RUTINA: Vacío.\nEl atleta NO tiene plan. Si pide una rutina, ofrécele crear una; si solo charla, conversa normal SIN pedirle datos."

    if solo_hoy and dia_actual is not None:
        dia_idx = _normalizar_dia_a_indice(dia_actual)
        if dia_idx is not None and 0 <= dia_idx < len(sesiones):
            sesiones_mostrar = [sesiones[dia_idx]]
            idx_base = dia_idx + 1
            encabezado = f"=== HOY: DÍA {idx_base} — SEMANA {semana_int} ===\n\n"
        else:
            sesiones_mostrar, idx_base = sesiones, 1
            encabezado = f"=== SEMANA {semana_int} ===\n\n"
    else:
        sesiones_mostrar, idx_base = sesiones, 1
        encabezado = f"=== PLANIFICACIÓN DETALLADA - SEMANA {semana_int} ===\n\n"

    texto = encabezado
    for offset, sesion in enumerate(sesiones_mostrar):
        idx = idx_base + offset
        enfoque_txt = str(sesion.get('Enfoque', 'N/A'))
        texto += f"--- DÍA {idx}: {enfoque_txt.upper()} | {sesion.get('Fase', 'N/A').upper()} ---\n"

        reps_compuestos = []
        for ej in sesion.get("Ejercicios", []):
            tipo = str(ej.get("tipo", "")).lower()
            nombre_ej = ej.get("Ejercicio", "?")
            m = PORCENTAJE_RE.search(str(ej.get("1RM", "")))
            es_analitico = any(t in tipo for t in ["monoarticular", "complementario", "analitico"])
            rm = None if es_analitico else _resolver_rm(nombre_ej, rms)

            if not es_analitico:
                prom_ej = _clasificar_caracter_reps(ej.get("Reps"))
                if prom_ej is not None:
                    reps_compuestos.append(prom_ej)

            if m and rm:
                pct = float(m.group(1))
                kg = _redondear_2_5(rm * pct / 100.0)
                carga = f"*{kg:g} kg* ({pct:g}% de su 1RM, ya calculado por el sistema)"
            elif m:
                # v3 — Bug "79% -> 15kg": mostrar el % sin kg invitaba al modelo a
                # "calcularlo" él mismo. Ahora se oculta el % y se le dan órdenes.
                carga = ("(*peso por calibrar* — NO calcules ni inventes kg para este "
                         "ejercicio; dile al atleta que reporte su primera serie y el "
                         "sistema fijará la carga)")
            else:
                carga = "(*peso por calibrar*)"

            texto += (f"- {nombre_ej} ({ej.get('Series')}x{ej.get('Reps')}) "
                      f"@ {carga} | RPE {ej.get('RPE', 'N/A')}\n")

        # v3 — Fallo 1 (disonancia cognitiva): si la etiqueta del día contradice
        # las series/reps reales, el sistema lo detecta y ordena al modelo
        # describir los NÚMEROS, no la definición de manual de la etiqueta.
        if reps_compuestos:
            prom = sum(reps_compuestos) / len(reps_compuestos)
            enfoque_low = enfoque_txt.lower()
            etiqueta_pesada = any(k in enfoque_low for k in ("potencia", "fuerza"))
            etiqueta_volumen = "hipertrofia" in enfoque_low or "volumen" in enfoque_low
            if (etiqueta_pesada and prom >= 8) or (etiqueta_volumen and prom <= 5):
                caracter = ("hipertrofia/volumen (reps altas)" if prom >= 8
                            else "fuerza/potencia (reps bajas)")
                texto += (f"[AUDITORÍA DEL SISTEMA]: la etiqueta dice '{enfoque_txt}' pero los "
                          f"compuestos promedian {prom:.0f} reps, es decir trabajo de {caracter}. "
                          "Al explicar el día, DESCRIBE lo que dicen las series/reps y porcentajes "
                          "de arriba; PROHIBIDO recitar la definición de la etiqueta si contradice "
                          "los números.\n")
                print(f"[AUDITORÍA RUTINA] {numero_usuario_limpio}: etiqueta '{enfoque_txt}' "
                      f"vs promedio {prom:.1f} reps — incoherencia del generador detectada")
        texto += "\n"
    return texto

# ==============================================================================
# ESTADO Y CALENDARIO
# ==============================================================================
def obtener_estado_usuario(numero_usuario: str) -> dict:
    hoy = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(ruta_estado, "r", encoding="utf-8") as f:
            return json.load(f).get(numero_usuario, {"semana": 1, "dia": 1, "fecha_programada": hoy})
    except FileNotFoundError:
        return {"semana": 1, "dia": 1, "fecha_programada": hoy}

def avanzar_estado_usuario(numero_usuario: str, forzar: bool = False) -> bool:
    hoy = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(ruta_estado, "r", encoding="utf-8") as f:
            estados = json.load(f)
    except FileNotFoundError:
        estados = {}

    estado = estados.get(numero_usuario, {"semana": 1, "dia": 1, "fecha_programada": hoy})

    if not forzar and estado.get("ultima_fecha_completada") == hoy:
        print(f"[CANDADO] {numero_usuario} ya avanzó hoy.")
        return False

    semana_actual = estado["semana"]
    dia_actual = int(estado["dia"]) if isinstance(estado["dia"], int) else 1

    ruta_base = Path(__file__).resolve().parent.parent / "resources" / "rutinas_generadas" / str(numero_usuario).strip()
    total_sesiones_semana = 3

    for archivo in ["1_acumulamiento.json", "2_fuerza.json", "3_realizacion.json"]:
        ruta_f = ruta_base / archivo
        if ruta_f.exists():
            try:
                with open(ruta_f, "r", encoding="utf-8") as f:
                    plan = json.load(f)
                    sesiones_encontradas = [s for s in plan if int(s.get("Semana_Abs", 0)) == semana_actual]
                    if sesiones_encontradas:
                        total_sesiones_semana = len(sesiones_encontradas)
                        break
            except Exception:
                pass

    if dia_actual >= total_sesiones_semana:
        sig_dia, sig_sem = 1, semana_actual + 1
        dias_suma = 1 if total_sesiones_semana >= 6 else 3
    else:
        sig_dia, sig_sem = dia_actual + 1, semana_actual
        dias_suma = 1 if total_sesiones_semana >= 6 else 2

    nueva_fecha = (datetime.now() + timedelta(days=dias_suma)).strftime("%Y-%m-%d")
    log(numero_usuario, "AVANCE", f"S{semana_actual}D{dia_actual} -> S{sig_sem}D{sig_dia}, próximo: {nueva_fecha}")

    # v2: update parcial en vez de reescribir el dict completo
    # (antes se perdían onboarding_activo, estado_generacion, meta, etc.)
    estado.update({
        "semana": sig_sem, "dia": sig_dia,
        "fecha_programada": nueva_fecha, "ultima_fecha_completada": hoy
    })
    estados[numero_usuario] = estado

    with open(ruta_estado, "w", encoding="utf-8") as f:
        json.dump(estados, f, indent=4, ensure_ascii=False)
    return True