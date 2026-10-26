# -*- coding: utf-8 -*-
"""
services/vbt/maquina_estados.py — Máquina de estados finitos para la segmentación y análisis de repeticiones VBT.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np

from services.vbt.configuracion import ConfigVBT, GRAVEDAD, logger
from services.vbt.modelos_datos import Repeticion


class MaquinaEstadosRep:
    """
    Estados: calibrando -> reposo -> excentrica <-> concentrica.
    Trabaja con la posición Y suavizada, la X cruda (para el bar path) y la
    velocidad con signo. Devuelve un objeto Repeticion cada vez que se cierra
    una rep válida.
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
        self.escala_fijada = False           # congelada tras el setup
        self._ultimo_alto_caja: Optional[float] = None

        # Cronometraje de fases
        self.t_inicio_rep = 0.0
        self.t_inicio_excentrica = 0.0
        self.t_fin_descenso = 0.0            # última vez que profundizó
        self.t_fin_ascenso = 0.0             # última vez que ganó altura ÚTIL
        self.y_tope_empuje: Optional[float] = None
        self.y_ref_ganancia: Optional[float] = None
        self.t_ultima_ganancia = 0.0
        self.empuje_cerrado = False
        self._t_exc_pend = 0.0               # excéntrica de la rep en curso
        self._t_amort_pend: Optional[float] = None

        # Bar path
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
        self.estado = self.fase_maxima = "excentrica"
        self.y_mas_bajo = y
        self.t_inicio_excentrica = t
        self.t_fin_descenso = t
        self.buffer_subida.clear()
        self.path_rep = [(t, float(self.x_suave), float(y))]

    def _abrir_empuje(self, t: float, y: float):
        self.y_tope = self.y_tope_empuje = self.y_ref_ganancia = y
        self.t_ultima_ganancia = t
        self.empuje_cerrado = False

    def _entrar_concentrica_desde_fondo(self, t: float, y: float):
        self.estado = self.fase_maxima = "concentrica"
        self._abrir_empuje(t, y)
        self.inicio_fase_y = self.y_mas_bajo
        self._t_exc_pend = max(self.t_fin_descenso - self.t_inicio_excentrica, 0.0)
        if self.buffer_subida:
            self.t_inicio_concentrica = self.buffer_subida[0][0]
            self.vels_concentrica = list(self.buffer_subida)
        else:
            self.t_inicio_concentrica = t
            self.vels_concentrica = []
        self.t_fin_ascenso = self.t_inicio_concentrica
        self.vels_serie.extend(v for _, v in self.vels_concentrica)
        self._t_amort_pend = max(self.t_inicio_concentrica - self.t_fin_descenso, 0.0)
        self.buffer_subida.clear()

    # --------------------------------------------------------------
    def actualizar(self, t: float, x: float, y: float, v: float,
                   alto_caja: Optional[float]) -> Optional[Repeticion]:
        self._ultimo_alto_caja = alto_caja
        if not self.escala_fijada and alto_caja and alto_caja > 0:
            self._fijar_escala(alto_caja)
        rom_min, reversa, compromiso = self._umbrales(alto_caja)

        self.x_suave = x if self.x_suave is None else (self.x_suave * 0.7 + x * 0.3)

        if self.y_inicial is None:
            self.y_inicial = self.y_mas_bajo = self.y_tope = self.inicio_fase_y = y

        self.frames_quietos = (self.frames_quietos + 1
                               if abs(v) < self.cfg.vel_min_registro else 0)

        self.max_descenso_px = max(self.max_descenso_px, abs(y - self.y_inicial))

        if self.estado in ("excentrica", "concentrica"):
            self.path_rep.append((t, float(self.x_suave), float(y)))
            tope_pts = int(self.fps * 30)
            if len(self.path_rep) > tope_pts:
                self.path_rep = self.path_rep[-tope_pts:]

        rep_cerrada: Optional[Repeticion] = None

        if self.estado == "calibrando":
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
            if v > self.cfg.vel_min_registro:
                self.buffer_arranque.append((t, v))
            else:
                self.buffer_arranque.clear()

            if y > (self.y_inicial + compromiso):
                self.inicio_fase_y = self.y_inicial
                self.t_inicio_rep = t
                self._entrar_excentrica(t, y)
            elif y < (self.y_inicial - rom_min):
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
            if (self.buffer_subida
                    and (t - self.buffer_subida[-1][0]) > 2.5 / self.fps):
                self.buffer_subida.clear()

            if y > self.y_mas_bajo:
                self.y_mas_bajo = y
                self.t_fin_descenso = t
                self.buffer_subida.clear()
            elif v < -self.cfg.vel_min_registro:
                self.buffer_subida.clear()
            elif v > self.cfg.vel_min_registro:
                self.buffer_subida.append((t, v))

            if y < (self.y_mas_bajo - reversa):
                if abs(self.y_mas_bajo - self.inicio_fase_y) >= rom_min:
                    self._entrar_concentrica_desde_fondo(t, y)
                else:
                    self._reiniciar_a_reposo(y)

        elif self.estado == "concentrica":
            if v > 0 and not self.empuje_cerrado:
                self.vels_concentrica.append((t, v))
                self.vels_serie.append(v)

            if self.y_tope_empuje is None:
                self._abrir_empuje(t, y)

            if not self.empuje_cerrado:
                if y < self.y_tope_empuje:
                    self.y_tope_empuje = y

                rom_actual = abs(self.y_tope_empuje - self.inicio_fase_y)
                margen = max(rom_actual * self.cfg.frac_ganancia_util, 2.0)
                sube_de_verdad = (y < (self.y_ref_ganancia - margen)
                                  and v > self.cfg.vel_sticking)
                if sube_de_verdad:
                    self.y_ref_ganancia = y
                    self.t_fin_ascenso = self.t_ultima_ganancia = t
                elif (t - self.t_ultima_ganancia) > self.cfg.t_fin_empuje_s:
                    self.empuje_cerrado = True

            if self.empuje_cerrado:
                tope_val = self.y_tope_empuje if self.y_tope_empuje is not None else self.y_tope
                if abs(tope_val - self.inicio_fase_y) >= rom_min:
                    rep_cerrada = self._cerrar_repeticion(self.t_fin_ascenso if self.t_fin_ascenso > self.t_inicio_concentrica else t)
                    self.inicio_fase_y = self.y_tope
                    self.t_inicio_rep = t
                    self._reiniciar_a_reposo(y)

            if not rep_cerrada and y < self.y_tope:
                self.y_tope = y

            if not rep_cerrada and y > (self.y_tope + reversa):
                if abs(self.y_tope - self.inicio_fase_y) >= rom_min:
                    rep_cerrada = self._cerrar_repeticion(t)
                    self.inicio_fase_y = self.y_tope
                    self.t_inicio_rep = t
                    self._entrar_excentrica(t, y)
                else:
                    self._reiniciar_a_reposo(y)

        return rep_cerrada

    # --------------------------------------------------------------
    def cerrar_por_inactividad(self, t: float) -> Optional[Repeticion]:
        """Cierra la última rep si la serie termina bloqueada arriba (lockout)."""
        if self.estado != "concentrica" or len(self.vels_concentrica) <= 3:
            return None
        rom_min, _, _ = self._umbrales(self._ultimo_alto_caja)
        tope = self.y_tope_empuje if self.y_tope_empuje is not None else self.y_tope
        if abs(tope - self.inicio_fase_y) < rom_min:
            return None
        t_fin_real = min(t, self.vels_concentrica[-1][0])
        rep = self._cerrar_repeticion(t_fin_real)
        self.estado = "finalizado"
        self.vels_concentrica = []
        return rep

    # --------------------------------------------------------------
    def _cerrar_repeticion(self, t_fin: float) -> Repeticion:
        tope = self.y_tope_empuje if self.y_tope_empuje is not None else self.y_tope
        recorrido_px = abs(tope - self.inicio_fase_y)
        rom_m = recorrido_px * self.escala_fija_m_px
        t_fin_empuje = (self.t_fin_ascenso
                        if self.t_fin_ascenso > self.t_inicio_concentrica
                        else t_fin)
        duracion = max(t_fin_empuje - self.t_inicio_concentrica, 1e-3)

        ts = np.array([p[0] for p in self.vels_concentrica], dtype=float)
        vs = np.array([p[1] for p in self.vels_concentrica], dtype=float)

        if rom_m > 0 and duracion > 0:
            v_avg = float(rom_m / duracion)
        else:
            validas = vs[vs > self.cfg.umbral_ruido_prop]
            v_avg = float(np.mean(validas)) if validas.size else 0.0

        sticking_s = self._calcular_sticking(ts, vs, duracion, t_fin_empuje)

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
