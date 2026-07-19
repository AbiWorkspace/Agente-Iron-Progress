# -*- coding: utf-8 -*-
"""
Motor VBT (Velocity Based Training) — Iron Progress
====================================================
Refactor enfocado en precisión, robustez y funcionalidades orientadas al
objetivo: contar repeticiones, medir velocidad de la serie y de cada rep,
y estimar RIR / RPE + métricas útiles para programación.

Mejoras clave respecto a la versión anterior:
  1. VELOCIDAD CON SIGNO: se distingue subida (+) de bajada (−). Antes se usaba
     abs(), por lo que la fase excéntrica contaminaba las velocidades concéntricas.
  2. FILTRO SAVITZKY-GOLAY REALMENTE APLICADO: en el código original se calculaba
     el suavizado pero la velocidad se seguía computando con la posición cruda.
     Ahora la derivada se obtiene directamente del filtro (deriv=1), mucho más
     estable frente al ruido de detección de YOLO.
  3. MPV (Mean Propulsive Velocity): métrica estándar de la literatura VBT
     (González-Badillo & Sánchez-Medina). Se corta la fase propulsiva cuando la
     desaceleración supera la gravedad (a < −9.81 m/s²).
  4. ESTIMACIÓN AUTOMÁTICA DE RIR/RPE: doble vía (velocidad de la última rep vs
     MVT del ejercicio, y pérdida de velocidad de la serie) calculada en el motor,
     no delegada por completo al LLM.
  5. ESTIMACIÓN DE ZONA DE INTENSIDAD (%1RM) a partir de la 1ª repetición.
  6. Se elimina la inflación artificial del pico (v_peak * 1.15): no se fabrican
     datos; el pico se calcula con media móvil de 3 frames sobre la señal real.
  7. Máquina de estados encapsulada (sin `locals()`/variables fantasma como
     `buffer_arranque`), reps cerradas por inactividad ahora incluyen rom_cm y
     tiempo_s (antes provocaban KeyError en el desglose).
  8. Arquitectura por clases + dataclasses + logging, misma API pública:
     `procesar_levantamiento_vbt(...)` es drop-in compatible.
"""
from __future__ import annotations

import logging
import os
import traceback
from collections import deque
from dataclasses import dataclass, asdict, field
from typing import Optional

import cv2
import numpy as np
from scipy.signal import savgol_filter
from ultralytics import YOLO
from moviepy import VideoFileClip

from data.meta_service import enviar_mensaje_whatsapp

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

    # Detección YOLO
    conf_deteccion: float = 0.15
    iou_nms: float = 0.45
    imgsz: int = 1024
    max_detecciones: int = 3                # >1 para permitir re-enganche por cercanía
    factor_reenganche: float = 1.5          # x alto de caja como radio máximo
    ema_alto_caja: float = 0.15             # Suavizado exponencial de la escala

    # Cinemática
    ventana_savgol: int = 7                 # Impar. ~0.23 s a 30 fps
    poly_savgol: int = 2
    vel_max_valida: float = 3.5             # m/s — techo físico razonable en barra
    umbral_ruido_prop: float = 0.08         # m/s — micro-pausas / ruido

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
    frac_linea_suelo: float = 0.75          # Ignora discos apilados en el piso


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
# ESTIMADORES DERIVADOS (RIR / RPE / %1RM / Velocity Loss)
# ==============================================================================
def estimar_rir_por_velocidad(v_ultima_rep: float, perfil: PerfilEjercicio,
                              nivel: str) -> Optional[float]:
    """RIR ≈ (v_última − MVT) / pendiente carga-velocidad del ejercicio."""
    if v_ultima_rep is None or v_ultima_rep <= 0:
        return None
    mvt = perfil.mvt[_normalizar_nivel(nivel)]
    if v_ultima_rep <= mvt:
        return 0.0
    rir = (v_ultima_rep - mvt) / perfil.pendiente_rir
    return float(round(min(rir, 8.0), 1))


def estimar_rir_por_vl(vl_pct: Optional[float]) -> Optional[str]:
    """Rango de RIR según la pérdida de velocidad intra-serie."""
    if vl_pct is None:
        return None
    if vl_pct < 10:
        return "4-6"
    if vl_pct < 20:
        return "2-3"
    if vl_pct < 30:
        return "1-2"
    return "0-1"


def estimar_zona_intensidad(v_primera_rep: float, perfil: PerfilEjercicio,
                            nivel: str) -> str:
    """Zona de %1RM estimada a partir de la velocidad de la primera repetición."""
    if v_primera_rep is None or v_primera_rep <= 0:
        return "Indeterminada"
    mvt = perfil.mvt[_normalizar_nivel(nivel)]
    v_sup_fv, v_inf_fv = perfil.zona_fuerza_vel
    if v_primera_rep > v_sup_fv:
        return "<80% 1RM (zona de potencia / fuerza-velocidad ligera)"
    if v_primera_rep >= v_inf_fv:
        return "80-89% 1RM (fuerza-velocidad)"
    if v_primera_rep > mvt:
        return "90-100% 1RM (fuerza máxima)"
    return "~100% 1RM (en o por debajo del MVT: intento máximo)"


def calcular_velocity_loss(registro: list) -> Optional[float]:
    """Pérdida de velocidad % entre la primera y la última repetición."""
    if len(registro) < 2:
        return None
    v1 = registro[0].v_avg
    vn = registro[-1].v_avg
    if v1 <= 0:
        return None
    return float(round(((v1 - vn) / v1) * 100.0, 1))


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
    v_avg: float              # media de la fase concéntrica (frames con empuje real)
    v_peak: float             # pico (media móvil de 3 frames, sin inflación artificial)
    mpv: float                # Mean Propulsive Velocity (corte cuando a < -g)
    rom_cm: float
    tiempo_s: float
    vl_pct: float = 0.0       # pérdida de velocidad vs la 1ª rep (se rellena al final)

    def a_dict(self) -> dict:
        return asdict(self)


