import ollama
import contextvars
import hashlib
import json
import math
import os
import asyncio
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import aiofiles
from core.ejercicios import canonizar   # identidad del movimiento, una sola fuente
from services.handlers_v9 import (DISPATCHER_V9, fijar_ejercicio_confirmado,
                                  leer_ejercicio_confirmado)
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
# Firma en disco del historial de cada atleta cuando se cargó a memoria: permite
# detectar ediciones o borrados externos sin releer el JSON en cada turno.
_firmas_historial: dict[str, tuple | None] = {}
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

# v8 (Mandato 4) — NOTACIÓN DE ENTRENAMIENTO. "100 x 10 r @8" no contiene ninguna
# keyword del vocabulario, mide <60 chars y caía en casual=True: tools apagadas y
# el modelo afirmando "serie anotada ✅" sin haber guardado nada (bug 3.2). Este
# patrón detecta la notación estándar (NxM, kg/lbs, @RPE, reps, velocidades VBT)
# y fuerza ESTRICTAMENTE casual=False en el router.
NOTACION_ENTRENO_RE = re.compile(
    r"\d+\s*(?:kg|lbs?|kilos?)?\s*[xX×]\s*\d+"        # 100x10 / 100 kg x 10
    r"|@\s*\d+(?:[.,]\d+)?"                            # @8 / @ 8.5 (RPE)
    r"|\brpe\s*\d+"                                    # rpe 8
    r"|\d+\s*(?:kg|lbs?|kilos?)\b"                     # 100kg / 220 lbs
    r"|\d+\s*(?:reps?|repeticiones?)\b"                # 10 reps
    r"|\b0[.,]\d{2,3}\b",                              # 0.25 m/s (VBT)
    re.IGNORECASE)

# ==============================================================================
# ¿ESTE TURNO NECESITA HERRAMIENTAS? (v11)
# ------------------------------------------------------------------------------
# `es_casual` apaga CUATRO cosas a la vez: las 22 tools, el thinking, el RAG del
# expediente y la telemetría completa. Es una optimización real, pero decidía por
# ausencia: si el mensaje no contenía ninguna palabra de una lista de gimnasio y
# medía <=60 caracteres, se apagaba todo.
#
# Medido sobre 19 frases que un atleta dice de verdad, 9 se quedaban sin
# herramientas — entre ellas "tengo hernia discal", "me operaron del menisco",
# "tomo creatina" y "soy vegetariano". No es que el modelo no usara la tool: no
# la tenía. Y son justo los datos que `guardar_en_expediente_personal` existe
# para capturar.
#
# El vocabulario de gimnasio nunca va a cubrir todo lo que una persona cuenta de
# su cuerpo y su vida, así que se añaden dos capas más: el vocabulario del
# EXPEDIENTE (salud, nutrición, vida, entorno, metas) y una regla estructural —
# una declaración en primera persona sobre uno mismo es, por definición, un dato
# del atleta. La política ya escrita en este módulo es que pasarse de activar
# tools es barato y quedarse corto obliga al modelo a inventar.
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
    "reactiva", "reanuda", "reanudar", "vuelta",
    # v11 — medido en producción: "cual es mi nivel como usuario de pesas?" (39
    # chars) no matcheaba NADA ("pesas" no contiene "peso") y caía en casual, así
    # que el turno se quedaba sin herramientas y el modelo respondió de memoria en
    # vez de llamar a evaluar_nivel_fuerza. Al reformular el atleta, casual=False
    # y la llamó al instante: fallaba el clasificador, no el modelo.
    # Pasarse de activar tools es barato (desde la opción B ya no cuestan prefill);
    # quedarse corto obliga al modelo a inventar.
    "pesas", "nivel", "fuerte", "fuerza", "ffmi", "grasa", "corporal",
    "composicion", "composición", "avanzado", "intermedio", "principiante",
    "novato", "elite", "élite", "percentil", "estandar", "estándar",
    "grafica", "gráfica", "progreso", "compar", "evalua", "evalúa", "1rm",
}

# Lo que el atleta cuenta de SÍ MISMO y hay que poder guardar en su expediente.
# Son las cinco categorías que enumera la description de
# guardar_en_expediente_personal, que hasta v11 no tenían vocabulario propio.
PALABRAS_EXPEDIENTE = {
    # salud y biomecánica
    "hernia", "discal", "operaron", "operar", "operad", "cirug", "menisco",
    "tendinit", "tendon", "tendón", "esguince", "fractur", "artritis", "artros",
    "rehabilit", "fisio", "condromalacia", "lumbalgia", "ciatica", "ciática",
    "manguito", "bursitis", "luxa", "desgarr", "contractur", "pinza", "crujen",
    "recuper", "sano", "sana", "curad", "medic", "doctor", "traumatolog",
    # nutrición
    "vegetarian", "vegan", "creatina", "proteina", "proteína", "suplement",
    "ayuno", "ayunar", "dieta", "caloria", "caloría", "macros", "keto", "celiac",
    "intoleran", "alergi", "como bien", "comiendo", "superavit", "superávit",
    "volumen", "definicion", "definición", "cafe", "café", "cafeina", "cafeína",
    # estilo de vida y entorno
    "trabajo", "trabajar", "oficina", "turno", "guardia", "viajo", "insomnio",
    "ansiedad", "depres", "fumo", "fumar", "alcohol", "cerveza", "hijo", "bebe",
    "bebé", "embaraz", "mudo", "mude", "mudé", "horario", "madrugo", "madrugar",
    "sentad", "de pie", "camino", "bici", "correr", "futbol", "fútbol", "nado",
    # metas y contexto
    "competir", "competencia", "campeonato", "torneo", "boda", "playa", "verano",
    "meta", "objetivo", "powerlifting", "powerbuilding", "hipertrofia",
    # equipo y preferencias
    "equipo", "material", "rack", "jaula", "banco", "disco", "kettlebell",
    "elastic", "banda", "smith", "casa", "parque",
}

# Una declaración en primera persona sobre uno mismo es, por definición, un dato
# del atleta: "tengo hernia", "soy vegetariano", "me operaron", "tomo creatina".
# Cubre lo que ninguna lista de palabras va a cubrir nunca.
DECLARACION_PERSONAL_RE = re.compile(
    r"\b(?:tengo|tenia|tenía|soy|estoy|ando|me\s+operaron|me\s+lesion\w*|"
    r"me\s+duele|me\s+cuesta|tomo|tomaba|como|comia|comía|bebo|duermo|"
    r"trabajo|vivo|voy\s+a|quiero|necesito|no\s+puedo|no\s+tengo|"
    r"empec[ée]|empiezo|dej[ée]|deje|sufro|padezco|me\s+diagnostic\w*|"
    # Hitos que el atleta cuenta y cambian su entrenamiento: recuperarse de una
    # lesión, el alta médica, una boda o un viaje con fecha.
    r"me\s+cur[éeoa]\w*|me\s+recuper\w*|me\s+dieron|me\s+caso|me\s+voy\s+a)\b",
    re.IGNORECASE)

# Charla de verdad trivial: un conjunto PEQUEÑO y cerrado. Saludos, cortesía,
# risas y muletillas. Cualquier cosa fuera de aquí que además dispare una de las
# señales de arriba se queda con herramientas.
CHARLA_TRIVIAL_RE = re.compile(
    r"^\s*[¿¡]?\s*(?:"
    r"(?:h?ola|buen[oa]s?(?:\s+(?:d[ií]as|tardes|noches))?|hey|qu[eé]\s+tal|"
    r"c[oó]mo\s+(?:est[aá]s|va|vas)|qu[eé]\s+onda|qu[eé]\s+hay|saludos)"
    r"|(?:gracias|muchas\s+gracias|mil\s+gracias|de\s+nada|genial|perfecto|"
    r"excelente|buenisimo|buen[ií]simo|va(?:le)?|listo|entendido|enterado|"
    r"ok(?:ey|as)?|okis|sale|dale|claro|obvio|dsp|ah+|oh+|uf+|eso|exacto)"
    r"|(?:j[ae]j[ae]\w*|jsjs\w*|xd+|lol|:\)|:d|😂|🤣|👍|💪|🔥|😅)"
    r"|(?:adi[oó]s|hasta\s+(?:luego|mañana|pronto)|nos\s+vemos|chao|bye|"
    r"buenas\s+noches|me\s+voy)"
    r")[\s!.,¿?¡🙏👊💪😂🤣👍🔥😅]*$",
    re.IGNORECASE)


def _es_charla_casual(texto: str) -> bool:
    """¿Es charla trivial, sin nada que registrar ni consultar?

    True apaga tools, thinking, RAG y telemetría de ese turno, así que un falso
    positivo le cuesta al atleta un dato perdido y una respuesta inventada,
    mientras que un falso negativo solo cuesta unos segundos.
    """
    t = str(texto or "").strip().lower()
    if not t:
        return True
    # Va PRIMERO: un mensaje que es ÍNTEGRAMENTE cortesía no necesita nada, y
    # las listas de abajo lo sacarían de casual por accidente ("buenos días"
    # contiene "día", que es palabra de entrenamiento).
    if CHARLA_TRIVIAL_RE.match(t):
        return True
    if NOTACION_ENTRENO_RE.search(t):
        return False
    if any(p in t for p in PALABRAS_ENTRENAMIENTO):
        return False
    if any(p in t for p in PALABRAS_EXPEDIENTE):
        return False
    if DECLARACION_PERSONAL_RE.search(t):
        return False                    # habla de sí mismo: puede haber dato
    # Una pregunta pide información, y buena parte de las respuestas viven en la
    # biblioteca o en su expediente. Medido en el historial real: "desequilibrios??"
    # se quedaba sin herramientas y el modelo contestaba teoría de memoria. Los
    # saludos con signo ("¿qué tal?") ya salieron por CHARLA_TRIVIAL_RE.
    if "?" in t and len(t) > 8:
        return False
    return len(t) <= 60


# v13 — "sí", "dale", "hazlo" caían en charla casual, y casual APAGA las
# herramientas del turno. Es decir: el coach ofrecía ("¿te lo sustituyo?"), el
# atleta aceptaba... y justo en ese turno el sistema le quitaba la posibilidad de
# ejecutar nada. O no pasaba nada, o el modelo afirmaba haberlo hecho — el peor
# de los dos finales. Un asentimiento SOLO es trivial si nadie le ofreció nada.
_OFERTA_COACH_RE = re.compile(
    r"\?\s*$"                                            # terminó preguntando
    r"|¿(?:quieres|te\s+(?:lo|la|los|las)|prefieres|hago|busco|lo\s+hago|"
    r"te\s+parece|arranco|empezamos|seguimos|le\s+entramos)"
    r"|\bdime\s+(?:y|si|cu[áa]l|cu[áa]nto)\b"
    r"|\b(?:av[íi]same|conf[íi]rmame|me\s+dices)\b",
    re.IGNORECASE)


def _responde_a_una_oferta(numero_usuario: str, texto: str) -> bool:
    """¿Es un 'sí' que contesta a algo que el coach acaba de ofrecer?"""
    if not AFIRMACION_CORTA_RE.match(str(texto or "").strip()):
        return False
    for m in reversed(historial_memoria.get(numero_usuario) or []):
        if m.get("role") == "assistant":
            return bool(_OFERTA_COACH_RE.search(str(m.get("content") or "")[-400:]))
    return False


