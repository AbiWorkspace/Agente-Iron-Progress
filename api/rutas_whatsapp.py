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
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse

# Importaciones de configuración y servicios propios
from core.config import settings
from core.ejercicios import canonizar, detectar
from data.meta_service import enviar_mensaje_whatsapp
from services.llm_service import generar_respuesta_coach
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
usuarios_procesando: set[str] = set()

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
    print("[SEGURIDAD]: META_APP_SECRET no está definido en el .env. El webhook "
          "aceptará CUALQUIER petición sin verificar que venga de Meta. Defínelo "
          "para activar la validación de firma (X-Hub-Signature-256).")


def firma_valida(cuerpo_crudo: bytes, cabecera_firma: str | None) -> bool:
    """Verifica el X-Hub-Signature-256 con el que Meta firma cada webhook.

    Sin esta comprobación, cualquiera que conozca la URL puede inyectar mensajes
    falsos y hacer que el coach escriba por WhatsApp a un atleta real. Si
    META_APP_SECRET no está configurado se acepta la petición (con el aviso de
    arriba) para no romper despliegues existentes.
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


async def tarea_en_segundo_plano_segura(numero_usuario: str, texto: str, media_id: str) -> None:
    usuarios_procesando.add(numero_usuario)
    try:
        await procesar_y_enviar_mensaje(numero_usuario, texto, media_id)
    finally:
        usuarios_procesando.discard(numero_usuario)


async def tarea_en_segundo_plano_video(numero_usuario: str, texto: str, media_id: str,
                                       nombre_ejercicio: str, carga_kg: float = None):
    usuarios_procesando.add(numero_usuario)
    try:
        await procesar_y_enviar_mensaje(numero_usuario, texto, media_id,
                                        nombre_ejercicio=nombre_ejercicio, carga_kg=carga_kg)
    finally:
        usuarios_procesando.discard(numero_usuario)


async def procesar_audio_en_segundo_plano(numero_usuario: str, audio_id: str) -> None:
    """Transcribe el audio y lo despacha FUERA del ciclo de petición del webhook.

    Antes esto ocurría dentro del handler: Whisper (5-30 s) más el extractor de
    tríada podían superar el timeout de Meta, que marcaba la entrega como fallida y
    la reintentaba. Ahora el webhook responde 200 al instante y el trabajo pasa aquí.
    """
    usuarios_procesando.add(numero_usuario)
    try:
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
    finally:
        usuarios_procesando.discard(numero_usuario)


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


async def _procesar_mensaje(mensaje: dict, background_tasks: BackgroundTasks) -> None:
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

    if numero_usuario in usuarios_procesando:
        print(f"[WEBHOOK]: {numero_usuario} ya tiene un mensaje en proceso; se descarta el nuevo.")
        return

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
        background_tasks.add_task(responder_readiness_en_segundo_plano,
                                  numero_usuario, texto_boton)
        return
    # =========================================================

    if tipo_mensaje == "video":
        video = mensaje.get("video") or {}
        media_id = video.get("id")
        if not media_id:
            return
        texto_recibido = (video.get("caption") or "").strip()

        if not contiene_peso(texto_recibido):
            videos_pendientes[numero_usuario] = {
                "id": media_id,
                "timestamp": time.time(),
            }
            background_tasks.add_task(procesar_prediccion_rapida, numero_usuario, media_id)
            return

        background_tasks.add_task(
            tarea_en_segundo_plano_video,
            numero_usuario,
            texto_recibido,
            media_id,
            corregir_ejercicio_usuario(texto_recibido, "Ejercicio No Especificado"),
            extraer_peso(texto_recibido),
        )

    elif tipo_mensaje == "audio":
        # Todo el trabajo del audio (Whisper + extractor de tríada) se encola:
        # ver procesar_audio_en_segundo_plano.
        audio_id = (mensaje.get("audio") or {}).get("id")
        if audio_id:
            background_tasks.add_task(procesar_audio_en_segundo_plano,
                                      numero_usuario, audio_id)

    elif tipo_mensaje == "text":
        texto_recibido = texto_boton or ""
        if not texto_recibido:
            return

        if "[ERROR]" in texto_recibido or "[INFO]" in texto_recibido:
            return

        if numero_usuario in videos_pendientes:
            if not contiene_peso(texto_recibido):
                ej_previo = videos_pendientes[numero_usuario].get("ejercicio_detectado", "")
                ej_actualizado = corregir_ejercicio_usuario(texto_recibido, ej_previo)
                videos_pendientes[numero_usuario]["ejercicio_detectado"] = ej_actualizado

                background_tasks.add_task(
                    enviar_whatsapp,
                    numero_usuario,
                    "No logré identificar los kilos en tu mensaje. Escríbeme el peso "
                    "(ej. '140kg') para poder iniciar el análisis VBT.")
                return

            datos_video = videos_pendientes.pop(numero_usuario, {})
            video_id_esperando = datos_video.get("id")
            ej_previo = datos_video.get("ejercicio_detectado", "Movimiento Libre")

            ej_corregido = corregir_ejercicio_usuario(texto_recibido, ej_previo)
            kilos_detectados = extraer_peso(texto_recibido)

            texto_fusionado = (f"IA detectó inicialmente: {ej_previo}. "
                               f"Atleta corrigió/confirmó con: {texto_recibido}")

            background_tasks.add_task(
                tarea_en_segundo_plano_video,
                numero_usuario,
                texto_fusionado,
                video_id_esperando,
                ej_corregido,
                kilos_detectados,
            )
        else:
            background_tasks.add_task(
                tarea_en_segundo_plano_segura,
                numero_usuario,
                texto_recibido,
                None,
            )


@router.post("/webhook")
async def recibir_mensaje_whatsapp(request: Request, background_tasks: BackgroundTasks):
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
                        await _procesar_mensaje(mensaje, background_tasks)
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
