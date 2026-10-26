# -*- coding: utf-8 -*-
"""
Motor VBT (Velocity Based Training) — Iron Progress · v3
=========================================================
Refactor enfocado en precisión, robustez y ANÁLISIS ACCIONABLE. Mantiene la
misma API pública (`procesar_levantamiento_vbt(...)` es drop-in compatible:
mismos parámetros posicionales, mismas claves en el dict de retorno) y añade:

NUEVO EN v3 — Métricas cinemáticas directas:
  1. BAR PATH: desviación horizontal máxima (cm) y eficiencia de trayectoria
     (recorrido vertical / longitud real del camino) por repetición.
  2. ANÁLISIS TEMPORAL DE FASES: duración de la excéntrica, tiempo de
     amortiguación (transición isométrica en el fondo, en ms) y concéntrica.
  3. CINEMÁTICA ARTICULAR (opcional, requiere un modelo YOLO-pose): ángulo
     mínimo de rodilla/cadera y velocidad angular pico de extensión de rodilla.
     Se activa pasando `ruta_modelo_pose` (p. ej. "yolo11n-pose.pt"). Si no se
     pasa, el motor funciona exactamente igual que antes (cero costo extra).

NUEVO EN v3 — Métricas derivadas y autorregulación:
  4. VELOCITY LOSS CORREGIDO: ahora se mide contra la repetición MÁS RÁPIDA de
     la serie (estándar VBT), no contra la primera. La rep más rápida no
     siempre es la 1ª (activación fría, ajuste de posición).
  5. ESTIMACIÓN DEL 1RM DIARIO: si el servicio pasa `carga_kg`, se estima el
     %1RM de la 1ª rep vía perfil carga-velocidad y de ahí el e1RM del día.
  6. REPS EN RESERVA: doble estimador (velocidad terminal vs MVT + VL%) que se
     reconcilia en un rango entero: "te quedaban ~2-3 reps".
  7. MOTOR DE ANÁLISIS DETERMINISTA (`analizar_serie`): insights concretos
     calculados con los datos (rep del mayor bajón, rep de corte para VL 10/20/
     30%, consistencia de ROM, crecimiento de la pausa en el fondo, bar path,
     sugerencia de ajuste de carga en % y kg). El reporte de WhatsApp y el
     prompt del LLM se construyen sobre estos hallazgos: nada de texto genérico.

FIXES v3.3 — PÉRDIDA DEL DISCO Y VELOCIDADES ERRÓNEAS EN VIDEO REAL
  (medido sobre el dataset de grabaciones propias, no supuesto):
  * LÍNEA DE SUELO: `frac_linea_suelo` EXCLUÍA toda caja por debajo del 75% de
    la altura de imagen para ignorar discos apilados. En peso muerto el disco
    de trabajo arranca en el piso, así que el filtro tiraba el setup y el primer
    tirón: en un video, YOLO veía el disco en el 99.6% de los frames y el motor
    solo usaba el 55.7% (211 frames descartados; enganche recién en el frame
    212). Ahora estar abajo PENALIZA la puntuación en vez de excluir.
  * TRACK IRRECUPERABLE: una vez fijado `id_fijado`, la re-fijación desde cero
    era código inalcanzable. Si el disco se perdía más allá del radio, no se
    recuperaba en todo el video. Ahora el radio de búsqueda se abre con los
    frames perdidos y, pasado un umbral, se libera el ID para re-fijar.
  * VELOCIDAD SOBRE HUECOS: Savitzky-Golay asume muestreo uniforme, pero la
    ventana solo recibe frames CON detección. Ajustaba a través de huecos de
    hasta 6 s como si fueran consecutivos -> picos imposibles. Sustituido por
    ajuste polinómico local sobre los TIEMPOS REALES, con descarte de ventana
    cuando el hueco supera `gap_reinicio_s`.
  * POSICIÓN Y VELOCIDAD DESFASADAS: se devolvía la posición del último punto
    y la velocidad del penúltimo — instantes distintos, y la máquina de estados
    cruza ambas para decidir las fases. Ahora se evalúan en el mismo instante.
  * ESCALA px->m: se sembraba con el PRIMER disco visto; un fotograma con la
    caja recortada desviaba TODA la serie (la velocidad es proporcional a ella).
    Ahora se siembra con la mediana de los primeros N y se rechazan los saltos
    de tamaño físicamente imposibles.
  * DIAGNÓSTICO HONESTO: con cobertura baja el motor culpaba al atleta
    ("movimiento insuficiente"); ahora reporta la falta de visibilidad del
    disco y explica cómo reencuadrar.

FIXES respecto a v2:
  * El cierre por inactividad NUNCA se disparaba: `frames_estables` solo se
    actualizaba en estado "calibrando". Ahora hay un contador de quietud global
    (`frames_quietos`) y además se intenta cerrar la última rep al terminar el
    video (caso típico: se corta la grabación con la barra bloqueada arriba).
  * El flag de inferencia FP16 (`half` / `quantize`) se detecta una sola vez
    según la versión de Ultralytics instalada, y si no se reconoce se omite.
    Evita tanto la caída silenciosa de `track()` como los miles de WARNING de
    deprecación que se emitían en cada frame.
  * La fase concéntrica ahora arranca en el primer frame real de subida (buffer
    durante la excéntrica), no cuando se supera el umbral de reversa: tiempos
    concéntricos y velocidades medias más fieles.

Mejoras heredadas de v2 (se conservan):
  - Velocidad con signo (+ sube / − baja), Savitzky-Golay con deriv=1,
    MPV (corte propulsivo cuando a < −g), RIR/RPE en el motor, máquina de
    estados encapsulada, re-enganche de tracking por cercanía, escala con EMA.
"""
from __future__ import annotations

import inspect
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import traceback
from collections import deque
from dataclasses import dataclass, asdict
from typing import Optional, Union, Callable

import cv2
import numpy as np
from ultralytics import YOLO
import imageio_ffmpeg

try:
    from data.meta_service import enviar_mensaje_whatsapp
except Exception:
    try:
        from services.data.meta_service import enviar_mensaje_whatsapp
    except Exception:
        def enviar_mensaje_whatsapp(texto: str, telefono_destino: str):
            pass

logger = logging.getLogger("motor_vbt")


# ==============================================================================
# CACHÉ GLOBAL DE MODELOS YOLO (Evita recargar pesos en VRAM en cada video)
# ==============================================================================
_CACHE_MODELOS_YOLO: dict[str, YOLO] = {}


def _obtener_modelo_yolo(ruta_modelo: str) -> YOLO:
    """Devuelve una instancia cacheada del modelo YOLO indexada por ruta para evitar
    recargas repetitivas desde disco y fragmentación de memoria GPU/VRAM."""
    if ruta_modelo not in _CACHE_MODELOS_YOLO:
        logger.info(f"Cargando modelo YOLO en caché global: {ruta_modelo}")
        _CACHE_MODELOS_YOLO[ruta_modelo] = YOLO(ruta_modelo)
    return _CACHE_MODELOS_YOLO[ruta_modelo]


def _notificar_whatsapp_seguro(texto: str, telefono_destino: str, callback: Optional[Callable] = None) -> None:
    """Desacopla las llamadas a la API de WhatsApp en un hilo en segundo plano para
    evitar que la latencia de red de Meta bloquee el motor de visión artificial."""
    def _tarea():
        try:
            if callback:
                callback(texto=texto, telefono_destino=telefono_destino)
            else:
                enviar_mensaje_whatsapp(texto=texto, telefono_destino=telefono_destino)
        except Exception as e:
            logger.warning(f"Error en notificación WhatsApp en segundo plano: {e}")

    threading.Thread(target=_tarea, daemon=True).start()


def _analizar_propiedades_video(ruta_video: str) -> tuple[bool, bool]:
    """Detecta si el video de entrada es HDR y/o entrelazado (interlaced) sin falsos positivos."""
    es_hdr = False
    es_entrelazado = False

    # 1. Intentar con ffprobe consultando los campos exactos del stream de video
    ffprobe_exe = shutil.which("ffprobe")
    if not ffprobe_exe:
        try:
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            posible_ffprobe = os.path.join(os.path.dirname(ffmpeg_exe), "ffprobe")
            if os.path.exists(posible_ffprobe):
                ffprobe_exe = posible_ffprobe
        except Exception:
            pass

    if ffprobe_exe:
        try:
            res = subprocess.run(
                [
                    ffprobe_exe, "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=field_order,color_transfer,pix_fmt",
                    "-of", "default=noprint_wrappers=1:nokey=0", ruta_video
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8
            )
            props = dict(line.strip().split("=", 1) for line in res.stdout.splitlines() if "=" in line)
            fo = props.get("field_order", "").lower().strip()
            ct = props.get("color_transfer", "").lower().strip()
            pf = props.get("pix_fmt", "").lower().strip()

            if fo in ("tt", "bb", "tb", "bt") or "interlaced" in fo:
                es_entrelazado = True

            hdr_transfers = ("smpte2084", "arib-std-b67", "linear", "bt2020-10", "bt2020-12")
            if ct in hdr_transfers or "10le" in pf or "p010" in pf:
                es_hdr = True

            return es_hdr, es_entrelazado
        except Exception as e:
            logger.debug(f"ffprobe falló al analizar video ({e}); usando fallback ffmpeg.")

    # 2. Fallback a ffmpeg -i inspeccionando exclusivamente la línea del Stream de video
    try:
        try:
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg_exe = "ffmpeg"

        res = subprocess.run([ffmpeg_exe, "-i", ruta_video], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8)
        err = res.stderr

        match_stream = re.search(r"Stream #\d+:\d+.*?: Video:.*", err)
        if match_stream:
            linea_video = match_stream.group(0).lower()
            if re.search(r"\b(interlaced|tff|bff)\b", linea_video):
                es_entrelazado = True
            if re.search(r"\b(bt2020|arib-std-b67|smpte2084|hlg|hdr10|yuv420p10le|yuv422p10le|p010le)\b", linea_video):
                es_hdr = True
    except Exception as e:
        logger.warning(f"No se pudieron verificar las propiedades del video: {e}")

    return es_hdr, es_entrelazado


def _preprocesar_video_hdr_si_es_necesario(ruta_video: str) -> tuple[str, bool]:
    """Si el video es HDR o entrelazado, lo preprocesa con FFmpeg (bwdif / zscale) a Rec. 709 SDR progresivo sin dientes de sierra."""
    es_hdr, es_entrelazado = _analizar_propiedades_video(ruta_video)
    if not es_hdr and not es_entrelazado:
        return ruta_video, False

    logger.info(f"Preprocesando video (HDR={es_hdr}, Entrelazado={es_entrelazado}) para eliminar dientes de sierra...")
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg_exe = "ffmpeg"

    dir_name, base_name = os.path.split(ruta_video)
    ruta_proc = os.path.join(dir_name, "proc_" + base_name)

    filtros = []
    if es_entrelazado:
        filtros.append("bwdif=mode=0:parity=-1:deint=1")
    if es_hdr:
        filtros.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    else:
        filtros.append("format=yuv420p")

    vf_filter = ",".join(filtros)

    cmd = [
        ffmpeg_exe, "-y",
        "-i", ruta_video,
        "-vf", vf_filter,
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
        ruta_proc
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        logger.info("Preprocesamiento de video completado.")
        return ruta_proc, True
    except Exception as e:
        logger.error(f"Fallo en preprocesamiento de video via FFmpeg: {e}. Se utilizará el video original.")
        return ruta_video, False


# ==============================================================================
# COMPATIBILIDAD ULTRALYTICS: flag de inferencia FP16 según versión instalada
# ==============================================================================
# El nombre del argumento cambió entre versiones: las antiguas solo aceptan
# `half=True`; las nuevas lo marcan como deprecado y emiten un WARNING **en cada
# llamada** (con inferencia por frame eso son miles de líneas en consola) pidiendo
# `quantize="fp16"`. Se resuelve UNA sola vez por proceso inspeccionando la firma
# real del método, y si no se puede inspeccionar se omite el flag: FP16 es una
# optimización, nunca vale la pena romper (o ensuciar) la ejecución por ella.
_KWARGS_FP16: Optional[dict] = None


def _kwargs_fp16(metodo) -> dict:
    """Devuelve {} , {'quantize': 'fp16'} o {'half': True} según la versión."""
    global _KWARGS_FP16
    if _KWARGS_FP16 is not None:
        return _KWARGS_FP16

    kwargs: dict = {}
    try:
        params = inspect.signature(metodo).parameters
        # Ultralytics acepta **kwargs, así que la firma casi siempre es genérica;
        # se comprueba también el validador de configuración cuando existe.
        nombres = set(params)
        try:
            from ultralytics.cfg import CFG_FRACTION_KEYS, CFG_BOOL_KEYS  # type: ignore
            nombres |= set(CFG_FRACTION_KEYS) | set(CFG_BOOL_KEYS)
        except Exception:
            try:
                from ultralytics.cfg import DEFAULT_CFG_DICT  # type: ignore
                nombres |= set(DEFAULT_CFG_DICT)
            except Exception:
                pass
        if "quantize" in nombres:
            kwargs = {"quantize": "fp16"}
        elif "half" in nombres:
            kwargs = {"half": True}
    except Exception as e:
        logger_tmp = logging.getLogger("MOTOR_VBT")
        logger_tmp.debug(f"No se pudo detectar el flag FP16 de Ultralytics: {e}")

    _KWARGS_FP16 = kwargs
    return kwargs


def _silenciar_avisos_ultralytics() -> None:
    """Ultralytics repite sus WARNING por frame. Se elevan a ERROR una sola vez
    para que la consola quede legible sin perder los fallos de verdad."""
    for nombre in ("ultralytics", "yolo"):
        try:
            logging.getLogger(nombre).setLevel(logging.ERROR)
        except Exception:
            pass
    try:
        from ultralytics.utils import LOGGER as UL_LOGGER  # type: ignore
        UL_LOGGER.setLevel(logging.ERROR)
    except Exception:
        pass

# ==============================================================================
# LOGGING
# ==============================================================================
logger = logging.getLogger("motor_vbt")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(levelname)s][VBT] %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)

# ==============================================================================
# CONFIGURACIÓN CENTRAL (un solo lugar para calibrar el motor)
# ==============================================================================
GRAVEDAD = 9.81


@dataclass(frozen=True)
class ConfigVBT:
    # Escala física
    diametro_disco_m: float = 0.45          # Disco olímpico estándar

    # Detección YOLO — Calibrado analíticamente según la banda operativa de tau* (0.1802)
    conf_deteccion: float = 0.18            # Punto de equilibrio óptimo F1 (0.9168)
    iou_nms: float = 0.45
    imgsz: int = 1024
    max_detecciones: int = 3
    factor_reenganche: float = 1.5          # Tolerancia de re-enganche (x diámetro del disco)
    ema_alto_caja: float = 0.15             # Suavizado exponencial de la escala

    # Recuperación del tracking y protección contra saltos (Anti-Switch)
    frames_para_soltar_id: int = 60         # Solo aplica ANTES de confirmar el ancla de la serie (1s a 60fps)
    crecimiento_radio_por_frame: float = 0.08  # Crecimiento suave (antes 0.35 abría a toda la imagen)
    factor_radio_max_tope: float = 1.60     # Tope duro del radio de re-enganche (x alto_ema)
    max_desvio_x_frac: float = 0.55         # Pasillo horizontal máx permitido (x alto_ema)
    max_salto_frame_frac: float = 0.65      # Desplazamiento máx posible en 1 frame (x alto_ema)
    margen_borde_px: int = 18               # Píxeles para detectar que el disco sale de cámara
    frac_min_visible_borde: float = 0.38    # Si queda menos del 38% del disco dentro, se ignora el frame
    penalizacion_suelo: float = 0.5         # Factor de puntuación en el piso

    # Estabilidad de la escala px->m
    frames_semilla_escala: int = 10         # Mediana de los primeros N discos
    salto_max_escala: float = 0.28          # Rechaza discos de distinto diámetro/profundidad

    # Cinemática
    ventana_s: float = 0.233                # Ancho temporal del ajuste local
    ventana_savgol: int = 7                 # Fallback si no se conocen los fps
    poly_savgol: int = 2                    # Grado del ajuste local
    gap_reinicio_s: float = 0.35            # Hueco de detección que reinicia la ventana
    vel_max_valida: float = 3.5             # m/s — techo físico en barra
    umbral_ruido_prop: float = 0.08         # m/s — micro-pausas / ruido
    vel_sticking: float = 0.05              # m/s — barra clavada (sticking point)

    # Máquina de estados
    vel_min_registro: float = 0.05
    vel_min_setup: float = 0.15
    seg_estabilidad_setup: float = 0.15
    seg_inactividad_fin: float = 8.0
    frac_rom_minimo: float = 0.25           # x alto de disco
    frac_reversa: float = 0.10
    frac_compromiso: float = 0.15
    rom_minimo_px_piso: float = 30.0
    reversa_px_piso: float = 8.0
    compromiso_px_piso: float = 15.0
    frac_linea_suelo: float = 0.75

    # Fin del empuje concéntrico
    t_fin_empuje_s: float = 1.20
    frac_ganancia_util: float = 0.035

    # Pose / cinemática articular (opcional)
    pose_cada_n_frames: int = 2
    conf_keypoint: float = 0.30

    # Umbrales del análisis técnico
    umbral_desv_horizontal_cm: float = 5.0
    rom_cv_alerta_pct: float = 7.0
    amort_alerta_crec_pct: float = 50.0


CONFIG = ConfigVBT()

# ==============================================================================
# PERFILES DE EJERCICIO — MVT, pendiente carga-velocidad y zonas de intensidad
# (valores alineados con la literatura VBT y con tu base de conocimiento previa)
# ==============================================================================
@dataclass(frozen=True)
class PerfilEjercicio:
    clave: str
    nombre: str
    mvt: dict                     # nivel -> velocidad mínima (RIR 0), m/s
    pendiente_rir: float          # m/s ganados por cada repetición en reserva
    zona_fuerza_vel: tuple        # (v_sup, v_inf) para 80–89% 1RM
    v_sup_max_fuerza: float       # límite superior de la zona 90–100% 1RM


PERFILES_EJERCICIO = {
    "sentadilla": PerfilEjercicio(
        clave="sentadilla", nombre="Sentadilla",
        mvt={"novato": 0.40, "avanzado": 0.25, "elite": 0.21},
        pendiente_rir=0.055,
        zona_fuerza_vel=(0.60, 0.40), v_sup_max_fuerza=0.39,
    ),
    "peso_muerto": PerfilEjercicio(
        clave="peso_muerto", nombre="Peso Muerto",
        mvt={"novato": 0.35, "avanzado": 0.15, "elite": 0.13},
        pendiente_rir=0.045,
        zona_fuerza_vel=(0.58, 0.46), v_sup_max_fuerza=0.45,
    ),
    "press_banca": PerfilEjercicio(
        clave="press_banca", nombre="Press de Banca",
        mvt={"novato": 0.30, "avanzado": 0.135, "elite": 0.11},
        pendiente_rir=0.040,
        zona_fuerza_vel=(0.42, 0.26), v_sup_max_fuerza=0.25,
    ),
    "dominadas": PerfilEjercicio(
        clave="dominadas", nombre="Dominadas",
        mvt={"novato": 0.40, "avanzado": 0.23, "elite": 0.22},
        pendiente_rir=0.050,
        zona_fuerza_vel=(0.59, 0.44), v_sup_max_fuerza=0.43,
    ),
}

_ALIASES_EJERCICIO = {
    "sentadilla": ("sentadilla", "squat", "hack"),
    "peso_muerto": ("peso muerto", "deadlift", "despegue", "rumano", "rdl", "sumo"),
    "press_banca": ("press", "banca", "bench", "militar", "hombro"),
    "dominadas": ("dominada", "pull up", "pull-up", "pullup", "jalon", "jalón"),
}

_PERFIL_GENERICO = PerfilEjercicio(
    clave="generico", nombre="Genérico",
    mvt={"novato": 0.38, "avanzado": 0.22, "elite": 0.18},
    pendiente_rir=0.048,
    zona_fuerza_vel=(0.55, 0.38), v_sup_max_fuerza=0.37,
)


def obtener_perfil_ejercicio(nombre_ejercicio: str) -> PerfilEjercicio:
    """Mapea el nombre libre del ejercicio a un perfil VBT conocido."""
    texto = (nombre_ejercicio or "").strip().lower()
    for clave, alias in _ALIASES_EJERCICIO.items():
        if any(a in texto for a in alias):
            return PERFILES_EJERCICIO[clave]
    return _PERFIL_GENERICO


def _normalizar_nivel(nivel: str) -> str:
    n = (nivel or "").strip().lower()
    if "nov" in n or "princ" in n:
        return "novato"
    if "eli" in n or "élite" in n:
        return "elite"
    return "avanzado"


# ==============================================================================
# ESTIMADORES DERIVADOS (RIR / RPE / %1RM / e1RM / Velocity Loss / reps rest.)
# ==============================================================================
def estimar_rir_por_velocidad(v_ultima_rep: float, perfil: PerfilEjercicio,
                              nivel: str,
                              mvt_override: Optional[float] = None
                              ) -> Optional[float]:
    """RIR ≈ (v_última − MVT) / pendiente carga-velocidad del ejercicio.

    `mvt_override` permite usar el MVT EFECTIVO del atleta (evidencia de la
    propia serie) en lugar del de tabla: un atleta que completa reps por
    debajo del MVT tabulado demuestra que su velocidad de fallo real es más
    baja, y con la tabla el RIR saldría 0 (o negativo) estando falseado."""
    if v_ultima_rep is None or v_ultima_rep <= 0:
        return None
    mvt = mvt_override if mvt_override else perfil.mvt[_normalizar_nivel(nivel)]
    if v_ultima_rep <= mvt:
        return 0.0
    rir = (v_ultima_rep - mvt) / perfil.pendiente_rir
    return float(round(min(rir, 8.0), 1))




def estimar_rir_por_vl(vl_pct: Optional[float],
                       n_reps: int = 0) -> Optional[str]:
    """Rango de RIR según la pérdida de velocidad intra-serie.

    FIX v3.4: el VL solo informa de fatiga si hubo serie suficiente para
    acumularla. Con 2-3 reps un VL bajo no prueba frescura — puede que la serie
    simplemente fuera corta — y la tabla devolvía alegremente "4-6 reps en
    reserva" tras un triple pesado. Por debajo de 4 reps el estimador se
    abstiene y el RIR queda en manos de la velocidad terminal.
    """
    if vl_pct is None or n_reps < 4:
        return None
    if vl_pct < 10:
        return "4-6"
    if vl_pct < 20:
        return "2-3"
    if vl_pct < 30:
        return "1-2"
    return "0-1"