# v8 (Mandato 5) — CREACIÓN FUERTE: señal INEQUÍVOCA de que el atleta quiere un
# plan nuevo. Los verbos débiles de EN_CREACION_RE ("quiero...rutina") ya no
# bastan para amenazar con regenerar un plan activo: "¿qué peso uso para cumplir
# mi rutina?" disparaba el REGEN jail (bug 3.3). La señal débil ahora pasa por el
# clasificador semántico; solo esta señal fuerte confirma directo.
CREACION_FUERTE_RE = re.compile(
    r"\b(hazme|haz|crea\w*|arma\w*|genera\w*|dise[ñn]a\w*|reha[zc]\w*|reempl[aá]z\w*)\b"
    r".{0,50}\b(rutina|plan|programa|mesociclo|entrenamiento)\b"
    r"|\b(rutina|plan|programa|mesociclo|entrenamiento)\b.{0,40}\b(nuev[oa]|desde\s+cero)\b"
    r"|\bnuev[oa]\s+(rutina|plan|programa|mesociclo)\b"
    r"|\botr[oa]\s+(rutina|plan|programa|mesociclo)\b"
    r"|\bdesde\s+cero\b",
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

# ==============================================================================
# EQUIPAMIENTO: DISCO MÍNIMO DISPONIBLE
# ==============================================================================
# El plan calcula cargas en múltiplos de 2.5 kg (discos de 1.25 por lado). Si el
# atleta solo tiene discos de 2.5, una prescripción de 112.5 kg es INEJECUTABLE:
# necesita bajar a 110 o subir a 115. Sin registrar esto, el coach proponía el
# ajuste correcto y el validador anti-alucinación lo bloqueaba por "kg inventado".
INCREMENTO_KG_POR_DEFECTO = 2.5     # discos de 1.25 kg por lado

# Escalera canónica de discos en kg; evita leer "no llego a 100 kg" como disco.
# Se omiten medidas no estándar (1.0, 2.0): si el atleta dice que no tiene los de
# 1.25, el siguiente escalón real de su gimnasio es 2.5, no 2.
_DISCOS_PLAUSIBLES = (0.25, 0.5, 1.25, 2.5, 5.0, 10.0, 15.0, 20.0, 25.0)

# Exigimos mención explícita de disco/placa: sin esto, "levanto 20 kg como
# mínimo" se interpretaría como equipamiento y corrompería el perfil.
_MENCIONA_DISCO_RE = re.compile(r"\bdiscos?\b|\bplacas?\b|\bfraccional", re.IGNORECASE)
_NEGACION_DISCO_RE = re.compile(r"\bno\b|\bsin\b|\bcarezco\b|\bfaltan?\b", re.IGNORECASE)
_NUM_DISCO_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def _extraer_disco_minimo(texto: str) -> float | None:
    """Disco más pequeño que el atleta declara TENER. Devuelve el valor del disco
    (no el incremento de la barra): 'solo tengo de 2.5 kg' -> 2.5.

    Trabaja por cláusulas porque la frase natural mezcla lo que no tiene con lo
    que sí: 'no tengo discos de 1.25, solo de 2.5 kg como mínimo'. Un regex único
    devolvería 1.25 (lo negado) o el máximo de una lista ('tengo de 2.5, 5, 10 y
    20' -> 20), ambos incorrectos.
    """
    if not texto or not _MENCIONA_DISCO_RE.search(texto):
        return None

    disponibles, negados = [], []
    # El separador NO puede partir decimales: "2.5" debe seguir entero, si no
    # se leería como los discos "2" y "5" y el mínimo saldría mal.
    separador = r"(?<!\d)[,;.]|[,;.](?!\d)|\bpero\b|\bsolo\b|\bs[oó]lo\b|\b[uú]nicamente\b"
    for clausula in re.split(separador, texto):
        if not clausula.strip():
            continue
        negada = bool(_NEGACION_DISCO_RE.search(clausula))
        for bruto in _NUM_DISCO_RE.findall(clausula):
            try:
                v = float(bruto.replace(",", "."))
            except ValueError:
                continue
            if not any(abs(v - d) < 0.01 for d in _DISCOS_PLAUSIBLES):
                continue
            (negados if negada else disponibles).append(v)

    if disponibles:
        return min(disponibles)
    if negados:
        # Solo dijo lo que le falta: el mínimo real es el siguiente disco arriba.
        mayor_negado = max(negados)
        arriba = [d for d in _DISCOS_PLAUSIBLES if d > mayor_negado]
        return arriba[0] if arriba else None
    return None


def _incremento_barra(numero_usuario: str) -> float:
    """Salto mínimo REAL en la barra = 2 discos (uno por lado)."""
    try:
        valor = cargar_perfil(numero_usuario).get("incremento_kg")
        if valor and float(valor) > 0:
            return float(valor)
    except Exception as e:
        print(f"[EQUIPO] perfil ilegible para incremento: {e}")
    return INCREMENTO_KG_POR_DEFECTO


def _registrar_equipamiento(numero_usuario: str, texto: str) -> float | None:
    """Si el atleta declara su disco mínimo, lo persiste como incremento de barra.
    Devuelve el incremento guardado, o None si no declaró nada."""
    disco = _extraer_disco_minimo(texto)
    if disco is None:
        return None
    incremento = round(disco * 2, 2)          # un disco por lado
    actual = _incremento_barra(numero_usuario)
    if abs(actual - incremento) < 0.01:
        return incremento                      # ya estaba registrado
    actualizar_perfil(numero_usuario, incremento_kg=incremento)
    log(numero_usuario, "EQUIPO",
        f"disco mínimo {disco:g}kg -> incremento de barra {incremento:g}kg")
    return incremento


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
# v13 — Cubría tres formas rebuscadas ("amanecí en 84 kg", "peso corporal 84",
# "pesé 84 kg hoy") y se le escapaban las que usa cualquiera: "ahora peso 84
# kilos", "bajé a 82", "hoy me pesé 83.5", "mi peso es 84". El registro quedaba
# a merced de que el modelo se acordara de llamar la herramienta, que es justo
# lo que este interceptor existe para evitar.
PESO_CORPORAL_RE = re.compile(
    r"\b(?:amanec[íi]|estoy|ando)\s+(?:pesando\s+|en\s+|con\s+)?(\d{2,3}(?:[.,]\d)?)\s*(?:kg|kilos?)\b"
    r"|\bpeso\s+corporal\s*(?:de\s+|es\s+|:\s*)?(\d{2,3}(?:[.,]\d)?)\b"
    r"|\bmi\s+peso\s+(?:actual\s+)?(?:es|de|est[áa]\s+en)?\s*(\d{2,3}(?:[.,]\d)?)\b"
    r"|\b(?:me\s+)?pes[éo]\s*:?\s*(\d{2,3}(?:[.,]\d)?)\s*(?:kg|kilos?)?\b"
    r"|\b(?:sub[íi]|baj[ée]|qued[ée])\s+(?:a|en)\s+(\d{2,3}(?:[.,]\d)?)\s*(?:kg|kilos?)\b",
    re.IGNORECASE)
# Un número con kilos NO es peso corporal si la frase habla de levantar algo.
_PESO_ES_CARGA_RE = re.compile(
    r"\d\s*[x×]\s*\d|\bpor\s+\d+\s*(?:reps?|repeticiones?)?\b|\brpe\b|\bserie|"
    r"\b(?:banca|banco|sentadilla|squat|muerto|deadlift|dominad|remo|press|curl|jal[óo]n|"
    r"prensa|hip\s*thrust|zancad|militar)\b",
    re.IGNORECASE)


def detectar_peso_corporal(mensaje: str) -> float | None:
    """Los kilos que pesa EL ATLETA, no los que levanta. None si no los dijo."""
    texto = str(mensaje or "")
    if _PESO_ES_CARGA_RE.search(texto):
        return None                      # "peso muerto 180x2", "subí a 85 kg en banca"
    m = PESO_CORPORAL_RE.search(texto)
    if not m:
        return None
    valor = next((g for g in m.groups() if g), None)
    try:
        pc = float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return pc if 35 <= pc <= 300 else None

# v13 — "cámbiame el press militar por press con mancuernas" NO casaba: el
# patrón empezaba en `cambi`, y "cámbiame" lleva tilde en la a. Tampoco entraban
# "sustituye" ni "reemplaza". Justo las tres formas que usa un atleta con dolor
# de hombro, que es cuando esto importa.
SUSTITUIR_RE = re.compile(
    r"\b(?:c[aá]mbi\w*|sustitu\w*|reempla[cz]\w*|permut\w*)\s+"
    r"(?:el\s+|la\s+|los\s+|las\s+|mi\s+|mis\s+)?(.+?)\s+por\s+"
    r"(?:el\s+|la\s+|un[ao]?\s+|unos\s+|unas\s+)?(.+?)"
    r"(?:[.,;?!]|\s+(?:en\s+todo|porque|ya\s+que|me\s+|solo|s[oó]lo|por\s+hoy)|$)",
    re.IGNORECASE)
# v7 — el coach afirma en 1ª persona haber COMPLETADO una acción de backend
# v12 — el regex cubría MENOS de lo que parecía. Medido el 2026-07-29 contra
# salidas reales, se le escapaban tres formas enteras:
#   · la ortografía c→qu: "apliqué" no casa con 'aplic[ée]' (solo "aplicé"),
#   · los tiempos compuestos: "he pausado", "hemos cambiado",
#   · el participio de titular: "*Ajuste aplicado:*", que es como el modelo
#     presenta un cambio inexistente sin usar ningún verbo conjugado.
# Con esas tres puertas abiertas, el guardia anti-mentira no saltaba nunca en
# los casos que más daño hacen.
# OJO con el subjuntivo: en español "que te aplique" y "apliqué" solo se
# distinguen por la tilde, y el primero es una PROPUESTA ("¿quieres que lo
# apunte?"), no una afirmación. Por eso la terminación admitida es 'é'/'ó'
# acentuada y NO la 'e' pelada: incluirla convertía cada ofrecimiento del coach
# en un falso positivo y le hacía reescribir respuestas honestas.
_CLAIM_ACCION_RE = re.compile(
    r"\b(?:ya\s+)?(?:reactiv|reanud|paus|congel|cambi|sustitu|reempla[cz]|"
    r"guard|registr|anot|apunt|actualic|actualiz|aplic|apliqu|elimin|borr|gener|avanc|avanz)"
    r"(?:[éó]|amos|aste|aron|ad[oa]s?|id[oa]s?)\b"
    r"|\b(?:he|hemos)\s+\w{2,12}(?:ad|id)[oa]\b"
    r"|\bqued[óo]\s+(?:guardad|registrad|anotad|cambiad|pausad|reactivad|aplicad|eliminad)"
    r"|\b(?:lo\s+)?tengo\s+(?:anotad|apuntad|guardad|registrad)"
    # v13: el presente también es una afirmación de hecho para quien lo lee.
    # "Lo corrijo en tu expediente" / "la ignoro por completo" sonaban a acción
    # consumada y no lo eran.
    # OJO: solo verbos de rectificación, y nunca precedidos de "y" — "dime el
    # peso y lo guardo" es una OFERTA, no una acción consumada, y marcarla como
    # claim falso hacía reescribir respuestas correctas.
    r"|(?<!\by )\b(?:lo|la|le|los|las)\s+(?:corrijo|ignoro|borro|elimino|quito|descarto)\b",
    re.IGNORECASE)

# ------------------------------------------------------------------------------
# v12 — CLAIMS SOBRE EL ESTADO DEL PLAN, CONTRASTADOS CONTRA EL DISCO.
#
# El guardia CLAIM-FALSO de v8 exigía que el ATLETA hubiera escrito cifras en su
# mensaje (NOTACION_ENTRENO_RE sobre texto_puro), así que las mentiras que no
# van acompañadas de números pasaban enteras. Caso medido el 2026-07-29: ante
# "llevo toda la semana molido, ¿me toca descarga?" el coach llamó analizar_mrv,
# NO llamó aplicar_descarga, y respondió "*Ajuste aplicado:* reducción del 10 %
# en la carga, una serie menos". Nada de eso se aplicó — y el coach siguió
# repitiéndolo en los 5 turnos siguientes ("la descarga que aplicamos"), así que
# el atleta habría entrenado un mesociclo entero más flojo por una alucinación.
#
# Aquí NO se mira qué herramientas corrieron en el turno (una descarga aplicada
# ayer es una afirmación honesta hoy): se mira el ESTADO REAL en disco.
# ------------------------------------------------------------------------------
_CLAIM_DESCARGA_RE = re.compile(
    # "la descarga quedó aplicada", "descarga técnica aplicada"
    r"(?:descarga|deload)[^.\n]{0,40}\b(?:aplicad[oa]s?|activad[oa]s?|puesta|hecha|en\s+marcha)\b"
    # solo formas de acción CONSUMADA: apliqué / aplicó / aplicamos / puse / metí
    r"|\b(?:apliqu[éó]|aplic[óé]|aplicamos|activ[éó]|activamos|puse|met[íi])\s+"
    r"(?:una\s+|la\s+|tu\s+|esa\s+)?(?:descarga|deload)"
    r"|\b(?:la|esa|esta|tu)\s+descarga\s+que\s+(?:aplicamos|apliqu[éó]|hicimos|puse|met[íi])"
    r"|\bestamos\s+en\s+(?:una\s+)?\*?(?:descarga|deload)"
    r"|\bajuste\s+aplicado\b",
    re.IGNORECASE)
_CLAIM_PAUSA_RE = re.compile(
    r"\b(?:he|hemos)\s+(?:pausad|congelad|detenid|parad)[oa]\s+(?:tu|el)\s+(?:plan|calendario)"
    r"|\b(?:paus[éó]|congel[éó]|detuve|pausamos|congelamos)\s+(?:tu|el)\s+(?:plan|calendario)"
    r"|\btu\s+plan\s+(?:est[áa]|queda|sigue)\s+(?:en\s+)?(?:pausad|congelad|pausa)",
    re.IGNORECASE)
# Sin 'reanudamos'/'reactivamos': en español el presente y el pretérito de la 1ª
# del plural son idénticos, así que "cuando vuelvas reanudamos el plan" (una
# promesa) es indistinguible de "reanudamos el plan" (un hecho). Ante la duda se
# deja pasar: reescribir un mensaje honesto es peor que dejar escapar una forma
# rara, y las variantes inequívocas ya quedan cubiertas.
_CLAIM_REANUDA_RE = re.compile(
    r"\b(?:he|hemos)\s+(?:reactivad|reanudad)[oa]\s+(?:tu|el)\s+(?:plan|calendario)"
    r"|\b(?:reactiv[éó]|reanud[éó])\s+(?:tu|el)\s+(?:plan|calendario)",
    re.IGNORECASE)
_CLAIM_AVANCE_RE = re.compile(
    r"\b(?:he|hemos)\s+avanzad[oa]\s+(?:tu|el)\s+(?:calendario|plan|d[íi]a)"
    r"|\bavanc[éó]\s+(?:tu|el)\s+(?:calendario|plan|d[íi]a)"
    r"|\bcalendario\s+(?:avanzado|actualizado)\b"
    r"|\b(?:d[íi]a|sesi[óo]n)\s+(?:marcad[oa]\s+como\s+)?complet(?:ad[oa]|o)\b"
    r"|\bsesi[óo]n\s+cerrada\b",
    re.IGNORECASE)


def _claims_plan_desmentidos(numero_usuario: str, texto: str, semana: int,
                             fecha_prog_inicial: str = "") -> list[str]:
    """Afirmaciones sobre el estado del plan que el disco DESMIENTE.

    Devuelve una lista de desmentidos en lenguaje llano, lista para inyectarse en
    la instrucción de reescritura. Vacía = el texto es coherente con la realidad.
    """
    desmentidos: list[str] = []
    try:
        overrides = _cargar_overrides(numero_usuario)
        estado = obtener_estado_usuario(numero_usuario)
    except Exception:
        return desmentidos                     # nunca romper el turno por auditar

    if _CLAIM_DESCARGA_RE.search(texto):
        descargas = {str(k) for k in (overrides.get("descargas") or {})}
        # El coach puede estar hablando de OTRA semana ("descarga aplicada a la
        # semana 2"), así que valen la semana actual y cualquiera que nombre el
        # texto. Si no hay ninguna descarga en ninguna semana, la afirmación es
        # falsa sin margen de duda.
        candidatas = {str(int(semana))} | set(re.findall(r"semana\s+(\d+)", texto, re.I))
        if not (descargas & candidatas):
            desmentidos.append(
                "dice que hay una DESCARGA aplicada, pero no hay ninguna descarga "
                "registrada en su plan: sus series y sus kilos siguen intactos")

    if _CLAIM_PAUSA_RE.search(texto) and not estado.get("plan_pausado"):
        desmentidos.append("dice que el plan quedó pausado, pero sigue ACTIVO")

    if _CLAIM_REANUDA_RE.search(texto) and estado.get("plan_pausado"):
        desmentidos.append("dice que reactivó el plan, pero sigue PAUSADO")

    # El avance de día se comprueba comparando la fecha programada de ANTES del
    # turno con la de ahora: si nadie llamó a avanzar_dia, no se ha movido.
    if fecha_prog_inicial and _CLAIM_AVANCE_RE.search(texto) \
            and str(estado.get("fecha_programada", "")) == str(fecha_prog_inicial):
        desmentidos.append(
            "dice que dio por terminada la sesión y avanzó el calendario, pero el "
            f"calendario NO se movió: su próximo entreno sigue siendo el "
            f"{fecha_humana(fecha_prog_inicial)}")

    return desmentidos

# ==============================================================================
# CATÁLOGO V9: DICCIONARIO MAESTRO DE EJERCICIOS
# ==============================================================================
# IMPORTANTE:  El orden importa. Las variantes específicas (con mancuernas, en máquina)
# van primero para evitar falsos positivos con los ejercicios base.
_CATALOGO_EJERCICIOS = {
    # --- VARIANTES ESPECÍFICAS ---
    "Press Banco Plano con Mancuernas": ["banca con mancuernas", "plano con mancuernas", "pecho con mancuernas",
                                         "banca mancuernas"],
    "Press Inclinado con Mancuernas": ["inclinado con mancuernas", "inclinado mancuernas"],
    "Press Inclinado en Maquina": ["inclinado maquina", "inclinado en maquina", "inclinado máquina"],
    "Press Pecho en Maquina": ["pecho maquina", "pecho en maquina", "press maquina", "press en maquina"],
    "Dominadas Asistidas en Maquina": ["dominadas asistidas", "dominada asistida"],
    "Dominadas Lastradas": ["dominadas lastradas", "dominada lastrada", "pull up lastrado", "dominadas con lastre"],
    "Remo en Maquina": ["remo maquina", "remo en maquina", "remo sentado"],
    "Jalon en Maquina": ["jalon", "jalón", "lat pulldown", "jalones"],
    "Extension de Triceps Katana": ["katana", "triceps katana", "extensión katana"],
    "Curl de Biceps con Barra": ["curl con barra", "curl barra", "biceps barra"],
    "Curl de Biceps con Mancuernas": ["curl con mancuernas", "curl mancuernas", "biceps mancuernas"],
    "Curl de Biceps en Polea": ["curl polea", "curl en polea", "biceps polea"],
    "Curl Martillo": ["martillo", "curl martillo", "biceps martillo"],
    "Sentadilla Copa con Mancuerna": ["sentadilla copa", "copa mancuerna", "goblet"],
    "Sentadilla en Maquina Hack": ["hack", "sentadilla hack", "maquina hack"],
    "Sentadilla en Maquina Smith": ["sentadilla smith", "smith squat", "sentadilla en smith"],
    "Peso Muerto Rumano con Mancuernas": ["rumano con mancuernas", "rumano mancuernas", "rdl mancuernas"],
    "Peso Muerto con Mancuernas": ["peso muerto mancuernas", "peso muerto con mancuernas"],
    "Peso Muerto en Maquina Smith": ["peso muerto smith", "peso muerto en smith"],

    # --- EJERCICIOS BASE / GENERALES ---
    "Press Banco Plano": ["banca", "banco plano", "press plano", "bench press", "pecho plano"],
    "Press Inclinado": ["inclinado", "incline press", "press inclinado"],
    "Vuelos Laterales": ["vuelos", "laterales", "elevaciones laterales", "vuelo lateral"],
    "Crunch Abdominal": ["crunch", "abdomen", "abdominales"],
    "Sentadillas": ["sentadilla", "squat", "sentadillas"],
    "Peso Muerto Rumano": ["rumano", "rdl", "peso muerto rumano"],
    "Peso Muerto": ["peso muerto", "deadlift", "muerto convencional"],
    "Prensa de Piernas": ["prensa", "leg press"],
    "Extension de Cuadriceps": ["extension de cuadriceps", "extensiones", "leg extension", "cuadriceps en maquina"],
    "Curl Femoral": ["femoral", "curl femoral", "leg curl"],
    "Abductor de Piernas": ["abductor", "abductores"],
    "Aductor de Piernas": ["aductor", "aductores"],
    "Extension de Tobillo": ["pantorrilla", "gemelos", "gemelo", "tobillo", "elevacion de talones", "costureras"],
    "Dominadas": ["dominadas", "dominada", "pull up", "pull-up", "barra"]
}


# Señales de que la serie CONTINÚA la del turno anterior. Nadie repite el nombre
# del ejercicio en cada serie: en WhatsApp real la segunda es "otra igual",
# "listo la segunda, 100x4" o directamente "90x8". Sin arrastre, esas series se
# perdían y el coach preguntaba de qué ejercicio eran — medido el 2026-07-29:
# 'listo la segunda igual, 100 por 4' no se guardó (series_log sin id 38).
_SERIE_CONTINUACION_RE = re.compile(
    r"\b(?:otra|otr[oa]s|igual|mismo|misma|tambi[eé]n|ahora|luego|despu[eé]s|"
    r"segunda|tercera|cuarta|quinta|[uú]ltima|siguiente|sigo|van|llevo|"
    r"cerr[eé]|termin[eé]|acab[eé]|list[oa]|hecho|ya)\b",
    re.IGNORECASE)


# Una MARCA HISTÓRICA no es una serie de hoy. "mi PR de banca es 110x3" contaba
# como set ejecutado en este momento: entraba en series_log con la fecha de hoy
# y sin RPE, y desde ahí ensuciaba el dashboard de últimas series y el análisis
# de MRV (que compara ventanas temporales). La regla 2 del prompt ya dice que una
# marca va a la calculadora de 1RM, no al diario de entrenamiento.
_MARCA_HISTORICA_RE = re.compile(
    r"\bmi\s+(?:pr|r[eé]cord|marca|tope|m[aá]ximo|mejor)\b"
    r"|\b(?:pr|r[eé]cord)\s+(?:de|en)\b"
    r"|\bmi\s+1\s*rm\b"
    r"|\b(?:llegu[ée]|sub[íi]|hice)\s+(?:una\s+vez|alguna\s+vez)\b"
    r"|\b(?:el\s+)?(?:mes|a[ñn]o|verano)\s+pasado\b"
    r"|\bhace\s+\w+\s+(?:semanas?|meses|a[ñn]os)\b",
    re.IGNORECASE)


def _ejercicio_de_la_serie(numero_usuario: str, mensaje: str) -> tuple[str | None, bool]:
    """El ejercicio al que pertenece una serie reportada.

    Devuelve (ejercicio, heredado). `heredado=True` significa que el nombre NO
    estaba en el mensaje y se tomó del último ejercicio registrado (vigente 30
    min, guarda de handlers_v9). En ese caso el coach debe DECIR cuál asumió,
    para que el atleta pueda corregirlo antes de que la serie contamine su
    historial.
    """
    explicito = _inferir_ejercicio_serie(mensaje)
    if explicito:
        return explicito, False
    # v13 — El catálogo de sinónimos nunca va a estar completo: el atleta dice
    # "remo", "militar" o "jalón" a secas y el interceptor lo daba por
    # desconocido, así que la serie quedaba pendiente y el coach le preguntaba
    # de qué ejercicio hablaba... teniendo el nombre delante en la sesión de hoy.
    # Contrastarlo contra el plan resuelve la clase entera sin listas a mano.
    de_hoy = _ejercicio_por_sesion_de_hoy(numero_usuario, mensaje)
    if de_hoy:
        return de_hoy, False
    txt = str(mensaje).strip()
    # "90x8" pelado también es continuación: un mensaje corto que es SOLO la
    # notación de la serie no puede referirse a otra cosa.
    solo_notacion = len(txt) <= 30 and not re.search(r"[a-zA-ZáéíóúñÁÉÍÓÚÑ]{4,}", txt)
    if _SERIE_CONTINUACION_RE.search(txt) or solo_notacion:
        heredado = leer_ejercicio_confirmado(numero_usuario)
        if heredado:
            return heredado, True
    return None, False


_PALABRAS_VACIAS_EJ = {
    "de", "con", "en", "el", "la", "los", "las", "por", "para", "y", "a", "al",
    "hice", "acabo", "acabe", "acabé", "serie", "series", "reps", "kilos", "kg",
    "rpe", "que", "me", "mi", "un", "una", "del", "muy", "todo", "esa", "este",
    "maquina", "máquina", "barra", "mancuernas", "polea",
}


def _ejercicio_por_sesion_de_hoy(numero_usuario: str, mensaje: str) -> str | None:
    """Casa una palabra del mensaje con UN ejercicio de la sesión de hoy.

    Solo resuelve si la coincidencia es única: con "press" en un día que tiene
    press de banca y press de pecho en máquina se prefiere preguntar antes que
    apuntar la serie en el ejercicio equivocado.
    """
    try:
        estado = _leer_estados().get(str(numero_usuario), {}) or {}
        sesiones = _sesiones_semana_cache(str(numero_usuario), estado.get("semana"))
        indice = _normalizar_dia_a_indice(estado.get("dia"))
        if not sesiones or indice is None or indice >= len(sesiones):
            return None
        nombres = [str(e.get("Ejercicio") or e.get("Nombre") or e.get("nombre") or "")
                   for e in sesiones[indice].get("Ejercicios", [])]
    except Exception:
        return None

    palabras = {p for p in re.findall(r"[a-záéíóúñ]{4,}", str(mensaje).lower())
                if p not in _PALABRAS_VACIAS_EJ}
    if not palabras:
        return None
    candidatos = {n for n in nombres if n and any(p in _norm_ej(n) for p in palabras)}
    return candidatos.pop() if len(candidatos) == 1 else None


def _inferir_ejercicio_serie(mensaje: str) -> str | None:
    """v9: Infiere el ejercicio interceptado usando el catálogo maestro extendido."""
    m = str(mensaje).lower()

    # 1. Buscamos en tu catálogo oficial primero
    for nombre_canonico, palabras_clave in _CATALOGO_EJERCICIOS.items():
        if any(k in m for k in palabras_clave):
            return nombre_canonico

    # 2. Fallback al diccionario legacy (onboarding) por seguridad
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
    ruta_base = _carpeta_plan(numero_usuario)
    return any((ruta_base / a).exists() for a in _ARCHIVOS_PLAN)



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
# CACHÉS DE DISCO (v9.1) — el turno leía los mismos archivos una y otra vez
# ==============================================================================
# Medido sobre un turno normal: estado_usuarios.json se abría 4-8 veces, los
# JSON del plan hasta 40 (el peor caso es `_avanzar_cursor`, que llama a
# `_contar_sesiones_semana` dos veces por salto y admite 21 saltos) y
# overrides_{numero}.json una vez por lectura de rutina. Todo eso es json.load
# repetido sobre archivos que no cambiaron entre llamada y llamada.
#
# La clave de invalidación es la FIRMA del archivo (mtime_ns + tamaño): si algo
# externo lo modifica, la caché se descarta sola. `os.stat` cuesta microsegundos
# frente a los milisegundos de abrir y parsear.
_ARCHIVOS_PLAN = ("1_acumulamiento.json", "2_fuerza.json", "3_realizacion.json")

_cache_json: dict[str, tuple] = {}          # ruta -> (firma, datos)
_lock_cache_json = threading.Lock()


def _firma_archivo(ruta) -> tuple | None:
    """(mtime_ns, tamaño) del archivo, o None si no existe."""
    try:
        st = os.stat(ruta)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _leer_json_cacheado(ruta, defecto):
    """json.load con caché invalidada por firma de archivo."""
    clave = str(ruta)
    firma = _firma_archivo(ruta)
    if firma is None:
        with _lock_cache_json:
            _cache_json.pop(clave, None)
        return defecto

    entrada = _cache_json.get(clave)
    if entrada is not None and entrada[0] == firma:
        return entrada[1]

    try:
        with open(ruta, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[CACHE-JSON] no se pudo leer {clave}: {e}")
        return defecto

    with _lock_cache_json:
        _cache_json[clave] = (firma, datos)
    return datos


def _invalidar_json(ruta) -> None:
    """Fuerza la relectura de un archivo que ACABAMOS de escribir nosotros."""
    with _lock_cache_json:
        _cache_json.pop(str(ruta), None)


def _carpeta_plan(numero_usuario: str) -> Path:
    return (Path(ruta_raiz_proyecto) / "resources" / "rutinas_generadas"
            / str(numero_usuario).strip())


def _archivos_plan_cargados(numero_usuario: str) -> list[list]:
    """Contenido de los 3 mesociclos, EN ORDEN, saltando los que no existen.

    Devuelve listas de sesiones ya parseadas (o vacías). Todo el resto del
    módulo consulta el plan a través de aquí: una sola lectura por cambio real
    del archivo, en vez de una por pregunta.
    """
    base = _carpeta_plan(numero_usuario)
    salida = []
    for archivo in _ARCHIVOS_PLAN:
        datos = _leer_json_cacheado(base / archivo, None)
        salida.append(datos if isinstance(datos, list) else [])
    return salida


def _sesiones_semana_cache(numero_usuario: str, semana: int) -> list[dict]:
    """Sesiones de una semana concreta, con la MISMA regla de siempre: gana el
    primer mesociclo que tenga sesiones para esa semana."""
    try:
        objetivo = int(semana)
    except (TypeError, ValueError):
        return []
    for sesiones in _archivos_plan_cargados(numero_usuario):
        encontradas = []
        for s in sesiones:
            try:
                if int(s.get("Semana_Abs", 0)) == objetivo:
                    encontradas.append(s)
            except (TypeError, ValueError):
                continue
        if encontradas:
            return encontradas
    return []


# ------------------------------------------------------------------ estado
# estado_usuarios.json es diminuto pero se abre y se reescribe varias veces por
# turno. Se mantiene en memoria y se sincroniza al escribir; la firma del
# archivo detecta cualquier edición externa (scripts/reset_usuario.py, etc.).
_estados_cache: dict | None = None
_estados_firma: tuple | None = None
_lock_estados = threading.Lock()


# v13 — El fichero de estado es el único sitio donde vive el calendario de CADA
# atleta, y además está versionado en git mientras la app lo reescribe varias
# veces por turno: los conflictos de merge son inevitables. El 2026-07-29 quedó
# uno sin resolver y el resultado fue silencioso y total: json.load lanzaba
# JSONDecodeError, el except lo confundía con "el fichero no existe", _leer_estados
# devolvía {} y el siguiente _escribir_estados habría dejado en disco un único
# atleta, borrando a todos los demás para siempre. Nadie se habría enterado.
_CONFLICTO_RE = re.compile(
    r"^<<<<<<< [^\n]*\n(?P<a>.*?)\n=======\n(?P<b>.*?)\n>>>>>>> [^\n]*$",
    re.S | re.M)


def _fusionar_estados(a: dict, b: dict) -> dict:
    """Une dos versiones del mapa de estados. Por atleta gana la marca de
    generación más alta; a igualdad, el primer argumento."""
    fusion = dict(b)
    for num, datos in a.items():
        previo = b.get(num) or {}
        if float(datos.get("generacion_ts") or 0) >= float(previo.get("generacion_ts") or 0):
            fusion[num] = datos
    return fusion


def _rescatar_estados_json(crudo: str) -> dict | None:
    """Intenta salvar un fichero de estado ilegible. Devuelve None si no puede.

    Cubre el caso realista: marcadores de conflicto de git. Se parsean los DOS
    lados y se fusionan, que es exactamente lo que haría a mano quien resuelve
    el conflicto. Cualquier otra corrupción no se adivina.
    """
    if "<<<<<<<" not in crudo:
        return None
    try:
        def _sin_conflictos(quien: str) -> str:
            return _CONFLICTO_RE.sub(lambda m: m.group(quien), crudo)
        lado_a = json.loads(_sin_conflictos("a"))
        lado_b = json.loads(_sin_conflictos("b"))
    except (json.JSONDecodeError, ValueError):
        return None
    if not (isinstance(lado_a, dict) and isinstance(lado_b, dict)):
        return None
    return _fusionar_estados(lado_a, lado_b)


def _leer_estados() -> dict:
    """Todos los estados de usuario, cacheados por firma del archivo."""
    global _estados_cache, _estados_firma
    firma = _firma_archivo(ruta_estado)
    if _estados_cache is not None and _estados_firma == firma:
        return _estados_cache

    datos, degradado = {}, False
    try:
        crudo = Path(ruta_estado).read_text(encoding="utf-8")
    except FileNotFoundError:
        crudo = ""                      # primer arranque: vacío es correcto
    except OSError as e:
        print(f"[ESTADO] ⚠ no se pudo LEER {ruta_estado}: {e}. Se sirve la última "
              f"copia en memoria y NO se sobrescribirá el fichero.")
        return _estados_cache if _estados_cache is not None else {}

    if crudo.strip():
        try:
            datos = json.loads(crudo)
            if not isinstance(datos, dict):
                raise json.JSONDecodeError("la raíz no es un objeto", crudo, 0)
        except json.JSONDecodeError as e:
            rescatado = _rescatar_estados_json(crudo)
            if rescatado is not None:
                # Se reescribe ya resuelto: dejarlo con marcadores haría que el
                # siguiente arranque volviera a pasar por aquí.
                print(f"[ESTADO] ⚠ {ruta_estado} tenía marcadores de conflicto de git. "
                      f"Resueltos automáticamente: {len(rescatado)} atletas recuperados.")
                datos = rescatado
                try:
                    Path(ruta_estado).write_text(
                        json.dumps(datos, ensure_ascii=False, indent=4), encoding="utf-8")
                    firma = _firma_archivo(ruta_estado)
                except OSError:
                    pass
            else:
                degradado = True
                respaldo = f"{ruta_estado}.corrupto-{time.strftime('%Y%m%d-%H%M%S')}"
                try:
                    Path(respaldo).write_text(crudo, encoding="utf-8")
                except OSError:
                    respaldo = "(no se pudo respaldar)"
                print(f"[ESTADO] ⚠⚠ {ruta_estado} ES ILEGIBLE ({e}). Copia íntegra en "
                      f"{respaldo}. NO se sobrescribirá el original: repáralo a mano. "
                      f"Mientras tanto el bot trabaja con el estado que tenga en memoria.")

    if degradado:
        # Sin caché previa no hay nada que servir, pero el flag impide que una
        # escritura posterior convierta la corrupción en pérdida definitiva.
        globals()["_estados_bloqueado"] = True
        return _estados_cache if _estados_cache is not None else {}

    globals()["_estados_bloqueado"] = False
    with _lock_estados:
        _estados_cache, _estados_firma = datos, firma
    return datos


_estados_bloqueado = False


def _escribir_estados(estados: dict) -> None:
    """Persiste y deja la caché coherente con lo que acaba de quedar en disco."""
    global _estados_cache, _estados_firma
    if _estados_bloqueado:
        print("[ESTADO] ⚠ escritura OMITIDA: el fichero en disco está corrupto y "
              "sobrescribirlo perdería a los atletas que no se hayan podido leer.")
        return
    # Red de última hora: nunca cambiar un fichero con atletas por uno vacío.
    if not estados and _estados_cache:
        print("[ESTADO] ⚠ escritura OMITIDA: se intentó dejar el fichero vacío "
              f"habiendo {len(_estados_cache)} atletas en memoria.")
        return
    try:
        # Escritura atómica: un corte a mitad del json.dump dejaba el fichero
        # truncado, que es justo la corrupción que este bloque intenta evitar.
        tmp = f"{ruta_estado}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(estados, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ruta_estado)
    except OSError as e:
        print(f"[ESTADO] no se pudo guardar: {e}")
        return
    with _lock_estados:
        _estados_cache = estados
        _estados_firma = _firma_archivo(ruta_estado)

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
PENSAR_HABILITADO = True

# v8 (Mandato 6) — la síntesis POST-tools ya no piensa: con think=True el
# razonamiento tardaba 60s+ a baja velocidad y se comía casi todo num_predict
# (los tokens de thinking y de contenido comparten presupuesto en Ollama),
# dejando salidas mutiladas tipo "No te" con done_reason='length'. El criterio
# de QUÉ herramienta usar ya se ejerció en la llamada principal; sintetizar
# resultados es redacción pura. Interruptor para A/B.
PENSAR_POST_TOOLS = False

# v10 — El thinking NO se apaga en turnos donde el modelo DECIDE: medido sobre 8
# intenciones reales, think=True acierta 6/8 tool calls y think=False solo 3/8.
# Falla justo en las inferencias ("ya terminé mi sesión" -> avanzar_dia, "cambia
# el peso muerto por rumano" -> sustituir_ejercicio), que es para lo que se puso.
# think="low" quedó descartado: mismo 6/8 pero MÁS lento (63 s vs 49 s de media).
#
# Lo que sí sobra es pensar en los turnos donde Python YA ejecutó la acción y le
# inyectó el resultado al modelo ([SISTEMA — SERIE YA REGISTRADA], [SISTEMA —
# RUTINA SOLICITADA]...): ahí no hay nada que decidir, solo redactar una
# confirmación, y se pagaban ~1.950 tokens de razonamiento (~50 s) por nada.
# Interruptor para A/B: ponlo en True y vuelve al comportamiento anterior.
PENSAR_AUNQUE_PYTHON_RESUELVA = False

# v8 (Mandato 2) — herramientas que ESCRIBEN estado. Solo estas se deduplican
# cross-loop dentro del mismo turno (una lectura repetida es inofensiva; una
# escritura repetida mete filas DUP en SQLite y corrompe el volumen del atleta).
# Umbral de la regla 10 del prompt: por encima de esto, una serie que acaba de
# hacer obliga a reajustar lo que queda del día en vez de seguir como si nada.
RPE_ALARMA_AUTORREGULAR = 9.5

TOOLS_ESCRITURA = {
    "generar_rutina", "avanzar_dia", "sustituir_ejercicio", "aplicar_descarga",
    "registrar_peso_corporal", "pausar_plan", "reanudar_plan",
    "calcular_rm_exacto", "registrar_serie_completada",
    "guardar_en_expediente_personal", "actualizar_1rm",
}

# Sampling recomendado por Qwen para modo thinking (no-thinking usa tu config)
# v8 (Mandato 6): num_predict 2048 -> 3072. El razonamiento comparte presupuesto
# con el contenido; con 2048 un thinking largo agotaba los tokens y la respuesta
# real llegaba truncada al webhook (bug 3.1: [OUT] -> 'No te' tras 82s).
AJUSTES_PENSANDO = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "num_predict": 3072}

OPCIONES_QWEN_32B = {
    "num_gpu": 99,
    "temperature": 0.3,
    "top_p": 0.85,
    "top_k": 20,
    "repeat_penalty": 1.1,
    "presence_penalty": 0.0,   # v7 CRÍTICO: anula el 1.5 del Modelfile que rompía los tool calls
    "min_p": 0.0,              # v7: fija el sampler, no heredes nada
    # v10 — 32k en vez de 16k. Medido: el prompt de un turno normal llegaba a
    # 15.234 tokens (system 3.075 + tools 6.299 + historial + telemetría), así que
    # solo quedaban 1.150 para generar. Con think=True el razonamiento se los comía
    # enteros y la respuesta salía VACÍA (done_reason='length', contenido 0 chars):
    # esa era la causa REAL del bug 3.1, no el num_predict corto. Peor: Ollama
    # marcaba 'truncated = 1' y descartaba tokens del prompt con n_keep=4, o sea
    # que se comía trozos del system prompt en silencio.
    # Coste: el KV pasa de 1.024 a ~2.048 MiB (solo 16 de las 64 capas llevan KV,
    # el resto son recurrentes). Hay VRAM de sobra.
    # Efecto lateral valioso: la caché de prompts de Ollama está topada al tamaño
    # del contexto, así que su techo sube de 16.384 a 32.768 tokens y por fin caben
    # DOS prompts a la vez (antes cualquier llamada intercalada —el clasificador,
    # un _forzar_respuesta_texto— desalojaba al principal y el siguiente turno
    # pagaba un reprefill completo de ~16 s).
    # OJO: este valor debe seguir igual al de scripts/resources/
    # Entrenamiento_llm_peticion.py; si difieren, Ollama RECARGA el modelo cada vez
    # que se alternan y vuelve el thrashing de recargas.
    "num_ctx": 32768,
    "num_predict": 2048,
}

# v3 — ANTI-FREEZE: en 24 GB de VRAM el 32B + KV de 16k ocupa ~22-23 GB.
# Si dos peticiones al modelo se solapan (chat + compresor, o chat + generador),
# Ollama intenta atender ambas y el sistema entra en swap/thrashing -> PC congelada.
# Este semáforo garantiza UNA sola inferencia a la vez en todo el proceso.
SEM_LLM = asyncio.Semaphore(1)
TIMEOUT_CHAT = 180.0   # ninguna llamada al modelo puede colgar el orquestador para siempre

# ==============================================================================
# v13 — EXCLUSIÓN POR TURNO (multiusuario): NO INTERCALAR ATLETAS
# ==============================================================================
# SEM_LLM se tomaba y se soltaba en CADA llamada al modelo, y un turno hace
# varias (clasificador de intención, llamada principal, síntesis post-tools,
# algún _forzar_respuesta_texto). Con un solo atleta eso da igual. Con dos
# atletas hablando a la vez, sus llamadas se INTERCALAN, y ahí está el problema:
# la caché de prompts de Ollama está topada al tamaño del contexto (num_ctx
# 32768), o sea que caben ~dos prompts de ~15k tokens. Atleta A + atleta B +
# un clasificador ya no caben: cada uno desaloja el prefijo del otro y ambos
# pagan el reprefill COMPLETO de ~20.600 tokens (17-26 s por turno medidos el
# 2026-07-28). Es exactamente la regresión que costó resolver dejar de podar la
# telemetría; llegaría sola el día que haya dos atletas activos.
#
# La solución NO es más paralelismo (no hay VRAM: 18,6 GB de los 24 los ocupa el
# modelo). Es que el turno de un atleta sea ATÓMICO frente al modelo: sus 2-4
# inferencias van seguidas, sin que nadie se cuele en medio.
#
# Adquisición PEREZOSA a propósito: el semáforo se toma en la PRIMERA inferencia
# del turno, no al empezarlo. Así la fase de preparación (RAG, SQLite, lectura de
# disco, estado) de varios atletas SÍ se solapa —que es trabajo de I/O y conviene
# que corra en paralelo— y solo se serializa el modelo, que es el recurso escaso.
class _GuardiaTurno:
    """Recuerda si este turno ya tiene SEM_LLM tomado.

    Es un objeto mutable (no un bool en el ContextVar) porque `chat_llm` tiene
    que poder marcarlo desde dentro y que el context manager del turno lo vea al
    salir para liberar.
    """
    __slots__ = ("adquirido",)

    def __init__(self):
        self.adquirido = False


_GUARDIA_TURNO: contextvars.ContextVar["_GuardiaTurno | None"] = contextvars.ContextVar(
    "guardia_turno_llm", default=None)


@asynccontextmanager
async def turno_exclusivo_llm():
    """Envuelve UN turno de atleta: todas sus inferencias van sin intercalar.

    El `finally` libera siempre, también si el turno revienta a mitad: sin eso un
    error entre dos inferencias dejaría SEM_LLM tomado para siempre y el bot
    quedaría mudo para TODOS los atletas hasta reiniciar.
    """
    guardia = _GuardiaTurno()
    token = _GUARDIA_TURNO.set(guardia)
    try:
        yield
    finally:
        _GUARDIA_TURNO.reset(token)
        if guardia.adquirido:
            guardia.adquirido = False
            SEM_LLM.release()


def _soltar_guardia_heredado() -> None:
    """Desvincula la task actual del turno que la creó.

    Los ContextVar se HEREDAN al crear una task: `asyncio.create_task` dentro de
    un turno copia el contexto, así que la tarea hija vería el guardia del padre
    y creería tener el semáforo que en realidad es del turno. Peor: al ser un
    objeto compartido, marcarlo desde la hija provocaría un `release()` de más al
    cerrar el turno padre y el semáforo quedaría permanentemente abierto (dos
    inferencias simultáneas = el thrashing de VRAM que SEM_LLM existe para
    evitar). Toda tarea de fondo lanzada desde un turno debe llamar a esto.
    """
    _GUARDIA_TURNO.set(None)


@asynccontextmanager
async def _acceso_al_modelo(quien: str):
    """Serializa el acceso al modelo, con o sin turno alrededor.

    · Fuera de un turno (readiness, extractor de tríada del audio, generación de
      rutina en background): igual que siempre, toma y suelta por llamada.
    · Dentro de un turno: lo toma en la primera inferencia y NO lo suelta; lo
      libera `turno_exclusivo_llm` al terminar el turno completo.
    """
    guardia = _GUARDIA_TURNO.get()
    if guardia is None:
        async with SEM_LLM:
            yield
        return
    if not guardia.adquirido:
        await SEM_LLM.acquire()
        guardia.adquirido = True
    else:
        print(f"[LLM] '{quien}' reusa el turno del modelo ya tomado (sin intercalar)")
    yield

def _campo_resp(resp, campo, defecto=None):
    """Lee un campo del objeto/dict de respuesta de Ollama sin romperse por versión."""
    try:
        if isinstance(resp, dict):
            return resp.get(campo, defecto)
        return getattr(resp, campo, defecto)
    except Exception:
        return defecto

def _desglose_tiempos(resp) -> str:
    """Reparto REAL del tiempo del modelo: prefill (procesar el prompt) vs
    decode (generar la respuesta), con tokens/s de cada fase.

    Sin esto, un turno de 84 s es una caja negra y es imposible saber si el
    cuello de botella es el contexto que se le manda o los tokens que escribe.
    Ollama devuelve las duraciones en NANOsegundos.
    """
    try:
        p_tok = int(_campo_resp(resp, "prompt_eval_count", 0) or 0)
        p_ns = int(_campo_resp(resp, "prompt_eval_duration", 0) or 0)
        e_tok = int(_campo_resp(resp, "eval_count", 0) or 0)
        e_ns = int(_campo_resp(resp, "eval_duration", 0) or 0)
        total_ns = int(_campo_resp(resp, "total_duration", 0) or 0)
        carga_ns = int(_campo_resp(resp, "load_duration", 0) or 0)
        if not (p_ns or e_ns):
            return "sin métricas de Ollama"
        p_s, e_s = p_ns / 1e9, e_ns / 1e9
        partes = [
            f"prefill {p_tok} tok en {p_s:.1f}s ({p_tok / p_s:.0f} tok/s)" if p_ns else "",
            f"decode {e_tok} tok en {e_s:.1f}s ({e_tok / e_s:.1f} tok/s)" if e_ns else "",
        ]
        if carga_ns > 1e8:      # >0.1 s: el modelo tuvo que (re)cargarse
            partes.append(f"CARGA DE MODELO {carga_ns / 1e9:.1f}s")
        if total_ns:
            partes.append(f"total {total_ns / 1e9:.1f}s")
        return " | ".join(p for p in partes if p)
    except Exception:
        return "sin métricas de Ollama"


# DEPURACIÓN TEMPORAL (2026-07-28) — ¿por qué diverge el prefijo del prompt?
# Ollama reporta checkpoints inservibles por ~60 tokens
# ("checking checkpoint with [19847,19847] against 19787"), lo que obliga a
# reprocesar los ~20.600 tok enteros cada turno (~26 s). La hipótesis es que la
# poda de telemetría reescribe la cola del prompt entre turnos.
#
# Vuelca una huella por mensaje para poder comparar dos turnos consecutivos y
# ver EXACTAMENTE cuál cambió. Inerte salvo IRON_DEBUG_PROMPT=1.
# Comparar con: python3 services/testbed/latencia/diff_prompts.py
DEBUG_PROMPT = os.environ.get("IRON_DEBUG_PROMPT") == "1"
DIR_VOLCADO_PROMPT = "/tmp/iron_prompts"

def _volcar_huella_prompt(mensajes, quien):
    """Huella por mensaje (rol, longitud, sha1, colas) + cola global del prompt."""
    try:
        os.makedirs(DIR_VOLCADO_PROMPT, exist_ok=True)
        huellas, plano = [], []
        for i, m in enumerate(mensajes):
            # default=str: los mensajes con tool_calls traen objetos ToolCall
            # de la librería de Ollama, que json no sabe serializar.
            crudo = json.dumps(m, ensure_ascii=False, sort_keys=True, default=str)
            plano.append(crudo)
            contenido = str(m.get("content") or "")
            huellas.append({
                "i": i,
                "role": m.get("role", "?"),
                "chars": len(crudo),
                "sha1": hashlib.sha1(crudo.encode("utf-8")).hexdigest()[:12],
                "cabeza": contenido[:160],
                "cola": contenido[-160:],
            })
        entero = "\n".join(plano)
        destino = os.path.join(
            DIR_VOLCADO_PROMPT,
            f"{time.strftime('%H%M%S')}_{quien}.json")
        with open(destino, "w", encoding="utf-8") as fh:
            json.dump({
                "quien": quien,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "n_msgs": len(mensajes),
                "chars_total": len(entero),
                "sha1_total": hashlib.sha1(entero.encode("utf-8")).hexdigest()[:12],
                "cola_global": entero[-3000:],
                "mensajes": huellas,
            }, fh, ensure_ascii=False, indent=1)
        print(f"[PROMPT-DUMP] '{quien}' {len(mensajes)} msgs, "
              f"{len(entero)} chars -> {destino}")
    except Exception as e:                      # nunca romper el turno por depurar
        print(f"[PROMPT-DUMP] fallo al volcar: {e}")


async def chat_llm(mensajes, tools=None, opciones=None, quien="chat", pensar=False,
                   timeout=None):
    """Único punto de entrada al LLM: serializado, con timeout, log y thinking opcional.

    v8 (Mandato 6): loguea done_reason y, si el modelo se quedó SIN presupuesto de
    tokens a mitad del razonamiento (done_reason='length' + thinking + contenido
    diminuto, el patrón exacto del bug 3.1 "[OUT] -> 'No te'"), reintenta UNA vez
    en frío sin thinking DENTRO del mismo semáforo, para que ninguna otra petición
    se cuele entre intento y reintento.

    v13: el semáforo se pide a través de `_acceso_al_modelo`, que dentro de un
    turno lo retiene de punta a punta para no intercalar atletas (ver la nota de
    EXCLUSIÓN POR TURNO).
    """
    t0 = time.monotonic()
    async with _acceso_al_modelo(quien):
        espera = time.monotonic() - t0
        if espera > 1.0:
            print(f"[LLM] '{quien}' esperó {espera:.1f}s el turno del modelo")
        usar_thinking = bool(pensar and PENSAR_HABILITADO)
        if DEBUG_PROMPT:
            _volcar_huella_prompt(mensajes, quien)
        kwargs = dict(model=MODELO_LLM,
                      messages=mensajes,
                      keep_alive=-1,
                      think=usar_thinking,
                      options=opciones or OPCIONES_QWEN_32B)
        if tools:
            kwargs["tools"] = tools
        resp = await asyncio.wait_for(cliente_ollama.chat(**kwargs),
                                      timeout=timeout or TIMEOUT_CHAT)
        msg = resp.get("message", {}) or {}
        razonamiento = msg.get("thinking") or ""
        contenido = msg.get("content") or ""
        done_reason = _campo_resp(resp, "done_reason", "")
        print(f"[LLM] '{quien}' respondió en {time.monotonic()-t0:.1f}s "
              f"({len(mensajes)} msgs, done_reason={done_reason or '?'}, "
              f"thinking={'sí, ' + str(len(razonamiento)) + ' chars' if razonamiento else 'no'})")
        print(f"[LLM-PERF] '{quien}' {_desglose_tiempos(resp)}")
        if razonamiento:
            # Oro para depurar el criterio: SE VE por qué eligió (o no) una herramienta.
            print(f"[LLM-THINK] '{quien}': {_trunc(razonamiento, 300)}")

        # v8 (Mandato 6) — AUTO-RESCATE DE TRUNCAMIENTO: el thinking se comió el
        # num_predict y el contenido real quedó mutilado. Sin tool calls que
        # procesar, reintenta sin thinking con presupuesto íntegro para contenido.
        #
        # v13 — el reintento ya NO va A CIEGAS. Medido (bench_techo_thinking, 48
        # llamadas): relanzar sin thinking y sin contexto recupera la tool call
        # 1 vez de 3; reinyectando el razonamiento que YA se hizo, 2 de 3. Tiene
        # sentido: se habían gastado ~3.000 tokens analizando el caso y se tiraban
        # enteros, así que el modelo volvía a decidir sin nada. Es lo que producía
        # el patrón del log del 2026-07-28: 121,7 s, contenido=0, ninguna tool
        # call, y el coach inventándose un "error técnico" que nunca hubo.
        if (str(done_reason) == "length" and usar_thinking
                and not msg.get("tool_calls") and len(contenido.strip()) < 60):
            print(f"[LLM] '{quien}' TRUNCADO por presupuesto (contenido={len(contenido)} chars); "
                  f"reintento sin thinking, reusando {len(razonamiento)} chars de razonamiento")
            kwargs["think"] = False
            kwargs["options"] = {**(opciones or OPCIONES_QWEN_32B), "num_predict": 1536}
            if razonamiento.strip():
                # Mensaje aparte y NO persistido: igual que el resto de rescates
                # (_forzar_respuesta_texto), esta llamada es excepcional y su
                # prompt no tiene que ser prefijo del turno siguiente.
                kwargs["messages"] = list(mensajes) + [{
                    "role": "user",
                    "content": ("[RAZONAMIENTO YA HECHO — no lo repitas, concluye desde aquí]:\n"
                                + razonamiento.strip()
                                + "\n\n[SISTEMA]: se agotó el presupuesto de análisis. DECIDE YA: "
                                  "emite la herramienta que corresponda o responde en corto. "
                                  "No sigas razonando.")}]
            resp = await asyncio.wait_for(cliente_ollama.chat(**kwargs),
                                          timeout=timeout or TIMEOUT_CHAT)
            print(f"[LLM] '{quien}' reintento sin thinking listo en {time.monotonic()-t0:.1f}s "
                  f"(done_reason={_campo_resp(resp, 'done_reason', '?')}, "
                  f"tools={bool((resp.get('message') or {}).get('tool_calls'))})")
        return resp

# ==============================================================================
# v8 (Mandato 5) — CLASIFICADOR SEMÁNTICO DE INTENCIÓN DE PLAN
# Reemplaza el keyword-matching ciego: "¿qué peso uso para cumplir mi rutina?"
# contenía el token 'rutina' + un verbo débil y secuestraba la conversación
# hacia el bucle sí/no de regeneración (bug 3.3). La regex fuerte
# (CREACION_FUERTE_RE) sigue confirmando directo sin costo; SOLO la señal
# débil ambigua paga esta llamada mínima (una palabra, temp 0, sin thinking).
# ==============================================================================
async def clasificar_intencion_plan(mensaje: str, numero_usuario: str = "") -> str:
    """Devuelve 'crear', 'consultar' u 'otro'. Ante cualquier fallo -> 'otro'
    (fail-safe: jamás se regenera un plan por un error del clasificador)."""
    prompt = (
        "Clasifica la intención del mensaje de un atleta hacia su plan de gimnasio.\n"
        "Responde EXCLUSIVAMENTE con una de estas palabras, sin nada más:\n"
        "crear     -> quiere un plan/rutina NUEVO o rehacer el actual desde cero\n"
        "consultar -> pregunta por su rutina existente: qué le toca, qué peso usar, "
        "cómo cumplirla, dudas sobre ella\n"
        "otro      -> cualquier otra cosa (charla, reporte de series, molestias...)\n\n"
        f"Mensaje: \"{str(mensaje).strip()[:300]}\"\n"
        "Respuesta:")
    try:
        resp = await chat_llm(
            [{"role": "user", "content": prompt}],
            opciones={**OPCIONES_QWEN_32B, "temperature": 0.0, "num_predict": 8},
            pensar=False,
            quien="clasificador-plan",
            timeout=20.0,
        )
        palabra = (resp.get("message", {}) or {}).get("content", "").strip().lower()
        palabra = re.sub(r"[^a-záéíóú]", "", palabra.split()[0]) if palabra.split() else ""
        veredicto = palabra if palabra in ("crear", "consultar", "otro") else "otro"
        if numero_usuario:
            log(numero_usuario, "CLASIF-PLAN", f"{mensaje[:60]!r} -> {veredicto}")
        return veredicto
    except Exception as e:
        if numero_usuario:
            log(numero_usuario, "CLASIF-PLAN", f"falló ({e}); fail-safe -> 'otro'")
        return "otro"

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

# ==============================================================================
# ¿ESE EJERCICIO EXISTE EN SU PLAN? (v11)
# ------------------------------------------------------------------------------
# `sustituir_ejercicio` aceptaba cualquier cadena. Medido en una conversación
# real: el atleta pidió cambiar el "press militar", que NO está en su plan, y el
# sistema escribió el override igualmente. El coach le dijo "he sustituido el
# Press Militar por Face Pulls en todo tu plan", el atleta se quedó tranquilo...
# y su plan siguió exactamente igual, porque no hay ningún press militar que
# sustituir. Una acción que no cambia nada, confirmada como hecha.
def _ejercicios_del_plan(numero_usuario: str) -> list[str]:
    """Nombres reales de los ejercicios del plan, tal y como están escritos.
    Incluye los reemplazos ya vigentes: si ayer cambió el press militar por face
    pulls, hoy 'face pulls' es un ejercicio suyo tan válido como los demás."""
    vistos: dict[str, str] = {}
    for sesiones in _archivos_plan_cargados(numero_usuario):
        for s in sesiones:
            for ej in (s.get("Ejercicios") or []):
                nombre = str(ej.get("Ejercicio") or ej.get("Nombre")
                             or ej.get("nombre") or "").strip()
                if nombre:
                    vistos.setdefault(_norm_ej(nombre), nombre)
    for datos in (_cargar_overrides(numero_usuario).get("ejercicios") or {}).values():
        nuevo = datos.get("nuevo") if isinstance(datos, dict) else datos
        if nuevo:
            vistos.setdefault(_norm_ej(nuevo), str(nuevo))
    return list(vistos.values())


def _resolver_ejercicio_del_plan(numero_usuario: str, nombre: str) -> str | None:
    """Traduce como lo dice el atleta ('el militar') al nombre EXACTO del plan
    ('Press Militar con Barra'), o None si ese movimiento no está en su plan.

    Devolver el nombre exacto no es cosmético: el override se guarda con esa
    clave y `obtener_rutina_del_json` la compara con `_norm_ej`, así que un
    nombre aproximado se guardaría sin llegar a aplicarse nunca."""
    buscado = _norm_ej(re.sub(r"^\s*(?:el|la|los|las|mi|mis)\s+", "", str(nombre or ""),
                              flags=re.IGNORECASE))
    if not buscado:
        return None
    catalogo = _ejercicios_del_plan(numero_usuario)
    sin_plural = lambda t: t[:-1] if t.endswith("s") else t   # noqa: E731

    for real in catalogo:                                   # exacto
        if _norm_ej(real) == buscado:
            return real
    for real in catalogo:                                   # 'sentadilla' -> 'Sentadillas'
        if sin_plural(_norm_ej(real)) == sin_plural(buscado):
            return real

    # De aquí en adelante puede haber EMPATE, y elegir a ciegas es peor que
    # preguntar: en un plan con 'Curl de Biceps' y 'Curl Femoral', resolver "el
    # curl" al primero cambiaría un ejercicio que el atleta no pidió tocar.
    canon = canonizar(buscado)
    if canon:
        iguales = [r for r in catalogo if canonizar(r) == canon]
        if len(iguales) == 1:
            return iguales[0]
        if len(iguales) > 1:
            return None
    if len(buscado) >= 4:                                   # 'militar' en 'Press Militar'
        parciales = [r for r in catalogo
                     if buscado in _norm_ej(r) or _norm_ej(r) in buscado]
        if len(parciales) == 1:
            return parciales[0]
    return None


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

def _append_historial_rm(numero_usuario: str, ejercicio: str, rm, peso_origen=None) -> None:
    # v8 (Mandato 1): se persiste TAMBIÉN el peso de origen de la marca (ej. el
    # lastre de 30 kg en dominadas). Antes solo se guardaba el e1RM calculado y
    # el validador de kilajes bloqueaba el peso real que el atleta sí levantó.
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
    entrada = {"fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
               "ejercicio": _norm_ej(ejercicio), "rm": round(rm, 1)}
    try:
        if peso_origen is not None:
            entrada["peso_origen"] = round(float(peso_origen), 1)
    except (TypeError, ValueError):
        pass
    hist.append(entrada)
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
    ruta_base = _carpeta_plan(numero_usuario)
    borrados = 0
    for a in _ARCHIVOS_PLAN:
        f = ruta_base / a
        if f.exists():
            try:
                f.unlink(); borrados += 1
            except OSError as e:
                print(f"[BORRAR] no se pudo borrar {a}: {e}")
        # El archivo ya no existe: la caché de plan se invalida sola por firma,
        # pero se descarta aquí para que la MISMA petición no vea datos zombis.
        _invalidar_json(f)
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
    # Hook del log propio (v8: con peso_origen para la whitelist de kilajes)
    try: _append_historial_rm(numero_usuario, ejercicio, rm, peso_origen=peso_origen)
    except Exception as e: print(f"[HIST-RM] {e}")

def cargar_perfil(u) -> dict:
    return repositorio.obtener_perfil(u)

def actualizar_perfil(u, **campos):
    try:
        repositorio.actualizar_perfil(u, **campos)
    except Exception as e:
        print(f"[REPO-ERROR] actualizar_perfil: {e}")

def _actualizar_estado_json(numero_usuario: str, **campos):
    estados = dict(_leer_estados())
    est = dict(estados.get(numero_usuario) or {
        "semana": 1, "dia": 1,
        "fecha_programada": datetime.now().strftime("%Y-%m-%d"),
        "ultima_fecha_completada": ""
    })
    est.update(campos)
    estados[numero_usuario] = est
    _escribir_estados(estados)

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

# ==============================================================================
# CLASIFICACIÓN DE META (v11)
# ------------------------------------------------------------------------------
# Había DOS listas de palabras distintas para lo mismo (una en _parsear_campo y
# otra en _parsear_campos_anclados), ambas encadenadas con elif. Consecuencia
# medida: "quiero ganar músculo y fuerza" hacía match con 'músculo' primero, el
# elif cortaba la evaluación y la meta quedaba 'hipertrofia' — cuando músculo +
# fuerza es literalmente la definición de powerbuilding. El mesociclo entero se
# generaba con la meta equivocada.
#
# Aquí la clasificación es una sola, y la coexistencia de señales manda sobre el
# orden en que aparezcan las palabras.
_META_HIPERTROFIA_RE = re.compile(
    r"hipertrofia|\bmasa\b|volumen|m[uú]sculo|musculatura|mamado|crecer|"
    r"\bgrande\b|\bbajar\b|definir|flaco")
_META_FUERZA_RE = re.compile(
    r"powerlifting|\bfuerza\b|\bfuerte\b|\bpesado\b|levantar\s+m[aá]s")
_META_POWERBUILDING_RE = re.compile(
    r"powerbuild\w*|power\s*building|fuerte\s+y\s+grande|grande\s+y\s+fuerte|"
    r"las\s+dos|ambas|los\s+dos|todo\s+junto")

# Para CORREGIR una meta ya guardada exigimos una señal inequívoca: o nombra la
# metodología, o expresa intención explícita. Sin esto, un "hoy me siento fuerte"
# en mitad de la conversación le cambiaría el plan al atleta.
_META_NOMBRADA_RE = re.compile(r"powerbuild\w*|power\s*building|powerlifting|hipertrofia")
_META_INTENCION_RE = re.compile(
    r"\b(?:quiero|mi\s+meta|mi\s+objetivo|busco|me\s+interesa|prefiero|"
    r"me\s+gustar[ií]a|enf[oó]ca\w*|en\s+realidad|mejor)\b")


def _clasificar_meta(texto: str) -> str | None:
    """Devuelve 'powerbuilding' | 'hipertrofia' | 'powerlifting', o None.
    Si el atleta pide músculo Y fuerza, eso es powerbuilding: la coexistencia
    gana sobre cuál de las dos palabras aparezca antes en la frase."""
    t = str(texto or "").lower()
    if _META_POWERBUILDING_RE.search(t):
        return "powerbuilding"
    musculo = bool(_META_HIPERTROFIA_RE.search(t))
    fuerza = bool(_META_FUERZA_RE.search(t))
    if musculo and fuerza:
        return "powerbuilding"
    if musculo:
        return "hipertrofia"
    if fuerza:
        return "powerlifting"
    return None


def _corregir_meta_declarada(numero_usuario: str, mensaje: str) -> str | None:
    """El atleta corrige su meta cuando el sistema le está pidiendo OTRA cosa.

    Pasó tal cual: dijo 'powerbuilding supongo' mientras la máquina pedía su
    marca de banca; el mensaje se descartó por 'no responde al campo pendiente',
    la meta mal clasificada siguió en pie y el plan se generó con ella — mientras
    el coach le contestaba 'exacto, powerbuilding es el camino'.

    Devuelve el feedback para el coach si hubo corrección real, o None."""
    nueva = _clasificar_meta(mensaje)
    if not nueva:
        return None
    t = str(mensaje or "").lower()
    if not (_META_NOMBRADA_RE.search(t) or _META_INTENCION_RE.search(t)):
        return None                      # mención de paso, no una decisión
    actual = (cargar_perfil(numero_usuario) or {}).get("meta")
    if not actual or actual == nueva:
        return None
    actualizar_perfil(numero_usuario, meta=nueva)
    log(numero_usuario, "ONB-META", f"corregida '{actual}' -> '{nueva}' por declaración explícita")
    return f"meta corregida a {nueva} (antes estaba anotada como {actual})"


# ==============================================================================
# PARSER DE MARCAS (v11)
# ------------------------------------------------------------------------------
# La versión anterior entendía 'N x M' y 'N por M', y para todo lo demás cogía el
# PRIMER número y asumía 1 repetición. Medido en una conversación real:
#
#   "dominadas con 20 kilos de lastre saqué 6"  ->  (20, 1)  ->  1RM = 20 kg
#
# El 6 estaba ahí, en el mismo mensaje, y se descartaba en silencio devolviendo
# éxito. El atleta quedaba registrado ~4 kg por debajo de su fuerza real y todas
# sus cargas de dominadas salían bajas, sin un solo error en ningún log.
#
# Ahora cada número se ANCLA por su unidad ('20 kilos' = peso, '6 reps' = reps) y
# la heurística de magnitud solo actúa cuando no hay ninguna ancla. Regla nueva:
# si sobra un número que nadie supo interpretar, NO se inventa nada — se devuelve
# el motivo para que el coach pregunte.
_U_PESO_RE = re.compile(r"^\s*(?:kgs?|kilos?|kilogramos?)\b", re.IGNORECASE)
_U_REPS_RE = re.compile(r"^\s*(?:reps?\b|repeticiones?\b|repes?\b|veces\b)", re.IGNORECASE)
# "3 series de 10 reps": el 3 no es ni peso ni repeticiones, y confundirlo con las
# reps registra la marca a un tercio de las repeticiones reales.
_U_SERIES_RE = re.compile(r"^\s*(?:series?\b|sets?\b)", re.IGNORECASE)
# Verbo de ejecución justo ANTES del número: "saqué 6", "hice 5", "me salieron 8".
_VERBO_REPS_RE = re.compile(
    r"(?:saqu[eé]|saco|hice|hago|logr[eé]|met[ií]|complet[eé]|sal[ií]eron|salen|"
    r"aguant[eé]|pude|puedo|llegu[eé]\s+a)\s+(?:unas?\s+|como\s+|hasta\s+)?$",
    re.IGNORECASE)
# "al fallo" / "las máximas": dice CUÁNTAS sin decir un número. No se puede estimar.
_REPS_SIN_CIFRA_RE = re.compile(
    r"al\s+fallo|hasta\s+(?:el\s+)?fallo|las\s+m[aá]ximas?|a\s+tope", re.IGNORECASE)
# Palabra numérica solo cuando va pegada a una unidad de repetición ("dos veces"),
# para no convertir el "un" de "un press" en el número 1.
_PALABRA_NUM_CTX_RE = re.compile(
    r"\b(un[oa]?|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)\s+"
    r"(veces|reps?|repeticiones?|repes?)\b", re.IGNORECASE)
_PALABRA_A_NUM = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
                  "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
                  "diez": 10, "once": 11, "doce": 12}
