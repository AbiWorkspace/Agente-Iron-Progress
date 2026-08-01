import asyncio
import hashlib
import hmac
import json
import os
import re
import shutil
import time
from collections import deque
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

# Importaciones de configuración y servicios propios
from api.cola_mensajes import (Trabajo, encolar, tiene_trabajo, TEXTO_AVISO_EN_COLA,
                               TEXTO_COLA_LLENA)
from core.config import settings
from core.ejercicios import canonizar, detectar
from data.meta_service import enviar_mensaje_whatsapp
from services.llm_service import enviar_bienvenida_si_procede, generar_respuesta_coach
from services.audio_service import procesar_audio_whatsapp
from services.vbt_service import procesar_y_responder_video_vbt
from services.vision_service import predecir_ejercicio
from services.handlers_v9 import fijar_ejercicio_confirmado

# --- GANCHOS V9 ---
from services.proactividad import (
    procesar_respuesta_readiness,
    redactar_respuesta_readiness,
    es_boton_readiness,
    BOTONES_READINESS,
    extraer_triada_audio,
)
from services.llm_service import chat_llm, OPCIONES_QWEN_32B
from services.db_service import obtener_dashboard_analitico

# ==========================================
# CONFIGURACIÓN Y CONSTANTES
# ==========================================
router = APIRouter()

# Tiempo de expiración para limpieza de caché (10 minutos)
EXPIRATION_TIME = 600
# Mensajes con más de 5 minutos: Meta los está reintentando, ya no valen.
ANTIGUEDAD_MAXIMA_MENSAJE = 300
# Cuántos IDs de mensaje recordamos para descartar reentregas de Meta.
MAX_MENSAJES_RECORDADOS = 1000

# ==========================================
# ESTADO DE LA APLICACIÓN (MEMORIA VOLÁTIL)
# ==========================================
videos_pendientes: dict[str, dict] = {}

# `usuarios_procesando` ya no existe. Era un filtro de admisión que DESCARTABA el
# mensaje nuevo del atleta cuando ya tenía uno en proceso ("sentadilla 140x5" y
# luego "@8": el segundo se perdía sin avisar), y además tenía una carrera —el
# chequeo estaba aquí y el `add` dentro de la tarea de fondo, que corre después de
# responder, así que dos entregas seguidas de Meta pasaban las dos—.
# Ahora manda api/cola_mensajes: una cola FIFO por atleta, nada se descarta.

# Antes era un `set` que se VACIABA entero al llegar a 1000: justo después del
# borrado, cualquier reentrega de Meta se procesaba como mensaje nuevo y el
# atleta recibía la respuesta duplicada. Ahora la cola descarta solo los IDs
# más antiguos, que son los que ya no pueden reentregarse.
mensajes_procesados: set[str] = set()
_orden_mensajes: deque[str] = deque()

# ==========================================
# CLIENTE HTTP COMPARTIDO
# ==========================================
# Cada descarga de media abría (y cerraba) su propio AsyncClient: handshake TLS
# completo contra graph.facebook.com por vídeo y por audio. Con un cliente único
# la conexión se reutiliza y el pool queda vivo durante toda la vida del proceso.
_cliente_http: httpx.AsyncClient | None = None
_lock_cliente = asyncio.Lock()


async def cliente_http() -> httpx.AsyncClient:
    global _cliente_http
    if _cliente_http is None or _cliente_http.is_closed:
        async with _lock_cliente:
            if _cliente_http is None or _cliente_http.is_closed:
                _cliente_http = httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=httpx.Timeout(60.0, connect=10.0),
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                )
    return _cliente_http


async def cerrar_cliente_http() -> None:
    """Se llama desde el lifespan de main.py al apagar."""
    global _cliente_http
    if _cliente_http is not None and not _cliente_http.is_closed:
        await _cliente_http.aclose()
    _cliente_http = None


if not settings.APP_SECRET:
    # En producción NO se arranca sin firma. Un webhook abierto no es un aviso
    # que se pueda ignorar: cualquiera que conozca la URL puede hacerse pasar
    # por un atleta e inyectar mensajes, y el coach le escribirá por WhatsApp a
    # una persona real. El aviso llevaba meses imprimiéndose sin que nadie lo
    # viera entre el resto del arranque, que es exactamente cómo un pendiente de
    # seguridad llega a producción.
    if settings.es_produccion:
        raise RuntimeError(
            "META_APP_SECRET no está definido y ENTORNO=prod. El webhook "
            "aceptaría CUALQUIER petición sin comprobar que viene de Meta. "
            "Cópialo de developers.facebook.com > tu app > Configuración > "
            "Básica > 'Clave secreta de la app' y ponlo en el .env. Para "
            "desarrollo local, usa ENTORNO=dev.")
    print("[SEGURIDAD]: META_APP_SECRET no está definido en el .env. El webhook "
          "aceptará CUALQUIER petición sin verificar que venga de Meta. Defínelo "
          "para activar la validación de firma (X-Hub-Signature-256). "
          "(Con ENTORNO=prod esto aborta el arranque.)")


def firma_valida(cuerpo_crudo: bytes, cabecera_firma: str | None) -> bool:
    """Verifica el X-Hub-Signature-256 con el que Meta firma cada webhook.

    Sin esta comprobación, cualquiera que conozca la URL puede inyectar mensajes
    falsos y hacer que el coach escriba por WhatsApp a un atleta real. Si
    META_APP_SECRET no está configurado se acepta la petición para no romper el
    desarrollo local — pero con ENTORNO=prod el proceso ni siquiera arranca, así
    que ese camino no existe en producción.
    """
    if not settings.APP_SECRET:
        return True
    if not cabecera_firma or not cabecera_firma.startswith("sha256="):
        print("[SEGURIDAD]: webhook SIN cabecera X-Hub-Signature-256; descartado.")
        return False
    esperada = hmac.new(settings.APP_SECRET.encode("utf-8"),
                        cuerpo_crudo, hashlib.sha256).hexdigest()
    recibida = cabecera_firma.split("=", 1)[1].strip()
    # compare_digest: comparación en tiempo constante, sin filtrar información
    # sobre el secreto por el tiempo de respuesta.
    if not hmac.compare_digest(esperada, recibida):
        print("[SEGURIDAD]: firma del webhook INVÁLIDA; petición descartada.")
        return False
    return True