# ==============================================================================
# RASTREADOR DE DISCO (YOLO + ByteTrack + re-enganche + escala estable)
# ==============================================================================
class RastreadorDisco:
    """Encapsula la detección/tracking del disco y la calibración px→metros."""

    def __init__(self, ruta_modelo: str, cfg: ConfigVBT = CONFIG):
        self.cfg = cfg
        self.modelo = YOLO(ruta_modelo)
        self.id_fijado: Optional[int] = None
        self.alto_ema: Optional[float] = None
        self.ultima_pos: Optional[tuple] = None

    # --------------------------------------------------------------
    def procesar(self, frame: np.ndarray) -> Optional[Deteccion]:
        alto_img = frame.shape[0]
        try:
            pred = self.modelo.track(
                frame,
                persist=True,
                tracker="bytetrack.yaml",
                half=True,
                agnostic_nms=True,
                conf=self.cfg.conf_deteccion,
                iou=self.cfg.iou_nms,
                max_det=self.cfg.max_detecciones,
                imgsz=self.cfg.imgsz,
                device=0,
                verbose=False,
            )
        except Exception as e:
            logger.debug(f"YOLO track falló en este frame: {e}")
            return None

        if pred[0].boxes.id is None:
            return None

        cajas = pred[0].boxes.xyxy.cpu().numpy()
        ids = pred[0].boxes.id.cpu().numpy().astype(int)

        caja = self._seleccionar_caja(cajas, ids, alto_img)
        if caja is None:
            return None

        x_min, y_min, x_max, y_max = caja
        alto_detectado = float(y_max - y_min)
        if alto_detectado <= 0:
            return None

        # EMA sobre el alto de caja: la escala no "parpadea" frame a frame
        if self.alto_ema and self.alto_ema > 0:
            self.alto_ema = (self.alto_ema * (1 - self.cfg.ema_alto_caja)
                             + alto_detectado * self.cfg.ema_alto_caja)
        else:
            self.alto_ema = alto_detectado

        m_por_px = self.cfg.diametro_disco_m / self.alto_ema
        cx = int((x_min + x_max) / 2)
        cy = int((y_min + y_max) / 2)
        self.ultima_pos = (cx, cy)

        return Deteccion(cx, cy, float(x_min), float(y_min), float(x_max),
                         float(y_max), self.alto_ema, m_por_px)

    # --------------------------------------------------------------
    def _seleccionar_caja(self, cajas: np.ndarray, ids: np.ndarray,
                          alto_img: int) -> Optional[np.ndarray]:
        # 1) El ID fijado sigue vivo -> lo seguimos
        if self.id_fijado is not None and self.id_fijado in ids:
            return cajas[int(np.where(ids == self.id_fijado)[0][0])]

        # 2) Re-enganche: el tracker perdió el ID, buscamos la caja más cercana
        if self.id_fijado is not None:
            if self.ultima_pos is None or len(cajas) == 0:
                return None
            radio_max = (self.alto_ema * self.cfg.factor_reenganche
                         if self.alto_ema else alto_img * 0.15)
            centros = np.column_stack([(cajas[:, 0] + cajas[:, 2]) / 2,
                                       (cajas[:, 1] + cajas[:, 3]) / 2])
            dists = np.linalg.norm(centros - np.array(self.ultima_pos), axis=1)
            i_mejor = int(np.argmin(dists))
            if dists[i_mejor] <= radio_max:
                self.id_fijado = int(ids[i_mejor])
                logger.info(f"Re-enganche de tracking a ID {self.id_fijado} "
                            f"(dist {dists[i_mejor]:.0f}px)")
                return cajas[i_mejor]
            return None

        # 3) Primera fijación: caja más grande por encima de la línea de suelo
        linea_suelo = alto_img * self.cfg.frac_linea_suelo
        mejor, area_max = None, 0.0
        for caja, id_caja in zip(cajas, ids):
            cy = (caja[1] + caja[3]) / 2
            if cy >= linea_suelo:
                continue  # probablemente un disco apilado en el piso
            area = (caja[2] - caja[0]) * (caja[3] - caja[1])
            if area > area_max:
                area_max, mejor = area, caja
                self.id_fijado = int(id_caja)
        return mejor


# ==============================================================================
# ESTIMADOR CINEMÁTICO (posición suavizada + velocidad con signo)
# ==============================================================================
class EstimadorCinematica:
    """
    Mantiene una ventana deslizante de (t, y) y devuelve:
      - y suavizada (Savitzky-Golay, deriv=0)
      - velocidad vertical CON SIGNO en m/s (SG deriv=1): + sube, − baja.
    Nota: SG asume muestreo uniforme; con VFR usamos el dt medio de la ventana,
    aproximación válida porque la ventana es corta (~0.2 s).
    """

    def __init__(self, cfg: ConfigVBT = CONFIG):
        self.cfg = cfg
        n = cfg.ventana_savgol
        self.ts: deque = deque(maxlen=n)
        self.ys: deque = deque(maxlen=n)

    def reiniciar(self):
        self.ts.clear()
        self.ys.clear()

    def actualizar(self, t: float, y_px: float, m_por_px: float) -> tuple:
        self.ts.append(float(t))
        self.ys.append(float(y_px))
        n = self.cfg.ventana_savgol

        if len(self.ys) < n:
            # Arranque: diferencia finita simple
            if len(self.ys) >= 2:
                dt = self.ts[-1] - self.ts[-2]
                v = (-(self.ys[-1] - self.ys[-2]) / dt * m_por_px) if dt > 0 else 0.0
            else:
                v = 0.0
            return float(self.ys[-1]), self._limitar(v)

        ys = np.asarray(self.ys)
        ts = np.asarray(self.ts)
        dts = np.diff(ts)
        dt_medio = float(np.mean(dts[dts > 0])) if np.any(dts > 0) else 1.0 / 30.0

        y_suave = savgol_filter(ys, n, self.cfg.poly_savgol)
        dy_dt = savgol_filter(ys, n, self.cfg.poly_savgol, deriv=1, delta=dt_medio)

        # Evaluamos en el penúltimo punto: la derivada SG en el borde es ruidosa.
        # Un frame de latencia (~33 ms) a cambio de una señal mucho más limpia.
        v = -float(dy_dt[-2]) * m_por_px
        return float(y_suave[-1]), self._limitar(v)

    def _limitar(self, v: float) -> float:
        tope = self.cfg.vel_max_valida
        return float(np.clip(v, -tope, tope))