def estimar_pct_1rm(v_primera_rep: Optional[float], perfil: PerfilEjercicio,
                    nivel: str,
                    mvt_override: Optional[float] = None) -> Optional[float]:
    """
    %1RM aproximado desde la velocidad de la 1ª rep con un modelo lineal de dos
    puntos del perfil del ejercicio: (MVT → 100% 1RM) y (v_sup de la zona
    fuerza-velocidad → 80% 1RM). Es un perfil poblacional: sirve como ancla
    diaria; un perfil carga-velocidad individual (2+ cargas) lo refinaría.
    """
    if not v_primera_rep or v_primera_rep <= 0:
        return None
    mvt = mvt_override if mvt_override else perfil.mvt[_normalizar_nivel(nivel)]
    v80 = perfil.zona_fuerza_vel[0]
    pendiente = (v80 - mvt) / 20.0        # m/s por cada 1% de 1RM
    if pendiente <= 0:
        return None
    pct = 100.0 - (v_primera_rep - mvt) / pendiente
    # Techo duro en 100: una rep COMPLETADA nunca es >100% del 1RM. El techo
    # anterior de 105 producía absurdos tipo "los 200 kg fueron el 102%".
    return float(round(min(max(pct, 30.0), 100.0), 1))


def estimar_1rm_diario(carga_kg: Optional[float], v_primera_rep: Optional[float],
                       perfil: PerfilEjercicio, nivel: str) -> Optional[float]:
    """e1RM del día = carga / (%1RM estimado). Requiere conocer la carga en kg.
    Guarda dura: nunca por debajo de la carga levantada (una rep completada
    prueba que e1RM ≥ carga). Para series, usar `estimar_e1rm_serie`."""
    if not carga_kg or carga_kg <= 0:
        return None
    pct = estimar_pct_1rm(v_primera_rep, perfil, nivel)
    if not pct:
        return None
    return float(round(max(carga_kg / (pct / 100.0), carga_kg), 1))


def estimar_e1rm_serie(carga_kg: Optional[float], v_primera: Optional[float],
                       v_min_serie: Optional[float], n_reps: int,
                       perfil: PerfilEjercicio, nivel: str) -> dict:
    """
    e1RM del día combinando DOS evidencias independientes, con sanity checks:

    1. Perfil de velocidad (modelo lineal MVT↔v80), pero con **MVT efectivo**:
       si la rep más lenta COMPLETADA fue más lenta que el MVT de tabla, la
       tabla no describe a este atleta hoy (muele por debajo del estándar) y
       se usa esa velocidad como MVT — es una cota superior demostrada de su
       velocidad de fallo real.
    2. **Piso por volumen (Epley)**: completar n reps con C kg garantiza
       e1RM ≥ C·(1 + n/30). Con 3×200 el piso es 220 kg: es matemáticamente
       imposible un 1RM de 196.

    e1RM final = max(estimación por velocidad, piso por reps). Si gana el
    piso, se reporta el origen para que los mensajes digan "mínimo
    garantizado" en lugar de fingir precisión del perfil de velocidad.
    """
    mvt_tabla = perfil.mvt[_normalizar_nivel(nivel)]
    mvt_ef = mvt_tabla
    if v_min_serie and 0 < v_min_serie < mvt_tabla:
        mvt_ef = float(v_min_serie)

    pct_vel = estimar_pct_1rm(v_primera, perfil, nivel, mvt_override=mvt_ef)

    e1rm_vel = None
    if carga_kg and pct_vel:
        e1rm_vel = carga_kg / (pct_vel / 100.0)

    piso = None
    if carga_kg and n_reps >= 1:
        piso = carga_kg * (1.0 + n_reps / 30.0)

    e1rm, origen = None, None
    if e1rm_vel is not None or piso is not None:
        if (piso or 0.0) > (e1rm_vel or 0.0):
            e1rm, origen = piso, "piso_reps"
        else:
            e1rm, origen = e1rm_vel, "velocidad"
        e1rm = max(e1rm, carga_kg or 0.0)          # jamás < carga levantada
        e1rm = float(round(e1rm, 1))

    pct_final = pct_vel
    if carga_kg and e1rm:
        pct_final = float(round(min(100.0 * carga_kg / e1rm, 100.0), 1))

    return {"e1rm_kg": e1rm, "pct_1rm": pct_final, "origen": origen,
            "mvt_efectivo": float(round(mvt_ef, 3)),
            "mvt_tabla": float(mvt_tabla)}


def estimar_reps_restantes(rir_velocidad: Optional[float],
                           rir_vl_rango: Optional[str]) -> Optional[dict]:
    """
    Reconcilia los dos estimadores de RIR en un rango entero de repeticiones
    que probablemente quedaban en reserva. Si ambos existen se toma su
    intersección (o el promedio de extremos si no se solapan).
    """
    rangos = []
    if rir_velocidad is not None:
        lo = int(math.floor(rir_velocidad))
        hi = max(int(math.ceil(rir_velocidad)), lo)
        rangos.append((lo, hi))
    if rir_vl_rango:
        try:
            a, b = rir_vl_rango.split("-")
            rangos.append((int(a), int(b)))
        except (ValueError, AttributeError):
            pass
    if not rangos:
        return None

    if len(rangos) == 2:
        lo = max(rangos[0][0], rangos[1][0])
        hi = min(rangos[0][1], rangos[1][1])
        if lo > hi:
            # FIX v3.4 — SIN SOLAPE = DESACUERDO, no dato.
            # Antes se promediaban los extremos: con velocidad diciendo (0,0) y
            # VL diciendo (2,3) salía un "~1-2" que ningún estimador sostenía,
            # y se reportaba con la misma confianza que un consenso real. Ahora
            # se devuelve la ENVOLVENTE de ambos y se marca como incierto, que
            # es lo que honestamente sabemos.
            lo = min(rangos[0][0], rangos[1][0])
            hi = max(rangos[0][1], rangos[1][1])
            texto = f"~{lo}-{hi} (estimadores en desacuerdo)"
            return {"min": lo, "max": hi, "texto": texto, "incierto": True}
    else:
        lo, hi = rangos[0]

    lo = max(lo, 0)
    hi = max(hi, lo)
    if hi == 0:
        texto = "0 (al fallo o al borde)"
    elif lo == hi:
        texto = f"~{lo}"
    else:
        texto = f"~{lo}-{hi}"
    return {"min": lo, "max": hi, "texto": texto, "incierto": False}


def estimar_zona_intensidad(pct_1rm: Optional[float]) -> str:
    """Zona de carga a partir del %1RM final (ya reconciliado con el piso por
    reps y el MVT efectivo). Antes se clasificaba por velocidad contra la
    tabla, lo que etiquetaba de "intento máximo" series enteras de atletas
    que muelen por debajo del MVT estándar."""
    if pct_1rm is None:
        return "Indeterminada"
    if pct_1rm >= 90:
        return "90-100% 1RM (fuerza máxima)"
    if pct_1rm >= 80:
        return "80-90% 1RM (fuerza)"
    if pct_1rm >= 60:
        return "60-80% 1RM (fuerza-velocidad)"
    return "<60% 1RM (velocidad / potencia)"


def calcular_velocity_loss(registro: list) -> Optional[float]:
    """
    Pérdida de velocidad %: repetición MÁS RÁPIDA de la serie vs la última.
    (Estándar VBT: la rep más rápida no siempre es la 1ª.)
    """
    if len(registro) < 2:
        return None
    v_best = max(r.v_avg for r in registro)
    v_last = registro[-1].v_avg
    if v_best <= 0:
        return None
    return float(round(((v_best - v_last) / v_best) * 100.0, 1))


def _recalcular_vl_por_rep(registro: list) -> None:
    """Rellena rep.vl_pct de TODAS las reps contra la más rápida de la serie."""
    if not registro:
        return
    v_best = max(r.v_avg for r in registro)
    if v_best <= 0:
        return
    for r in registro:
        r.vl_pct = float(round(((v_best - r.v_avg) / v_best) * 100.0, 1))


def _clasificar_estimulo(vl_pct: Optional[float]) -> str:
    """Traduce el VL% de la serie al tipo de adaptación predominante."""
    if vl_pct is None:
        return "single (sin fatiga intra-serie medible)"
    if vl_pct < 10:
        return "estímulo de potencia/velocidad: fatiga mínima, fibras rápidas intactas"
    if vl_pct < 20:
        return "estímulo de fuerza: fatiga controlada, zona óptima para progresar carga"
    if vl_pct < 30:
        return "estímulo de hipertrofia: alto estrés mecánico y metabólico"
    return "al borde del fallo: costo de recuperación alto (48-72 h)"