def _mensaje_ya_visto(mensaje_id: str) -> bool:
    """True si Meta ya nos entregó este mensaje (reintento). Cola acotada."""
    if not mensaje_id:
        return False
    if mensaje_id in mensajes_procesados:
        return True
    mensajes_procesados.add(mensaje_id)
    _orden_mensajes.append(mensaje_id)
    while len(_orden_mensajes) > MAX_MENSAJES_RECORDADOS:
        mensajes_procesados.discard(_orden_mensajes.popleft())
    return False


async def enviar_whatsapp(numero_usuario: str, texto: str) -> None:
    """Envío no bloqueante. `enviar_mensaje_whatsapp` usa requests + pausas, así
    que llamarla directo desde una corrutina congelaba el event loop en cada
    mensaje (y hasta 4 s cuando el texto se parte en varios fragmentos)."""
    detener_escribiendo(numero_usuario)
    await asyncio.to_thread(enviar_mensaje_whatsapp,
                            telefono_destino=numero_usuario, texto=texto)



def corregir_ejercicio_usuario(texto_usuario: str, ejercicio_ia: str) -> str:
    """Analiza el texto del atleta para detectar si corrigió a la IA.

    Delega en `core.ejercicios`: antes esta función mantenía su PROPIO mapa de
    alias y devolvía claves con guion bajo ("press_banca") que no coincidían ni
    con lo que escribía el clasificador de visión ("Press banco") ni con lo que
    esperaba `_GRUPO_DE` ("press banca"). Tres vocabularios para el mismo
    concepto, y la base de datos guardando los tres. El mapa vive ahora en un
    solo sitio; aquí solo queda la política: lo que dice el atleta pisa a la
    detección automática, y si no nombra ningún ejercicio se respeta la
    detección (no todo mensaje es una corrección: "Sí, 120kg" confirma).
    """
    return detectar(texto_usuario) or canonizar(ejercicio_ia)


async def filtrar_audio_triada(numero_usuario: str, texto_transcrito: str) -> str:
    """Pasa el transcript de Whisper por el extractor de tríada de proactividad.

    `extraer_triada_audio` es async y necesita (transcript, contexto_dia, chat_llm,
    opciones_base); antes se llamaba sin await y con un solo argumento, así que
    lanzaba TypeError y el webhook descartaba TODOS los audios en silencio.

    Devuelve siempre un string: el texto normalizado (números en palabras ya
    convertidos a cifras) más la tríada detectada como anexo para el orquestador.
    Ante cualquier fallo devuelve el transcript original: nunca perdemos el audio.
    """
    if not texto_transcrito:
        return texto_transcrito
    try:
        contexto_dia = await asyncio.to_thread(obtener_dashboard_analitico, numero_usuario)
        triada = await extraer_triada_audio(
            texto_transcrito, contexto_dia, chat_llm, OPCIONES_QWEN_32B
        )
    except Exception as e:
        print(f"[AUDIO] extractor de tríada falló ({e}); uso el transcript crudo")
        return texto_transcrito

    texto = triada.get("texto_normalizado") or texto_transcrito
    datos = {k: triada.get(k) for k in ("ejercicio", "peso_kg", "repeticiones", "rpe")
             if triada.get(k) is not None}
    if datos:
        detalle = ", ".join(f"{k}={v}" for k, v in datos.items())
        texto += f"\n[TRIADA DETECTADA]: {detalle}"
        if triada.get("necesita_confirmacion"):
            texto += " (confianza baja o datos incompletos: confirma con el atleta antes de registrar)"
    return texto


async def limpiar_cache_videos():
    while True:
        await asyncio.sleep(300)
        # Un fallo aquí dentro (p. ej. el KeyError de un `del` sobre una entrada que
        # otra corrutina ya borró) mataba la tarea para SIEMPRE y videos_pendientes
        # crecía sin límite el resto de la vida del proceso.
        try:
            ahora = time.time()
            a_eliminar = [
                num for num, data in list(videos_pendientes.items())
                if ahora - data.get("timestamp", 0) > EXPIRATION_TIME
            ]
            for num in a_eliminar:
                if videos_pendientes.pop(num, None) is not None:
                    print(f"[CLEANUP]: Eliminando video expirado de {num}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[CLEANUP]: error durante la limpieza (la tarea sigue viva): {e}")


_NUMERO_MEXICANO_RE = re.compile(r"^521(\d{10})$")


def normalizar_numero_mexicano(numero: str) -> str:
    return _NUMERO_MEXICANO_RE.sub(r"52\1", str(numero or ""))


# El patrón viejo (`\d+(?:[.,]\d+)?\s*(?:kg|kilos?|k)?\b`) tenía la unidad
# OPCIONAL: cualquier dígito contaba como peso, así que "hice 3 series" pasaba
# el filtro y `extraer_peso` devolvía 3.0 kg como carga del análisis VBT.
# Ahora hay dos caminos: número CON unidad explícita, o número desnudo dentro
# del rango plausible de una barra cargada.
_PESO_CON_UNIDAD_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:kg|kgs|kilo|kilos|k)\b", re.IGNORECASE)
_NUMERO_SUELTO_RE = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)(?![\d.,]*\s*(?:reps?|repeticiones?|series?|s\b))",
                               re.IGNORECASE)
