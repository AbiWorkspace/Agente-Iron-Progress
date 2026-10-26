# -*- coding: utf-8 -*-
"""
services/vbt/rastreo_disco.py — Rastreador de disco olímpico con YOLO, ByteTrack y anclaje anti-switch.
"""
from __future__ import annotations

import logging
import math
import os
from collections import deque
from typing import Optional, Union

import numpy as np
from ultralytics import YOLO

from services.vbt.configuracion import ConfigVBT, CONFIG, logger, _kwargs_fp16
from services.vbt.modelos_datos import Deteccion
from services.vbt.utilidades_video import _obtener_modelo_yolo


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
        el tracking cuando el disco se sale parcialmente por los bordes del encuadre."""
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

        err_tam = abs(tam_ef - self.alto_ema) / self.alto_ema
        if not en_borde and err_tam > self.cfg.salto_max_escala:
            return False

        dx_ult = abs(cx - self.ultima_pos[0])
        max_dx_ult = self.alto_ema * self.cfg.max_desvio_x_frac
        if dx_ult > max_dx_ult:
            return False

        if self.salio_por_borde and self.frames_sin_deteccion > 0:
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

        ruta_tracker = os.path.join(os.path.dirname(__file__), "vbt_bytetrack.yaml")
        if not os.path.exists(ruta_tracker):
            ruta_tracker = os.path.join(os.path.dirname(os.path.dirname(__file__)), "vbt_bytetrack.yaml")

        try:
            pred = self.modelo.track(
                frame,
                persist=True,
                tracker=ruta_tracker,
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

        self.frames_seguidos_id += 1
        self.total_frames_trackeados += 1
        if self.frames_seguidos_id >= 8 or self.total_frames_trackeados >= 12:
            if not self.ancla_confirmada:
                self.ancla_confirmada = True
                logger.info(
                    f"Ancla de serie confirmada tras {self.total_frames_trackeados} frames de tracking."
                )

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

        candidatos = []
        for idx, (caja, id_c) in enumerate(zip(cajas, ids)):
            cx, cy, tam_ef, en_borde, rec_extremo, lado_borde = self._compensar_caja_borde(
                caja, ancho_img, alto_img
            )
            candidatos.append((idx, int(id_c), caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde))

        if self.id_fijado is not None and self.id_fijado in ids:
            for other_id in ids:
                if other_id != self.id_fijado:
                    self.coexisting_ids[int(other_id)] = self._frame_count

        # 1) ID fijado sigue presente
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

                if id_c != self.id_fijado:
                    last_seen_together = self.coexisting_ids.get(id_c, -9999)
                    if (self._frame_count - last_seen_together) < 90:
                        continue

                dx = cx - pos_predicha[0]
                dy = cy - pos_predicha[1]
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

            if self.ancla_confirmada or self.frames_sin_deteccion < self.cfg.frames_para_soltar_id:
                return None

            logger.info(
                f"Disco perdido {self.frames_sin_deteccion} frames durante setup inicial: "
                f"se libera el ID para re-fijar."
            )
            self.id_fijado = None
            self.ultima_pos = None
            self.vel_px = (0.0, 0.0)

        # 3) Fijación inicial por puntuación (setup)
        linea_suelo = alto_img * self.cfg.frac_linea_suelo
        mejor_tuple = None
        mejor_score = 0.0
        for idx, id_c, caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde in candidatos:
            if rec_extremo:
                continue

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
                score *= 0.5
            if score > mejor_score:
                mejor_score = score
                mejor_tuple = (id_c, caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde)

        if mejor_tuple is not None:
            id_c, caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde = mejor_tuple
            self.id_fijado = id_c
            return caja, cx, cy, tam_ef, en_borde, rec_extremo, lado_borde
        return None
