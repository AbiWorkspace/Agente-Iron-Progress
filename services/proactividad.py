# -*- coding: utf-8 -*-
"""
proactividad.py — Iron Progress v9
Tres frentes de UX proactiva:
  1. READINESS matutino: el coach abre la conversación (plantilla aprobada de
     Meta — fuera de la ventana de 24 h es la ÚNICA forma legal de iniciar).
  2. GRÁFICAS: matplotlib → PNG → upload a Cloud API → mensaje de imagen.
     (Nota: la Cloud API NO acepta base64 inline; eso era de la On-Premises
     API ya deprecada. El flujo correcto es media upload → media_id.)
  3. AUDIO: extracción robusta de la tríada [ejercicio, peso, reps @ RPE]
     desde transcripts caóticos de Whisper, con eco de confirmación.

Credenciales: se leen de core.config.settings (WHATSAPP_ACCESS_TOKEN y
PHONE_NUMBER_ID del .env). NO leer os.environ aquí directamente: este módulo
puede importarse antes de load_dotenv() y las credenciales quedarían vacías.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import date, datetime

import matplotlib
matplotlib.use("Agg")                     # sin display: solo PNG a disco
import matplotlib.pyplot as plt
from core.config import  settings


from services import motor_predictivo as mp

GRAPH_URL   = settings.GRAPH_URL
TOKEN       = settings.ACCESS_TOKEN or ""
PHONE_ID    = settings.PHONE_NUMBER_ID or ""
CARPETA_TMP = os.environ.get("IRON_TMP", "/tmp/iron_graficas")
os.makedirs(CARPETA_TMP, exist_ok=True)

if not TOKEN or not PHONE_ID:
    print("[PROACTIVIDAD] ADVERTENCIA: faltan WHATSAPP_ACCESS_TOKEN o "
          "PHONE_NUMBER_ID en el .env. El readiness matutino y las gráficas "
          "fallarán con 401.")


# ==============================================================================
# 1) READINESS MATUTINO
# ==============================================================================
# Plantilla a registrar UNA vez en Meta Business Manager (categoría UTILITY).
# Sin plantilla aprobada, cualquier mensaje iniciado por el negocio fuera de la
# ventana de 24 h falla (el clásico error 131047 de re-engagement).
PLANTILLA_READINESS = {
    "name": "readiness_diario",
    "language": "es_MX",
    "category": "UTILITY",
    "components": [
        {"type": "BODY",
         "text": ("¡Buen día {{1}}! 💪 Antes de armarte la sesión de hoy: "
                  "¿cómo amaneció la máquina?")},
        {"type": "BUTTONS", "buttons": [
            {"type": "QUICK_REPLY", "text": "Al 100 💪"},
            {"type": "QUICK_REPLY", "text": "Normal 😐"},
            {"type": "QUICK_REPLY", "text": "Molido 😮‍💨"},
        ]},
    ],
}

# Textos de botón CON emoji tal como los queremos mostrar. OJO: la plantilla ya
# registrada en Meta los tiene SIN emoji ("Al 100", "Normal", "Molido"), así que
# el webhook recibe el texto pelón. Por eso nada compara contra estas cadenas
# directamente: todo pasa por _norm_boton(), que quita emojis y puntuación.
BOTONES_READINESS = ("Al 100 💪", "Normal 😐", "Molido 😮‍💨")


def _norm_boton(texto: str) -> str:
    """'Al 100 💪' y 'Al 100' colapsan a 'al 100'. Sobrevive a que Meta guarde
    la plantilla con o sin emoji, y a los selectores de variación / ZWJ que
    trae '😮‍💨' (que además rompen las comparaciones ingenuas por igualdad)."""
    t = unicodedata.normalize("NFKC", str(texto or "")).lower()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)   # los emojis no son \w
    return re.sub(r"\s+", " ", t).strip()


# payload del botón (llega en el webhook como button.text / button.payload)
_SCORE_BOTON = {"al 100": 1.0, "normal": 0.75, "molido": 0.45}


def es_boton_readiness(texto: str) -> bool:
    """¿Este texto corresponde a uno de los tres botones del readiness?"""
    return _norm_boton(texto) in _SCORE_BOTON


async def _post_graph(ruta: str, **kwargs) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(f"{GRAPH_URL}/{PHONE_ID}/{ruta}",
                           headers={"Authorization": f"Bearer {TOKEN}"}, **kwargs)
        r.raise_for_status()
        return r.json()


async def enviar_readiness(numero: str, nombre: str = "campeón") -> dict:
    """Dispara la plantilla matutina a UN usuario."""
    return await _post_graph("messages", json={
        "messaging_product": "whatsapp", "to": numero, "type": "template",
        "template": {"name": PLANTILLA_READINESS["name"],
                     "language": {"code": PLANTILLA_READINESS["language"]},
                     "components": [{"type": "body", "parameters": [
                         {"type": "text", "text": nombre}]}]}})


async def broadcast_readiness(usuarios_activos: list[tuple[str, str]]) -> None:
    """(numero, nombre) de cada atleta activo — excluye pausados/onboarding
    desde tu estado_usuarios.json antes de llamar aquí."""
    for numero, nombre in usuarios_activos:
        try:
            await enviar_readiness(numero, nombre)
        except Exception as e:                       # un fallo no tumba el resto
            print(f"[READINESS] fallo con {numero}: {e}")


def iniciar_scheduler(obtener_usuarios_activos, hora: int = 6, minuto: int = 30,
                      tz: str = "America/Mexico_City"):
    """Engancha el cron EN EL MISMO proceso FastAPI (una sola caja: APScheduler
    gana a Celery/cron externo por simplicidad). Llamar desde el lifespan:

        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def lifespan(app):
            sched = iniciar_scheduler(mis_usuarios_activos)
            yield
            sched.shutdown(wait=False)
        app = FastAPI(lifespan=lifespan)

    ⚠️ Con `uvicorn --reload` hay DOS procesos: usa --workers 1 sin reload en
    producción o el broadcast saldrá duplicado.
    `obtener_usuarios_activos` es un callable (sync) -> list[(numero, nombre)].
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    async def _job():
        usuarios = obtener_usuarios_activos() or []
        print(f"[READINESS] broadcast a {len(usuarios)} atletas")
        await broadcast_readiness(usuarios)

    sched = AsyncIOScheduler(timezone=tz)
    sched.add_job(_job, "cron", hour=hora, minute=minuto,
                  id="readiness_diario", replace_existing=True)
    sched.start()
    return sched


