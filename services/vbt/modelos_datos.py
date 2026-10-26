# -*- coding: utf-8 -*-
"""
services/vbt/modelos_datos.py — Estructuras y clases de datos para detecciones y repeticiones VBT.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


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
    t_amortiguacion_s: Optional[float] = None  # transición isométrica (None si la rep arrancó desde abajo, sin fondo)
    # --- v3: bar path ---
    desv_horizontal_cm: float = 0.0     # deriva lateral máx. en la concéntrica
    eficiencia_barra_pct: float = 100.0  # recorrido vertical / camino real
    # --- v3: ventana temporal de la rep (para cortar clips / cruzar con pose)
    t_inicio_s: float = 0.0
    t_fin_s: float = 0.0
    vl_pct: float = 0.0             # pérdida vs la rep más rápida de la serie
    sticking_s: float = 0.0         # tiempo total con la barra casi parada a media subida (grind real, no ruido)
    # --- v3: cinemática articular (solo si hay modelo de pose) ---
    angulo_rodilla_min: Optional[float] = None      # grados (profundidad)
    angulo_cadera_min: Optional[float] = None       # grados
    vel_angular_rodilla_max: Optional[float] = None  # grados/s en la subida

    def a_dict(self) -> dict:
        return asdict(self)