# ==============================================================================
# MÁQUINA DE ESTADOS DE REPETICIONES
# ==============================================================================
class MaquinaEstadosRep:
    """
    Estados: calibrando -> reposo -> excentrica <-> concentrica.
    Trabaja con la posición Y suavizada y la velocidad con signo.
    Devuelve un objeto Repeticion cada vez que se cierra una rep válida.
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

        self.frames_estables = 0
        self.buffer_arranque: list = []      # [(t, v)] previos al despegue en reposo
        self.vels_concentrica: list = []     # [(t, v)] de la fase concéntrica actual
        self.t_inicio_concentrica = 0.0
        self.escala_fija_m_px = 0.005        # fallback conservador
        self._ultimo_alto_caja: Optional[float] = None

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

    def _reiniciar_a_reposo(self, y: float):
        self.estado = "reposo"
        self.y_inicial = self.inicio_fase_y = self.y_mas_bajo = self.y_tope = y
        self.buffer_arranque.clear()

    # --------------------------------------------------------------
    def actualizar(self, t: float, y: float, v: float,
                   alto_caja: Optional[float]) -> Optional[Repeticion]:
        self._ultimo_alto_caja = alto_caja
        rom_min, reversa, compromiso = self._umbrales(alto_caja)

        if self.y_inicial is None:
            self.y_inicial = self.y_mas_bajo = self.y_tope = self.inicio_fase_y = y

        self.max_descenso_px = max(self.max_descenso_px, abs(y - self.y_inicial))
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
                self.estado = self.fase_maxima = "excentrica"
                self.y_mas_bajo = y
                self.inicio_fase_y = self.y_tope = self.y_inicial

        elif self.estado == "reposo":
            # Buffer de arranque: si el press/dominada empieza directo hacia arriba
            if v > self.cfg.vel_min_registro:
                self.buffer_arranque.append((t, v))
            else:
                self.buffer_arranque.clear()

            if y > (self.y_inicial + compromiso):
                self.estado = self.fase_maxima = "excentrica"
                self.y_mas_bajo = y
                self.inicio_fase_y = self.y_inicial
            elif y < (self.y_inicial - compromiso):
                self.estado = self.fase_maxima = "concentrica"
                self.y_tope = y
                self.inicio_fase_y = self.y_inicial
                self.t_inicio_concentrica = (self.buffer_arranque[0][0]
                                             if self.buffer_arranque else t)
                self.vels_concentrica = list(self.buffer_arranque)

        elif self.estado == "excentrica":
            self.y_mas_bajo = max(self.y_mas_bajo, y)
            if y < (self.y_mas_bajo - reversa):
                if abs(self.y_mas_bajo - self.inicio_fase_y) >= rom_min:
                    self.estado = self.fase_maxima = "concentrica"
                    self.y_tope = y
                    self.inicio_fase_y = self.y_mas_bajo
                    self.t_inicio_concentrica = t
                    self.vels_concentrica = []
                else:
                    self._reiniciar_a_reposo(y)  # rebote pequeño = ruido

        elif self.estado == "concentrica":
            if v > 0:  # solo empuje real: la velocidad negativa no es concéntrica
                self.vels_concentrica.append((t, v))
                self.vels_serie.append(v)
            self.y_tope = min(self.y_tope, y)

            if y > (self.y_tope + reversa):
                if abs(self.y_tope - self.inicio_fase_y) >= rom_min:
                    rep_cerrada = self._cerrar_repeticion(t)
                    self.estado = "excentrica"
                    self.y_mas_bajo = y
                    self.inicio_fase_y = self.y_tope
                else:
                    self._reiniciar_a_reposo(y)

        return rep_cerrada

    # --------------------------------------------------------------
    def cerrar_por_inactividad(self, t: float) -> Optional[Repeticion]:
        """Cierra la última rep si el video termina bloqueado arriba (lockout)."""
        if self.estado != "concentrica" or len(self.vels_concentrica) <= 3:
            return None
        rom_min, _, _ = self._umbrales(self._ultimo_alto_caja)
        if abs(self.y_tope - self.inicio_fase_y) < rom_min:
            return None
        return self._cerrar_repeticion(t)

    # --------------------------------------------------------------
    def _cerrar_repeticion(self, t_fin: float) -> Repeticion:
        recorrido_px = abs(self.y_tope - self.inicio_fase_y)
        rom_m = recorrido_px * self.escala_fija_m_px
        duracion = max(t_fin - self.t_inicio_concentrica, 1e-3)

        ts = np.array([p[0] for p in self.vels_concentrica], dtype=float)
        vs = np.array([p[1] for p in self.vels_concentrica], dtype=float)

        validas = vs[vs > self.cfg.umbral_ruido_prop]
        if validas.size:
            v_avg = float(np.mean(validas))
        else:
            v_avg = rom_m / duracion  # fallback cinemático puro

        if vs.size >= 3:
            v_peak = float(np.max(np.convolve(vs, np.ones(3) / 3, mode="valid")))
        elif vs.size:
            v_peak = float(np.max(vs))
        else:
            v_peak = v_avg

        mpv = self._calcular_mpv(ts, vs, fallback=v_avg)

        self.reps += 1
        return Repeticion(
            rep=self.reps,
            v_avg=round(v_avg, 3),
            v_peak=round(max(v_peak, v_avg), 3),
            mpv=round(mpv, 3),
            rom_cm=round(rom_m * 100.0, 1),
            tiempo_s=round(duracion, 2),
        )

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
            acel = np.gradient(vs, ts)
        except Exception:
            return fallback

        i_pico = int(np.argmax(vs))
        corte = vs.size
        for i in range(i_pico, vs.size):
            if acel[i] < -GRAVEDAD:
                corte = i
                break

        propulsiva = vs[:max(corte, 1)]
        propulsiva = propulsiva[propulsiva > self.cfg.umbral_ruido_prop]
        return float(np.mean(propulsiva)) if propulsiva.size else fallback

# ==============================================================================
# SPORT HUB HUD · Renderizado profesional sobre video (OpenCV)
# ------------------------------------------------------------------------------
# Mejoras clave respecto a la versión anterior:
#   1. Anti-aliasing (cv2.LINE_AA) en TODO — es la diferencia #1 entre
#      "script de OpenCV" y "broadcast graphics".
#   2. Paneles semitransparentes (alpha blending) en vez de rectángulos sólidos.
#   3. Esquinas redondeadas reales en paneles, pastillas y barras.
#   4. Escalado relativo a la resolución del frame (se ve igual en 720p y 4K).
#   5. Glow sutil en acentos (doble trazo grueso translúcido + trazo fino).
#   6. Barra de progreso con marcador de umbral y cabeza redondeada.
#   7. Sparkline de historial de velocidad (contexto temporal, muy "pro").
#   8. Estela de trayectoria con desvanecimiento y color por velocidad.
#   9. Tipografía jerarquizada: etiquetas pequeñas en gris, valores grandes.
# ==============================================================================


import cv2
import numpy as np

# ── Paleta centralizada (BGR) ─────────────────────────────────────────────────
_HUB = {
    "bg":      (24, 18, 12),    # carbón profundo (fondo del panel)
    "bg_soft": (34, 27, 20),    # carbón elevado (tarjetas internas)
    "border":  (70, 62, 52),    # borde exterior sutil
    "sep":     (52, 46, 40),    # separadores internos / fondo de barras
    "accent":  (255, 220, 0),   # cian eléctrico  (B=255, G=220, R=0)
    "green":   (150, 240, 0),   # verde neón
    "amber":   (0, 170, 255),   # ámbar
    "red":     (55, 55, 255),   # rojo alerta
    "white":   (245, 246, 250), # texto principal
    "muted":   (150, 140, 120), # etiquetas secundarias
    "shadow":  (0, 0, 0),
}

# NOTA: en tu versión original "accent" era (0, 220, 255) — en BGR eso es
# NARANJA, no cian. Cian eléctrico en BGR es (255, 220, 0). Corregido aquí.

_FASES = {
    "concentrica": (_HUB["green"], "CONCENTRICA", 1),   # 1 = flecha arriba
    "excentrica":  (_HUB["amber"], "EXCENTRICA", -1),   # -1 = flecha abajo
    "eccentrica":  (_HUB["amber"], "EXCENTRICA", -1),
    "reposo":      (_HUB["muted"], "REPOSO", 0),
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
    """Panel semitransparente estilo glassmorphism oscuro.
    El alpha blending es lo que hace que el HUD 'flote' sobre el video
    en vez de taparlo con un bloque sólido."""
    r = _s(frame, radius)
    overlay = frame.copy()
    _rounded_rect(overlay, x1, y1, x2, y2, r, _HUB["bg"], -1)
    cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

    _rounded_rect(frame, x1, y1, x2, y2, r, _HUB["border"], _s(frame, 1))

    if accent_top:
        # Filete de acento superior con glow sutil
        glow = frame.copy()
        cv2.line(glow, (x1 + r, y1), (x2 - r, y1), _HUB["accent"], _s(frame, 5), cv2.LINE_AA)
        cv2.addWeighted(glow, 0.35, frame, 0.65, 0, frame)
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
    """Marcador táctico de esquinas con glow: el trazo grueso translúcido
    debajo del fino crea el efecto 'neón' sin postprocesado."""
    color = color or _HUB["accent"]
    a, t = _s(frame, arm), _s(frame, thick)
    segs = []
    for (cx, cy, sx, sy) in [(x1, y1, 1, 1), (x2, y1, -1, 1),
                             (x1, y2, 1, -1), (x2, y2, -1, -1)]:
        segs.append(((cx, cy), (cx + sx * a, cy)))
        segs.append(((cx, cy), (cx, cy + sy * a)))
    glow = frame.copy()
    for p1, p2 in segs:
        cv2.line(glow, p1, p2, color, t + _s(frame, 4), cv2.LINE_AA)
    cv2.addWeighted(glow, 0.30, frame, 0.70, 0, frame)
    for p1, p2 in segs:
        cv2.line(frame, p1, p2, color, t, cv2.LINE_AA)


def _bar(frame, x, y, w, h, ratio, color_fg, threshold=None):
    """Barra de progreso con extremos redondeados, cabeza brillante y
    marcador opcional de umbral (línea vertical)."""
    r = h // 2
    _rounded_rect(frame, x, y, x + w, y + h, r, _HUB["sep"], -1)
    ratio = float(np.clip(ratio, 0.0, 1.0))
    fill = int(ratio * w)
    if fill > h:
        _rounded_rect(frame, x, y, x + fill, y + h, r, color_fg, -1)
        # Cabeza más clara: da sensación de 'movimiento'
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
    """Pastilla de fase con triángulo vectorial (▲/▼ de Hershey se ven mal)."""
    color, label, direc = _FASES.get(fase, _FASES["reposo"])
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
    """Mini-gráfico del historial de velocidad. Da contexto temporal
    inmediato: se ve si la serie está cayendo (fatiga) de un vistazo."""
    if len(values) < 2:
        return
    color = color or _HUB["accent"]
    vals = np.asarray(values, dtype=np.float32)
    vmin, vmax = float(vals.min()), float(vals.max())
    rng = (vmax - vmin) or 1.0
    xs = np.linspace(x, x + w, len(vals)).astype(int)
    ys = (y + h - ((vals - vmin) / rng) * h).astype(int)
    pts = np.stack([xs, ys], axis=1)

    if fill:
        poly = np.vstack([pts, [[x + w, y + h], [x, y + h]]])
        overlay = frame.copy()
        cv2.fillPoly(overlay, [poly], color, cv2.LINE_AA)
        cv2.addWeighted(overlay, 0.18, frame, 0.82, 0, frame)

    cv2.polylines(frame, [pts], False, color, _s(frame, 2), cv2.LINE_AA)
    # Punto actual con halo
    cx, cy = int(xs[-1]), int(ys[-1])
    glow = frame.copy()
    cv2.circle(glow, (cx, cy), _s(frame, 7), color, -1, cv2.LINE_AA)
    cv2.addWeighted(glow, 0.35, frame, 0.65, 0, frame)
    cv2.circle(frame, (cx, cy), _s(frame, 3), _HUB["white"], -1, cv2.LINE_AA)


def _trail(frame, puntos, velocidades=None, max_pts=40):
    """Estela de la trayectoria de la barra: se desvanece hacia el pasado
    y se colorea por velocidad. Dibujar sobre overlay + blend por segmento
    sería carísimo; un solo overlay con grosor decreciente rinde bien."""
    if len(puntos) < 2:
        return
    pts = puntos[-max_pts:]
    vels = (velocidades or [0.6] * len(pts))[-max_pts:]
    n = len(pts)
    overlay = frame.copy()
    for i in range(1, n):
        t = i / n                          # 0 = viejo, 1 = reciente
        col = _color_por_velocidad(vels[i])
        col = tuple(int(c * (0.35 + 0.65 * t)) for c in col)
        th = max(1, int(_s(frame, 6) * t))
        cv2.line(overlay, tuple(map(int, pts[i - 1])), tuple(map(int, pts[i])),
                 col, th, cv2.LINE_AA)
    cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
    # Punto actual con glow
    cx, cy = map(int, pts[-1])
    col = _color_por_velocidad(vels[-1])
    glow = frame.copy()
    cv2.circle(glow, (cx, cy), _s(frame, 12), col, -1, cv2.LINE_AA)
    cv2.addWeighted(glow, 0.35, frame, 0.65, 0, frame)
    cv2.circle(frame, (cx, cy), _s(frame, 5), col, -1, cv2.LINE_AA)
    cv2.circle(frame, (cx, cy), _s(frame, 5), _HUB["white"], _s(frame, 1), cv2.LINE_AA)


# ==============================================================================
# COMPOSICIÓN: panel principal listo para usar
# ==============================================================================

def render_hud(frame, *, reps=0, fase="reposo", velocidad=0.0,
               fatiga=0.0, historial_vel=(), titulo="SPORT HUB",
               bbox=None, trayectoria=(), vel_trayectoria=()):
    """Dibuja el HUD completo. Llamar una vez por frame.

    Args:
        frame: imagen BGR (se modifica in-place).
        reps: repeticiones contadas.
        fase: 'concentrica' | 'excentrica' | 'reposo'.
        velocidad: velocidad media normalizada 0-1 de la rep actual.
        fatiga: pérdida de velocidad 0-1 (0 = fresco).
        historial_vel: lista de velocidades por rep (para el sparkline).
        titulo: marca en la cabecera.
        bbox: (x1, y1, x2, y2) opcional para marcador táctico del atleta.
        trayectoria: lista de (x, y) del recorrido de la barra.
        vel_trayectoria: velocidad instantánea por punto (misma longitud).
    """
    H, W = frame.shape[:2]
    m = _s(frame, 28)                       # margen exterior
    pw, ph = _s(frame, 330), _s(frame, 320) # tamaño del panel
    x1, y1 = m, m
    x2, y2 = x1 + pw, y1 + ph
    pad = _s(frame, 22)

    # Estela y marcador sobre el video (debajo del panel)
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


# ==============================================================================
# DEMO rápida:  python sport_hub_hud.py  → genera hud_demo.png
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

# ── Función principal ─────────────────────────────────────────────────────────

def renderizar_frame(frame, det: Optional['Deteccion'], trayectoria: deque,
                     v_inst: float, reps: int, fase: str,
                     registro: list, vl_actual: Optional[float]):
    # ── 1. TRAYECTORIA DEGRADADA (cian oscuro → verde neón) ───────────────────
    n = len(trayectoria)
    for j in range(1, n):
        t = j / n
        col = (0, int(140 + 100 * t), int(190 + 65 * t))
        grosor = 1 + int(t > 0.6)
        cv2.line(frame, trayectoria[j - 1], trayectoria[j], col, grosor)

    # ── 2. MARCADOR DE DISCO ──────────────────────────────────────────────────
    if det is not None:
        bx1, by1 = int(det.x_min), int(det.y_min)
        bx2, by2 = int(det.x_max), int(det.y_max)

        _corners(frame, bx1, by1, bx2, by2, _HUB["accent"])  # esquinas HUD
        cv2.circle(frame, (det.cx, det.cy), 4, _HUB["accent"], -1)  # centro
        cv2.circle(frame, (det.cx, det.cy), 8, _HUB["accent"], 1)  # halo

        vel = abs(v_inst)
        # Asegúrate de tener tu función _color_por_velocidad(vel) definida
        col_vel = _color_por_velocidad(vel) if '_color_por_velocidad' in globals() else _HUB["white"]
        _txt(frame, f"{vel:.2f} m/s",
             (det.cx + 50, det.cy + 6),
             scale=0.65, color=col_vel, shadow=True)

    # ── 3. PANEL HUD CORREGIDO ────────────────────────────────────────────────
    PX, PY = 16, 16
    PW, PH = 295, 300  # <--- Aumentado de 228 a 300 para arreglar la superposición

    # 3a. Fondo semi-transparente
    overlay = frame.copy()
    cv2.rectangle(overlay, (PX, PY), (PX + PW, PY + PH), _HUB["bg"], -1)
    cv2.addWeighted(overlay, 0.76, frame, 0.24, 0, frame)

    # 3b. Bordes del panel
    cv2.rectangle(frame, (PX, PY), (PX + PW, PY + PH), _HUB["border"], 1)
    cv2.line(frame, (PX + 10, PY), (PX + PW - 10, PY), _HUB["accent"], 2)
    cv2.line(frame, (PX + 10, PY + PH), (PX + PW - 10, PY + PH), _HUB["sep"], 1)

    # ── NUEVO: MARCA IRON PROGRESS ────────────────────────────────────────────
    _txt(frame, "IRON PROGRESS", (PX + 18, PY + 24),
         scale=0.45, color=_HUB["accent"], shadow=True, thickness=1)
    cv2.line(frame, (PX + 12, PY + 34), (PX + PW - 12, PY + 34), _HUB["sep"], 1)

    # ── 3c. REPETICIONES ──────────────────────────────────────────────────────
    _txt(frame, "REPETICIONES", (PX + 18, PY + 52),
         scale=0.34, color=_HUB["muted"])
    _txt(frame, str(reps), (PX + 16, PY + 106),
         scale=2.5, color=_HUB["green"], thickness=2, shadow=True)

    cv2.line(frame, (PX + 12, PY + 120), (PX + PW - 12, PY + 120), _HUB["sep"], 1)

    # ── 3d. FASE ──────────────────────────────────────────────────────────────
    color_fase, label_fase, _ = _FASES.get(fase.lower(), (_HUB["muted"], f"  {fase.upper()}", 0))
    _pill(frame, label_fase, PX + 18, PY + 145, color_fase)

    cv2.line(frame, (PX + 12, PY + 160), (PX + PW - 12, PY + 160), _HUB["sep"], 1)

    # ── 3e. VELOCIDAD ÚLTIMA REP + barra ──────────────────────────────────────
    if registro:
        v_last = registro[-1].v_avg
        col_last = _color_por_velocidad(v_last) if '_color_por_velocidad' in globals() else _HUB["white"]

        _metric(frame, "Ultima rep",
                f"{v_last:.2f} m/s",
                PX + 18, PY + 178,
                vcolor=col_last)
        _bar(frame, PX + 18, PY + 208, PW - 36, 6, v_last / 1.5, col_last)

    cv2.line(frame, (PX + 12, PY + 224), (PX + PW - 12, PY + 224), _HUB["sep"], 1)

    # ── 3f. PÉRDIDA DE VELOCIDAD (fatiga) + barra ─────────────────────────────
    if vl_actual is not None:
        if vl_actual < 20:
            color_vl, estado = _HUB["green"], "OPTIMO"
        elif vl_actual < 30:
            color_vl, estado = _HUB["amber"], "ALERTA"
        else:
            color_vl, estado = _HUB["red"], "FATIGA"

        _metric(frame, "Perdida de velocidad",
                f"{vl_actual:.1f}%   {estado}",
                PX + 18, PY + 242,
                vcolor=color_vl, vscale=0.60)

        _bar(frame, PX + 18, PY + 272, PW - 36, 6, vl_actual / 50, color_vl)


# ==============================================================================
# UTILIDADES DE VIDEO
# ==============================================================================
def inicializar_video(ruta_video: str, numero_usuario: str):
    try:
        enviar_mensaje_whatsapp(texto="Procesando video...",
                                telefono_destino=numero_usuario)
    except Exception as e:
        logger.warning(f"No se pudo notificar inicio por WhatsApp: {e}")

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


def _inicializar_escritor_video(ruta_salida: str, ancho: int, alto: int, fps: float):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    directorio, nombre = os.path.split(ruta_salida)
    ruta_temporal = os.path.join(directorio, "temp_" + nombre)
    writer = cv2.VideoWriter(ruta_temporal, fourcc, fps, (ancho, alto))
    if not writer.isOpened():
        raise ValueError(f"No se pudo crear el escritor de video en {ruta_temporal}")
    return writer, ruta_temporal


def _convertir_video_h264(ruta_temporal: str, ruta_salida: str):
    logger.info("Convirtiendo video a H.264 (compatible con móvil)...")
    try:
        clip = VideoFileClip(ruta_temporal)
        clip.write_videofile(ruta_salida, codec="libx264", audio=False, logger=None)
        clip.close()
        if os.path.exists(ruta_temporal):
            os.remove(ruta_temporal)
    except Exception as e:
        logger.error(f"Conversión H.264 falló: {e}")
        if os.path.exists(ruta_temporal):
            os.replace(ruta_temporal, ruta_salida)


# ==============================================================================
# DIAGNÓSTICO (autopsia cuando no se detectan reps)
# ==============================================================================
def _generar_autopsia_vbt(totales: int, con_disco: int, fase: str,
                          max_descenso: float, max_vel: float) -> str:
    if totales == 0:
        razon = "El video está corrupto o tiene 0 frames leíbles."
    elif con_disco == 0:
        razon = "YOLO no detectó el disco en ningún momento."
    elif fase in ("calibrando", "reposo"):
        razon = f"Movimiento insuficiente: descenso máximo de {max_descenso:.1f}px."
    elif fase == "excentrica":
        razon = f"La barra bajó pero no subió. Vel máx: {max_vel:.3f} m/s."
    elif fase == "concentrica":
        razon = "La barra subió, pero la repetición no cerró correctamente."
    else:
        razon = f"Fase final atípica: {fase}."
    return (f"Fallo VBT.\nCAUSA: {razon}\n"
            f"TELEMETRÍA -> Frames: {totales} | Tracked: {con_disco} | "
            f"Descenso Max: {max_descenso:.1f}px")


# ==============================================================================
# CONTEXTO PARA EL LLM + REPORTE WHATSAPP
# ==============================================================================
def _generar_contexto_llm(reps_completadas: int, velocidades_globales: list,
                          registro: list, ejercicio: str,
                          nivel_atleta: str = "Avanzado",
                          numero_usuario: str = None) -> str:
    perfil = obtener_perfil_ejercicio(ejercicio)
    nivel = _normalizar_nivel(nivel_atleta)

    es_single = reps_completadas == 1
    vl_pct = calcular_velocity_loss(registro)
    fatiga_str = (f"{vl_pct:.1f}%" if vl_pct is not None
                  else ("N/A (Single: no hay degradación intra-serie)"
                        if es_single else "0.0%"))

    v_primera = registro[0].v_avg if registro else 0.0
    v_ultima = registro[-1].v_avg if registro else 0.0

    rir_vel = estimar_rir_por_velocidad(v_ultima, perfil, nivel)
    rir_vl = estimar_rir_por_vl(vl_pct)
    zona_1rm = estimar_zona_intensidad(v_primera, perfil, nivel)
    rpe_estimado = (round(max(10.0 - rir_vel, 5.0), 1)
                    if rir_vel is not None else None)

    detalle_reps_str = "".join(
        f"*Rep {r.rep}* -> Media: *{r.v_avg:.3f} m/s* | "
        f"Pico: _{r.v_peak:.3f} m/s_ | MPV: {r.mpv:.3f} m/s | "
        f"ROM: {r.rom_cm:.1f} cm | {r.tiempo_s:.2f}s | VL: {r.vl_pct:.1f}%\n"
        for r in registro
    )

    # -------- Envío inmediato del desglose bruto a WhatsApp --------
    rir_txt = (f"~{rir_vel:.0f} (RPE ~{rpe_estimado})"
               if rir_vel is not None else "N/D")
    mensaje_whatsapp = (
        f"*📊 REPORTE VBT: {ejercicio.upper()}*\n\n"
        f"*Reps completadas:* {reps_completadas}\n"
        f"*Fatiga (Velocity Loss):* {fatiga_str}\n"
        f"*RIR estimado:* {rir_txt}\n"
        f"*Zona de carga:* {zona_1rm}\n\n"
        f"*DESGLOSE:*\n{detalle_reps_str}"
    )
    try:
        enviar_mensaje_whatsapp(texto=mensaje_whatsapp,
                                telefono_destino=numero_usuario)
    except Exception as e:
        logger.error(f"No se pudo enviar el desglose por WhatsApp: {e}")

    # -------- Súper-prompt VBT para el LLM --------
    metricas_motor = f"""[MÉTRICAS YA CALCULADAS POR EL MOTOR — ÚSALAS COMO ANCLA, NO LAS CONTRADIGAS]