_HORAS_SUENO_RE = re.compile(r"(\d{1,2})(?:[.,](\d))?\s*(?:h(?:oras?|rs?)?\b|de\s+sue[ñn]o)", re.I)


def procesar_respuesta_readiness(numero: str, texto_boton_o_libre: str) -> dict:
    """Webhook: convierte la respuesta (botón o texto libre) en score 0-1 y la
    persiste. Devuelve el dict para inyectarlo a la telemetría del turno y que
    el coach ajuste el plan del día con motivo explícito."""
    t = str(texto_boton_o_libre or "").strip().lower()
    score = _SCORE_BOTON.get(_norm_boton(t))
    fuente = "botones"
    sueno = None
    if score is None:                                 # texto libre: heurística
        fuente = "texto"
        m = _HORAS_SUENO_RE.search(t)
        if m:
            sueno = float(m.group(1)) + (0.5 if m.group(2) else 0.0)
        base = 0.75
        if sueno is not None:
            base = 0.45 if sueno < 5.5 else 0.65 if sueno < 7 else 0.85
        if re.search(r"molid|fatal|destruid|cansad[íi]sim|no dorm|desvel", t):
            base -= 0.25
        if re.search(r"estr[eé]s|estresad|ansied|frito", t):
            base -= 0.10
        if re.search(r"duele|dolor|molestia|lesion", t):
            base -= 0.10
        if re.search(r"al 100|perfecto|excelente|descansad|como nuevo", t):
            base += 0.15
        score = max(0.0, min(1.0, base))
    dolor = None
    m_dolor = re.search(r"(?:duele|dolor(?:\s+de|\s+en)?|molestia\s+en)\s+(?:el|la|los|las)?\s*(\w+)", t)
    if m_dolor:
        dolor = m_dolor.group(1)
    mp.guardar_readiness(numero, score, sueno_horas=sueno, dolor=dolor, fuente=fuente)
    return {"score": round(score, 2), "sueno_horas": sueno, "dolor": dolor,
            "interpretacion": ("verde: sesión completa" if score >= 0.8 else
                               "amarillo: sesión normal, vigilar RPE" if score >= 0.6 else
                               "rojo: recortar volumen o descargar hoy")}