def _json_safe(obj):
    """Convierte tipos numpy a tipos nativos para que el dict sea serializable."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


# ==============================================================================
# ESTRUCTURAS DE DATOS
# ==============================================================================
@dataclass
class Deteccion:
    cx: int
    cy: int
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    alto_caja: float          # ya suavizado con EMA
    m_por_px: float


@dataclass
class Repeticion:
    rep: int
    v_avg: float                    # VCM: media de la fase concéntrica real
    v_peak: float                   # pico (media móvil de 3 frames, señal real)
    mpv: float                      # Mean Propulsive Velocity (corte a < -g)
    rom_cm: float                   # desplazamiento vertical de la concéntrica
    tiempo_s: float                 # duración de la fase concéntrica
    # --- v3: análisis temporal de fases ---
    t_excentrica_s: float = 0.0     # duración de la bajada
    t_amortiguacion_s: Optional[float] = None  # transición isométrica (None si
                                    # la rep arrancó desde abajo, sin fondo)
    # --- v3: bar path ---
    desv_horizontal_cm: float = 0.0     # deriva lateral máx. en la concéntrica
    eficiencia_barra_pct: float = 100.0  # recorrido vertical / camino real
    # --- v3: ventana temporal de la rep (para cortar clips / cruzar con pose)
    t_inicio_s: float = 0.0
    t_fin_s: float = 0.0
    vl_pct: float = 0.0             # pérdida vs la rep más rápida de la serie
    sticking_s: float = 0.0         # tiempo total con la barra casi parada
                                    # a media subida (grind real, no ruido)
    # --- v3: cinemática articular (solo si hay modelo de pose) ---
    angulo_rodilla_min: Optional[float] = None      # grados (profundidad)
    angulo_cadera_min: Optional[float] = None       # grados
    vel_angular_rodilla_max: Optional[float] = None  # grados/s en la subida

    def a_dict(self) -> dict:
        return asdict(self)

# ==============================================================================
# RASTREADOR DE DISCO (YOLO + ByteTrack + Sticky Anchor + Guarda de Borde FOV)
# ==============================================================================
class RastreadorDisco:
    """Encapsula la detección/tracking del disco y la calibración px->metros."""

    def __init__(self, ruta_o_modelo: Union[str, YOLO], cfg: ConfigVBT = CONFIG):
        self.cfg = cfg
        if isinstance(ruta_o_modelo, str):
            self.modelo = _obtener_modelo_yolo(ruta_o_modelo)
        else:
            self.modelo = ruta_o_modelo
        self.reset()

    def _resetear_tracker_ultralytics(self) -> None:
        """Limpia los filtros de Kalman e IDs de ByteTrack acumulados de videos previos
        cuando el modelo YOLO se obtiene desde la caché global en VRAM."""
        try:
            predictor = getattr(self.modelo, "predictor", None)
            if predictor is not None and getattr(predictor, "trackers", None):
                for tracker in predictor.trackers:
                    tracker.reset()
        except Exception as e:
            logger.debug(f"No se pudo resetear el tracker interno de Ultralytics: {e}")

    def reset(self) -> None:
        """Reinicia el estado interno del rastreador y limpia ByteTrack sin recargar en GPU."""
        self._resetear_tracker_ultralytics()
        self.id_fijado: Optional[int] = None
        self.alto_ema: Optional[float] = None
        self.ultima_pos: Optional[tuple[int, int]] = None
        self.vel_px: tuple[float, float] = (0.0, 0.0)
        self.frames_sin_deteccion: int = 0
        self._altos_semilla: deque = deque(maxlen=self.cfg.frames_semilla_escala)
        self._escala_sembrada: bool = False
        self.ancla_confirmada: bool = False
        self.x_ancla: Optional[float] = None
        self.salio_por_borde: bool = False
        self._fallos_yolo_seguidos: int = 0
        self._aviso_yolo_emitido: bool = False
        # Control anti-switch y consistencia multi-disco
        self.frames_seguidos_id: int = 0
        self.total_frames_trackeados: int = 0
        self.coexisting_ids: dict[int, int] = {}
        self.borde_salida: Optional[str] = None
        self.pos_salida: Optional[tuple[int, int]] = None
        self._frame_count: int = 0

    # --------------------------------------------------------------
    def _perder(self) -> None:
        """Registra un frame sin detección válida amortiguando la inercia proyectada."""
        self.frames_sin_deteccion += 1
        self.frames_seguidos_id = 0
        # Si salió por el borde de la cámara, frenamos la proyección para que el
        # punto predicho no siga viajando fuera del lienzo.
        factor_freno = 0.50 if self.salio_por_borde else 0.80
        self.vel_px = (self.vel_px[0] * factor_freno, self.vel_px[1] * factor_freno)

    # --------------------------------------------------------------
    def _actualizar_escala(
        self, alto_detectado: float, en_borde: bool = False, demasiado_recortada: bool = False
    ) -> float:
        """Escala px->m estable. Congela actualizaciones si la caja está excesivamente recortada
        o si hay motion blur por velocidad alta."""
        if demasiado_recortada and self.alto_ema is not None:
            return self.alto_ema

        if not self._escala_sembrada:
            self._altos_semilla.append(alto_detectado)
            self.alto_ema = float(np.median(self._altos_semilla))
            if len(self._altos_semilla) >= self.cfg.frames_semilla_escala:
                self._escala_sembrada = True
                self.ancla_confirmada = True
                logger.info(
                    f"Escala y ancla fijadas por mediana de {len(self._altos_semilla)} "
                    f"frames: {self.alto_ema:.1f}px = {self.cfg.diametro_disco_m}m"
                )
            return self.alto_ema

        # Si la escala ya está sembrada, congelar si está en el borde o hay velocidad alta
        if en_borde:
            return self.alto_ema

        vel_mag = math.hypot(self.vel_px[0], self.vel_px[1])
        if vel_mag > 3.0:
            return self.alto_ema

        desvio = abs(alto_detectado - self.alto_ema) / max(self.alto_ema, 1e-6)
        if desvio > self.cfg.salto_max_escala:
            return self.alto_ema

        a = self.cfg.ema_alto_caja
        self.alto_ema = self.alto_ema * (1.0 - a) + alto_detectado * a
        return self.alto_ema

    # --------------------------------------------------------------
    def _compensar_caja_borde(
        self, caja: np.ndarray, ancho_img: int, alto_img: int
    ) -> tuple[int, int, float, bool, bool, Optional[str]]:
        """Reconstruye el centro real (cx, cy) y el diámetro estimado ANTES de evaluar
        el tracking cuando el disco se sale parcialmente por los bordes del encuadre.
        Retorna: (cx, cy, tam_efectivo, en_borde, demasiado_recortada, lado_borde)"""
        x_min, y_min, x_max, y_max = map(float, caja)
        ancho_c = max(0.0, x_max - x_min)
        alto_c = max(0.0, y_max - y_min)
        cx = (x_min + x_max) / 2.0
        cy = (y_min + y_max) / 2.0

        m = self.cfg.margen_borde_px
        toca_arr = y_min <= m
        toca_aba = y_max >= (alto_img - m)
        toca_izq = x_min <= m
        toca_der = x_max >= (ancho_img - m)
        en_borde = toca_arr or toca_aba or toca_izq or toca_der

        lado_borde = None
        if toca_der:
            lado_borde = "der"
        elif toca_izq:
            lado_borde = "izq"
        elif toca_aba:
            lado_borde = "aba"
        elif toca_arr:
            lado_borde = "arr"

        # Si se recorta verticalmente pero no horizontalmente, el ancho refleja el diámetro real
        if (toca_arr or toca_aba) and not (toca_izq or toca_der):
            tam_ef = ancho_c
        elif (toca_izq or toca_der) and not (toca_arr or toca_aba):
            tam_ef = alto_c
        else:
            tam_ef = max(ancho_c, alto_c)

        demasiado_recortada = False
        if self.alto_ema is not None and self.alto_ema > 0 and en_borde:
            radio = self.alto_ema / 2.0
            min_dim = min(ancho_c, alto_c)
            if min_dim < self.alto_ema * self.cfg.frac_min_visible_borde:
                demasiado_recortada = True

            if toca_arr and not toca_aba and alto_c < self.alto_ema * 0.94:
                cy = y_max - radio
            elif toca_aba and not toca_arr and alto_c < self.alto_ema * 0.94:
                cy = y_min + radio

            if toca_izq and not toca_der and ancho_c < self.alto_ema * 0.94:
                cx = x_max - radio
            elif toca_der and not toca_izq and ancho_c < self.alto_ema * 0.94:
                cx = x_min + radio

            tam_ef = max(tam_ef, self.alto_ema if not demasiado_recortada else tam_ef)

        return int(round(cx)), int(round(cy)), float(tam_ef), en_borde, demasiado_recortada, lado_borde

    # --------------------------------------------------------------
    def _es_candidato_fisicamente_valido(
        self, cx: int, cy: int, tam_ef: float, en_borde: bool, demasiado_recortada: bool,
        ancho_img: int = 1920, alto_img: int = 1080
    ) -> bool:
        """Verifica que la caja candidata no sea un salto a otro disco del gimnasio."""
        if demasiado_recortada or tam_ef <= 0:
            return False

        # Si tenemos x_ancla, SIEMPRE validamos el pasillo vertical (no teletransporte horizontal)
        if self.x_ancla is not None:
            max_dx = (
                self.alto_ema * (self.cfg.max_desvio_x_frac * 1.40)
                if (self.alto_ema and self.alto_ema > 0)
                else max(70.0, ancho_img * 0.25)
            )
            if abs(cx - self.x_ancla) > max_dx:
                return False

        if self.ultima_pos is None or self.alto_ema is None or self.alto_ema <= 0:
            return True

        # 1. Consistencia de diámetro (evita saltar a discos más pequeños o lejanos en el fondo)
        err_tam = abs(tam_ef - self.alto_ema) / self.alto_ema
        if not en_borde and err_tam > self.cfg.salto_max_escala:
            return False

        # 2. Pasillo vertical estricto respecto a última posición
        dx_ult = abs(cx - self.ultima_pos[0])
        max_dx_ult = self.alto_ema * self.cfg.max_desvio_x_frac
        if dx_ult > max_dx_ult:
            return False

        # 3. Si el disco había salido por el borde de la cámara, verificar consistencia espacial
        if self.salio_por_borde and self.frames_sin_deteccion > 0:
            # No puede reingresar por un lado opuesto de la pantalla
            if self.borde_salida == "der" and cx < ancho_img * 0.50:
                return False
            if self.borde_salida == "izq" and cx > ancho_img * 0.50:
                return False
            if self.borde_salida == "arr" and cy > alto_img * 0.50:
                return False
            if self.borde_salida == "aba" and cy < alto_img * 0.50:
                return False

            ref_pos = self.pos_salida if self.pos_salida is not None else self.ultima_pos
            dy_borde = abs(cy - ref_pos[1])
            if dy_borde > self.alto_ema * 1.25:
                return False

        # 4. Límite de velocidad máxima posible entre frames vistos
        dt_frames = max(1, self.frames_sin_deteccion + 1)
        salto_max_px = self.alto_ema * self.cfg.max_salto_frame_frac * min(dt_frames, 4)
        if not self.salio_por_borde and dt_frames <= 4:
            dist_real = math.hypot(cx - self.ultima_pos[0], cy - self.ultima_pos[1])
            if dist_real > salto_max_px:
                return False

        return True

    # --------------------------------------------------------------
    def procesar(self, frame: np.ndarray) -> Optional[Deteccion]:
        alto_img, ancho_img = frame.shape[:2]
        self._frame_count += 1
        try:
            pred = self.modelo.track(
                frame,
                persist=True,
                tracker=os.path.join(os.path.dirname(__file__), "vbt_bytetrack.yaml"),
                **_kwargs_fp16(self.modelo.track),
                agnostic_nms=True,
                conf=self.cfg.conf_deteccion,
                iou=self.cfg.iou_nms,
                max_det=self.cfg.max_detecciones,
                imgsz=self.cfg.imgsz,
                device=0,
                verbose=False,
            )
        except Exception as e:
            self._fallos_yolo_seguidos += 1
            if self._fallos_yolo_seguidos >= 15 and not self._aviso_yolo_emitido:
                self._aviso_yolo_emitido = True
                logger.error(
                    f"YOLO lleva {self._fallos_yolo_seguidos} frames fallando seguidos; "
                    f"no habrá análisis VBT. Último error: {e}"
                )
            else:
                logger.debug(f"YOLO track falló en este frame: {e}")
            self._perder()
            return None
        self._fallos_yolo_seguidos = 0

        if pred[0].boxes.id is None:
            self._perder()
            return None

        cajas = pred[0].boxes.xyxy.cpu().numpy()
        ids = pred[0].boxes.id.cpu().numpy().astype(int)

        seleccion = self._seleccionar_caja(cajas, ids, ancho_img, alto_img)
        if seleccion is None:
            self._perder()
            return None

        caja, cx, cy, alto_detectado, en_borde, rec_extremo, lado_borde = seleccion
        x_min, y_min, x_max, y_max = caja

        alto_estable = self._actualizar_escala(alto_detectado, en_borde=en_borde, demasiado_recortada=rec_extremo)
        m_por_px = self.cfg.diametro_disco_m / alto_estable

        # Confirmación de ancla por persistencia temporal del tracking
        self.frames_seguidos_id += 1
        self.total_frames_trackeados += 1
        if self.frames_seguidos_id >= 8 or self.total_frames_trackeados >= 12:
            if not self.ancla_confirmada:
                self.ancla_confirmada = True
                logger.info(
                    f"Ancla de serie confirmada tras {self.total_frames_trackeados} frames de tracking."
                )

        # Actualizar velocidad en px/frame y pasillo horizontal X
        if self.ultima_pos is not None:
            dt_f = max(1, self.frames_sin_deteccion + 1)
            v_inst_px = ((cx - self.ultima_pos[0]) / dt_f, (cy - self.ultima_pos[1]) / dt_f)
            self.vel_px = (
                self.vel_px[0] * 0.4 + v_inst_px[0] * 0.6,
                self.vel_px[1] * 0.4 + v_inst_px[1] * 0.6,
            )
        else:
            self.vel_px = (0.0, 0.0)

        self.x_ancla = float(cx) if self.x_ancla is None else (self.x_ancla * 0.92 + cx * 0.08)
        self.ultima_pos = (cx, cy)
        self.salio_por_borde = en_borde
        if en_borde:
            self.borde_salida = lado_borde
            self.pos_salida = (cx, cy)
        else:
            self.borde_salida = None
            self.pos_salida = None

        self.frames_sin_deteccion = 0

        return Deteccion(
            cx, cy, float(x_min), float(y_min), float(x_max), float(y_max), alto_estable, m_por_px
        )

    # --------------------------------------------------------------
    def _seleccionar_caja(
        self, cajas: np.ndarray, ids: np.ndarray, ancho_img: int, alto_img: int
    ) -> Optional[tuple[np.ndarray, int, int, float, bool, bool, Optional[str]]]:
        if len(cajas) == 0:
            return None

        # Pre-calcular geometría compensada por borde para todas las detecciones
        candidatos = []
        for idx, (caja, id_c) in enumerate(zip(cajas, ids)):
            cx, cy, tam_ef, en_borde, rec_extremo, lado_borde = self._compensar_caja_borde(
                caja, ancho_img, alto_img
            )
            candidatos.append((idx, int(id_c), caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde))

        # Registrar IDs co-existentes cuando el ID fijado está presente
        if self.id_fijado is not None and self.id_fijado in ids:
            for other_id in ids:
                if other_id != self.id_fijado:
                    self.coexisting_ids[int(other_id)] = self._frame_count

        # 1) El ID fijado sigue presente -> validamos que ByteTrack no haya hecho ID-switch a otro disco
        if self.id_fijado is not None:
            for idx, id_c, caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde in candidatos:
                if id_c == self.id_fijado:
                    if self._es_candidato_fisicamente_valido(
                        cx, cy, tam_ef, en_borde, rec_extremo, ancho_img, alto_img
                    ):
                        return caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde
                    else:
                        logger.debug(
                            f"Rechazado salto irracional en mismo ID {id_c} "
                            f"hacia ({cx}, {cy}) desde {self.ultima_pos}"
                        )
                    break

        # 2) Re-enganche cinemático dentro del pasillo vertical del disco
        if self.id_fijado is not None and self.ultima_pos is not None:
            if self.salio_por_borde:
                pos_predicha = (float(self.ultima_pos[0]), float(self.ultima_pos[1]))
            else:
                amort = 0.75 ** min(self.frames_sin_deteccion, 8)
                pos_predicha = (
                    self.ultima_pos[0] + self.vel_px[0] * min(self.frames_sin_deteccion, 4) * amort,
                    self.ultima_pos[1] + self.vel_px[1] * min(self.frames_sin_deteccion, 4) * amort,
                )

            base_r = (
                self.alto_ema * self.cfg.factor_reenganche
                if self.alto_ema
                else alto_img * 0.12
            )
            factor_crec = min(
                1.0 + self.frames_sin_deteccion * self.cfg.crecimiento_radio_por_frame,
                self.cfg.factor_radio_max_tope,
            )
            radio_max = base_r * factor_crec

            mejor_cand = None
            mejor_score = float("inf")

            for idx, id_c, caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde in candidatos:
                if not self._es_candidato_fisicamente_valido(
                    cx, cy, tam_ef, en_borde, rec_extremo, ancho_img, alto_img
                ):
                    continue

                # REGLA DE CO-EXISTENCIA: Si este ID fue visto simultáneamente con
                # nuestro disco objetivo en los últimos 90 frames (~1.5s), es otro disco del gimnasio
                if id_c != self.id_fijado:
                    last_seen_together = self.coexisting_ids.get(id_c, -9999)
                    if (self._frame_count - last_seen_together) < 90:
                        continue

                dx = cx - pos_predicha[0]
                dy = cy - pos_predicha[1]
                # Distancia anisotrópica: penaliza el doble cualquier desvío horizontal (X)
                dist_euclidea = math.hypot(dx, dy)
                dist_anisotropica = math.hypot(dx * 2.2, dy)

                if dist_euclidea <= radio_max:
                    if self.alto_ema and self.alto_ema > 0:
                        err_tam = abs(tam_ef - self.alto_ema) / self.alto_ema
                        dist_anisotropica *= (1.0 + 2.5 * err_tam)
                    if dist_anisotropica < mejor_score:
                        mejor_score = dist_anisotropica
                        mejor_cand = (id_c, caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde, dist_euclidea)

            if mejor_cand is not None:
                id_c, caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde, dist_e = mejor_cand
                if id_c != self.id_fijado:
                    logger.info(
                        f"Re-enganche cinemático seguro a ID {id_c} "
                        f"(dist {dist_e:.0f}px, radio máx {radio_max:.0f}px)"
                    )
                self.id_fijado = id_c
                return caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde

            # GUARDIA CLAVE: Si el ancla ya fue confirmada (serie en curso), NUNCA soltamos
            # la referencia para caer al Paso 3 (evita enganchar otro disco cuando sale de cámara).
            if self.ancla_confirmada or self.frames_sin_deteccion < self.cfg.frames_para_soltar_id:
                return None

            logger.info(
                f"Disco perdido {self.frames_sin_deteccion} frames durante setup inicial: "
                f"se libera el ID para re-fijar."
            )
            self.id_fijado = None
            self.ultima_pos = None
            self.vel_px = (0.0, 0.0)

        # 3) Fijación inicial por PUNTUACIÓN (solo ocurre en el setup antes de confirmar el ancla)
        linea_suelo = alto_img * self.cfg.frac_linea_suelo
        mejor_tuple = None
        mejor_score = 0.0
        for idx, id_c, caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde in candidatos:
            if rec_extremo:
                continue

            # Si ya teníamos un x_ancla previo, exigir que no esté lejos
            if self.x_ancla is not None:
                max_dx = (
                    self.alto_ema * (self.cfg.max_desvio_x_frac * 1.5)
                    if (self.alto_ema and self.alto_ema > 0)
                    else ancho_img * 0.30
                )
                if abs(cx - self.x_ancla) > max_dx:
                    continue

            area = float((caja[2] - caja[0]) * (caja[3] - caja[1]))
            if area <= 0:
                continue
            score = area * (self.cfg.penalizacion_suelo if cy >= linea_suelo else 1.0)
            if en_borde:
                score *= 0.5  # Priorizar disco central del levantamiento
            if score > mejor_score:
                mejor_score = score
                mejor_tuple = (id_c, caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde)

        if mejor_tuple is not None:
            id_c, caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde = mejor_tuple
            self.id_fijado = id_c
            return caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde
        return None


# ==============================================================================
# ESTIMADOR CINEMÁTICO (posición suavizada + rechazo de saltos discontinuos)
# ==============================================================================
class EstimadorCinematica:
    """
    Mantiene una ventana deslizante de (t, y) y devuelve el instante evaluado,
    la posición suavizada y la velocidad vertical CON SIGNO en m/s (+ sube, - baja).
    Incluye compuerta anti-discontinuidad para evitar picos por saltos de caja.
    """

    def __init__(self, cfg: ConfigVBT = CONFIG, fps: Optional[float] = None):
        self.cfg = cfg
        if fps and fps > 0:
            n = int(max(5, round(fps * cfg.ventana_s)))
        else:
            n = cfg.ventana_savgol
        self.n = n
        self.ts: deque = deque(maxlen=n)
        self.ys: deque = deque(maxlen=n)
        self.reinicios_por_hueco = 0

    def reiniciar(self):
        self.ts.clear()
        self.ys.clear()

    def actualizar(self, t: float, y_px: float, m_por_px: float) -> tuple[float, float, float]:
        t = float(t)
        y_px = float(y_px)

        if self.ts:
            dt_ultimo = t - self.ts[-1]
            # 1. Hueco temporal largo -> reinicio limpio de la ventana
            if dt_ultimo > self.cfg.gap_reinicio_s:
                self.reinicios_por_hueco += 1
                logger.debug(f"Hueco de {dt_ultimo:.2f}s sin disco: reiniciando ventana cinemática.")
                self.reiniciar()
            # 2. Compuerta de velocidad implícita: si entre muestras cercanas hay un salto
            # físicamente imposible (> vel_max_valida * 1.15), no ajustamos un polinomio sobre el escalón
            elif dt_ultimo > 1e-4:
                v_implicita = abs(y_px - self.ys[-1]) * m_por_px / dt_ultimo
                if v_implicita > self.cfg.vel_max_valida * 1.15:
                    logger.warning(
                        f"Salto discontinuo detectado en señal Y ({v_implicita:.2f} m/s implícitos): "
                        f"se amortigua muestra para proteger el VBT."
                    )
                    # Acotamos la coordenada al desplazamiento máximo plausible desde el punto previo
                    dy_max_px = (self.cfg.vel_max_valida * dt_ultimo) / max(m_por_px, 1e-6)
                    signo = 1.0 if y_px >= self.ys[-1] else -1.0
                    y_px = float(self.ys[-1] + signo * dy_max_px)

        self.ts.append(t)
        self.ys.append(y_px)

        if len(self.ys) < 3:
            if len(self.ys) >= 2:
                dt = self.ts[-1] - self.ts[-2]
                v = (-(self.ys[-1] - self.ys[-2]) / dt * m_por_px) if dt > 0 else 0.0
                t_eval = float(self.ts[-1])
            else:
                v = 0.0
                t_eval = float(self.ts[-1])
            return t_eval, float(self.ys[-1]), self._limitar(v)

        ts = np.asarray(self.ts, dtype=float)
        ys = np.asarray(self.ys, dtype=float)
        t_rel = ts - ts[-1]

        t_eval_rel = t_rel[-2]
        t_eval_absoluto = float(ts[-2])
        grado = self.cfg.poly_savgol if ts.size >= self.cfg.poly_savgol + 2 else 1

        try:
            coef = np.polyfit(t_rel, ys, grado)
        except Exception as e:
            logger.debug(f"Ajuste local falló ({e}); uso diferencia finita.")
            dt = ts[-1] - ts[-2]
            v = (-(ys[-1] - ys[-2]) / dt * m_por_px) if dt > 0 else 0.0
            return t_eval_absoluto, float(ys[-2]), self._limitar(v)

        y_ajustada = float(np.polyval(coef, t_eval_rel))
        dy_dt = float(np.polyval(np.polyder(coef), t_eval_rel))
        v = -dy_dt * m_por_px
        return t_eval_absoluto, y_ajustada, self._limitar(v)

    def _limitar(self, v: float) -> float:
        tope = self.cfg.vel_max_valida
        return float(np.clip(v, -tope, tope))


# ==============================================================================
# ESTIMADOR DE POSE (cinemática articular, OPCIONAL)
# ==============================================================================
# Keypoints COCO de los modelos YOLO-pose
_KP = {"l_shoulder": 5, "r_shoulder": 6, "l_hip": 11, "r_hip": 12,
       "l_knee": 13, "r_knee": 14, "l_ankle": 15, "r_ankle": 16}


def _angulo_3p(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> Optional[float]:
    """Ángulo (grados) en el vértice b del triángulo a-b-c."""
    ba, bc = a - b, c - b
    den = float(np.linalg.norm(ba) * np.linalg.norm(bc))
    if den < 1e-6:
        return None
    cosang = float(np.clip(np.dot(ba, bc) / den, -1.0, 1.0))
    return float(np.degrees(np.arccos(cosang)))


class EstimadorPose:
    """
    Cinemática articular vía YOLO-pose. Reutiliza modelo en caché global y
    asocia espacialmente al atleta según la proximidad de los keypoints al disco.
    """

    def __init__(self, ruta_o_modelo_pose: Optional[Union[str, YOLO]] = None, cfg: ConfigVBT = CONFIG):
        self.cfg = cfg
        self.activo = False
        self.modelo = None
        self.serie: list = []        # [(t, angulo_rodilla, angulo_cadera)]
        if not ruta_o_modelo_pose:
            return
        try:
            if isinstance(ruta_o_modelo_pose, str):
                self.modelo = _obtener_modelo_yolo(ruta_o_modelo_pose)
            else:
                self.modelo = ruta_o_modelo_pose
            self.activo = True
            logger.info(f"Cinemática articular activada ({ruta_o_modelo_pose}).")
        except Exception as e:
            logger.warning(f"No se pudo cargar el modelo de pose ({e}). "
                           "Métricas articulares desactivadas; el resto sigue.")

    def reset(self) -> None:
        """Reinicia las series registradas del estimador de pose."""
        self.serie.clear()

    # --------------------------------------------------------------
    def procesar(self, frame: np.ndarray, t: float,
                 pos_disco: Optional[tuple[int, int]] = None) -> None:
        if not self.activo:
            return
        try:
            pred = self.modelo.predict(frame, conf=0.25, imgsz=640, device=0,
                                       max_det=4, verbose=False,
                                       **_kwargs_fp16(self.modelo.predict))
        except Exception as e:
            logger.debug(f"Pose falló en este frame: {e}")
            return

        r = pred[0]
        if r.keypoints is None or r.keypoints.xy is None or len(r.keypoints.xy) == 0:
            return

        num_personas = len(r.keypoints.xy)
        idx = 0

        # FIX: Asociación espacial atleta-barra. Si hay varias personas en el encuadre (ej. spotter),
        # seleccionamos el esqueleto cuyas muñecas/hombros o centro de masa estén más cerca del disco.
        if num_personas > 1 and pos_disco is not None:
            disc_x, disc_y = pos_disco
            mejor_dist = float("inf")
            for i in range(num_personas):
                kpts = r.keypoints.xy[i].cpu().numpy()
                confs = (r.keypoints.conf[i].cpu().numpy()
                         if r.keypoints.conf is not None else np.ones(len(kpts)))
                pts_relevantes = [kpts[k] for k in (9, 10, 7, 8, 5, 6) if confs[k] > 0.25]
                if pts_relevantes:
                    d_min = min(math.hypot(p[0] - disc_x, p[1] - disc_y) for p in pts_relevantes)
                elif r.boxes is not None and len(r.boxes) > i:
                    box = r.boxes.xyxy[i].cpu().numpy()
                    cx_p = (box[0] + box[2]) / 2.0
                    cy_p = (box[1] + box[3]) / 2.0
                    d_min = math.hypot(cx_p - disc_x, cy_p - disc_y)
                else:
                    d_min = float("inf")

                if d_min < mejor_dist:
                    mejor_dist = d_min
                    idx = i
        elif r.boxes is not None and len(r.boxes) > 0:
            xyxy = r.boxes.xyxy.cpu().numpy()
            areas = (xyxy[:, 2] - xyxy[:, 0]) * (xyxy[:, 3] - xyxy[:, 1])
            idx = int(np.argmax(areas))

        xy = r.keypoints.xy[idx].cpu().numpy()
        if r.keypoints.conf is not None:
            conf = r.keypoints.conf[idx].cpu().numpy()
        else:
            conf = np.ones(len(xy))

        lado = self._mejor_lado(conf)
        if lado is None:
            return
        i_s, i_h = _KP[f"{lado}_shoulder"], _KP[f"{lado}_hip"]
        i_k, i_a = _KP[f"{lado}_knee"], _KP[f"{lado}_ankle"]
        if min(conf[i_s], conf[i_h], conf[i_k], conf[i_a]) < self.cfg.conf_keypoint:
            return

        rodilla = _angulo_3p(xy[i_h], xy[i_k], xy[i_a])
        cadera = _angulo_3p(xy[i_s], xy[i_h], xy[i_k])
        if rodilla is None or cadera is None:
            return
        self.serie.append((float(t), rodilla, cadera))

    # --------------------------------------------------------------
    def _mejor_lado(self, conf: np.ndarray) -> Optional[str]:
        izq = float(np.mean([conf[i] for i in (5, 11, 13, 15)]))
        der = float(np.mean([conf[i] for i in (6, 12, 14, 16)]))
        if max(izq, der) < self.cfg.conf_keypoint:
            return None
        return "l" if izq >= der else "r"

    # --------------------------------------------------------------
    def metricas_rep(self, t_ini: float, t_fin: float,
                     t_ini_concentrica: float) -> dict:
        """Métricas articulares dentro de la ventana temporal de una rep."""
        if not self.serie:
            return {}
        sel = [(t, k, h) for t, k, h in self.serie
               if (t_ini - 0.05) <= t <= (t_fin + 0.05)]
        if len(sel) < 4:
            return {}
        ts = np.array([p[0] for p in sel])
        rod = np.array([p[1] for p in sel])
        cad = np.array([p[2] for p in sel])

        out = {"angulo_rodilla_min": float(round(float(rod.min()), 1)),
               "angulo_cadera_min": float(round(float(cad.min()), 1))}

        # Velocidad angular pico de extensión de rodilla (fase concéntrica)
        m = ts >= (t_ini_concentrica - 0.05)
        if int(m.sum()) >= 4:
            try:
                grad = np.gradient(rod[m], ts[m])
                out["vel_angular_rodilla_max"] = float(round(float(np.max(grad)), 0))
            except Exception:
                pass
        return out


# ==============================================================================
# MÁQUINA DE ESTADOS DE REPETICIONES
# ==============================================================================
class MaquinaEstadosRep:
    """
    Estados: calibrando -> reposo -> excentrica <-> concentrica.
    Trabaja con la posición Y suavizada, la X cruda (para el bar path) y la
    velocidad con signo. Devuelve un objeto Repeticion cada vez que se cierra
    una rep válida.

    v3: además de contar y medir velocidad, cronometra las fases (excéntrica /
    amortiguación / concéntrica), captura el bar path de cada rep y mantiene un
    contador de quietud global (`frames_quietos`) para el fin de serie.
    """

    def __init__(self, cfg: ConfigVBT, fps: float):
        self.cfg = cfg
        self.fps = fps
        self.estado = "calibrando"
        self.reps = 0

        self.y_inicial: Optional[float] = None
        self.y_mas_bajo: Optional[float] = None
        self.y_tope: Optional[float] = None
        self.inicio_fase_y: Optional[float] = None

        self.frames_estables = 0             # solo para la calibración inicial
        self.frames_quietos = 0              # quietud global (fin de serie)
        self.buffer_arranque: list = []      # reposo: (t, v) previos al despegue
        self.buffer_subida: list = []        # excéntrica: (t, v>0) tras el fondo
        self.vels_concentrica: list = []     # (t, v) de la concéntrica actual
        self.t_inicio_concentrica = 0.0
        self.escala_fija_m_px = 0.005        # fallback conservador
        self.escala_fijada = False           # FIX: congelada tras el setup
        self._ultimo_alto_caja: Optional[float] = None

        # Cronometraje de fases (v3)
        self.t_inicio_rep = 0.0
        self.t_inicio_excentrica = 0.0
        self.t_fin_descenso = 0.0            # última vez que profundizó
        self.t_fin_ascenso = 0.0             # última vez que ganó altura ÚTIL
        # Fin del empuje (v3.4): `y_tope` sigue siendo el mínimo absoluto (lo
        # necesita la detección de reversa para cerrar la rep), pero el ROM y el
        # tiempo concéntricos se miden contra `y_tope_empuje`, que se CONGELA en
        # cuanto la barra deja de subir. Sin esta separación el lockout entero
        # contaba como fase concéntrica.
        self.y_tope_empuje: Optional[float] = None
        self.y_ref_ganancia: Optional[float] = None
        self.t_ultima_ganancia = 0.0
        self.empuje_cerrado = False
        self._t_exc_pend = 0.0               # excéntrica de la rep en curso
        self._t_amort_pend: Optional[float] = None

        # Bar path (v3)
        self.x_suave: Optional[float] = None
        self.path_rep: list = []             # [(t, x, y)] desde inicio de rep

        # Telemetría / diagnóstico
        self.fase_maxima = "calibrando"
        self.max_descenso_px = 0.0
        self.vels_serie: list = []           # todas las velocidades concéntricas

    # --------------------------------------------------------------
    def _umbrales(self, alto_caja: Optional[float]) -> tuple:
        c = self.cfg
        if alto_caja and alto_caja > 0:
            return (max(alto_caja * c.frac_rom_minimo, c.rom_minimo_px_piso),
                    max(alto_caja * c.frac_reversa, c.reversa_px_piso),
                    max(alto_caja * c.frac_compromiso, c.compromiso_px_piso))
        return c.rom_minimo_px_piso + 5.0, c.reversa_px_piso + 2.0, c.compromiso_px_piso

    def _fijar_escala(self, alto_caja: Optional[float]):
        if alto_caja and alto_caja > 0:
            self.escala_fija_m_px = self.cfg.diametro_disco_m / alto_caja
            self.escala_fijada = True

    def _reiniciar_a_reposo(self, y: float):
        self.estado = "reposo"
        self.y_inicial = self.inicio_fase_y = self.y_mas_bajo = self.y_tope = y
        self.buffer_arranque.clear()
        self.buffer_subida.clear()
        self.path_rep.clear()
        self._t_exc_pend = 0.0
        self._t_amort_pend = None

    def _entrar_excentrica(self, t: float, y: float):
        """El caller debe fijar antes inicio_fase_y y t_inicio_rep."""
        self.estado = self.fase_maxima = "excentrica"
        self.y_mas_bajo = y
        self.t_inicio_excentrica = t
        self.t_fin_descenso = t
        self.buffer_subida.clear()
        self.path_rep = [(t, float(self.x_suave), float(y))]

    def _abrir_empuje(self, t: float, y: float):
        """Arranca el cronómetro del empuje concéntrico (v3.4)."""
        self.y_tope = self.y_tope_empuje = self.y_ref_ganancia = y
        self.t_ultima_ganancia = t
        self.empuje_cerrado = False

    def _entrar_concentrica_desde_fondo(self, t: float, y: float):
        self.estado = self.fase_maxima = "concentrica"
        self._abrir_empuje(t, y)
        self.inicio_fase_y = self.y_mas_bajo
        # Excéntrica: desde su inicio hasta la última vez que profundizó
        self._t_exc_pend = max(self.t_fin_descenso - self.t_inicio_excentrica, 0.0)
        # La concéntrica real arranca en el primer frame de subida (buffer),
        # no cuando se superó el umbral de reversa -> tiempos/vels más fieles
        if self.buffer_subida:
            self.t_inicio_concentrica = self.buffer_subida[0][0]
            self.vels_concentrica = list(self.buffer_subida)
        else:
            self.t_inicio_concentrica = t
            self.vels_concentrica = []
        self.t_fin_ascenso = self.t_inicio_concentrica
        self.vels_serie.extend(v for _, v in self.vels_concentrica)
        # Amortiguación: fondo -> primer empuje real (transición isométrica)
        self._t_amort_pend = max(self.t_inicio_concentrica - self.t_fin_descenso, 0.0)
        self.buffer_subida.clear()

    # --------------------------------------------------------------
    def actualizar(self, t: float, x: float, y: float, v: float,
                   alto_caja: Optional[float]) -> Optional[Repeticion]:
        self._ultimo_alto_caja = alto_caja
        # FIX: Solo fijamos la escala si aún no ha sido fijada (o en el setup).
        # Esto evita que el jitter o motion blur frame a frame mute continuamente la escala física.
        if not self.escala_fijada and alto_caja and alto_caja > 0:
            self._fijar_escala(alto_caja)
        rom_min, reversa, compromiso = self._umbrales(alto_caja)

        # X con EMA ligera para el bar path (la Y ya llega filtrada por SG)
        self.x_suave = x if self.x_suave is None else (self.x_suave * 0.7 + x * 0.3)

        if self.y_inicial is None:
            self.y_inicial = self.y_mas_bajo = self.y_tope = self.inicio_fase_y = y

        # Quietud global: alimenta el fin de serie por inactividad (FIX v3:
        # antes solo se contaba en "calibrando" y el cierre nunca disparaba)
        self.frames_quietos = (self.frames_quietos + 1
                               if abs(v) < self.cfg.vel_min_registro else 0)

        self.max_descenso_px = max(self.max_descenso_px, abs(y - self.y_inicial))

        # Bar path de la rep en curso (acotado en memoria)
        if self.estado in ("excentrica", "concentrica"):
            self.path_rep.append((t, float(self.x_suave), float(y)))
            tope_pts = int(self.fps * 30)
            if len(self.path_rep) > tope_pts:
                self.path_rep = self.path_rep[-tope_pts:]

        rep_cerrada: Optional[Repeticion] = None

        if self.estado == "calibrando":
            # Efecto trinquete: siempre guardamos el punto más alto del inicio
            if y < self.y_inicial:
                self.y_inicial = y

            self.frames_estables = (self.frames_estables + 1
                                    if abs(v) < self.cfg.vel_min_setup else 0)

            if self.frames_estables > self.fps * self.cfg.seg_estabilidad_setup:
                logger.info(f"Setup por estabilización. ROM mínimo: {rom_min:.1f}px")
                self._fijar_escala(alto_caja)
                self._reiniciar_a_reposo(y)
            elif (y - self.y_inicial) > compromiso:
                logger.info("Setup forzado por descenso inmediato: repetición iniciada.")
                self._fijar_escala(alto_caja)
                self.inicio_fase_y = self.y_tope = self.y_inicial
                self.t_inicio_rep = t
                self._entrar_excentrica(t, y)

        elif self.estado == "reposo":
            # Buffer de arranque: si el press/dominada empieza directo hacia arriba
            if v > self.cfg.vel_min_registro:
                self.buffer_arranque.append((t, v))
            else:
                self.buffer_arranque.clear()

            if y > (self.y_inicial + compromiso):
                self.inicio_fase_y = self.y_inicial
                self.t_inicio_rep = t
                self._entrar_excentrica(t, y)
            elif y < (self.y_inicial - rom_min):
                # Arranque directo hacia arriba (dominadas / press desde abajo):
                # no hay excéntrica previa ni transición isométrica que medir.
                # FIX v3.4: el umbral era `compromiso` (15 px), tan bajo que un
                # simple unrack o un reajuste de hombros lo disparaba: la 1ª rep
                # de sentadilla y de peso muerto salía por esta rama y se
                # reportaba con t_excentrica_s=0 y amortiguación None. Se exige
                # un ROM completo para aceptar que la serie empezó subiendo.
                self.estado = self.fase_maxima = "concentrica"
                self._abrir_empuje(t, y)
                self.inicio_fase_y = self.y_inicial
                self.t_inicio_concentrica = (self.buffer_arranque[0][0]
                                             if self.buffer_arranque else t)
                self.t_inicio_rep = self.t_inicio_concentrica
                self.vels_concentrica = list(self.buffer_arranque)
                self.t_fin_ascenso = self.t_inicio_concentrica
                self.vels_serie.extend(v for _, v in self.vels_concentrica)
                self._t_exc_pend = 0.0
                self._t_amort_pend = None
                self.path_rep = [(t, float(self.x_suave), float(y))]

        elif self.estado == "excentrica":
            # Micro-blips al tocar fondo: si el conato de subida quedó "viejo"
            # (siguió una pausa isométrica), NO era el inicio del empuje real.
            # Sin esta purga, t_inicio_concentrica se ancla demasiado pronto y
            # la pausa entera se contabiliza como fase concéntrica.
            if (self.buffer_subida
                    and (t - self.buffer_subida[-1][0]) > 2.5 / self.fps):
                self.buffer_subida.clear()

            if y > self.y_mas_bajo:
                # Sigue profundizando: el fondo se redefine y el reloj de la
                # amortiguación se reinicia
                self.y_mas_bajo = y
                self.t_fin_descenso = t
                self.buffer_subida.clear()
            elif v < -self.cfg.vel_min_registro:
                # Vuelve a bajar sin superar el fondo: aborta el conato de subida
                self.buffer_subida.clear()
            elif v > self.cfg.vel_min_registro:
                self.buffer_subida.append((t, v))

            if y < (self.y_mas_bajo - reversa):
                if abs(self.y_mas_bajo - self.inicio_fase_y) >= rom_min:
                    self._entrar_concentrica_desde_fondo(t, y)
                else:
                    self._reiniciar_a_reposo(y)  # rebote pequeño = ruido

        elif self.estado == "concentrica":
            # Solo se acumulan muestras mientras el empuje sigue vivo: las del
            # lockout son ruido alrededor de cero y hundían la media y el MPV.
            if v > 0 and not self.empuje_cerrado:
                self.vels_concentrica.append((t, v))
                self.vels_serie.append(v)

            # --- Ganancia de altura ÚTIL (v3.4) --------------------------
            # El tope de empuje solo avanza si la mejora supera el ruido de la
            # señal. Medido en peso muerto: tras el bloqueo la barra "ganaba"
            # 8.6 px (3.4% del ROM) por reajuste postural 3.5 s más tarde, y
            # eso estiraba la concéntrica de 1.73 s a 5.23 s.
            if self.y_tope_empuje is None:
                self._abrir_empuje(t, y)

            if not self.empuje_cerrado:
                # ROM: mínimo absoluto alcanzado mientras el empuje sigue vivo.
                # Se mide SIN margen para no recortar el recorrido real.
                if y < self.y_tope_empuje:
                    self.y_tope_empuje = y

                # ¿Sigue subiendo? Se decide contra una referencia con margen,
                # porque en el lockout la barra oscila varios píxeles (medido:
                # ±7 px) y sin ese colchón cualquier vaivén se leía como empuje
                # y volvía a arrancar el cronómetro.
                rom_actual = abs(self.y_tope_empuje - self.inicio_fase_y)
                margen = max(rom_actual * self.cfg.frac_ganancia_util, 2.0)
                sube_de_verdad = (y < (self.y_ref_ganancia - margen)
                                  and v > self.cfg.vel_sticking)
                if sube_de_verdad:
                    self.y_ref_ganancia = y
                    self.t_fin_ascenso = self.t_ultima_ganancia = t
                elif (t - self.t_ultima_ganancia) > self.cfg.t_fin_empuje_s:
                    # Tiempo sin subir: la barra está bloqueada arriba. El
                    # empuje terminó en la última ganancia real, no aquí.
                    self.empuje_cerrado = True

            # Cierre inmediato si el empuje concéntrico terminó en el lockout
            if self.empuje_cerrado:
                tope_val = self.y_tope_empuje if self.y_tope_empuje is not None else self.y_tope
                if abs(tope_val - self.inicio_fase_y) >= rom_min:
                    rep_cerrada = self._cerrar_repeticion(self.t_fin_ascenso if self.t_fin_ascenso > self.t_inicio_concentrica else t)
                    self.inicio_fase_y = self.y_tope
                    self.t_inicio_rep = t
                    self._reiniciar_a_reposo(y)

            if not rep_cerrada and y < self.y_tope:          # mínimo absoluto (cierre por reversa)
                self.y_tope = y

            if not rep_cerrada and y > (self.y_tope + reversa):
                if abs(self.y_tope - self.inicio_fase_y) >= rom_min:
                    rep_cerrada = self._cerrar_repeticion(t)
                    # Arranca la excéntrica de la siguiente repetición
                    self.inicio_fase_y = self.y_tope
                    self.t_inicio_rep = t
                    self._entrar_excentrica(t, y)
                else:
                    self._reiniciar_a_reposo(y)

        return rep_cerrada

    # --------------------------------------------------------------
    def cerrar_por_inactividad(self, t: float) -> Optional[Repeticion]:
        """
        Cierra la última rep si la serie termina bloqueada arriba (lockout),
        ya sea por quietud prolongada o porque el video se cortó ahí.
        Idempotente: tras cerrar, pasa a estado 'finalizado'.
        """
        if self.estado != "concentrica" or len(self.vels_concentrica) <= 3:
            return None
        rom_min, _, _ = self._umbrales(self._ultimo_alto_caja)
        tope = self.y_tope_empuje if self.y_tope_empuje is not None else self.y_tope
        if abs(tope - self.inicio_fase_y) < rom_min:
            return None
        # Este cierre llega tarde por definición (tras N s de quietud o al
        # acabarse el archivo): la rep terminó en el último empuje real, no
        # en el momento de esta llamada. Evita inflar tiempo_s con el lockout.
        t_fin_real = min(t, self.vels_concentrica[-1][0])
        rep = self._cerrar_repeticion(t_fin_real)
        self.estado = "finalizado"
        self.vels_concentrica = []
        return rep

    # --------------------------------------------------------------
    def _cerrar_repeticion(self, t_fin: float) -> Repeticion:
        # ROM y tiempo se miden sobre el EMPUJE, no sobre el mínimo absoluto:
        # `y_tope` puede haber avanzado unos píxeles durante el lockout.
        tope = self.y_tope_empuje if self.y_tope_empuje is not None else self.y_tope
        recorrido_px = abs(tope - self.inicio_fase_y)
        rom_m = recorrido_px * self.escala_fija_m_px
        # Fin real del empuje: última vez que la barra ganó altura. Así el
        # lockout (barra quieta arriba) no infla el tiempo concéntrico ni
        # contamina el bar path con frames estáticos.
        t_fin_empuje = (self.t_fin_ascenso
                        if self.t_fin_ascenso > self.t_inicio_concentrica
                        else t_fin)
        duracion = max(t_fin_empuje - self.t_inicio_concentrica, 1e-3)

        ts = np.array([p[0] for p in self.vels_concentrica], dtype=float)
        vs = np.array([p[1] for p in self.vels_concentrica], dtype=float)

        # FIX v3.2 (sticking point): v_avg = desplazamiento real / tiempo real.
        # Antes se promediaban solo las muestras con v > umbral_ruido (0.08),
        # lo que EXCLUÍA los frames del sticking point (0.02-0.08 m/s) e
        # inflaba la velocidad media de los grinds (0.111 medido → 0.17
        # reportado). ROM/tiempo es la definición canónica de la velocidad
        # media concéntrica y es inmune a umbrales.
        if rom_m > 0 and duracion > 0:
            v_avg = float(rom_m / duracion)
        else:
            validas = vs[vs > self.cfg.umbral_ruido_prop]
            v_avg = float(np.mean(validas)) if validas.size else 0.0

        sticking_s = self._calcular_sticking(ts, vs, duracion,
                                             t_fin_empuje)

        # FIX v3.4: el suavizado del pico se define en TIEMPO (~0.1 s). Con 3
        # muestras fijas promediaba 100 ms a 30 fps pero solo 50 ms a 60 fps, y
        # el video a 60 conservaba más ruido de derivada: el mismo movimiento
        # reportaba un pico más alto solo por haberse grabado más rápido.
        k = int(max(3, round(self.fps * 0.10)))
        if vs.size >= k:
            v_peak = float(np.max(np.convolve(vs, np.ones(k) / k, mode="valid")))
        elif vs.size >= 3:
            v_peak = float(np.max(np.convolve(vs, np.ones(3) / 3, mode="valid")))
        elif vs.size:
            v_peak = float(np.max(vs))
        else:
            v_peak = v_avg

        mpv = self._calcular_mpv(ts, vs, fallback=v_avg)
        desv_cm, eficiencia = self._metricas_trayectoria(t_fin_empuje)

        self.reps += 1
        rep = Repeticion(
            rep=self.reps,
            v_avg=round(v_avg, 3),
            v_peak=round(max(v_peak, v_avg), 3),
            mpv=round(mpv, 3),
            rom_cm=round(rom_m * 100.0, 1),
            tiempo_s=round(duracion, 2),
            t_excentrica_s=round(float(self._t_exc_pend), 2),
            t_amortiguacion_s=(round(float(self._t_amort_pend), 3)
                               if self._t_amort_pend is not None else None),
            desv_horizontal_cm=desv_cm,
            eficiencia_barra_pct=eficiencia,
            sticking_s=round(sticking_s, 2),
            t_inicio_s=round(float(self.t_inicio_rep), 3),
            t_fin_s=round(float(t_fin), 3),
        )
        self._t_exc_pend = 0.0
        self._t_amort_pend = None
        return rep

    # --------------------------------------------------------------
    def _calcular_sticking(self, ts: np.ndarray, vs: np.ndarray,
                           duracion: float, t_fin_empuje: float) -> float:
        """
        Episodio más largo con la barra "clavada" durante la subida (grind).
        Cuenta dos cosas como barra clavada:
          * muestras registradas con v < vel_sticking, y
          * HUECOS temporales entre muestras: en `vels_concentrica` solo entran
            frames con v > 0, así que cuando la barra se detiene de verdad
            (v ≈ 0 oscilando alrededor de cero) los frames desaparecen de la
            lista y el estancamiento se manifiesta como un gap en `ts`.
        Sin esto, un sticking point de 1.5 s sería invisible para el análisis.
        """
        # Acotar al empuje real: tras t_fin_empuje la barra ya está en
        # lockout y esa quietud NO es sticking point.
        m = ts <= (t_fin_empuje + 1e-6)
        ts, vs = ts[m], vs[m]
        if ts.size < 2:
            return 0.0
        dt_frame = 1.0 / max(self.fps, 1.0)
        umbral = self.cfg.vel_sticking
        mejor = 0.0
        actual = 0.0
        for i in range(1, ts.size):
            gap = ts[i] - ts[i - 1]
            lento = vs[i] < umbral
            hueco = gap > 2.0 * dt_frame
            if lento or hueco:
                actual += gap if hueco else dt_frame
                mejor = max(mejor, actual)
            else:
                actual = 0.0
        return float(min(mejor, duracion))

    # --------------------------------------------------------------
    def _metricas_trayectoria(self, t_fin_conc: float) -> tuple:
        """Bar path de la fase concéntrica: deriva lateral respecto al eje principal y eficiencia.
        Invariante a la inclinación de la cámara (phone tilt) al medir la distancia perpendicular
        a la recta secante entre el inicio y final del levantamiento."""
        pts = [p for p in self.path_rep
               if self.t_inicio_concentrica - 1e-6 <= p[0] <= t_fin_conc + 0.05]
        if len(pts) < 2:
            return 0.0, 100.0
        xs = np.array([p[1] for p in pts], dtype=float)
        ys = np.array([p[2] for p in pts], dtype=float)

        dx = xs[-1] - xs[0]
        dy = ys[-1] - ys[0]
        longitud_eje = math.hypot(dx, dy)

        if longitud_eje > 1e-4:
            # Distancia perpendicular de cada punto a la recta secante inicio->fin
            distancias_perp = np.abs((xs - xs[0]) * dy - (ys - ys[0]) * dx) / longitud_eje
            desv_px = float(np.max(distancias_perp))
        else:
            desv_px = float(np.max(np.abs(xs - xs[0])))

        camino = float(np.sum(np.hypot(np.diff(xs), np.diff(ys))))
        vertical = float(abs(ys[-1] - ys[0]))
        eficiencia = (float(np.clip(vertical / camino * 100.0, 0.0, 100.0))
                      if camino > 1e-6 else 100.0)
        return (round(desv_px * self.escala_fija_m_px * 100.0, 1),
                round(eficiencia, 1))

    # --------------------------------------------------------------
    def _calcular_mpv(self, ts: np.ndarray, vs: np.ndarray,
                      fallback: float) -> float:
        """
        Mean Propulsive Velocity: media de la velocidad únicamente durante la
        fase propulsiva, que termina cuando la desaceleración de la barra supera
        la gravedad (a < −9.81 m/s²), momento en que la barra "vuela" sola.
        """
        if vs.size < 4 or ts.size != vs.size:
            return fallback
        try:
            diffs = np.diff(ts)
            if np.any(diffs <= 1e-7):
                mask = np.insert(diffs > 1e-7, 0, True)
                ts = ts[mask]
                vs = vs[mask]
                if vs.size < 4:
                    return fallback
            acel = np.gradient(vs, ts)
        except Exception:
            return fallback

        # FIX v3.4: `vels_concentrica` solo contiene muestras con v > 0, así que
        # sus instantes NO son contiguos. `np.gradient` sobre esa serie salta los
        # huecos y fabrica aceleraciones enormes que disparan el corte a < −g en
        # sitios arbitrarios. Solo se acepta el corte cuando las dos muestras
        # implicadas son realmente consecutivas en el video.
        dt_frame = 1.0 / max(self.fps, 1.0)
        i_pico = int(np.argmax(vs))
        corte = vs.size
        for i in range(max(i_pico, 1), vs.size):
            if acel[i] < -GRAVEDAD and (ts[i] - ts[i - 1]) <= 1.5 * dt_frame:
                corte = i
                break

        propulsiva = vs[:max(corte, 1)]
        propulsiva = propulsiva[propulsiva > self.cfg.umbral_ruido_prop]
        return float(np.mean(propulsiva)) if propulsiva.size else fallback


# ==============================================================================
# MOTOR DE ANÁLISIS DETERMINISTA DE LA SERIE (v3)
# ------------------------------------------------------------------------------
# Todo lo que el usuario/entrenador necesita para autorregular se calcula AQUÍ,
# con los datos. El LLM y el mensaje de WhatsApp solo narran estos hallazgos:
# se acabaron las salidas genéricas.
# ==============================================================================
def analizar_serie(registro: list, perfil: PerfilEjercicio,
                   nivel_atleta: str = "Avanzado",
                   carga_kg: Optional[float] = None) -> dict:
    nivel = _normalizar_nivel(nivel_atleta)
    n = len(registro)
    if n == 0:
        return {}
    _recalcular_vl_por_rep(registro)

    v_avgs = [r.v_avg for r in registro]
    i_best = int(np.argmax(v_avgs))
    v_best, v_first, v_last = v_avgs[i_best], v_avgs[0], v_avgs[-1]

    vl = calcular_velocity_loss(registro)
    estimulo = _clasificar_estimulo(vl)

    v_min = float(min(v_avgs))
    res_1rm = estimar_e1rm_serie(carga_kg, v_first, v_min, n, perfil, nivel)
    pct1rm, e1rm = res_1rm["pct_1rm"], res_1rm["e1rm_kg"]
    mvt_ef, e1rm_origen = res_1rm["mvt_efectivo"], res_1rm["origen"]
    zona = estimar_zona_intensidad(pct1rm)

    # FIX v3.4 — CÍRCULO VICIOSO DEL MVT EFECTIVO.
    # `mvt_ef` se define como min(v_avg de la serie) cuando esa mínima baja del
    # MVT de tabla. Pero el RIR se evaluaba con `v_last` contra ese mismo
    # `mvt_ef`, y en una serie normal la última rep ES la más lenta: entonces
    # `v_last <= mvt_ef` y `estimar_rir_por_velocidad` devolvía 0.0 SIEMPRE.
    # El estimador se comparaba consigo mismo y declaraba fallo en toda serie
    # que rompiera la tabla. Para el RIR se usa el MVT de TABLA (referencia
    # externa e independiente de la serie); `mvt_ef` sigue anclando el e1RM,
    # donde sí es una cota superior legítima demostrada por el propio atleta.
    rir_vel = estimar_rir_por_velocidad(v_last, perfil, nivel)
    rir_vl = estimar_rir_por_vl(vl, n)
    reps_rest = estimar_reps_restantes(rir_vel, rir_vl)

    # ---- Técnica: consistencia de ROM ----
    roms = [r.rom_cm for r in registro]
    rom_medio = float(np.mean(roms))
    rom_cv = (float(np.std(roms) / rom_medio * 100.0)
              if (n >= 2 and rom_medio > 0) else None)
    i_rom_min = int(np.argmin(roms))

    # ---- Bar path ----
    desvs = [r.desv_horizontal_cm for r in registro]
    i_desv = int(np.argmax(desvs))
    desv_max = float(desvs[i_desv])
    efic_media = float(np.mean([r.eficiencia_barra_pct for r in registro]))

    # ---- Sticking point (barra clavada a media subida) ----
    stickings = [getattr(r, "sticking_s", 0.0) or 0.0 for r in registro]
    i_stick = int(np.argmax(stickings))
    sticking_max = float(stickings[i_stick])

    # ---- Tempo por fases ----
    t_exc = [r.t_excentrica_s for r in registro]
    amorts = [r.t_amortiguacion_s for r in registro
              if r.t_amortiguacion_s is not None]
    t_conc = [r.tiempo_s for r in registro]
    amort_delta = None
    if len(amorts) >= 2 and amorts[0] > 0.03:
        amort_delta = float(round((amorts[-1] - amorts[0]) / amorts[0] * 100.0, 0))

    # ---- Mayor caída entre reps consecutivas + patrón ----
    caida = None
    if n >= 2 and v_best > 0:
        drops = [(v_avgs[i] - v_avgs[i + 1]) / v_best * 100.0 for i in range(n - 1)]
        j = int(np.argmax(drops))
        patron = "progresiva"
        if n >= 3:
            previas = [d for d in drops[:-1] if d > 0]
            if previas and drops[-1] > 1.8 * float(np.mean(previas)) and drops[-1] > 5:
                patron = "abrupta al final (borde del límite real)"
        caida = {"entre": (registro[j].rep, registro[j + 1].rep),
                 "delta_pct": float(round(max(drops[j], 0.0), 1)),
                 "patron": patron}

    # ---- Reps de corte por umbral de VL (clave para autorregular) ----
    def _rep_corte(umbral: float) -> Optional[int]:
        for r in registro:
            if r.vl_pct >= umbral:
                return r.rep
        return None
    cortes = {"vl10": _rep_corte(10), "vl20": _rep_corte(20), "vl30": _rep_corte(30)}

    # ---- Cinemática articular (si hubo pose) ----
    rodillas = [r.angulo_rodilla_min for r in registro
                if r.angulo_rodilla_min is not None]
    pose = None
    if rodillas:
        pose = {"rodilla_min_media": float(round(float(np.mean(rodillas)), 1)),
                "rodilla_rango": float(round(max(rodillas) - min(rodillas), 1))}
        vang = [r.vel_angular_rodilla_max for r in registro
                if r.vel_angular_rodilla_max is not None]
        if len(vang) >= 2 and vang[0] > 0:
            pose["caida_vel_angular_pct"] = float(
                round((vang[0] - vang[-1]) / vang[0] * 100.0, 1))

    # =========================== INSIGHTS ===============================
    insights = []

    # 1) Intensidad real de la carga
    txt = f"1ª rep a {v_first:.2f} m/s"
    if pct1rm:
        txt += f" ≈ {pct1rm:.0f}% de tu 1RM"
    if e1rm and carga_kg:
        if e1rm_origen == "piso_reps":
            txt += (f" → completar {n} rep{'s' if n > 1 else ''} con "
                    f"{carga_kg:g} kg GARANTIZA un 1RM de al menos "
                    f"{e1rm:.1f} kg (piso por volumen, Epley)")
        else:
            txt += (f" → con {carga_kg:g} kg en barra, tu 1RM de HOY "
                    f"ronda {e1rm:.1f} kg")
    insights.append(txt + f". Zona: {zona}.")

    # 1b) Perfil de velocidad fuera de tabla (atleta que muele)
    if mvt_ef < res_1rm["mvt_tabla"] - 1e-6:
        insights.append(
            f"Completaste reps a {v_min:.2f} m/s, por debajo del MVT de tabla "
            f"({res_1rm['mvt_tabla']:.2f}): tu velocidad de fallo real es más "
            f"baja que el estándar de tu nivel. Los cálculos de hoy usan tu "
            f"MVT efectivo ({mvt_ef:.2f} m/s).")

    # 1c) Sticking point medido (el grind cuenta entero)
    if sticking_max >= 0.5:
        insights.append(
            f"En la rep {registro[i_stick].rep} la barra quedó clavada "
            f"{sticking_max:.1f} s a media subida (sticking point): la subida "
            f"completa duró {registro[i_stick].tiempo_s:.1f} s y se contabiliza "
            f"entera, sin recortar los frames lentos.")

    # 2) La rep más rápida no fue la 1ª
    if i_best != 0:
        insights.append(
            f"Tu rep más rápida fue la {registro[i_best].rep} ({v_best:.2f} m/s), "
            f"no la 1ª ({v_first:.2f}): saliste 'frío' de inicio; el Velocity Loss "
            f"se mide desde la rep {registro[i_best].rep}.")

    # 3) Fatiga acumulada
    if vl is not None:
        insights.append(f"Velocity Loss de {vl:.1f}% → {estimulo}.")

    # 4) Dónde se rompió la serie
    if caida and caida["delta_pct"] >= 3:
        insights.append(
            f"El mayor bajón fue entre la rep {caida['entre'][0]} y la "
            f"{caida['entre'][1]} (−{caida['delta_pct']:.1f}% sobre tu mejor); "
            f"patrón de caída: {caida['patron']}.")

    # 5) Reps que probablemente quedaban en el tanque
    if reps_rest:
        mvt = perfil.mvt[nivel]
        insights.append(
            f"Última rep a {v_last:.2f} m/s vs tu velocidad de fallo (~{mvt:.2f}): "
            f"quedaban {reps_rest['texto']} reps en reserva.")

    # 6) Consistencia técnica (ROM)
    if rom_cv is not None and rom_cv > CONFIG.rom_cv_alerta_pct:
        insights.append(
            f"ROM inconsistente (CV {rom_cv:.1f}%): la rep {registro[i_rom_min].rep} "
            f"se quedó en {roms[i_rom_min]:.0f} cm vs {rom_medio:.0f} cm de media — "
            f"vigila la profundidad cuando aparece la fatiga.")
    elif rom_cv is not None:
        insights.append(
            f"ROM estable ({rom_medio:.0f} cm, CV {rom_cv:.1f}%): técnica "
            f"estandarizada rep a rep, las velocidades son comparables.")

    # 7) Transición en el fondo (pérdida del rebote elástico)
    if (amort_delta is not None and amort_delta >= CONFIG.amort_alerta_crec_pct):
        insights.append(
            f"La pausa en el fondo creció de {int(amorts[0] * 1000)} a "
            f"{int(amorts[-1] * 1000)} ms (+{amort_delta:.0f}%): estás perdiendo "
            f"el rebote elástico, señal temprana de fatiga.")

    # 8) Grind temporal
    if n >= 2 and t_conc[0] > 0 and t_conc[-1] > t_conc[0] * 1.4:
        insights.append(
            f"La subida pasó de {t_conc[0]:.2f} s (rep 1) a {t_conc[-1]:.2f} s "
            f"(última): cada rep te costó más tiempo bajo la barra.")

    # 9) Bar path
    if desv_max >= CONFIG.umbral_desv_horizontal_cm:
        insights.append(
            f"Bar path: la rep {registro[i_desv].rep} derivó {desv_max:.1f} cm en "
            f"horizontal (eficiencia {registro[i_desv].eficiencia_barra_pct:.0f}%): "
            f"la barra se salió de la vertical.")

    # 10) Articular
    if pose and pose.get("rodilla_rango", 0) > 8:
        insights.append(
            f"Profundidad articular variable: el ángulo mínimo de rodilla osciló "
            f"{pose['rodilla_rango']:.0f}° entre repeticiones.")
    if pose and pose.get("caida_vel_angular_pct") and pose["caida_vel_angular_pct"] > 15:
        insights.append(
            f"La velocidad de extensión de rodilla cayó "
            f"{pose['caida_vel_angular_pct']:.0f}% de la 1ª a la última rep: la "
            f"fatiga también se ve en la articulación, no solo en la barra.")

    recomendacion = _construir_recomendacion(
        vl, reps_rest, carga_kg, v_first, perfil, nivel,
        cortes, rom_cv, desv_max, amort_delta, rir_vel,
        pct_1rm=pct1rm, e1rm=e1rm, sticking_max=sticking_max)

    return _json_safe({
        "reps": n,
        "v_mejor": round(v_best, 3), "rep_mas_rapida": registro[i_best].rep,
        "v_primera": round(v_first, 3), "v_ultima": round(v_last, 3),
        "vl_pct": vl, "estimulo": estimulo,
        "pct_1rm_estimado": pct1rm, "e1rm_kg": e1rm, "zona_intensidad": zona,
        "e1rm_origen": e1rm_origen, "mvt_efectivo": mvt_ef,
        "mvt_tabla": res_1rm["mvt_tabla"],
        "sticking_max_s": round(sticking_max, 2),
        "rep_sticking": (registro[i_stick].rep if sticking_max > 0 else None),
        "rir_velocidad": rir_vel, "rir_vl": rir_vl, "reps_restantes": reps_rest,
        "rom_medio_cm": round(rom_medio, 1),
        "rom_cv_pct": (round(rom_cv, 1) if rom_cv is not None else None),
        "desv_horizontal_max_cm": round(desv_max, 1),
        "eficiencia_barra_media_pct": round(efic_media, 1),
        "tempo": {
            "excentrica_media_s": round(float(np.mean(t_exc)), 2),
            "amortiguacion_media_ms": (int(float(np.mean(amorts)) * 1000)
                                       if amorts else None),
            "concentrica_media_s": round(float(np.mean(t_conc)), 2),
            "amort_delta_pct": amort_delta,
        },
        "caida_mayor": caida, "cortes_vl": cortes, "pose": pose,
        "insights": insights, "recomendacion": recomendacion,
    })


# ------------------------------------------------------------------------------
def _construir_recomendacion(vl, reps_rest, carga_kg, v_first, perfil, nivel,
                             cortes, rom_cv, desv_max, amort_delta,
                             rir_vel, pct_1rm=None, e1rm=None,
                             sticking_max=0.0) -> dict:
    """Orden concreta para la siguiente serie + guía por objetivo."""
    mvt = perfil.mvt[_normalizar_nivel(nivel)]
    v80 = perfil.zona_fuerza_vel[0]
    pendiente = (v80 - mvt) / 20.0                    # m/s por 1% de 1RM
    v_target = float(np.mean(perfil.zona_fuerza_vel))  # centro de la zona fuerza

    # Ajuste de carga con GUARDRAILS DUROS, anclado al e1RM ya reconciliado
    # (piso por reps incluido). ANTES se perseguía la velocidad-objetivo de la
    # zona de fuerza de la tabla: con un atleta cuyo perfil rompe la tabla eso
    # producía saltos absurdos ("baja 32.5 kg para fuerza pura" tras un triple
    # al 90%+). Reglas: bajada máx 7.5%/sesión, subida máx 10%/sesión; entre
    # 80 y 90% del 1RM no se sugiere cambio (ya es zona de fuerza); ≥90% la
    # única bajada que se propone es explícitamente PARA VOLUMEN.
    CAP_SUBIDA_PCT, CAP_BAJADA_PCT = 10.0, 7.5
    ajuste = None
    ajuste_datos = None
    if carga_kg and e1rm and pct_1rm:
        objetivo, motivo = None, None
        if pct_1rm >= 90:
            objetivo, motivo = e1rm * 0.875, "volumen"       # centro de 85-90%
        elif pct_1rm < 80:
            objetivo, motivo = e1rm * 0.85, "acercar_fuerza"
        if objetivo is not None:
            delta_pct = (objetivo - carga_kg) / carga_kg * 100.0
            delta_pct = max(min(delta_pct, CAP_SUBIDA_PCT), -CAP_BAJADA_PCT)
            kg = round(abs(carga_kg * delta_pct / 100.0) / 2.5) * 2.5
            if kg >= 2.5:
                nueva = carga_kg + kg if delta_pct > 0 else carga_kg - kg
                ajuste_datos = {"delta_pct": float(round(delta_pct, 1)),
                                "kg": float(kg), "carga_nueva": float(nueva),
                                "motivo": motivo}
                if motivo == "volumen":
                    ajuste = (f"Hoy trabajaste al ~{pct_1rm:.0f}% de tu 1RM: "
                              f"para acumular series de 3-5 con margen "
                              f"(85-90%), baja a ~{nueva:g} kg (−{kg:g} kg).")
                else:
                    ajuste = (f"Estás al ~{pct_1rm:.0f}% del 1RM: para entrenar "
                              f"fuerza (≥85%) sube a ~{nueva:g} kg (+{kg:g} kg).")
    elif not carga_kg and pendiente > 0 and v_first and v_first > 0:
        # Sin kg solo cabe una orientación en %, con los mismos topes.
        delta_pct = (v_first - v_target) / pendiente
        delta_pct = max(min(delta_pct, CAP_SUBIDA_PCT), -CAP_BAJADA_PCT)
        if abs(delta_pct) >= 3:
            verbo = "Sube" if delta_pct > 0 else "Baja"
            ajuste = (f"{verbo} ~{abs(delta_pct):.0f}% la carga para la zona de "
                      f"fuerza. Mándame los kg y te lo doy en kilos exactos.")
            ajuste_datos = {"delta_pct": float(round(delta_pct, 1))}

    # Titular según fatiga real
    rest_txt = reps_rest["texto"] if reps_rest else "varias"
    if vl is None:
        if rir_vel is not None and rir_vel <= 1:
            titular = ("Single al límite: no acumules más intentos máximos hoy; "
                       "cierra con trabajo técnico al 80-85%.")
        else:
            titular = ("Single con margen: hay espacio para subir en el próximo "
                       "intento o pasar al trabajo de volumen.")
    elif vl < 10:
        titular = (f"Serie muy fresca (VL {vl:.0f}%, {rest_txt} reps en reserva): "
                   f"si el objetivo es fuerza, añade carga o repeticiones; si era "
                   f"aproximación o trabajo de potencia, quedó perfecta.")
    elif vl < 20:
        c20 = cortes.get("vl20")
        cola = (f"hoy el punto de corte del 20% llegó en la rep {c20}."
                if c20 else "no llegaste al corte del 20%: cabe 1 rep más por serie.")
        titular = (f"Zona óptima de fuerza (VL {vl:.0f}%): repite esta carga y "
                   f"este esfuerzo; {cola}")
    elif vl < 30:
        c20 = cortes.get("vl20")
        cola = (f"si el bloque es de fuerza, corta en la rep {c20} (VL 20%)."
                if c20 else "para fuerza, corta ~2 reps antes.")
        titular = (f"Estímulo de hipertrofia conseguido (VL {vl:.0f}%); {cola}")
    else:
        titular = (f"VL {vl:.0f}%: serie al borde del fallo, costo de "
                   f"recuperación alto. Baja 5-10% la carga en la siguiente o "
                   f"alarga el descanso a 4-5 min.")

    # Prioridad técnica (una sola, la más relevante)
    tecnica = None
    if desv_max >= CONFIG.umbral_desv_horizontal_cm:
        tecnica = (f"Prioridad técnica: controla la deriva horizontal de la "
                   f"barra ({desv_max:.1f} cm).")
    elif rom_cv is not None and rom_cv > CONFIG.rom_cv_alerta_pct:
        tecnica = (f"Prioridad técnica: iguala la profundidad en todas las reps "
                   f"(el ROM varió {rom_cv:.0f}%).")
    elif sticking_max >= 0.8:
        tecnica = (f"Prioridad técnica: la barra se te clava ~{sticking_max:.1f} s "
                   f"a media subida; mete trabajo del punto de estancamiento "
                   f"(pausas, pines o velocidad en ese rango).")
    elif amort_delta is not None and amort_delta >= CONFIG.amort_alerta_crec_pct:
        tecnica = ("Prioridad técnica: acorta la transición en el fondo, estás "
                   "perdiendo el rebote elástico.")

    # Guía por objetivo, anclada a lo que pasó HOY
    c10, c20 = cortes.get("vl10"), cortes.get("vl20")
    fuerza = "Corta cada serie al perder ~20% de velocidad"
    fuerza += (f" (hoy: rep {c20})." if c20
               else " (hoy no lo alcanzaste: puedes añadir reps o carga).")
    if vl is not None:
        hiper = f"Busca series con 20-30% de VL; hoy cerraste en {vl:.0f}%."
    else:
        hiper = "Busca series con 20-30% de VL (con singles no aplica)."
    potencia = "Corta al 10% de VL"
    if c10:
        potencia += f" (hoy: rep {c10})."
    elif vl is not None and vl < 10:
        potencia += "; hoy toda la serie fue válida para potencia."
    else:
        potencia += "."

    return {"titular": titular, "ajuste_carga": ajuste,
            "ajuste_datos": ajuste_datos, "tecnica": tecnica,
            "por_objetivo": {"fuerza": fuerza, "hipertrofia": hiper,
                             "potencia": potencia}}


# ==============================================================================
# REPORTES: técnico (denso, para logs/entrenador) y WhatsApp (lenguaje llano)
# ==============================================================================
def _lineas_por_rep(registro: list) -> list:
    lineas = []
    for r in registro:
        extra = (f" · trans {int(r.t_amortiguacion_s * 1000)}ms"
                 if r.t_amortiguacion_s is not None else "")
        if getattr(r, "sticking_s", 0.0) >= 0.3:
            extra += f" · clavada {r.sticking_s:.1f}s"
        lineas.append(
            f"R{r.rep}: {r.v_avg:.2f} m/s (pico {r.v_peak:.2f}) · "
            f"ROM {r.rom_cm:.0f}cm · sube {r.tiempo_s:.2f}s · "
            f"baja {r.t_excentrica_s:.2f}s{extra} · VL {r.vl_pct:.0f}%")
    return lineas


def _construir_reporte_tecnico(ejercicio: str, registro: list, analisis: dict,
                               carga_kg: Optional[float] = None) -> str:
    """Versión densa con todas las métricas. NO se envía al atleta:
    queda en el dict de retorno (`reporte_tecnico`) para dashboards,
    logs o el entrenador."""
    a = analisis
    L = [f"*IRON PROGRESS · VBT (TÉCNICO) — {ejercicio.upper()}*", ""]

    # --- Resumen ---
    L.append("*RESUMEN*")
    carga_txt = f"{carga_kg:g} kg" if carga_kg else "peso por calibrar (envíame los kg)"
    linea = f"Reps: *{a['reps']}* · Carga: {carga_txt}"
    if a.get("pct_1rm_estimado"):
        linea += f" (~{a['pct_1rm_estimado']:.0f}% 1RM)"
    L.append(linea)
    if a.get("e1rm_kg"):
        L.append(f"1RM estimado de hoy: *{a['e1rm_kg']:.1f} kg*")
    if a.get("vl_pct") is not None:
        L.append(f"Fatiga (VL): *{a['vl_pct']:.1f}%* → {a['estimulo']}")
    if a.get("reps_restantes"):
        L.append(f"Reps que quedaban en reserva: *{a['reps_restantes']['texto']}*")

    # --- Desglose por rep ---
    L += ["", "*POR REPETICIÓN*"]
    lineas_reps = _lineas_por_rep(registro)
    L += lineas_reps

    # --- Lectura de la serie ---
    L += ["", "*LECTURA DE LA SERIE*"]
    L += [f"• {i}" for i in a["insights"][:6]]

    # --- Técnica y tempo ---
    t = a["tempo"]
    amort_txt = (f"{t['amortiguacion_media_ms']} ms"
                 if t["amortiguacion_media_ms"] is not None else "—")
    rom_txt = f"ROM medio {a['rom_medio_cm']:.0f} cm"
    if a["rom_cv_pct"] is not None:
        rom_txt += f" (CV {a['rom_cv_pct']:.1f}%)"
    L += ["", "*TÉCNICA Y TEMPO*",
          rom_txt + (f" · Bar path: desv. máx {a['desv_horizontal_max_cm']:.1f} cm, "
                     f"eficiencia {a['eficiencia_barra_media_pct']:.0f}%"),
          (f"Tempo medio: bajada {t['excentrica_media_s']:.2f}s · "
           f"transición {amort_txt} · subida {t['concentrica_media_s']:.2f}s")]

    # --- Siguiente serie ---
    rec = a["recomendacion"]
    L += ["", "*SIGUIENTE SERIE*", rec["titular"]]
    if rec.get("ajuste_carga"):
        L.append(rec["ajuste_carga"])
    if rec.get("tecnica"):
        L.append(rec["tecnica"])
    obj = rec["por_objetivo"]
    L.append(f"Según tu objetivo → Fuerza: {obj['fuerza']} "
             f"Hipertrofia: {obj['hipertrofia']} Potencia: {obj['potencia']}")

    msg = "\n".join(L)

    # WhatsApp corta ~4096 chars: compactar el desglose si hace falta
    if len(msg) > 3800 and len(registro) > 6:
        idx = L.index("*POR REPETICIÓN*")
        compacto = (lineas_reps[:3]
                    + [f"… ({len(registro) - 5} reps intermedias omitidas) …"]
                    + lineas_reps[-2:])
        L = L[:idx + 1] + compacto + L[idx + 1 + len(lineas_reps):]
        msg = "\n".join(L)
    return msg




def _semaforo_vl(vl_pct: float) -> str:
    if vl_pct < 10:
        return "\U0001F7E2"   # verde
    if vl_pct < 20:
        return "\U0001F7E1"   # amarillo
    if vl_pct < 30:
        return "\U0001F7E0"   # naranja
    return "\U0001F534"       # rojo


def _construir_reporte_whatsapp(ejercicio: str, registro: list, analisis: dict,
                                carga_kg: Optional[float] = None) -> str:
    """Reporte de WhatsApp para ATLETA y COACH (Usuario Normal y Técnico):
    Combina la síntesis clara de gimnasio con telemetría VBT estructurada,
    legible y digerible para ambos perfiles."""
    a = analisis
    n = a["reps"]
    vl = a.get("vl_pct")
    rest = a.get("reps_restantes") or {}

    rir_val = a.get("rir_velocidad")
    if rir_val is None and rest:
        try:
            rir_val = float(rest.get("min", 0))
        except (ValueError, TypeError):
            rir_val = None


    rpe_val = round(max(10.0 - rir_val, 5.0), 1) if rir_val is not None else None
    rpe_txt = f"{rpe_val:g}" if rpe_val is not None else "N/D"
    rir_txt = f"~{rir_val:.0f}" if rir_val is not None else (rest.get("texto", "N/D"))

    carga_linea = f"{carga_kg:g} kg" if carga_kg else "Peso por calibrar"
    resumen_serie = f"{carga_linea} × {n} rep{'s' if n != 1 else ''}  ·  RPE {rpe_txt} (RIR {rir_txt})"

    L = [
        f"🏋️ *{ejercicio.upper()}*",
        resumen_serie,
        ""
    ]

    # ---------- RESUMEN DE LA SERIE (legible para cualquier usuario) ----------
    L.append("*Resumen de la serie*")
    if vl is None:
        rir_v = a.get("rir_velocidad")
        if rir_v is not None and rir_v <= 1:
            lectura = "fue un intento casi máximo: poquísimo margen de fallo"
        elif rir_v is not None and rir_v <= 3:
            lectura = "pesó considerablemente, pero mantuviste margen"
        else:
            lectura = "salió fluida y explosiva, con bastante margen"
        L.append(f"Repetición única a {a['v_mejor']:.2f} m/s: {lectura}.")
    elif vl < 10:
        L.append(f"Serie muy fresca: apenas perdiste velocidad ({vl:.0f}% VL). Terminaste con mucha reserva.")
    elif vl < 20:
        L.append(f"Serie sólida y controlada ({vl:.0f}% VL): fatiga óptima para estimular fuerza sin sobrecargar el SNC.")
    elif vl < 30:
        L.append(f"Serie exigente ({vl:.0f}% VL): alto estímulo de hipertrofia con fatiga acumulada notable.")
    else:
        L.append(f"Serie al límite ({vl:.0f}% VL): cercana al fallo muscular. Requiere 48-72 h de recuperación.")

    # ---------- DETALLE POR REPETICIÓN (semáforo de velocidad) ----------
    L += ["", "*Detalle por repetición*"]
    i_best = a.get("rep_mas_rapida")
    lineas_reps = []
    for r in registro:
        pico_txt = f" (pico {r.v_peak:.2f} m/s)" if hasattr(r, "v_peak") and r.v_peak else ""
        if len(registro) == 1:
            lineas_reps.append(f"R{r.rep}: {r.v_avg:.2f} m/s{pico_txt}")
        elif r.rep == i_best:
            lineas_reps.append(f"R{r.rep} {_semaforo_vl(0)} {r.v_avg:.2f} m/s{pico_txt} — mejor rep")
        else:
            lineas_reps.append(f"R{r.rep} {_semaforo_vl(r.vl_pct)} {r.v_avg:.2f} m/s{pico_txt} (−{r.vl_pct:.0f}%)")
    L += lineas_reps

    # ---------- ANÁLISIS VBT Y TÉCNICO (para perfil técnico/coach) ----------
    frases = []
    if a.get("e1rm_kg") and carga_kg:
        if a.get("e1rm_origen") == "piso_reps":
            frases.append(f"*1RM de hoy:* al menos *{a['e1rm_kg']:.0f} kg* (garantizado por volumen a ~{a['pct_1rm_estimado']:.0f}% 1RM).")
        else:
            frases.append(f"*1RM estimado:* *{a['e1rm_kg']:.0f} kg* (~{a['pct_1rm_estimado']:.0f}% de intensidad relativa).")

    if rest:
        if rest.get("max", 9) <= 1:
            frases.append("*Reserva:* Terminaste al límite (≤1 rep en reserva).")
        else:
            frases.append(f"*Reserva:* Quedaban *{rest['texto']} reps* en el tanque.")

    if a.get("sticking_max_s", 0) >= 0.8 and a.get("rep_sticking"):
        frases.append(f"*Sticking point:* Clavado *{a['sticking_max_s']:.1f} s* a media subida en R{a['rep_sticking']}.")

    caida = a.get("caida_mayor") or {}
    if str(caida.get("patron", "")).startswith("abrupta"):
        ra, rb = caida["entre"]
        frases.append(f"*Degradación:* Bajón brusco entre R{ra} y R{rb} (−{caida['delta_pct']:.0f}%).")

    amort_delta = (a.get("tempo") or {}).get("amort_delta_pct")
    if amort_delta is not None and amort_delta >= CONFIG.amort_alerta_crec_pct:
        amorts = [r.t_amortiguacion_s for r in registro if r.t_amortiguacion_s is not None]
        if len(amorts) >= 2:
            frases.append(f"*Pausa inferior:* Se alargó ({amorts[0]:.1f}s → {amorts[-1]:.1f}s), perdiendo elástico.")

    if a.get("desv_horizontal_max_cm", 0) >= CONFIG.umbral_desv_horizontal_cm:
        frases.append(f"*Bar path:* Desviación de *{a['desv_horizontal_max_cm']:.0f} cm* de la vertical.")
    elif a.get("rom_cv_pct") is not None and a["rom_cv_pct"] > CONFIG.rom_cv_alerta_pct:
        frases.append(f"*Consistencia ROM:* Profundidad variable ({a['rom_cv_pct']:.0f}% CV).")
    elif n > 1:
        frases.append("*Técnica:* Trayectoria vertical limpia y ROM consistente.")

    if frases:
        L += ["", "*Análisis VBT y técnico*"] + [f"• {f}" for f in frases[:5]]

    # ---------- INDICACIONES PARA LA SIGUIENTE SERIE ----------
    acciones = []
    cortes = a.get("cortes_vl") or {}
    c10, c20 = cortes.get("vl10"), cortes.get("vl20")
    if vl is None:
        rir_v = a.get("rir_velocidad")
        if rir_v is not None and rir_v <= 1:
            acciones.append("Intento máximo alcanzado: concluye con series secundarias más ligeras.")
        else:
            acciones.append("Margen disponible: puedes incrementar 2.5-5 kg en la siguiente.")
    elif vl >= 30:
        acciones.append("Fatiga alta acumulada: reduce carga 5-10% o descansa 4-5 min.")
    elif vl >= 20:
        cola = f"corta en rep {c20}" if c20 else "corta 1-2 reps antes"
        acciones.append(f"Excelente estímulo para fuerza/hipertrofia. Para enfoque de fuerza pura: {cola}.")
    elif vl >= 10:
        acciones.append("Carga e intensidad en punto óptimo: mantén para la siguiente serie.")
    else:
        acciones.append("Carga ligera para tu potencial: incrementa peso o repeticiones.")

    ad = (a.get("recomendacion") or {}).get("ajuste_datos") or {}
    if ad.get("carga_nueva"):
        if ad.get("motivo") == "volumen":
            acciones.append(f"Para series de trabajo acumulativo (3-5 reps), ajusta a *{ad['carga_nueva']:g} kg* (−{ad['kg']:g} kg).")
        elif vl is None or vl < 30:
            acciones.append(f"Para zona de fuerza principal (≥85%), prueba *{ad['carga_nueva']:g} kg* (+{ad['kg']:g} kg).")

    if n > 1:
        p10 = f" (rep {c10})" if c10 else ""
        p20 = f" (rep {c20})" if c20 else ""
        acciones.append(f"Corte por velocidad: VL 10% potencia{p10} | VL 20% fuerza{p20} | VL 30% hipertrofia.")

    L += ["", "*Recomendación para la próxima serie*"]
    L += [f"• {t}" for t in acciones[:3]]

    if not carga_kg:
        L.append("Nota: indica los kg utilizados para calcular tu 1RM diario y cargas exactas.")

    L += ["", "_¿Necesitas ajustar la rutina o tienes dudas sobre la técnica? Escríbeme._"]
    msg = "\n".join(L)

    # WhatsApp corta ~4096 chars: compactar el desglose si hace falta
    if len(msg) > 3800 and len(registro) > 6:
        idx = L.index("*Detalle por repetición*")
        compacto = (lineas_reps[:3]
                    + [f"… ({len(registro) - 5} reps intermedias) …"]
                    + lineas_reps[-2:])
        L = L[:idx + 1] + compacto + L[idx + 1 + len(lineas_reps):]
        msg = "\n".join(L)
    return msg


# ==============================================================================
# DIAGNÓSTICO (autopsia cuando no se detectan reps)
# ==============================================================================
def _generar_autopsia_vbt(totales: int, con_disco: int, fase: str,
                          max_descenso: float, max_vel: float) -> str:
    cobertura = (100.0 * con_disco / totales) if totales else 0.0
    consejo = ""

    if totales == 0:
        razon = "El video está corrupto o tiene 0 frames leíbles."
    elif con_disco == 0:
        razon = "YOLO no detectó el disco en ningún momento."
        consejo = ("Graba de lado, con el disco COMPLETO dentro del encuadre "
                   "y sin que el cuerpo lo tape.")
    elif cobertura < 40.0:
        # Antes esto caía en "movimiento insuficiente" y el atleta entendía que
        # no había bajado lo suficiente, cuando el problema real es que el disco
        # apenas se ve. Con tan pocos frames NINGUNA métrica es fiable.
        razon = (f"El disco solo fue visible en el {cobertura:.0f}% del video "
                 f"({con_disco} de {totales} frames): no hay señal suficiente "
                 f"para medir la barra.")
        consejo = ("Reencuadra: el disco debe verse completo durante toda la "
                   "serie. Cámara de lado, a la altura de la barra, y que no "
                   "quede fuera de plano al subir.")
    elif fase in ("calibrando", "reposo"):
        razon = f"Movimiento insuficiente: descenso máximo de {max_descenso:.1f}px."
    elif fase == "excentrica":
        razon = f"La barra bajó pero no subió. Vel máx: {max_vel:.3f} m/s."
    elif fase == "concentrica":
        razon = "La barra subió, pero la repetición no cerró correctamente."
    else:
        razon = f"Fase final atípica: {fase}."

    texto = (f"Fallo VBT.\nCAUSA: {razon}\n")
    if consejo:
        texto += f"CÓMO ARREGLARLO: {consejo}\n"
    return (texto + f"TELEMETRÍA -> Frames: {totales} | Tracked: {con_disco} "
            f"({cobertura:.0f}%) | Descenso Max: {max_descenso:.1f}px")


# ==============================================================================
# CONTEXTO PARA EL LLM (anclado al análisis determinista del motor)
# ==============================================================================
def _generar_contexto_llm(reps_completadas: int, velocidades_globales: list,
                          registro: list, ejercicio: str,
                          nivel_atleta: str = "Avanzado",
                          numero_usuario: str = None,
                          analisis: Optional[dict] = None,
                          carga_kg: Optional[float] = None) -> str:
    """
    Construye el súper-prompt para el LLM con formato atrapable (kg + reps + RPE + RIR).
    """
    perfil = obtener_perfil_ejercicio(ejercicio)
    if analisis is None:
        analisis = analizar_serie(registro, perfil, nivel_atleta, carga_kg)
    a = analisis

    es_single = reps_completadas == 1
    vl_pct = a.get("vl_pct")
    fatiga_str = (f"{vl_pct:.1f}%" if vl_pct is not None
                  else ("N/A (Single: no hay degradación intra-serie)"
                        if es_single else "0.0%"))

    detalle_reps_str = ""
    for r in registro:
        trans = (f"{int(r.t_amortiguacion_s * 1000)} ms"
                 if r.t_amortiguacion_s is not None else "N/A")
        detalle_reps_str += (
            f"*Rep {r.rep}* -> Media: *{r.v_avg:.3f} m/s* | "
            f"Pico: _{r.v_peak:.3f} m/s_ | MPV: {r.mpv:.3f} m/s | "
            f"ROM: {r.rom_cm:.1f} cm | Conc: {r.tiempo_s:.2f}s | "
            f"Exc: {r.t_excentrica_s:.2f}s | Trans: {trans} | "
            f"DesvX: {r.desv_horizontal_cm:.1f} cm | VL: {r.vl_pct:.1f}%\n")

    rir_val = a.get("rir_velocidad")
    if rir_val is None and a.get("reps_restantes"):
        try:
            rir_val = float(a["reps_restantes"].get("min", 0))
        except (ValueError, TypeError):
            rir_val = None

    rpe_val = round(max(10.0 - rir_val, 5.0), 1) if rir_val is not None else None
    rpe_txt = f"{rpe_val:g}" if rpe_val is not None else "N/D"
    reps_rest_txt = (a["reps_restantes"]["texto"]
                     if a.get("reps_restantes") else "N/D")
    rir_txt = f"~{rir_val:.0f}" if rir_val is not None else reps_rest_txt
    e1rm_txt = (f"{a['e1rm_kg']:.1f} kg" if a.get("e1rm_kg")
                else "peso por calibrar (pídele los kg al atleta)")
    carga_txt = f"{carga_kg:g} kg" if carga_kg else "no informada"
    carga_linea = f"{carga_kg:g} kg" if carga_kg else "Peso por calibrar"

    v_prom = round(float(np.mean([r.v_avg for r in registro])), 3) if registro else 0.0
    resumen_serie_estandar = f"{carga_linea} x {reps_completadas} reps @ RPE {rpe_txt} (RIR {rir_txt})"

    hallazgos = "\n".join(f"- {i}" for i in a.get("insights", []))
    rec = a.get("recomendacion", {}) or {}
    rec_txt = " ".join(filter(None, [rec.get("titular"),
                                     rec.get("ajuste_carga"),
                                     rec.get("tecnica")]))
    obj = rec.get("por_objetivo", {})
    t = a.get("tempo", {})
    amort_txt = (f"{t.get('amortiguacion_media_ms')} ms"
                 if t.get("amortiguacion_media_ms") is not None else "N/A")

    contexto_final = f"""Eres el motor analítico de Iron Progress, un coach experto especializado en Velocity Based Training (VBT). Tu función es analizar la cinemática de la serie con máxima rigurosidad científica internamente, pero EXPRESAR tus conclusiones en un lenguaje de gimnasio motivador, directo y muy fácil de entender para el levantador.

