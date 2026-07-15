import os
import httpx
import traceback
from core.config import settings
from services.Creardataset import procesar_levantamiento_vbt
import asyncio
from data.meta_service import enviar_mensaje_whatsapp
TOKEN_META = settings.ACCESS_TOKEN
ID_NUMERO_TELEFONO = settings.PHONE_NUMBER_ID
import os
import httpx
import asyncio
import traceback


async def procesar_y_responder_video_vbt(media_id_entrada: str, numero_usuario: str, nombre_ejercicio: str = "Movimiento Libre"):
    """
    Orquestador completo: Descarga de Meta -> Procesa VBT -> Sube a Meta -> Envia WhatsApp.
    """

    # ==============================================================================
    # 1. ENRUTAMIENTO DINÁMICO
    # ==============================================================================
    # Calculamos la raíz (asumiendo que este script está en /services)
    ruta_script_actual = os.path.dirname(os.path.abspath(__file__))
    ruta_raiz_proyecto = os.path.dirname(ruta_script_actual)

    # Definimos las carpetas necesarias
    carpeta_data = os.path.join(ruta_raiz_proyecto, "data")
    carpeta_models = os.path.join(ruta_raiz_proyecto, "models")

    # Seguro de vida por si las carpetas no existen
    os.makedirs(carpeta_data, exist_ok=True)
    os.makedirs(carpeta_models, exist_ok=True)

    # Rutas exactas de los archivos
    ruta_video_original = os.path.join(carpeta_data, f"input_{media_id_entrada}.mp4")
    ruta_video_procesado = os.path.join(carpeta_data, f"output_{media_id_entrada}.mp4")
    ruta_modelo = os.path.join(carpeta_models, "best.pt")

    # ==============================================================================
    # 2. EJECUCIÓN DEL PIPELINE
    # ==============================================================================
    headers_auth = {"Authorization": f"Bearer {TOKEN_META}"}

    try:
        print("[HTTP]: Solicitando URL del video a Meta...")
        url_info = f"https://graph.facebook.com/v18.0/{media_id_entrada}"

        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            respuesta_info = await client.get(url_info, headers=headers_auth)
            if respuesta_info.status_code != 200:
                print(f"[ERROR META]: No se pudo obtener la info del video. {respuesta_info.text}")
                return "[ERROR]: No pude descargar tu video. Intenta enviarlo de nuevo."

            url_descarga = respuesta_info.json().get("url")

            print("[HTTP]: Descargando archivo fisico MP4...")
            respuesta_video = await client.get(url_descarga, headers=headers_auth)

            with open(ruta_video_original, "wb") as f:
                f.write(respuesta_video.content)

        print("[SISTEMA]: Entregando video al motor VBT local...")
        resultado_vbt = procesar_levantamiento_vbt(
            ruta_modelo=ruta_modelo,
            ruta_video=ruta_video_original,
            ruta_salida=ruta_video_procesado,
            media_id=media_id_entrada,
            numero_usuario=numero_usuario,
            nombre_ejercicio=nombre_ejercicio  # <--- ¡AQUÍ ESTÁ LA MAGIA!
        )

        if resultado_vbt["estado"] == "error":
            # 1. Mandas el texto explicando el error
            mensaje_error = resultado_vbt.get(
                "mensaje", "Hubo un error en el análisis de video."
            )
            enviar_mensaje_whatsapp(numero_usuario, f"*Aviso VBT:*\n{mensaje_error}")

            # 2. LA CLAVE CORREGIDA: Subir y enviar el video forense
            if "ruta_video_renderizado" in resultado_vbt and os.path.exists(resultado_vbt["ruta_video_renderizado"]):
                print("[HTTP]: Subiendo video forense a Meta...")
                url_upload = f"https://graph.facebook.com/v18.0/{ID_NUMERO_TELEFONO}/media"

                with open(resultado_vbt["ruta_video_renderizado"], "rb") as f_video:
                    archivos = {
                        "file": (os.path.basename(resultado_vbt["ruta_video_renderizado"]), f_video, "video/mp4")}
                    datos = {"messaging_product": "whatsapp"}

                    async with httpx.AsyncClient(timeout=120.0) as client:
                        respuesta_upload = await client.post(url_upload, headers=headers_auth, data=datos,
                                                             files=archivos)

                        if respuesta_upload.status_code == 200:
                            nuevo_media_id = respuesta_upload.json().get("id")
                            url_mensajes = f"https://graph.facebook.com/v18.0/{ID_NUMERO_TELEFONO}/messages"
                            payload_mensaje = {
                                "messaging_product": "whatsapp",
                                "recipient_type": "individual",
                                "to": numero_usuario,
                                "type": "video",
                                "video": {"id": nuevo_media_id, "caption": "Video de diagnóstico VBT"},
                            }

                            await asyncio.sleep(2)  # Respiro para Meta
                            await client.post(url_mensajes, headers=headers_auth, json=payload_mensaje)
                            print("[EXITO]: Video forense enviado por WhatsApp.")
                        else:
                            print(f"[ERROR UPLOAD FORENSE]: {respuesta_upload.text}")

                # Ahora sí, podemos salir de la función con la conciencia tranquila
                return "Error manejado con video forense enviado."

            return "Error manejado sin video."

        # Meta requiere que el archivo se suba usando multipart/form-data
        print("[HTTP]: Subiendo video renderizado a los servidores de Meta...")
        url_upload = f"https://graph.facebook.com/v18.0/{ID_NUMERO_TELEFONO}/media"

        with open(ruta_video_procesado, "rb") as f_video:
            archivos = {
                "file": (os.path.basename(ruta_video_procesado), f_video, "video/mp4")
            }
            datos = {"messaging_product": "whatsapp"}

            async with httpx.AsyncClient(timeout=120.0) as client:
                respuesta_upload = await client.post(
                    url_upload, headers=headers_auth, data=datos, files=archivos
                )

                if respuesta_upload.status_code != 200:
                    print(f"[ERROR UPLOAD]: {respuesta_upload.text}")
                    return "[ERROR]: El video se proceso, pero hubo un fallo al enviartelo de regreso por WhatsApp."

                nuevo_media_id = respuesta_upload.json().get("id")

                print(f"[HTTP]: Enviando mensaje de video final al usuario {numero_usuario}...")
                url_mensajes = f"https://graph.facebook.com/v18.0/{ID_NUMERO_TELEFONO}/messages"

                payload_mensaje = {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": numero_usuario,
                    "type": "video",
                    "video": {
                        "id": nuevo_media_id,
                        "caption": "Análisis VBT completado. Revisando tus métricas...",
                    },
                }

                # LA CURA: Le damos 2 segundos de respiro a Meta para que encripte el video en sus servidores
                print("[SISTEMA]: Esperando 2 segundos a que Meta procese el MP4...")
                await asyncio.sleep(2)

                async with httpx.AsyncClient() as client:
                    respuesta_envio = await client.post(
                        url_mensajes, headers=headers_auth, json=payload_mensaje
                    )

                    if respuesta_envio.status_code in [200, 201]:
                        print(f"[EXITO META]: Video entregado exitosamente a la API de WhatsApp.")
                    else:
                        print(f"[ERROR META VIDEO]: Meta rechazo el envio del video.")
                        print(f"[DETALLE EXACTO]: {respuesta_envio.text}")

                print("[EXITO]: Pipeline completado. Devolviendo contexto superalimentado para Qwen.")

                # LA CORRECCIÓN CLAVE: Devolvemos el contexto limpio en string para que `procesar_y_enviar_mensaje` lo lea
                return resultado_vbt.get("contexto_agente", "Contexto VBT no disponible")

    except Exception as e:
        # ... resto de tus excepciones ...
        print("[CRASHEO PIPELINE]: Error inesperado en la integracion con Meta.")
        traceback.print_exc()
        return "[ERROR]: Ocurrio un error interno en el servidor al procesar el video."

    finally:
        # La limpieza ahora se hace en la carpeta data/
        if os.path.exists(ruta_video_original):
            os.remove(ruta_video_original)
            print(f"[LIMPIEZA]: Archivo original borrado de data/.")
        if os.path.exists(ruta_video_procesado):
            os.remove(ruta_video_procesado)
            print(f"[LIMPIEZA]: Archivo procesado borrado de data/.")