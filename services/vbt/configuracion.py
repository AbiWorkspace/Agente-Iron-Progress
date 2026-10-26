# -*- coding: utf-8 -*-
"""
services/vbt/configuracion.py — Configuración central, umbrales y calibración del motor VBT.
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Optional

# ==============================================================================
# LOGGING CENTRALIZADO
# ==============================================================================
logger = logging.getLogger("motor_vbt")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(levelname)s][VBT] %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)

# ==============================================================================
# CONSTANTES FÍSICAS Y CONFIGURACIÓN CENTRAL
# ==============================================================================
GRAVEDAD: float = 9.81


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
# COMPATIBILIDAD ULTRALYTICS: flag de inferencia FP16 según versión instalada
# ==============================================================================
_KWARGS_FP16: Optional[dict] = None


def _kwargs_fp16(metodo) -> dict:
    """Devuelve {} , {'quantize': 'fp16'} o {'half': True} según la versión instalada de Ultralytics."""
    global _KWARGS_FP16
    if _KWARGS_FP16 is not None:
        return _KWARGS_FP16

    kwargs: dict = {}
    try:
        params = inspect.signature(metodo).parameters
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
        logger.debug(f"No se pudo detectar el flag FP16 de Ultralytics: {e}")

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
