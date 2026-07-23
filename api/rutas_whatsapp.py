import re
import time
import asyncio
from fastapi import APIRouter, Request, HTTPException, Response, BackgroundTasks
from fastapi.responses import PlainTextResponse
from pathlib import Path
import httpx
import os
from datetime import datetime
import shutil

# Importaciones de configuración y servicios propios
from core.config import settings
from data.meta_service import enviar_mensaje_whatsapp
from services.llm_service import generar_respuesta_coach
from services.audio_service import procesar_audio_whatsapp
from services.vbt_service import procesar_y_responder_video_vbt
from services.vision_service import predecir_ejercicio

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

# Expresión regular para el número telefónico mexicano
RE_NUMERO_MEXICANO = r'^521(\d{10})$'
# Tiempo de expiración para limpieza de caché (10 minutos)
EXPIRATION_TIME = 600

# ==========================================
# ESTADO DE LA APLICACIÓN (MEMORIA VOLÁTIL)
# ==========================================
videos_pendientes = {}
mensajes_procesados = set()
usuarios_procesando = set()


def corregir_ejercicio_usuario(texto_usuario: str, ejercicio_ia: str) -> str:
    """Analiza el texto del atleta para detectar si corrigió a la IA."""
    t = texto_usuario.lower()
    if any(x in t for x in ["peso muerto", "deadlift", "sumo", "rdl"]):
        return "peso_muerto"
    if any(x in t for x in ["sentadilla", "squat", "hack"]):
        return "sentadilla"
    if any(x in t for x in ["press", "banca", "bench", "pecho"]):
        return "press_banca"
    if any(x in t for x in ["dominada", "pull up", "jalon"]):
        return "dominadas"
    return ejercicio_ia

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
        ahora = time.time()
        a_eliminar = [
            num for num, data in videos_pendientes.items()
            if ahora - data["timestamp"] > EXPIRATION_TIME
        ]
        for num in a_eliminar:
            print(f"[CLEANUP]: Eliminando video expirado de {num}")
            del videos_pendientes[num]

def normalizar_numero_mexicano(numero: str) -> str:
    patron_mexicano = r"^521(\d{10})$"
    return re.sub(patron_mexicano, r"52\1", numero)

def contiene_peso(texto: str) -> bool:
    if not texto: return False
    patron_peso = r"\b\d+(?:[.,]\d+)?\s*(?:kg|kilos?|k)?\b"
    return bool(re.search(patron_peso, texto, re.IGNORECASE))

def extraer_peso(texto: str) -> float:
    if not texto: return None
    match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(?:kg|kilos?|k)?\b", texto, re.IGNORECASE)
    if match:
        return float(match.group(1).replace(",", "."))
    return None

async def procesar_y_enviar_mensaje(
        numero_usuario: str, texto_usuario: str = None, media_id: str = None, nombre_ejercicio:str = None, carga_kg: float = None
):
    try:
        print(f"Iniciando procesamiento en segundo plano para {numero_usuario}...")
        mensaje_final_para_llm = ""

        # FLUJO 1: PROCESAMIENTO DE VIDEO (VBT) Y FUSIÓN DE DATOS
        if media_id and media_id != "0":
            print("[INFO]: Iniciando pipeline de video desde WhatsApp...")
            texto_contexto_vbt = await procesar_y_responder_video_vbt(
                media_id_entrada=media_id, numero_usuario=numero_usuario, nombre_ejercicio=nombre_ejercicio, carga_kg=carga_kg
            )
            texto_limpio = texto_contexto_vbt.strip() if texto_contexto_vbt else ""

            if texto_limpio and "[ERROR]" not in texto_limpio:
                print("[ORQUESTADOR]: Video procesado. FUSIONANDO EJERCICIO, PESO Y MÉTRICAS...")
                info_declarada = texto_usuario if texto_usuario else "Datos no especificados"
                mensaje_final_para_llm = (
                    f"TELEMETRÍA DE VIDEO VBT RECIBIDA.\n"
                    f"Detalles de la serie (Ejercicio y Peso): {info_declarada}\n\n"
                    f"{texto_limpio}"
                )
            elif "[ERROR]" in texto_limpio:
                print("[ORQUESTADOR]: Fallo detectado en el contenido.")
                return
            else:
                return

        # FLUJO 2: TEXTO NORMAL
        elif texto_usuario:
            print("[INFO]: Tenemos un mensaje de texto normal")
            mensaje_final_para_llm = texto_usuario
        else:
            print("[LLM]: No se recibió ni texto ni video válido.")
            return

        respuesta_ia = await generar_respuesta_coach(numero_usuario, mensaje_final_para_llm)

        if not respuesta_ia or respuesta_ia.strip() == "":
            print("La IA solo generó eventos internos. Se cancela el envío a WhatsApp.")
            return

        enviar_mensaje_whatsapp(telefono_destino=numero_usuario, texto=respuesta_ia)
        print(f"Mensaje enviado con éxito a {numero_usuario}")

    except Exception as e:
        print(f"Error en la tarea en segundo plano: {e}")