PESO_MINIMO_PLAUSIBLE = 20.0     # barra olímpica vacía
PESO_MAXIMO_PLAUSIBLE = 500.0


def extraer_peso(texto: str) -> float | None:
    """Kilos declarados por el atleta, o None si no dijo ninguno."""
    if not texto:
        return None
    m = _PESO_CON_UNIDAD_RE.search(texto)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None
    for bruto in _NUMERO_SUELTO_RE.findall(texto):
        try:
            valor = float(bruto.replace(",", "."))
        except ValueError:
            continue
        if PESO_MINIMO_PLAUSIBLE <= valor <= PESO_MAXIMO_PLAUSIBLE:
            return valor
    return None


def contiene_peso(texto: str) -> bool:
    return extraer_peso(texto) is not None


# ==========================================
# PIPELINES EN SEGUNDO PLANO
# ==========================================
async def procesar_y_enviar_mensaje(
        numero_usuario: str, texto_usuario: str = None, media_id: str = None,
        nombre_ejercicio: str = None, carga_kg: float = None
):
    try:
        print(f"Iniciando procesamiento en segundo plano para {numero_usuario}...")
        mensaje_final_para_llm = ""

        # FLUJO 1: PROCESAMIENTO DE VIDEO (VBT) Y FUSIÓN DE DATOS
        if media_id and media_id != "0":
            print("[INFO]: Iniciando pipeline de video desde WhatsApp...")
            texto_contexto_vbt = await procesar_y_responder_video_vbt(
                media_id_entrada=media_id, numero_usuario=numero_usuario,
                nombre_ejercicio=nombre_ejercicio, carga_kg=carga_kg
            )
            texto_limpio = (texto_contexto_vbt or "").strip()

            if not texto_limpio or "[ERROR]" in texto_limpio:
                # El pipeline VBT ya avisó al atleta por su cuenta (texto de error
                # y, si lo hubo, el video forense). Aquí solo se corta el flujo.
                print("[ORQUESTADOR]: el pipeline de video no devolvió telemetría utilizable.")
                return

            print("[ORQUESTADOR]: Video procesado. FUSIONANDO EJERCICIO, PESO Y MÉTRICAS...")
            info_declarada = texto_usuario if texto_usuario else "Datos no especificados"
            # El ejercicio y la carga YA están resueltos aquí: `nombre_ejercicio`
            # es la salida de `corregir_ejercicio_usuario`, que da prioridad a lo
            # que dijo el atleta sobre lo que detectó la visión. Antes ese dato se
            # usaba solo para el motor VBT y al LLM le llegaba únicamente el texto
            # narrativo ("IA detectó inicialmente: Press banco. Atleta corrigió
            # con: No, sentadilla 140kg"). Con la rutina del día en contexto
            # diciendo "Press Banco Plano", el modelo se quedaba con la detección
            # errónea y registraba la serie con el ejercicio equivocado, pese a
            # que el reporte VBT decía SENTADILLA. Ahora el hecho ya resuelto va
            # primero y sin ambigüedad.
            hechos = ["TELEMETRÍA DE VIDEO VBT RECIBIDA."]
            if nombre_ejercicio:
                # Además del prompt, la guarda dura: el handler de registro
                # impondrá este ejercicio aunque el modelo escriba otro.
                fijar_ejercicio_confirmado(numero_usuario, nombre_ejercicio)
                hechos.append(
                    f"EJERCICIO CONFIRMADO POR EL ATLETA: {nombre_ejercicio}. "
                    f"Este es el dato válido: úsalo tal cual al registrar la "
                    f"serie, aunque la rutina del día o la detección automática "
                    f"digan otra cosa.")
            if carga_kg:
                hechos.append(f"CARGA CONFIRMADA: {carga_kg:g} kg.")
            hechos.append(f"Mensaje original del atleta: {info_declarada}")
            mensaje_final_para_llm = "\n".join(hechos) + f"\n\n{texto_limpio}"

        # FLUJO 2: TEXTO NORMAL
        elif texto_usuario:
            print("[INFO]: Tenemos un mensaje de texto normal")
            mensaje_final_para_llm = texto_usuario
        else:
            print("[LLM]: No se recibió ni texto ni video válido.")
            return

        # v14 — CARTA DE PRESENTACIÓN. Va AQUÍ, antes de arrancar el turno: el
        # orquestador tarda 20-130 s y esto uno, así que la presentación llega
        # primero y su mensaje se responde igual justo después. No la lanzamos en
        # paralelo a propósito — si el turno fuera rapidísimo podrían cruzarse y
        # el atleta leería la respuesta antes que el saludo.
        await enviar_bienvenida_si_procede(numero_usuario)

        respuesta_ia = await generar_respuesta_coach(numero_usuario, mensaje_final_para_llm)

        if not respuesta_ia or not respuesta_ia.strip():
            print("La IA solo generó eventos internos. Se cancela el envío a WhatsApp.")
            return

        await enviar_whatsapp(numero_usuario, respuesta_ia)
        print(f"Mensaje enviado con éxito a {numero_usuario}")

    except Exception as e:
        print(f"Error en la tarea en segundo plano: {e}")
        try:
            await enviar_whatsapp(
                numero_usuario,
                "Se me trabó el sistema procesando eso bro 🙏 Mándamelo otra vez porfa.")
        except Exception as e_aviso:
            print(f"[ERROR]: tampoco pude avisar del fallo: {e_aviso}")
    finally:
        detener_escribiendo(numero_usuario)