# '1rm' / 'rm': el '1' es parte de la palabra, no una repetición.
_RUIDO_RM_RE = re.compile(r"\b1\s*rm\b|\brm\b", re.IGNORECASE)

_PESO_MIN_SIN_UNIDAD = 20.0   # una barra olímpica vacía: por debajo, sin 'kg'
                              # explícito, un número suelto casi nunca es el peso.


def _normalizar_fragmento_marca(fragmento: str) -> str:
    t = str(fragmento or "").lower().replace(",", ".")
    t = _PALABRA_NUM_CTX_RE.sub(lambda m: f"{_PALABRA_A_NUM[m.group(1)]} {m.group(2)}", t)
    return _RUIDO_RM_RE.sub(" ", t)


def _tokens_marca(texto: str) -> list[dict]:
    """Cada número del texto con su papel, deducido de lo que lo rodea."""
    tokens = []
    for m in re.finditer(r"\d+(?:\.\d+)?", texto):
        post = texto[m.end():m.end() + 16]
        pre = texto[max(0, m.start() - 22):m.start()]
        if _U_PESO_RE.match(post):
            papel = "peso"
        elif _U_REPS_RE.match(post):
            papel = "reps"          # anclado por su unidad: el más fiable
        elif _U_SERIES_RE.match(post):
            papel = "series"        # se descarta: no es ni peso ni repeticiones
        elif _VERBO_REPS_RE.search(pre):
            papel = "reps_verbo"    # "saqué 6": fiable, pero cede ante la unidad
        else:
            papel = None
        tokens.append({"val": float(m.group(0)), "papel": papel})
    return tokens


def _parsear_marca_dx(fragmento: str) -> tuple[tuple[float, int] | None, str]:
    """Devuelve ((peso, reps) | None, motivo). El motivo explica QUÉ falta cuando
    no se pudo interpretar, para que el coach pregunte eso y solo eso."""
    texto = _normalizar_fragmento_marca(fragmento)
    tokens = _tokens_marca(texto)
    if not tokens:
        return None, ""

    pesos = [t["val"] for t in tokens if t["papel"] == "peso"]
    # La unidad manda sobre el verbo: en "hice 3 series de 10 reps" el 3 lo marca
    # el verbo y el 10 su unidad — las repeticiones son 10.
    repes = ([int(t["val"]) for t in tokens if t["papel"] == "reps"]
             or [int(t["val"]) for t in tokens if t["papel"] == "reps_verbo"])
    libres = [t["val"] for t in tokens if t["papel"] is None]

    # Con el peso anclado no hay duda de cuál número es cuál, así que se admiten
    # reps altas y es el flujo de arriba quien las rechaza con su mensaje bueno
    # ("¿fueron 78 reps de verdad?"). Cuando hay que adivinar por magnitud, en
    # cambio, el rango se aprieta: ahí un número grande sí puede ser el peso.
    REPS_MAX_ANCLADO, REPS_MAX_INFERIDO = 100, 30

    def valida(p, r, rmax=REPS_MAX_ANCLADO):
        return (p, int(r)) if (1 <= p <= 500 and 1 <= r <= rmax) else None

    # 1. Ambos anclados por su unidad: no hay nada que adivinar.
    if pesos and repes:
        return (valida(pesos[0], repes[0]) or None,
                "" if valida(pesos[0], repes[0]) else
                f"{pesos[0]:g} kg x {repes[0]} está fuera de rango")

    # 2. Peso anclado ('20 kilos'): las reps son el otro número que haya.
    if pesos and not repes:
        candidatas = [v for v in libres if 1 <= v <= REPS_MAX_ANCLADO]
        if candidatas:
            return valida(pesos[0], candidatas[0]), ""
        if libres:
            return None, (f"dijo {pesos[0]:g} kg y otro número ({libres[0]:g}) que no "
                          f"parece repeticiones")
        if _REPS_SIN_CIFRA_RE.search(texto):
            return None, f"dijo {pesos[0]:g} kg pero no cuántas repeticiones"
        return valida(pesos[0], 1), ""          # solo el peso: se asume 1RM directo

    # 3. Reps ancladas ('6 reps'): el peso es el otro número.
    if repes and not pesos:
        if libres:
            return valida(libres[0], repes[0]), ""
        return None, f"dijo {repes[0]} repeticiones pero no con cuánto peso"

    # 4. Sin anclas: el patrón canónico 'N x M' / 'N por M' sigue mandando.
    m = _MARCA_KG_RE.search(texto)
    if m:
        return valida(float(m.group(1)), int(m.group(2))), ""
    serie = detectar_serie_en_mensaje(texto)
    if serie:
        return valida(serie["peso"], serie["reps"]), ""

    # 5. Dos números sueltos sin ninguna pista: el mayor es el peso, pero solo si
    #    de verdad parece un peso. '6 o 7' no es 7 kg a 6 reps.
    if len(libres) == 2:
        mayor, menor = max(libres), min(libres)
        if mayor >= _PESO_MIN_SIN_UNIDAD and menor <= REPS_MAX_INFERIDO:
            return valida(mayor, menor, REPS_MAX_INFERIDO), ""
        return None, f"no queda claro cuál de los dos números ({libres[0]:g}, {libres[1]:g}) es el peso"

    # 6. Un solo número: se asume su 1RM, salvo que haya dicho que fueron varias.
    if len(libres) == 1:
        if _REPS_SIN_CIFRA_RE.search(texto):
            return None, f"dijo {libres[0]:g} pero no cuántas repeticiones"
        if 5 <= libres[0] <= 450:
            return valida(libres[0], 1), ""
        return None, ""

    # 7. Tres o más números sueltos: antes se cogía el primero y a correr.
    if len(libres) > 2:
        return None, (f"vinieron varios números ({', '.join(f'{v:g}' for v in libres[:4])}) "
                      f"y no está claro cuál es el peso y cuál las repeticiones")
    return None, ""


def _parsear_marca(fragmento: str):
    """Devuelve (peso, reps) de un fragmento de texto, o None."""
    marca, _motivo = _parsear_marca_dx(fragmento)
    return marca


def _parcial_marca(fragmento: str) -> dict:
    """Lo que SÍ se entendió de una marca incompleta: {'peso': x} o {'reps': y}.
    Sirve para no perder la mitad del dato entre dos mensajes: "100 kilos al
    fallo" y luego "5 reps" son una sola marca repartida en dos turnos."""
    tokens = _tokens_marca(_normalizar_fragmento_marca(fragmento))
    pesos = [t["val"] for t in tokens if t["papel"] == "peso"]
    repes = [int(t["val"]) for t in tokens if t["papel"] in ("reps", "reps_verbo")]
    if pesos and not repes:
        return {"peso": pesos[0]}
    if repes and not pesos:
        return {"reps": repes[0]}
    return {}

def parsear_batch_rms(numero_usuario: str, mensaje: str,
                      solo_campos: set[str] | None = None) -> list[str]:
    """Extrae varias marcas de un solo mensaje ('150x3 en banca, 215 en sentadilla...').

    `solo_campos` limita QUÉ campos puede escribir (nombres tipo 'rm_banca'). Sin
    él, esta función ESCRIBE toda marca que encuentre, y como se la llama dos
    veces por turno de onboarding —una desde procesar_respuesta_onboarding:1699 y
    otra desde _parsear_campos_anclados— cada marca acababa duplicada en
    historial_rm_{numero}.json. Medido el 2026-07-29: banca, sentadilla y peso
    muerto aparecían dos veces con el mismo timestamp, lo que falsea el progreso
    y la gráfica de e1RM. Filtrar el RESULTADO no servía: la escritura ya había
    ocurrido.
    """
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
        if solo_campos is not None and campo_detectado not in solo_campos:
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
        meta = _clasificar_meta(msg)
        if meta:
            actualizar_perfil(numero_usuario, meta=meta)
            return True, ("meta powerlifting (fuerza)" if meta == "powerlifting"
                          else f"meta {meta}")

    elif campo in RM_CANONICO:
        # ORDEN CRÍTICO (v3): la marca real se parsea PRIMERO.
        # Tu red anterior con `|0|` hacía match con el dígito '0' de "150kg" o "30kg"
        # y guardaba barra vacía (20 kg) o lastre 0 en marcas perfectamente válidas.
        marca, motivo_marca = _parsear_marca_dx(msg)

        # v11 — MEMORIA DE MARCA A MEDIAS. Al dejar de inventar repeticiones, el
        # sistema empezó a preguntar lo que faltaba... y a olvidar lo que ya sabía:
        # "100 kilos al fallo" -> "¿cuántas reps?" -> "5 reps" -> "¿con cuánto
        # peso?", en bucle. Las dos mitades se acumulan hasta formar la marca.
        #
        # Va ANTES de aceptar lo que dio el parser: si ya teníamos las reps de un
        # turno anterior, "con 100 kilos" son 100 kg x 5, y NO un 1RM de 100 kg
        # (que es justo lo que devolvería el parser leyendo el mensaje aislado).
        previo = obtener_estado_usuario(numero_usuario).get("marca_parcial") or {}
        acumulado = ({k: v for k, v in previo.items() if k != "campo"}
                     if previo.get("campo") == campo else {})
        if acumulado:
            acumulado.update(_parcial_marca(msg))
            if "peso" in acumulado and "reps" in acumulado:
                marca, motivo_marca = (float(acumulado["peso"]), int(acumulado["reps"])), ""
                log(numero_usuario, "ONB-MARCA-UNIDA",
                    f"{campo}: {acumulado['peso']:g} kg x {acumulado['reps']} (en dos mensajes)")
        if not marca:
            pendiente = {**acumulado, **_parcial_marca(msg)}
            if pendiente:
                _actualizar_estado_json(numero_usuario,
                                        marca_parcial={"campo": campo, **pendiente})

        if marca:
            peso, reps = marca
            if reps > 15:
                log(numero_usuario, "ONB-RECHAZO", f"{campo}: {peso}kg x {reps} reps fuera de rango 1-15")
                return False, (f"Reportó {peso:g} kg x {int(reps)} reps: FUERA del rango confiable "
                               f"(1-15) para estimar un 1RM. NO se guardó. Pregúntale con "
                               f"naturalidad si la cifra es correcta (¿fueron {int(reps)} reps de verdad?).")
            rm = _calcular_y_guardar_rm(numero_usuario, RM_CANONICO[campo], peso, int(reps))
            if rm is not None:
                _actualizar_estado_json(numero_usuario, marca_parcial={})
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

        # v11 — El atleta SÍ dio cifras pero no se pudo saber cuál era cuál. Antes
        # esto caía al "no responde a lo pendiente" y el coach repetía la pregunta
        # entera como si no hubiera dicho nada; peor aún, el parser viejo se
        # inventaba 1 repetición y lo daba por bueno. Ahora se le pregunta
        # exactamente el dato que falta.
        if motivo_marca:
            log(numero_usuario, "ONB-AMBIGUO", f"{campo}: {motivo_marca}")
            # Plantilla neutra a propósito: los motivos son frases sueltas y
            # encajarlas tras "El atleta" producía textos agramaticales ("El
            # atleta no queda claro cuál…") que van directos al modelo.
            return False, (f"NO se pudo interpretar su marca de {RM_CANONICO[campo]}: "
                           f"{motivo_marca}. NO se guardó nada. Pregúntale con naturalidad, "
                           f"en una sola frase, el dato que falta (peso en kg y repeticiones).")

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

    # RMs por keyword de ejercicio, SOLO de los campos que siguen faltando. El
    # guardado NO es idempotente (guardar_rm hace append al historial), así que
    # el filtro tiene que viajar hacia dentro: si se deja escribir todo y se
    # filtra el resultado, la marca del campo que acaba de guardar
    # procesar_respuesta_onboarding se duplica.
    campos_rm_faltantes = {c for c in faltantes if c in RM_CANONICO}
    if campos_rm_faltantes:
        guardados.extend(parsear_batch_rms(numero_usuario, msg,
                                           solo_campos=campos_rm_faltantes))

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
        meta = _clasificar_meta(msg)
        if meta:
            actualizar_perfil(numero_usuario, meta=meta)
            guardados.append("meta powerlifting (fuerza)" if meta == "powerlifting"
                             else f"meta {meta}")

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
# asyncio solo guarda referencias DÉBILES a las tareas: sin este set, el recolector
# de basura puede llevarse la generación a medio camino y el atleta se queda con el
# estado en 'generando' hasta que salte el self-heal de 15 minutos.
_tareas_generacion: set[asyncio.Task] = set()