async def tarea_en_segundo_plano_segura(numero_usuario: str, texto: str, media_id: str) -> None:
    usuarios_procesando.add(numero_usuario)
    try:
        await procesar_y_enviar_mensaje(numero_usuario, texto, media_id)
    finally:
        if numero_usuario in usuarios_procesando:
            usuarios_procesando.remove(numero_usuario)

async def tarea_en_segundo_plano_video(numero_usuario: str, texto: str, media_id: str, nombre_ejercicio: str, carga_kg: float = None):
    usuarios_procesando.add(numero_usuario)
    try:
        await procesar_y_enviar_mensaje(numero_usuario, texto, media_id, nombre_ejercicio=nombre_ejercicio, carga_kg=carga_kg)
    finally:
        if numero_usuario in usuarios_procesando:
            usuarios_procesando.remove(numero_usuario)

@router.get("/webhook")
async def verificar_webhook_meta(request: Request):
    parametros = request.query_params
    modo = parametros.get("hub.mode")
    token = parametros.get("hub.verify_token")
    desafio = parametros.get("hub.challenge")

    if modo and token:
        if modo == "subscribe" and token == settings.VERIFY_TOKEN:
            return PlainTextResponse(content=desafio, status_code=200)
        else:
            raise HTTPException(status_code=403, detail="Token invalido")
    raise HTTPException(status_code=400, detail="Faltan parametros")


async def procesar_prediccion_rapida(numero_usuario: str, media_id: str):
    print(f"[IA VISION]: Analizando video {media_id} para detectar ejercicio...")
    carpeta_temp = "data/temp_vision"
    carpeta_dataset = "data/dataset_recoleccion"
    os.makedirs(carpeta_temp, exist_ok=True)
    os.makedirs(carpeta_dataset, exist_ok=True)
    ruta_temporal = os.path.join(carpeta_temp, f"temp_{media_id}.mp4")

    try:
        headers_auth = {"Authorization": f"Bearer {settings.ACCESS_TOKEN}"}
        url_info = f"https://graph.facebook.com/v18.0/{media_id}"

        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            respuesta_info = await client.get(url_info, headers=headers_auth)
            if respuesta_info.status_code == 200:
                url_descarga = respuesta_info.json().get("url")
                respuesta_video = await client.get(url_descarga, headers=headers_auth)
                with open(ruta_temporal, "wb") as f:
                    f.write(respuesta_video.content)

        ejercicio_detectado = predecir_ejercicio(ruta_temporal)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ej_limpio = ejercicio_detectado.replace("_", " ") if ejercicio_detectado != "Ejercicio_Desconocido" else "No_Reconocido"

        carpeta_ejercicio = os.path.join(carpeta_dataset, ej_limpio.replace(" ", "_"))
        os.makedirs(carpeta_ejercicio, exist_ok=True)
        nombre_final = f"{timestamp}_atleta_{numero_usuario[-4:]}_{media_id}.mp4"
        ruta_final = os.path.join(carpeta_ejercicio, nombre_final)
        shutil.copy2(ruta_temporal, ruta_final)

        if ejercicio_detectado != "Ejercicio_Desconocido":
            videos_pendientes[numero_usuario]["ejercicio_detectado"] = ej_limpio
            msj = (f"¡Video recibido!\n\nMi sistema detecta que hiciste *{ej_limpio}*.\n\n"
                   f"¿Es correcto? Confírmamelo y dime con cuántos kilos lo hiciste para analizar tu velocidad.\n"
                   f"*(Ej: 'Sí, 120kg' o 'No, fue Remo con 80kg')*")
        else:
            videos_pendientes[numero_usuario]["ejercicio_detectado"] = "Movimiento Libre"
            msj = (f"🎥 ¡Video recibido!\n\nNo pude clasificar el movimiento automáticamente (lo guardaré como *Movimiento Libre* por ahora).\n\n"
                   f"¿Qué ejercicio fue exactamente y con cuántos kilos lo hiciste? Intentaré medir tu velocidad con el VBT de todos modos.")

        enviar_mensaje_whatsapp(telefono_destino=numero_usuario, texto=msj)
    except Exception as e:
        print(f"[ERROR IA VISION]: {e}")
        enviar_mensaje_whatsapp(telefono_destino=numero_usuario, texto="¡Video recibido!\n\n¿Qué ejercicio es y con cuántos kilos lo hiciste?\n(Ejemplo: 'Press de banca, 80kg')")
    finally:
        if os.path.exists(ruta_temporal):
            os.remove(ruta_temporal)