[DATOS ESTRUCTURADOS DE LA SERIE PARA GUARDAR EN HISTORIAL]
- Serie Estándar (Formato Atrapable): {resumen_serie_estandar}
- Ejercicio: {ejercicio.upper()} (perfil VBT aplicado: {perfil.nombre})
- Carga en barra: {carga_txt}
- Repeticiones Completadas: {reps_completadas}
- RPE Estimado: {rpe_txt}
- RIR Estimado: {rir_txt}
- e1RM Estimado: {e1rm_txt}
- Pérdida de Velocidad (VL %, vs la rep más rápida): {fatiga_str}
- Velocidad Media Concéntrica (VCM): {v_prom:.3f} m/s

[TELEMETRÍA DETALLADA DE LA SERIE]
- Perfil del Atleta: {nivel_atleta} (Alta eficiencia neural y capacidad de "grind")

[DESGLOSE CINEMÁTICO POR REPETICIÓN]
{detalle_reps_str}
[ANÁLISIS DETERMINISTA DEL MOTOR — TU ÚNICA FUENTE DE VERDAD]
Estos hallazgos YA fueron calculados con los datos. Tu trabajo es narrarlos y traducirlos, NO recalcularlos ni contradecirlos:
{hallazgos}
- Reps en reserva estimadas: {reps_rest_txt} | RIR/RPE: {rir_txt} (RPE {rpe_txt})
- 1RM estimado de hoy: {e1rm_txt} | Zona de carga: {a.get('zona_intensidad', 'N/D')} | Origen del e1RM: {a.get('e1rm_origen') or 'N/D'} (piso_reps = mínimo garantizado por completar las reps; NUNCA digas que la carga superó el 100%)
- MVT efectivo usado hoy: {a.get('mvt_efectivo', 'N/D')} m/s (tabla del nivel: {a.get('mvt_tabla', 'N/D')}) | Barra clavada máx: {a.get('sticking_max_s', 0)} s (rep {a.get('rep_sticking') or '—'})
- Tempo medio: excéntrica {t.get('excentrica_media_s', 'N/A')}s · transición {amort_txt} · concéntrica {t.get('concentrica_media_s', 'N/A')}s
- Recomendación del motor para la siguiente serie: {rec_txt}
- Guía por objetivo -> Fuerza: {obj.get('fuerza', 'N/D')} | Hipertrofia: {obj.get('hipertrofia', 'N/D')} | Potencia: {obj.get('potencia', 'N/D')}
- El atleta YA recibió por WhatsApp un reporte en lenguaje simple (sin jerga). Si te pide más detalle (tempos por rep, recorrido, trayectoria de la barra, %s exactos), respóndelo desde ESTE análisis, en el mismo tono llano, sin recalcular nada.