async def _generar_rutina_background(datos: dict, numero_usuario: str):
    t0 = time.monotonic()
    # v13 — Esta tarea nace DENTRO de un turno (`_handle_generar_rutina` la lanza
    # con create_task), así que hereda su guardia de SEM_LLM por ContextVar. Sin
    # desvincularla, cualquier inferencia que se añada aquí creería tener el
    # semáforo del turno padre y acabaría soltándolo dos veces: SEM_LLM quedaría
    # abierto y volverían las inferencias solapadas que congelan el equipo. El
    # `async with SEM_LLM` de abajo es el que de verdad debe protegerla.
    _soltar_guardia_heredado()
    log(numero_usuario, "GEN-BG", f"inicio con datos={datos}")
    try:
        # El generador dispara 4 inferencias a Ollama desde su propio hilo. Sin este
        # semáforo se solapaban con las del chat, y dos contextos simultáneos del
        # modelo de 17 GB son exactamente el thrashing de VRAM que congelaba el
        # equipo (ver la nota de SEM_LLM arriba). El precio es que los chats esperan
        # mientras dura la generación, que es justo lo que el atleta ya está esperando.
        async with SEM_LLM:
            await asyncio.to_thread(recibir_peticion, datos, numero_usuario)

        carpeta = _carpeta_plan(numero_usuario)
        archivos = list(carpeta.glob("*.json")) if carpeta.exists() else []
        log(numero_usuario, "GEN-BG", f"terminó en {time.monotonic()-t0:.1f}s, archivos={[a.name for a in archivos]}")
        if not archivos:
            raise RuntimeError("El generador no produjo archivos")

        # El plan ACABA de cambiar en disco: se descarta la caché para que el
        # primer mensaje tras la generación lea el plan nuevo aunque el mtime
        # caiga dentro de la misma marca de tiempo que la lectura anterior.
        for a in _ARCHIVOS_PLAN:
            _invalidar_json(carpeta / a)

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
            # enviar_mensaje_whatsapp es SÍNCRONA (requests) y devuelve None:
            # el 'await' anterior lanzaba "object NoneType can't be used in
            # 'await' expression". Va a un hilo para no bloquear el event loop.
            from api.rutas_whatsapp import enviar_mensaje_whatsapp
            await asyncio.to_thread(
                enviar_mensaje_whatsapp,
                numero_usuario,
                "🔥 ¡Listo bro! Tu nuevo plan ya está armado y guardado. Escríbeme *ver mi rutina* y arrancamos."
            )
        except Exception as e:
            print(f"[GENERACIÓN BG] Plan listo pero no pude notificar: {e}")


    # SystemExit NO hereda de Exception: Distribucion_Torso_Pierna lo lanza cuando
    # falta o está corrupta base_de_datos_ejercicios.json, y sin capturarlo el estado
    # se quedaba en 'generando' bloqueando cualquier plan nuevo hasta el self-heal.
    except (Exception, SystemExit) as e:

        import traceback

        print(f"[GENERACIÓN BG] ERROR: {e}")

        traceback.print_exc()

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

# v11 — El alias de arriba corregía el NOMBRE de la herramienta, pero nadie
# corregía el nombre de sus ARGUMENTOS, y ahí había una deriva silenciosa entre
# core/tools.json (lo que el modelo ve y rellena) y los handlers (lo que el
# código lee). El modelo mandaba 'dato_a_guardar', tal como pide el schema, y el
# handler buscaba 'dato': el expediente NUNCA se escribía por esa vía y el coach
# confirmaba un guardado fantasma. Lo mismo con 'nombre_ejercicio' y
# 'peso_levantado' en calcular_rm_exacto (el 1RM se atribuía a un ejercicio
# inferido por regex) y con 'reduccion_intensidad_pct' en aplicar_descarga.
#
# El CONTRATO es tools.json: a partir de aquí los handlers leen EXACTAMENTE el
# nombre declarado en el schema, y esta tabla es solo la red de seguridad para
# lo que el modelo deforme. Al añadir una tool, el nombre que lees en el handler
# debe ser literalmente el del schema; services/testbed/test_contrato_tools.py
# falla si vuelven a separarse.
_ALIAS_ARGS: dict[str, dict[str, str]] = {
    "calcular_rm_exacto": {
        "ejercicio": "nombre_ejercicio", "nombre": "nombre_ejercicio",
        "peso_kg": "peso_levantado", "peso": "peso_levantado", "kg": "peso_levantado",
        "carga": "peso_levantado", "reps": "repeticiones", "repes": "repeticiones",
        "tipo": "tipo_ejercicio",
    },
    "registrar_serie_completada": {
        "nombre_ejercicio": "ejercicio", "peso": "peso_kg",
        "peso_levantado": "peso_kg", "carga": "peso_kg", "reps": "repeticiones",
        "esfuerzo": "rpe", "dolor": "dolor_articular", "molestia": "dolor_articular",
    },
    "guardar_en_expediente_personal": {
        "dato": "dato_a_guardar", "texto": "dato_a_guardar",
        "informacion": "dato_a_guardar", "información": "dato_a_guardar",
        "nota": "dato_a_guardar", "contenido": "dato_a_guardar",
        "dato_personal": "dato_a_guardar",
    },
    "consultar_expediente_personal": {
        "concepto": "concepto_a_buscar", "tema": "concepto_a_buscar",
        "busqueda": "concepto_a_buscar", "búsqueda": "concepto_a_buscar",
        "consulta": "concepto_a_buscar", "query": "concepto_a_buscar",
        "dato_a_buscar": "concepto_a_buscar",
    },
    "consultar_historial_narrativo": {
        "contexto": "contexto_a_buscar", "concepto": "contexto_a_buscar",
        "concepto_a_buscar": "contexto_a_buscar", "tema": "contexto_a_buscar",
        "busqueda": "contexto_a_buscar", "consulta": "contexto_a_buscar",
        "query": "contexto_a_buscar",
    },
    "consultar_libro_cientifico": {
        "pregunta": "pregunta_teorica", "tema": "pregunta_teorica",
        "consulta": "pregunta_teorica", "query": "pregunta_teorica",
        "concepto": "pregunta_teorica", "pregunta_cientifica": "pregunta_teorica",
    },
    "aplicar_descarga": {
        "reduccion_intensidad": "reduccion_intensidad_pct",
        "red_intensidad": "reduccion_intensidad_pct",
        "reduccion_intensidad_porcentaje": "reduccion_intensidad_pct",
        "intensidad": "reduccion_intensidad_pct",
        "red_series": "reduccion_series", "series": "reduccion_series",
    },
    "sustituir_ejercicio": {
        "original": "ejercicio_original", "actual": "ejercicio_original",
        "viejo": "ejercicio_original", "nuevo": "ejercicio_nuevo",
        "reemplazo": "ejercicio_nuevo", "sustituto": "ejercicio_nuevo",
        "razon": "motivo", "razón": "motivo",
    },
    "consultar_rutina_futura": {
        "numero_semana": "semana", "week": "semana",
        "día": "dia", "dia_numero": "dia", "numero_dia": "dia",
    },
    "consultar_progreso": {
        "métrica": "metrica", "tipo": "metrica", "nombre_ejercicio": "ejercicio",
    },
    "explicar_tecnica_ejercicio": {
        "nombre_ejercicio": "ejercicio", "movimiento": "ejercicio",
    },
    "registrar_peso_corporal": {
        "peso": "peso_kg", "peso_corporal": "peso_kg", "peso_corporal_kg": "peso_kg",
    },
    "predecir_carga_optima": {
        "nombre_ejercicio": "ejercicio", "reps_objetivo": "repeticiones_objetivo",
        "repeticiones": "repeticiones_objetivo", "rpe": "rpe_objetivo",
    },
    "analizar_mrv": {
        "musculo": "grupo_muscular", "músculo": "grupo_muscular",
        "grupo": "grupo_muscular", "dias": "ventana_dias", "ventana": "ventana_dias",
    },
    "autorregular_dia": {
        "nombre_ejercicio": "ejercicio", "rpe": "rpe_reportado",
        "peso_kg": "carga_actual_kg", "peso": "carga_actual_kg",
        "carga_kg": "carga_actual_kg", "carga": "carga_actual_kg",
        "series": "series_restantes",
    },
    "enviar_grafica_progreso": {
        "nombre_ejercicio": "ejercicio", "dias": "periodo_dias",
        "periodo": "periodo_dias", "pie": "caption", "texto": "caption",
    },
    "evaluar_perfil_fisico": {
        "peso": "peso_kg", "altura": "altura_m", "estatura": "altura_m",
        "grasa": "porcentaje_grasa", "grasa_corporal": "porcentaje_grasa",
        "sexo": "genero",
    },
    "evaluar_nivel_fuerza": {
        "peso": "peso_kg", "sexo": "genero", "banca": "rm_banca_kg",
        "sentadilla": "rm_sentadilla_kg", "peso_muerto": "rm_peso_muerto_kg",
    },
    "avanzar_dia": {"forzar_avance": "forzar", "force": "forzar"},
    "pausar_plan": {"razon": "motivo", "razón": "motivo"},
    "generar_rutina": {
        "dias_semana": "dias", "objetivo": "meta",
        "periodizacion": "tipo_periodizacion", "tipo": "tipo_periodizacion",
        "grasa": "porcentaje_grasa", "contexto": "contexto_usuario",
    },
}

def _normalizar_args_tool(nombre_tool: str, args: dict, usuario: str = "") -> dict:
    """Traduce los sinónimos de argumento al nombre canónico del schema.
    Idempotente (aplicarla dos veces da lo mismo) y no destructiva: si el
    canónico ya viene con valor, gana él y el alias se descarta."""
    if not isinstance(args, dict):
        return {}
    tabla = _ALIAS_ARGS.get(nombre_tool)
    if not tabla:
        return args
    salida = dict(args)
    renombrados = []
    for alias, canonico in tabla.items():
        if alias not in salida:
            continue
        valor = salida.pop(alias)
        if salida.get(canonico) in (None, "", []):
            salida[canonico] = valor
            renombrados.append(f"{alias}->{canonico}")
    if renombrados and usuario:
        log(usuario, "ARG-ALIAS", f"{nombre_tool}: {', '.join(renombrados)}")
    return salida

def _bool_seguro(v) -> bool:
    """El modelo manda booleanos como true, "true", "sí" o 1 indistintamente."""
    if isinstance(v, bool):
        return v
    return str(v if v is not None else "").strip().lower() in (
        "true", "1", "si", "sí", "yes", "y")

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

    # v13 — El resumen se cortaba SIEMPRE a media frase (num_predict=220 y
    # done_reason=length en todos los turnos), y lo que se perdía era el final:
    # justo [ESTADO DE LA CHARLA], lo ÚNICO que no vive en ninguna otra parte.
    # Encima gastaba ese presupuesto en repetir biometría y marcas que el sistema
    # ya inyecta cada turno desde SQL — y las repetía MAL ("Edad: No
    # especificado" cuando el perfil dice 29 años), que es peor que no repetirlas
    # porque el modelo se cree el resumen y vuelve a pedirle al atleta datos que
    # dio hace meses. Ahora: primero el hilo (lo irremplazable) y sin duplicar lo
    # que Python ya sabe.
    prompt_compresion = (
            "ERES EL MOTOR DE COMPRESIÓN DE MEMORIA DE 'IRON PROGRESS'. "
            "Reduces el historial a un bloque compacto de hechos duros.\n\n"
            "=== NO REPITAS ESTO (el sistema ya se lo da al coach en cada turno) ===\n"
            "Ficha del atleta (edad, peso, altura, % grasa, meta, días), 1RM actuales, "
            "rutina del día y últimas series registradas. NO gastes espacio en ellos.\n\n"
            "=== LO QUE SOLO VIVE AQUÍ (obligatorio, por orden de importancia) ===\n"
            "1. ESTADO DE LA CHARLA: de qué se está hablando AHORA, qué preguntó el atleta y "
            "qué quedó a medias (ej. 'pidió cambiar el press militar y falta que confirme').\n"
            "2. ACUERDOS Y PREFERENCIAS: decisiones tomadas, sustituciones acordadas, equipo "
            "que no tiene, cosas que dijo que no le gustan, molestias reportadas y si siguen "
            "activas o ya se resolvieron.\n"
            "3. DATOS SUELTOS: cifras o marcas que el atleta mencionó de pasada y que no "
            "estaban en su ficha (PRs antiguos, sensaciones, cargas de otro gimnasio).\n\n"
            "=== FORMATO DE SALIDA OBLIGATORIO (sé breve, una o dos frases por sección) ===\n"
            "[ESTADO DE LA CHARLA]: [...]\n"
            "[ACUERDOS Y PREFERENCIAS]: [...]\n"
            "[DATOS SUELTOS]: [...]\n\n"
            + prefijo_previo + "\n".join(lineas) + "\n\nResumen compacto actualizado (formato obligatorio):"
    )

    try:
        resp = await chat_llm(
            [{"role": "user", "content": prompt_compresion}],
            # 220 no daba ni para el formato: se truncaba en el 100 % de los
            # turnos. Con 3 secciones breves, 400 sobra y solo cuesta ~4 s más.
            opciones={**OPCIONES_QWEN_32B, "num_predict": 400, "temperature": 0.1},
            quien="compresor"
        )
        texto_resumen = resp.get("message", {}).get("content", "").strip()
        texto_resumen = re.sub(r"<think>.*?</think>\s*", "", texto_resumen, flags=re.DOTALL).strip()

        if not texto_resumen:
            raise ValueError("Resumen vacío")

        if str(_campo_resp(resp, "done_reason", "")) == "length":
            print("[COMPRESOR] ⚠ el resumen se truncó por límite de tokens: se está "
                  "perdiendo el final. Sube num_predict o acorta el formato.")
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


def _buscar(coleccion, texto: str, k: int, filtro=None, vector=None):
    """Búsqueda vectorial reutilizando un embedding ya calculado.

    `similarity_search_with_score(texto, ...)` embebe el texto POR DENTRO: las
    tres consultas del contexto enriquecido (expediente, historial, ciencia)
    pedían a Ollama el embedding del MISMO mensaje tres veces por turno. Con el
    vector precalculado se hace una sola vez. Si el vector no está disponible
    (o la versión de langchain-chroma no expone la búsqueda por vector), se cae
    al camino de siempre: mismo resultado, solo que más lento.
    """
    if vector is not None:
        try:
            return coleccion.similarity_search_by_vector_with_relevance_scores(
                embedding=vector, k=k, filter=filtro)
        except (AttributeError, TypeError) as e:
            print(f"[RAG] búsqueda por vector no disponible ({e}); uso la clásica")
    return coleccion.similarity_search_with_score(texto, k=k, filter=filtro)


def _embebe(texto: str):
    """Embedding del turno, o None si Ollama no responde (fallback silencioso)."""
    try:
        return motor_embeddings.embed_query(texto)
    except Exception as e:
        print(f"[RAG] no se pudo calcular el embedding ({e}); cada colección lo hará por su cuenta")
        return None


def consultar_literatura_cientifica(pregunta: str, vector=None) -> str:
    if not db_cientifica: return "Error: Base de datos científica no disponible."
    try:
        # v4: k=3 con deduplicación — un solo fragmento de 400 chars era muy poco
        # para sustentar una recomendación; ahora se citan hasta 3 pasajes y el libro.
        resultados = _buscar(db_cientifica, pregunta, 3, None, vector)
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

def consultar_memoria_historica_atleta(numero_usuario: str, pregunta: str, vector=None) -> str:
    if not db_memoria_atleta: return "Error: Base de datos histórica no disponible."
    try:
        resultados = _buscar(db_memoria_atleta, pregunta, 3,
                             {"numero_usuario": str(numero_usuario)}, vector)
        vistos, textos = set(), []
        for doc, score in resultados:
            if score >= UMBRAL_RAG or doc.page_content in vistos:
                continue
            vistos.add(doc.page_content)
            textos.append(f"- {doc.page_content}")
        return "\n\n".join(textos) if textos else "No hay registros narrativos previos."
    except Exception as e:
        return f"Error al leer el historial: {e}"