def redactar_respuesta_readiness(veredicto: dict) -> str:
    """El veredicto es un dict (telemetría para el coach); esto lo convierte en
    el texto que ve el atleta. Sin esto el webhook mandaba el dict crudo a Meta
    y la Cloud API respondía 400."""
    score = float(veredicto.get("score") or 0.0)
    if score >= 0.8:
        base = ("Excelente, vas en verde 🟢\nHoy le entramos a la sesión completa. "
                "Avísame cuando arranques y mándame el video de tu serie top.")
    elif score >= 0.6:
        base = ("Anotado, estás en ámbar 🟡\nSesión normal, pero vigilamos el RPE: "
                "si una serie se siente más pesada de lo previsto, la recortamos.")
    else:
        base = ("Entendido, hoy vienes en rojo 🔴\nBajamos volumen y nos quedamos "
                "lejos del fallo. Más vale acumular que romperse.")

    extras = []
    if veredicto.get("sueno_horas") is not None:
        extras.append(f"Dormiste {veredicto['sueno_horas']:g} h.")
    if veredicto.get("dolor"):
        extras.append(f"Ojo con {veredicto['dolor']}: lo tomo en cuenta al armar la sesión.")
    if extras:
        base += "\n\n" + " ".join(extras)
    return base


# ==============================================================================
# 2) GRÁFICAS DE PROGRESO (e1RM) → WHATSAPP
# ==============================================================================
def generar_grafica_e1rm(numero: str, ejercicio: str, dias: int = 90) -> tuple[str, dict] | None:
    """PNG con: e1RM por sesión (puntos), tendencia EWMA (línea) y PRs (★).
    Devuelve (ruta_png, resumen_numérico) o None sin datos suficientes."""
    series = mp.leer_series(numero, ejercicio, dias)
    ses = mp._sesiones(series)
    if len(ses) < 3:
        return None
    fechas = [s["fecha"] for s in ses]
    e1s = [s["e1rm"] for s in ses]
    ewma, acc, a = [], None, 1 - 0.5 ** (1 / mp.HALFLIFE_E1RM)
    for v in e1s:
        acc = v if acc is None else a * v + (1 - a) * acc
        ewma.append(acc)
    prs = [i for i, v in enumerate(e1s) if v > max(e1s[:i], default=0)]

    fig, ax = plt.subplots(figsize=(7, 4), dpi=150)
    ax.plot(fechas, e1s, "o", ms=5, color="#4C78A8", alpha=0.75, label="e1RM sesión")
    ax.plot(fechas, ewma, "-", lw=2.2, color="#F58518", label="tendencia (EWMA)")
    if prs:
        ax.plot([fechas[i] for i in prs], [e1s[i] for i in prs], "*",
                ms=14, color="#E45756", label="récord")
    ax.set_title(f"{ejercicio.title()} — e1RM ({dias} días)", fontsize=12, weight="bold")
    ax.set_ylabel("kg")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="lower right")
    fig.autofmt_xdate(rotation=30)
    ruta = os.path.join(CARPETA_TMP, f"e1rm_{numero}_{re.sub(r'\\W+', '_', ejercicio)}.png")
    fig.savefig(ruta, bbox_inches="tight")
    plt.close(fig)

    resumen = {"e1rm_actual_kg": round(ewma[-1], 1),
               "e1rm_inicio_kg": round(ewma[0], 1),
               "delta_kg": round(ewma[-1] - ewma[0], 1),
               "mejor_e1rm_kg": round(max(e1s), 1),
               "sesiones": len(ses), "records_en_periodo": len(prs)}
    return ruta, resumen


async def _subir_media(ruta_png: str) -> str:
    import httpx
    async with httpx.AsyncClient(timeout=60) as cli:
        with open(ruta_png, "rb") as f:
            r = await cli.post(
                f"{GRAPH_URL}/{PHONE_ID}/media",
                headers={"Authorization": f"Bearer {TOKEN}"},
                data={"messaging_product": "whatsapp"},
                files={"file": (os.path.basename(ruta_png), f, "image/png")})
        r.raise_for_status()
        return r.json()["id"]


