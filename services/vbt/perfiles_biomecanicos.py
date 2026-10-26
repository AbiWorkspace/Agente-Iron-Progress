# -*- coding: utf-8 -*-
"""
services/vbt/perfiles_biomecanicos.py — Perfiles por ejercicio, zonas de intensidad y fórmulas biomecánicas VBT.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Union

import numpy as np


# ==============================================================================
# PERFILES DE EJERCICIO — MVT, pendiente carga-velocidad y zonas de intensidad
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
    """RIR ≈ (v_última − MVT) / pendiente carga-velocidad del ejercicio."""
    if v_ultima_rep is None or v_ultima_rep <= 0:
        return None
    mvt = mvt_override if mvt_override else perfil.mvt[_normalizar_nivel(nivel)]
    if v_ultima_rep <= mvt:
        return 0.0
    rir = (v_ultima_rep - mvt) / perfil.pendiente_rir
    return float(round(min(rir, 8.0), 1))


def estimar_rir_por_vl(vl_pct: Optional[float],
                       n_reps: int = 0) -> Optional[str]:
    """Rango de RIR según la pérdida de velocidad intra-serie."""
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
    """%1RM aproximado desde la velocidad de la 1ª rep con el modelo lineal del perfil."""
    if not v_primera_rep or v_primera_rep <= 0:
        return None
    mvt = mvt_override if mvt_override else perfil.mvt[_normalizar_nivel(nivel)]
    v80 = perfil.zona_fuerza_vel[0]
    pendiente = (v80 - mvt) / 20.0        # m/s por cada 1% de 1RM
    if pendiente <= 0:
        return None
    pct = 100.0 - (v_primera_rep - mvt) / pendiente
    return float(round(min(max(pct, 30.0), 100.0), 1))


def estimar_1rm_diario(carga_kg: Optional[float], v_primera_rep: Optional[float],
                       perfil: PerfilEjercicio, nivel: str) -> Optional[float]:
    """e1RM del día = carga / (%1RM estimado). Requiere conocer la carga en kg."""
    if not carga_kg or carga_kg <= 0:
        return None
    pct = estimar_pct_1rm(v_primera_rep, perfil, nivel)
    if not pct:
        return None
    return float(round(max(carga_kg / (pct / 100.0), carga_kg), 1))


def estimar_e1rm_serie(carga_kg: Optional[float], v_primera: Optional[float],
                       v_min_serie: Optional[float], n_reps: int,
                       perfil: PerfilEjercicio, nivel: str) -> dict:
    """e1RM del día combinando perfil de velocidad y piso por repeticiones (Epley)."""
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
        e1rm = max(e1rm, carga_kg or 0.0)
        e1rm = float(round(e1rm, 1))

    pct_final = pct_vel
    if carga_kg and e1rm:
        pct_final = float(round(min(100.0 * carga_kg / e1rm, 100.0), 1))

    return {"e1rm_kg": e1rm, "pct_1rm": pct_final, "origen": origen,
            "mvt_efectivo": float(round(mvt_ef, 3)),
            "mvt_tabla": float(mvt_tabla)}


def estimar_reps_restantes(rir_velocidad: Optional[float],
                           rir_vl_rango: Optional[str]) -> Optional[dict]:
    """Reconcilia los dos estimadores de RIR en un rango entero de repeticiones."""
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
    """Zona de carga a partir del %1RM final."""
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
    """Pérdida de velocidad %: repetición MÁS RÁPIDA de la serie vs la última."""
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
    """Convierte tipos numpy a tipos nativos para serialización JSON segura."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    return obj