def consultar_expediente_atleta(numero_usuario: str, concepto: str, vector=None) -> str:
    if not db_perfil_atleta: return "Error: base de datos no disponible."
    try:
        resultados = _buscar(db_perfil_atleta, concepto, 3,
                             {"numero_usuario": {"$eq": str(numero_usuario)}}, vector)
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
    if not (db_perfil_atleta or db_memoria_atleta or db_cientifica):
        return ""
    # UN solo embedding para las tres colecciones (antes: tres idénticos).
    vector = await asyncio.to_thread(_embebe, mensaje)
    expediente, historial, ciencia = await asyncio.gather(
        asyncio.to_thread(consultar_expediente_atleta, numero_usuario, mensaje, vector),
        asyncio.to_thread(consultar_memoria_historica_atleta, numero_usuario, mensaje, vector),
        asyncio.to_thread(consultar_literatura_cientifica, mensaje, vector),
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

# v13 — Notación de PRESCRIPCIÓN (series x reps) frente a notación de MARCA
# (kilos x reps). Los dos usan la misma "x" y el parser solo entendía la segunda,
# con el agravante de que ordenaba los números por tamaño: "hice 4x8 con 60 kg"
# entraba al diario como 8 kg x 4 reps. No es un dato incompleto, es un dato
# FALSO, y además contamina el 1RM estimado y todas las cargas que se predicen
# a partir de él. La pista que desambigua es el peso dicho con unidad: si el
# atleta ya declaró los kilos aparte, la "x" no puede ser de kilos.
_PESO_CON_UNIDAD_RE = re.compile(
    r"(?:con|a|al|de)\s*(\d+(?:[.,]\d+)?)\s*(?:kgs?|kilos?|kg)\b", re.IGNORECASE)
_BLOQUE_SERIES_RE = re.compile(
    r"\b(\d{1,2})\s*(?:series?|sets?)\s*(?:de|x|×)\s*(\d{1,2})\b"          # 3 series de 12
    r"|\b(\d{1,2})\s*[x×]\s*(\d{1,2})\b"                                    # 4x8 …con 60 kg
    r"(?=[^\n]*?(?:con|a|al)\s*\d+(?:[.,]\d+)?\s*(?:kgs?|kilos?|kg)\b)",
    re.IGNORECASE)


# v13 — PREGUNTA vs. REPORTE. "¿hago 4x8 con 60 kg?" trae una notación de serie
# impecable, pero el atleta está PREGUNTANDO: sin este filtro entraban al diario
# 4 series que nunca hizo (con la notación de marca era 1, por eso pasaba menos).
#
# EL VERBO MANDA SOBRE EL SIGNO, y ese es el punto delicado: "hice 4x8 con 60 kg,
# está bien?" es un reporte CON una pregunta detrás, y descartarlo por el "?"
# sería perder una serie real. Un falso negativo aquí (no registrar algo que sí
# hizo) es peor que el bug que se arregla, así que solo se bloquea cuando NO hay
# ninguna señal de ejecución Y sí la hay de pregunta.
_EJECUCION_RE = re.compile(
    r"\b(?:hice|hicimos|hac[ií]a|termin[ée]|acab[ée]|complet[ée]|met[ií]|saqu[ée]|"
    r"levant[ée]|logr[ée]|consegu[ií]|jal[ée]|llev[oé]|ven[ií]a|hech[oa]s?)\b"
    r"|\bacabo\s+de\b|\breci[ée]n\b|\bya\s+(?:voy|van|llevo)\b",
    re.IGNORECASE)
_PREGUNTA_SERIE_RE = re.compile(
    r"^\s*¿"                                        # abre interrogación
    r"|\?\s*$"                                      # cierra interrogación
    r"|^\s*(?:qu[ée]|cu[áa]nt[oa]s?|cu[áa]l|c[óo]mo|debo|deber[íi]a|puedo|hago|"
    r"har[ée]|hag[oa]|me\s+conviene|me\s+recomiendas|recomiendas|toca|pongo|subo|bajo)\b",
    re.IGNORECASE)


def es_pregunta_no_reporte(mensaje: str) -> bool:
    """True si el mensaje trae notación de serie pero PREGUNTA en vez de reportar.

    Conservadora a propósito: ante la duda devuelve False (registrar), porque
    perder una serie real es peor que registrar una de más, que el atleta puede
    retirar (ver `_rectificar_ultima_serie`).
    """
    t = str(mensaje or "").strip()
    if not t:
        return False
    if _EJECUCION_RE.search(t):
        return False              # dijo que lo hizo: es un reporte, pregunte o no
    return bool(_PREGUNTA_SERIE_RE.search(t))


def detectar_bloque_de_series(mensaje: str) -> list[dict]:
    """'3 series de 12 con 40 kilos' / '4x8 con 60 kg' → N series idénticas.

    Devuelve [] si el mensaje no declara los kilos con unidad: sin esa pista la
    "x" es ambigua y se prefiere no adivinar.
    """
    t = str(mensaje or "").lower()
    mp = _PESO_CON_UNIDAD_RE.search(t)
    if not mp:
        return []
    m = _BLOQUE_SERIES_RE.search(t)
    if not m:
        return []
    series, reps = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    try:
        series, reps, peso = int(series), int(reps), float(mp.group(1).replace(",", "."))
    except (TypeError, ValueError):
        return []
    if not (1 <= series <= 12 and 1 <= reps <= 30 and peso >= 1):
        return []
    ejercicio = None
    for kws, campo in _KW_EJERCICIO:
        if any(k in t for k in kws):
            ejercicio = RM_CANONICO[campo]
            break
    rpe = rpe_desde_texto(t)
    return [{"peso": peso, "reps": reps, "rpe": rpe, "ejercicio": ejercicio}
            for _ in range(series)]


# v13 — El atleta se corrige constantemente ("perdón, eran 90 no 100") y retira
# series que no llegó a hacer. Sin una vía real, el coach respondía "lo corrijo
# en tu expediente" / "la ignoro por completo" y NO ocurría nada: el dato falso
# se quedaba en el diario contaminando el 1RM y las cargas predichas, y encima
# el atleta se iba convencido de lo contrario. Es el peor tipo de fallo: el que
# se ve bien.
_CORRECCION_RE = re.compile(
    r"\b(?:perd[óo]n|perdona|disculpa|me equivoqu[ée]|error m[íi]o|mejor dicho|"
    r"corrige|corr[íi]jelo|no espera|espera no)\b|"
    r"\b(?:eran|era|fueron|fue)\s+\d+(?:[.,]\d+)?\s*(?:kgs?|kilos?)?\s*,?\s*no\s+\d+"
    r"|\bno\s+(?:eran|era|fueron|fue)\s+\d+(?:[.,]\d+)?\s*(?:kgs?|kilos?)?\s*,?\s*"
    r"(?:sino|si\s+no|eran|era|fueron|fue)\s+\d+",
    re.IGNORECASE)
# "eran 90 no 100" → el bueno es el primero. "no eran 100 sino 90" → el segundo.
_CORR_VALOR_RE = (
    re.compile(r"\bno\s+(?:eran|era|fueron|fue)\s+\d+(?:[.,]\d+)?\s*(?:kgs?|kilos?)?\s*,?\s*"
               r"(?:sino|si\s+no|eran|era|fueron|fue)\s+(\d+(?:[.,]\d+)?)", re.IGNORECASE),
    re.compile(r"\b(?:eran|era|fueron|fue|son|es)\s+(\d+(?:[.,]\d+)?)\s*(?:kgs?|kilos?)?\s*,?\s*"
               r"no\s+(\d+(?:[.,]\d+)?)", re.IGNORECASE),
)
_RETIRAR_SERIE_RE = re.compile(
    r"\b(?:olv[ií]dat?e?|borra|elimina|quita|descarta|no\s+cuentes|no\s+apuntes)\b"
    r"[^.\n]{0,25}\b(?:esa|esta|ese|[úu]ltima|la\s+serie|el\s+registro)\b"
    r"|\bno\s+la\s+(?:hice|llegu[ée]\s+a\s+hacer)\b"
    r"|\besa\s+serie\s+no\s+(?:cuenta|va|la\s+hice)\b",
    re.IGNORECASE)


def _rectificar_ultima_serie(numero_usuario: str, mensaje: str) -> dict | None:
    """Corrige o retira la última serie del diario. None si el mensaje no lo pide.

    El campo a corregir NO se adivina por el texto: se deduce contrastando el
    valor que el atleta declara erróneo con la última serie guardada. Si ese
    valor no coincide con ninguno de sus campos, no se toca nada — más vale no
    rectificar que rectificar lo que no era.
    """
    from services.db_service import (borrar_ultima_serie, corregir_ultima_serie,
                                     obtener_ultima_serie)
    texto = str(mensaje or "")

    if _RETIRAR_SERIE_RE.search(texto):
        res = borrar_ultima_serie(numero_usuario)
        if not res:
            return {"etapa": "SERIE-RETIRO", "resumen": "no había ninguna serie que retirar",
                    "bloque": "NO HAY SERIE QUE RETIRAR]: no consta ninguna serie suya en el "
                              "diario. Díselo tal cual; NO digas que la borraste."}
        b = res["borrada"]
        det = f"{b['ejercicio']} {b['peso_kg']:g} kg x {int(b['repeticiones'] or 0)}"
        return {"etapa": "SERIE-RETIRO", "resumen": f"serie retirada del diario ({det})",
                "bloque": f"SERIE RETIRADA DEL DIARIO]: se borró {det}. Confírmaselo con esos "
                          "datos exactos. Aviso: el 1RM estimado que ya se calculó con esa serie "
                          "NO se revierte; si te pregunta, dilo."}

    if not _CORRECCION_RE.search(texto):
        return None

    correcto = erroneo = None
    for i, patron in enumerate(_CORR_VALOR_RE):
        m = patron.search(texto)
        if m:
            correcto = float(m.group(1).replace(",", "."))
            if i == 1:
                erroneo = float(m.group(2).replace(",", "."))
            break
    if correcto is None:
        return None

    ultima = obtener_ultima_serie(numero_usuario)
    if not ultima:
        return None
    equivalencias = {"peso_kg": ultima.get("peso_kg"),
                     "repeticiones": ultima.get("repeticiones"),
                     "rpe_reportado": ultima.get("rpe_reportado")}

    # La pista que el atleta escribe ("el rpe", "kilos", "reps") MANDA sobre la
    # deducción por valor, porque los valores COLISIONAN: reps 8 y RPE 8 son el
    # mismo número. Consultándola solo como último recurso, "corrige el rpe, era
    # 9 no 8" cambiaba las REPETICIONES a 9 y dejaba el RPE como estaba: una
    # serie que el atleta nunca hizo, y encima el 1RM se recalcula desde ahí.
    if re.search(r"\brpe\b", texto, re.I):
        pista = "rpe_reportado"
    elif re.search(r"\d+\s*(?:kgs?|kilos?)", texto, re.I):
        pista = "peso_kg"
    elif re.search(r"\breps?\b|repeticion", texto, re.I):
        pista = "repeticiones"
    else:
        pista = None

    campo = None
    if erroneo is not None:
        candidatos = [c for c, v in equivalencias.items()
                      if v is not None and abs(float(v) - erroneo) < 1e-6]
        # Empate sin pista: gana el orden peso > reps > rpe. El RPE casi siempre
        # se nombra por su jerga, así que es el menos probable de los mudos.
        campo = pista if pista in candidatos else (candidatos[0] if candidatos else None)
    if campo is None:
        # Sin valor erróneo que contrastar, la pista del texto es lo único que hay.
        campo = pista
    if campo is None:
        return None
    if campo == "repeticiones":
        correcto = int(correcto)

    # Mismos rangos que exige `tool_registrar_serie_completada`: esta ruta NO pasa
    # por el handler, así que sin esto un "no eran 100 sino 900" mete 900 kg en el
    # diario. Ante un valor imposible se prefiere no tocar nada.
    rangos = {"peso_kg": (1.0, 500.0), "repeticiones": (1, 50), "rpe_reportado": (5.0, 10.0)}
    lo, hi = rangos[campo]
    if not (lo <= correcto <= hi):
        log(numero_usuario, "SERIE-CORRECCION",
            f"valor fuera de rango para {campo}: {correcto} (no se toca el diario)")
        return None

    res = corregir_ultima_serie(numero_usuario, {campo: correcto})
    if not res:
        return None
    a, d = res["antes"], res["despues"]
    ej = d.get("ejercicio") or "el ejercicio"
    nuevo = (f"{ej} {float(d['peso_kg']):g} kg x {int(d['repeticiones'] or 0)}"
             + (f" @RPE {float(d['rpe_reportado']):g}" if d.get("rpe_reportado") else ""))
    detalle_rm = ""
    try:
        reps = int(d["repeticiones"] or 0)
        if 1 <= reps <= 15:
            rm_val = calcular_1rm(TipoEjercicio("peso_libre"), reps, float(d["peso_kg"]))
            guardar_rm(numero_usuario, ej, rm_val, peso_origen=float(d["peso_kg"]),
                       reps_origen=reps, fuente="correccion_serie")
            detalle_rm = f" Su 1RM estimado se recalculó: {rm_val:g} kg."
    except Exception as e:                                  # el 1RM es accesorio
        print(f"[SERIE-CORRECCION] ⚠ no se pudo recalcular el 1RM: {e}")
    return {"etapa": "SERIE-CORRECCION",
            "resumen": f"serie corregida: {a} → {d}",
            "bloque": f"SERIE CORREGIDA EN EL DIARIO]: quedó como {nuevo}.{detalle_rm} "
                      "Confírmaselo con esos números exactos; ya está hecho, NO llames "
                      "herramientas para esto."}


def detectar_series_multiples(mensaje: str) -> list[dict]:
    """v9: Enumera TODAS las series (peso, reps, rpe y ejercicio) en un volcado de marcas."""
    bloque = detectar_bloque_de_series(mensaje)
    if bloque:
        return bloque
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
        # Aquí agregamos el RPE al diccionario
        encontradas.append({"peso": s["peso"], "reps": s["reps"], "rpe": s.get("rpe"), "ejercicio": ejercicio})

    # v11 — El split por comas y por " y " parte "100x5 y me quedaron 2 en el
    # tanque" en dos trozos: la serie queda en uno y su esfuerzo en el otro, así
    # que ningún fragmento por separado tiene ambas cosas. Si el mensaje entero
    # declara un esfuerzo y hay UNA sola serie huérfana, es de ella. Con varias
    # no se reparte a ciegas: se prefiere perder el dato a atribuirlo mal.
    sin_rpe = [e for e in encontradas if e["rpe"] is None]
    if len(sin_rpe) == 1:
        rpe_global = rpe_desde_texto(mensaje)
        if rpe_global is not None:
            sin_rpe[0]["rpe"] = rpe_global
    return encontradas

# ==============================================================================
# INTERCEPTOR DE SERIES
# ==============================================================================
# ==============================================================================
# RPE HABLADO (v11)
# ------------------------------------------------------------------------------
# La tabla de conversión existía... pero solo dentro de la description del
# parámetro 'rpe' en core/tools.json, o sea únicamente para que la leyera el
# modelo. El interceptor determinista solo entendía '@8' o 'rpe 8', así que
# "me quedaron 2 en el tanque" —que es MÁS preciso que decir 'rpe 8', porque es
# la definición misma del RPE— se guardaba como rpe=None.
#
# Lo que se perdía con ese None:
#   · e1rm_serie() calcula con RIR = 10 − RPE: sin RPE no hay reps efectivas.
#   · autorregular_dia es OBLIGATORIA con RPE ≥ 9.5. El atleta decía "casi no
#     subió" y la bajada de carga del día no se disparaba nunca.
#   · registrar_serie_completada exige la tríada y devolvía faltan_datos.
_RIR_EXCLUIR = r"(?!\s*(?:series?|ejercicios?|d[ií]as?|semanas?|kilos?|kg|min))"
_RIR_PATRONES = (
    rf"(?:me\s+)?qued(?:aron|aban|an|aba|ó|o)\s+(?:como\s+|unas?\s+)?(\d+){_RIR_EXCLUIR}",
    r"(\d+)\s*(?:reps?\s*)?(?:en\s+(?:el\s+|la\s+)?(?:tanque|reserva|rec[áa]mara)|de\s+reserva)",
    r"\brir\s*(?:de\s*)?(\d+)",
    r"(\d+)\s*rir\b",
    r"podr[ií]a\s+(?:haber\s+)?(?:hecho|sacado|metido)\s+(\d+)\s+m[áa]s",
    r"me\s+sobr(?:aron|aba|ó|o)\s+(\d+)",
)
# Frases sin cifra. El orden importa: 'casi no subió' debe ganar a 'no subió'.
_RPE_VERBAL = (
    (9.5, r"casi\s+(?:no\s+)?(?:sub|fall|se\s+queda)|a\s+punto\s+de\s+fallar|"
          r"por\s+poco\s+(?:no\s+)?(?:sub|la\s+saco)|apenas\s+(?:la\s+)?sub|"
          r"me\s+cost[óo]\s+(?:horrores|much[íi]simo)|se\s+me\s+atasc"),
    (10.0, r"\bal\s+fallo\b|hasta\s+el\s+fallo|fallo\s+t[ée]cnico|\bfall[ée]\b|"
           r"no\s+(?:me\s+)?(?:la\s+)?sali[óo]\s+la\s+[úu]ltima|me\s+qued[ée]\s+trabado|"
           r"no\s+(?:me\s+)?sub[íi]a?\b"),
    (9.0, r"(?:me\s+)?qued(?:aba|ó|o|aban)\s+(?:solo\s+)?una?\b|"
          r"(?:solo\s+)?una\s+m[áa]s\s+(?:y\s+ya|nada\s+m[áa]s)?|una\s+en\s+el\s+tanque"),
    (6.0, r"\bf[áa]cil\b|\bsuave\b|\bc[óo]modo\b|sobrad[oa]|\bvolad[oa]\b|"
          r"\bligero\b|sin\s+problema|ni\s+me\s+enter[ée]"),
)
_RPE_NUMERICO_RE = re.compile(r"(?:@|\brpe\s*)\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)


def rpe_desde_texto(texto: str) -> float | None:
    """Traduce a RPE cómo describe el atleta el esfuerzo. Devuelve None si no
    hay ninguna señal — nunca se inventa un esfuerzo que no se dijo."""
    t = str(texto or "").lower()
    m = _RPE_NUMERICO_RE.search(t)               # '@8' / 'rpe 8' siguen mandando
    if m:
        try:
            v = float(m.group(1).replace(",", "."))
            if 5.0 <= v <= 10.0:
                return round(v * 2) / 2.0
        except ValueError:
            pass
    for patron in _RIR_PATRONES:                 # RIR explícito: RPE = 10 − RIR
        m = re.search(patron, t, re.IGNORECASE)
        if m:
            try:
                rir = int(m.group(1))
            except ValueError:
                continue
            if 0 <= rir <= 10:
                return max(5.0, min(10.0, 10.0 - rir))
    for valor, patron in _RPE_VERBAL:
        if re.search(patron, t, re.IGNORECASE):
            return valor
    return None


def detectar_serie_en_mensaje(mensaje: str) -> dict | None:
    """v9: Detecta peso, repeticiones y opcionalmente el RPE en mensajes directos."""
    # v13: "4x8 con 60 kg" son series x reps, no kilos x reps. Se resuelve antes
    # de los patrones de marca para no invertir los números (ver
    # detectar_bloque_de_series).
    bloque = detectar_bloque_de_series(mensaje)
    if bloque:
        return {k: bloque[0][k] for k in ("peso", "reps", "rpe")}
    patrones = [
        r'(\d+(?:[.,]\d+)?)\s*(?:kg)?\s*[xX×]\s*(\d+)(?:\s*(?:@|rpe)\s*(\d+(?:[.,]\d+)?))?',
        r'(\d+(?:[.,]\d+)?)\s*(?:kg)?\s*por\s*(\d+)(?:\s*(?:@|rpe)\s*(\d+(?:[.,]\d+)?))?',
        r'hice\s+(\d+)\s+(?:reps?|repeticiones?)\s+con\s+(\d+(?:[.,]\d+)?)\s*kg(?:\s*(?:@|rpe)\s*(\d+(?:[.,]\d+)?))?',
        r'(\d+)\s+(?:reps?|repeticiones?)\s+(?:con|a)\s+(\d+(?:[.,]\d+)?)\s*kg(?:\s*(?:@|rpe)\s*(\d+(?:[.,]\d+)?))?',
    ]
    for patron in patrones:
        match = re.search(patron, mensaje, re.IGNORECASE)
        if match:
            g = match.groups()
            # Asignación inteligente dependiendo del orden de los grupos en el patrón
            if len(g) >= 3 and g[2]:  # El tercer grupo atrapó el RPE
                a, b, rpe = float(g[0].replace(",", ".")), float(g[1].replace(",", ".")), float(g[2].replace(",", "."))
            else:
                a, b, rpe = float(g[0].replace(",", ".")), float(g[1].replace(",", ".")), None

            peso, reps = (a, b) if a > b else (b, a)
            reps = int(reps)

            if 1 <= reps <= 30 and peso >= 1:
                # Sin '@8' pegado a la serie, el esfuerzo puede venir dicho con
                # palabras en el mismo fragmento ("100x5 y casi no sube").
                if rpe is None:
                    rpe = rpe_desde_texto(mensaje)
                return {"peso": peso, "reps": reps, "rpe": rpe}
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

# Patrones de limpieza precompilados. Antes se compilaban 14 regex en CADA
# llamada, y la función se llama hasta 4 veces por turno (borrador, reescritura
# honesta, anti-rutina-fantasma, validador de kilajes).
_LIMPIEZA_PATRONES = (
    re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE),
    re.compile(r"\[INFO DE SISTEMA[^\n\]]*\]\s*", re.IGNORECASE),
    re.compile(r"<tool_call>.*?</tool_call>\s*", re.DOTALL | re.IGNORECASE),
    re.compile(r'```(?:json)?\s*\{[^`]*"(?:name|arguments)"[^`]*\}[^`]*```', re.DOTALL | re.IGNORECASE),
    re.compile(r'\{\s*"name"\s*:.*?"arguments"\s*:\s*\{.*?\}\s*\}', re.DOTALL),
)
_SALTOS_EXTRA_RE = re.compile(r"\n{3,}")
_NOMBRES_TOOLS_RE = re.compile(
    r"[`'\"]?(?:evaluar_perfil_fisico|generar_rutina|registrar_serie_completada|"
    r"calcular_rm_exacto|consultar_libro_cientifico|avanzar_dia|"
    r"consultar_expediente_personal|consultar_historial_narrativo|"
    r"guardar_en_expediente_personal|predecir_carga_optima|analizar_mrv|"
    r"autorregular_dia|enviar_grafica_progreso|consultar_rutina_futura|"
    r"sustituir_ejercicio|aplicar_descarga|consultar_progreso|"
    r"explicar_tecnica_ejercicio|registrar_peso_corporal|pausar_plan|"
    r"reanudar_plan|evaluar_nivel_fuerza|actualizar_1rm|consultar_volumen_plan)[`'\"]?",
    re.IGNORECASE)


def limpiar_texto_para_whatsapp(texto: str) -> str:
    if not texto:
        return ""
    for patron in _LIMPIEZA_PATRONES:
        texto = patron.sub("", texto)
    texto = texto.replace("**", "*").replace("#", "")
    texto = _SALTOS_EXTRA_RE.sub("\n\n", texto)
    texto = _NOMBRES_TOOLS_RE.sub("mis métodos de coach", texto)
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
    return _sesiones_semana_cache(numero_usuario, semana)


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


_CR_SEMANA_N_RE = re.compile(r'\b(?:ver|mostrar\w*|pasar\s+a|semana)\s+(\d+)')
_CR_DIA_PALABRA_RE = re.compile(r'\bd[ií]a\b')
_CR_DIA_N_RE = re.compile(r'\bd[ií]a\s*(\d+)\b')
_CR_GUARD_DIA_RE = re.compile(r'rutina|plan|entren|toca|ver|dime|mu[eé]str|mostrar|dame|'
                              r'ense[ñn]|ejercicio|semana|saber|c[oó]mo')
_CR_DIA_SIGUIENTE_RE = re.compile(
    r'(siguiente|pr[óo]xim[oa])\s+(d[ií]a|entrenamiento|sesi[oó]n)|'
    r'd[ií]a\s+(siguiente|que\s+sigue)|\bqu[eé]\s+sigue\b|'
    r'\b(?:y\s+)?(?:el|la)\s+(?:que\s+)?sigue\b|\bel\s+siguiente\b')
_CR_SEMANA_COMPLETA_RE = re.compile(
    r'(tod[ao]|completa|entera)\s+(?:l?a\s+|esta\s+)?semana|'
    r'semana\s+(completa|entera)|'
    r'c[oó]mo\s+(?:vamos|voy)\s+a\s+entrenar|'
    r'entrenar\s+(?:toda\s+)?(?:esta|la)\s+semana|'
    r'todos\s+los\s+d[ií]as')

# Precompilados: esta función corre 2-3 veces por turno y compilaba ~20 patrones
# en cada pasada.
_CR_SEMANA_SIGUIENTE = tuple(re.compile(p) for p in [
    r'semana\s+(?:siguiente|próxima|prox|que\s+viene|futura)',
    r'(?:siguiente|próxima|prox)\s+semana',
    r'pasemos?\s+(?:a\s+)?(?:la\s+)?(?:siguiente|próxima|prox)?\s*semana',
    r'(?:avanzar?|ir|pasar)\s+(?:a\s+)?(?:la\s+)?(?:siguiente|próxima)\s+semana',
    r'(?:dame|muéstrame|ver|dime)\s+(?:la\s+)?(?:siguiente|próxima)\s+semana',
    r'(?:semana|plan|rutina)\s+\d+',
])

_CR_VER_RUTINA = tuple(re.compile(p) for p in [
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
    r'\b(?:cual|cuál)\s+es\s+mi\s+(?:rutina|plan)\b',
    # v8 (Mandato 5) — "¿qué peso uso para cumplir mi rutina?" es una CONSULTA
    # de la rutina existente (los kg ya vienen calculados en el plan). Antes no
    # matcheaba nada aquí, caía como señal débil de creación y disparaba el
    # REGEN jail (bug 3.3).
    r'(?:qu[eé]|cu[aá]nt[oa])s?\s+pesos?\s+(?:le\s+|me\s+)?'
    r'(?:uso|us[ao]r?|pongo|poner|meto|cargo|carg[ao]r?|debo|deber[ií]a|'
    r'me\s+toca|corresponde|manejo|levanto)\b',
    r'(?:c[oó]mo|con\s+qu[eé]\s+peso)\s+(?:cumplo|cumplir|hago|realizo)\s+'
    r'(?:mi\s+|la\s+)?(?:rutina|plan|sesi[oó]n)\b',
])


def detectar_consulta_rutina(mensaje: str) -> dict | None:
    msg = mensaje.lower()

    # Semana N explícita ("ver semana 2", "semana 3")
    match_semana = _CR_SEMANA_N_RE.search(msg)
    if match_semana and not _CR_DIA_PALABRA_RE.search(msg):
        return {"tipo": "semana_futura", "semana_objetivo": int(match_semana.group(1))}

    # v4.2 — DÍA específico ("dime el día 3", "no pero el día 3"). Era el agujero
    # que hacía al coach repetir "hoy toca Día 1" en bucle: sin detección, la
    # telemetría solo traía el día actual y el modelo confabulaba un bloqueo.
    m_dia = _CR_DIA_N_RE.search(msg)
    if m_dia:
        guard = _CR_GUARD_DIA_RE.search(msg)
        if guard or len(msg.strip()) <= 45:
            return {"tipo": "dia_especifico", "dia_objetivo": int(m_dia.group(1)),
                    "enfoque": _detectar_enfoque(msg)}

    # v5.3 — día siguiente. El regex ya NO destruye los constraints del usuario:
    # "el siguiente día DE PIERNA" conserva el enfoque para que el cursor avance
    # hasta la próxima sesión que lo cumpla, en vez de entregar el día N+1 a ciegas.
    if _CR_DIA_SIGUIENTE_RE.search(msg):
        return {"tipo": "dia_siguiente", "enfoque": _detectar_enfoque(msg)}

    # v4.2 — semana completa ("toda esta semana", "cómo vamos a entrenar la semana")
    if _CR_SEMANA_COMPLETA_RE.search(msg):
        return {"tipo": "semana_completa"}

    for patron in _CR_SEMANA_SIGUIENTE:
        if patron.search(msg): return {"tipo": "semana_futura"}
    for patron in _CR_VER_RUTINA:
        if patron.search(msg): return {"tipo": "ver_rutina"}
    return None

def _contar_sesiones_semana(numero_usuario: str, semana: int) -> int:
    """v4.2: cuántos días reales tiene una semana del plan (para respuestas exactas)."""
    return len(_sesiones_semana_cache(numero_usuario, semana))

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


_DIAS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def fecha_humana(fecha) -> str:
    """'2026-07-30' -> '2026-07-30 (jueves)'.

    v11 — el modelo deducía el día de la semana a partir de la fecha ISO y lo hacía
    MAL: dijo "domingo 30" tres veces seguidas cuando el 2026-07-30 era jueves, y el
    error se propagaba al leer su propia respuesta anterior. Los LLM son malos en
    aritmética de calendario; se lo damos ya calculado.

    No se usa strftime('%A') a propósito: depende del locale del sistema y en este
    equipo saldría en inglés.
    """
    try:
        d = datetime.strptime(str(fecha)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return str(fecha)
    return f"{d.strftime('%Y-%m-%d')} ({_DIAS_ES[d.weekday()]})"


def construir_telemetria(fecha, hora, estatus, instruccion_de_hoy, dashboard_sql,
                         es_casual, estado_gen, semana_actual,
                         bloque_onboarding: str = "", ficha_atleta: str = "") -> str:
    """v2: el onboarding ya no manda una lista de instrucciones genéricas;
    manda EXACTAMENTE lo que el sistema acaba de hacer y la ÚNICA pregunta siguiente."""
    if bloque_onboarding:
        partes = [f"FECHA: {fecha_humana(fecha)} {hora}", bloque_onboarding]
        return TELEMETRIA_INICIO + "\n" + "\n\n".join(partes) + "\n" + TELEMETRIA_FIN

    if es_casual:
        # v4.2: en charla casual el estatus es SOLO contexto interno; antes el
        # coach saludaba con "estás atrasado" y soltaba la rutina sin que nadie
        # le preguntara.
        return (f"{TELEMETRIA_INICIO}\nFECHA: {fecha_humana(fecha)} {hora}\n"
                f"CALENDARIO (contexto interno: NO lo menciones ni muestres la rutina "
                f"a menos que el atleta saque el tema de entrenar): {estatus}"
                f"\n{TELEMETRIA_FIN}")

    partes = [f"FECHA: {fecha_humana(fecha)} {hora}",
              f"CALENDARIO: {estatus} | SEMANA ACTUAL: {semana_actual}"]

    if estado_gen == "generando":
        partes.append("ESTADO: El plan se está generando AHORA MISMO. No muestres ni "
                      "inventes ejercicios; dile que le avisarás en unos minutos.")
    else:
        # v4.4 — Timeline 01:24: el modelo afirmó "ya se está generando tu plan"
        # sin que nada se hubiera disparado. Ahora la verdad es explícita.
        partes.append("GENERACIÓN DE PLAN: ninguna en curso. PROHIBIDO decir que un plan "
                      "'se está generando' o 'ya viene en camino': si hubiera una en marcha "
                      "lo verías escrito aquí.")
        cabecera = (
            "PRÓXIMA SESIÓN — hoy NO toca entrenar. No la presentes como el entrenamiento de "
            "hoy; si el atleta quiere adelantarla, dile que puede y que el calendario se moverá. "
            "Los *kg* ya vienen calculados: transcríbelos literales."
            if str(estatus).startswith("DESCANSO") else
            "RUTINA DEL DÍA (los *kg* ya vienen calculados y redondeados: transcríbelos "
            "literales, no los recalcules):")
        partes.append(f"{cabecera}\n{instruccion_de_hoy}")
        partes.append(f"ÚLTIMAS SERIES:\n{dashboard_sql}")
        # v13 — La ficha del atleta SOLO se inyectaba durante el onboarding y en
        # la regeneración. En conversación normal el coach no tenía delante ni la
        # edad ni la altura de quien le hablaba, así que se las pedía otra vez
        # ("dime tu edad y tu altura y te calculo las calorías") aunque llevaran
        # meses guardadas. Para el atleta eso es un coach que no le conoce.
        if ficha_atleta and ficha_atleta != "nada todavía":
            partes.append(f"FICHA DEL ATLETA (YA la tienes: NO se la vuelvas a pedir; si "
                          f"algo falta, pide SOLO eso): {ficha_atleta}")

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
    semanas = set()
    for sesiones in _archivos_plan_cargados(numero_usuario):
        for s in sesiones:
            try:
                semanas.add(int(s.get("Semana_Abs", 0)))
            except (TypeError, ValueError):
                continue
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

# v8 (Mandato 1) — números "libres" (sin sufijo kg) en rango plausible de carga.
# El historial dice "145x3" o "mi PR fue 145": si se exigiera el sufijo, todo eso
# quedaría fuera de la whitelist y el validador seguiría bloqueando memoria real.
_NUM_LIBRE_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{1,2})?)(?![\d%])")

def _extraer_numeros_libres(texto: str) -> set[float]:
    vals = set()
    for v in _NUM_LIBRE_RE.findall(texto or ""):
        try:
            f = float(v.replace(",", "."))
        except ValueError:
            continue
        if 1.0 <= f <= 500.0:      # rango plausible de cargas/lastres/pesos corporales
            vals.add(f)
    return vals

def _construir_whitelist_kgs(numero_usuario: str, fuentes_turno: str,
                             historial: list, resultados_tools: str) -> set[float]:
    """v8 (Mandato 1) — WHITELIST DINÁMICA del turno. Junta TODO número legítimo:

    1. Fuentes del turno (mensaje, rutina calculada, dashboard, telemetría).
    2. Payloads REALES de las tools ([TOOL-RESULT]) — bug 1.3: los 135 kg de la
       semana 2 devueltos por consultar_rutina_futura se marcaban inventados.
    3. TODO el historial en memoria (ventana de corto plazo) — bug 1.1: el PR de
       145 kg dicho turnos atrás se bloqueaba; y bug 1.4: con "siii" como único
       mensaje, la whitelist quedaba casi vacía y colapsaba por arrastre. Nota de
       seguridad: los mensajes del assistant en historial YA pasaron este mismo
       validador cuando se emitieron, así que citarlos no re-abre la puerta.
    4. Perfil completo (peso, lastre, etc.) — bug 1.2: los 30 kg de lastre del
       expediente se bloqueaban por no cruzar el perfil global.
    5. RMs canónicos + historial fechado (rm Y peso_origen) + peso corporal.
    """
    permitidos: set[float] = set()

    # 1-2) turno + tools: con sufijo kg Y números libres (notación 145x3)
    for bloque in (fuentes_turno, resultados_tools):
        permitidos |= _extraer_kgs(bloque)
        permitidos |= _extraer_numeros_libres(bloque)

    # 3) ventana de memoria completa del usuario (user + assistant)
    try:
        for m in (historial or []):
            c = m.get("content", "") if isinstance(m, dict) else str(m)
            permitidos |= _extraer_kgs(c)
            permitidos |= _extraer_numeros_libres(c)
    except Exception as e:
        print(f"[WHITELIST-KG] historial ilegible: {e}")

    # 4) perfil numérico completo
    try:
        for v in cargar_perfil(numero_usuario).values():
            if isinstance(v, (int, float)) and 1.0 <= float(v) <= 500.0:
                permitidos.add(round(float(v), 1))
    except Exception as e:
        print(f"[WHITELIST-KG] perfil ilegible: {e}")

    # 5) RMs canónicos + historial fechado (rm y peso_origen) + peso corporal
    try:
        permitidos |= {round(float(v), 1) for v in cargar_rms(numero_usuario).values()
                       if isinstance(v, (int, float))}
    except Exception:
        pass
    # Ambos historiales se leen con caché por firma de archivo: son dos json.load
    # por turno sobre archivos que solo cambian cuando se registra una marca.
    try:
        for h in _leer_json_cacheado(_ruta_historial_rm(numero_usuario), []) or []:
            if not isinstance(h, dict):
                continue
            for k in ("rm", "peso_origen"):
                if isinstance(h.get(k), (int, float)):
                    permitidos.add(round(float(h[k]), 1))
    except (TypeError, ValueError):
        pass
    try:
        for h in _leer_json_cacheado(_ruta_peso(numero_usuario), []) or []:
            if isinstance(h, dict) and isinstance(h.get("peso"), (int, float)):
                permitidos.add(round(float(h["peso"]), 1))
    except (TypeError, ValueError):
        pass

    # 6) ADAPTACIÓN AL EQUIPAMIENTO. El plan calcula en múltiplos de 2.5, pero si
    #    el atleta solo tiene discos de 2.5 kg no puede montar 112.5: el ajuste
    #    correcto es 110 o 115. Ese ajuste NO estaba en ninguna fuente y el
    #    validador lo marcaba inventado, así que el coach terminaba negándose a
    #    adaptar la carga. Se admite únicamente el redondeo de un valor YA
    #    legítimo a su múltiplo adyacente: nunca se aleja más de un incremento
    #    del ancla real, así que no habilita cifras salidas de la nada.
    incremento = _incremento_barra(numero_usuario)
    if incremento > 0:
        derivados = set()
        for p in permitidos:
            if p < 20.0:          # discos y accesorios: no aplica el redondeo de barra
                continue
            base = math.floor(p / incremento) * incremento
            derivados.add(round(base, 1))
            derivados.add(round(base + incremento, 1))
        permitidos |= derivados

    return permitidos

def _verificar_kgs_salida(respuesta: str, permitidos: set[float]) -> list[float]:
    """v3 → v8 (Mandato 1) — Devuelve los kilajes de la respuesta que NO existen en
    la whitelist dinámica. Tolerancia ±0.5 (redondeos conversacionales tipo
    147.5 -> 147) y discos/menciones triviales exentos."""
    kgs_respuesta = _extraer_kgs(respuesta)
    if not kgs_respuesta:
        return []
    triviales = {0.0, 1.25, 2.5, 5.0, 10.0, 20.0}   # discos y barra vacía
    inventados = []
    for kg in kgs_respuesta:
        if kg in triviales:
            continue
        if any(abs(kg - p) <= 0.5 for p in permitidos):
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
    # v13 — `turno_exclusivo_llm` va DENTRO del lock por usuario y ENVUELVE al
    # try: si algo revienta entre dos inferencias, el semáforo se libera igual
    # (ver el finally del context manager). El lock por usuario evita que un
    # atleta se solape consigo mismo; esto evita que se solape con OTRO atleta a
    # mitad de turno y se desalojen la caché de prompt mutuamente.
    async with lock, turno_exclusivo_llm():
        try:
            return await _generar_respuesta_coach_interno(numero_usuario, mensaje_usuario)
        except Exception as e:
            # Toda la fase previa a la llamada al modelo (onboarding, calendario,
            # interceptores) vivía FUERA del try interno: un estado corrupto en
            # disco reventaba aquí y el atleta se quedaba sin ninguna respuesta.
            # Ahora siempre sale algo, y la memoria se recarga del disco en el
            # siguiente turno para no arrastrar un historial a medio escribir.
            import traceback
            traceback.print_exc()
            log(numero_usuario, "ERROR-ORQUESTADOR", f"{type(e).__name__}: {e}")
            historial_memoria.pop(numero_usuario, None)
            _firmas_historial.pop(numero_usuario, None)
            return "Se me cruzaron los cables un segundo bro 🙏 Repíteme eso porfa."


async def _forzar_respuesta_texto(mensajes: list, instruccion: str) -> str:
    """Último recurso: obliga al modelo a responder en texto plano, SIN herramientas."""
    try:
        resp = await chat_llm(
            mensajes + [{"role": "user", "content": f"[SISTEMA]: {instruccion}"}],
            tools=None,   # sin tools a propósito: no puede escaparse a otra tool call
            opciones={**OPCIONES_QWEN_32B, "num_predict": 512},
            pensar=False,
            quien="fallback-texto"
        )
        return (resp.get("message", {}) or {}).get("content", "") or ""
    except Exception as e:
        print(f"[FALLBACK TEXTO] {e}")
        return ""