[BASE DE CONOCIMIENTO 1: UMBRALES MVT (RIR 0 / RPE 10) POR NIVEL]
El MVT define la velocidad de fallo. Los atletas avanzados/élite tienen un MVT mucho más bajo (mayor fricción isométrica) que los novatos:
- Sentadilla (Squat): Novato ~0.40 m/s | Avanzado ~0.25 m/s | Élite 0.18-0.24 m/s.
- Peso Muerto (Deadlift): Novato ~0.35 m/s | Avanzado ~0.15 m/s | Élite <0.15 m/s.
- Press de Banca (Bench): Novato ~0.30 m/s | Avanzado 0.12-0.15 m/s | Élite 0.10-0.12 m/s.
- Dominadas (Pull-Ups): Novato ~0.40 m/s | Avanzado ~0.23 m/s | Élite 0.20-0.26 m/s.

[BASE DE CONOCIMIENTO 2: ZONAS DE INTENSIDAD ABSOLUTA (80% - 100% 1RM)]
Para estimar la carga sobre el 1RM basado en la velocidad de la primera repetición:
- Sentadilla: Fuerza-Vel (80-89%) = 0.60 - 0.40 m/s | Max Fuerza (90-100%) = 0.39 - MVT
- Peso Muerto: Fuerza-Vel (80-89%) = 0.58 - 0.46 m/s | Max Fuerza (90-100%) = 0.45 - MVT
- Press Banca: Fuerza-Vel (80-89%) = 0.42 - 0.26 m/s | Max Fuerza (90-100%) = 0.25 - MVT
- Dominadas: Fuerza-Vel (80-89%) = 0.59 - 0.44 m/s | Max Fuerza (90-100%) = 0.43 - MVT

