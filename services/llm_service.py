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
    r"program\w*|prep[aá]r\w*|p[oó]nme|quiero|necesito|empezar|comenzar|nuev[oa])\b"
    r".{0,50}\b(rutina|plan|programa|mesociclo|entrenamiento)\b)"
    r"|(?:\b(rutina|plan|programa|mesociclo|entrenamiento)\b.{0,50}\b(nuev[oa]|desde\s+cero)\b)",
    re.IGNORECASE)

META_KEYWORDS_RE = re.compile(
    r"(powerbuild\w*|powerlifting|hipertrofia|\bmasa\b|volumen|fuerza\s+pura|\bfuerza\b)",
    re.IGNORECASE)

def _detectar_meta(texto: str) -> str | None:
    """Devuelve la meta canónica mencionada en el texto, o None."""
    t = texto.lower()
    if re.search(r"powerbuild|fuerte\s+y\s+grande", t):
        return "powerbuilding"
    if re.search(r"powerlifting|fuerza\s+pura", t):
        return "powerlifting"
    if re.search(r"hipertrofia|\bmasa\b|volumen|m[uú]sculo", t):
        return "hipertrofia"
    if re.search(r"\bfuerza\b", t):
        return "powerlifting"
    return None

NEGACION_CORTA_RE = re.compile(
    r"^\s*(no+|mejor\s+no|nel|nop|d[eé]jalo|as[ií]\s+est[aá]\s*(bien)?|luego|despu[eé]s)\s*[!.]*\s*$",
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

# v5.2 — Reporte "okey espero": el fallback prometió "tu plan se está generando"
# sin que NADA se hubiera disparado. Este patrón detecta esa promesa en el
# último mensaje del coach para que Python la reconcilie con la realidad.
PROMESA_GENERACION_RE = re.compile(
    r"se\s+est[aá]\s+(?:gener|arma|crea|calcul|cocin)|est[aá]\s+en\s+el\s+horno|"
    r"estar[aá]\s+list[oa]|en\s+2-?3\s+min|te\s+aviso\s+cuando|"
    r"siendo\s+calculad|ya\s+viene\s+tu\s+(?:plan|rutina|mesociclo)",
    re.IGNORECASE)

# ==============================================================================
# v6 — INTENCIONES DESTRUCTIVAS (Borrar plan / Cambiar días)
# ==============================================================================
BORRAR_PLAN_RE = re.compile(
    r"\b(borra\w*|b[oó]rra\w*|elimina\w*|elim[ií]na\w*)\b.{0,25}"
    r"\b(plan|rutina|entrenamiento|mesociclo|todo)\b"
    r"|\b(plan|rutina|entrenamiento|mesociclo)\b.{0,25}\b(borra\w*|elimina\w*)\b",
    re.IGNORECASE)

CAMBIAR_DIAS_INTENT_RE = re.compile(
    r"\b(cambi\w*|ajust\w*|modific\w*)\b[^.?!]{0,20}\bd[ií]as\b"
    r"|\b(?:ahora|ya|solo|s[oó]lo)\b[^.?!]{0,20}\b(?:puedo|podr[ée]|quiero|entren\w*)\b[^.?!]{0,15}\bd[ií]as\b"
    r"|\bentrenar\s+\d\s*d[ií]as\b|\bpasar\s+a\s+\d\s*d[ií]as\b",
    re.IGNORECASE)

_DIAS_NUM_RE = re.compile(r"(\d)\s*d[ií]as|\bd[ií]as\b\s*(?:a\s+)?(\d)", re.IGNORECASE)

def _extraer_dias(texto: str):
    m = _DIAS_NUM_RE.search(texto)
    if not m:
        return None
    for g in m.groups():
        if g and g.isdigit() and 1 <= int(g) <= 7:
            return int(g)
    return None


# ==============================================================================
# v7 — CONSTANTES PARA REGISTRO DETERMINISTA
# ==============================================================================
PESO_CORPORAL_RE = re.compile(
    r"\b(?:amanec[íi]|estoy|hoy)\s+(?:pesando\s+|en\s+|peso\s+)?(\d{2,3}(?:[.,]\d)?)\s*(?:kg|kilos?)\b"
    r"|\bpeso\s+corporal\s+(?:de\s+|es\s+)?(\d{2,3}(?:[.,]\d)?)\b"
    r"|\bpes[oé]\s+(\d{2,3}(?:[.,]\d)?)\s*(?:kg|kilos?)\s+(?:hoy|corporal|ahora)\b",
    re.IGNORECASE)

SUSTITUIR_RE = re.compile(
    r"\bcambi\w*\s+(?:el\s+|la\s+|mi\s+)?(.+?)\s+por\s+(?:el\s+|la\s+|un[ao]?\s+)?(.+?)"
    r"(?:[.,;]|\s+(?:en\s+todo|porque|ya\s+que|me\s+|solo|s[oó]lo|por\s+hoy)|$)",
    re.IGNORECASE)
# v7 — el coach afirma en 1ª persona haber COMPLETADO una acción de backend
_CLAIM_ACCION_RE = re.compile(
    r"\b(?:ya\s+)?(?:reactiv[ée]|reanud[ée]|paus[ée]|congel[ée]|cambi[ée]|sustitu[íi]|"
    r"reempla[cz][ée]|guard[ée]|registr[ée]|anot[ée]|apunt[ée]|actualic[ée]|aplic[ée]|"
    r"elimin[ée]|borr[ée]|gener[ée]|avanc[ée])\b"
    r"|\bqued[óo]\s+(?:guardad|registrad|anotad|cambiad|pausad|reactivad|aplicad|eliminad)"
    r"|\b(?:lo\s+)?tengo\s+(?:anotad|apuntad|guardad|registrad)",
    re.IGNORECASE)


def _inferir_ejercicio_serie(mensaje: str) -> str | None:
    m = str(mensaje).lower()
    for kws, campo in _KW_EJERCICIO:
        if any(k in m for k in kws):
            return RM_CANONICO[campo]
    return None

# ==============================================================================
# v6 — BLINDAJE ADICIONAL (antídotos a los fallos F1-F5 de la línea de tiempo)
# ==============================================================================

# --- F1: reparador de onboarding ---------------------------------------------
# Variantes de pregunta escritas por Python. Si el modelo se desvía, se usa una
# de estas (rotación por microsegundo para dar variedad sin sonar a plantilla).
_ONB_VARIANTES = {
    "peso_kg": ["¿Cuánto pesas ahorita, en kilos?",
                "Cuéntame tu peso corporal actual en kg 💪",
                "Para arrancar, ¿cuál es tu peso hoy en kg?"],
    "altura_m": ["¿Y cuánto mides? (en metros o cm)",
                 "Pásame tu estatura 📏",
                 "¿Cuál es tu altura?"],
    "edad": ["¿Cuántos años tienes?",
             "¿Qué edad tienes?",
             "Dime tu edad 👊"],
    "genero": ["¿Eres hombre o mujer?",
               "Para afinar los cálculos, ¿hombre o mujer?"],
    "porcentaje_grasa": ["¿Más o menos qué porcentaje de grasa corporal tienes? "
                         "Si no lo sabes, dímelo y lo estimamos.",
                         "¿Tu porcentaje de grasa aproximado? (si no lo sabes, no pasa nada)"],
    "dias_semana": ["¿Cuántos días por semana puedes entrenar? (1 a 7)",
                    "¿Cuántos días a la semana le vas a meter?"],
    "meta": ["¿Cuál es tu meta principal: fuerza (powerlifting), hipertrofia o powerbuilding?",
             "¿Qué buscas: ganar fuerza, músculo (hipertrofia) o las dos (powerbuilding)?"],
    "rm_banca": ["¿Cuál es tu mejor marca en press de banca plano? Dime el peso y las reps (ej. 100 kg x 5).",
                 "Pásame tu tope en banca plana: peso y repeticiones."],
    "rm_sentadilla": ["¿Tu mejor marca en sentadilla libre? Peso y reps (ej. 140 kg x 4).",
                      "¿Cuánto levantas en sentadilla? Dime peso y repeticiones."],
    "rm_peso_muerto": ["¿Tu mejor peso muerto? Dame peso y reps (ej. 180 kg x 3).",
                       "Pásame tu tope en peso muerto: peso y repeticiones."],
    "rm_dominadas": ["En dominadas, ¿cuántas haces y con cuánto lastre? (o dime si son a peso corporal)",
                     "¿Tu marca en dominadas? kg de lastre y reps, o solo a peso corporal."],
}

def _pregunta_onboarding(campo: str) -> str:
    variantes = _ONB_VARIANTES.get(campo)
    if not variantes:
        return f"¿Me confirmas {_desc_campo(campo)}?"
    return variantes[datetime.now().microsecond % len(variantes)]

# Detecta que el modelo preguntó por un campo YA guardado (repregunta redundante).
_ONB_YA_PREGUNTADO = {
    "peso_kg": re.compile(r"cu[aá]nto\s+pesas|\btu\s+peso\b|peso\s+corporal", re.IGNORECASE),
    "altura_m": re.compile(r"cu[aá]nto\s+mides|\btu\s+(?:altura|estatura)\b", re.IGNORECASE),
    "edad": re.compile(r"cu[aá]ntos\s+a[ñn]os\s+tienes|\btu\s+edad\b", re.IGNORECASE),
    "genero": re.compile(r"eres\s+hombre\s+o\s+mujer|hombre\s+o\s+mujer|\btu\s+g[eé]nero\b", re.IGNORECASE),
    "porcentaje_grasa": re.compile(r"porcentaje\s+de\s+grasa|%\s+de\s+grasa|\btu\s+grasa\b", re.IGNORECASE),
    "dias_semana": re.compile(r"cu[aá]ntos\s+d[ií]as|d[ií]as\s+(?:por|a\s+la)\s+semana", re.IGNORECASE),
    "meta": re.compile(r"\btu\s+(?:meta|objetivo)\b|qu[eé]\s+buscas", re.IGNORECASE),
}
# Pregunta "fuera de guion" clásica del modelo: nivel/experiencia (NO lo recolectamos).
_ONB_OFF_SCRIPT_RE = re.compile(
    r"cu[aá]nt[oa]s?\s+a[ñn]os\s+(?:de\s+)?experiencia|"
    r"experiencia\s+entrenando|"
    r"eres\s+(?:principiante|intermedio|avanzad)|"
    r"\bnivel\b[^.?!]{0,30}(?:principiante|intermedio|avanzad)",
    re.IGNORECASE)

def _reparar_onboarding(texto: str, numero_usuario: str, campo_pendiente: str) -> tuple[str, bool]:
    """
    Solo interviene ante desvíos CLAROS del modelo durante un turno de pregunta:
      (a) preguntó por 'experiencia/nivel' (campo inexistente), o
      (b) preguntó por un dato que YA está guardado (repregunta redundante).
    En esos casos devuelve una pregunta limpia del campo pendiente. Si no detecta
    desvío, devuelve el texto original intacto (bajo riesgo de falsos positivos).
    """
    if not campo_pendiente:
        return texto, False
    t = texto.lower()
    if _ONB_OFF_SCRIPT_RE.search(t):
        return _pregunta_onboarding(campo_pendiente), True
    perfil = cargar_perfil(numero_usuario)
    rms = cargar_rms(numero_usuario)
    lleno = {
        "peso_kg": perfil.get("peso_kg") not in (None, ""),
        "altura_m": perfil.get("altura_m") not in (None, ""),
        "edad": perfil.get("edad") not in (None, ""),
        "genero": perfil.get("genero") not in (None, ""),
        "porcentaje_grasa": perfil.get("porcentaje_grasa") not in (None, ""),
        "dias_semana": perfil.get("dias_semana") not in (None, ""),
        "meta": perfil.get("meta") not in (None, ""),
    }
    for campo, patron in _ONB_YA_PREGUNTADO.items():
        if campo == campo_pendiente:
            continue
        if lleno.get(campo) and patron.search(t):
            return _pregunta_onboarding(campo_pendiente), True
    return texto, False


# --- F2: bloqueo de rutinas fantasma -----------------------------------------
# Cuenta líneas con forma de prescripción de ejercicio ("4 series de 8", "5x5",
# "8 reps"). Si NO hay plan real y el modelo escribe >=3 de estas líneas, alucinó.
_LINEA_EJERCICIO_RE = re.compile(
    r"\d+\s*(?:series?|sets?)\s*(?:de|x|×)?\s*\d+"   # "4 series de 8"
    r"|\d+\s*[x×]\s*\d+"                              # "4x8"
    r"|\d+\s*(?:reps?|repeticiones?)",               # "8 reps"
    re.IGNORECASE)

def _parece_rutina_inventada(texto: str) -> int:
    return sum(1 for linea in texto.splitlines() if _LINEA_EJERCICIO_RE.search(linea))


# --- F3/F5: recuperación de lesiones -----------------------------------------
RECUPERACION_RE = re.compile(
    r"\b(ya\s+me\s+recuper[ée]|ya\s+estoy\s+bien|ya\s+no\s+me\s+duele|"
    r"se\s+me\s+quit[óo]\s+(?:el\s+)?dolor|ya\s+no\s+tengo\s+(?:dolor|molestia)|"
    r"me\s+cur[ée]|super[ée]\s+(?:la\s+)?lesi[óo]n|estoy\s+(?:ya\s+)?recuperad[oa]|"
    r"\bsan[ée]\b)\b",
    re.IGNORECASE)

_PARTES_CUERPO = {"hombro", "rodilla", "espalda", "codo", "muñeca", "muneca",
                  "cadera", "cuello", "lumbar", "cervical", "pecho", "biceps",
                  "bíceps", "triceps", "tríceps", "tobillo", "isquios", "femoral"}

def _detectar_partes_cuerpo(texto: str) -> list[str]:
    t = texto.lower()
    return sorted({p for p in _PARTES_CUERPO if p in t})


# --- F4: integridad de estado del plan ---------------------------------------
def _plan_files_existen(numero_usuario: str) -> bool:
    ruta_base = (Path(__file__).resolve().parent.parent / "resources"
                 / "rutinas_generadas" / str(numero_usuario).strip())
    return any((ruta_base / a).exists()
               for a in ["1_acumulamiento.json", "2_fuerza.json", "3_realizacion.json"])



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

# ==============================================================================
# v5 — MODELO Y MODO THINKING
# ==============================================================================
# Cambia el modelo AQUÍ (y usa el MISMO nombre + num_ctx en el servicio generador,
# o tendrás dos modelos rotando en 24 GB y vuelve el thrashing de recargas).
MODELO_LLM = "qwen3.6:27b"   # rollback instantáneo: "qwen3:32b"

# Thinking selectivo: el modelo RAZONA solo en turnos donde decide herramientas
# o sintetiza sus resultados. Donde Python ya decidió todo (onboarding, casual,
# bloques inyectados), pensar es latencia desperdiciada: solo redacta.
# Con el 35B-A3B (~60-80 tok/s), ~400 tokens de razonamiento cuestan +5-8s:
# tiempo total similar al 32B sin pensar, pero con mejor criterio.
# Interruptor global para A/B: ponlo en False y todo vuelve al modo actual.
PENSAR_HABILITADO = False


# Sampling recomendado por Qwen para modo thinking (no-thinking usa tu config)
AJUSTES_PENSANDO = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "num_predict": 2048}