def _instruccion_fallback(base: str, acciones_ok: list, acciones_fallidas: list,
                          notas_turno: list | None = None) -> str:
    """v8 (Mandato 3) — Construye la instrucción del fallback con el ESTADO REAL del
    turno. Antes la instrucción era genérica y el modelo, sin contexto de error,
    ASUMÍA éxito y redactaba '¡Listo! Serie anotada ✅' con la base de datos vacía
    (bug 2.2). Ahora la verdad viaja explícita y la mentira queda prohibida.

    v12 — `notas_turno` existe porque la reescritura se hace sobre
    historial_memoria, que NO contiene los resultados de las herramientas de este
    turno (viven en la copia del orquestador y no se persisten). Es decir: el
    modelo reescribe A CIEGAS. Medido el 2026-07-29: analizar_mrv devolvió
    'datos_insuficientes', el guardia reescribió, y el modelo —sin ver ese
    resultado— redactó "hubo un problema técnico momentáneo". Nunca lo hubo.
    """
    partes = [base]
    if notas_turno:
        partes.append("LO QUE DE VERDAD PASÓ AL CONSULTAR: " + " ".join(notas_turno))
    if acciones_ok:
        partes.append("ACCIONES QUE SÍ SE COMPLETARON en este turno (puedes confirmarlas): "
                      + "; ".join(acciones_ok) + ".")
    else:
        partes.append("NINGUNA acción de guardado/registro se completó en este turno: "
                      "tienes PROHIBIDO afirmar que algo quedó guardado, anotado o registrado.")
    if acciones_fallidas:
        partes.append("ACCIONES QUE FALLARON (informa al atleta con honestidad y en corto que "
                      "esto NO se guardó, y pídele reintentarlo o los datos que falten): "
                      + "; ".join(acciones_fallidas) + ".")
    else:
        # v11 — sin esto el modelo rellenaba el hueco INVENTANDO la causa: dijo
        # "hubo un error técnico en el guardado" cuando no hubo ningún error —
        # simplemente no se intentó nada, porque faltaba saber de qué ejercicio
        # eran las series. Culpar al sistema de un fallo inexistente destruye la
        # confianza del atleta y le hace reintentar algo que nunca falló.
        partes.append("NO hubo ningún error técnico ni fallo del sistema: sencillamente no "
                      "se ejecutó ninguna acción. Tienes PROHIBIDO inventar errores de "
                      "guardado, fallos técnicos o problemas del sistema. Si falta un dato "
                      "para poder registrar algo (por ejemplo de QUÉ EJERCICIO es una serie), "
                      "pídeselo con naturalidad, como haría un coach que necesita saberlo.")
    partes.append("Responde al atleta en 1-3 frases, con tu tono de coach, sin tecnicismos "
                  "ni nombres de herramientas.")
    return " ".join(partes)