@router.post("/webhook")
async def recibir_mensaje_whatsapp(request: Request, background_tasks: BackgroundTasks):
    try:
        cuerpo_peticion = await request.json()

        if "entry" in cuerpo_peticion and len(cuerpo_peticion["entry"]) > 0:
            cambios = cuerpo_peticion["entry"][0].get("changes", [])

            if len(cambios) > 0:
                valor = cambios[0].get("value", {})
                mensajes = valor.get("messages", [])

                if mensajes:
                    mensaje = mensajes[0]
                    numero_crudo = mensaje.get("from")
                    tipo_mensaje = mensaje.get("type")
                    mensaje_id = mensaje.get("id")

                    if mensaje_id in mensajes_procesados:
                        return {"status": "success"}

                    mensajes_procesados.add(mensaje_id)
                    if len(mensajes_procesados) > 1000:
                        mensajes_procesados.clear()

                    timestamp_mensaje = int(mensaje.get("timestamp", 0))
                    tiempo_actual = int(time.time())
                    if (tiempo_actual - timestamp_mensaje) > 300:
                        return {"status": "success"}

                    numero_usuario = normalizar_numero_mexicano(numero_crudo)

                    if numero_usuario in usuarios_procesando:
                        return {"status": "success"}

                    # =========================================================
                    # GANCHO V9 (1/2): INTERCEPTOR DE BOTONES (READINESS)
                    # =========================================================
                    # La plantilla aprobada en Meta tiene los botones SIN emoji
                    # ("Al 100"), así que comparar contra "Al 100 💪" nunca casaba
                    # y la respuesta se colaba al flujo normal del LLM.
                    #   · Pulsación real de botón -> comparación normalizada
                    #     (es_boton_readiness), acepta con y sin emoji.
                    #   · Texto libre -> se exige la forma CON emoji, para que un
                    #     "normal" suelto en otra conversación no se secuestre.
                    texto_boton = None
                    es_pulsacion = False
                    if tipo_mensaje == "button":
                        texto_boton = mensaje["button"]["text"].strip()
                        es_pulsacion = True
                    elif tipo_mensaje == "interactive" and mensaje["interactive"]["type"] == "button_reply":
                        texto_boton = mensaje["interactive"]["button_reply"]["title"].strip()
                        es_pulsacion = True
                    elif tipo_mensaje == "text":
                        texto_boton = mensaje["text"]["body"].strip()

                    if texto_boton and (es_boton_readiness(texto_boton) if es_pulsacion
                                        else texto_boton in BOTONES_READINESS):
                        print(f"[PROACTIVIDAD]: Botón de readiness recibido de {numero_usuario}: {texto_boton}")
                        veredicto = procesar_respuesta_readiness(numero_usuario, texto_boton)
                        print(f"[PROACTIVIDAD]: veredicto -> {veredicto}")
                        enviar_mensaje_whatsapp(
                            telefono_destino=numero_usuario,
                            texto=redactar_respuesta_readiness(veredicto),
                        )
                        return {"status": "success"}
                    # =========================================================

                    if tipo_mensaje == "video":
                        media_id = mensaje["video"]["id"]
                        texto_recibido = mensaje["video"].get("caption", "").strip()

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
                            "Ejercicio No Especificado"
                        )

                    elif tipo_mensaje == "audio":
                        audio_id = mensaje["audio"]["id"]
                        texto_transcrito = await procesar_audio_whatsapp(audio_id)

                        # =========================================================
                        # GANCHO V9 (2/2): FILTRO DE AUDIO CAÓTICO
                        # =========================================================
                        print(f"[WHISPER ORIGINAL]: {texto_transcrito}")
                        texto_transcrito = await filtrar_audio_triada(numero_usuario, texto_transcrito)
                        print(f"[WHISPER FILTRADO]: {texto_transcrito}")
                        # =========================================================

                        if numero_usuario in videos_pendientes:
                            if not contiene_peso(texto_transcrito):
                                enviar_mensaje_whatsapp(
                                    telefono_destino=numero_usuario,
                                    texto="No logré identificar los kilos en tu audio. Escríbeme el peso (ej. '140kg') para analizar el VBT."
                                )
                                return

                            video_id_esperando = videos_pendientes[numero_usuario]["id"]
                            ej_previo = videos_pendientes[numero_usuario].get("ejercicio_detectado", "Movimiento Libre")
                            del videos_pendientes[numero_usuario]

                            texto_fusionado = f"Ejercicio detectado por IA: {ej_previo}. Atleta responde: {texto_transcrito}"
                            background_tasks.add_task(
                                tarea_en_segundo_plano_video,
                                numero_usuario,
                                texto_fusionado,
                                video_id_esperando,
                                ej_previo
                            )
                        else:
                            background_tasks.add_task(
                                tarea_en_segundo_plano_segura,
                                numero_usuario,
                                texto_transcrito,
                                None,
                            )

                    elif tipo_mensaje == "text":
                        texto_recibido = mensaje["text"]["body"].strip()

                        if "[ERROR]" in texto_recibido or "[INFO]" in texto_recibido:
                            return

                        if numero_usuario in videos_pendientes:
                            if not contiene_peso(texto_recibido):
                                ej_previo = videos_pendientes[numero_usuario].get("ejercicio_detectado", "")
                                ej_actualizado = corregir_ejercicio_usuario(texto_recibido, ej_previo)
                                videos_pendientes[numero_usuario]["ejercicio_detectado"] = ej_actualizado

                                enviar_mensaje_whatsapp(
                                    telefono_destino=numero_usuario,
                                    texto="No logré identificar los kilos en tu mensaje. Escríbeme el peso (ej. '140kg') para poder iniciar el análisis VBT."
                                )
                                return

                            video_id_esperando = videos_pendientes[numero_usuario]["id"]
                            ej_previo = videos_pendientes[numero_usuario].get("ejercicio_detectado", "Movimiento Libre")
                            del videos_pendientes[numero_usuario]

                            ej_corregido = corregir_ejercicio_usuario(texto_recibido, ej_previo)
                            kilos_detectados = extraer_peso(texto_recibido)

                            texto_fusionado = f"IA detectó inicialmente: {ej_previo}. Atleta corrigió/confirmó con: {texto_recibido}"

                            background_tasks.add_task(
                                tarea_en_segundo_plano_video,
                                numero_usuario,
                                texto_fusionado,
                                video_id_esperando,
                                ej_corregido,
                                kilos_detectados
                            )
                        else:
                            background_tasks.add_task(
                                tarea_en_segundo_plano_segura,
                                numero_usuario,
                                texto_recibido,
                                None
                            )

        return {"status": "success"}

    except Exception as e:
        print(f"[ERROR WEBHOOK]: {e}")
        return {"status": "error"}