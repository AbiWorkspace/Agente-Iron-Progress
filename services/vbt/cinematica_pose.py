# -*- coding: utf-8 -*-
"""
services/vbt/cinematica_pose.py — Estimación cinemática (Savitzky-Golay / polinomios locales) y análisis de pose articular.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from typing import Optional, Union

import numpy as np
from ultralytics import YOLO

from services.vbt.configuracion import ConfigVBT, CONFIG, logger, _kwargs_fp16
from services.vbt.utilidades_video import _obtener_modelo_yolo


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
            # 2. Compuerta de velocidad implícita
            elif dt_ultimo > 1e-4:
                v_implicita = abs(y_px - self.ys[-1]) * m_por_px / dt_ultimo
                if v_implicita > self.cfg.vel_max_valida * 1.15:
                    logger.warning(
                        f"Salto discontinuo detectado en señal Y ({v_implicita:.2f} m/s implícitos): "
                        f"se amortigua muestra para proteger el VBT."
                    )
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

        m = ts >= (t_ini_concentrica - 0.05)
        if int(m.sum()) >= 4:
            try:
                grad = np.gradient(rod[m], ts[m])
                out["vel_angular_rodilla_max"] = float(round(float(np.max(grad)), 0))
            except Exception:
                pass
        return out