async def _generar_respuesta_coach_interno(numero_usuario: str, mensaje_usuario: str):
    t_inicio = time.monotonic()
    log(numero_usuario, "IN", f"mensaje: {mensaje_usuario!r}")
    mis_herramientas = cargar_herramientas()
    ahora        = datetime.now()
    fecha_actual = ahora.strftime("%Y-%m-%d")
    hora_actual  = ahora.strftime("%H:%M:%S")
    ruta_historial = historial_usuario(numero_usuario)

    # HISTORIAL: memoria primero, disco solo cuando hace falta de verdad.
    # Antes se releía y se parseaba el JSON completo (decenas de KB) en CADA
    # mensaje, aunque este proceso sea el único que lo escribe. Ahora el disco
    # es respaldo: al final del turno la memoria queda EXACTAMENTE igual que lo
    # persistido (misma poda de telemetría), así que el modelo ve literalmente
    # el mismo contexto que antes.
    #
    # El `os.stat` sigue haciéndose cada turno (microsegundos) para que un
    # borrado externo —scripts/reset_usuario.py con el servidor encendido— se
    # detecte igual que antes y la memoria no se quede con datos zombis.
    firma_hist = _firma_archivo(ruta_historial)
    if (numero_usuario not in historial_memoria
            or _firmas_historial.get(numero_usuario) != firma_hist):
        historial_memoria[numero_usuario] = []
        if firma_hist is not None:
            try:
                async with aiofiles.open(ruta_historial, mode="r", encoding="utf-8") as f:
                    contenido = await f.read()
                cargado = json.loads(contenido)
                historial_memoria[numero_usuario] = cargado if isinstance(cargado, list) else []
            except Exception as e:
                log(numero_usuario, "HIST-ERROR", f"historial corrupto, se reinicia: {e}")
                historial_memoria[numero_usuario] = []
        else:
            log(numero_usuario, "HIST", "sin historial en disco; se arranca de cero")
        _firmas_historial[numero_usuario] = firma_hist

    estado_hoy           = obtener_estado_usuario(numero_usuario)
    fecha_programada_str = estado_hoy.get("fecha_programada", fecha_actual)
    # Foto de la fecha ANTES de tocar nada: el validador de salida la compara
    # con la de después para saber si el calendario se movió de verdad.
    fecha_prog_al_entrar = str(fecha_programada_str)
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

    # Una fecha corrupta en estado_usuarios.json (editada a mano, o un formato
    # de otra versión) lanzaba ValueError AQUÍ, antes de cualquier try: el
    # atleta se quedaba sin respuesta y el log solo mostraba el traceback.
    try:
        fecha_prog = datetime.strptime(str(fecha_programada_str)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        log(numero_usuario, "ESTADO",
            f"fecha_programada inválida ({fecha_programada_str!r}); se reprograma para hoy")
        fecha_programada_str = fecha_actual
        fecha_prog = ahora
        _actualizar_estado_json(numero_usuario, fecha_programada=fecha_actual)

    if ahora.date() < fecha_prog.date():
        estatus = f"DESCANSO: Próximo entreno el {fecha_humana(fecha_programada_str)}."
        # La telemetría se contradecía a sí misma: CALENDARIO decía DESCANSO y dos
        # líneas más abajo la rutina venía encabezada "*HOY — Día 2 · Pierna*". El
        # modelo resolvía la contradicción a cara o cruz: en un turno anunciaba
        # descanso y en el siguiente aconsejaba sobre "la sesión de hoy". Aquí se
        # deja UNA sola verdad; la sesión sigue delante, pero etiquetada como lo
        # que es.
        instruccion_de_hoy = str(instruccion_de_hoy).replace(
            "*HOY — ", "*PRÓXIMA SESIÓN (no es hoy) — ")
    elif ahora.date() == fecha_prog.date():
        estatus = f"AL DÍA: Hoy ({fecha_humana(fecha_actual)}) toca entrenamiento."
    else:
        estatus = f"ATRASADO: Era el {fecha_humana(fecha_programada_str)}, reponiendo hoy ({fecha_humana(fecha_actual)})."

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

    # v8 (Mandato 5) — GATE SEMÁNTICO: con un plan ACTIVO, una señal DÉBIL de
    # creación (verbos tipo "quiero" + token 'rutina', el keyword-matching que
    # secuestraba la charla) ya NO basta para amenazar con regenerar. Solo si el
    # clasificador LLM confirma 'crear' se sigue; 'consultar'/'otro' devuelven la
    # conversación a su cauce. La señal FUERTE (CREACION_FUERTE_RE) no paga esta
    # llamada, y el flujo de onboarding sin plan tampoco se toca.
    if (en_creacion and not rutina_vacia and estado_gen != "generando"
            and not onboarding_activo and not bloque_onboarding
            and not CREACION_FUERTE_RE.search(texto_puro)
            and not estado_hoy.get("confirmar_regeneracion")
            and not estado_hoy.get("confirmar_datos_regen")):
        veredicto = await clasificar_intencion_plan(mensaje_usuario, numero_usuario)
        if veredicto != "crear":
            en_creacion = False
            log(numero_usuario, "INTENCION",
                f"señal débil de creación con plan activo; clasificador dice '{veredicto}' -> NO regenerar")

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
        # v8 (Mandato 5): la señal débil EN_CREACION_RE ya NO confirma — "quiero
        # saber qué peso usar para mi rutina" durante el flag pendiente contaba
        # como un "sí" al reemplazo. Solo afirmación corta, señal FUERTE o meta.
        if (AFIRMACION_CORTA_RE.match(texto_puro) or CREACION_FUERTE_RE.search(texto_puro)
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
        elif (detectar_consulta_rutina(texto_puro)
              or ("?" in texto_puro and len(texto_puro) > 8)):
            # v8 (Mandato 5) — ESCAPE DEL JAIL: si en vez de sí/no el atleta hace
            # una PREGUNTA o consulta su rutina, el bucle se cancela y su duda se
            # atiende. Antes esto caía en "re-pregunta" hasta 2 veces: el bucle
            # sí/no ineludible del bug 3.3.
            _actualizar_estado_json(numero_usuario, confirmar_regeneracion=0)
            en_creacion = False
            log(numero_usuario, "REGEN",
                "el atleta preguntó otra cosa; se cancela la confirmación y se atiende su duda")
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
            # v11 — La meta ya guardada NO está en 'faltantes', así que _parsear_
            # campos_anclados ni la miraba: cuando el atleta la corregía ("en
            # realidad powerbuilding") el mensaje se descartaba por no responder
            # al campo pendiente, y el plan se generaba con la meta vieja mientras
            # el coach le confirmaba la nueva. Una declaración explícita manda.
            if campo_pendiente != "meta":
                correccion = _corregir_meta_declarada(numero_usuario, mensaje_usuario)
                if correccion:
                    extras = list(extras) + [correccion]

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
    # v6 → v8 — FLUJOS DESTRUCTIVOS (Borrar plan / Cambiar días)
    # v8 FIX ESTRUCTURAL: este bloque completo vivía INDENTADO dentro del `else`
    # de "mensaje no responde al onboarding", donde sus condiciones
    # (`not onboarding_activo and not bloque_onboarding`) eran SIEMPRE falsas:
    # era código muerto y ni el borrado ni el cambio de días se ejecutaban jamás.
    # Reubicado al nivel del orquestador, después de toda la cadena de onboarding.
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
        if AFIRMACION_CORTA_RE.match(texto_puro) or CREACION_FUERTE_RE.search(texto_puro):
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
    # El vocabulario y las reglas viven a nivel de módulo (_es_charla_casual):
    # antes este set se reconstruía en CADA turno y solo era visible desde aquí,
    # así que no había forma de probar el clasificador sin levantar el orquestador
    # entero. Ver services/testbed/test_router_casual.py.
    es_casual = _es_charla_casual(texto_puro)
    if es_casual and _responde_a_una_oferta(numero_usuario, texto_puro):
        es_casual = False
        log(numero_usuario, "MODO", "afirmación a una oferta del coach -> NO casual "
                                    "(necesita herramientas para cumplirla)")

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
        # v13 — "¿hago 4x8 con 60 kg?" parsea como serie perfecta. Preguntar no es
        # reportar: el registro determinista de más abajo la metería en el diario.
        if serie and es_pregunta_no_reporte(mensaje_usuario):
            log(numero_usuario, "SERIE-PREGUNTA",
                f"notación de serie dentro de una PREGUNTA, no se registra: "
                f"{_trunc(mensaje_usuario, 80)}")
            serie = None

    # v8 (Mandato 4) — cinturón extra: si el interceptor de series SÍ ve una serie,
    # el turno jamás puede ser casual (necesita registro y confirmación honesta).
    if serie and es_casual:
        es_casual = False
        log(numero_usuario, "MODO", "serie detectada -> NO casual (forzado por notación)")

    log(numero_usuario, "MODO",
        f"casual={es_casual} onboarding={onboarding_activo or bool(bloque_onboarding)} "
        f"consulta={consulta_rutina} serie={serie}")

    contexto_dinamico = construir_telemetria(
        fecha_actual, hora_actual, estatus, instruccion_de_hoy, dashboard_sql,
        es_casual, estado_gen, estado_hoy["semana"], bloque_onboarding,
        ficha_atleta=("" if (es_casual or onboarding_activo or bloque_onboarding)
                      else _resumen_perfil_humano(numero_usuario))
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
        contenido_mensaje += ("\n\n[SISTEMA — RUTINA SOLICITADA, ya consultada. "
                              "Transcríbela con los kg literales; NO llames herramientas para esto]:\n"
                              + rutina_pedida)

    # ==========================================================================
    # v7 → v8 — REGISTRO DETERMINISTA (serie / peso / sustitución).
    # Python ejecuta la acción con lo que ya parseó; el modelo SOLO confirma.
    # v8 (Mandatos 2 y 3): las escrituras pesadas van a asyncio.to_thread (una
    # escritura SQLite/Chroma síncrona bloqueaba el event loop completo y sumaba
    # segundos al turno), y toda acción real alimenta la contabilidad del turno
    # (acciones_ok / acciones_fallidas) que luego consumen los fallbacks.
    # ==========================================================================
    accion_ejecutada = False
    serie_registrada_python = False
    peso_registrado_python = False
    sustitucion_hecha_python = False
    equipo_registrado_python = False
    acciones_ok: list[str] = []          # v8: escrituras COMPLETADAS de verdad
    acciones_fallidas: list[str] = []    # v8: errores/candados/timeouts del turno
    # v12: hechos del turno que NO son ni éxito ni fallo (p. ej. "faltan datos
    # para el análisis") y que los fallbacks necesitan para no inventarse la causa.
    notas_turno: list[str] = []
    texto_tools_acumulado = ""           # v8: payloads de tools -> whitelist de kg

    # --- RECTIFICACIÓN DE LA ÚLTIMA SERIE (v13) ---
    # Antes de intentar registrar nada: si el atleta viene a corregirse o a
    # retirar una serie, lo que toca es UPDATE/DELETE, no un INSERT nuevo.
    rectificacion = await asyncio.to_thread(_rectificar_ultima_serie, numero_usuario, mensaje_usuario)
    if rectificacion:
        accion_ejecutada = True
        acciones_ok.append(rectificacion["resumen"])
        contenido_mensaje += "\n\n[SISTEMA — " + rectificacion["bloque"]
        log(numero_usuario, rectificacion["etapa"], rectificacion["resumen"])
        serie = None                      # ya está resuelto: no registrar de nuevo

    # --- SERIE(S) ---
    if serie and not rutina_vacia:
        series_todas = detectar_series_multiples(mensaje_usuario)
        ej_turno, ej_heredado = _ejercicio_de_la_serie(numero_usuario, mensaje_usuario)
        # Una marca de la que PRESUME no es una serie que acaba de hacer: actualiza
        # su 1RM, pero no entra en el diario de entrenamiento con la fecha de hoy.
        es_marca_historica = bool(_MARCA_HISTORICA_RE.search(mensaje_usuario))
        if not series_todas:
            series_todas = [{"peso": serie["peso"], "reps": serie["reps"], "rpe": serie.get("rpe"),
                             "ejercicio": ej_turno}]
        registradas, pendientes = [], []
        heredadas = []
        for s in series_todas:
            ej = s.get("ejercicio") or ej_turno
            if ej and not s.get("ejercicio") and ej_heredado:
                heredadas.append(ej)
            if not ej:
                # El RPE viaja en el aviso: sin él, el atleta responde de qué
                # ejercicio era y el esfuerzo que ya había contado se pierde,
                # obligándole a repetirlo.
                pendientes.append(f"{s['peso']:g} kg x {s['reps']}"
                                  + (f" @RPE {s['rpe']:g}" if s.get("rpe") else ""))
                continue
            try:
                if not es_marca_historica:
                    # v9: Se inyecta el rpe_reportado para que vaya directo a la base de datos
                    await asyncio.to_thread(guardar_serie_sql, numero_usuario,
                                            {"ejercicio": ej, "peso_kg": s["peso"],
                                             "repeticiones": int(s["reps"]),
                                             "rpe_reportado": s.get("rpe")})

                detalle = f"{ej} {s['peso']:g} kg x {int(s['reps'])}"
                if s.get("rpe"):
                    detalle += f" @RPE {s['rpe']:g}"

                if 1 <= int(s["reps"]) <= 15:
                    rm_val = calcular_1rm(TipoEjercicio("peso_libre"), int(s["reps"]), s["peso"])
                    await asyncio.to_thread(guardar_rm, numero_usuario, ej, rm_val,
                                            peso_origen=s["peso"], reps_origen=int(s["reps"]),
                                            fuente="auto_serie")
                    detalle += f" (1RM estimado {rm_val:g} kg)"
                registradas.append(detalle)
                # Sella el ejercicio en curso (TTL 30 min). Esta guarda existía en
                # handlers_v9 desde v9 pero NADIE la llamaba, así que
                # leer_ejercicio_confirmado devolvía None siempre: ni protegía al
                # handler de que el LLM cambiara el ejercicio, ni permitía heredarlo
                # en la serie siguiente.
                fijar_ejercicio_confirmado(numero_usuario, ej)
            except Exception as e:
                log(numero_usuario, "SERIE-AUTO", f"error: {e}")
                acciones_fallidas.append(f"registro automático de serie ({s['peso']:g}x{s['reps']}): {e}")

        if registradas and es_marca_historica:
            serie_registrada_python = True
            accion_ejecutada = True
            acciones_ok.append("marca histórica: 1RM actualizado (" + "; ".join(registradas) + ")")
            contenido_mensaje += (
                "\n\n[SISTEMA — MARCA ACTUALIZADA]: " + "; ".join(registradas)
                + ". Es una marca de la que habla, NO una serie de la sesión de hoy: se actualizó "
                  "su 1RM y sus cargas futuras, pero NO se apuntó en el diario de entrenamiento de "
                  "hoy. Celébrasela y usa EXACTAMENTE esos números; NO digas que 'quedó registrada "
                  "la serie de hoy'.")
            log(numero_usuario, "MARCA-AUTO", f"1RM actualizado sin tocar el diario: {registradas}")
        elif registradas:
            serie_registrada_python = True
            accion_ejecutada = True
            acciones_ok.append("series registradas: " + "; ".join(registradas))
            contenido_mensaje += ("\n\n[SISTEMA — SERIES YA REGISTRADAS]: " + "; ".join(registradas)
                                  + ". Ya quedaron guardadas. NO llames herramientas para esto; solo "
                                    "confírmaselo con tu tono y un comentario breve. Usa EXACTAMENTE esos números.")
            if heredadas:
                # El atleta no dijo el ejercicio: se dio por hecho que seguía con
                # el anterior. Se registra igual (perder el dato es peor), pero el
                # coach tiene que nombrarlo para que pueda corregirlo en el acto.
                contenido_mensaje += (
                    f" El atleta NO dijo el ejercicio: se asumió que seguía con "
                    f"{', '.join(dict.fromkeys(heredadas))}. Nómbralo al confirmar, "
                    f"en tono natural, para que pueda corregirte si era otro.")
            log(numero_usuario, "SERIE-AUTO",
                f"registradas: {registradas}" + (f" (ejercicio heredado: {heredadas})" if heredadas else ""))

            # RPE de alarma en una serie que acaba de hacer. La regla 10 del prompt
            # manda autorregular el día, pero el bloque de arriba acaba de decirle
            # "NO llames herramientas para esto", así que el coach improvisaba el
            # ajuste EN TEXTO con cifras de su cosecha ("bájale un 3-5 %, quita
            # 1-2 series") — justo lo que <fuente_de_verdad> prohíbe. Aquí se le
            # levanta el veto y se le dan los datos que la herramienta pide, que
            # Python sí conoce: el RPE objetivo y las series del plan.
            duras = [s for s in series_todas
                     if s.get("rpe") and float(s["rpe"]) >= RPE_ALARMA_AUTORREGULAR]
            if duras and not es_marca_historica:
                s_dura = duras[0]
                ej_dura = s_dura.get("ejercicio") or ej_turno
                presc = prescripcion_de_hoy(numero_usuario, estado_hoy["semana"],
                                            estado_hoy["dia"], ej_dura or "")
                # Se ejecuta AQUÍ, no se le pide al modelo. Pedírselo no funciona:
                # con una acción ya ejecutada el turno va sin thinking
                # (solo_redaccion), el modelo no delibera sobre herramientas y, con
                # la invitación delante, se inventó el ajuste imitando el
                # vocabulario del motor ("baja a 78 kg por overshoot") sin llamar
                # nada. Python tiene los cinco datos que la herramienta pide.
                if presc and presc.get("series") and presc.get("rpe_objetivo") not in (None, ""):
                    try:
                        res_auto = await DISPATCHER_V9["autorregular_dia"](numero_usuario, {
                            "ejercicio": ej_dura,
                            "rpe_reportado": float(s_dura["rpe"]),
                            "rpe_objetivo": float(presc["rpe_objetivo"]),
                            "series_restantes": max(0, int(presc["series"]) - 1),
                            "carga_actual_kg": float(s_dura["peso"]),
                        })
                    except Exception as e:
                        res_auto = {"ok": False, "error": str(e)}
                    if res_auto.get("ok"):
                        accion_ejecutada = True
                        acciones_ok.append(f"día autorregulado: {res_auto.get('resumen', '')}")
                        contenido_mensaje += (
                            f"\n\n[SISTEMA — DÍA YA AUTORREGULADO]: RPE {float(s_dura['rpe']):g} en "
                            f"{ej_dura} (objetivo {presc['rpe_objetivo']}). El sistema ya recalculó "
                            f"lo que le queda del día: {json.dumps(res_auto, ensure_ascii=False)}. "
                            "Comunícale ESOS kilos y ESAS series, literales. PROHIBIDO improvisar "
                            "el ajuste: nada de 'bájale un 5 %' ni 'quita un par de series' de tu "
                            "cosecha, y no llames más herramientas para esto.")
                        texto_tools_acumulado += "\n" + json.dumps(res_auto, ensure_ascii=False)
                        log(numero_usuario, "RPE-ALARMA",
                            f"{ej_dura} RPE {s_dura['rpe']} -> autorregulado: {res_auto.get('resumen')}")
                    else:
                        acciones_fallidas.append(
                            f"autorregulación del día ({ej_dura}): {res_auto.get('error', 'fallo interno')}")
                        log(numero_usuario, "RPE-ALARMA", f"{ej_dura}: autorregulación falló: {res_auto}")
                else:
                    # Sin prescripción (ejercicio que no es del día) no hay RPE
                    # objetivo ni series que recortar: se le pide el dato al atleta
                    # en vez de inventarlo.
                    contenido_mensaje += (
                        f"\n\n[SISTEMA — RPE DE ALARMA]: reportó RPE {float(s_dura['rpe']):g} en "
                        f"{ej_dura}, que NO está en la sesión de hoy, así que el sistema no sabe "
                        "cuántas series le quedan ni con qué RPE objetivo. Reconoce el esfuerzo, "
                        "avísale de que ese RPE es alto para una serie de trabajo y pregúntale "
                        "cuántas series le quedan. PROHIBIDO inventar el ajuste tú.")
                    log(numero_usuario, "RPE-ALARMA", f"{ej_dura} RPE {s_dura['rpe']} sin prescripción de hoy")
        if pendientes:
            contenido_mensaje += ("\n\n[SISTEMA]: También reportó series sin decir de qué ejercicio: "
                                  f"{', '.join(pendientes)}. Pregúntale a cuál corresponden ANTES de darlas por hechas.")

    # --- PESO CORPORAL ---
    if not onboarding_activo and not bloque_onboarding and not serie_registrada_python:
        pc = detectar_peso_corporal(texto_puro)
        if pc:
            await asyncio.to_thread(_registrar_peso_corporal, numero_usuario, pc)
            actualizar_perfil(numero_usuario, peso_kg=round(pc, 1))
            peso_registrado_python = True
            accion_ejecutada = True
            acciones_ok.append(f"peso corporal {pc:g} kg registrado")
            contenido_mensaje += (f"\n\n[SISTEMA — PESO YA REGISTRADO]: guardé su peso corporal "
                                  f"de {pc:g} kg y actualicé su ficha. NO llames herramientas para esto; solo "
                                  "confírmaselo brevemente.")
            log(numero_usuario, "PESO-AUTO", f"{pc} kg")

    # --- EQUIPAMIENTO (DISCOS DISPONIBLES) ---
    # Debe correr ANTES de construir la whitelist de kg: registra el incremento
    # real de la barra para que el coach pueda reajustar las cargas del plan a
    # algo montable sin que el validador lo trate como cifra inventada.
    if not onboarding_activo and not bloque_onboarding:
        incremento_nuevo = await asyncio.to_thread(
            _registrar_equipamiento, numero_usuario, texto_puro)
        if incremento_nuevo:
            equipo_registrado_python = True
            accion_ejecutada = True
            acciones_ok.append(f"equipamiento registrado: saltos de {incremento_nuevo:g} kg")
            contenido_mensaje += (
                f"\n\n[SISTEMA — EQUIPAMIENTO REGISTRADO]: el atleta solo puede cargar la "
                f"barra en múltiplos de {incremento_nuevo:g} kg (disco mínimo "
                f"{incremento_nuevo / 2:g} kg por lado). Ya quedó guardado en su ficha y las "
                f"próximas rutinas se redondearán así. Para ESTE turno: reajusta las cargas "
                f"de hoy al múltiplo de {incremento_nuevo:g} kg más cercano (hacia abajo si "
                f"quedan a medio camino) y dile los kg exactos que debe montar. NO llames "
                f"herramientas para esto.")

    # --- SUSTITUCIÓN DE EJERCICIO ---
    if (not onboarding_activo and not bloque_onboarding and not rutina_vacia
            and not serie_registrada_python):
        m_sub = SUSTITUIR_RE.search(mensaje_usuario)
        if m_sub:
            orig = m_sub.group(1).strip(" .,")
            nuevo = m_sub.group(2).strip(" .,")
            plausible = (2 <= len(orig) <= 60 and 2 <= len(nuevo) <= 60
                         and orig.lower() != nuevo.lower())
            # v11 — Mismo agujero que en la tool: este interceptor escribía el
            # override sin comprobar que el ejercicio existiera en el plan.
            orig_real = _resolver_ejercicio_del_plan(numero_usuario, orig) if plausible else None
            if plausible and not orig_real:
                catalogo = _ejercicios_del_plan(numero_usuario)
                contenido_mensaje += (
                    f"\n\n[SISTEMA — SUSTITUCIÓN NO APLICADA]: el atleta pidió cambiar "
                    f"'{orig}', que NO está en su plan. NO se cambió nada y tienes PROHIBIDO "
                    f"decirle que sí. Sus ejercicios son: "
                    f"{', '.join(catalogo) if catalogo else '(plan vacío)'}. Dile que ese "
                    f"movimiento no lo tiene y pregúntale a cuál se refería.")
                acciones_fallidas.append(f"sustitución de '{_trunc(orig, 40)}': no está en el plan")
                log(numero_usuario, "SUSTITUCION-FANTASMA", f"'{orig}' no existe en el plan")
            elif orig_real:
                orig = orig_real
                alcance = "solo_hoy" if re.search(r"solo\s+hoy|s[oó]lo\s+hoy|por\s+hoy",
                                                  texto_puro) else "permanente"
                _guardar_override_ejercicio(numero_usuario, orig, nuevo, alcance)
                await asyncio.to_thread(guardar_dato_en_expediente, numero_usuario,
                                        f"El atleta sustituyó '{orig}' por '{nuevo}' (alcance: {alcance}).")
                sustitucion_hecha_python = True
                accion_ejecutada = True
                acciones_ok.append(f"sustitución aplicada: '{orig}' -> '{nuevo}' ({alcance})")
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
        await asyncio.to_thread(
            guardar_dato_en_expediente, numero_usuario,
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

    # v11 (Opción B) — la telemetría se concatena Y SE PERSISTE. Ver el bloque de
    # persistencia más abajo: NO se poda. Es lo que hace que el prompt del turno N
    # sea prefijo EXACTO del turno N+1 y la caché de Ollama acierte.
    contenido_mensaje += contexto_dinamico

    # v3 — herramientas solo cuando pueden hacer falta:
    #  - onboarding: nunca (el sistema recolecta)
    #  - charla casual: nunca (más fluidez, cero tool calls espurias, menos latencia)
    #  - resto: sí
    tools_de_turno = None if (onboarding_activo or bloque_onboarding or es_casual) else mis_herramientas
    log(numero_usuario, "TOOLS", "desactivadas este turno" if not tools_de_turno
        else f"{len(mis_herramientas)} herramientas disponibles")

    # v7 — REFUERZO DE EMISIÓN NATIVA
    # v11 — este bloque NUNCA llegaba al modelo: el `+=` iba DESPUÉS del append al
    # historial, y como las cadenas son inmutables el dict conservaba la versión
    # anterior. Se rebindeaba una variable local que ya no se usaba para nada.
    # Ahora se compone el contenido completo ANTES de guardarlo.
    if tools_de_turno:
        contenido_mensaje += ("\n\n[SISTEMA]: Si decides usar una herramienta, EMÍTELA como "
                              "llamada real (no la escribas como texto ni asumas que ya se ejecutó).")

    historial_memoria[numero_usuario].append({"role": "user", "content": contenido_mensaje})


    # v3 — num_predict dinámico: una charla normal no necesita 2048 tokens
    quiere_semana_completa = bool(consulta_rutina)
    opciones_turno = dict(OPCIONES_QWEN_32B)
    if not quiere_semana_completa:
        opciones_turno["num_predict"] = 768

    # v5 — THINKING SELECTIVO: razona solo cuando el modelo DECIDE (elegir
    # herramientas, sintetizar sus resultados). En onboarding/casual Python ya
    # decidió todo y el modelo solo redacta: pensar ahí es latencia tirada.
    # OJO: los tokens de razonamiento consumen num_predict -> presupuesto amplio.
    # v10 — el "selectivo" de v5 no lo era tanto: tools_de_turno is not None es
    # cierto en casi todos los turnos reales, así que el modelo razonaba (~1.950
    # tok, ~50 s medidos) incluso cuando Python ya había ejecutado la acción y le
    # había inyectado el resultado. Esos turnos son REDACCIÓN, no decisión.
    # Se excluye el fin de sesión a propósito: ahí, aunque Python haya registrado
    # la serie, el modelo todavía tiene que decidir si llama a avanzar_dia, y sin
    # razonar falla ese caso (medido).
    solo_redaccion = (not PENSAR_AUNQUE_PYTHON_RESUELVA
                      and (accion_ejecutada or bool(consulta_rutina))
                      and not FIN_SESION_RE.search(texto_puro))
    pensar_turno = (PENSAR_HABILITADO and tools_de_turno is not None
                    and not solo_redaccion)
    if pensar_turno:
        opciones_turno.update(AJUSTES_PENSANDO)
    log(numero_usuario, "THINK",
        "activado este turno" if pensar_turno
        else ("desactivado (Python ya resolvió: solo redacción)" if solo_redaccion
              else "desactivado (solo redacción)"))

    try:
        t_llm = time.monotonic()
        try:
            respuesta = await chat_llm(
                historial_memoria[numero_usuario],
                tools=tools_de_turno,
                opciones=opciones_turno,
                pensar=pensar_turno,
                quien="principal"
            )
        except asyncio.TimeoutError:
            # v5.2 — el modelo se colgó: un reintento en frío (sin thinking, presupuesto
            # corto) casi siempre sale en <15s y evita el silencio total hacia WhatsApp.
            log(numero_usuario, "TIMEOUT", "llamada principal; reintento sin thinking")
            respuesta = await chat_llm(
                historial_memoria[numero_usuario],
                tools=tools_de_turno,
                opciones={**OPCIONES_QWEN_32B, "num_predict": 768},
                pensar=False,
                quien="principal-retry"
            )

        mensaje_salida = respuesta.get("message", {}) or {}
        texto_final = mensaje_salida.get("content", "") or ""

        # v8 (Mandato 2) — estado del loop de herramientas:
        #   firmas_ejecutadas: dedupe cross-loop (el modelo repetía el MISMO call en
        #   Loop 1 y Loop 2 bajo latencia alta -> filas DUP en SQLite).
        max_loops = 3
        loop_count = 0
        firmas_ejecutadas: set[str] = set()
        mensajes_orquestador = list(historial_memoria[numero_usuario])

        # v8 (Mandato 6) — la síntesis post-tools va SIN thinking (PENSAR_POST_TOOLS):
        # con think=True el razonamiento consumía casi todo num_predict a ~30 tok/s
        # (los 64s del reporte) y el contenido llegaba mutilado (done_reason=length).
        post_opts = {**OPCIONES_QWEN_32B,
                     "num_predict": 2048 if quiere_semana_completa else 1024}

        while tools_de_turno and loop_count < max_loops:
            calls = mensaje_salida.get("tool_calls") or []
            if not calls:
                # v7 — RESCATE: el modelo a veces escribe la tool call como TEXTO
                rescatadas = _extraer_tools_de_texto(texto_final)
                if rescatadas:
                    log(numero_usuario, "TOOL-RESCATE",
                        f"{len(rescatadas)} call(s) recuperadas del texto")
                    calls = rescatadas
                else:
                    break
            loop_count += 1

            # v8 (Mandato 2) — dedupe INTRA-BATCH: dos calls idénticas en el mismo
            # batch se colapsan a una antes de tocar la base de datos.
            calls_filtradas: list[tuple] = []
            firmas_batch: set[str] = set()
            for tool in calls:
                n_t, a_t = _extraer_tool(tool)
                # La firma se calcula sobre los args YA normalizados: dos calls que
                # solo difieren en el alias del argumento ('dato' vs 'dato_a_guardar')
                # son la misma escritura y deben deduplicarse como tal.
                n_t = _normalizar_nombre_tool(n_t)
                a_t = _normalizar_args_tool(n_t, a_t)
                firma = f"{n_t}|{json.dumps(a_t, sort_keys=True, ensure_ascii=False)}"
                if firma in firmas_batch:
                    log(numero_usuario, "TOOL-DUP", f"call duplicada en el batch omitida: {_trunc(firma, 100)}")
                    continue
                firmas_batch.add(firma)
                calls_filtradas.append((tool, firma))

            resultados_texto: list[str] = []
            necesita_reinvocacion = False

            for tool, firma in calls_filtradas:
                nombre, args = _extraer_tool(tool)
                nombre = _normalizar_nombre_tool(nombre, numero_usuario)
                args = _normalizar_args_tool(nombre, args, numero_usuario)

                # v8 (Mandato 2) — dedupe CROSS-LOOP: una escritura ya ejecutada en
                # una vuelta anterior de ESTE turno no se repite jamás.
                if firma in firmas_ejecutadas and nombre in TOOLS_ESCRITURA:
                    resultados_texto.append(f"[{nombre}]: ya se ejecutó en este turno con esos mismos "
                                            "datos; NO se repitió (anti-duplicados).")
                    log(numero_usuario, "TOOL-DUP", f"{nombre} repetida cross-loop; omitida")
                    necesita_reinvocacion = True
                    continue
                firmas_ejecutadas.add(firma)
                log(numero_usuario, "TOOL",
                    f"{nombre}({_trunc(json.dumps(args, ensure_ascii=False), 150)}) [loop {loop_count}]")

                try:
                    if nombre == "generar_rutina":
                        res = await _handle_generar_rutina(args, numero_usuario, texto_puro)
                        resultados_texto.append(f"[generar_rutina]: {res}")
                        if "[ERROR" in res or "Faltan" in res or "Ya hay" in res:
                            acciones_fallidas.append(f"generar_rutina: {_trunc(res, 110)}")
                        else:
                            acciones_ok.append("generación de plan disparada en segundo plano")
                        necesita_reinvocacion = True

                    elif nombre == "consultar_rutina_futura":
                        sem = int(_num_seguro(args.get("semana"), estado_hoy["semana"]) or estado_hoy["semana"])
                        dia_v = _num_seguro(args.get("dia"))
                        dia_num = int(dia_v) if dia_v else None
                        res = obtener_rutina_del_json(numero_usuario, sem, dia_num,
                                                      solo_hoy=dia_num is not None)
                        resultados_texto.append(
                            f"[consultar_rutina_futura semana {sem}"
                            f"{' día ' + str(dia_num) if dia_num else ''}]:\n{res}")
                        necesita_reinvocacion = True

                    elif nombre == "avanzar_dia":
                        if estado_hoy.get("plan_pausado"):
                            resultados_texto.append("[avanzar_dia]: el plan está PAUSADO; NO se avanzó. "
                                                    "El atleta debe reanudarlo primero.")
                            notas_turno.append("El plan sigue pausado, así que el día no avanzó; "
                                               "hay que reanudarlo primero.")
                        else:
                            forzado = _bool_seguro(args.get("forzar"))
                            avance = avanzar_estado_usuario(numero_usuario, forzar=forzado)
                            if avance.get("ok"):
                                estado_hoy = obtener_estado_usuario(numero_usuario)
                                resultados_texto.append(
                                    f"[avanzar_dia]: calendario avanzado a semana {avance['semana']}, "
                                    f"día {avance['dia']}"
                                    f"{' (avance FORZADO, segundo de hoy)' if forzado else ''}. "
                                    f"Próximo entreno: {fecha_humana(avance['fecha_programada'])}.")
                                acciones_ok.append(
                                    f"día avanzado a semana {avance['semana']}, día {avance['dia']}")
                            elif avance.get("neutro"):
                                # Ni éxito ni avería: hay que CONTÁRSELO, no pedir perdón.
                                humano = ("Hoy ya cerraste tu sesión, así que el calendario no "
                                          "se mueve otra vez: el siguiente día entra mañana."
                                          if "CANDADO" in str(avance.get("motivo"))
                                          else "Se acabaron las semanas generadas del plan: no "
                                               "hay un día siguiente al que avanzar.")
                                resultados_texto.append(f"[avanzar_dia]: {humano}")
                                notas_turno.append(humano)
                            else:
                                resultados_texto.append(f"[avanzar_dia]: {avance.get('motivo', 'no se pudo avanzar')}")
                                acciones_fallidas.append(
                                    f"avanzar_dia: {_trunc(avance.get('motivo', 'bloqueado'), 90)}")
                        necesita_reinvocacion = True

                    elif nombre == "calcular_rm_exacto":
                        peso = _num_seguro(args.get("peso_levantado"))
                        reps = _num_seguro(args.get("repeticiones"))
                        ej = (str(args.get("nombre_ejercicio") or "").strip()
                              or _inferir_ejercicio_serie(texto_puro) or "press banco plano")
                        # v11 — 'tipo_ejercicio' lleva desde siempre en el schema con
                        # sus 4 valores y se descartaba: TODO se calculaba como peso
                        # libre. Unas dominadas con 30 kg de lastre daban un 1RM de
                        # ~32 kg, ignorando los ~85 kg de peso corporal que el atleta
                        # también mueve, y ese número entraba a la base como su marca.
                        try:
                            tipo = TipoEjercicio(str(args.get("tipo_ejercicio")
                                                     or "peso_libre").strip().lower())
                        except ValueError:
                            tipo = TipoEjercicio.PESO_LIBRE
                        es_calistenia = tipo is TipoEjercicio.CALISTENIA_PURA
                        if es_calistenia and peso is None:
                            peso = 0.0          # solo peso corporal: no hay lastre que reportar
                        motivos = []
                        if peso is None or (peso <= 0 and not es_calistenia):
                            motivos.append(f"peso={args.get('peso_levantado')!r}")
                        if not reps or not (1 <= int(reps) <= 15):
                            motivos.append(f"reps={args.get('repeticiones')!r} (rango confiable 1-15)")
                        if motivos:
                            resultados_texto.append(
                                f"[calcular_rm_exacto]: datos inválidos ({'; '.join(motivos)}). "
                                "NO se calculó ni guardó nada.")
                            acciones_fallidas.append("calcular_rm_exacto: datos fuera de rango, no se guardó")
                        else:
                            pc = _num_seguro(cargar_perfil(numero_usuario).get("peso_kg"), 85.0) or 85.0
                            bruto = calcular_1rm(tipo, int(reps), float(peso), peso_corporal=float(pc))
                            # lastrado y calistenia devuelven un dict (absoluto + lastre);
                            # se guarda el kilaje COMPARABLE con lo que el atleta carga,
                            # y el absoluto solo se le cuenta como contexto.
                            if isinstance(bruto, dict):
                                rm = float(bruto.get("1rm_solo_lastre_kg",
                                                     bruto.get("lastre_sugerido_para_1rm_kg", 0.0)))
                                absoluto = bruto.get("1rm_absoluto_kg",
                                                     bruto.get("1rm_fuerza_total_kg"))
                                detalle = (f" [fuerza total con su peso corporal de {float(pc):g} kg: "
                                           f"{float(absoluto):g} kg]")
                            else:
                                rm, detalle = float(bruto), ""
                            # v8 (Mandato 2): escritura fuera del event loop
                            await asyncio.to_thread(guardar_rm, numero_usuario, ej, rm,
                                                    peso_origen=float(peso), reps_origen=int(reps),
                                                    fuente="tool")
                            resultados_texto.append(
                                f"[calcular_rm_exacto]: {ej} ({tipo.value}) -> 1RM estimado "
                                f"{rm:g} kg (a partir de {float(peso):g} kg x {int(reps)})"
                                f"{detalle}. Ya quedó guardado.")
                            acciones_ok.append(f"1RM de {ej} guardado: {rm:g} kg")
                        necesita_reinvocacion = True


                    elif nombre in DISPATCHER_V9:

                        # Si es la tool de registrar serie y Python ya la agarró por regex, saltamos para no duplicar

                        if nombre == "registrar_serie_completada" and serie_registrada_python:

                            resultados_texto.append("[registrar_serie_completada]: la serie YA la registró el "

                                                    "sistema en este turno determinísticamente; NO se duplicó.")

                        else:

                            # Ejecutamos la herramienta V9 (viaja asíncrono y en thread seguro)

                            res_v9 = await DISPATCHER_V9[nombre](numero_usuario, args)

                            # El resultado viaja directo como JSON string al LLM

                            resultados_texto.append(f"[{nombre}]: {json.dumps(res_v9, ensure_ascii=False)}")

                            # v12 — 'datos_insuficientes' NO es una avería: es que
                            # el atleta todavía no ha entrenado lo bastante para
                            # que el análisis tenga sentido. Sin decírselo, el
                            # modelo lo redactaba como fallo técnico ("no pude
                            # verificar tus números, ¿lo intentamos otra vez?") e
                            # invitaba a reintentar algo que va a fallar igual
                            # hasta que haya más sesiones registradas.
                            if res_v9.get("error") == "datos_insuficientes" or \
                                    res_v9.get("veredicto") == "datos_insuficientes":
                                aviso_insuf = (
                                    f"'{nombre}' no falló: devolvió que aún NO hay suficientes "
                                    "sesiones registradas para que el análisis signifique algo. "
                                    "Díselo así, como coach — que todavía te falta historial suyo "
                                    "para medirlo con rigor, qué necesitas de él (que siga "
                                    "reportando sus series con peso, reps y RPE) y qué hacéis "
                                    "mientras tanto. PROHIBIDO hablar de fallos técnicos, de que "
                                    "'no pudiste verificar' ni ofrecerle reintentarlo.")
                                resultados_texto.append(f"[SISTEMA]: {aviso_insuf}")
                                # También por el canal que sobrevive a la reescritura:
                                # resultados_texto no llega a los fallbacks.
                                notas_turno.append(aviso_insuf)

                            # Engranaje con tu sistema de honestidad v8

                            if res_v9.get("ok"):

                                if nombre in ("registrar_serie_completada", "autorregular_dia",
                                              "enviar_grafica_progreso"):
                                    acciones_ok.append(f"{nombre}: {res_v9.get('resumen', 'ejecutado con éxito')}")

                            else:

                                # Si faltan datos, no lo marcamos como fallo catastrófico, sino como instrucción pendiente
                                # v12: analizar_mrv NO usa la clave 'error' — señala la
                                # falta de historial con 'veredicto': "datos_insuficientes".
                                # Mirando solo 'error', ese caso normalísimo (atleta nuevo,
                                # <4 series por ventana) se contabilizaba como
                                # "analizar_mrv: fallo interno" y el atleta acababa leyendo
                                # una avería inexistente, con el nombre de la herramienta.
                                senal = res_v9.get("error") or res_v9.get("veredicto")
                                if senal not in ("datos_insuficientes", "faltan_datos"):
                                    acciones_fallidas.append(f"{nombre}: {senal or 'fallo interno'}")

                        necesita_reinvocacion = True

                    elif nombre == "sustituir_ejercicio":
                        if sustitucion_hecha_python:
                            resultados_texto.append("[sustituir_ejercicio]: la sustitución YA la aplicó el "
                                                    "sistema en este turno; NO se repitió.")
                        else:
                            orig = str(args.get("ejercicio_original") or "").strip()
                            nuevo = str(args.get("ejercicio_nuevo") or "").strip()
                            motivo = str(args.get("motivo") or "").strip()
                            alcance = ("solo_hoy" if str(args.get("alcance", "")).lower().startswith("solo")
                                       else "permanente")
                            orig_real = (_resolver_ejercicio_del_plan(numero_usuario, orig)
                                         if orig else None)
                            if not orig or not nuevo:
                                resultados_texto.append("[sustituir_ejercicio]: faltan el ejercicio original "
                                                        "y/o el nuevo; NO se aplicó nada.")
                                acciones_fallidas.append("sustituir_ejercicio: datos incompletos, no se aplicó")
                            elif not orig_real:
                                # v11 — Antes se escribía el override igual y el coach
                                # confirmaba un cambio que no tocaba una sola línea del plan.
                                catalogo = _ejercicios_del_plan(numero_usuario)
                                resultados_texto.append(
                                    f"[sustituir_ejercicio]: '{orig}' NO está en el plan de este atleta, "
                                    f"así que NO se cambió nada. Sus ejercicios son: "
                                    f"{', '.join(catalogo) if catalogo else '(el plan está vacío)'}. "
                                    f"Dile con naturalidad que ese movimiento no lo tiene en el plan y "
                                    f"pregúntale a cuál de los suyos se refería.")
                                acciones_fallidas.append(
                                    f"sustituir_ejercicio: '{_trunc(orig, 40)}' no está en el plan, no se aplicó")
                                log(numero_usuario, "SUSTITUCION-FANTASMA",
                                    f"'{orig}' no existe en el plan; no se aplicó")
                            else:
                                orig = orig_real
                                _guardar_override_ejercicio(numero_usuario, orig, nuevo, alcance)
                                # El motivo es required en el schema y se descartaba: sin
                                # él, el expediente no explica POR QUÉ dejó de hacer el
                                # ejercicio, que es justo lo que hay que recordar meses
                                # después ('dolor_hombro' no es lo mismo que 'preferencia').
                                await asyncio.to_thread(  # v8 (Mandato 2): Chroma en thread
                                    guardar_dato_en_expediente, numero_usuario,
                                    f"Sustitución vía coach: '{orig}' -> '{nuevo}' ({alcance})"
                                    f"{'; motivo: ' + motivo if motivo else ''}.")
                                resultados_texto.append(
                                    f"[sustituir_ejercicio]: aplicado '{orig}' -> '{nuevo}' ({alcance}), "
                                    "manteniendo series, reps y cargas.")
                                acciones_ok.append(f"sustitución '{orig}' -> '{nuevo}' aplicada")
                        necesita_reinvocacion = True

                    elif nombre == "aplicar_descarga":
                        # v11 — 'semana' era required en el schema y se ignoraba (siempre
                        # descargaba la actual), y la intensidad se leía de un
                        # 'reduccion_intensidad' que el modelo nunca manda: el 10 % por
                        # defecto se aplicaba decidiera lo que decidiera el coach.
                        sem_d = int(_num_seguro(args.get("semana"), estado_hoy["semana"])
                                    or estado_hoy["semana"])
                        v_int = _num_seguro(args.get("reduccion_intensidad_pct"), 10.0)
                        v_ser = _num_seguro(args.get("reduccion_series"), 1)
                        # El schema anuncia 0-40 % y 0-3 series; fuera de ahí la semana
                        # deja de parecerse a lo que el atleta tenía planificado.
                        red_int = min(40.0, max(0.0, float(10.0 if v_int is None else v_int)))
                        red_ser = min(3, max(0, int(1 if v_ser is None else v_ser)))
                        _guardar_descarga(numero_usuario, sem_d, red_int, red_ser)
                        resultados_texto.append(
                            f"[aplicar_descarga]: descarga aplicada a la semana {sem_d}: "
                            f"-{red_int:g}% de intensidad y -{red_ser} serie(s) por ejercicio.")
                        acciones_ok.append(f"descarga aplicada a la semana {sem_d}")
                        necesita_reinvocacion = True

                    elif nombre == "consultar_progreso":
                        # 'metrica' es required en el schema y se ignoraba: preguntara lo
                        # que preguntara el atleta, se le devolvían fuerza Y peso corporal.
                        metrica = str(args.get("metrica") or "").strip().lower()
                        bloques = []
                        if metrica != "peso_corporal":
                            bloques.append(_resumen_progreso_rm(numero_usuario, args.get("ejercicio")))
                        if metrica != "1rm":
                            bloques.append(_resumen_progreso_peso(numero_usuario))
                        res = "\n".join(b for b in bloques if b) or "Sin historial de progreso todavía."
                        resultados_texto.append(f"[consultar_progreso]:\n{res}")
                        necesita_reinvocacion = True

                    elif nombre == "explicar_tecnica_ejercicio":
                        ej = str(args.get("ejercicio") or "").strip() or texto_puro
                        res = await asyncio.to_thread(  # v8 (Mandato 2): Chroma en thread
                            consultar_literatura_cientifica, f"técnica correcta de {ej}")
                        resultados_texto.append(f"[explicar_tecnica_ejercicio '{_trunc(ej, 50)}']:\n{res}")
                        necesita_reinvocacion = True

                    elif nombre == "registrar_peso_corporal":
                        if peso_registrado_python:
                            resultados_texto.append("[registrar_peso_corporal]: el peso YA lo registró el "
                                                    "sistema en este turno; NO se duplicó.")
                        else:
                            pc = _num_seguro(args.get("peso_kg") or args.get("peso"))
                            if pc and 35 <= pc <= 300:
                                await asyncio.to_thread(_registrar_peso_corporal, numero_usuario, float(pc))
                                actualizar_perfil(numero_usuario, peso_kg=round(float(pc), 1))
                                resultados_texto.append(f"[registrar_peso_corporal]: {float(pc):g} kg "
                                                        "guardado y ficha actualizada.")
                                acciones_ok.append(f"peso corporal {float(pc):g} kg guardado")
                            else:
                                resultados_texto.append(f"[registrar_peso_corporal]: valor inválido "
                                                        f"({args.get('peso_kg')!r}); NO se guardó.")
                                acciones_fallidas.append("registrar_peso_corporal: valor inválido, no se guardó")
                        necesita_reinvocacion = True

                    elif nombre == "pausar_plan":
                        motivo_pausa = str(args.get("motivo") or "").strip()
                        _actualizar_estado_json(numero_usuario, plan_pausado=1,
                                                motivo_pausa=motivo_pausa)
                        estado_hoy["plan_pausado"] = 1
                        if motivo_pausa:
                            # Saber que paró por lesión y no por viaje cambia cómo se
                            # retoma el plan cuando vuelva.
                            await asyncio.to_thread(
                                guardar_dato_en_expediente, numero_usuario,
                                f"El atleta pausó su plan el {fecha_actual}; motivo: {motivo_pausa}.")
                        resultados_texto.append(
                            f"[pausar_plan]: plan PAUSADO"
                            f"{' (' + motivo_pausa + ')' if motivo_pausa else ''}; el calendario "
                            "no avanzará hasta que lo reanude.")
                        acciones_ok.append("plan pausado")
                        necesita_reinvocacion = True

                    elif nombre == "reanudar_plan":
                        _actualizar_estado_json(numero_usuario, plan_pausado=0,
                                                fecha_programada=fecha_actual)
                        estado_hoy["plan_pausado"] = 0
                        resultados_texto.append(f"[reanudar_plan]: plan REANUDADO; próximo entreno "
                                                f"programado hoy ({fecha_actual}).")
                        acciones_ok.append("plan reanudado")
                        necesita_reinvocacion = True

                    elif nombre == "guardar_en_expediente_personal":
                        # v11 — leía 'dato'/'texto'/'informacion' y el schema pide
                        # 'dato_a_guardar': el modelo mandaba bien el argumento, aquí no
                        # se encontraba nunca y se caía al else. La memoria permanente
                        # del atleta llevaba sin escribirse una sola vez por esta vía,
                        # mientras el coach le confirmaba que lo tenía apuntado.
                        dato = str(args.get("dato_a_guardar") or "").strip()
                        if dato:
                            await asyncio.to_thread(guardar_dato_en_expediente, numero_usuario, dato)
                            resultados_texto.append(f"[guardar_en_expediente_personal]: anotado: {_trunc(dato, 110)}")
                            acciones_ok.append("dato guardado en su expediente")
                        else:
                            resultados_texto.append("[guardar_en_expediente_personal]: no venía ningún dato; "
                                                    "NO se guardó nada. No le digas que lo apuntaste.")
                            acciones_fallidas.append("guardar_en_expediente_personal: sin dato, no se guardó")
                        necesita_reinvocacion = True

                    elif nombre == "consultar_expediente_personal":
                        concepto = str(args.get("concepto_a_buscar") or "").strip() or texto_puro
                        res = await asyncio.to_thread(consultar_expediente_atleta, numero_usuario, concepto)
                        resultados_texto.append(f"[consultar_expediente_personal '{_trunc(concepto, 50)}']:\n{res}")
                        necesita_reinvocacion = True

                    elif nombre == "consultar_historial_narrativo":
                        concepto = str(args.get("contexto_a_buscar") or "").strip() or texto_puro
                        res = await asyncio.to_thread(consultar_memoria_historica_atleta,
                                                      numero_usuario, concepto)
                        resultados_texto.append(f"[consultar_historial_narrativo '{_trunc(concepto, 50)}']:\n{res}")
                        necesita_reinvocacion = True

                    elif nombre == "consultar_libro_cientifico":
                        tema = str(args.get("pregunta_teorica") or "").strip() or texto_puro
                        res = await asyncio.to_thread(consultar_literatura_cientifica, tema)
                        resultados_texto.append(f"[consultar_libro_cientifico '{_trunc(tema, 50)}']:\n{res}")
                        necesita_reinvocacion = True

                    elif nombre == "consultar_volumen_plan":
                        # Sin semana explícita manda la actual: el atleta pregunta
                        # por "su" volumen, no por el de una semana cualquiera.
                        sem_v = _num_seguro(args.get("semana"), estado_hoy.get("semana", 1))
                        grupo_v = str(args.get("grupo_muscular") or "").strip() or None
                        res = await asyncio.to_thread(obtener_volumen_semanal_plan,
                                                      numero_usuario, int(sem_v), grupo_v)
                        resultados_texto.append(
                            f"[consultar_volumen_plan semana={int(sem_v)} grupo={grupo_v or 'todos'}]:\n{res}\n"
                            "Cita estas cifras TAL CUAL; son las de su plan real. No las recalcules.")
                        texto_tools_acumulado += "\n" + res
                        necesita_reinvocacion = True

                    elif nombre == "evaluar_perfil_fisico":
                        p = cargar_perfil(numero_usuario)
                        datos_eval = {
                            "peso_kg": _num_seguro(args.get("peso_kg"), p.get("peso_kg")),
                            "altura_m": _num_seguro(args.get("altura_m"), p.get("altura_m")),
                            "edad": int(_num_seguro(args.get("edad"), p.get("edad")) or 0) or None,
                            "genero": args.get("genero") or p.get("genero"),
                            "porcentaje_grasa": _num_seguro(args.get("porcentaje_grasa"),
                                                            p.get("porcentaje_grasa")),
                        }
                        try:
                            res = evaluar_perfil_completo(datos_eval)
                        except Exception as e_perf:
                            res = f"No se pudo evaluar el perfil: {e_perf}"
                        resultados_texto.append(f"[evaluar_perfil_fisico]:\n{res}")
                        necesita_reinvocacion = True

                    elif nombre == "evaluar_nivel_fuerza":
                        p = cargar_perfil(numero_usuario)
                        r = cargar_rms(numero_usuario)
                        res = evaluar_nivel_fuerza({
                            "peso_kg": _num_seguro(args.get("peso_kg"), p.get("peso_kg", 80)),
                            "rm_banca_kg": _num_seguro(args.get("rm_banca_kg"),
                                                       r.get("press banco plano", 0)),
                            "rm_sentadilla_kg": _num_seguro(args.get("rm_sentadilla_kg"),
                                                            r.get("sentadilla", 0)),
                            "rm_peso_muerto_kg": _num_seguro(args.get("rm_peso_muerto_kg"),
                                                             r.get("peso muerto", 0)),
                            "genero": args.get("genero") or p.get("genero", "hombre"),
                        })
                        resultados_texto.append(f"[evaluar_nivel_fuerza]:\n{res['reporte_texto']}")
                        necesita_reinvocacion = True

                    elif nombre == "actualizar_1rm":
                        resultados_texto.append("[actualizar_1rm]: los 1RM NO se editan a mano; el sistema "
                                                "los recalcula con cada serie registrada. Si el atleta reportó "
                                                "una marca (peso x reps), usa calcular_rm_exacto.")
                        necesita_reinvocacion = True

                    else:
                        resultados_texto.append(f"[{nombre}]: herramienta DESCONOCIDA para el sistema; no "
                                                "existe. NO afirmes haberla usado.")
                        log(numero_usuario, "TOOL-DESCONOCIDA", nombre)
                        necesita_reinvocacion = True

                except Exception as e_tool:
                    resultados_texto.append(f"[{nombre}]: ERROR del sistema — {e_tool}. La acción NO se completó.")
                    acciones_fallidas.append(f"{nombre}: {_trunc(str(e_tool), 90)}")  # v8 (Mandato 3)
                    log(numero_usuario, "TOOL-ERROR", f"{nombre}: {e_tool}")
                    necesita_reinvocacion = True

            if not (resultados_texto and necesita_reinvocacion):
                break

            bloque_resultados = "\n".join(resultados_texto)
            # v8 (Mandato 1): los payloads REALES de las tools alimentan la whitelist
            # de kilajes del turno (antes 135 kg de 'semana 2' se marcaba inventado).
            texto_tools_acumulado += "\n" + bloque_resultados

            mensajes_orquestador.append({"role": "assistant",
                                         "content": texto_final or "",
                                         "tool_calls": calls})
            mensajes_orquestador.append({
                "role": "user",
                "content": ("[TOOL-RESULT — datos REALES ya calculados. Redacta tu "
                            "respuesta final para el atleta usando EXACTAMENTE estos datos, sin "
                            "inventar nada]:\n" + bloque_resultados)})

            ultima_vuelta = loop_count >= max_loops - 1
            try:
                respuesta2 = await chat_llm(
                    mensajes_orquestador,
                    # v8 (Mandato 2): en la última vuelta tools=None — el modelo ya no
                    # puede re-emitir calls y queda OBLIGADO a sintetizar texto.
                    tools=None if ultima_vuelta else tools_de_turno,
                    opciones=post_opts,
                    pensar=PENSAR_POST_TOOLS,   # v8 (Mandato 6): False por defecto
                    quien=f"post-tools-{loop_count}"
                )
            except asyncio.TimeoutError:
                # v8 (Mandato 3): el timeout deja huella para el fallback honesto
                acciones_fallidas.append("síntesis post-herramientas: timeout del modelo")
                log(numero_usuario, "TOOL-LOOP", "timeout en síntesis; se sale a fallback honesto")
                texto_final = ""
                break
            mensaje_salida = respuesta2.get("message", {}) or {}
            texto_final = mensaje_salida.get("content", "") or texto_final

        # v4.1 — tool call ESCAPADA como texto con las tools APAGADAS
        if not tools_de_turno and texto_final and ('"name"' in texto_final
                                                   or "tool_call" in texto_final.lower()):
            n_esc, _a_esc = extraer_tool_call_escapado(texto_final)
            if n_esc:
                log(numero_usuario, "TOOL-ESCAPADA", f"'{n_esc}' con tools apagadas; se fuerza redacción")
                texto_final = await _forzar_respuesta_texto(
                    historial_memoria[numero_usuario],
                    "Responde al atleta SOLO con texto natural. Las herramientas están desactivadas "
                    "en este turno: NO escribas JSON ni llamadas a funciones.")

        # v8 (Mandato 3) — FALLBACK CON CONTEXTO REAL: antes la instrucción era
        # genérica ('quedó a medias') y el modelo ASUMÍA éxito -> '¡Listo! Serie
        # anotada ✅' con la base de datos vacía. Ahora viaja el estado verdadero.
        if not texto_final.strip():
            log(numero_usuario, "FALLBACK", "respuesta vacía tras el loop")
            texto_final = await _forzar_respuesta_texto(
                historial_memoria[numero_usuario],
                _instruccion_fallback("Tu respuesta anterior llegó vacía.",
                                      acciones_ok, acciones_fallidas, notas_turno))
        if not texto_final.strip():
            texto_final = "Se me trabó el sistema un segundo bro 🙏 Mándame eso de nuevo porfa."

        texto_final = limpiar_texto_para_whatsapp(texto_final)

        # ======================================================================
        # v3 → v8 (Mandato 1) — VALIDADOR DE KILAJES con whitelist DINÁMICA.
        # Antes solo miraba mensaje+rutina+dashboard+RMs: bloqueaba PRs dichos
        # turnos atrás (145), el lastre del perfil (30) y los payloads de tools
        # (135 de semana 2), y colapsaba hasta con un 'siii'. Ahora la whitelist
        # se construye con TODO lo legítimo del turno + historial + repositorio.
        # ======================================================================
        fuentes_turno = "\n".join([str(mensaje_usuario), instruccion_de_hoy,
                                   dashboard_sql, contenido_mensaje])
        whitelist_kgs = _construir_whitelist_kgs(numero_usuario, fuentes_turno,
                                                 historial_memoria[numero_usuario],
                                                 texto_tools_acumulado)
        usuario_trae_cifras = bool(re.search(r"\d", str(mensaje_usuario)))
        # v8 — bypass ACOTADO (pedido del Mandato 1, con cinturón): si el usuario no
        # trae cifras Y la salida no tiene forma de rutina prescrita, el riesgo real
        # es un falso positivo de memoria, no una rutina alucinada -> no se bloquea.
        # Con rutina vacía o salida con forma de prescripción, el chequeo SIEMPRE corre.
        aplicar_chequeo = (usuario_trae_cifras or rutina_vacia
                           or _parece_rutina_inventada(texto_final) >= 2)
        if aplicar_chequeo:
            inventados = _verificar_kgs_salida(texto_final, whitelist_kgs)
            if inventados:
                log(numero_usuario, "KG-INVENTADOS", f"{inventados} no existen en las fuentes")
                texto_re = await _forzar_respuesta_texto(
                    historial_memoria[numero_usuario],
                    f"Tu borrador incluía kilajes que NO existen en ninguna fuente del sistema: "
                    f"{inventados}. Reescríbelo usando SOLO números presentes en la rutina "
                    "calculada, la telemetría, sus marcas guardadas o lo que el atleta escribió. "
                    "Si el dato no existe, dilo con honestidad y pídele el contexto que falte.")
                texto_re = limpiar_texto_para_whatsapp(texto_re)
                if texto_re and not _verificar_kgs_salida(texto_re, whitelist_kgs):
                    texto_final = texto_re
                else:
                    texto_final = ("Bro, prefiero no aventarte cifras al aire 🙏 Dime el ejercicio y "
                                   "a qué sesión te refieres, o pídeme *ver mi rutina* y te paso los "
                                   "kg exactos ya calculados.")
        else:
            log(numero_usuario, "KG-CHECK", "bypass: usuario sin cifras y salida sin forma de rutina")

        # v4.4 — RED ANTI-RUTINA-FANTASMA: salida con forma de plan sin plan real.
        estado_gen_actual = obtener_estado_usuario(numero_usuario).get("estado_generacion", "")
        if (rutina_vacia and estado_gen_actual != "lista"
                and _parece_rutina_inventada(texto_final) >= 2):
            log(numero_usuario, "RUTINA-FANTASMA", "salida con forma de rutina SIN plan real; se reescribe")
            if calcular_faltantes(numero_usuario):
                instr_fant = ("REESCRIBE tu respuesta: NO existe ningún plan generado y NO debes "
                              "inventar ejercicios ni kilajes. Dile con honestidad que el plan real "
                              "aún no está y que primero deben terminar de armar su ficha.")
            else:
                instr_fant = ("REESCRIBE tu respuesta: NO existe ningún plan generado todavía. NO "
                              "inventes ejercicios. Sus datos están completos: el plan real lo "
                              "genera el sistema en 2-3 minutos cuando se dispare.")
            texto_final = limpiar_texto_para_whatsapp(
                await _forzar_respuesta_texto(historial_memoria[numero_usuario], instr_fant)) \
                or "Todavía no tengo tu plan real generado bro; en cuanto esté, te aviso yo 🙏"

        # v3.1 — el coach NUNCA se sale del guion durante el onboarding
        if onboarding_activo and campo_pendiente:
            texto_final, reparado = _reparar_onboarding(texto_final, numero_usuario, campo_pendiente)
            if reparado:
                log(numero_usuario, "ONB-REPARADO", f"salida re-encauzada a '{campo_pendiente}'")

        # ======================================================================
        # v4.4 → v8 (Mandato 3) — GUARDIA CLAIM-FALSO sin bucle infinito.
        # Antes: la reescritura usaba un prompt genérico -> el modelo volvía a
        # 'confirmar' el guardado -> el filtro volvía a saltar -> bucle. Ahora:
        # (1) la condición mira acciones_ok REALES (no un flag genérico),
        # (2) la instrucción de reescritura lleva el detalle exacto de lo que
        #     falló, y (3) si el modelo REINCIDE, responde una plantilla honesta
        #     determinista construida en Python (cero LLM, cero bucle).
        # ======================================================================
        if (not acciones_ok and _CLAIM_ACCION_RE.search(texto_final)
                and (serie or NOTACION_ENTRENO_RE.search(texto_puro)
                     or acciones_fallidas or notas_turno)):
            log(numero_usuario, "CLAIM-FALSO", "afirma una acción NO ejecutada; reescritura honesta")
            texto_honesto = await _forzar_respuesta_texto(
                historial_memoria[numero_usuario],
                _instruccion_fallback(
                    "Tu borrador AFIRMABA haber guardado/registrado/actualizado algo, pero ninguna "
                    "escritura se completó en este turno.",
                    acciones_ok, acciones_fallidas, notas_turno))
            texto_honesto = limpiar_texto_para_whatsapp(texto_honesto)
            if texto_honesto and not _CLAIM_ACCION_RE.search(texto_honesto):
                texto_final = texto_honesto
            else:
                # acciones_fallidas viaja con los nombres CRUDOS de las herramientas
                # ("analizar_mrv: fallo interno"). Esta plantilla se asigna después
                # del limpiado general, así que sin pasarla por el filtro el atleta
                # leía el nombre de la función en WhatsApp — justo lo que prohíbe
                # <secreto_profesional>.
                # El consejo "mándame ejercicio + peso + reps" solo tiene sentido si
                # lo que se quedó sin guardar era una SERIE. Puesto fijo, el atleta
                # que decía "ya terminé el entreno" recibía una plantilla pidiéndole
                # kilos que nadie le había preguntado.
                detalle_f = (" Falló: " + "; ".join(acciones_fallidas[:2]) + ".") if acciones_fallidas else ""
                era_serie = bool(serie or NOTACION_ENTRENO_RE.search(texto_puro))
                cierre = ("Mándamelo así: *ejercicio + peso + reps* (ej: 'banca 100x10') "
                          "y lo registro al toque 💪" if era_serie else
                          "Dame un momento y lo intento otra vez; si sigue igual te aviso.")
                texto_final = limpiar_texto_para_whatsapp(
                    f"Seré honesto contigo bro: eso todavía NO quedó guardado en el "
                    f"sistema.{detalle_f} {cierre}")

        # v12 — GUARDIA DE ESTADO DEL PLAN. Independiente del anterior: aquí no
        # importa si el turno ejecutó algo, sino si lo que el coach AFIRMA sobre
        # el plan coincide con el disco. Es la red que faltaba para las mentiras
        # sin cifras (descarga, pausa, reanudación), que el atleta no puede
        # verificar y que arrastran la conversación entera.
        desmentidos = _claims_plan_desmentidos(numero_usuario, texto_final,
                                               estado_hoy.get("semana", 1), fecha_prog_al_entrar)
        if desmentidos:
            log(numero_usuario, "CLAIM-ESTADO", "; ".join(desmentidos))
            instr_estado = (
                "Tu borrador afirma cambios en el plan que NO existen. La verdad es: "
                + "; ".join(desmentidos) + ". REESCRIBE la respuesta sin dar por hecho "
                "ninguno de esos cambios. Si crees que al atleta le conviene una descarga, "
                "una pausa o un ajuste, PROPÓNSELO y pregúntale si lo aplicas — nunca lo "
                "des por aplicado. Mantén tu tono de coach y no menciones nada técnico.")
            texto_estado = limpiar_texto_para_whatsapp(
                await _forzar_respuesta_texto(historial_memoria[numero_usuario], instr_estado))
            if texto_estado and not _claims_plan_desmentidos(
                    numero_usuario, texto_estado, estado_hoy.get("semana", 1),
                    fecha_prog_al_entrar):
                texto_final = texto_estado
            else:
                # Reincide: plantilla honesta en Python, sin más vueltas al modelo.
                # Se nombra lo que se desmiente para que el atleta sepa qué NO pasó;
                # una disculpa genérica le deja sin saber a qué atenerse.
                texto_final = limpiar_texto_para_whatsapp(
                    "Ojo bro, me adelanté 🙏 Eso todavía NO está hecho: tu plan sigue "
                    "exactamente como lo tenías. Dime si quieres que lo aplique de "
                    "verdad y lo hago al toque.")

        # v4 — anti-eco: respuesta idéntica a la anterior se varía mínimamente
        ultimo_asst = next((m.get("content", "") for m in reversed(historial_memoria[numero_usuario])
                            if m.get("role") == "assistant"), "")
        if texto_final and texto_final.strip() == ultimo_asst.strip():
            log(numero_usuario, "DUPLICADO", "respuesta idéntica a la previa; se añade variación")
            texto_final += "\n\n¿Seguimos bro? 💪"

        historial_memoria[numero_usuario].append({"role": "assistant", "content": texto_final})

        # v11 (Opción B) — LA TELEMETRÍA YA NO SE PODA. Antes se recortaba aquí, y
        # eso hacía que el turno siguiente reconstruyera el mensaje SIN el bloque:
        # el prompt del turno N dejaba de ser prefijo del N+1, Ollama descartaba su
        # único checkpoint (lo graba al final del prompt, o sea DESPUÉS del bloque)
        # y reprocesaba los ~15.000 tokens enteros — 17,6 s por turno medidos.
        #
        # Persistiendo lo mismo que se envió, el historial es un prefijo que solo
        # crece y la caché acierta. Referencia medida en las llamadas 'post-tools',
        # que ya cumplían esa condición: 21.260 tok de prefill en 1,3 s.
        #
        # Coste aceptado: ~600 tok/turno de telemetría acumulada. Quedan DENTRO del
        # prefijo cacheado (se procesan una sola vez) y el compresor los recoge al
        # pasar UMBRAL_COMPRESION. El riesgo es que el modelo cite un calendario
        # viejo: por eso el system prompt avisa de que solo vale el ÚLTIMO bloque.
        #
        # NO volver a podar aquí "para limpiar el historial" sin releer esto:
        # es exactamente el cambio que costaba 17,6 s por turno.
        hist_disco = [{"role": m.get("role"), "content": m.get("content", "")}
                      for m in historial_memoria[numero_usuario]]
        historial_memoria[numero_usuario] = hist_disco
        try:
            async with aiofiles.open(ruta_historial, mode="w", encoding="utf-8") as f:
                await f.write(json.dumps(hist_disco, indent=2, ensure_ascii=False))
            # Firma del archivo que ACABAMOS de escribir: sin esto el turno
            # siguiente vería un mtime distinto y releería el JSON entero.
            _firmas_historial[numero_usuario] = _firma_archivo(ruta_historial)
        except Exception as e_hist:
            log(numero_usuario, "HIST-ERROR", f"no se pudo persistir: {e_hist}")
            _firmas_historial.pop(numero_usuario, None)

        _limpiar_overrides_solo_hoy(numero_usuario)
        log(numero_usuario, "OUT", f"{time.monotonic() - t_inicio:.1f}s -> {_trunc(texto_final, 150)}")
        return texto_final

    except Exception as e:
        import traceback
        traceback.print_exc()
        log(numero_usuario, "ERROR-FATAL", f"{type(e).__name__}: {e}")
        # El mensaje del atleta ya está en memoria pero no hubo respuesta: si se
        # dejara así, el siguiente turno arrancaría con un historial desalineado
        # respecto al disco. Se descarta la memoria y se recarga del archivo.
        historial_memoria.pop(numero_usuario, None)
        _firmas_historial.pop(numero_usuario, None)
        return "Se me cruzaron los cables un segundo bro 🙏 Repíteme eso porfa."

# ==============================================================================
# GENERACIÓN DE RUTINA (handler compartido: pipeline de onboarding y tool call)
# ==============================================================================
async def _handle_generar_rutina(args: dict, numero_usuario: str, texto_puro: str) -> str:
    estado = obtener_estado_usuario(numero_usuario)
    if estado.get("estado_generacion") == "generando":
        return "Ya hay una generación EN CURSO para este atleta; no se disparó otra."
    p = cargar_perfil(numero_usuario)
    r = cargar_rms(numero_usuario)
    datos = {
        "meta": args.get("meta") or p.get("meta") or "powerbuilding",
        "nivel": args.get("nivel") or "intermedio",
        "dias": int(_num_seguro(args.get("dias"), p.get("dias_semana", 4)) or 4),
        "peso_kg": _num_seguro(args.get("peso_kg"), p.get("peso_kg")),
        "porcentaje_grasa": _num_seguro(args.get("porcentaje_grasa"), p.get("porcentaje_grasa")),
        "rm_banca_kg": _num_seguro(args.get("rm_banca_kg"), r.get("press banco plano")),
        "rm_sentadilla_kg": _num_seguro(args.get("rm_sentadilla_kg"), r.get("sentadilla")),
        "rm_peso_muerto_kg": _num_seguro(args.get("rm_peso_muerto_kg"), r.get("peso muerto")),
        "rm_dominadas_kg": _num_seguro(args.get("rm_dominadas_kg"), r.get("dominadas")),
        "contexto_usuario": str(args.get("contexto_usuario") or ""),
        # v11 — 'tipo_periodizacion' es required en el schema y se perdía aquí (y
        # otra vez en el puente): el atleta podía pedir bloques tradicionales y
        # siempre se le generaba DUP, que es el default del motor.
        "tipo_periodizacion": ("tradicional"
                               if str(args.get("tipo_periodizacion") or "").strip().lower()
                               == "tradicional" else "DUP"),
    }
    faltan = [k for k in ("peso_kg", "rm_banca_kg", "rm_sentadilla_kg", "rm_peso_muerto_kg")
              if datos.get(k) in (None, 0)]
    if faltan:
        return f"Faltan datos para generar: {', '.join(faltan)}."
    _actualizar_estado_json(numero_usuario, estado_generacion="generando",
                            generacion_ts=time.time())
    tarea = asyncio.create_task(_generar_rutina_background(datos, numero_usuario))
    _tareas_generacion.add(tarea)                      # referencia fuerte: ver nota
    tarea.add_done_callback(_tareas_generacion.discard)
    log(numero_usuario, "GEN", f"pipeline lanzado en background: {datos}")
    return ("Generación DISPARADA en segundo plano (tarda 2-3 minutos). NO describas "
            "ejercicios todavía; el sistema avisará al atleta cuando esté lista.")

# ==============================================================================
# LECTURA DE RUTINA (kg calculados y redondeados EN CÓDIGO)
# ==============================================================================
def _redondear_carga(kg: float, incremento: float = INCREMENTO_KG_POR_DEFECTO) -> float:
    """Redondea al salto MONTABLE por el atleta. Por defecto 2.5 (discos de 1.25
    por lado); si solo tiene discos de 2.5, el incremento es 5 y prescribir 112.5
    sería pedirle algo que no puede cargar."""
    if not incremento or incremento <= 0:
        incremento = INCREMENTO_KG_POR_DEFECTO
    return round(round(kg / incremento) * incremento, 1)

def prescripcion_de_hoy(numero_usuario: str, semana, dia, ejercicio: str) -> dict | None:
    """Lo que el plan manda HOY para ese ejercicio: series, reps y RPE objetivo.

    Se usa para poder invitar a autorregular con datos reales. Sin esto, ante un
    RPE 9.5 en la primera serie el coach improvisaba el ajuste en texto ("bájale
    un 3-5 % y quita 1-2 series"), cifras que no salen de ninguna parte, en vez
    de llamar a autorregular_dia — que necesita el RPE objetivo y las series
    restantes, y ninguno de los dos estaba en la telemetría.
    """
    sesiones = _sesiones_semana_cache(numero_usuario, semana)
    indice = _normalizar_dia_a_indice(dia)
    if not sesiones or indice is None or indice >= len(sesiones):
        return None
    objetivo = _norm_ej(ejercicio)
    for ej in sesiones[indice].get("Ejercicios", []):
        nombre = str(ej.get("Ejercicio") or ej.get("Nombre") or ej.get("nombre") or "")
        if _norm_ej(nombre) != objetivo:
            continue
        try:
            series = int(ej.get("Series"))
        except (TypeError, ValueError):
            series = None
        return {"ejercicio": nombre, "series": series,
                "reps": ej.get("Reps", ej.get("Repeticiones")),
                "rpe_objetivo": ej.get("RPE")}
    return None


def obtener_volumen_semanal_plan(numero_usuario: str, semana: int,
                                 grupo_muscular: str | None = None) -> str:
    """Series PRESCRITAS por grupo muscular en una semana del plan.

    Existe porque no había ninguna forma de responder "¿cuántas series de pecho
    hago a la semana?" con la verdad: analizar_mrv mide el volumen EJECUTADO
    (y exige ≥4 series por ventana, así que devuelve 'datos_insuficientes' a
    cualquier atleta nuevo), y consultar_rutina_futura devuelve el texto de la
    semana pero contar sus series es justo la aritmética que el coach tiene
    prohibida. Sin fuente, el modelo se lo inventaba: el 2026-07-29 respondió
    "13 series directas de pecho" sin consultar nada — acertó, pero por suerte.

    Cuenta sobre el plan REAL: aplica las sustituciones vigentes (que pueden
    cambiar el grupo muscular) y la reducción de series de una descarga.
    """
    from services.motor_predictivo import grupo_muscular_de

    sesiones = _sesiones_semana_cache(numero_usuario, semana)
    if not sesiones:
        return f"ESTADO: no hay plan generado para la semana {semana}."

    overrides = _cargar_overrides(numero_usuario)
    sustituciones = overrides.get("ejercicios") or {}
    descarga = (overrides.get("descargas") or {}).get(str(int(semana)), {}) or {}
    red_series = int(descarga.get("red_series", 0) or 0)

    objetivo = _norm_ej(grupo_muscular) if grupo_muscular else None
    por_grupo: dict[str, dict] = {}
    for i, ses in enumerate(sesiones, start=1):
        for ej in ses.get("Ejercicios", []):
            nombre = str(ej.get("Ejercicio") or ej.get("Nombre") or ej.get("nombre") or "")
            if not nombre:
                continue
            base = _norm_ej(nombre)
            for o_orig, o_datos in sustituciones.items():
                if _norm_ej(o_orig) != base:
                    continue
                o_new = o_datos.get("nuevo") if isinstance(o_datos, dict) else o_datos
                if o_new:
                    nombre = str(o_new)
                break
            try:
                series = int(ej.get("Series", 0))
            except (TypeError, ValueError):
                continue
            if red_series:
                series = max(1, series - red_series)
            grupo = grupo_muscular_de(nombre) or "sin clasificar"
            b = por_grupo.setdefault(grupo, {"series": 0, "detalle": []})
            b["series"] += series
            b["detalle"].append(f"día {i} · {nombre} {series}x{ej.get('Reps', '?')}")

    if objetivo:
        por_grupo = {g: v for g, v in por_grupo.items() if objetivo in _norm_ej(g)}
        if not por_grupo:
            disponibles = ", ".join(sorted(
                grupo_muscular_de(str(e.get("Ejercicio") or "")) or "sin clasificar"
                for s in sesiones for e in s.get("Ejercicios", [])))
            return (f"No hay series de '{grupo_muscular}' en la semana {semana}. "
                    f"Grupos que sí entrena esa semana: {', '.join(sorted(set(disponibles.split(', '))))}.")

    # El desglose ejercicio a ejercicio solo cuando se pregunta por UN grupo: en
    # la vista general son 10 líneas kilométricas que se comen el contexto sin
    # aportar nada que el atleta haya pedido.
    lineas = [f"VOLUMEN PRESCRITO — semana {semana} "
              f"({len(sesiones)} sesiones){' con DESCARGA aplicada' if red_series else ''}:"]
    for grupo, datos in sorted(por_grupo.items(), key=lambda kv: -kv[1]["series"]):
        detalle = f" ({'; '.join(datos['detalle'])})" if objetivo else ""
        lineas.append(f"  • {grupo}: {datos['series']} series/semana{detalle}")
    return "\n".join(lineas)


def obtener_rutina_del_json(numero_usuario, semana, dia_actual, solo_hoy: bool = False) -> str:
    numero_usuario = str(numero_usuario).strip()
    sesiones = _sesiones_semana_cache(numero_usuario, semana)
    if not sesiones:
        return "ESTADO DE RUTINA: Vacío (no hay plan generado para esa semana)."

    overrides = _cargar_overrides(numero_usuario)
    rms = cargar_rms(numero_usuario)
    incremento = _incremento_barra(numero_usuario)   # discos reales del atleta
    # Las descargas viven en overrides_{numero}.json bajo la clave "descargas": es
    # lo que escribe _guardar_descarga. Antes se leía un descargas_{numero}.json que
    # NADIE creaba nunca, y con el campo equivocado ('red_intensidad' en vez de
    # 'red_intensidad_pct'), así que aplicar_descarga no reducía absolutamente nada.
    descarga = (overrides.get("descargas") or {}).get(str(int(semana)), {}) or {}
    red_int = float(descarga.get("red_intensidad_pct", descarga.get("red_intensidad", 0)) or 0)
    red_ser = int(descarga.get("red_series", 0) or 0)

    indice_hoy = _normalizar_dia_a_indice(dia_actual)
    lineas = []
    for i, ses in enumerate(sesiones, start=1):
        if solo_hoy and indice_hoy is not None and (i - 1) != indice_hoy:
            continue
        encabezado = "HOY — " if (solo_hoy and indice_hoy is not None) else ""
        lineas.append(f"*{encabezado}Semana {semana} · Día {i} · {ses.get('Enfoque', 'Sesión')}*")
        for ej in ses.get("Ejercicios", []):
            # El generador (Entrenamiento.py) escribe la clave "Ejercicio"; leer
            # solo "Nombre" devolvía "?" para TODOS los movimientos del plan.
            # Se aceptan las variantes por si hay planes de formatos anteriores.
            nombre = str(ej.get("Ejercicio")
                         or ej.get("Nombre")
                         or ej.get("nombre")
                         or "?")
            base = _norm_ej(nombre)
            # _cargar_overrides devuelve el JSON completo ({"ejercicios": {...},
            # "descargas": {...}}): iterarlo directo comparaba el nombre del ejercicio
            # contra las claves de primer nivel, así que la sustitución NUNCA se
            # aplicaba. El valor es el dict {"nuevo", "alcance"} de
            # _guardar_override_ejercicio; se tolera el formato plano antiguo.
            for o_orig, o_datos in (overrides.get("ejercicios") or {}).items():
                if _norm_ej(o_orig) != base:
                    continue
                o_new = o_datos.get("nuevo") if isinstance(o_datos, dict) else o_datos
                if o_new:
                    nombre, base = str(o_new), _norm_ej(o_new)
                break
            series = ej.get("Series", "?")
            try:
                if red_ser and str(series).isdigit():
                    series = max(1, int(series) - red_ser)
            except (TypeError, ValueError):
                pass
            reps = ej.get("Reps", ej.get("Repeticiones", "?"))
            # Idem: el generador escribe "1RM" con formato "70.0%". Sin esta
            # variante el plan salía sin cargas en kg, solo series x reps.
            pct = next((ej[k] for k in ("Porcentaje_RM", "%RM", "1RM")
                        if ej.get(k) not in (None, "")), None)
            rpe = ej.get("RPE")
            carga_txt = ""
            if pct not in (None, ""):
                rm_base = None
                for patron, canon in _HEREDA_RM:
                    if patron.match(base):
                        rm_base = rms.get(canon)
                        break
                if rm_base is None:
                    rm_base = rms.get(base)
                try:
                    pct_f = float(str(pct).replace("%", "").replace(",", "."))
                    if red_int:
                        pct_f = max(1.0, pct_f - red_int)
                    if rm_base:
                        carga_txt = (f" — *{_redondear_carga(float(rm_base) * pct_f / 100.0, incremento):g} kg* "
                                     f"({pct_f:g}% RM)")
                    else:
                        carga_txt = f" — {pct_f:g}% RM"
                except (TypeError, ValueError):
                    pass
            rpe_txt = f" @RPE {rpe}" if rpe not in (None, "") else ""
            lineas.append(f"  • {nombre}: {series}x{reps}{carga_txt}{rpe_txt}")
        lineas.append("")
    if not any(l.strip() for l in lineas):
        return "ESTADO DE RUTINA: Vacío (ese día no existe en la semana)."
    if red_int or red_ser:
        lineas.append(f"_Semana con DESCARGA: -{red_int:g}% intensidad, -{red_ser} serie(s)._")
    return "\n".join(lineas).strip()

# ==============================================================================
# ESTADO Y CALENDARIO
# ==============================================================================
def obtener_estado_usuario(numero_usuario: str) -> dict:
    """Estado del atleta. Devuelve SIEMPRE una copia: el orquestador escribe
    sobre el dict que recibe (`estado_hoy['plan_pausado'] = 1`) y no debe poder
    corromper la caché compartida sin pasar por _actualizar_estado_json."""
    estados = _leer_estados()
    est = estados.get(numero_usuario)
    if not est:
        est = {"semana": 1, "dia": 1,
               "fecha_programada": datetime.now().strftime("%Y-%m-%d"),
               "ultima_fecha_completada": ""}
        estados = dict(estados)
        estados[numero_usuario] = est
        _escribir_estados(estados)
    est = dict(est)
    est.setdefault("semana", 1)
    est.setdefault("dia", 1)
    est.setdefault("fecha_programada", datetime.now().strftime("%Y-%m-%d"))
    est.setdefault("ultima_fecha_completada", "")
    return est

def _salto_hasta_siguiente_sesion(numero_usuario: str, est: dict) -> int:
    """Días hasta el próximo entreno, repartiendo las sesiones dentro de la semana.

    Antes era `round(7 / dias_semana)` para TODAS las sesiones, y ese número solo
    cuadra con 1 o 7 días: con 4 días/semana daba saltos de 2 → microciclos de 8
    días (el plan se retrasa 1 día por semana) y con 5 días saltos de 1 → 5 días
    (se adelanta 2 por semana). A las pocas semanas la "Semana 3" del plan caía
    en otra semana del calendario y la fecha que el coach anunciaba ("tu próximo
    entreno es el sábado") dejaba de tener que ver con la realidad.

    Ahora los saltos de una semana SUMAN 7 exactos: con 4 sesiones → 2,2,1,2.
    """
    total = _contar_sesiones_semana(numero_usuario, est.get("semana")) or 0
    if not total:
        total = int(cargar_perfil(numero_usuario).get("dias_semana") or 4)
    total = max(1, min(7, int(total)))
    try:
        i = int(_num_seguro(est.get("dia", 1), 1) or 1) - 1      # sesión completada, 0-based
    except (TypeError, ValueError):
        i = 0
    i %= total
    return max(1, round((i + 1) * 7 / total) - round(i * 7 / total))


def avanzar_estado_usuario(numero_usuario: str, forzar: bool = False) -> dict:
    """Avanza el calendario UN día real del plan. Candado: máx. un avance por fecha.
    `forzar` lo levanta SOLO cuando el atleta insiste tras habérsele avisado (es lo
    que promete el parámetro 'forzar' del schema, que hasta v11 no llegaba aquí).
    Nunca salta el fin del plan: si no hay más semanas generadas, no hay a dónde ir."""
    est = obtener_estado_usuario(numero_usuario)
    hoy = datetime.now().strftime("%Y-%m-%d")
    if est.get("ultima_fecha_completada") == hoy and not forzar:
        # 'neutro': esto NO es una avería. Sin la marca, el orquestador lo metía
        # en acciones_fallidas y el atleta recibía "eso todavía NO quedó guardado
        # en el sistema. Falló: avanzar_dia: CANDADO del día..." por haber
        # terminado su entrenamiento dos veces. Es un hecho que se le cuenta con
        # naturalidad, no un error del que disculparse.
        return {"ok": False, "neutro": True,
                "motivo": ("CANDADO del día: hoy ya se marcó una sesión como completada; el "
                           "siguiente día se desbloquea mañana (evita avances dobles)."),
                "semana": est.get("semana"), "dia": est.get("dia"),
                "fecha_programada": est.get("fecha_programada")}
    semana = int(est.get("semana", 1))
    dia = int(_num_seguro(est.get("dia", 1), 1) or 1)
    total = _contar_sesiones_semana(numero_usuario, semana)
    if total and dia >= total:
        if not _contar_sesiones_semana(numero_usuario, semana + 1):
            _actualizar_estado_json(numero_usuario, ultima_fecha_completada=hoy)
            return {"ok": False, "neutro": True,
                    "motivo": "FIN DEL PLAN: no hay más semanas generadas después de esta.",
                    "semana": semana, "dia": dia,
                    "fecha_programada": est.get("fecha_programada")}
        semana, dia = semana + 1, 1
    else:
        dia += 1
    fecha_prox = (datetime.now() + timedelta(
        days=_salto_hasta_siguiente_sesion(numero_usuario, est))).strftime("%Y-%m-%d")
    _actualizar_estado_json(numero_usuario, semana=semana, dia=dia,
                            fecha_programada=fecha_prox, ultima_fecha_completada=hoy,
                            cursor_semana=semana, cursor_dia=0)
    log(numero_usuario, "AVANZAR", f"-> semana {semana}, día {dia}; próximo {fecha_prox}")
    return {"ok": True, "semana": semana, "dia": dia, "fecha_programada": fecha_prox}

# Arranque de la base analítica (idempotente)
try:
    inicializar_db()
except Exception as e:
    print(f"[SISTEMA] inicializar_db: {e}")