# ==============================================================================
# v14 — TRANSCRIPCIÓN ADELANTADA (audio que no espera su turno)
# ------------------------------------------------------------------------------
# La descarga desde Meta (red) y Whisper (CPU, device="cpu") NO tocan la GPU,
# pero corrían dentro del trabajo encolado: el segundo audio ni siquiera empezaba
# a bajarse hasta que el primero terminaba su turno completo de LLM. Medido el
# 2026-07-30: esperas de 65,8 s, 54,4 s y 94,7 s en cola.
#
# Ahora la transcripción arranca AL RECIBIR el audio, en paralelo con el turno
# que esté ocupando la GPU. El trabajo se encola igual y en el mismo instante que
# antes —el orden de la conversación no cambia—, solo que cuando le llega el
# turno su texto ya suele estar listo.
#
# El semáforo evita que una ráfaga de audios lance ocho Whisper a la vez y
# ahogue la CPU que el resto del proceso necesita.
_SEM_TRANSCRIPCION = asyncio.Semaphore(2)
_tareas_transcripcion: set[asyncio.Task] = set()


async def _transcribir_adelantado(numero_usuario: str, audio_id: str) -> str:
    """Descarga y transcribe ya, sin esperar a la cola del LLM."""
    t0 = time.monotonic()
    async with _SEM_TRANSCRIPCION:
        espera = time.monotonic() - t0
        try:
            texto = await procesar_audio_whatsapp(audio_id)
        except Exception as e:
            print(f"[AUDIO] {numero_usuario}: transcripción falló "
                  f"({type(e).__name__}: {e})")
            return ""
    print(f"[AUDIO] {numero_usuario}: transcrito en {time.monotonic() - t0:.1f}s"
          + (f" (esperó {espera:.1f}s por otra transcripción)" if espera > 0.5 else "")
          + " — adelantado, sin ocupar el turno")
    return texto


def lanzar_transcripcion(numero_usuario: str, audio_id: str) -> asyncio.Task:
    """Arranca la transcripción en paralelo y devuelve su tarea.

    Referencia fuerte en `_tareas_transcripcion`: asyncio solo guarda referencias
    débiles y el recolector puede llevarse una a medias.
    """
    tarea = asyncio.create_task(_transcribir_adelantado(numero_usuario, audio_id))
    _tareas_transcripcion.add(tarea)
    tarea.add_done_callback(_tareas_transcripcion.discard)
    return tarea


async def procesar_audio_en_segundo_plano(numero_usuario: str, audio_id: str,
                                          transcripcion: asyncio.Task | None = None) -> None:
    """Despacha el audio FUERA del ciclo de petición del webhook.

    Antes esto ocurría dentro del handler: Whisper (5-30 s) más el extractor de
    tríada podían superar el timeout de Meta, que marcaba la entrega como fallida y
    la reintentaba. Ahora el webhook responde 200 al instante y el trabajo pasa a
    la cola del atleta (api/cola_mensajes).

    `transcripcion` es la tarea que ya arrancó al recibir el audio: aquí solo se
    espera su resultado, que casi siempre está listo. Sin ella (llamada directa o
    reintento), se transcribe aquí como antes.
    """
    try:
        if transcripcion is not None:
            t_espera = time.monotonic()
            texto_transcrito = await transcripcion
            faltaba = time.monotonic() - t_espera
            if faltaba > 0.5:
                print(f"[AUDIO] {numero_usuario}: el turno esperó {faltaba:.1f}s a "
                      f"que acabara la transcripción")
        else:
            texto_transcrito = await procesar_audio_whatsapp(audio_id)

        if not texto_transcrito:
            # Antes el atleta se quedaba en silencio absoluto cuando Whisper
            # fallaba o el audio venía vacío.
            await enviar_whatsapp(
                numero_usuario,
                "No logré escuchar bien ese audio bro 🙏 ¿Me lo repites o me lo escribes?")
            return

        # =====================================================================
        # GANCHO V9 (2/2): FILTRO DE AUDIO CAÓTICO
        # =====================================================================
        print(f"[WHISPER ORIGINAL]: {texto_transcrito}")
        texto_transcrito = await filtrar_audio_triada(numero_usuario, texto_transcrito)
        print(f"[WHISPER FILTRADO]: {texto_transcrito}")
        # =====================================================================

        if numero_usuario in videos_pendientes:
            if not contiene_peso(texto_transcrito):
                await enviar_whatsapp(
                    numero_usuario,
                    "No logré identificar los kilos en tu audio. Escríbeme el peso "
                    "(ej. '140kg') para analizar el VBT."
                )
                return

            datos_video = videos_pendientes.pop(numero_usuario, {})
            video_id_esperando = datos_video.get("id")
            ej_previo = datos_video.get("ejercicio_detectado", "Movimiento Libre")

            # La rama de audio NO aplicaba `corregir_ejercicio_usuario`: pasaba
            # `ej_previo` (la detección de visión) tal cual, así que un atleta
            # que corrigiera el ejercicio HABLANDO era ignorado por completo y
            # todo el análisis VBT corría con el perfil equivocado (MVT y
            # pendiente carga-velocidad de otro ejercicio). La rama de texto sí
            # lo hacía; era una asimetría, no una decisión.
            ej_corregido = corregir_ejercicio_usuario(texto_transcrito, ej_previo)
            texto_fusionado = (f"IA detectó inicialmente: {ej_previo}. "
                               f"Atleta corrigió/confirmó con: {texto_transcrito}")
            await procesar_y_enviar_mensaje(numero_usuario, texto_fusionado,
                                            video_id_esperando,
                                            nombre_ejercicio=ej_corregido,
                                            carga_kg=extraer_peso(texto_transcrito))
        else:
            await procesar_y_enviar_mensaje(numero_usuario, texto_transcrito, None)
    except Exception as e:
        print(f"[ERROR AUDIO BG]: {e}")
        try:
            await enviar_whatsapp(
                numero_usuario,
                "Algo falló procesando tu audio bro 🙏 ¿Me lo mandas de nuevo?")
        except Exception:
            pass


