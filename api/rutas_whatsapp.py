import re
import time
import asyncio
from fastapi import APIRouter, Request, HTTPException, Response, BackgroundTasks
from fastapi.responses import PlainTextResponse
from pathlib import Path
from services.vbt_service import procesar_y_responder_video_vbt
# Importaciones de configuración y servicios propios
from core.config import settings
from data.meta_service import enviar_mensaje_whatsapp
from services.llm_service import generar_respuesta_coach
from services.audio_service import procesar_audio_whatsapp
import httpx
from services.vision_service import predecir_ejercicio
import os
from datetime import datetime
import shutil
# ==========================================
# CONFIGURACIÓN Y CONSTANTES
# ==========================================
router = APIRouter()

# Expresión regular para el número telefónico mexicano (evitando el error de '512')
RE_NUMERO_MEXICANO = r'^521(\d{10})$'

# Tiempo de expiración para limpieza de caché (10 minutos)
EXPIRATION_TIME = 600

# ==========================================
# ESTADO DE LA APLICACIÓN (MEMORIA VOLÁTIL)
# ==========================================
# Estructura: { "numero": {"id": "media_id", "timestamp": 171123456} }
videos_pendientes = {}

# Set para evitar procesar el mismo webhook de Meta dos veces
mensajes_procesados = set()
usuarios_procesando = set()

async def limpiar_cache_videos():
    while True:
        await asyncio.sleep(300)  # Se ejecuta cada 5 minutos
        ahora = time.time()
        # Creamos una lista de llaves a borrar para evitar error de "dict size changed during iteration"
        a_eliminar = [
            num
            for num, data in videos_pendientes.items()
            if ahora - data["timestamp"] > EXPIRATION_TIME
        ]

        for num in a_eliminar:
            print(f"[CLEANUP]: Eliminando video expirado de {num}")
            del videos_pendientes[num]

def normalizar_numero_mexicano(numero: str) -> str:
    """
    Interviene números mexicanos entrantes de WhatsApp.
    Convierte formatos como '5217291395298' a '527291395298'.
    """
    patron_mexicano = r"^521(\d{10})$"
    numero_limpio = re.sub(patron_mexicano, r"52\1", numero)
    return numero_limpio
def contiene_peso(texto: str) -> bool:
    """
    Busca un número, opcionalmente acompañado de kg, k o kilos.
    Ejemplos válidos: "120", "120kg", "120 kg", "120.5", "fueron 100"
    """
    if not texto:
        return False
    # Regex: Busca dígitos enteros o decimales, y opcionalmente la palabra kg/kilos
    patron_peso = r"\b\d+(?:[.,]\d+)?\s*(?:kg|kilos?|k)?\b"
    return bool(re.search(patron_peso, texto, re.IGNORECASE))

async def procesar_y_enviar_mensaje(
        numero_usuario: str, texto_usuario: str = None, media_id: str = None, nombre_ejercicio:str = None
):
    """
    Esta función vivirá en segundo plano.
    """
    try:
        print(f"Iniciando procesamiento en segundo plano para {numero_usuario}...")
        mensaje_final_para_llm = ""

        # =====================================================================
        # FLUJO 1: PROCESAMIENTO DE VIDEO (VBT) Y FUSIÓN DE DATOS
        # =====================================================================
        if media_id and media_id != "0":
            print("[INFO]: Iniciando pipeline de video desde WhatsApp...")
            texto_contexto_vbt = await procesar_y_responder_video_vbt(
                media_id_entrada=media_id, numero_usuario=numero_usuario, nombre_ejercicio= nombre_ejercicio
            )
            texto_limpio = texto_contexto_vbt.strip() if texto_contexto_vbt else ""

            # 2. Casos de Éxito del Video
            if texto_limpio and "[ERROR]" not in texto_limpio:
                print("[ORQUESTADOR]: Video procesado. FUSIONANDO EJERCICIO, PESO Y MÉTRICAS...")

                # Capturamos la respuesta del usuario que ahora debe traer (Ejercicio + Peso)
                info_declarada = (
                    texto_usuario if texto_usuario else "Datos no especificados"
                )

                mensaje_final_para_llm = (
                    f"TELEMETRÍA DE VIDEO VBT RECIBIDA.\n"
                    f"Detalles de la serie (Ejercicio y Peso): {info_declarada}\n\n"
                    f"{texto_limpio}"
                )
                print(f'Aqui el mensaje que devolvio el VBT:\n{mensaje_final_para_llm}')

            # 3. Caso de Error en el Video
            elif "[ERROR]" in texto_limpio:
                print("[ORQUESTADOR]: Fallo detectado en el contenido.")
                return
            else:
                return

                # =====================================================================
        # FLUJO 2: TEXTO NORMAL (Sin Video)
        # =====================================================================
        elif texto_usuario:
            print("[INFO]: Tenemos un mensaje de texto normal")
            mensaje_final_para_llm = texto_usuario

        else:
            print("[LLM]: No se recibió ni texto ni video válido.")
            return

        respuesta_ia = await generar_respuesta_coach(
            numero_usuario, mensaje_final_para_llm
        )

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

