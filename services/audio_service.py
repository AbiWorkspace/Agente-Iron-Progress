import asyncio
import os

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

# Vocabulario de gimnasio: mejora muchísimo los números y los nombres de ejercicios.
PROMPT_INICIAL_WHISPER = (
    "VBT, RPE, RIR, AMRAP, 1RM, Powerlifting, Powerbuilding, sentadilla, banca, "
    "peso muerto, convencional, sumo, rdl, press militar, remo con barra, dominadas, "
    "fondos, desplantes, hipertrofia, potencia, fuerza máxima, serie efectiva, "
    "serie de aproximación, kilos, kg, libras, lbs, repeticiones, reps, m/s, "
    "metros por segundo, velocidad media, velocidad pico, pérdida de velocidad, "
    "fatiga acumulada, bloque, mesociclo, descarga, fallo técnico, unrack, setup, "
    "barra olímpica."
)

# Por debajo de esto, Meta nos devolvió un cuerpo vacío o un error disfrazado.
TAMANO_MINIMO_AUDIO = 1000


def _transcribir(ruta_audio: str) -> str:
    """Transcripción completa (bloqueante). Se ejecuta SIEMPRE dentro de un hilo:
    `transcribe` devuelve un generador perezoso, así que el cómputo real ocurre al
    consumirlo aquí dentro."""
    segmentos, _info = modelo_whisper.transcribe(
        ruta_audio,
        language="es",
        beam_size=5,
        vad_filter=True,   # Limpia silencios y ruidos de fondo antes de procesar
        initial_prompt=PROMPT_INICIAL_WHISPER,
        condition_on_previous_text=False,  # Evita confusión con varios audios seguidos
    )
    return "".join(s.text for s in segmentos)


async def procesar_audio_whatsapp(media_id: str) -> str:
    """
    Recibe el ID del audio de Meta, lo descarga, lo transcribe y borra el archivo.
    Devuelve "" ante cualquier fallo (el llamador avisa al atleta).
    """
    ruta_script_actual = os.path.dirname(os.path.abspath(__file__))
    ruta_raiz_proyecto = os.path.dirname(ruta_script_actual)

    carpeta_data = os.path.join(ruta_raiz_proyecto, "data")
    os.makedirs(carpeta_data, exist_ok=True)

    # El archivo temporal ahora vive felizmente dentro de data/
    ruta_temporal = os.path.join(carpeta_data, f"temp_{media_id}.ogg")

    try:
        # Cliente HTTP compartido: la descarga reutiliza la conexión ya abierta
        # contra graph.facebook.com en vez de rehacer el handshake TLS.
        from api.rutas_whatsapp import cliente_http
        cliente = await cliente_http()

        headers = {"Authorization": f"Bearer {settings.ACCESS_TOKEN}"}

        # FASE 1: Obtener la URL de descarga desde Meta
        respuesta_info = await cliente.get(f"{settings.GRAPH_URL}/{media_id}", headers=headers)
        if respuesta_info.status_code != 200:
            print(f"[ERROR META]: Error al obtener info del audio: {respuesta_info.text[:300]}")
            return ""

        url_descarga = respuesta_info.json().get("url")
        if not url_descarga:
            print("[ERROR META]: Meta no devolvió URL de descarga para el audio.")
            return ""

        # FASE 2: Descargar el archivo físico a la carpeta data/
        respuesta_audio = await cliente.get(url_descarga, headers=headers, timeout=60.0)
        if respuesta_audio.status_code != 200:
            print(f"[ERROR META]: descarga del audio falló ({respuesta_audio.status_code}).")
            return ""

        with open(ruta_temporal, "wb") as f:
            f.write(respuesta_audio.content)

        # VALIDACIÓN: Ahora sí el archivo está guardado y cerrado
        if os.path.getsize(ruta_temporal) < TAMANO_MINIMO_AUDIO:
            print("[ERROR META]: El archivo pesa muy poco. Probablemente Meta nos "
                  "bloqueó la descarga.")
            return ""

        # FASE 3: Transcribir el audio usando Whisper
        print(f"[SISTEMA]: Transcribiendo audio {media_id}...")
        try:
            # Whisper es CPU-bound y tarda entre 5 y 30 s. Corriéndolo aquí mismo
            # bloqueaba el event loop entero: mientras un atleta mandaba un audio,
            # el servidor no atendía NI un webhook NI el health check. Va a un hilo.
            # OJO: transcribe() devuelve un generador perezoso, así que el consumo
            # de los segmentos tiene que ocurrir DENTRO del hilo (ver _transcribir).
            texto_transcrito = await asyncio.to_thread(_transcribir, ruta_temporal)
        except Exception as error_whisper:
            print(f"[CRASHEO WHISPER]: Error interno (¿Falta FFmpeg?): {error_whisper}")
            return ""

        print(f"[WHISPER]: Texto detectado: '{texto_transcrito.strip()}'")
        return texto_transcrito.strip()

    except Exception as e:
        print(f"[ERROR AUDIO]: {type(e).__name__}: {e}")
        return ""

    finally:
        # FASE 4: Limpieza. Borramos el archivo .ogg de la carpeta data/
        if os.path.exists(ruta_temporal):
            try:
                os.remove(ruta_temporal)
                print(f"[LIMPIEZA]: Audio temporal {media_id}.ogg borrado de data/.")
            except OSError as e:
                print(f"[LIMPIEZA]: no se pudo borrar el audio temporal: {e}")