async def enviar_imagen(numero: str, media_id: str, caption: str = "") -> dict:
    return await _post_graph("messages", json={
        "messaging_product": "whatsapp", "to": numero, "type": "image",
        "image": {"id": media_id, "caption": caption[:1024]}})


async def generar_y_enviar_grafica(numero: str, ejercicio: str, dias: int = 90,
                                   caption: str | None = None) -> dict:
    """Flujo completo tool-friendly. La generación matplotlib corre en hilo
    (CPU-bound: no bloquea el event loop del orquestador)."""
    import asyncio
    res = await asyncio.to_thread(generar_grafica_e1rm, numero, ejercicio, dias)
    if res is None:
        return {"ok": False, "error": "datos_insuficientes",
                "detalle": "se necesitan ≥3 sesiones con tríada completa para graficar."}
    ruta, resumen = res
    try:
        media_id = await _subir_media(ruta)
        pie = caption or (f"📈 {ejercicio.title()}: e1RM {resumen['e1rm_inicio_kg']:g} → "
                          f"{resumen['e1rm_actual_kg']:g} kg en {dias} días")
        await enviar_imagen(numero, media_id, pie)
        enviada = True
    except Exception as e:
        print(f"[GRAFICA] no se pudo enviar ({e}); el resumen numérico sí viaja")
        enviada = False
    return {"ok": True, "grafica_enviada": enviada, "resumen": resumen,
            "nota_coach": ("La imagen ya salió: comenta estos números en 1-2 líneas."
                           if enviada else
                           "La imagen falló: da el resumen en texto y discúlpate en corto.")}


# ==============================================================================
# 3) AUDIO CAÓTICO → TRÍADA [ejercicio, peso, reps @ RPE]
# ==============================================================================
# Vocabulario de dominio para Whisper: mejora números y nombres de ejercicios.
#   modelo.transcribe(audio, language="es", initial_prompt=WHISPER_INITIAL_PROMPT)
WHISPER_INITIAL_PROMPT = (
    "Registro de gimnasio de powerbuilding en español: press banca, sentadilla, "
    "peso muerto, press militar, remo con barra, dominadas, hip thrust. Cargas "
    "en kilos, repeticiones y RPE, por ejemplo: ciento dos punto cinco kilos por "
    "ocho repeticiones a RPE nueve.")

_UNIDADES = {"cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3,
             "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
             "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
             "catorce": 14, "quince": 15, "veinte": 20, "treinta": 30,
             "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
             "ochenta": 80, "noventa": 90}
_CENTENAS = {"cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300,
             "cuatrocientos": 400, "quinientos": 500}


def normalizar_numeros_es(texto: str) -> str:
    """'ciento dos y medio kilos por ocho' → '102.5 kilos por 8'. Whisper en
    español escribe números como palabras la mitad de las veces; sin esto, los
    regex deterministas del interceptor (v8) no ven nada."""
    tokens = re.split(r"(\s+)", str(texto or ""))
    out, i = [], 0
    while i < len(tokens):
        tk = tokens[i].lower().strip(".,;:")
        if tk in _CENTENAS or tk in _UNIDADES:
            val = 0.0
            consumidos = 0
            j = i
            esperando = True
            while j < len(tokens):
                t2 = tokens[j].lower().strip(".,;:")
                if t2.isspace() or t2 == "":
                    j += 1
                    continue
                if t2 in _CENTENAS and esperando:
                    val += _CENTENAS[t2]
                    consumidos = j - i + 1
                    esperando = True
                elif t2 in _UNIDADES and esperando:
                    val += _UNIDADES[t2]
                    consumidos = j - i + 1
                    # tras decena redonda puede venir 'y cinco'
                    esperando = _UNIDADES[t2] % 10 == 0 and _UNIDADES[t2] >= 20
                elif t2 == "y":
                    par = tokens[j + 2].lower().strip(".,;:") if j + 2 < len(tokens) else ""
                    if par == "medio":
                        val += 0.5
                        consumidos = j + 2 - i + 1
                        break
                    if par in _UNIDADES and _UNIDADES[par] < 10:
                        val += _UNIDADES[par]
                        consumidos = j + 2 - i + 1
                        j += 2
                        esperando = False
                        continue
                    break
                else:
                    break
                j += 1
            if consumidos and val > 0:
                out.append(f"{val:g}")
                i += consumidos
                continue
        out.append(tokens[i])
        i += 1
    return "".join(out)