async def responder_readiness_en_segundo_plano(numero_usuario: str, texto_boton: str) -> None:
    """El readiness escribe en SQLite y manda un WhatsApp: ~1 s de trabajo que
    antes ocurría DENTRO del handler del webhook, retrasando el 200 a Meta."""
    try:
        veredicto = await asyncio.to_thread(procesar_respuesta_readiness,
                                            numero_usuario, texto_boton)
        print(f"[PROACTIVIDAD]: veredicto -> {veredicto}")
        await enviar_whatsapp(numero_usuario, redactar_respuesta_readiness(veredicto))
    except Exception as e:
        print(f"[ERROR READINESS]: {e}")


async def procesar_prediccion_rapida(numero_usuario: str, media_id: str):
    print(f"[IA VISION]: Analizando video {media_id} para detectar ejercicio...")
    carpeta_temp = os.path.join("data", "temp_vision")
    carpeta_dataset = os.path.join("data", "dataset_recoleccion")
    os.makedirs(carpeta_temp, exist_ok=True)
    os.makedirs(carpeta_dataset, exist_ok=True)
    ruta_temporal = os.path.join(carpeta_temp, f"temp_{media_id}.mp4")

    try:
        headers_auth = {"Authorization": f"Bearer {settings.ACCESS_TOKEN}"}
        url_info = f"{settings.GRAPH_URL}/{media_id}"

        cliente = await cliente_http()
        respuesta_info = await cliente.get(url_info, headers=headers_auth)
        if respuesta_info.status_code != 200:
            raise RuntimeError(f"Meta rechazó la info del video: {respuesta_info.text[:200]}")

        url_descarga = respuesta_info.json().get("url")
        if not url_descarga:
            raise RuntimeError("Meta no devolvió URL de descarga para el video")

        respuesta_video = await cliente.get(url_descarga, headers=headers_auth)
        respuesta_video.raise_for_status()
        with open(ruta_temporal, "wb") as f:
            f.write(respuesta_video.content)

        # MediaPipe + OpenCV decodifican el video completo: son decenas de segundos
        # de CPU pura. En la corrutina bloqueaban el servidor entero; van a un hilo.
        ejercicio_detectado = await asyncio.to_thread(predecir_ejercicio, ruta_temporal)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ej_limpio = (ejercicio_detectado.replace("_", " ")
                     if ejercicio_detectado != "Ejercicio_Desconocido" else "No_Reconocido")

        carpeta_ejercicio = os.path.join(carpeta_dataset, ej_limpio.replace(" ", "_"))
        os.makedirs(carpeta_ejercicio, exist_ok=True)
        nombre_final = f"{timestamp}_atleta_{str(numero_usuario)[-4:]}_{media_id}.mp4"
        await asyncio.to_thread(shutil.copy2, ruta_temporal,
                                os.path.join(carpeta_ejercicio, nombre_final))

        if ejercicio_detectado != "Ejercicio_Desconocido":
            videos_pendientes.setdefault(numero_usuario, {})["ejercicio_detectado"] = ej_limpio
            msj = (f"¡Video recibido!\n\nMi sistema detecta que hiciste *{ej_limpio}*.\n\n"
                   f"¿Es correcto? Confírmamelo y dime con cuántos kilos lo hiciste para analizar tu velocidad.\n"
                   f"*(Ej: 'Sí, 120kg' o 'No, fue Remo con 80kg')*")
        else:
            videos_pendientes.setdefault(numero_usuario, {})["ejercicio_detectado"] = "Movimiento Libre"
            msj = ("🎥 ¡Video recibido!\n\nNo pude clasificar el movimiento automáticamente "
                   "(lo guardaré como *Movimiento Libre* por ahora).\n\n"
                   "¿Qué ejercicio fue exactamente y con cuántos kilos lo hiciste? "
                   "Intentaré medir tu velocidad con el VBT de todos modos.")

        await enviar_whatsapp(numero_usuario, msj)
    except Exception as e:
        print(f"[ERROR IA VISION]: {e}")
        await enviar_whatsapp(
            numero_usuario,
            "¡Video recibido!\n\n¿Qué ejercicio es y con cuántos kilos lo hiciste?\n"
            "(Ejemplo: 'Press de banca, 80kg')")
    finally:
        if os.path.exists(ruta_temporal):
            try:
                os.remove(ruta_temporal)
            except OSError as e:
                print(f"[LIMPIEZA]: no se pudo borrar {ruta_temporal}: {e}")


# ==========================================
# WEBHOOK
# ==========================================
@router.get("/webhook")
async def verificar_webhook_meta(request: Request):
    parametros = request.query_params
    modo = parametros.get("hub.mode")
    token = parametros.get("hub.verify_token")
    desafio = parametros.get("hub.challenge")

    if modo and token:
        if modo == "subscribe" and token == settings.VERIFY_TOKEN:
            return PlainTextResponse(content=desafio, status_code=200)
        raise HTTPException(status_code=403, detail="Token invalido")
    raise HTTPException(status_code=400, detail="Faltan parametros")


def _texto_de_boton(mensaje: dict, tipo_mensaje: str) -> tuple[str | None, bool]:
    """Devuelve (texto, es_pulsacion_real) del mensaje entrante.

    Todos los accesos son `.get()`: un payload de Meta sin la forma esperada
    lanzaba KeyError, el except global lo convertía en 200 y ese mensaje del
    atleta se perdía sin dejar rastro.
    """
    if tipo_mensaje == "button":
        texto = (mensaje.get("button") or {}).get("text")
        return (texto.strip() if texto else None), True
    if tipo_mensaje == "interactive":
        interactivo = mensaje.get("interactive") or {}
        if interactivo.get("type") == "button_reply":
            texto = (interactivo.get("button_reply") or {}).get("title")
            return (texto.strip() if texto else None), True
        return None, False
    if tipo_mensaje == "text":
        texto = (mensaje.get("text") or {}).get("body")
        return (texto.strip() if texto else None), False
    return None, False