- Zona de intensidad estimada (1ª rep): {zona_1rm}
- RIR estimado por velocidad terminal: {rir_txt}
- RIR estimado por pérdida de velocidad: {rir_vl if rir_vl else 'N/D'}
- MPV de la serie: {np.mean([r.mpv for r in registro]):.3f} m/s (media)"""

    contexto_final = f"""Eres el motor analítico de Iron Progress, un coach experto especializado en Velocity Based Training (VBT). Tu función es analizar la cinemática de la serie con máxima rigurosidad científica internamente, pero EXPRESAR tus conclusiones en un lenguaje de gimnasio motivador, directo y muy fácil de entender para el levantador.

[TELEMETRÍA DE LA SERIE]
- Ejercicio: {ejercicio.upper()} (perfil VBT aplicado: {perfil.nombre})
- Perfil del Atleta: {nivel_atleta} (Alta eficiencia neural y capacidad de "grind")
- Repeticiones Completadas: {reps_completadas}
- Pérdida de Velocidad (VL %): {fatiga_str}

[DESGLOSE CINEMÁTICO POR REPETICIÓN]
{detalle_reps_str}
{metricas_motor}

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
Analiza los datos internamente usando las 3 bases de conocimiento y las métricas ya calculadas por el motor, pero REDACTA el reporte como un coach humano hablando directamente con el atleta. PROHIBIDO usar términos como "fenotipo", "neurogénica", "isquemia", "MVT" o "MPV" en la respuesta final. Usa el siguiente formato amigable:

1. INTENSIDAD DE LA CARGA: ¿Qué tan pesado estuvo? (Ej. "Esa barra iba súper pesada, estás muy cerca de tu máximo" o "Se vio explosivo, traes un peso manejable para potencia").
2. FATIGA Y ESFUERZO: ¿Qué tan cerca del fallo se quedó? Traduce el VL% y el RIR estimado a palabras simples. (Ej. "Dejaste unas cuantas repeticiones en el tanque" o "Llegaste al límite total, exprimiste todo").
3. OBJETIVO LOGRADO: Traduce el tipo de adaptación. (Ej. "Con esta serie construimos fuerza bruta sin freírte" o "Acabas de mandar un estímulo brutal para hipertrofia").
4. QUÉ HACER AHORA: Dale la orden directa para la siguiente serie. (Ej. "Tienes margen, métele un par de kilos más", "Quédate con este peso, la velocidad es perfecta", o "Descansa bien o bájale al peso, tu cuerpo está pidiendo esquina").

Usa ÚNICAMENTE los 1RMs y pesos que el sistema te proporciona en el bloque de telemetría. Si un peso no aparece explícitamente en los datos del sistema, escribe exactamente 'peso por calibrar' y pregúntale al atleta.
"""
    return contexto_final


# ==============================================================================
# FUNCIÓN PRINCIPAL ORQUESTADORA (Faltante en el refactor)
# ==============================================================================
def procesar_levantamiento_vbt(
        ruta_modelo: str,
        ruta_video: str,
        media_id: str,
        numero_usuario: str,
        nombre_ejercicio: str = "Ejercicio No Especificado",
        ruta_salida: str = "output_vbt.mp4",
) -> dict:
    if not nombre_ejercicio:
        nombre_ejercicio = "Movimiento Libre"

    logger.info(f"Iniciando Motor VBT para: {nombre_ejercicio}...")
    cap = None
    video_writer = None
    ruta_temporal = ruta_salida

    try:
        # 1. Inicialización de los nuevos componentes
        rastreador = RastreadorDisco(ruta_modelo)
        cap, fps, dt_nominal, ancho, alto = inicializar_video(ruta_video, numero_usuario)
        video_writer, ruta_temporal = _inicializar_escritor_video(ruta_salida, ancho, alto, fps)

        estimador = EstimadorCinematica()
        maquina = MaquinaEstadosRep(CONFIG, fps)

        puntos_trayectoria = deque(maxlen=30)
        registro_repeticiones = []
        tiempo_anterior_msec = 0.0

        frames_totales = 0
        frames_con_disco = 0

        logger.info("Analizando video frame a frame...")

        while cap.isOpened():
            exito, frame = cap.read()
            if not exito:
                break

            frames_totales += 1

            # Timestamps reales
            timestamp_actual = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if timestamp_actual > tiempo_anterior_msec:
                dt_real = timestamp_actual - tiempo_anterior_msec
            else:
                dt_real = dt_nominal
            tiempo_anterior_msec = timestamp_actual

            # 2. Tracking YOLO encapsulado
            det = rastreador.procesar(frame)
            v_inst = 0.0

            if det is not None:
                frames_con_disco += 1
                puntos_trayectoria.appendleft((det.cx, det.cy))

                # 3. Filtrado cinemático independiente
                y_suave, v_inst = estimador.actualizar(timestamp_actual, det.cy, det.m_por_px)

                # 4. Máquina de estados (evalúa si se cerró una repetición)
                rep_cerrada = maquina.actualizar(timestamp_actual, y_suave, v_inst, det.alto_caja)

                if rep_cerrada:
                    # Rellenar Velocity Loss respecto a la primera rep
                    if len(registro_repeticiones) > 0:
                        v_primera = registro_repeticiones[0].v_avg
                        if v_primera > 0:
                            rep_cerrada.vl_pct = round(((v_primera - rep_cerrada.v_avg) / v_primera) * 100.0, 1)
                    registro_repeticiones.append(rep_cerrada)

            # 5. Cierre forzado por inactividad prolongada (Lockout)
            if maquina.frames_estables > (fps * CONFIG.seg_inactividad_fin) and maquina.estado != "calibrando":
                rep_final = maquina.cerrar_por_inactividad(timestamp_actual)
                if rep_final:
                    if len(registro_repeticiones) > 0:
                        v_primera = registro_repeticiones[0].v_avg
                        if v_primera > 0:
                            rep_final.vl_pct = round(((v_primera - rep_final.v_avg) / v_primera) * 100.0, 1)
                    registro_repeticiones.append(rep_final)

                cv2.putText(frame, "FIN DE SERIE", (ancho // 2 - 150, alto // 2),
                            cv2.FONT_HERSHEY_DUPLEX, 2, (0, 0, 255), 3)
                video_writer.write(frame)
                break

            # 6. Renderizado HUD y Trayectoria
            vl_actual = None
            if len(registro_repeticiones) >= 2:
                vl_actual = calcular_velocity_loss(registro_repeticiones)

            renderizar_frame(
                frame=frame,
                det=det,
                trayectoria=puntos_trayectoria,
                v_inst=v_inst,
                reps=maquina.reps,
                fase=maquina.estado,
                registro=registro_repeticiones,
                vl_actual=vl_actual
            )

            video_writer.write(frame)

    except Exception as e:
        logger.error(f"Crasheo en ciclo principal: {e}")
        traceback.print_exc()
    finally:
        if cap: cap.release()
        if video_writer: video_writer.release()

    # 7. Diagnóstico y Manejo de Errores VBT
    if maquina.reps == 0 or not registro_repeticiones:
        max_vel = max(maquina.vels_serie) if maquina.vels_serie else 0.0
        msg_error = _generar_autopsia_vbt(
            frames_totales, frames_con_disco, maquina.fase_maxima,
            maquina.max_descenso_px, max_vel
        )
        _convertir_video_h264(ruta_temporal, ruta_salida)
        return {"estado": "error", "mensaje": msg_error, "ruta_video_renderizado": ruta_salida}

    # 8. Reporte Final y Prompt LLM
    contexto_base = _generar_contexto_llm(
        reps_completadas=maquina.reps,
        velocidades_globales=maquina.vels_serie,
        registro=registro_repeticiones,
        ejercicio=nombre_ejercicio,
        numero_usuario=numero_usuario
    )

    contexto_agente = f"EJERCICIO IDENTIFICADO VÍA IA: {nombre_ejercicio.upper()}\n\n{contexto_base}"

    # Conversión final para iOS/Web
    _convertir_video_h264(ruta_temporal, ruta_salida)

    # 9. Retorno estructurado (Drop-in compatible con tu main)
    return {
        "estado": "exito",
        "reps": maquina.reps,
        "v_promedio_global": round(float(np.mean(maquina.vels_serie)), 3),
        "detalle_repeticiones": [r.a_dict() for r in registro_repeticiones],
        "ruta_video_renderizado": ruta_salida,
        "contexto_agente": contexto_agente
    }