PROMPT_EXTRACCION_AUDIO = """Extrae los datos de entrenamiento de esta transcripción de un audio de gimnasio (puede ser caótica, con correcciones y muletillas). Responde SOLO con JSON válido, sin markdown ni texto extra, con este formato exacto:
{{"ejercicio": str|null, "peso_kg": number|null, "repeticiones": int|null, "rpe": number|null, "confianza": number}}

Reglas:
- Si el atleta se corrige ("100... no, 102 y medio"), vale la ÚLTIMA cifra.
- RPE por frases: "fácil"≈6, "quedaban dos"=8, "quedaba una"=9, "casi no sube / a punto de fallar"=9.5, "fallé"=10.
- "la de pecho" o similar: resuélvelo con los ejercicios de HOY listados abajo; si no puedes, null.
- NO inventes: dato ausente = null. confianza ∈ [0,1] global.

Ejercicios de hoy y últimas cargas: {contexto}
Transcripción: "{transcript}"
JSON:"""


async def extraer_triada_audio(transcript: str, contexto_dia: str, chat_llm,
                               opciones_base: dict) -> dict:
    """Pipeline: normalizar números → regex determinista (si tu interceptor v8
    ya la saca completa, ni gastamos LLM) → extracción LLM estricta (JSON,
    temp 0) → veredicto con `faltantes` y `necesita_confirmacion`.

    `chat_llm` se INYECTA desde llm_service (evita import circular): la misma
    función serializada/con timeout de siempre."""
    texto = normalizar_numeros_es(transcript)
    resultado = {"ejercicio": None, "peso_kg": None, "repeticiones": None,
                 "rpe": None, "confianza": 0.0, "texto_normalizado": texto}

    # regex barata primero: "102.5 kilos por 8 a rpe 9" ya normalizado
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:kg|kilos?)?\s*(?:x|por|×)\s*(\d{1,2})", texto, re.I)
    if m:
        resultado["peso_kg"] = float(m.group(1))
        resultado["repeticiones"] = int(m.group(2))
    m_rpe = re.search(r"rpe\s*(\d{1,2}(?:\.\d)?)|@\s*(\d{1,2}(?:\.\d)?)", texto, re.I)
    if m_rpe:
        resultado["rpe"] = float(m_rpe.group(1) or m_rpe.group(2))

    if not all(resultado[k] for k in ("ejercicio", "peso_kg", "repeticiones", "rpe")):
        try:
            resp = await chat_llm(
                [{"role": "user", "content": PROMPT_EXTRACCION_AUDIO.format(
                    contexto=contexto_dia[:400], transcript=texto[:600])}],
                opciones={**opciones_base, "temperature": 0.0, "num_predict": 120},
                pensar=False, quien="extractor-audio", timeout=25.0)
            crudo = (resp.get("message", {}) or {}).get("content", "")
            crudo = re.sub(r"```(?:json)?|```", "", crudo).strip()
            datos = json.loads(crudo)
            for k in ("ejercicio", "peso_kg", "repeticiones", "rpe", "confianza"):
                if datos.get(k) is not None and resultado.get(k) in (None, 0.0):
                    resultado[k] = datos[k]
        except Exception as e:
            print(f"[AUDIO] extractor LLM falló ({e}); queda lo determinista")

    faltantes = [k for k in ("ejercicio", "peso_kg", "repeticiones", "rpe")
                 if resultado.get(k) in (None, "")]
    resultado["faltantes"] = faltantes
    # Los números por voz son los más caros de equivocar: bajo confianza o con
    # huecos, el coach hace ECO ("¿Anoto banca 102.5×8 @9?") y espera el "sí"
    # (AFIRMACION_CORTA_RE de v8) ANTES de llamar registrar_serie_completada.
    resultado["necesita_confirmacion"] = bool(faltantes) or float(resultado.get("confianza") or 0) < 0.9
    return resultado