# Señales de que el mensaje NO es la respuesta al video pendiente, sino otra
# conversación: una pregunta, una consulta de calendario, una molestia, una
# cancelación. La corrección del ejercicio ("era sentadilla") no casa aquí y
# sigue el camino de siempre.
_INTENCION_AJENA_RE = re.compile(
    r"\?|\b(?:qu[eé]|cu[aá]ndo|cu[aá]nto|c[oó]mo|por\s+qu[eé]|olvida|olv[ií]dalo|cancela|"
    r"d[eé]jalo|ma[ñn]ana|hoy|rutina|plan|duele|dolor|molest|descanso|semana|gracias|"
    r"hola|listo\s+termin|termin[ée])\b",
    re.IGNORECASE)


async def _atender_texto(numero_usuario: str, texto_recibido: str) -> None:
    """Un mensaje de texto, ya dentro del worker del atleta.

    La consulta a `videos_pendientes` se hace AQUÍ y no al recibir el webhook, y
    eso arregla una carrera real: la entrada la enriquece `procesar_prediccion_
    rapida` con el `ejercicio_detectado`, y esa predicción tarda (descarga +
    MediaPipe). Si el atleta mandaba el video y los kilos casi seguidos, el
    webhook leía la entrada todavía a medias y TODO el análisis VBT corría como
    "Movimiento Libre" —perfil de otro ejercicio: MVT y pendiente carga-velocidad
    equivocados—. Ahora la cola garantiza que el video se atendió antes, así que
    lo que se lee aquí ya está completo.
    """
    if numero_usuario not in videos_pendientes:
        await procesar_y_enviar_mensaje(numero_usuario, texto_recibido, None)
        return

    if not contiene_peso(texto_recibido):
        # Sin kilos no hay VBT posible, pero si nombró el ejercicio se guarda:
        # es la corrección del atleta a la detección automática.
        ej_previo = videos_pendientes[numero_usuario].get("ejercicio_detectado", "")
        videos_pendientes[numero_usuario]["ejercicio_detectado"] = \
            corregir_ejercicio_usuario(texto_recibido, ej_previo)

        # Con un video esperando, CUALQUIER mensaje sin kilos recibía "no logré
        # identificar los kilos". El atleta mandaba su serie, preguntaba "¿y
        # mañana qué toca?" y se topaba con eso; la conversación quedaba
        # secuestrada los 10 minutos que dura el video en memoria. Si lo que
        # escribe es claramente otra cosa, se le atiende y se le recuerda el
        # video UNA vez.
        if _INTENCION_AJENA_RE.search(texto_recibido or ""):
            await procesar_y_enviar_mensaje(numero_usuario, texto_recibido, None)
            if not videos_pendientes[numero_usuario].get("recordatorio_enviado"):
                videos_pendientes[numero_usuario]["recordatorio_enviado"] = True
                await enviar_whatsapp(
                    numero_usuario,
                    "Por cierto, sigo con tu video en espera: mándame los kilos "
                    "(ej. '140kg') cuando quieras y lo analizo.")
            return

        await enviar_whatsapp(
            numero_usuario,
            "No logré identificar los kilos en tu mensaje. Escríbeme el peso "
            "(ej. '140kg') para poder iniciar el análisis VBT.")
        return

    datos_video = videos_pendientes.pop(numero_usuario, {})
    ej_previo = datos_video.get("ejercicio_detectado", "Movimiento Libre")
    texto_fusionado = (f"IA detectó inicialmente: {ej_previo}. "
                       f"Atleta corrigió/confirmó con: {texto_recibido}")
    await procesar_y_enviar_mensaje(
        numero_usuario, texto_fusionado, datos_video.get("id"),
        nombre_ejercicio=corregir_ejercicio_usuario(texto_recibido, ej_previo),
        carga_kg=extraer_peso(texto_recibido))


def _trabajo_texto(numero_usuario: str, texto: str) -> Trabajo:
    """Turno de chat normal. Es el ÚNICO tipo fusionable: varios textos seguidos
    del mismo atleta se atienden como un solo turno (ver `_extraer` en la cola)."""
    return Trabajo(
        numero=numero_usuario,
        tipo="texto",
        etiqueta=f"texto[{_trunc_log(texto)}]",
        texto=texto,
        ejecutar=lambda: _atender_texto(numero_usuario, texto),
        rehacer=lambda t: _trabajo_texto(numero_usuario, t),
    )


def _trunc_log(texto: str, n: int = 40) -> str:
    texto = " ".join(str(texto or "").split())
    return texto if len(texto) <= n else texto[:n - 1] + "…"


async def _encolar_y_avisar(trabajo: Trabajo) -> None:
    """Encola y, si el atleta queda esperando detrás de su propio trabajo, se lo
    dice. El silencio es lo que hacía que un mensaje aparcado pareciera perdido."""
    recibo = await encolar(trabajo)
    if not recibo.aceptado:
        await enviar_whatsapp(trabajo.numero, TEXTO_COLA_LLENA)
        return
    if recibo.avisar:
        await enviar_whatsapp(trabajo.numero, TEXTO_AVISO_EN_COLA)


_TAREAS_TYPING: dict[str, asyncio.Task] = {}
_ULTIMO_MENSAJE_ID: dict[str, str] = {}


def detener_escribiendo(clave: str) -> None:
    """Cancela el bucle de 'escribiendo...' cuando la respuesta ya fue enviada."""
    if not clave:
        return
    task = _TAREAS_TYPING.pop(clave, None)
    if task and not task.done():
        task.cancel()