OPCIONES_QWEN_32B = {
    "num_gpu": 99,
    "temperature": 0.3,
    "top_p": 0.85,
    "top_k": 20,
    "repeat_penalty": 1.1,
    "presence_penalty": 0.0,   # v7 CRÍTICO: anula el 1.5 del Modelfile que rompía los tool calls
    "min_p": 0.0,              # v7: fija el sampler, no heredes nada
    "num_ctx": 16384,
    "num_predict": 2048,
}

# v3 — ANTI-FREEZE: en 24 GB de VRAM el 32B + KV de 16k ocupa ~22-23 GB.
# Si dos peticiones al modelo se solapan (chat + compresor, o chat + generador),
# Ollama intenta atender ambas y el sistema entra en swap/thrashing -> PC congelada.
# Este semáforo garantiza UNA sola inferencia a la vez en todo el proceso.
SEM_LLM = asyncio.Semaphore(1)
TIMEOUT_CHAT = 180.0   # ninguna llamada al modelo puede colgar el orquestador para siempre

async def chat_llm(mensajes, tools=None, opciones=None, quien="chat", pensar=False):
    """Único punto de entrada al LLM: serializado, con timeout, log y thinking opcional."""
    t0 = time.monotonic()
    async with SEM_LLM:
        espera = time.monotonic() - t0
        if espera > 1.0:
            print(f"[LLM] '{quien}' esperó {espera:.1f}s el turno del modelo")
        usar_thinking = bool(pensar and PENSAR_HABILITADO)
        kwargs = dict(model=MODELO_LLM,
                      messages=mensajes,
                      keep_alive=-1,
                      think=usar_thinking,
                      options=opciones or OPCIONES_QWEN_32B)
        if tools:
            kwargs["tools"] = tools
        resp = await asyncio.wait_for(cliente_ollama.chat(**kwargs), timeout=TIMEOUT_CHAT)
        msg = resp.get("message", {}) or {}
        razonamiento = msg.get("thinking") or ""
        print(f"[LLM] '{quien}' respondió en {time.monotonic()-t0:.1f}s "
              f"({len(mensajes)} msgs, thinking={'sí, ' + str(len(razonamiento)) + ' chars' if razonamiento else 'no'})")
        if razonamiento:
            # Oro para depurar el criterio: SE VE por qué eligió (o no) una herramienta.
            print(f"[LLM-THINK] '{quien}': {_trunc(razonamiento, 300)}")
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

# ==============================================================================
# v6 — ALMACENES Y HELPERS PARA HERRAMIENTAS
# ==============================================================================
def _ruta_overrides(numero_usuario: str) -> str:
    return os.path.join(carpeta_data, f"overrides_{str(numero_usuario).strip()}.json")