[BASE DE CONOCIMIENTO 3: ESPECTRO DE ADAPTACIÓN Y FATIGA (VELOCITY LOSS - VL)]
Si la serie tiene múltiples repeticiones, el VL (%) dicta el fenotipo celular:
- 10% VL (4-6 RIR): Neurogénica Pura. Estrés lactácido nulo. Mantiene fibras rápidas IIX, aumenta Salto Vertical (CMJ) y potencia pico.
- 20% VL (2-3 RIR): Fuerza Máxima Híbrida. Optimiza eficiencia neural con bajo daño tisular.
- 30% VL (1-2 RIR): Miogénica Estructural. Umbral primario para hipertrofia miofibrilar. Alta fatiga y amoníaco.
- 40%+ VL (0 RIR/Fallo en tren inferior): Miogénica Extrema. Conversión a fibras IIA. Agotamiento masivo del SNC. Requiere >72h de recuperación.
*Nota: En Press de Banca y Dominadas, el fallo absoluto ocurre cerca del 50-60% VL.*

[INSTRUCCIONES DE ANÁLISIS EXCLUSIVAS Y ESTILO DE RESPUESTA]
0. REGISTRO DE SERIE: El bloque [DATOS ESTRUCTURADOS DE LA SERIE PARA GUARDAR EN HISTORIAL] contiene el formato exacto '{resumen_serie_estandar}' (kg + cantidad de reps + RPE + RIR) necesario para invocar la herramienta registrar_serie_completada o guardar la serie en el historial si la conversación lo amerita.
1. INTENSIDAD DE LA CARGA: ¿Qué tan pesado estuvo? Menciona el %1RM estimado y, si existe, el 1RM de hoy en kg. Si la carga no fue informada, escribe exactamente 'peso por calibrar' y pídele los kg al atleta.
2. FATIGA Y ESFUERZO: ¿Qué tan cerca del fallo se quedó? Traduce el VL% y di EXPLÍCITAMENTE cuántas reps le quedaban en el tanque ({reps_rest_txt}) y el RPE/RIR. Si el RPE es 9.5-10 o el VL > 35%, reconoce el agotamiento total de la serie; PROHIBIDO decir 'sin agotarte del todo'.
3. OBJETIVO LOGRADO: Traduce el tipo de adaptación conseguida con esta serie a palabras simples.
4. QUÉ HACER AHORA: Da la orden directa del motor para la siguiente serie (carga, corte de reps y prioridad técnica si la hay). Si sugieres bajar la carga, el peso sugerido DEBE ser numéricamente menor al levantado hoy ({carga_txt}).
5. VARIABILIDAD: Varía tu redacción respecto a turnos anteriores; prohibido repetir frases cliché o plantillas idénticas.