def _lanzar_escribiendo(mensaje_id: str = "", numero_usuario: str = "") -> None:
    """Enciende el 'escribiendo…' y lo mantiene activo en segundo plano cada 18s mientras el modelo piensa.

    La tarea suelta el guardia heredado antes de nada: `asyncio.create_task`
    COPIA los ContextVar, así que sin esto una tarea de fondo creada dentro de un
    turno se creería dueña del semáforo del LLM y provocaría un `release()` de
    más al cerrarse el turno padre — dos inferencias a la vez y vuelta al
    thrashing de VRAM, sin ningún error visible. Ver la nota de EXCLUSIÓN POR
    TURNO en llm_service y el test 7 de test_turno_exclusivo.
    """
    if numero_usuario and mensaje_id:
        _ULTIMO_MENSAJE_ID[numero_usuario] = mensaje_id
    elif numero_usuario and not mensaje_id:
        mensaje_id = _ULTIMO_MENSAJE_ID.get(numero_usuario, "")

    if not mensaje_id:
        return

    clave = numero_usuario or mensaje_id
    detener_escribiendo(clave)

    async def _tarea():
        try:
            from services.llm_service import _soltar_guardia_heredado
            _soltar_guardia_heredado()
        except Exception:
            pass
        from services import proactividad
        max_duracion = 180  # 3 minutos tope por seguridad
        inicio = time.time()
        try:
            while (time.time() - inicio) < max_duracion:
                await proactividad.marcar_leido_y_escribiendo(mensaje_id)
                await asyncio.sleep(18)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[TYPING] fallo en el bucle del indicador: {e}")
        finally:
            _TAREAS_TYPING.pop(clave, None)

    try:
        task = asyncio.create_task(_tarea())
        if clave:
            _TAREAS_TYPING[clave] = task
    except RuntimeError:
        pass          # sin event loop (tests sincronos): el indicador es opcional