def _cargar_overrides(numero_usuario: str) -> dict:
    try:
        with open(_ruta_overrides(numero_usuario), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _guardar_overrides(numero_usuario: str, data: dict) -> None:
    with open(_ruta_overrides(numero_usuario), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _guardar_override_ejercicio(numero_usuario: str, original: str, nuevo: str, alcance: str) -> None:
    ov = _cargar_overrides(numero_usuario)
    ov.setdefault("ejercicios", {})
    ov["ejercicios"][_norm_ej(original)] = {
        "nuevo": nuevo, "alcance": alcance,
        "fecha": datetime.now().strftime("%Y-%m-%d")}
    _guardar_overrides(numero_usuario, ov)

def _limpiar_overrides_solo_hoy(numero_usuario: str) -> None:
    ov = _cargar_overrides(numero_usuario)
    ejercicios = ov.get("ejercicios", {})
    quedan = {k: v for k, v in ejercicios.items() if v.get("alcance") != "solo_hoy"}
    if len(quedan) != len(ejercicios):
        ov["ejercicios"] = quedan
        _guardar_overrides(numero_usuario, ov)

def _guardar_descarga(numero_usuario: str, semana: int, red_intensidad: float, red_series: int) -> None:
    ov = _cargar_overrides(numero_usuario)
    ov.setdefault("descargas", {})
    ov["descargas"][str(int(semana))] = {
        "red_intensidad_pct": float(red_intensidad),
        "red_series": int(red_series),
        "fecha": datetime.now().strftime("%Y-%m-%d")}
    _guardar_overrides(numero_usuario, ov)

def _ruta_historial_rm(numero_usuario: str) -> str:
    return os.path.join(carpeta_data, f"historial_rm_{str(numero_usuario).strip()}.json")

def _append_historial_rm(numero_usuario: str, ejercicio: str, rm) -> None:
    try:
        rm = float(rm)
    except (TypeError, ValueError): return
    ruta = _ruta_historial_rm(numero_usuario)
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            hist = json.load(f)
            if not isinstance(hist, list): hist = []
    except (FileNotFoundError, json.JSONDecodeError):
        hist = []
    hist.append({"fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "ejercicio": _norm_ej(ejercicio), "rm": round(rm, 1)})
    hist = hist[-500:]
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[HIST-RM] no se pudo guardar: {e}")

def _resumen_progreso_rm(numero_usuario: str, ejercicio: str | None) -> str:
    try:
        with open(_ruta_historial_rm(numero_usuario), "r", encoding="utf-8") as f:
            hist = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return ""
    if not isinstance(hist, list) or not hist: return ""
    objetivo = _norm_ej(ejercicio) if ejercicio else None
    ejercicios = ([objetivo] if objetivo else sorted({h["ejercicio"] for h in hist if h.get("ejercicio")}))
    lineas = []
    for ej in ejercicios:
        registros = [h for h in hist if h.get("ejercicio") == ej and h.get("rm") is not None]
        if not registros: continue
        rm_i, rm_f = float(registros[0]["rm"]), float(registros[-1]["rm"])
        if len(registros) == 1:
            lineas.append(f"- {ej}: 1RM actual {rm_f:g} kg (aún sin historial para comparar).")
        else:
            delta = rm_f - rm_i
            pct = (delta / rm_i * 100) if rm_i > 0 else 0
            s = "+" if delta >= 0 else ""
            lineas.append(f"- {ej}: de {rm_i:g} a {rm_f:g} kg ({s}{delta:g} kg, {s}{pct:.1f}%) entre {registros[0]['fecha'][:10]} y {registros[-1]['fecha'][:10]}.")
    return "\n".join(lineas)

def _ruta_peso(numero_usuario: str) -> str:
    return os.path.join(carpeta_data, f"peso_corporal_{str(numero_usuario).strip()}.json")

def _registrar_peso_corporal(numero_usuario: str, peso: float) -> None:
    ruta = _ruta_peso(numero_usuario)
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            hist = json.load(f)
            if not isinstance(hist, list): hist = []
    except (FileNotFoundError, json.JSONDecodeError):
        hist = []
    hist.append({"fecha": datetime.now().strftime("%Y-%m-%d"), "peso": round(float(peso), 1)})
    hist = hist[-500:]
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(hist, f, indent=2, ensure_ascii=False)

def _resumen_progreso_peso(numero_usuario: str) -> str:
    try:
        with open(_ruta_peso(numero_usuario), "r", encoding="utf-8") as f:
            hist = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return ""
    if not isinstance(hist, list) or not hist: return ""
    if len(hist) == 1: return f"- Peso corporal actual: {hist[0]['peso']:g} kg (sin historial previo)."
    p_i, p_f = float(hist[0]["peso"]), float(hist[-1]["peso"])
    delta = p_f - p_i
    s = "+" if delta >= 0 else ""
    return f"- Peso corporal: de {p_i:g} a {p_f:g} kg ({s}{delta:g} kg) entre {hist[0]['fecha']} y {hist[-1]['fecha']}."

def _ejecutar_borrado_plan(numero_usuario: str) -> int:
    ruta_base = (Path(__file__).resolve().parent.parent / "resources"
                 / "rutinas_generadas" / str(numero_usuario).strip())
    borrados = 0
    for a in ["1_acumulamiento.json", "2_fuerza.json", "3_realizacion.json"]:
        f = ruta_base / a
        if f.exists():
            try:
                f.unlink(); borrados += 1
            except OSError as e:
                print(f"[BORRAR] no se pudo borrar {a}: {e}")
    try: os.remove(_ruta_overrides(numero_usuario))
    except OSError: pass
    _actualizar_estado_json(numero_usuario, semana=1, dia=1, estado_generacion="",
                            cursor_semana=1, cursor_dia=1, plan_pausado=0,
                            fecha_programada=datetime.now().strftime("%Y-%m-%d"),
                            ultima_fecha_completada="")
    log(numero_usuario, "BORRAR", f"{borrados} archivos de plan borrados")
    return borrados

def guardar_rm(numero_usuario: str, ejercicio: str, rm,
               peso_origen=None, reps_origen=None, fuente: str = "chat") -> None:
    try:
        repositorio.guardar_rm(numero_usuario, ejercicio, rm,
                               peso_origen=peso_origen, reps_origen=reps_origen,
                               fuente=fuente)
    except Exception as e:
        print(f"[REPO-ERROR] guardar_rm({ejercicio}): {e}")
    # Hook del log propio
    try: _append_historial_rm(numero_usuario, ejercicio, rm)
    except Exception as e: print(f"[HIST-RM] {e}")

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
        # Si el atleta reporta su experiencia (dato que NO recolectamos pero es útil),
        # se guarda en el expediente en vez de descartarlo. No consume el turno.
    m_exp = re.search(r"\b(principiante|novato|intermedio|avanzad[oa]|"
                      r"(\d+)\s*a[ñn]os?\s+(?:entrenando|de\s+experiencia))\b", msg)
    if m_exp:
        guardar_dato_en_expediente(
            numero_usuario, f"El atleta se describe con experiencia: \"{m_exp.group(0)}\".")
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
            cursor_semana=1, cursor_dia=1,   # v5.3: plan nuevo -> cursor limpio
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

def detectar_series_multiples(mensaje: str) -> list[dict]:
    """v4.4 — Timeline 01:22: un volcado de varias marcas solo registraba la
    primera. Enumera TODAS las series (peso, reps y ejercicio si es deducible)
    para que la señal al modelo las liste completas."""
    encontradas = []
    for parte in re.split(r"[,;\n]|\s+y\s+", str(mensaje).lower()):
        s = detectar_serie_en_mensaje(parte)
        if not s:
            continue
        ejercicio = None
        for kws, campo in _KW_EJERCICIO:
            if any(k in parte for k in kws):
                ejercicio = RM_CANONICO[campo]
                break
        encontradas.append({"peso": s["peso"], "reps": s["reps"], "ejercicio": ejercicio})
    return encontradas

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

def _detectar_enfoque(msg: str) -> str | None:
    """v5.3 — Constraint semántico del usuario ('el siguiente día DE PIERNA')."""
    if re.search(r"\bpiernas?\b|\bpierna\b|\btren\s+inferior\b|\bpiern", msg):
        return "Pierna"
    if re.search(r"\btorsos?\b|\btren\s+superior\b|\bupper\b", msg):
        return "Torso"
    return None


def _sesiones_de_semana(numero_usuario: str, semana: int) -> list[dict]:
    """Sesiones reales de una semana (para conocer enfoques y totales sin adivinar)."""
    ruta_base = Path(__file__).resolve().parent.parent / "resources" / "rutinas_generadas" / str(numero_usuario).strip()
    for archivo in ["1_acumulamiento.json", "2_fuerza.json", "3_realizacion.json"]:
        ruta_f = ruta_base / archivo
        if not ruta_f.exists():
            continue
        try:
            with open(ruta_f, "r", encoding="utf-8") as f:
                ses = [s for s in json.load(f) if int(s.get("Semana_Abs", 0)) == int(semana)]
                if ses:
                    return ses
        except Exception:
            pass
    return []


def _avanzar_cursor(numero_usuario: str, semana: int, dia: int,
                    enfoque: str | None = None, max_saltos: int = 21) -> tuple[int, int] | None:
    """
    v5.3 — Devuelve el SIGUIENTE (semana, día) real después de (semana, día),
    saltando de semana cuando se acaba y respetando el enfoque si se pidió.
    Devuelve None si se acabó el plan: el sistema dirá la verdad en vez de
    inventar un día que no existe.
    """
    s, d = int(semana), int(dia)
    for _ in range(max_saltos):
        total = _contar_sesiones_semana(numero_usuario, s)
        if not total:
            return None                      # no hay más plan
        if d >= total:
            s, d = s + 1, 1                  # salto de semana
        else:
            d += 1
        if not _contar_sesiones_semana(numero_usuario, s):
            return None
        if enfoque is None:
            return s, d
        sesiones = _sesiones_de_semana(numero_usuario, s)
        if 1 <= d <= len(sesiones) and enfoque.lower() in str(sesiones[d - 1].get("Enfoque", "")).lower():
            return s, d                      # cumple el constraint del usuario
    return None


def detectar_consulta_rutina(mensaje: str) -> dict | None:
    msg = mensaje.lower()

    # Semana N explícita ("ver semana 2", "semana 3")
    match_semana = re.search(r'\b(?:ver|mostrar\w*|pasar\s+a|semana)\s+(\d+)', msg)
    if match_semana and not re.search(r'\bd[ií]a\b', msg):
        return {"tipo": "semana_futura", "semana_objetivo": int(match_semana.group(1))}

    # v4.2 — DÍA específico ("dime el día 3", "no pero el día 3"). Era el agujero
    # que hacía al coach repetir "hoy toca Día 1" en bucle: sin detección, la
    # telemetría solo traía el día actual y el modelo confabulaba un bloqueo.
    m_dia = re.search(r'\bd[ií]a\s*(\d+)\b', msg)
    if m_dia:
        guard = re.search(r'rutina|plan|entren|toca|ver|dime|mu[eé]str|mostrar|dame|'
                          r'ense[ñn]|ejercicio|semana|saber|c[oó]mo', msg)
        if guard or len(msg.strip()) <= 45:
            return {"tipo": "dia_especifico", "dia_objetivo": int(m_dia.group(1)),
                    "enfoque": _detectar_enfoque(msg)}

    # v5.3 — día siguiente. El regex ya NO destruye los constraints del usuario:
    # "el siguiente día DE PIERNA" conserva el enfoque para que el cursor avance
    # hasta la próxima sesión que lo cumpla, en vez de entregar el día N+1 a ciegas.
    if re.search(r'(siguiente|pr[óo]xim[oa])\s+(d[ií]a|entrenamiento|sesi[oó]n)|'
                 r'd[ií]a\s+(siguiente|que\s+sigue)|\bqu[eé]\s+sigue\b|'
                 r'\b(?:y\s+)?(?:el|la)\s+(?:que\s+)?sigue\b|\bel\s+siguiente\b', msg):
        return {"tipo": "dia_siguiente", "enfoque": _detectar_enfoque(msg)}

    # v4.2 — semana completa ("toda esta semana", "cómo vamos a entrenar la semana")
    if re.search(r'(tod[ao]|completa|entera)\s+(?:l?a\s+|esta\s+)?semana|'
                 r'semana\s+(completa|entera)|'
                 r'c[oó]mo\s+(?:vamos|voy)\s+a\s+entrenar|'
                 r'entrenar\s+(?:toda\s+)?(?:esta|la)\s+semana|'
                 r'todos\s+los\s+d[ií]as', msg):
        return {"tipo": "semana_completa"}

    patrones_semana_siguiente = [
        r'semana\s+(?:siguiente|próxima|prox|que\s+viene|futura)',
        r'(?:siguiente|próxima|prox)\s+semana',
        r'pasemos?\s+(?:a\s+)?(?:la\s+)?(?:siguiente|próxima|prox)?\s*semana',
        r'(?:avanzar?|ir|pasar)\s+(?:a\s+)?(?:la\s+)?(?:siguiente|próxima)\s+semana',
        r'(?:dame|muéstrame|ver|dime)\s+(?:la\s+)?(?:siguiente|próxima)\s+semana',
        r'(?:semana|plan|rutina)\s+\d+',
    ]
    patrones_ver_rutina = [
        r'(?:dame|mu[eé]strame|mostrarme|mostrar|dime|ver|ense[ñn]a\w*|muestra)\s+'
        r'(?:me\s+)?(?:mi\s+|la\s+|el\s+)?(?:rutina|plan|entrenamiento)',
        # v5.1 — Timeline 01:46: "que entrenamiento??" no matcheaba nada
        r'\b(?:qu[eé]|cu[aá]l)\s+entrenamiento\b',
        r'^\s*(?:y\s+)?mi\s+(?:rutina|plan|entrenamiento)\s*\??\s*$',
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

def _contar_sesiones_semana(numero_usuario: str, semana: int) -> int:
    """v4.2: cuántos días reales tiene una semana del plan (para respuestas exactas)."""
    ruta_base = Path(__file__).resolve().parent.parent / "resources" / "rutinas_generadas" / str(numero_usuario).strip()
    for archivo in ["1_acumulamiento.json", "2_fuerza.json", "3_realizacion.json"]:
        ruta_f = ruta_base / archivo
        if not ruta_f.exists():
            continue
        try:
            with open(ruta_f, "r", encoding="utf-8") as f:
                ses = [s for s in json.load(f) if int(s.get("Semana_Abs", 0)) == int(semana)]
                if ses:
                    return len(ses)
        except Exception:
            pass
    return 0

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

def _extraer_tools_de_texto(texto: str) -> list[dict]:
    """
    Recupera tool calls que el modelo escribió como TEXTO en lugar de emitirlas
    nativamente. Cubre los formatos típicos de Qwen/Ollama:
      - <tool_call>{...}</tool_call>   (Hermes de Qwen3)
      - ```json {...} ```              (fenced)
      - {"name": ..., "arguments"/"parameters": {...}}  (crudo, 1 nivel de anidamiento)
    Devuelve una lista con la MISMA forma que message['tool_calls'] para que el
    dispatcher nativo las procese igual. Lista vacía si no hay nada.
    """
    if not texto or ('"name"' not in texto and "tool_call" not in texto.lower()):
        return []
    candidatos: list[str] = []
    candidatos += re.findall(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", texto, re.DOTALL | re.IGNORECASE)
    candidatos += re.findall(r"```(?:json|tool_call)?\s*(\{.*?\})\s*```", texto, re.DOTALL | re.IGNORECASE)
    if not candidatos:
        # objetos JSON con "name" (permite 1 nivel de anidamiento en arguments)
        candidatos += re.findall(r"(\{(?:[^{}]|\{[^{}]*\})*?\"name\"(?:[^{}]|\{[^{}]*\})*?\})",
                                 texto, re.DOTALL)
    tools, firmas = [], set()
    for c in candidatos:
        c = c.strip()
        try:
            data = json.loads(c)
        except json.JSONDecodeError:
            try:
                data = json.loads(c.replace("'", '"'))
            except json.JSONDecodeError:
                continue
        if not isinstance(data, dict):
            continue
        nombre = data.get("name") or data.get("tool") or data.get("function")
        args = data.get("arguments")
        if args is None:
            args = data.get("parameters", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(nombre, str) or not nombre:
            continue
        if not isinstance(args, dict):
            args = {}
        firma = f"{nombre}|{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        if firma in firmas:
            continue
        firmas.add(firma)
        tools.append({"function": {"name": nombre, "arguments": args}})
    return tools


def construir_telemetria(fecha, hora, estatus, instruccion_de_hoy, dashboard_sql,
                         es_casual, estado_gen, semana_actual,
                         bloque_onboarding: str = "") -> str:
    """v2: el onboarding ya no manda una lista de instrucciones genéricas;
    manda EXACTAMENTE lo que el sistema acaba de hacer y la ÚNICA pregunta siguiente."""
    if bloque_onboarding:
        partes = [f"FECHA: {fecha} {hora}", bloque_onboarding]
        return TELEMETRIA_INICIO + "\n" + "\n\n".join(partes) + "\n" + TELEMETRIA_FIN

    if es_casual:
        # v4.2: en charla casual el estatus es SOLO contexto interno; antes el
        # coach saludaba con "estás atrasado" y soltaba la rutina sin que nadie
        # le preguntara.
        return (f"{TELEMETRIA_INICIO}\nFECHA: {fecha} {hora}\n"
                f"CALENDARIO (contexto interno: NO lo menciones ni muestres la rutina "
                f"a menos que el atleta saque el tema de entrenar): {estatus}"
                f"\n{TELEMETRIA_FIN}")

    partes = [f"FECHA: {fecha} {hora}",
              f"CALENDARIO: {estatus} | SEMANA ACTUAL: {semana_actual}"]

    if estado_gen == "generando":
        partes.append("ESTADO: El plan se está generando AHORA MISMO. No muestres ni "
                      "inventes ejercicios; dile que le avisarás en unos minutos.")
    else:
        # v4.4 — Timeline 01:24: el modelo afirmó "ya se está generando tu plan"
        # sin que nada se hubiera disparado. Ahora la verdad es explícita.
        partes.append("GENERACIÓN DE PLAN: ninguna en curso. PROHIBIDO decir que un plan "
                      "'se está generando' o 'ya viene en camino': solo el sistema dispara "
                      "generaciones y te lo informaría aquí.")
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

    # v6 (F4) — INTEGRIDAD ESTADO↔ARCHIVOS: si el estado cree que hay plan pero
    # no hay archivos, se corrige a "sin plan" (evita que el coach afirme tener
    # un plan que ya no existe y luego lo niegue en el siguiente turno).
    plan_existe = _plan_files_existen(numero_usuario)
    if not plan_existe and (estado_gen == "lista"
                            or int(str(estado_hoy.get("cursor_semana") or 1)) > 1):
        log(numero_usuario, "INTEGRIDAD",
            "estado indica plan pero NO hay archivos; se limpia a sin-plan")
        _actualizar_estado_json(numero_usuario, estado_generacion="",
                                cursor_semana=1, cursor_dia=1)
        estado_gen = ""
        estado_hoy["estado_generacion"] = ""
        estado_hoy["cursor_semana"] = 1
        estado_hoy["cursor_dia"] = 1

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

    if estado_hoy.get("plan_pausado"):
        estatus = "PLAN PAUSADO por el atleta; no hay entrenamiento programado hasta que lo reactive."

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

    # v4.2 — FIX bug latente: "quiero VER mi rutina" matcheaba el regex de creación
    # (quiero + rutina) y arrancaba onboarding. Si el mensaje es una CONSULTA y no
    # dice "nuevo/desde cero/otro", la intención es ver, no crear... PERO solo si
    # hay algo que ver: sin plan, una consulta de rutina ES intención de crear.
    if en_creacion and detectar_consulta_rutina(texto_puro) and not rutina_vacia and not re.search(
            r"nuev[oa]|desde\s+cero|otr[oa]\s+(rutina|plan|programa|entrenamiento)|"
            r"reempl[aá]z\w*|vuelve\s+a\s+(crear|armar|generar)", texto_puro):
        log(numero_usuario, "INTENCION", "consulta de rutina, no creación; se suprime en_creacion")
        en_creacion = False

    # v5.1 — TIMELINE 01:46 (bypass del pipeline): "¿qué entrenamiento?" sin plan
    # no arrancaba la máquina, y el LLM montaba su PROPIA entrevista de onboarding
    # fuera del control de Python (7 turnos, sin pipeline, rutina alucinada al
    # final). Regla dura: sin plan + pregunta por su rutina => Modo Creación YA.
    if not en_creacion and not onboarding_activo and rutina_vacia \
            and estado_gen != "generando" and detectar_consulta_rutina(texto_puro):
        en_creacion = True
        log(numero_usuario, "ONB", "sin plan + consulta de rutina -> se activa creación (anti-bypass)")

    # v5.1 — RED DE ARRASTRE: si sin plan y sin onboarding el atleta suelta datos
    # de perfil parseables ("hipertrofia pura", "82kg", "6 días"...), es que hay
    # una entrevista corriendo FUERA de la máquina. Se capturan los datos y la
    # máquina toma el control desde ese punto.
    extras_arrastre: list[str] = []
    if (not en_creacion and not onboarding_activo and rutina_vacia
            and estado_gen != "generando" and not bloque_onboarding):
        extras_arrastre = _parsear_campos_anclados(
            numero_usuario, mensaje_usuario, calcular_faltantes(numero_usuario))
        if extras_arrastre:
            en_creacion = True
            log(numero_usuario, "ONB-ARRASTRE",
                f"datos de perfil sin plan ni onboarding: {extras_arrastre} -> la máquina toma el control")

    # v5.2 — RECONCILIADOR DE PROMESAS: si el último mensaje del coach prometió
    # un plan "generándose" y en realidad NADA está en curso (el fallo del
    # reporte: fallback prometió, "okey espero" cayó en casual sin tools, y el
    # modelo alucinó la rutina), Python cumple o corrige la promesa AQUÍ:
    # datos completos -> dispara el pipeline DE VERDAD; datos incompletos ->
    # fuerza la corrección honesta y retoma la recolección.
    if (not onboarding_activo and not bloque_onboarding and not en_creacion
            and estado_gen != "generando"
            and (rutina_vacia or estado_gen in ("", "error"))
            and PROMESA_GENERACION_RE.search(ultimo_coach)):
        faltan_promesa = calcular_faltantes(numero_usuario)
        if not faltan_promesa:
            resultado_pipeline = await _disparar_pipeline_generacion(numero_usuario)
            log(numero_usuario, "PROMESA",
                f"promesa incumplida detectada; pipeline disparado de verdad: {resultado_pipeline!r}")
            if "[ERROR" in resultado_pipeline or "Faltan" in resultado_pipeline:
                bloque_onboarding = (
                    "OJO: en tu mensaje anterior afirmaste que su plan se estaba generando, "
                    f"pero NO era cierto, y al intentarlo ahora el sistema reportó: "
                    f"{resultado_pipeline}. Discúlpate en una línea y explícalo con honestidad.")
            else:
                bloque_onboarding = (
                    "En tu mensaje anterior prometiste un plan que en realidad NO se estaba "
                    "generando. El sistema ACABA de dispararlo de verdad con sus datos "
                    "completos. Dile con naturalidad que ya está en marcha y que estará listo "
                    "en 2-3 minutos; TÚ le avisarás. NO muestres ni inventes ejercicios.")
        else:
            campo_pendiente = faltan_promesa[0]
            _actualizar_estado_json(numero_usuario, onboarding_activo=True,
                                    campo_pendiente=campo_pendiente)
            onboarding_activo = True
            log(numero_usuario, "PROMESA",
                f"promesa incumplida con datos incompletos; onboarding retoma en '{campo_pendiente}'")
            bloque_onboarding = (
                "OJO: en tu mensaje anterior afirmaste que su plan se estaba generando, pero "
                "NO era cierto: aún faltan datos. Discúlpate en UNA línea sin tecnicismos "
                f"('me adelanté, aún me falta un dato') y pide SOLO: {_desc_campo(campo_pendiente)}.")

    # v4.2 — CONFIRMACIÓN DE REGENERACIÓN: pedir un plan nuevo teniendo uno activo
    # ya no lo reemplaza en silencio; se confirma primero (los datos no se pierden).
    regen_confirmada = False
    if not onboarding_activo and estado_hoy.get("confirmar_regeneracion"):
        intentos_regen = int(estado_hoy.get("confirmar_regeneracion") or 0)
        meta_mencionada = _detectar_meta(texto_puro)
        # v4.3 FIX: "prefiero uno de powerlifting" NO era afirmación corta ni regex
        # de creación -> el flag moría como "sin confirmación clara" y la
        # regeneración jamás se disparaba (el modelo luego lo alucinaba).
        # Ahora mencionar la meta deseada TAMBIÉN confirma, y actualiza el perfil.
        if (AFIRMACION_CORTA_RE.match(texto_puro) or EN_CREACION_RE.search(texto_puro)
                or meta_mencionada):
            _actualizar_estado_json(numero_usuario, confirmar_regeneracion=0)
            if meta_mencionada:
                actualizar_perfil(numero_usuario, meta=meta_mencionada)
                log(numero_usuario, "REGEN", f"confirmado; meta actualizada a '{meta_mencionada}'")
            else:
                log(numero_usuario, "REGEN", "reemplazo del plan CONFIRMADO por el atleta")
            en_creacion = False
            # v4.4 — LECCIÓN DE LA LÍNEA DE TIEMPO: el atleta confirma primero y
            # manda sus marcas nuevas DESPUÉS. Si disparáramos aquí, el plan se
            # regeneraría con las marcas viejas y sus datos frescos llegarían
            # tarde. Se abre un paso de VERIFICACIÓN DE DATOS antes de generar.
            extras_confirm = _parsear_campos_anclados(
                numero_usuario, mensaje_usuario, [c for c, _t, _d in CAMPOS_ONBOARDING])
            _actualizar_estado_json(numero_usuario, confirmar_datos_regen=1)
            nota = (f"ACTUALIZADO (venía en su mensaje): {'; '.join(extras_confirm)}.\n"
                    if extras_confirm else "")
            bloque_onboarding = (
                "REEMPLAZO DE PLAN CONFIRMADO — paso de verificación de datos.\n" + nota +
                f"Resumen vigente de su ficha: {_resumen_perfil_humano(numero_usuario)}.\n"
                "Muéstrale ese resumen con tus palabras y pregúntale en UNA línea si genera "
                "el plan nuevo con esos datos, o si actualiza algo (peso, marcas con "
                "ejercicio+kg+reps, días o meta). NO llames herramientas; el sistema captura "
                "lo que responda. NO afirmes que algo se está generando todavía.")
        elif NEGACION_CORTA_RE.match(texto_puro):
            _actualizar_estado_json(numero_usuario, confirmar_regeneracion=0)
            en_creacion = False
            bloque_onboarding = ("El atleta decidió CONSERVAR su plan actual. Confírmaselo en "
                                 "una línea, sin drama, y sigue la conversación normal.")
            log(numero_usuario, "REGEN", "reemplazo rechazado; conserva plan")
        elif intentos_regen < 2:
            # Mensaje ambiguo: NO se descarta la intención; se re-pregunta una vez
            _actualizar_estado_json(numero_usuario, confirmar_regeneracion=intentos_regen + 1)
            en_creacion = False
            bloque_onboarding = ("SIGUE PENDIENTE confirmar si reemplaza su plan actual por uno "
                                 "nuevo. Responde primero a lo que acaba de decir y vuelve a "
                                 "preguntarle en UNA línea (sí/no) si arma el plan nuevo. "
                                 "NO pidas datos ni afirmes que algo se está generando.")
            log(numero_usuario, "REGEN", f"sin confirmación clara; re-pregunta ({intentos_regen+1}/2)")
        else:
            _actualizar_estado_json(numero_usuario, confirmar_regeneracion=0)
            log(numero_usuario, "REGEN", "confirmación abandonada tras 2 intentos; flujo normal")

    # v4.4 — VERIFICACIÓN DE DATOS PRE-REGENERACIÓN: aquí el atleta vuelca marcas
    # nuevas, corrige peso/meta/días, y el sistema lo captura TODO en código.
    # "sí/dale/genera" dispara la regeneración; "cancélalo" conserva el plan viejo.
    if (not onboarding_activo and not bloque_onboarding
            and estado_hoy.get("confirmar_datos_regen")):
        if CANCELAR_ONBOARDING_RE.search(texto_puro):
            _actualizar_estado_json(numero_usuario, confirmar_datos_regen=0)
            log(numero_usuario, "REGEN", "verificación cancelada; conserva su plan actual")
            bloque_onboarding = ("El atleta canceló el cambio de plan: conserva el actual y sus "
                                 "datos quedan guardados. Confírmalo en una línea y sigue normal.")
        else:
            extras_regen = _parsear_campos_anclados(
                numero_usuario, mensaje_usuario, [c for c, _t, _d in CAMPOS_ONBOARDING])
            meta_regen = _detectar_meta(texto_puro)
            if meta_regen:
                actualizar_perfil(numero_usuario, meta=meta_regen)
                extras_regen.append(f"meta {meta_regen}")
            listo = bool(AFIRMACION_CORTA_RE.match(texto_puro) or
                         re.search(r"\b(gener[aá]\w*|ya\s+con\s+eso|con\s+esos?\s+datos?|"
                                   r"as[ií]\s+est[aá]\s+bien|est[aá]n?\s+bien|correcto|dale|"
                                   r"adelante|hazlo)\b", texto_puro))
            if listo:
                _actualizar_estado_json(numero_usuario, confirmar_datos_regen=0)
                resultado_pipeline = await _disparar_pipeline_generacion(numero_usuario)
                log(numero_usuario, "REGEN", f"datos verificados -> pipeline: {resultado_pipeline!r}")
                extra_txt = (f"ACTUALIZADO al vuelo: {'; '.join(extras_regen)}. "
                             if extras_regen else "")
                if "[ERROR" in resultado_pipeline or "Faltan" in resultado_pipeline:
                    bloque_onboarding = (f"{extra_txt}El sistema intentó regenerar pero: "
                                         f"{resultado_pipeline}. Explícaselo con naturalidad.")
                else:
                    bloque_onboarding = (
                        f"{extra_txt}EL SISTEMA YA DISPARÓ la regeneración del plan con sus "
                        "datos verificados. Dile que estará listo en 2-3 minutos y que TÚ le "
                        "avisarás. NO describas ejercicios, NO muestres el plan viejo, NO "
                        "preguntes nada más.")
            elif extras_regen:
                log(numero_usuario, "REGEN", f"datos actualizados en verificación: {extras_regen}")
                bloque_onboarding = (
                    f"ACTUALIZADO EN SU FICHA: {'; '.join(extras_regen)}.\n"
                    f"Resumen vigente: {_resumen_perfil_humano(numero_usuario)}.\n"
                    "Confírmale en corto lo actualizado y pregúntale si genera ya el plan o "
                    "cambia algo más. Si dio una marca SIN decir de qué ejercicio, pregúntale "
                    "a cuál corresponde. NO llames herramientas; el sistema captura todo.")
            else:
                bloque_onboarding = (
                    "SIGUE EN VERIFICACIÓN DE DATOS PRE-REGENERACIÓN. Su mensaje no trajo un "
                    "dato reconocible ni una confirmación: responde a lo que dijo y vuelve a "
                    "preguntarle, en corto, si genera el plan con el resumen que le diste o "
                    "qué actualiza (peso, marcas con ejercicio+kg+reps, días o meta).")

    if onboarding_activo and CANCELAR_ONBOARDING_RE.search(texto_puro):
        _actualizar_estado_json(numero_usuario, onboarding_activo=False, campo_pendiente="")
        onboarding_activo = False
        log(numero_usuario, "ONB", "CANCELADO por el atleta (datos conservados)")
        bloque_onboarding = ("EL ATLETA CANCELÓ la creación del plan. Confírmaselo sin drama "
                             "(sus datos quedan guardados para cuando quiera retomar) y sigue "
                             "la conversación normal.")

    elif not onboarding_activo and en_creacion and estado_gen != "generando" \
            and not rutina_vacia and not regen_confirmada:
        # v4.2 — ya tiene un plan activo: confirmar antes de reemplazarlo
        _actualizar_estado_json(numero_usuario, confirmar_regeneracion=1)
        meta_disparo = _detectar_meta(texto_puro)
        if meta_disparo:
            actualizar_perfil(numero_usuario, meta=meta_disparo)
            log(numero_usuario, "REGEN", f"meta '{meta_disparo}' capturada del mensaje disparador")
        log(numero_usuario, "REGEN", "plan activo detectado; se pide confirmación antes de regenerar")
        bloque_onboarding = (
            f"El atleta pidió un plan NUEVO{f' de {meta_disparo}' if meta_disparo else ''}, pero "
            f"YA tiene uno activo (va en la semana {estado_hoy['semana']}). Pregúntale en UNA "
            "sola línea, con tu tono, si quiere reemplazarlo por uno nuevo desde cero. Menciona "
            f"que lo armarías con sus datos guardados ({_resumen_perfil_humano(numero_usuario)}) "
            "y que si alguna marca cambió te la diga junto con su confirmación. "
            "NO afirmes que algo se está generando todavía.")

    elif not onboarding_activo and en_creacion and estado_gen != "generando":
        # v3.1 — FIX: el mensaje que dispara puede TRAER datos
        # ("hazme una rutina, peso 82kg, mido 1.73, entreno 5 días..."). Antes se
        # ignoraban y el coach pedía el peso recién dado -> "me vuelve a pedir todo".
        extras_iniciales = extras_arrastre + _parsear_campos_anclados(
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
            # v6 — FLUJOS DESTRUCTIVOS (Intercepción Python)
            # ==========================================================================
            if not onboarding_activo and not bloque_onboarding and estado_hoy.get("confirmar_borrado"):
                intentos_b = int(estado_hoy.get("confirmar_borrado") or 0)
                if AFIRMACION_CORTA_RE.match(texto_puro) or re.search(
                        r"\b(b[oó]rralo|elim[ií]nalo|s[ií]\s+b[oó]rra|s[ií]\s+elim[ií]na|hazlo|conf[ií]rmo|adelante)\b",
                        texto_puro):
                    borrados = _ejecutar_borrado_plan(numero_usuario)
                    _actualizar_estado_json(numero_usuario, confirmar_borrado=0)
                    log(numero_usuario, "BORRAR", f"confirmado ({borrados} archivos)")
                    bloque_onboarding = (f"El sistema ELIMINÓ el plan del atleta ({borrados} archivos). "
                                         "Sus marcas e historial se conservan. Confírmaselo sin drama y ofrécele armar uno "
                                         "nuevo cuando quiera. NO afirmes que sigue habiendo un plan activo.")
                elif NEGACION_CORTA_RE.match(texto_puro) or CANCELAR_ONBOARDING_RE.search(texto_puro):
                    _actualizar_estado_json(numero_usuario, confirmar_borrado=0)
                    log(numero_usuario, "BORRAR", "cancelado; conserva plan")
                    bloque_onboarding = "El atleta decidió CONSERVAR su plan. Confírmaselo en una línea y sigue normal."
                elif intentos_b < 2:
                    _actualizar_estado_json(numero_usuario, confirmar_borrado=intentos_b + 1)
                    bloque_onboarding = ("SIGUE PENDIENTE confirmar el borrado (es IRREVERSIBLE). Responde a "
                                         "lo que dijo y vuelve a preguntarle claro, en UNA línea, si de verdad quiere ELIMINAR "
                                         "su plan (sí/no). NO borres nada aún.")
                else:
                    _actualizar_estado_json(numero_usuario, confirmar_borrado=0)
            elif (not onboarding_activo and not bloque_onboarding and not rutina_vacia
                  and BORRAR_PLAN_RE.search(texto_puro)):
                _actualizar_estado_json(numero_usuario, confirmar_borrado=1)
                log(numero_usuario, "BORRAR", "intención detectada -> pide confirmación")
                bloque_onboarding = (f"El atleta pidió ELIMINAR su plan (va en semana {estado_hoy['semana']}). "
                                     "Es IRREVERSIBLE. Pregúntale en UNA línea, con tu tono, si está seguro de borrarlo "
                                     "(sus marcas se conservan). NO borres nada todavía; solo pregunta.")

            if not onboarding_activo and not bloque_onboarding and estado_hoy.get("confirmar_cambio_dias"):
                nuevos_dias = int(estado_hoy.get("confirmar_cambio_dias") or 0)
                intentos_d = int(estado_hoy.get("intentos_cambio_dias") or 0)
                if AFIRMACION_CORTA_RE.match(texto_puro) or EN_CREACION_RE.search(texto_puro):
                    actualizar_perfil(numero_usuario, dias_semana=nuevos_dias)
                    _actualizar_estado_json(numero_usuario, confirmar_cambio_dias=0, intentos_cambio_dias=0)
                    resultado_pipeline = await _disparar_pipeline_generacion(numero_usuario)
                    log(numero_usuario, "CAMBIO-DIAS", f"confirmado {nuevos_dias} días -> {resultado_pipeline!r}")
                    if "[ERROR" in resultado_pipeline or "Faltan" in resultado_pipeline:
                        bloque_onboarding = f"Se intentó rehacer el plan a {nuevos_dias} días pero: {resultado_pipeline}."
                    else:
                        bloque_onboarding = (f"El sistema YA está rehaciendo el plan para {nuevos_dias} días. "
                                             "Dile que estará listo en 2-3 minutos y le avisarás. NO muestres ejercicios.")
                elif NEGACION_CORTA_RE.match(texto_puro) or CANCELAR_ONBOARDING_RE.search(texto_puro):
                    _actualizar_estado_json(numero_usuario, confirmar_cambio_dias=0, intentos_cambio_dias=0)
                    bloque_onboarding = "El atleta prefiere mantener sus días actuales. Confírmaselo y sigue."
                elif intentos_d < 2:
                    _actualizar_estado_json(numero_usuario, intentos_cambio_dias=intentos_d + 1)
                    bloque_onboarding = (f"SIGUE PENDIENTE confirmar el cambio a {nuevos_dias} días (rehará el "
                                         "plan desde cero). Vuelve a preguntarle en UNA línea si lo confirma (sí/no).")
                else:
                    _actualizar_estado_json(numero_usuario, confirmar_cambio_dias=0, intentos_cambio_dias=0)
            elif (not onboarding_activo and not bloque_onboarding and not rutina_vacia
                  and CAMBIAR_DIAS_INTENT_RE.search(texto_puro)):
                nd = _extraer_dias(texto_puro)
                perfil_actual = cargar_perfil(numero_usuario)
                if nd and nd != perfil_actual.get("dias_semana"):
                    _actualizar_estado_json(numero_usuario, confirmar_cambio_dias=nd, intentos_cambio_dias=0)
                    log(numero_usuario, "CAMBIO-DIAS", f"intención {nd} días -> pide confirmación")
                    bloque_onboarding = (
                        f"El atleta quiere entrenar {nd} días (antes: {perfil_actual.get('dias_semana', '?')}). "
                        "Cambiar los días rehace el plan desde cero. Pregúntale en UNA línea si quiere que "
                        "lo rehagas con esa nueva frecuencia (sí/no). NO rehagas nada todavía.")
    # ==========================================================================
    # MODO NORMAL (rutina activa / charla) — señales e interceptores
    # ==========================================================================
    # v4.1: vocabulario ampliado — "ya terminé", "me duele el hombro" o "dormí mal"
    # eran clasificados como casuales y se quedaban SIN herramientas (avanzar_dia,
    # expediente...). Además, un mensaje largo nunca es casual aunque no matchee.
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
        "maquina", "máquina", "video", "veloc", "prefiero", "odio", "calent", "deficit", "déficit",
        # v7 - nuevas palabras de control
        "viaje", "volvi", "volví", "regrese", "regresé", "seguir", "continuar",
        "reactiva", "reanuda", "reanudar", "vuelta"
    }
    es_casual = (not any(p in texto_puro for p in PALABRAS_ENTRENAMIENTO)
                 and len(texto_puro) <= 60)

    # v5.2 — Reporte "okey espero": un mensaje de transición corto tras un turno
    # donde el coach habló de plan/generación NO debe apagar herramientas ni
    # thinking; era la ventana exacta donde el modelo quedaba forzado a alucinar.
    if es_casual and re.search(r"plan|rutina|gener|mesociclo|minut|marcas?", ultimo_coach.lower()) \
            and (AFIRMACION_CORTA_RE.match(texto_puro)
                 or re.search(r"\bespero\b|\bok(?:ey)?\b|\bva\b|\bgracias\b", texto_puro)):
        es_casual = False
        log(numero_usuario, "MODO", "transición corta tras tema de plan -> NO casual (tools activas)")

        # v7 — NUNCA casual si hay una acción latente que necesita herramientas.
    if es_casual:
        accion_latente = (estado_hoy.get("plan_pausado")
                          or estado_hoy.get("confirmar_borrado")
                          or estado_hoy.get("confirmar_cambio_dias")
                          or estado_hoy.get("confirmar_regeneracion")
                          or estado_hoy.get("confirmar_datos_regen")
                          or re.search(r"\b(reactiv\w*|reanud\w*|seguir|continu\w*|volv[íi]|"
                                       r"regres[ée]|cambi\w*|registr\w*|paus\w*|borr\w*|elimin\w*|"
                                       r"viaje|de\s+vuelta|ya\s+volv[íi])\b", texto_puro))
        if accion_latente:
            es_casual = False
            log(numero_usuario, "MODO", "acción latente -> NO casual (tools activas)")

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
        tipo_consulta = consulta_rutina.get("tipo")
        semana_obj = estado_hoy["semana"]
        dia_obj = None
        enfoque_pedido = consulta_rutina.get("enfoque")
        fin_de_plan = False

        # v5.3 — CURSOR DE CONSULTA PERSISTENTE (estado_usuarios.json, no memoria:
        # historial_memoria se destruye cada turno y una global no sobrevive
        # reinicios). Arregla el bucle "siempre Día 2": antes dia_obj se calculaba
        # desde estado_hoy['dia'] (el día EN CURSO, siempre 1) -> 1+1=2 eternamente.
        # Ahora "siguiente" avanza desde el ÚLTIMO DÍA CONSULTADO.
        cur_sem = estado_hoy.get("cursor_semana")
        cur_dia = estado_hoy.get("cursor_dia")

        if tipo_consulta == "semana_futura":
            semana_obj = consulta_rutina.get("semana_objetivo", estado_hoy["semana"] + 1)
            _actualizar_estado_json(numero_usuario, cursor_semana=semana_obj, cursor_dia=0)
        elif tipo_consulta == "dia_especifico":
            dia_obj = consulta_rutina["dia_objetivo"]
            # Un día explícito REANCLA el cursor: "día 3" -> "y el siguiente" = 4.
            _actualizar_estado_json(numero_usuario, cursor_semana=semana_obj, cursor_dia=dia_obj)
        elif tipo_consulta == "dia_siguiente":
            # Ancla: último día consultado si existe; si no, el día en curso.
            if cur_sem and cur_dia:
                base_sem, base_dia = int(cur_sem), int(cur_dia)
            else:
                base_sem = estado_hoy["semana"]
                base_dia = int(estado_hoy.get("dia", 1)) if str(estado_hoy.get("dia", 1)).isdigit() else 1
            siguiente = _avanzar_cursor(numero_usuario, base_sem, base_dia, enfoque_pedido)
            if siguiente:
                semana_obj, dia_obj = siguiente
                _actualizar_estado_json(numero_usuario, cursor_semana=semana_obj, cursor_dia=dia_obj)
                log(numero_usuario, "CURSOR",
                    f"S{base_sem}D{base_dia} -> S{semana_obj}D{dia_obj}"
                    + (f" (filtrado por enfoque '{enfoque_pedido}')" if enfoque_pedido else ""))
            else:
                fin_de_plan = True
                log(numero_usuario, "CURSOR",
                    f"sin más días tras S{base_sem}D{base_dia}"
                    + (f" con enfoque '{enfoque_pedido}'" if enfoque_pedido else ""))
        else:
            # "semana_completa" / "ver_rutina": el usuario cambió de marco -> el
            # cursor se reancla a la semana mostrada (dia 0 = aún no pidió día).
            _actualizar_estado_json(numero_usuario, cursor_semana=semana_obj, cursor_dia=0)

        if fin_de_plan:
            disponibles = _semanas_disponibles(numero_usuario)
            rutina_pedida = (
                "NO HAY MÁS DÍAS después del último que le mostraste"
                + (f" que sean de {enfoque_pedido}" if enfoque_pedido else "")
                + f". El plan cubre las semanas {disponibles if disponibles else 'ninguna'}. "
                "Díselo tal cual y ofrécele volver a una semana que sí exista. "
                "PROHIBIDO inventar días o semanas que no están en el plan.")
        elif dia_obj is not None:
            total = _contar_sesiones_semana(numero_usuario, semana_obj)
            if total and dia_obj > total:
                rutina_pedida = (f"EL DÍA {dia_obj} NO EXISTE: la semana {semana_obj} tiene "
                                 f"exactamente {total} sesiones (días 1 a {total}). Díselo tal "
                                 "cual y ofrécele ver una de esas. PROHIBIDO inventar un día extra.")
                log(numero_usuario, "CONSULTA-DIA", f"día {dia_obj} fuera de rango (semana de {total})")
            else:
                rutina_pedida = obtener_rutina_del_json(numero_usuario, semana_obj, dia_obj, solo_hoy=True)
                nota_enfoque = (f" Es el siguiente día de {enfoque_pedido} que pidió."
                                if enfoque_pedido else "")
                rutina_pedida = (f"EL ATLETA PIDIÓ EL DÍA {dia_obj} DE LA SEMANA {semana_obj}."
                                 f"{nota_enfoque} (Si el encabezado dice 'HOY', ignóralo: "
                                 f"preséntalo como el día {dia_obj}, NO como el entrenamiento "
                                 "de hoy):\n" + rutina_pedida)
                log(numero_usuario, "CONSULTA-DIA", f"día {dia_obj} de semana {semana_obj} inyectado")
        else:
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

    # ==========================================================================
    # v7 — REGISTRO DETERMINISTA (serie / peso / sustitución).
    # Python ejecuta la acción con lo que ya parseó; el modelo SOLO confirma.
    # Esto elimina la dependencia de que el modelo emita la tool call.
    # ==========================================================================
    accion_ejecutada = False
    serie_registrada_python = False
    peso_registrado_python = False
    sustitucion_hecha_python = False

    # --- SERIE(S) ---
    if serie and not rutina_vacia:
        series_todas = detectar_series_multiples(mensaje_usuario)
        if not series_todas:
            series_todas = [{"peso": serie["peso"], "reps": serie["reps"],
                             "ejercicio": _inferir_ejercicio_serie(mensaje_usuario)}]
        registradas, pendientes = [], []
        for s in series_todas:
            ej = s.get("ejercicio") or _inferir_ejercicio_serie(mensaje_usuario)
            if not ej:
                pendientes.append(f"{s['peso']:g} kg x {s['reps']}")
                continue
            try:
                guardar_serie_sql(numero_usuario, {"ejercicio": ej, "peso_kg": s["peso"],
                                                   "repeticiones": int(s["reps"])})
                detalle = f"{ej} {s['peso']:g} kg x {int(s['reps'])}"
                if 1 <= int(s["reps"]) <= 15:
                    rm_val = calcular_1rm(TipoEjercicio("peso_libre"), int(s["reps"]), s["peso"])
                    guardar_rm(numero_usuario, ej, rm_val, peso_origen=s["peso"],
                               reps_origen=int(s["reps"]), fuente="auto_serie")
                    detalle += f" (1RM estimado {rm_val:g} kg)"
                registradas.append(detalle)
            except Exception as e:
                log(numero_usuario, "SERIE-AUTO", f"error: {e}")
        if registradas:
            serie_registrada_python = True
            accion_ejecutada = True
            contenido_mensaje += ("\n\n[SISTEMA — SERIES YA REGISTRADAS]: " + "; ".join(registradas)
                                  + ". Ya quedaron guardadas con su 1RM. NO llames herramientas para esto; solo "
                                    "confírmaselo con tu tono y un comentario breve. Usa EXACTAMENTE esos números.")
            log(numero_usuario, "SERIE-AUTO", f"registradas: {registradas}")
        if pendientes:
            contenido_mensaje += ("\n\n[SISTEMA]: También reportó series sin decir de qué ejercicio: "
                                  f"{', '.join(pendientes)}. Pregúntale a cuál corresponden ANTES de darlas por hechas.")

    # --- PESO CORPORAL ---
    if not onboarding_activo and not bloque_onboarding and not serie_registrada_python:
        m_peso = PESO_CORPORAL_RE.search(texto_puro)
        if m_peso:
            valor = next((g for g in m_peso.groups() if g), None)
            try:
                pc = float(str(valor).replace(",", "."))
            except (TypeError, ValueError):
                pc = None
            if pc and 35 <= pc <= 300:
                _registrar_peso_corporal(numero_usuario, pc)
                actualizar_perfil(numero_usuario, peso_kg=round(pc, 1))
                peso_registrado_python = True
                accion_ejecutada = True
                contenido_mensaje += (f"\n\n[SISTEMA — PESO YA REGISTRADO]: guardé su peso corporal "
                                      f"de {pc:g} kg y actualicé su ficha. NO llames herramientas para esto; solo "
                                      "confírmaselo brevemente.")
                log(numero_usuario, "PESO-AUTO", f"{pc} kg")

    # --- SUSTITUCIÓN DE EJERCICIO ---
    if (not onboarding_activo and not bloque_onboarding and not rutina_vacia
            and not serie_registrada_python):
        m_sub = SUSTITUIR_RE.search(mensaje_usuario)
        if m_sub:
            orig = m_sub.group(1).strip(" .,")
            nuevo = m_sub.group(2).strip(" .,")
            if 2 <= len(orig) <= 60 and 2 <= len(nuevo) <= 60 and orig.lower() != nuevo.lower():
                alcance = "solo_hoy" if re.search(r"solo\s+hoy|s[oó]lo\s+hoy|por\s+hoy",
                                                  texto_puro) else "permanente"
                _guardar_override_ejercicio(numero_usuario, orig, nuevo, alcance)
                guardar_dato_en_expediente(numero_usuario,
                                           f"El atleta sustituyó '{orig}' por '{nuevo}' (alcance: {alcance}).")
                sustitucion_hecha_python = True
                accion_ejecutada = True
                contenido_mensaje += (f"\n\n[SISTEMA — SUSTITUCIÓN YA APLICADA]: cambié '{orig}' por "
                                      f"'{nuevo}' ({'todo el plan' if alcance == 'permanente' else 'solo hoy'}), "
                                      "manteniendo series, reps y cargas. NO llames herramientas; solo confírmaselo.")
                log(numero_usuario, "SUSTITUCION-AUTO", f"'{orig}' -> '{nuevo}' ({alcance})")

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

    # v6 (F3/F5) — RECUPERACIÓN DE LESIÓN: si el atleta dice que ya se curó,
    # se registra, se marca la parte como resuelta y se le ordena al modelo NO
    # tratar lesiones previas como activas ni dramatizar (nada de "hospital").
    if not onboarding_activo and not bloque_onboarding and RECUPERACION_RE.search(texto_puro):
        partes = _detectar_partes_cuerpo(texto_puro)
        resueltas = set(estado_hoy.get("lesiones_resueltas") or [])
        resueltas |= set(partes) if partes else {"general"}
        estado_hoy["lesiones_resueltas"] = sorted(resueltas)  # refresco local
        _actualizar_estado_json(numero_usuario, lesiones_resueltas=sorted(resueltas))
        guardar_dato_en_expediente(
            numero_usuario,
            f"({fecha_actual}) El atleta reportó RECUPERACIÓN: "
            f"\"{str(mensaje_usuario).strip()[:180]}\". Tratar como RESUELTA cualquier molestia "
            f"previa en: {', '.join(partes) or 'no especificado'}.")
        contenido_mensaje += (
            "\n\n[ACTUALIZACIÓN DE SALUD — PRIORIDAD]: el atleta dice que YA SE RECUPERÓ / ya no "
            "le duele. NO trates ninguna lesión previa como activa, NO menciones reposo médico, "
            "médicos ni hospital. Felicítalo por la recuperación y retoma el entrenamiento normal "
            "(con progresión prudente solo si él lo pide).")
        log(numero_usuario, "RECUPERACION", f"partes resueltas: {partes or 'general'}")

    # v6 (F3/F5) — no reinyectar notas de una lesión YA resuelta, salvo que el
    # atleta la vuelva a mencionar en este turno. Mata el "loro" en su origen.
    if contexto_extra:
        resueltas = set(estado_hoy.get("lesiones_resueltas") or [])
        partes_ahora = set(_detectar_partes_cuerpo(texto_puro))
        if resueltas and not (partes_ahora & resueltas):
            lineas_ok = []
            for linea in contexto_extra.splitlines():
                low = linea.lower()
                es_nota_lesion = any(k in low for k in ("dolor", "molestia", "lesion", "lesión", "duele"))
                menciona_resuelta = any(r in low for r in resueltas)
                if es_nota_lesion and menciona_resuelta:
                    continue  # se omite: lesión resuelta que el atleta no mencionó
                lineas_ok.append(linea)
            contexto_extra = "\n".join(lineas_ok)
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

    # v7 — REFUERZO DE EMISIÓN NATIVA
    if tools_de_turno:
        contenido_mensaje += ("\n\n[SISTEMA]: Si decides usar una herramienta, EMÍTELA como "
                              "llamada real (no la escribas como texto ni asumas que ya se ejecutó).")


    # v3 — num_predict dinámico: una charla normal no necesita 2048 tokens
    quiere_semana_completa = bool(consulta_rutina)
    opciones_turno = dict(OPCIONES_QWEN_32B)
    if not quiere_semana_completa:
        opciones_turno["num_predict"] = 768

    # v5 — THINKING SELECTIVO: razona solo cuando el modelo DECIDE (elegir
    # herramientas, sintetizar sus resultados). En onboarding/casual Python ya
    # decidió todo y el modelo solo redacta: pensar ahí es latencia tirada.
    # OJO: los tokens de razonamiento consumen num_predict -> presupuesto amplio.
    pensar_turno = PENSAR_HABILITADO and tools_de_turno is not None
    if pensar_turno:
        opciones_turno.update(AJUSTES_PENSANDO)
    log(numero_usuario, "THINK", "activado este turno" if pensar_turno else "desactivado (solo redacción)")

    try:
        try:
            respuesta_ia = await chat_llm(
                historial_memoria[numero_usuario],
                tools=tools_de_turno,
                opciones=opciones_turno,
                quien="principal",
                pensar=pensar_turno
            )
        except asyncio.TimeoutError:
            # v5.2 — Reporte 131.6s: el turno crítico con thinking puede exceder
            # el timeout (sobre todo con un modelo denso). Antes esto caía al
            # fallback sin tools y ahí nacía la promesa falsa. Ahora se reintenta
            # UNA vez sin thinking y con presupuesto corto: rápido y con tools.
            log(numero_usuario, "THINK-TIMEOUT",
                "turno principal excedió el timeout; reintento SIN thinking")
            respuesta_ia = await chat_llm(
                historial_memoria[numero_usuario],
                tools=tools_de_turno,
                opciones={**OPCIONES_QWEN_32B, "num_predict": 768},
                quien="principal-retry",
                pensar=False
            )

        mensaje_salida = respuesta_ia.get("message", {})
        texto_final    = mensaje_salida.get("content", "") or ""

        max_loops = 4
        loop_count = 0
        mensajes_orquestador = list(historial_memoria[numero_usuario])

        while tools_de_turno and loop_count < max_loops:
            calls = mensaje_salida.get("tool_calls") or []
            if not calls:
                # v7 — RESCATE: el modelo pudo escribir la tool call como TEXTO.
                # Se recupera y se procesa por el MISMO dispatcher (todas las tools).
                texto_bruto = mensaje_salida.get("content", "") or ""
                calls = _extraer_tools_de_texto(texto_bruto)
                if calls:
                    log(numero_usuario, "TOOL-RESCATE",
                        f"{len(calls)} recuperada(s) del texto: "
                        f"{[_extraer_tool(t)[0] for t in calls]}")
                    mensaje_salida = dict(mensaje_salida)
                    mensaje_salida["content"] = ""  # se limpia para no mezclar JSON con la redacción
                else:
                    texto_final = texto_bruto
                    break
            accion_ejecutada = True

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

                        if obtener_estado_usuario(numero_usuario).get("plan_pausado"):

                            texto_herramientas_agregado += ("[SISTEMA]: El plan está PAUSADO. No se "

                                                            "avanzó nada. Pregúntale si quiere reactivarlo antes de entrenar.\n")

                        elif avanzar_estado_usuario(numero_usuario, forzar=bool(args.get("forzar"))):

                            _limpiar_overrides_solo_hoy(numero_usuario)

                            nuevo_estado = obtener_estado_usuario(numero_usuario)

                            nueva_rutina = obtener_rutina_del_json(

                                numero_usuario, nuevo_estado["semana"], nuevo_estado["dia"], solo_hoy=True)

                            texto_herramientas_agregado += (f"[SISTEMA: DÍA AVANZADO]\nSemana "

                                                            f"{nuevo_estado['semana']}, Día: {nuevo_estado['dia']}.\nNueva rutina:\n"

                                                            f"{nueva_rutina}\n")

                        else:

                            texto_herramientas_agregado += ("[SISTEMA]: CANDADO ACTIVO — el calendario "

                                                            "ya avanzó hoy. NO afirmes que avanzaste. Explícaselo y pregúntale si "

                                                            "de verdad quiere forzar otro avance hoy.\n")

                        necesita_reinvocacion = True


                    elif nombre == "sustituir_ejercicio":

                        orig = str(args.get("ejercicio_original", "")).strip()

                        nuevo = str(args.get("ejercicio_nuevo", "")).strip()

                        alcance = args.get("alcance", "permanente")

                        if alcance not in ("permanente", "solo_hoy"): alcance = "permanente"

                        if orig and nuevo:

                            _guardar_override_ejercicio(numero_usuario, orig, nuevo, alcance)

                            guardar_dato_en_expediente(numero_usuario,

                                                       f"El atleta sustituyó '{orig}' por '{nuevo}' (motivo: {args.get('motivo', 'n/e')}, alcance: {alcance}).")

                            texto_herramientas_agregado += (f"[SISTEMA]: Sustitución guardada: '{orig}' -> '{nuevo}' "

                                                            f"({'todo el plan' if alcance == 'permanente' else 'solo hoy'}). Confírmaselo con naturalidad; series, reps y cargas se mantienen.\n")

                        else:

                            texto_herramientas_agregado += "[SISTEMA - ERROR]: faltó el ejercicio original o el nuevo.\n"

                        necesita_reinvocacion = True


                    elif nombre == "aplicar_descarga":

                        sem = int(_num_seguro(args.get("semana"), estado_hoy["semana"]) or estado_hoy["semana"])

                        red_int = min(max(float(_num_seguro(args.get("reduccion_intensidad_pct"), 10) or 10), 0), 40)

                        red_ser = min(max(int(_num_seguro(args.get("reduccion_series"), 1) or 1), 0), 3)

                        disponibles = _semanas_disponibles(numero_usuario)

                        if disponibles and sem in disponibles:

                            _guardar_descarga(numero_usuario, sem, red_int, red_ser)

                            guardar_dato_en_expediente(numero_usuario,
                                                       f"Semana {sem} marcada como DESCARGA el {fecha_actual}.")

                            texto_herramientas_agregado += (f"[SISTEMA]: Descarga aplicada a la semana {sem} "

                                                            f"(-{red_int:g}% intensidad, -{red_ser} series). Explícale que es una semana para recuperar.\n")

                        else:

                            texto_herramientas_agregado += f"[SISTEMA - ERROR]: semana {sem} no existe.\n"

                        necesita_reinvocacion = True


                    elif nombre == "consultar_progreso":

                        metrica = str(args.get("metrica", "1rm")).lower().strip()

                        if metrica == "peso_corporal":
                            resumen = _resumen_progreso_peso(numero_usuario)

                        else:
                            resumen = _resumen_progreso_rm(numero_usuario, args.get("ejercicio"))

                        if resumen:
                            texto_herramientas_agregado += f"[SISTEMA - PROGRESO]:\n{resumen}\nTranscríbeselo celebrando lo positivo.\n"

                        else:
                            texto_herramientas_agregado += "[SISTEMA - PROGRESO]: aún no hay suficientes registros para mostrar evolución.\n"

                        necesita_reinvocacion = True


                    elif nombre == "explicar_tecnica_ejercicio":

                        ejercicio = str(args.get("ejercicio", "")).strip()

                        if not ejercicio:
                            texto_herramientas_agregado += "[SISTEMA - ERROR]: no se indicó ejercicio.\n"

                        else:

                            resultado = consultar_literatura_cientifica(
                                f"cues técnicos, ejecución correcta y errores comunes del ejercicio {ejercicio}")

                            texto_herramientas_agregado += f"[BIBLIOTECA CIENTÍFICA — TÉCNICA {ejercicio}]:\n{resultado}\nResume en 2-3 puntos accionables.\n"

                        necesita_reinvocacion = True



                    elif nombre == "registrar_peso_corporal":

                        if peso_registrado_python:

                            texto_herramientas_agregado += ("[SISTEMA]: el peso ya quedó registrado; "

                                                            "solo confírmalo.\n")

                        else:

                            peso = _num_seguro(args.get("peso_kg"), None)

                            if peso is None or not (35 <= float(peso) <= 300):

                                texto_herramientas_agregado += ("[SISTEMA - ERROR]: peso fuera de "

                                                                "rango o ausente.\n")

                            else:

                                _registrar_peso_corporal(numero_usuario, float(peso))

                                actualizar_perfil(numero_usuario, peso_kg=round(float(peso), 1))

                                texto_herramientas_agregado += (f"[SISTEMA]: Peso {float(peso):g} kg "

                                                                "registrado.\n")

                        necesita_reinvocacion = True


                    elif nombre == "pausar_plan":

                        _actualizar_estado_json(numero_usuario, plan_pausado=1)

                        guardar_dato_en_expediente(numero_usuario, f"El atleta pausó su plan el {fecha_actual}.")

                        texto_herramientas_agregado += "[SISTEMA]: Plan PAUSADO. El calendario no avanzará hasta reactivarlo.\n"

                        necesita_reinvocacion = True


                    elif nombre == "reanudar_plan":

                        _actualizar_estado_json(numero_usuario, plan_pausado=0,
                                                fecha_programada=datetime.now().strftime("%Y-%m-%d"))

                        texto_herramientas_agregado += "[SISTEMA]: Plan REACTIVADO. Hoy vuelve a haber entrenamiento.\n"

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

                        if serie_registrada_python:

                            texto_herramientas_agregado += ("[SISTEMA]: esa serie ya la registró el "

                                                            "sistema; no la dupliques, solo confírmala.\n")

                        else:

                            if serie:
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
                    quien=f"post-tools-{loop_count+1}",
                    pensar=pensar_turno
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
                "que ya tienes en esta conversación. No llames herramientas. "
                "PROHIBIDO afirmar que algo se generó, se guardó o 'está en camino': si "
                "ibas a ejecutar una acción y no se completó, dilo con honestidad y ofrece "
                "reintentarla.")
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
                    # v5.2 — Reporte: la doble derrota entregaba la rutina alucinada
                    # al usuario. Ahora se BLOQUEA: mejor una respuesta honesta sin
                    # kilos que kilos inventados en su WhatsApp.
                    log(numero_usuario, "KG-INVENTADOS",
                        "el reintento también inventó; respuesta BLOQUEADA y sustituida por honestidad")
                    if rutina_vacia:
                        texto_final = ("Bro, freno aquí para no inventarte números 🙅. Tu plan "
                                       "todavía no está generado en mi sistema, así que no tengo "
                                       "cargas reales que darte. ¿Le doy ya para armarlo en serio? "
                                       "Dime *sí* y arrancamos.")
                    else:
                        texto_final = ("Bro, prefiero no aventarte cifras al aire. Déjame darte "
                                       "los números exactos: escríbeme *ver mi rutina* y te paso "
                                       "las cargas reales calculadas por mis métodos.")

        # ======================================================================
        # v6 — RED DE SEGURIDAD (F2): rutina fantasma.
        # Sin plan real o mientras se genera, el modelo NO puede listar ejercicios.
        # Se relee el estado de generación por si el pipeline se disparó este turno.
        # ======================================================================
        estado_gen_actual = obtener_estado_usuario(numero_usuario).get("estado_generacion", "")
        if _parece_rutina_inventada(texto_final) >= 3 and (rutina_vacia or estado_gen_actual == "generando"):
            log(numero_usuario, "RUTINA-FANTASMA",
                "salida con forma de rutina sin plan real; BLOQUEADA y sustituida")
            if estado_gen_actual == "generando":
                texto_final = ("¡Vamos! 🔥 Tu plan se está armando ahorita mismo. Dame 2-3 "
                               "minutos y te aviso en cuanto esté listo con tus cargas exactas.")
            else:
                texto_final = ("Ojo bro, todavía no tengo un plan generado para ti, así que no te "
                               "voy a inventar ejercicios ni pesos 🙅. ¿Le damos para armarte la "
                               "rutina en serio? Dime *sí* y arrancamos.")

        # ======================================================================
        # v6 — RED DE SEGURIDAD (F1): reparador de onboarding.
        # Si estamos en un turno de PREGUNTA de onboarding y el modelo se desvió
        # (preguntó 'experiencia/nivel' o repreguntó un dato ya guardado), se
        # reemplaza por una pregunta limpia del campo pendiente.
        # ======================================================================
        if onboarding_activo and bloque_onboarding and campo_pendiente:
            corregido, reparado = _reparar_onboarding(texto_final, numero_usuario, campo_pendiente)
            if reparado:
                log(numero_usuario, "ONB-REPARADO",
                    f"pregunta desviada/redundante -> se fuerza '{campo_pendiente}'")
                texto_final = limpiar_texto_para_whatsapp(corregido)

        # ======================================================================
        # v7 — ESCUDO ANTI "ACCIÓN ASUMIDA".
        # ======================================================================
        if (not accion_ejecutada and not onboarding_activo and not bloque_onboarding
                and _CLAIM_ACCION_RE.search(texto_final)):
            log(numero_usuario, "CLAIM-FALSO", "afirma una acción que no se ejecutó; se reescribe")
            honesto = await _forzar_respuesta_texto(
                list(historial_memoria[numero_usuario]),
                "Tu borrador afirma que hiciste una acción (cambiar, registrar, reactivar, pausar, "
                "aplicar, etc.) que en realidad NO se ejecutó en el sistema. Reescribe SIN afirmar "
                "que ya está hecho: dile con honestidad qué necesitas para hacerlo o pídele que te "
                "confirme el dato que falta, y ofrécele reintentarlo. Nada de dar por hecha la acción.")
            if honesto.strip():
                texto_final = limpiar_texto_para_whatsapp(honesto)

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

    overrides = _cargar_overrides(numero_usuario_limpio)
    sub_ejercicios = overrides.get("ejercicios", {})
    descarga = overrides.get("descargas", {}).get(str(semana_int))

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
        return ("ESTADO DE RUTINA: Vacío.\nEl atleta NO tiene plan. Si pide una rutina, "
                "ofrécele crear una; si solo charla, conversa normal SIN pedirle datos. "
                "PROHIBIDO dictarle una rutina tú mismo o improvisar ejercicios y cargas: "
                "el sistema es el ÚNICO que genera planes.")

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
            nombre_original = ej.get("Ejercicio", "?")
            nombre_ej = nombre_original
            ov = sub_ejercicios.get(_norm_ej(nombre_original))
            if not ov:
                nn = _norm_ej(nombre_original)
                for k, v in sub_ejercicios.items():  # v7 — match tolerante por subcadena
                    if k and (k in nn or nn in k):
                        ov = v
                        break
            m = PORCENTAJE_RE.search(str(ej.get("1RM", "")))
            es_analitico = any(t in tipo for t in ["monoarticular", "complementario", "analitico"])
            rm = None if es_analitico else _resolver_rm(nombre_original, rms)

            if not es_analitico:
                prom_ej = _clasificar_caracter_reps(ej.get("Reps"))
                if prom_ej is not None:
                    reps_compuestos.append(prom_ej)

            # v6 — DESCARGA
            series_val = ej.get("Series")
            if descarga:
                m_s = re.search(r"\d+", str(series_val if series_val is not None else ""))
                if m_s:
                    series_val = max(1, int(m_s.group(0)) - int(descarga.get("red_series", 0)))

            if m and rm:
                pct = float(m.group(1))
                kg = rm * pct / 100.0
                if descarga:
                    kg *= (1 - float(descarga.get("red_intensidad_pct", 0)) / 100.0)
                kg = _redondear_2_5(kg)
                nota_d = " [semana de descarga]" if descarga else ""
                carga = f"*{kg:g} kg* ({pct:g}% de su 1RM, ya calculado por el sistema){nota_d}"
            elif m:
                carga = ("(*peso por calibrar* — NO calcules ni inventes kg para este "
                         "ejercicio; dile al atleta que reporte su primera serie y el "
                         "sistema fijará la carga)")
            else:
                carga = "(*peso por calibrar*)"

            texto += (f"- {nombre_ej} ({series_val}x{ej.get('Reps')}) "
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
        "fecha_programada": nueva_fecha, "ultima_fecha_completada": hoy,
        # v5.3: el calendario avanzó -> el cursor de consulta se reancla al día
        # real en curso; "el siguiente" vuelve a significar "el que sigue a hoy".
        "cursor_semana": sig_sem, "cursor_dia": sig_dia,
    })
    estados[numero_usuario] = estado

    with open(ruta_estado, "w", encoding="utf-8") as f:
        json.dump(estados, f, indent=4, ensure_ascii=False)
    return True