Usa ÚNICAMENTE los 1RMs y pesos que el sistema te proporciona en los bloques anteriores. Si un peso no aparece explícitamente en los datos del sistema, escribe exactamente 'peso por calibrar' y pregúntale al atleta.
"""
    return contexto_final


# ==============================================================================
# SPORT HUB HUD · Renderizado profesional sobre video (OpenCV)
# ------------------------------------------------------------------------------
# (Se conserva íntegro el sistema de HUD de v2; en v3 solo se añade la fila
#  "EN RESERVA" al panel en vivo y la demo se movió al final del archivo.)
# ==============================================================================

# ── Paleta centralizada (BGR) ─────────────────────────────────────────────────
_HUB = {
    "bg":      (28, 28, 28),     # backgroundDark (#1C1C1C)
    "bg_soft": (26, 26, 26),     # cardBackground (#1A1A1A)
    "border":  (47, 47, 47),     # darkGray (#2F2F2F)
    "sep":     (47, 47, 47),     # darkGray (#2F2F2F)
    "accent":  (53, 57, 229),    # brandRed (#E53935)
    "green":   (134, 135, 1),    # teal (#018786)
    "amber":   (40, 40, 198),    # darkRed (#C62828)
    "red":     (70, 57, 230),    # red (#E63946)
    "white":   (255, 255, 255),  # white (#FFFFFF)
    "muted":   (187, 187, 187),  # hintColor (#BBBBBB)
    "shadow":  (0, 0, 0),        # black (#000000)
}

_FASES = {
    "concentrica": (_HUB["green"], "CONCENTRICA", 1),   # 1 = flecha arriba
    "excentrica":  (_HUB["amber"], "EXCENTRICA", -1),   # -1 = flecha abajo
    "eccentrica":  (_HUB["amber"], "EXCENTRICA", -1),
    "reposo":      (_HUB["muted"], "REPOSO", 0),
    "finalizado":  (_HUB["accent"], "SERIE FINALIZADA", 0),
}

_FONT = cv2.FONT_HERSHEY_DUPLEX
_FONT_UI = cv2.FONT_HERSHEY_SIMPLEX


# ==============================================================================
# UTILIDADES BASE
# ==============================================================================

def _s(frame, v: float) -> int:
    """Escala un valor de diseño (pensado para 1080p) a la resolución real."""
    return max(1, int(round(v * frame.shape[0] / 1080.0)))


def _color_por_velocidad(v: float) -> tuple:
    """Verde = explosivo, ámbar = medio, rojo = grinding.
    Interpola suavemente entre los tres para evitar saltos bruscos."""
    def lerp(c1, c2, t):
        return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

    if v >= 0.75:
        return _HUB["green"]
    if v >= 0.45:
        t = (v - 0.45) / 0.30
        return lerp(_HUB["amber"], _HUB["green"], t)
    t = max(0.0, v / 0.45)
    return lerp(_HUB["red"], _HUB["amber"], t)


def _rounded_rect(img, x1, y1, x2, y2, radius, color, thickness=-1):
    """Rectángulo con esquinas redondeadas, relleno o sólo borde."""
    r = int(max(0, min(radius, (x2 - x1) // 2, (y2 - y1) // 2)))
    if r == 0:
        cv2.rectangle(img, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)
        return
    if thickness < 0:
        cv2.rectangle(img, (x1 + r, y1), (x2 - r, y2), color, -1, cv2.LINE_AA)
        cv2.rectangle(img, (x1, y1 + r), (x2, y2 - r), color, -1, cv2.LINE_AA)
        for cx, cy in ((x1 + r, y1 + r), (x2 - r, y1 + r),
                       (x1 + r, y2 - r), (x2 - r, y2 - r)):
            cv2.circle(img, (cx, cy), r, color, -1, cv2.LINE_AA)
    else:
        cv2.ellipse(img, (x1 + r, y1 + r), (r, r), 180, 0, 90, color, thickness, cv2.LINE_AA)
        cv2.ellipse(img, (x2 - r, y1 + r), (r, r), 270, 0, 90, color, thickness, cv2.LINE_AA)
        cv2.ellipse(img, (x1 + r, y2 - r), (r, r), 90, 0, 90, color, thickness, cv2.LINE_AA)
        cv2.ellipse(img, (x2 - r, y2 - r), (r, r), 0, 0, 90, color, thickness, cv2.LINE_AA)
        cv2.line(img, (x1 + r, y1), (x2 - r, y1), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x1 + r, y2), (x2 - r, y2), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x1, y1 + r), (x1, y2 - r), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x2, y1 + r), (x2, y2 - r), color, thickness, cv2.LINE_AA)


def _panel(frame, x1, y1, x2, y2, alpha=0.82, radius=14, accent_top=True):
    """Panel semitransparente estilo glassmorphism oscuro optimizado con slicing de ROI."""
    H, W = frame.shape[:2]
    rx1, ry1 = max(0, x1), max(0, y1)
    rx2, ry2 = min(W, x2), min(H, y2)
    if rx2 <= rx1 or ry2 <= ry1:
        return
    r = _s(frame, radius)
    roi = frame[ry1:ry2, rx1:rx2]
    overlay_roi = roi.copy()
    _rounded_rect(overlay_roi, x1 - rx1, y1 - ry1, x2 - rx1, y2 - ry1, r, _HUB["bg"], -1)
    cv2.addWeighted(overlay_roi, alpha, roi, 1.0 - alpha, 0, roi)

    _rounded_rect(frame, x1, y1, x2, y2, r, _HUB["border"], _s(frame, 1))

    if accent_top:
        glow_y1, glow_y2 = max(0, y1 - _s(frame, 4)), min(H, y1 + _s(frame, 6))
        glow_x1, glow_x2 = max(0, x1 + r), min(W, x2 - r)
        if glow_x2 > glow_x1 and glow_y2 > glow_y1:
            glow_roi = frame[glow_y1:glow_y2, glow_x1:glow_x2]
            glow_ov = glow_roi.copy()
            cv2.line(glow_ov, (0, y1 - glow_y1), (glow_x2 - glow_x1, y1 - glow_y1),
                     _HUB["accent"], _s(frame, 5), cv2.LINE_AA)
            cv2.addWeighted(glow_ov, 0.35, glow_roi, 0.65, 0, glow_roi)
        cv2.line(frame, (x1 + r, y1), (x2 - r, y1), _HUB["accent"], _s(frame, 2), cv2.LINE_AA)


def _txt(frame, text, pos, scale=0.5, color=None, thickness=1,
         shadow=True, font=_FONT):
    """Texto anti-aliased con sombra suave (offset 2px) para legibilidad."""
    color = color or _HUB["white"]
    sc = scale * frame.shape[0] / 1080.0
    th = max(1, _s(frame, thickness))
    if shadow:
        off = _s(frame, 2)
        cv2.putText(frame, text, (pos[0] + off, pos[1] + off),
                    font, sc, _HUB["shadow"], th + _s(frame, 1), cv2.LINE_AA)
    cv2.putText(frame, text, pos, font, sc, color, th, cv2.LINE_AA)


def _txt_size(frame, text, scale=0.5, thickness=1, font=_FONT):
    sc = scale * frame.shape[0] / 1080.0
    th = max(1, _s(frame, thickness))
    (tw, thh), _ = cv2.getTextSize(text, font, sc, th)
    return tw, thh


# ==============================================================================
# COMPONENTES DEL HUD
# ==============================================================================

def _corners(frame, x1, y1, x2, y2, color=None, arm=18, thick=2):
    """Marcador táctico de esquinas con glow optimizado por ROI."""
    color = color or _HUB["accent"]
    a, t = _s(frame, arm), _s(frame, thick)
    H, W = frame.shape[:2]
    rx1 = max(0, min(x1, x2) - a - t - 4)
    ry1 = max(0, min(y1, y2) - a - t - 4)
    rx2 = min(W, max(x1, x2) + a + t + 4)
    ry2 = min(H, max(y1, y2) + a + t + 4)
    if rx2 <= rx1 or ry2 <= ry1:
        return

    segs = []
    for (cx, cy, sx, sy) in [(x1, y1, 1, 1), (x2, y1, -1, 1),
                             (x1, y2, 1, -1), (x2, y2, -1, -1)]:
        segs.append(((cx, cy), (cx + sx * a, cy)))
        segs.append(((cx, cy), (cx, cy + sy * a)))

    roi = frame[ry1:ry2, rx1:rx2]
    glow_roi = roi.copy()
    for p1, p2 in segs:
        lp1 = (p1[0] - rx1, p1[1] - ry1)
        lp2 = (p2[0] - rx1, p2[1] - ry1)
        cv2.line(glow_roi, lp1, lp2, color, t + _s(frame, 4), cv2.LINE_AA)
    cv2.addWeighted(glow_roi, 0.30, roi, 0.70, 0, roi)
    for p1, p2 in segs:
        cv2.line(frame, p1, p2, color, t, cv2.LINE_AA)


def _bar(frame, x, y, w, h, ratio, color_fg, threshold=None):
    """Barra de progreso con extremos redondeados y marcador de umbral."""
    r = h // 2
    _rounded_rect(frame, x, y, x + w, y + h, r, _HUB["sep"], -1)
    ratio = float(np.clip(ratio, 0.0, 1.0))
    fill = int(ratio * w)
    if fill > h:
        _rounded_rect(frame, x, y, x + fill, y + h, r, color_fg, -1)
        head = tuple(min(255, int(c * 1.35)) for c in color_fg)
        cv2.circle(frame, (x + fill - r, y + r), r, head, -1, cv2.LINE_AA)
    if threshold is not None:
        tx = x + int(np.clip(threshold, 0, 1) * w)
        cv2.line(frame, (tx, y - _s(frame, 3)), (tx, y + h + _s(frame, 3)),
                 _HUB["white"], _s(frame, 1), cv2.LINE_AA)
    _rounded_rect(frame, x, y, x + w, y + h, r, _HUB["border"], _s(frame, 1))


def _pill(frame, text, x, y, color_bg, scale=0.42):
    """Pastilla redondeada con texto oscuro sobre color sólido."""
    px, py = _s(frame, 10), _s(frame, 7)
    tw, th = _txt_size(frame, text, scale, 1, _FONT_UI)
    x2, y2 = x + tw + px * 2, y + py
    y1 = y - th - py
    _rounded_rect(frame, x, y1, x2, y2, (y2 - y1) // 2, color_bg, -1)
    _txt(frame, text, (x + px, y), scale=scale, color=(12, 12, 12),
         shadow=False, font=_FONT_UI)
    return tw + px * 3


def _fase_pill(frame, fase: str, x, y, scale=0.46):
    """Pastilla de fase con triángulo vectorial."""
    color, label, direc = _FASES.get(fase.lower(), _FASES["reposo"])
    px, py = _s(frame, 12), _s(frame, 8)
    tri_w = _s(frame, 14) if direc else 0
    tw, th = _txt_size(frame, label, scale, 1, _FONT_UI)
    y1, y2 = y - th - py, y + py
    x2 = x + tri_w + tw + px * 2 + (_s(frame, 6) if direc else 0)
    _rounded_rect(frame, x, y1, x2, y2, (y2 - y1) // 2, color, -1)

    tx = x + px
    if direc:
        cy = (y1 + y2) // 2
        hw, hh = tri_w // 2, _s(frame, 5)
        if direc > 0:
            pts = np.array([[tx + hw, cy - hh], [tx, cy + hh], [tx + tri_w, cy + hh]])
        else:
            pts = np.array([[tx + hw, cy + hh], [tx, cy - hh], [tx + tri_w, cy - hh]])
        cv2.fillPoly(frame, [pts], (12, 12, 12), cv2.LINE_AA)
        tx += tri_w + _s(frame, 6)
    _txt(frame, label, (tx, y), scale=scale, color=(12, 12, 12),
         shadow=False, font=_FONT_UI)
    return x2 - x


def _metric(frame, label, value, x, y, vcolor=None, vscale=0.78, unit=""):
    """Bloque etiqueta pequeña + valor grande (+ unidad pequeña opcional)."""
    _txt(frame, label.upper(), (x, y), scale=0.34, color=_HUB["muted"],
         shadow=False, font=_FONT_UI)
    gap = _s(frame, 30)
    _txt(frame, value, (x, y + gap), scale=vscale,
         color=vcolor or _HUB["white"], thickness=1)
    if unit:
        vw, _ = _txt_size(frame, value, vscale)
        _txt(frame, unit, (x + vw + _s(frame, 6), y + gap), scale=0.36,
             color=_HUB["muted"], shadow=False, font=_FONT_UI)


def _sparkline(frame, x, y, w, h, values, color=None, fill=True):
    """Mini-gráfico del historial de velocidad optimizado por ROI."""
    if len(values) < 2:
        return
    H, W = frame.shape[:2]
    color = color or _HUB["accent"]
    vals = np.asarray(values, dtype=np.float32)
    vmin, vmax = float(vals.min()), float(vals.max())
    rng = (vmax - vmin) or 1.0
    xs = np.linspace(x, x + w, len(vals)).astype(int)
    ys = (y + h - ((vals - vmin) / rng) * h).astype(int)
    pts = np.stack([xs, ys], axis=1)

    if fill:
        rx1, ry1 = max(0, x), max(0, y)
        rx2, ry2 = min(W, x + w), min(H, y + h)
        if rx2 > rx1 and ry2 > ry1:
            poly = np.vstack([pts, [[x + w, y + h], [x, y + h]]])
            local_poly = poly - np.array([rx1, ry1])
            roi = frame[ry1:ry2, rx1:rx2]
            overlay_roi = roi.copy()
            cv2.fillPoly(overlay_roi, [local_poly.astype(np.int32)], color, cv2.LINE_AA)
            cv2.addWeighted(overlay_roi, 0.18, roi, 0.82, 0, roi)

    cv2.polylines(frame, [pts], False, color, _s(frame, 2), cv2.LINE_AA)
    cx, cy = int(xs[-1]), int(ys[-1])
    gr = _s(frame, 7)
    gx1, gy1 = max(0, cx - gr), max(0, cy - gr)
    gx2, gy2 = min(W, cx + gr), min(H, cy + gr)
    if gx2 > gx1 and gy2 > gy1:
        groi = frame[gy1:gy2, gx1:gx2]
        gov = groi.copy()
        cv2.circle(gov, (cx - gx1, cy - gy1), gr, color, -1, cv2.LINE_AA)
        cv2.addWeighted(gov, 0.35, groi, 0.65, 0, groi)
    cv2.circle(frame, (cx, cy), _s(frame, 3), _HUB["white"], -1, cv2.LINE_AA)


def _trail(frame, puntos, velocidades=None, max_pts=40):
    """Estela de la trayectoria de la barra con desvanecimiento, color y ROI."""
    if len(puntos) < 2:
        return
    H, W = frame.shape[:2]
    pts = puntos[-max_pts:]
    vels = (velocidades or [0.6] * len(pts))[-max_pts:]
    n = len(pts)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    max_th = _s(frame, 6)
    rx1 = max(0, int(min(xs)) - max_th - 4)
    ry1 = max(0, int(min(ys)) - max_th - 4)
    rx2 = min(W, int(max(xs)) + max_th + 4)
    ry2 = min(H, int(max(ys)) + max_th + 4)
    if rx2 <= rx1 or ry2 <= ry1:
        return

    roi = frame[ry1:ry2, rx1:rx2]
    overlay_roi = roi.copy()
    for i in range(1, n):
        t = i / n                          # 0 = viejo, 1 = reciente
        col = _color_por_velocidad(vels[i])
        col = tuple(int(c * (0.35 + 0.65 * t)) for c in col)
        th = max(1, int(_s(frame, 6) * t))
        p1 = (int(pts[i - 1][0]) - rx1, int(pts[i - 1][1]) - ry1)
        p2 = (int(pts[i][0]) - rx1, int(pts[i][1]) - ry1)
        cv2.line(overlay_roi, p1, p2, col, th, cv2.LINE_AA)
    cv2.addWeighted(overlay_roi, 0.75, roi, 0.25, 0, roi)

    cx, cy = map(int, pts[-1])
    gr = _s(frame, 12)
    gx1, gy1 = max(0, cx - gr), max(0, cy - gr)
    gx2, gy2 = min(W, cx + gr), min(H, cy + gr)
    if gx2 > gx1 and gy2 > gy1:
        groi = frame[gy1:gy2, gx1:gx2]
        gov = groi.copy()
        cv2.circle(gov, (cx - gx1, cy - gy1), gr, _color_por_velocidad(vels[-1]), -1, cv2.LINE_AA)
        cv2.addWeighted(gov, 0.35, groi, 0.65, 0, groi)
    cv2.circle(frame, (cx, cy), _s(frame, 5), _color_por_velocidad(vels[-1]), -1, cv2.LINE_AA)
    cv2.circle(frame, (cx, cy), _s(frame, 5), _HUB["white"], _s(frame, 1), cv2.LINE_AA)


def _render_watermark_pill(frame, text: str = "AbiSchulz", active_area: Optional[tuple] = None) -> None:
    """
    Dibuja una cápsula / pastilla estética semitransparente con la firma «AbiSchulz»
    en la parte inferior del video. Ubicada estratégicamente fuera de la zona de colisión
    de la interfaz de Instagram Reels / Stories (los últimos 250 a 320 px).
    """
    H, W = frame.shape[:2]
    scale = 0.44
    tw, th = _txt_size(frame, text, scale=scale, thickness=1, font=_FONT_UI)
    pad_h = _s(frame, 12)
    pad_v = _s(frame, 6)
    dot_r = _s(frame, 3)
    dot_gap = _s(frame, 7)

    pill_w = pad_h + dot_r * 2 + dot_gap + tw + pad_h
    pill_h = th + pad_v * 2

    # Centrado horizontal en el lienzo
    px1 = (W - pill_w) // 2
    px2 = px1 + pill_w

    # Posicionamiento vertical seguro para Reels/Stories
    if active_area:
        pad_x, pad_y, new_w, new_h = active_area
        # Si hay banda inferior libre amplia (video horizontal/cuadrado)
        if pad_y > _s(frame, 120):
            py_ideal = pad_y + new_h + _s(frame, 25)
            # Asegurar que no caiga en la colisión extrema de la app
            py1 = H - _s(frame, 220) if (py_ideal + pill_h > H - _s(frame, 180)) else py_ideal
        else:
            py1 = H - _s(frame, 220)
    else:
        # Por defecto a 220px del fondo (por encima de los 250-320px de colisión de IG)
        py1 = H - _s(frame, 220)

    py2 = py1 + pill_h

    rx1, ry1 = max(0, px1), max(0, py1)
    rx2, ry2 = min(W, px2), min(H, py2)
    if rx2 <= rx1 or ry2 <= ry1:
        return

    # Slicing de ROI para máxima velocidad
    roi = frame[ry1:ry2, rx1:rx2]
    overlay_roi = roi.copy()
    radius = pill_h // 2
    _rounded_rect(overlay_roi, px1 - rx1, py1 - ry1, px2 - rx1, py2 - ry1, radius, _HUB["bg"], -1)
    cv2.addWeighted(overlay_roi, 0.78, roi, 0.22, 0, roi)

    _rounded_rect(frame, px1, py1, px2, py2, radius, _HUB["border"], max(1, _s(frame, 1)))

    # Punto de acento
    dot_cx = px1 + pad_h + dot_r
    dot_cy = py1 + pill_h // 2
    cv2.circle(frame, (dot_cx, dot_cy), dot_r, _HUB["accent"], -1, cv2.LINE_AA)

    # Texto de la firma sin sombra pesada
    txt_x = dot_cx + dot_r + dot_gap
    txt_y = py1 + pad_v + th
    _txt(frame, text, (txt_x, txt_y), scale=scale, color=_HUB["white"],
         thickness=1, shadow=False, font=_FONT_UI)


# ==============================================================================
# COMPOSICIÓN: panel principal listo para usar (API pública, se conserva)
# ==============================================================================

def render_hud(frame, *, reps=0, fase="reposo", velocidad=0.0,
               fatiga=0.0, historial_vel=(), titulo="SPORT HUB",
               bbox=None, trayectoria=(), vel_trayectoria=()):
    """Dibuja el HUD completo. Llamar una vez por frame."""
    H, W = frame.shape[:2]
    m = _s(frame, 28)                       # margen exterior
    pw, ph = _s(frame, 330), _s(frame, 320) # tamaño del panel
    x1, y1 = m, m
    x2, y2 = x1 + pw, y1 + ph
    pad = _s(frame, 22)

    if trayectoria:
        _trail(frame, list(trayectoria), list(vel_trayectoria) or None)
    if bbox:
        _corners(frame, *map(int, bbox))

    _panel(frame, x1, y1, x2, y2)

    # ── Cabecera ──
    cy = y1 + pad + _s(frame, 8)
    cv2.circle(frame, (x1 + pad + _s(frame, 5), cy - _s(frame, 5)),
               _s(frame, 5), _HUB["accent"], -1, cv2.LINE_AA)
    _txt(frame, titulo, (x1 + pad + _s(frame, 20), cy), scale=0.55,
         color=_HUB["white"], font=_FONT_UI)
    cy += _s(frame, 16)
    cv2.line(frame, (x1 + pad, cy), (x2 - pad, cy), _HUB["sep"],
             _s(frame, 1), cv2.LINE_AA)

    # ── Reps + fase ──
    cy += _s(frame, 34)
    _metric(frame, "reps", str(reps), x1 + pad, cy,
            vcolor=_HUB["green"], vscale=1.15)
    _fase_pill(frame, fase, x1 + pad + _s(frame, 130), cy + _s(frame, 26))

    # ── Velocidad ──
    cy += _s(frame, 78)
    vcol = _color_por_velocidad(velocidad)
    _metric(frame, "velocidad media", f"{velocidad:.2f}", x1 + pad, cy,
            vcolor=vcol, unit="m/s")
    cy += _s(frame, 44)
    _bar(frame, x1 + pad, cy, pw - pad * 2, _s(frame, 10),
         velocidad, vcol, threshold=0.75)

    # ── Fatiga ──
    cy += _s(frame, 40)
    fcol = _HUB["green"] if fatiga < 0.2 else (
        _HUB["amber"] if fatiga < 0.4 else _HUB["red"])
    _txt(frame, "FATIGA", (x1 + pad, cy), scale=0.34, color=_HUB["muted"],
         shadow=False, font=_FONT_UI)
    _txt(frame, f"{fatiga * 100:.0f}%", (x2 - pad - _s(frame, 58), cy),
         scale=0.44, color=fcol, font=_FONT_UI)
    cy += _s(frame, 12)
    _bar(frame, x1 + pad, cy, pw - pad * 2, _s(frame, 10), fatiga, fcol)

    # ── Sparkline del historial ──
    if len(historial_vel) >= 2:
        cy += _s(frame, 36)
        _txt(frame, "VELOCIDAD POR REP", (x1 + pad, cy), scale=0.32,
             color=_HUB["muted"], shadow=False, font=_FONT_UI)
        _sparkline(frame, x1 + pad, cy + _s(frame, 10),
                   pw - pad * 2, _s(frame, 42), list(historial_vel),
                   color=_HUB["accent"])

    # ── Firma inferior «AbiSchulz» ──
    _render_watermark_pill(frame, text="AbiSchulz")


# ── Función principal de render por frame ────────────────────────────────────

def renderizar_frame(frame, det: Optional['Deteccion'], trayectoria: deque,
                     v_inst: float, reps: int, fase: str,
                     registro: list, vl_actual: Optional[float],
                     rir_txt: Optional[str] = None,
                     active_area: Optional[tuple] = None,
                     nombre_ejercicio: Optional[str] = None,
                     carga_kg: Optional[float] = None):
    """Renderizado por cuadro optimizado por ROI con HUD modular, ejercicio confirmado y firma AbiSchulz."""
    # ── 1. TRAYECTORIA DEGRADADA (más reciente = verde neón brillante y grueso) ─
    n = len(trayectoria)
    for j in range(1, n):
        t = j / n
        col = (0, int(140 + 100 * t), int(190 + 65 * t))
        grosor = max(1, _s(frame, 2 if t > 0.6 else 1))
        cv2.line(frame, trayectoria[j - 1], trayectoria[j], col, grosor, cv2.LINE_AA)

    # ── 2. MARCADOR DE DISCO ──────────────────────────────────────────────────
    if det is not None:
        bx1, by1 = int(det.x_min), int(det.y_min)
        bx2, by2 = int(det.x_max), int(det.y_max)

        _corners(frame, bx1, by1, bx2, by2, _HUB["accent"])
        cv2.circle(frame, (det.cx, det.cy), _s(frame, 4), _HUB["accent"], -1, cv2.LINE_AA)
        cv2.circle(frame, (det.cx, det.cy), _s(frame, 8), _HUB["accent"], 1, cv2.LINE_AA)

        vel = abs(v_inst)
        col_vel = _color_por_velocidad(vel)
        _txt(frame, f"{vel:.2f} m/s",
             (det.cx + _s(frame, 45), det.cy + _s(frame, 6)),
             scale=0.65, color=col_vel, shadow=True)

    # ── 3. PANEL HUD MODULAR (escalado responsivo para 1080x1920 Stories/Reels) ─
    PX = _s(frame, 20)
    PW = _s(frame, 330)
    PH = _s(frame, 355)

    # Si hay padding superior amplio (video horizontal), alojar el HUD fuera del levantamiento
    if active_area and active_area[1] > _s(frame, 180):
        PY = max(_s(frame, 20), (active_area[1] - PH) // 2)
    else:
        PY = _s(frame, 20)

    # Usar _panel con ROI en lugar de copiar todo el frame
    _panel(frame, PX, PY, PX + PW, PY + PH, alpha=0.76, radius=12, accent_top=True)
    cv2.line(frame, (PX + _s(frame, 10), PY + PH), (PX + PW - _s(frame, 10), PY + PH), _HUB["sep"], _s(frame, 1), cv2.LINE_AA)

    # ── MARCA IRON PROGRESS ──
    _txt(frame, "IRON PROGRESS", (PX + _s(frame, 18), PY + _s(frame, 22)),
         scale=0.42, color=_HUB["accent"], shadow=True, thickness=1)

    # ── 3b. EJERCICIO CONFIRMADO Y CARGA ──
    txt_ej = ""
    if nombre_ejercicio and str(nombre_ejercicio).strip() and str(nombre_ejercicio).strip().lower() not in ("ejercicio no especificado", "movimiento libre", "no_reconocido"):
        txt_ej = str(nombre_ejercicio).strip().upper()
        if carga_kg and float(carga_kg) > 0:
            txt_ej += f" · {float(carga_kg):g} KG"
    elif carga_kg and float(carga_kg) > 0:
        txt_ej = f"{float(carga_kg):g} KG"
    else:
        txt_ej = "VBT TRACKER"

    scale_ej = 0.44
    max_w_ej = PW - _s(frame, 36)
    (tw_ej, _), _ = cv2.getTextSize(txt_ej, _FONT_UI, scale_ej, 1)
    if tw_ej > max_w_ej and tw_ej > 0:
        scale_ej = max(0.28, scale_ej * (max_w_ej / tw_ej))

    _txt(frame, txt_ej, (PX + _s(frame, 18), PY + _s(frame, 38)),
         scale=scale_ej, color=_HUB["white"], shadow=True, thickness=1, font=_FONT_UI)

    cv2.line(frame, (PX + _s(frame, 12), PY + _s(frame, 46)), (PX + PW - _s(frame, 12), PY + _s(frame, 46)), _HUB["sep"], _s(frame, 1), cv2.LINE_AA)

    # ── 3c. REPETICIONES ──
    _txt(frame, "REPETICIONES", (PX + _s(frame, 18), PY + _s(frame, 58)),
         scale=0.34, color=_HUB["muted"])
    _txt(frame, str(reps), (PX + _s(frame, 16), PY + _s(frame, 122)),
         scale=2.5, color=_HUB["green"], thickness=2, shadow=True)

    cv2.line(frame, (PX + _s(frame, 12), PY + _s(frame, 132)), (PX + PW - _s(frame, 12), PY + _s(frame, 132)), _HUB["sep"], _s(frame, 1), cv2.LINE_AA)

    # ── 3d. FASE ──
    _fase_pill(frame, fase, PX + _s(frame, 18), PY + _s(frame, 157), scale=0.46)
    cv2.line(frame, (PX + _s(frame, 12), PY + _s(frame, 172)), (PX + PW - _s(frame, 12), PY + _s(frame, 172)), _HUB["sep"], _s(frame, 1), cv2.LINE_AA)

    # ── 3e. VELOCIDAD ÚLTIMA REP + barra ──
    if registro:
        v_last = registro[-1].v_avg
        col_last = _color_por_velocidad(v_last)
        _metric(frame, "Ultima rep", f"{v_last:.2f} m/s",
                PX + _s(frame, 18), PY + _s(frame, 190), vcolor=col_last)
        _bar(frame, PX + _s(frame, 18), PY + _s(frame, 220), PW - _s(frame, 36), _s(frame, 6), v_last / 1.5, col_last)

    cv2.line(frame, (PX + _s(frame, 12), PY + _s(frame, 236)), (PX + PW - _s(frame, 12), PY + _s(frame, 236)), _HUB["sep"], _s(frame, 1), cv2.LINE_AA)

    # ── 3f. PÉRDIDA DE VELOCIDAD (fatiga) + barra ──
    if vl_actual is not None:
        if vl_actual < 20:
            color_vl, estado = _HUB["green"], "OPTIMO"
        elif vl_actual < 30:
            color_vl, estado = _HUB["amber"], "ALERTA"
        else:
            color_vl, estado = _HUB["red"], "FATIGA"

        _metric(frame, "Perdida de velocidad",
                f"{vl_actual:.1f}%   {estado}",
                PX + _s(frame, 18), PY + _s(frame, 254),
                vcolor=color_vl, vscale=0.60)
        _bar(frame, PX + _s(frame, 18), PY + _s(frame, 284), PW - _s(frame, 36), _s(frame, 6), vl_actual / 50, color_vl)

    # ── 3g. REPS EN RESERVA (v3) ──
    if rir_txt:
        cv2.line(frame, (PX + _s(frame, 12), PY + _s(frame, 300)), (PX + PW - _s(frame, 12), PY + _s(frame, 300)),
                 _HUB["sep"], _s(frame, 1), cv2.LINE_AA)
        _txt(frame, "EN RESERVA (RIR est.)", (PX + _s(frame, 18), PY + _s(frame, 318)),
             scale=0.34, color=_HUB["muted"], shadow=False, font=_FONT_UI)
        _txt(frame, rir_txt, (PX + PW - _s(frame, 72), PY + _s(frame, 320)),
             scale=0.52, color=_HUB["accent"], font=_FONT_UI)

    # ── 4. MARCA DE AGUA INFERIOR «AbiSchulz» (Safe Zone Reels/Stories) ──────
    _render_watermark_pill(frame, text="AbiSchulz", active_area=active_area)


# ==============================================================================
# UTILIDADES DE VIDEO Y ESCRITOR DE ALTA CALIDAD SÍNCRONO VIA PIPE FFMPEG
# ==============================================================================
def inicializar_video(ruta_video: str, numero_usuario: Optional[str] = None,
                      callback_notificacion: Optional[Callable] = None):
    if numero_usuario:
        _notificar_whatsapp_seguro("Procesando video...", numero_usuario, callback_notificacion)

    cap = cv2.VideoCapture(ruta_video)
    if not cap.isOpened():
        raise ValueError(f"OpenCV no pudo abrir el video: {ruta_video}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or np.isnan(fps) or fps <= 0:
        logger.warning("FPS ilegibles; forzando 30.0 por seguridad.")
        fps = 30.0

    ancho = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    alto = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    return cap, fps, 1.0 / fps, ancho, alto


class EscritorVideoFFmpeg:
    """Escritor de video de alta calidad mediante canal (pipe) directo a FFmpeg.
    - Preserva el audio ambiente original en AAC a 192 kbps y 48 kHz (-shortest).
    - Codificación en una pasada con CRF 18 y VBV Constrained Encoding (-maxrate / -bufsize).
    - Elimina deadlock en el pipe con loglevel error, stdout DEVNULL y drenaje en hilo background.
    - Flags específicos para Reels/Stories: Profile High 4.1, GOP cerrado de 2s y rango limitado TV."""

    def __init__(self, ruta_salida: str, fps: float, ancho: int = 1080, alto: int = 1920,
                 crf: int = 18, ruta_audio_original: Optional[str] = None,
                 duracion_s: Optional[float] = None):
        self.ruta_salida = ruta_salida
        # Limitar framerate a un máximo de 60 fps constantes para Meta
        self.fps = min(float(fps), 60.0)
        self.ancho = ancho
        self.alto = alto
        directorio, nombre = os.path.split(ruta_salida)
        self.ruta_temporal = os.path.join(directorio, "temp_" + nombre)

        try:
            self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            self.ffmpeg_exe = "ffmpeg"

        # Cálculo de Bitrate VBV para garantizar matemáticamente peso < 15 MB
        if duracion_s and duracion_s > 0:
            calc_max = int((14.2 * 8192) / duracion_s - 192)
            maxrate_kbps = min(7500, max(1500, calc_max))
        else:
            maxrate_kbps = 6500
        bufsize_kbps = maxrate_kbps * 2

        gop_size = max(1, int(round(self.fps * 2)))
        keyint_min = max(1, int(round(self.fps)))

        cmd = [
            self.ffmpeg_exe, "-y",
            "-loglevel", "error",
            "-nostats",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{ancho}x{alto}",
            "-pix_fmt", "bgr24",
            "-r", f"{self.fps:.3f}",
            "-i", "-",
        ]

        if ruta_audio_original and os.path.exists(ruta_audio_original):
            cmd.extend(["-i", ruta_audio_original])
            cmd.extend(["-map", "0:v:0", "-map", "1:a:0?"])
            cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest"])
        else:
            cmd.extend(["-an"])

        cmd.extend([
            "-c:v", "libx264",
            "-crf", str(crf),
            "-maxrate", f"{maxrate_kbps}k",
            "-bufsize", f"{bufsize_kbps}k",
            "-preset", "fast",
            "-profile:v", "high",
            "-level:v", "4.1",
            "-g", str(gop_size),
            "-keyint_min", str(keyint_min),
            "-sc_threshold", "0",
            "-pix_fmt", "yuv420p",
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-colorspace", "bt709",
            "-color_range", "tv",
            "-movflags", "+faststart",
            self.ruta_temporal
        ])

        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
            self._usando_pipe = True
            self._stderr_lines = []

            def _drain():
                try:
                    for line in iter(self.proc.stderr.readline, b''):
                        self._stderr_lines.append(line.decode('utf-8', errors='ignore'))
                except Exception:
                    pass

            self._thread_stderr = threading.Thread(target=_drain, daemon=True)
            self._thread_stderr.start()
        except Exception as e:
            logger.warning(f"No se pudo iniciar canal directo de FFmpeg: {e}. Usando fallback OpenCV VideoWriter.")
            self._usando_pipe = False
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self.cv_writer = cv2.VideoWriter(self.ruta_temporal, fourcc, self.fps, (ancho, alto))

    def write(self, frame: np.ndarray) -> None:
        if self._usando_pipe and self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(frame.tobytes())
            except Exception as e:
                logger.error(f"Fallo al escribir cuadro en canal FFmpeg: {e}")
        elif hasattr(self, "cv_writer") and self.cv_writer and self.cv_writer.isOpened():
            self.cv_writer.write(frame)

    def write_raw_bytes(self, raw_bytes: bytes) -> None:
        """Escribe bytes crudos directamente al pipe sin serializar en cada llamada."""
        if self._usando_pipe and self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(raw_bytes)
            except BrokenPipeError:
                self._usando_pipe = False
            except Exception as e:
                logger.error(f"Fallo al escribir bytes en canal FFmpeg: {e}")

    def release(self) -> None:
        if self._usando_pipe and self.proc:
            try:
                if self.proc.stdin:
                    try:
                        self.proc.stdin.close()
                    except Exception:
                        pass
                self.proc.wait(timeout=20)
                if hasattr(self, "_thread_stderr") and self._thread_stderr.is_alive():
                    self._thread_stderr.join(timeout=2)
                if self.proc.returncode != 0:
                    err_txt = "".join(getattr(self, "_stderr_lines", []))
                    logger.error(f"FFmpeg devolvió código {self.proc.returncode}: {err_txt}")
            except Exception as e:
                logger.error(f"Error al cerrar canal de FFmpeg: {e}")
                try:
                    self.proc.kill()
                except Exception:
                    pass
            finally:
                self.proc = None
        elif hasattr(self, "cv_writer") and self.cv_writer:
            try:
                self.cv_writer.release()
            except Exception:
                pass


def _inicializar_escritor_video(ruta_salida: str, fps: float,
                                ruta_audio_original: Optional[str] = None,
                                duracion_s: Optional[float] = None):
    writer = EscritorVideoFFmpeg(
        ruta_salida=ruta_salida,
        fps=fps,
        ancho=1080,
        alto=1920,
        crf=18,
        ruta_audio_original=ruta_audio_original,
        duracion_s=duracion_s
    )
    return writer, writer.ruta_temporal


def _convertir_video_h264(ruta_temporal: str, ruta_salida: str):
    logger.info("Finalizando exportación en alta calidad para Instagram Stories/Reels (Rec. 709 SDR)...")
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg_exe = "ffmpeg"

    if os.path.exists(ruta_temporal) and os.path.abspath(ruta_temporal) != os.path.abspath(ruta_salida):
        try:
            os.replace(ruta_temporal, ruta_salida)
        except Exception:
            cmd = [
                ffmpeg_exe, "-y",
                "-i", ruta_temporal,
                "-c", "copy",
                "-movflags", "+faststart",
                ruta_salida
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if os.path.exists(ruta_temporal):
                try:
                    os.remove(ruta_temporal)
                except OSError:
                    pass

    # Salvaguarda de tamaño para WhatsApp Cloud API (Límite máximo de Meta: 16 MB)
    MAX_BYTES = int(15.5 * 1024 * 1024)
    if os.path.exists(ruta_salida):
        tam_actual = os.path.getsize(ruta_salida)
        if tam_actual > MAX_BYTES:
            logger.warning(f"Video ({tam_actual / (1024*1024):.2f} MB) excede 15.5 MB. Re-comprimiendo en una sola pasada VBV...")
            ruta_comp = ruta_salida + ".comp.mp4"
            cmd_comp = [
                ffmpeg_exe, "-y",
                "-i", ruta_salida,
                "-c:v", "libx264",
                "-crf", "22",
                "-maxrate", "3000k",
                "-bufsize", "6000k",
                "-preset", "fast",
                "-profile:v", "high",
                "-level:v", "4.1",
                "-pix_fmt", "yuv420p",
                "-c:a", "copy",
                "-movflags", "+faststart",
                ruta_comp
            ]
            res_comp = subprocess.run(cmd_comp, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if res_comp.returncode == 0 and os.path.exists(ruta_comp):
                os.replace(ruta_comp, ruta_salida)
            elif os.path.exists(ruta_comp):
                try:
                    os.remove(ruta_comp)
                except OSError:
                    pass


# ==============================================================================
# FUNCIÓN PRINCIPAL ORQUESTADORA — API pública, drop-in compatible
# ------------------------------------------------------------------------------
# Mismos 6 parámetros de siempre + 3 opcionales nuevos:
#   carga_kg         -> desbloquea %1RM preciso, e1RM diario y ajustes en kg
#   nivel_atleta     -> "Novato" | "Avanzado" | "Elite" (antes fijo interno)
#   ruta_modelo_pose -> activa la cinemática articular (ej. "yolo11n-pose.pt")
# El dict de retorno conserva TODAS las claves anteriores y añade nuevas.
# ==============================================================================
def procesar_levantamiento_vbt(
        ruta_modelo: str,
        ruta_video: str,
        media_id: str,
        numero_usuario: str,
        nombre_ejercicio: str = "Ejercicio No Especificado",
        ruta_salida: str = "output_vbt.mp4",
        carga_kg: Optional[float] = None,
        nivel_atleta: str = "Avanzado",
        ruta_modelo_pose: Optional[str] = None,
        callback_notificacion: Optional[Callable] = None,
) -> dict:
    if not nombre_ejercicio:
        nombre_ejercicio = "Movimiento Libre"

    logger.info(f"Iniciando Motor VBT v3 para: {nombre_ejercicio}...")
    _silenciar_avisos_ultralytics()

    # Preprocesamiento HDR -> Rec. 709 SDR si el video de entrada está en HDR
    ruta_video_proc, es_temp_sdr = _preprocesar_video_hdr_si_es_necesario(ruta_video)

    perfil = obtener_perfil_ejercicio(nombre_ejercicio)
    nivel = _normalizar_nivel(nivel_atleta)

    cap = None
    video_writer = None
    ruta_temporal = ruta_salida
    maquina: Optional[MaquinaEstadosRep] = None
    registro_repeticiones: list = []
    frames_totales = 0
    frames_con_disco = 0
    timestamp_actual = 0.0

    TARGET_W, TARGET_H = 1080, 1920

    try:
        # 1. Inicialización de componentes (reutilizando modelos cacheados)
        rastreador = RastreadorDisco(ruta_modelo)
        pose = EstimadorPose(ruta_modelo_pose)
        cap, fps, dt_nominal, ancho_orig, alto_orig = inicializar_video(ruta_video_proc, numero_usuario, callback_notificacion)

        total_frames_est = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duracion_s = (total_frames_est / fps) if (fps and fps > 0 and total_frames_est and total_frames_est > 0) else None
        video_writer, ruta_temporal = _inicializar_escritor_video(
            ruta_salida=ruta_salida,
            fps=fps,
            ruta_audio_original=ruta_video,
            duracion_s=duracion_s
        )

        # Cálculo de escalado y centrado del video original sobre lienzo 1080x1920 (9:16)
        scale_canvas = min(TARGET_W / ancho_orig, TARGET_H / alto_orig)
        new_w = int(round(ancho_orig * scale_canvas))
        new_h = int(round(alto_orig * scale_canvas))
        pad_x = (TARGET_W - new_w) // 2
        pad_y = (TARGET_H - new_h) // 2
        active_area = (pad_x, pad_y, new_w, new_h)

        estimador = EstimadorCinematica(CONFIG, fps)
        maquina = MaquinaEstadosRep(CONFIG, fps)

        puntos_trayectoria = deque(maxlen=30)
        puntos_trayectoria_canvas = deque(maxlen=30)
        tiempo_anterior = 0.0
        rir_live: Optional[str] = None
        ultima_det_canvas = None
        ultimo_frame_orig = None

        def _adjuntar_pose(rep: Repeticion) -> None:
            """Cruza la rep con la cinemática articular (si está activa)."""
            if not pose.activo:
                return
            t_conc_ini = (rep.t_inicio_s + rep.t_excentrica_s
                          + (rep.t_amortiguacion_s or 0.0))
            met = pose.metricas_rep(rep.t_inicio_s, rep.t_fin_s, t_conc_ini)
            rep.angulo_rodilla_min = met.get("angulo_rodilla_min")
            rep.angulo_cadera_min = met.get("angulo_cadera_min")
            rep.vel_angular_rodilla_max = met.get("vel_angular_rodilla_max")

        logger.info("Analizando video frame a frame...")
        interp = cv2.INTER_AREA if scale_canvas < 1.0 else cv2.INTER_LINEAR

        while cap.isOpened():
            exito, frame_orig = cap.read()
            if not exito:
                break

            frames_totales += 1
            ultimo_frame_orig = frame_orig

            # Timestamps reales, forzados a monotónicos
            timestamp_actual = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if timestamp_actual <= tiempo_anterior:
                timestamp_actual = tiempo_anterior + dt_nominal
            tiempo_anterior = timestamp_actual

            # 2. Tracking YOLO del disco sobre frame original
            det = rastreador.procesar(frame_orig)
            v_inst = 0.0

            # 3. Cinemática articular asociada espacialmente a la barra
            pos_disco_actual = (det.cx, det.cy) if det else rastreador.ultima_pos
            if pose.activo and frames_totales % CONFIG.pose_cada_n_frames == 0:
                pose.procesar(frame_orig, timestamp_actual, pos_disco=pos_disco_actual)

            det_canvas = None
            if det is not None:
                frames_con_disco += 1
                # FIX: append para que el orden cronológico sea correcto (cola vieja -> cabeza reciente)
                puntos_trayectoria.append((det.cx, det.cy))

                # Coordenadas transformadas al lienzo 1080x1920 (9:16)
                det_canvas = Deteccion(
                    cx=int(det.cx * scale_canvas + pad_x),
                    cy=int(det.cy * scale_canvas + pad_y),
                    x_min=det.x_min * scale_canvas + pad_x,
                    y_min=det.y_min * scale_canvas + pad_y,
                    x_max=det.x_max * scale_canvas + pad_x,
                    y_max=det.y_max * scale_canvas + pad_y,
                    alto_caja=det.alto_caja * scale_canvas,
                    m_por_px=det.m_por_px / scale_canvas,
                )
                ultima_det_canvas = det_canvas
                puntos_trayectoria_canvas.append((det_canvas.cx, det_canvas.cy))

                # 4. Filtrado cinemático + máquina de estados evaluados en el mismo instante exacto
                t_eval, y_suave, v_inst = estimador.actualizar(timestamp_actual, det.cy, det.m_por_px)
                rep_cerrada = maquina.actualizar(t_eval, det.cx,
                                                 y_suave, v_inst, det.alto_caja)

                if rep_cerrada:
                    _adjuntar_pose(rep_cerrada)
                    registro_repeticiones.append(rep_cerrada)
                    _recalcular_vl_por_rep(registro_repeticiones)
                    est = estimar_rir_por_velocidad(rep_cerrada.v_avg, perfil, nivel)
                    rir_live = f"~{est:.0f}" if est is not None else None

            # Construcción estética del lienzo 1080x1920 (desenfoque Reels si hay letterbox)
            resized_orig = cv2.resize(frame_orig, (new_w, new_h), interpolation=interp)
            if pad_x > 0 or pad_y > 0:
                small = cv2.resize(frame_orig, (108, 192), interpolation=cv2.INTER_LINEAR)
                small_dark = cv2.convertScaleAbs(small, alpha=0.35)
                blurred = cv2.GaussianBlur(small_dark, (15, 15), 0)
                canvas_frame = cv2.resize(blurred, (TARGET_W, TARGET_H), interpolation=cv2.INTER_LINEAR)
            else:
                canvas_frame = np.empty((TARGET_H, TARGET_W, 3), dtype=np.uint8)

            canvas_frame[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized_orig

            # 5. Fin de serie por quietud prolongada
            if (maquina.estado != "calibrando"
                    and maquina.frames_quietos > fps * CONFIG.seg_inactividad_fin
                    and (maquina.reps > 0 or maquina.estado == "concentrica")):
                rep_final = maquina.cerrar_por_inactividad(timestamp_actual)
                if rep_final:
                    _adjuntar_pose(rep_final)
                    registro_repeticiones.append(rep_final)
                    _recalcular_vl_por_rep(registro_repeticiones)

                break

            # 6. Renderizado HUD y trayectoria sobre lienzo 1080x1920
            vl_actual = (calcular_velocity_loss(registro_repeticiones)
                         if len(registro_repeticiones) >= 2 else None)

            renderizar_frame(
                frame=canvas_frame,
                det=det_canvas,
                trayectoria=puntos_trayectoria_canvas,
                v_inst=v_inst,
                reps=maquina.reps,
                fase=maquina.estado,
                registro=registro_repeticiones,
                vl_actual=vl_actual,
                rir_txt=rir_live,
                active_area=active_area,
                nombre_ejercicio=nombre_ejercicio,
                carga_kg=carga_kg,
            )

            video_writer.write(canvas_frame)

        # 7. Asegurar cierre de la última repetición al terminar el video (lockout o fin de grabación)
        if maquina is not None:
            rep_final = maquina.cerrar_por_inactividad(timestamp_actual)
            if rep_final:
                _adjuntar_pose(rep_final)
                registro_repeticiones.append(rep_final)
                _recalcular_vl_por_rep(registro_repeticiones)
                logger.info(f"Rep #{rep_final.rep} cerrada al terminar el "
                            f"video (grabación cortada en el lockout).")

        # 8. Congelar e imprimir el estado final completado en el HUD (2 segundos)
        reps_finales = maquina.reps if maquina else len(registro_repeticiones)
        vl_final = (calcular_velocity_loss(registro_repeticiones)
                    if len(registro_repeticiones) >= 2 else None)
        if registro_repeticiones:
            est_fin = estimar_rir_por_velocidad(registro_repeticiones[-1].v_avg, perfil, nivel)
            rir_live = f"~{est_fin:.0f}" if est_fin is not None else rir_live

        # Construir lienzo final limpio una sola vez fuera del bucle
        if ultimo_frame_orig is not None:
            resized_fin = cv2.resize(ultimo_frame_orig, (new_w, new_h), interpolation=interp)
            if pad_x > 0 or pad_y > 0:
                small = cv2.resize(ultimo_frame_orig, (108, 192), interpolation=cv2.INTER_LINEAR)
                small_dark = cv2.convertScaleAbs(small, alpha=0.35)
                blurred = cv2.GaussianBlur(small_dark, (15, 15), 0)
                final_canvas = cv2.resize(blurred, (TARGET_W, TARGET_H), interpolation=cv2.INTER_LINEAR)
            else:
                final_canvas = np.empty((TARGET_H, TARGET_W, 3), dtype=np.uint8)
            final_canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized_fin
        else:
            final_canvas = np.full((TARGET_H, TARGET_W, 3), _HUB["bg"], dtype=np.uint8)

        renderizar_frame(
            frame=final_canvas,
            det=ultima_det_canvas,
            trayectoria=puntos_trayectoria_canvas,
            v_inst=0.0,
            reps=reps_finales,
            fase="finalizado",
            registro=registro_repeticiones,
            vl_actual=vl_final,
            rir_txt=rir_live,
            active_area=active_area,
            nombre_ejercicio=nombre_ejercicio,
            carga_kg=carga_kg,
        )

        frames_congelados = int(round(video_writer.fps * 2.0))
        # OPTIMIZACIÓN: Serializar bytes una sola vez antes de escribir
        raw_final_bytes = final_canvas.tobytes()
        for _ in range(frames_congelados):
            video_writer.write_raw_bytes(raw_final_bytes)

    except Exception as e:
        logger.error(f"Crasheo en ciclo principal: {e}")
        traceback.print_exc()
    finally:
        if cap:
            cap.release()
        if video_writer:
            video_writer.release()

        # Limpiar archivo temporal de video SDR si fue generado durante la entrada HDR
        if es_temp_sdr and os.path.exists(ruta_video_proc):
            try:
                os.remove(ruta_video_proc)
            except OSError:
                pass

    # 8. Diagnóstico si no hubo repeticiones válidas
    reps_ok = maquina.reps if maquina else 0
    if reps_ok == 0 or not registro_repeticiones:
        max_vel = max(maquina.vels_serie) if (maquina and maquina.vels_serie) else 0.0
        fase_max = maquina.fase_maxima if maquina else "init"
        descenso = maquina.max_descenso_px if maquina else 0.0
        msg_error = _generar_autopsia_vbt(
            frames_totales, frames_con_disco, fase_max, descenso, max_vel)
        _convertir_video_h264(ruta_temporal, ruta_salida)
        return {"estado": "error", "mensaje": msg_error,
                "ruta_video_renderizado": ruta_salida}

    # 9. Análisis determinista de la serie (v3) -> reporte + prompt LLM
    analisis = analizar_serie(registro_repeticiones, perfil, nivel_atleta, carga_kg)

    reporte_whatsapp = _construir_reporte_whatsapp(
        nombre_ejercicio, registro_repeticiones, analisis, carga_kg)
    reporte_tecnico = _construir_reporte_tecnico(
        nombre_ejercicio, registro_repeticiones, analisis, carga_kg)

    if numero_usuario:
        _notificar_whatsapp_seguro(reporte_whatsapp, numero_usuario, callback_notificacion)

    contexto_base = _generar_contexto_llm(
        reps_completadas=reps_ok,
        velocidades_globales=maquina.vels_serie,
        registro=registro_repeticiones,
        ejercicio=nombre_ejercicio,
        nivel_atleta=nivel_atleta,
        numero_usuario=numero_usuario,
        analisis=analisis,
        carga_kg=carga_kg,
    )
    contexto_agente = (f"EJERCICIO DE ESTA SERIE: "
                       f"{nombre_ejercicio.upper()}\n\n{contexto_base}")

    # Conversión final para iOS/Web
    _convertir_video_h264(ruta_temporal, ruta_salida)

    v_prom = round(float(np.mean([r.v_avg for r in registro_repeticiones])), 3)

    # 10. Retorno estructurado: claves originales intactas + claves v3
    return {
        "estado": "exito",
        "reps": reps_ok,
        "v_promedio_global": v_prom,
        "detalle_repeticiones": [r.a_dict() for r in registro_repeticiones],
        "ruta_video_renderizado": ruta_salida,
        "contexto_agente": contexto_agente,
        # --- v3 ---
        "analisis_serie": analisis,
        "reporte_whatsapp": reporte_whatsapp,
        "reporte_tecnico": reporte_tecnico,
        "carga_kg": carga_kg,
        "e1rm_estimado_kg": analisis.get("e1rm_kg"),
        "vl_pct": analisis.get("vl_pct"),
        "reps_en_reserva": analisis.get("reps_restantes"),
    }


# ==============================================================================
# DEMO rápida del HUD:  python motor_vbt.py  → genera hud_demo.png
# ==============================================================================
if __name__ == "__main__":
    demo = np.full((1080, 1920, 3), 60, np.uint8)
    demo[:] = (70, 60, 50)
    cv2.circle(demo, (1200, 540), 320, (90, 80, 70), -1)  # 'atleta' de mentira

    traj = [(1200 + int(24 * np.sin(i / 5)), 760 - i * 9) for i in range(45)]
    vels = list(np.clip(np.linspace(0.9, 0.35, 45) + np.random.rand(45) * .05, 0, 1))

    render_hud(
        demo, reps=6, fase="concentrica", velocidad=0.62, fatiga=0.31,
        historial_vel=[0.88, 0.85, 0.79, 0.74, 0.68, 0.62],
        bbox=(980, 260, 1430, 900), trayectoria=traj, vel_trayectoria=vels,
    )
    cv2.imwrite("hud_demo.png", demo)
    print("Demo guardada en hud_demo.png")