async def _procesar_mensaje(mensaje: dict) -> None:
    """Encola UN mensaje entrante. Cualquier `return` descarta solo este mensaje;
    los demás del mismo lote se siguen procesando."""
    numero_crudo = mensaje.get("from")
    tipo_mensaje = mensaje.get("type")
    mensaje_id = mensaje.get("id")
    if not numero_crudo or not tipo_mensaje:
        return

    if _mensaje_ya_visto(mensaje_id):
        return

    try:
        timestamp_mensaje = int(mensaje.get("timestamp", 0))
    except (TypeError, ValueError):
        timestamp_mensaje = 0
    if timestamp_mensaje and (int(time.time()) - timestamp_mensaje) > ANTIGUEDAD_MAXIMA_MENSAJE:
        print(f"[WEBHOOK]: mensaje {mensaje_id} demasiado viejo; se descarta.")
        return

    numero_usuario = normalizar_numero_mexicano(numero_crudo)

    # Fase 4 — 'escribiendo…' ANTES de ponerse a trabajar. Va aquí y no más
    # abajo porque a partir de este punto el mensaje ya se va a atender seguro
    # (es nuevo, reciente y de un número válido), y cada segundo que se retrase
    # es un segundo de silencio para el atleta.
    #
    # Fire-and-forget a propósito: el webhook tiene que devolverle 200 a Meta
    # cuanto antes o reintenta la entrega, así que esperar a la Graph API aquí
    # sería cambiar un problema de percepción por uno real.
    _lanzar_escribiendo(mensaje_id, numero_usuario)

    # =========================================================
    # GANCHO V9 (1/2): INTERCEPTOR DE BOTONES (READINESS)
    # =========================================================
    # La plantilla aprobada en Meta tiene los botones SIN emoji ("Al 100"), así
    # que comparar contra "Al 100 💪" nunca casaba y la respuesta se colaba al
    # flujo normal del LLM.
    #   · Pulsación real de botón -> comparación normalizada (es_boton_readiness),
    #     acepta con y sin emoji.
    #   · Texto libre -> se exige la forma CON emoji, para que un "normal" suelto
    #     en otra conversación no se secuestre.
    texto_boton, es_pulsacion = _texto_de_boton(mensaje, tipo_mensaje)

    if texto_boton and (es_boton_readiness(texto_boton) if es_pulsacion
                        else texto_boton in BOTONES_READINESS):
        print(f"[PROACTIVIDAD]: Botón de readiness recibido de {numero_usuario}: {texto_boton}")
        # También va por la cola: así el readiness queda ANTES del turno en el que
        # el atleta pregunta qué le toca hoy, que es el orden causal que espera.
        await _encolar_y_avisar(Trabajo(
            numero=numero_usuario, tipo="otro", etiqueta=f"readiness[{texto_boton}]",
            ejecutar=lambda: responder_readiness_en_segundo_plano(numero_usuario,
                                                                 texto_boton)))
        return
    # =========================================================

    if tipo_mensaje == "video":
        video = mensaje.get("video") or {}
        media_id = video.get("id")
        if not media_id:
            return
        texto_recibido = (video.get("caption") or "").strip()

        if not contiene_peso(texto_recibido):
            # Se marca AQUÍ, no al ejecutar: el texto con los kilos puede llegar
            # en el mismo lote y tiene que ver que hay un video esperando.
            videos_pendientes[numero_usuario] = {
                "id": media_id,
                "timestamp": time.time(),
            }
            await _encolar_y_avisar(Trabajo(
                numero=numero_usuario, tipo="video", etiqueta=f"video[{media_id[-6:]}]",
                ejecutar=lambda: procesar_prediccion_rapida(numero_usuario, media_id)))
            return

        ejercicio = corregir_ejercicio_usuario(texto_recibido, "Ejercicio No Especificado")
        carga = extraer_peso(texto_recibido)
        await _encolar_y_avisar(Trabajo(
            numero=numero_usuario, tipo="video",
            etiqueta=f"video+datos[{ejercicio} {carga:g}kg]" if carga else "video+datos",
            ejecutar=lambda: procesar_y_enviar_mensaje(
                numero_usuario, texto_recibido, media_id,
                nombre_ejercicio=ejercicio, carga_kg=carga)))

    elif tipo_mensaje == "audio":
        audio_id = (mensaje.get("audio") or {}).get("id")
        if audio_id:
            # v14 — La descarga y Whisper arrancan YA, en paralelo con el turno
            # que esté ocupando la GPU (ver TRANSCRIPCIÓN ADELANTADA). El trabajo
            # se encola en este mismo instante, así que el orden de la
            # conversación es exactamente el de antes.
            tarea_texto = lanzar_transcripcion(numero_usuario, audio_id)
            await _encolar_y_avisar(Trabajo(
                numero=numero_usuario, tipo="audio", etiqueta=f"audio[{audio_id[-6:]}]",
                ejecutar=lambda: procesar_audio_en_segundo_plano(
                    numero_usuario, audio_id, tarea_texto)))

    elif tipo_mensaje == "text":
        texto_recibido = texto_boton or ""
        if not texto_recibido:
            return

        if "[ERROR]" in texto_recibido or "[INFO]" in texto_recibido:
            return

        await _encolar_y_avisar(_trabajo_texto(numero_usuario, texto_recibido))

    elif tipo_mensaje == "reaction":
        # Un ❤️ a un mensaje anterior no pide respuesta: contestar aquí sería ruido.
        print(f"[WEBHOOK] reacción de {numero_usuario}; sin respuesta (correcto)")

    else:
        # Antes: silencio absoluto. Una foto de la máquina del gimnasio, un
        # sticker o un PDF no entraban en ninguna rama y el atleta se quedaba sin
        # respuesta, con toda la pinta de que el coach se había caído.
        respuestas = {
            "image":    ("Las fotos todavía no las leo bro 🙏 Si quieres que te revise la "
                         "técnica, mándame un *video* de la serie; y si son datos, "
                         "escríbemelos (ej. 'banca 100x5 rpe 8')."),
            "sticker":  "😄 Te leo. Cuéntame qué tal va el entreno.",
            "document": ("No puedo abrir documentos por aquí bro. Escríbeme lo importante "
                         "y lo trabajamos igual."),
            "location": "Gracias, pero con la ubicación no hago nada 😅 ¿Cómo va el entreno?",
            "contacts": "Me llegó un contacto, pero lo mío son tus series 💪 ¿Qué necesitas?",
        }
        aviso = respuestas.get(tipo_mensaje,
                               "Eso no lo puedo abrir bro 🙏 Mándame texto, un audio o un "
                               "video de tu serie y seguimos.")
        # v14 — Este aviso NO pasa por la cola ni por el orquestador: sale al
        # instante. Si el atleta ya tiene un turno en vuelo, se le cuela por
        # delante de la respuesta que está esperando y lee dos mensajes del coach
        # sin relación. Medido el 2026-07-30: mandó un sticker mientras el turno
        # trabajaba y recibió "😄 Te leo. Cuéntame qué tal va el entreno" justo
        # antes de la respuesta real. Un sticker no pide contestación urgente:
        # con trabajo suyo en marcha, se calla.
        if tiene_trabajo(numero_usuario):
            print(f"[WEBHOOK] tipo '{tipo_mensaje}' de {numero_usuario} con turno en "
                  f"curso; NO se avisa (evita el mensaje doble)")
            return
        print(f"[WEBHOOK] tipo '{tipo_mensaje}' no procesable de {numero_usuario}; se avisa")
        try:
            await enviar_whatsapp(numero_usuario, aviso)
        except Exception as e:
            print(f"[WEBHOOK] no se pudo avisar del tipo no soportado: {e}")


@router.post("/webhook")
async def recibir_mensaje_whatsapp(request: Request):
    """Valida, deduplica y ENCOLA. El trabajo real lo hace el worker del atleta.

    Ya no hay `BackgroundTasks`: sus tareas corrían en serie dentro de la misma
    entrega, así que un lote con mensajes de dos atletas hacía esperar al segundo
    detrás del pipeline completo del primero (hasta 2 minutos si era un video).
    """
    try:
        # La firma se calcula sobre el cuerpo EXACTO recibido, así que hay que leer
        # los bytes crudos antes de parsear el JSON.
        cuerpo_crudo = await request.body()
        if not firma_valida(cuerpo_crudo, request.headers.get("X-Hub-Signature-256")):
            raise HTTPException(status_code=403, detail="Firma invalida")
        cuerpo_peticion = json.loads(cuerpo_crudo)

        # Meta agrupa varios mensajes en una sola entrega cuando llegan juntos.
        # Antes solo se leía entry[0].changes[0].value.messages[0]: TODO lo demás
        # se descartaba en silencio (dos mensajes seguidos del atleta = el
        # segundo nunca existió para el sistema).
        for entrada in cuerpo_peticion.get("entry") or []:
            for cambio in entrada.get("changes") or []:
                valor = cambio.get("value") or {}
                for mensaje in valor.get("messages") or []:
                    try:
                        await _procesar_mensaje(mensaje)
                    except Exception as e_msg:
                        print(f"[ERROR WEBHOOK MENSAJE]: {e_msg}")

        return {"status": "success"}

    except HTTPException:
        # 403 de firma inválida: debe llegar como 403 al cliente, no convertirse en
        # un 200 con {"status": "error"} por culpa del except genérico de abajo.
        raise
    except Exception as e:
        print(f"[ERROR WEBHOOK]: {e}")
        return {"status": "error"}