async def tarea_en_segundo_plano_video(numero_usuario: str, texto: str, media_id: str, nombre_ejercicio: str):
    usuarios_procesando.add(numero_usuario)
    try:
        await procesar_y_enviar_mensaje(numero_usuario, texto, media_id, nombre_ejercicio= nombre_ejercicio)
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
    """Descarga el video rápido, adivina el ejercicio, lo archiva para reentrenamiento y avisa al usuario."""
    print(f"[IA VISION]: Analizando video {media_id} para detectar ejercicio...")

    # 1. ESTRUCTURA DE CARPETAS (Arquitectura Mantenible)
    carpeta_temp = "data/temp_vision"
    carpeta_dataset = "data/dataset_recoleccion"

    os.makedirs(carpeta_temp, exist_ok=True)
    os.makedirs(carpeta_dataset, exist_ok=True)

    ruta_temporal = os.path.join(carpeta_temp, f"temp_{media_id}.mp4")

    try:
        # 2. Descarga del video
        headers_auth = {"Authorization": f"Bearer {settings.ACCESS_TOKEN}"}
        url_info = f"https://graph.facebook.com/v18.0/{media_id}"

        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            respuesta_info = await client.get(url_info, headers=headers_auth)
            if respuesta_info.status_code == 200:
                url_descarga = respuesta_info.json().get("url")
                respuesta_video = await client.get(url_descarga, headers=headers_auth)
                with open(ruta_temporal, "wb") as f:
                    f.write(respuesta_video.content)

        # 3. La IA Visual hace su predicción
        ejercicio_detectado = predecir_ejercicio(ruta_temporal)

        # 4. DATA FLYWHEEL: Guardado inteligente para futuro reentrenamiento
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ej_limpio = ejercicio_detectado.replace("_",
                                                " ") if ejercicio_detectado != "Ejercicio_Desconocido" else "No_Reconocido"

        # Creamos una subcarpeta para el ejercicio específico
        carpeta_ejercicio = os.path.join(carpeta_dataset, ej_limpio.replace(" ", "_"))
        os.makedirs(carpeta_ejercicio, exist_ok=True)

        # Movemos el video con un nombre descriptivo
        nombre_final = f"{timestamp}_atleta_{numero_usuario[-4:]}_{media_id}.mp4"
        ruta_final = os.path.join(carpeta_ejercicio, nombre_final)

        # Copiamos a la bóveda (usamos copy para mantener el original para el VBT si es necesario)
        shutil.copy2(ruta_temporal, ruta_final)
        print(f"[DATASET]: Video guardado exitosamente en bóveda -> {ruta_final}")

        # 5. Interacción con el usuario (WhatsApp)
        if ejercicio_detectado != "Ejercicio_Desconocido":
            videos_pendientes[numero_usuario]["ejercicio_detectado"] = ej_limpio
            msj = (f"¡Video recibido!\n\nMi sistema detecta que hiciste *{ej_limpio}*.\n\n"
                   f"¿Es correcto? Confírmamelo y dime con cuántos kilos lo hiciste para analizar tu velocidad.\n"
                   f"*(Ej: 'Sí, 120kg' o 'No, fue Remo con 80kg')*")
        else:
            videos_pendientes[numero_usuario]["ejercicio_detectado"] = "Movimiento Libre"
            msj = (
                f"🎥 ¡Video recibido!\n\nNo pude clasificar el movimiento automáticamente (lo guardaré como *Movimiento Libre* por ahora).\n\n"
                f"¿Qué ejercicio fue exactamente y con cuántos kilos lo hiciste? Intentaré medir tu velocidad con el VBT de todos modos.")

        enviar_mensaje_whatsapp(telefono_destino=numero_usuario, texto=msj)

    except Exception as e:
        print(f"[ERROR IA VISION]: {e}")
        # Salvavidas para el usuario
        enviar_mensaje_whatsapp(
            telefono_destino=numero_usuario,
            texto="¡Video recibido!\n\n¿Qué ejercicio es y con cuántos kilos lo hiciste?\n(Ejemplo: 'Press de banca, 80kg')"
        )

    finally:
        # 6. LIMPIEZA ABSOLUTA (Evita discos duros llenos y archivos zombies)
        if os.path.exists(ruta_temporal):
            os.remove(ruta_temporal)
            print(f"[SISTEMA]: Archivo temporal {ruta_temporal} eliminado.")
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

                    if tipo_mensaje == "video":
                        media_id = mensaje["video"]["id"]
                        texto_recibido = mensaje["video"].get("caption", "").strip()

                        # VALIDACIÓN 1: Si no trae texto, o el texto NO tiene el peso
                        if not contiene_peso(texto_recibido):
                            videos_pendientes[numero_usuario] = {
                                "id": media_id,
                                "timestamp": time.time(),
                            }
                            background_tasks.add_task(procesar_prediccion_rapida, numero_usuario, media_id)
                            return

                        # 🛠️ CORRECCIÓN: Si manda el peso de golpe, la IA aún no sabe qué ejercicio es.
                        background_tasks.add_task(
                            tarea_en_segundo_plano_video,
                            numero_usuario,
                            texto_recibido,
                            media_id,
                            "Ejercicio No Especificado"  # Se lo mandamos así para que el Coach deduzca por contexto
                        )

                    elif tipo_mensaje == "audio":
                        audio_id = mensaje["audio"]["id"]
                        texto_transcrito = await procesar_audio_whatsapp(audio_id)

                        if numero_usuario in videos_pendientes:
                            # VALIDACIÓN 3: Si el texto responde al video, pero no trae peso
                            # 🛠️ CORRECCIÓN: Aquí se usa texto_transcrito en lugar de texto_recibido
                            if not contiene_peso(texto_transcrito):
                                enviar_mensaje_whatsapp(
                                    telefono_destino=numero_usuario,
                                    texto="No logré identificar los kilos en tu mensaje. Escríbeme el peso (ej. '140kg') para poder iniciar el análisis VBT."
                                )
                                return  # Cortamos aquí, el video sigue pendiente

                            video_id_esperando = videos_pendientes[numero_usuario]["id"]
                            ej_previo = videos_pendientes[numero_usuario].get("ejercicio_detectado", "Movimiento Libre")
                            del videos_pendientes[numero_usuario]

                            # 🛠️ CORRECCIÓN: Aquí se usa texto_transcrito en lugar de texto_recibido
                            texto_fusionado = f"Ejercicio detectado por IA: {ej_previo}. Atleta responde: {texto_transcrito}"

                            # 🛠️ CORRECCIÓN: Usamos la tarea de video y le pasamos el nombre!
                            background_tasks.add_task(
                                tarea_en_segundo_plano_video,
                                numero_usuario,
                                texto_fusionado,
                                video_id_esperando,
                                ej_previo  # <--- ¡ESTE ES EL ESLABÓN PERDIDO!
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
                            # VALIDACIÓN 3: Si el texto responde al video, pero no trae peso
                            if not contiene_peso(texto_recibido):
                                enviar_mensaje_whatsapp(
                                    telefono_destino=numero_usuario,
                                    texto="No logré identificar los kilos en tu mensaje. Escríbeme el peso (ej. '140kg') para poder iniciar el análisis VBT."
                                )
                                return  # Cortamos aquí, el video sigue pendiente

                            video_id_esperando = videos_pendientes[numero_usuario]["id"]
                            ej_previo = videos_pendientes[numero_usuario].get("ejercicio_detectado", "Movimiento Libre")
                            del videos_pendientes[numero_usuario]

                            # 🛠️ CORRECCIÓN: Aquí se usa texto_recibido en lugar de texto_transcrito
                            texto_fusionado = f"Ejercicio detectado por IA: {ej_previo}. Atleta responde: {texto_recibido}"

                            # 🛠️ CORRECCIÓN: Usamos la tarea de video y le pasamos el nombre!
                            background_tasks.add_task(
                                tarea_en_segundo_plano_video,
                                numero_usuario,
                                texto_fusionado,
                                video_id_esperando,
                                ej_previo  # <--- EL NOMBRE VIAJA AQUÍ
                            )
                        else:
                            background_tasks.add_task(
                                tarea_en_segundo_plano_segura,
                                numero_usuario,
                                texto_recibido,
                                None
                            )

                    # --- NOTA: MANTUVE ESTOS BLOQUES INTACTOS A PETICIÓN TUYA ---
                    elif tipo_mensaje == "audio":
                        audio_id = mensaje["audio"]["id"]
                        texto_transcrito = await procesar_audio_whatsapp(audio_id)

                        if numero_usuario in videos_pendientes:
                            video_id_esperando = videos_pendientes[numero_usuario]["id"]
                            ej_previo = videos_pendientes[numero_usuario].get("ejercicio_detectado", "")
                            del videos_pendientes[numero_usuario]

                            # FUSIÓN: Audio + Predicción Visual
                            texto_fusionado = f"Ejercicio detectado por IA: {ej_previo}. Atleta responde: {texto_transcrito}" if ej_previo else texto_transcrito

                            background_tasks.add_task(
                                tarea_en_segundo_plano_segura,
                                numero_usuario,
                                texto_fusionado,
                                video_id_esperando,
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
                            parece_datos_serie = bool(re.search(r"\d+", texto_recibido))
                            if parece_datos_serie:
                                video_id_esperando = videos_pendientes[numero_usuario]["id"]
                                ej_previo = videos_pendientes[numero_usuario].get("ejercicio_detectado", "")
                                del videos_pendientes[numero_usuario]

                                # FUSIÓN: Texto + Predicción Visual
                                texto_fusionado = f"Ejercicio detectado por IA: {ej_previo}. Atleta responde: {texto_recibido}" if ej_previo else texto_recibido

                                background_tasks.add_task(
                                    tarea_en_segundo_plano_segura,
                                    numero_usuario,
                                    texto_fusionado,
                                    video_id_esperando,
                                )
                            else:
                                background_tasks.add_task(
                                    tarea_en_segundo_plano_segura,
                                    numero_usuario,
                                    texto_recibido,
                                    None,
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