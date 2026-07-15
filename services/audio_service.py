import os
import httpx
from faster_whisper import WhisperModel
from core.config import settings

# 1. CARGAMOS EL MODELO EN MEMORIA (Fuera de las funciones para que cargue solo 1 vez)
# Usamos el modelo "small" (rápido y muy preciso en español) y forzamos CPU en int8 para estabilidad.
print("Cargando modelo Whisper para audios...")
modelo_whisper = WhisperModel(
    "small",
    device="cpu",
    compute_type="int8", # Excelente para CPU
    cpu_threads=8,
    num_workers=4
)
print("¡Oídos del Coach listos!")


async def procesar_audio_whatsapp(media_id: str) -> str:
    """
    Recibe el ID del audio de Meta, lo descarga, lo transcribe y borra el archivo.
    Aplica enrutamiento dinámico para trabajar de forma segura en la carpeta 'data/'.
    """

    # ==============================================================================
    # 1. ENRUTAMIENTO DINÁMICO
    # ==============================================================================
    ruta_script_actual = os.path.dirname(os.path.abspath(__file__))
    ruta_raiz_proyecto = os.path.dirname(ruta_script_actual)

    carpeta_data = os.path.join(ruta_raiz_proyecto, "data")
    os.makedirs(carpeta_data, exist_ok=True)

    # El archivo temporal ahora vive felizmente dentro de data/
    ruta_temporal = os.path.join(carpeta_data, f"temp_{media_id}.ogg")

    try:
        # FASE 1: Obtener la URL de descarga desde Meta
        url_info = f"https://graph.facebook.com/v18.0/{media_id}"
        headers = {"Authorization": f"Bearer {settings.ACCESS_TOKEN}"}

        # Agregamos follow_redirects=True para que persiga el archivo en los servidores de Meta
        async with httpx.AsyncClient(follow_redirects=True) as client:
            respuesta_info = await client.get(url_info, headers=headers)
            if respuesta_info.status_code != 200:
                print(
                    f"[ERROR META]: Error al obtener info del audio: {respuesta_info.text}"
                )
                return ""

            url_descarga = respuesta_info.json().get("url")

            # FASE 2: Descargar el archivo físico a la carpeta data/
            respuesta_audio = await client.get(url_descarga, headers=headers)

            with open(ruta_temporal, "wb") as f:
                f.write(respuesta_audio.content)

            # FASE 3: Transcribir el audio usando Whisper
            print(f"[SISTEMA]: Transcribiendo audio {media_id}...")

            # VALIDACIÓN: Ahora sí el archivo está guardado y cerrado
            peso_archivo = os.path.getsize(ruta_temporal)

            if peso_archivo < 1000:
                print(
                    "[ERROR META]: El archivo pesa muy poco. Probablemente Meta nos bloqueó la descarga."
                )
                return ""

            try:
                segmentos, info = modelo_whisper.transcribe(
                    ruta_temporal,
                    language="es",
                    beam_size=5,
                    vad_filter=True,  # Limpia silencios y ruidos de fondo antes de procesar
                    initial_prompt="VBT, RPE, RIR, AMRAP, 1RM, Powerlifting, Powerbuilding, sentadilla, banca, peso muerto, convencional, sumo, rdl, press militar, remo con barra, dominadas, fondos, desplantes, hipertrofia, potencia, fuerza máxima, serie efectiva, serie de aproximación, kilos, kg, libras, lbs, repeticiones, reps, m/s, metros por segundo, velocidad media, velocidad pico, pérdida de velocidad, fatiga acumulada, bloque, mesociclo, descarga, fallo técnico, unrack, setup, barra olímpica.",
                    # Diccionario de gimnasio
                    condition_on_previous_text=False  # Evita que se confunda si mandas varios audios seguidos
                )

                lista_segmentos = list(segmentos)
                texto_transcrito = "".join([s.text for s in lista_segmentos])

                print(f"[WHISPER]: Texto detectado: '{texto_transcrito.strip()}'")
                return texto_transcrito.strip()

            except Exception as error_whisper:
                print(
                    f"[CRASHEO WHISPER]: Error interno (¿Falta FFmpeg?): {error_whisper}"
                )
                return ""

    finally:
        # FASE 4: Limpieza. Borramos el archivo .ogg de la carpeta data/
        if os.path.exists(ruta_temporal):
            os.remove(ruta_temporal)
            print(f"[LIMPIEZA]: Audio temporal {media_id}.ogg borrado de data/.")