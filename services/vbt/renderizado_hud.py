# -*- coding: utf-8 -*-
"""
services/vbt/renderizado_hud.py — Renderizado visual del HUD sobre el video con OpenCV (Sport Hub / Iron Progress).
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import cv2
import numpy as np

from services.vbt.modelos_datos import Deteccion

# ==============================================================================
# PALETA DE COLOR (BGR) Y FUENTES
# ==============================================================================
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
    "concentrica": (_HUB["green"], "CONCENTRICA", 1),
    "excentrica":  (_HUB["amber"], "EXCENTRICA", -1),
    "eccentrica":  (_HUB["amber"], "EXCENTRICA", -1),
    "reposo":      (_HUB["muted"], "REPOSO", 0),
    "finalizado":  (_HUB["accent"], "SERIE FINALIZADA", 0),
}

_FONT = cv2.FONT_HERSHEY_DUPLEX
_FONT_UI = cv2.FONT_HERSHEY_SIMPLEX


# ==============================================================================
# UTILIDADES GRÁFICAS BASE
# ==============================================================================
def _s(frame: np.ndarray, v: float) -> int:
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


def _rounded_rect(img: np.ndarray, x1: int, y1: int, x2: int, y2: int,
                  radius: int, color: tuple, thickness: int = -1) -> None:
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


def _panel(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int,
           alpha: float = 0.82, radius: int = 14, accent_top: bool = True) -> None:
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


def _txt(frame: np.ndarray, text: str, pos: tuple[int, int], scale: float = 0.5,
         color: Optional[tuple] = None, thickness: int = 1,
         shadow: bool = True, font: int = _FONT) -> None:
    """Texto anti-aliased con sombra suave para legibilidad."""
    color = color or _HUB["white"]
    sc = scale * frame.shape[0] / 1080.0
    th = max(1, _s(frame, thickness))
    if shadow:
        off = _s(frame, 2)
        cv2.putText(frame, text, (pos[0] + off, pos[1] + off),
                    font, sc, _HUB["shadow"], th + _s(frame, 1), cv2.LINE_AA)
    cv2.putText(frame, text, pos, font, sc, color, th, cv2.LINE_AA)


def _txt_size(frame: np.ndarray, text: str, scale: float = 0.5,
              thickness: int = 1, font: int = _FONT) -> tuple[int, int]:
    sc = scale * frame.shape[0] / 1080.0
    th = max(1, _s(frame, thickness))
    (tw, thh), _ = cv2.getTextSize(text, font, sc, th)
    return tw, thh


def _corners(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int,
             color: Optional[tuple] = None, arm: int = 18, thick: int = 2) -> None:
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


def _bar(frame: np.ndarray, x: int, y: int, w: int, h: int,
         ratio: float, color_fg: tuple, threshold: Optional[float] = None) -> None:
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


def _pill(frame: np.ndarray, text: str, x: int, y: int, color_bg: tuple, scale: float = 0.42) -> int:
    """Pastilla redondeada con texto oscuro sobre color sólido."""
    px, py = _s(frame, 10), _s(frame, 7)
    tw, th = _txt_size(frame, text, scale, 1, _FONT_UI)
    x2, y2 = x + tw + px * 2, y + py
    y1 = y - th - py
    _rounded_rect(frame, x, y1, x2, y2, (y2 - y1) // 2, color_bg, -1)
    _txt(frame, text, (x + px, y), scale=scale, color=(12, 12, 12),
         shadow=False, font=_FONT_UI)
    return tw + px * 3


def _fase_pill(frame: np.ndarray, fase: str, x: int, y: int, scale: float = 0.46) -> int:
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


def _metric(frame: np.ndarray, label: str, value: str, x: int, y: int,
            vcolor: Optional[tuple] = None, vscale: float = 0.78, unit: str = "") -> None:
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


def _sparkline(frame: np.ndarray, x: int, y: int, w: int, h: int,
               values: list, color: Optional[tuple] = None, fill: bool = True) -> None:
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


def _trail(frame: np.ndarray, puntos: list, velocidades: Optional[list] = None, max_pts: int = 40) -> None:
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
        t = i / n
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


def _render_watermark_pill(frame: np.ndarray, text: str = "AbiSchulz", active_area: Optional[tuple] = None) -> None:
    """Dibuja una cápsula / pastilla estética semitransparente con la firma «AbiSchulz»."""
    H, W = frame.shape[:2]
    scale = 0.44
    tw, th = _txt_size(frame, text, scale=scale, thickness=1, font=_FONT_UI)
    pad_h = _s(frame, 12)
    pad_v = _s(frame, 6)
    dot_r = _s(frame, 3)
    dot_gap = _s(frame, 7)

    pill_w = pad_h + dot_r * 2 + dot_gap + tw + pad_h
    pill_h = th + pad_v * 2

    px1 = (W - pill_w) // 2
    px2 = px1 + pill_w

    if active_area:
        pad_x, pad_y, new_w, new_h = active_area
        if pad_y > _s(frame, 120):
            py_ideal = pad_y + new_h + _s(frame, 25)
            py1 = H - _s(frame, 220) if (py_ideal + pill_h > H - _s(frame, 180)) else py_ideal
        else:
            py1 = H - _s(frame, 220)
    else:
        py1 = H - _s(frame, 220)

    py2 = py1 + pill_h

    rx1, ry1 = max(0, px1), max(0, py1)
    rx2, ry2 = min(W, px2), min(H, py2)
    if rx2 <= rx1 or ry2 <= ry1:
        return

    roi = frame[ry1:ry2, rx1:rx2]
    overlay_roi = roi.copy()
    radius = pill_h // 2
    _rounded_rect(overlay_roi, px1 - rx1, py1 - ry1, px2 - rx1, py2 - ry1, radius, _HUB["bg"], -1)
    cv2.addWeighted(overlay_roi, 0.78, roi, 0.22, 0, roi)

    _rounded_rect(frame, px1, py1, px2, py2, radius, _HUB["border"], max(1, _s(frame, 1)))

    dot_cx = px1 + pad_h + dot_r
    dot_cy = py1 + pill_h // 2
    cv2.circle(frame, (dot_cx, dot_cy), dot_r, _HUB["accent"], -1, cv2.LINE_AA)

    txt_x = dot_cx + dot_r + dot_gap
    txt_y = py1 + pad_v + th
    _txt(frame, text, (txt_x, txt_y), scale=scale, color=_HUB["white"],
         thickness=1, shadow=False, font=_FONT_UI)


# ==============================================================================
# COMPOSICIÓN Y RENDERIZADO DEL HUD
# ==============================================================================
def render_hud(frame: np.ndarray, *, reps: int = 0, fase: str = "reposo", velocidad: float = 0.0,
               fatiga: float = 0.0, historial_vel: tuple = (), titulo: str = "SPORT HUB",
               bbox: Optional[tuple] = None, trayectoria: tuple = (), vel_trayectoria: tuple = ()) -> None:
    """Dibuja el HUD completo. Llamar una vez por frame."""
    H, W = frame.shape[:2]
    m = _s(frame, 28)
    pw, ph = _s(frame, 330), _s(frame, 320)
    x1, y1 = m, m
    x2, y2 = x1 + pw, y1 + ph
    pad = _s(frame, 22)

    if trayectoria:
        _trail(frame, list(trayectoria), list(vel_trayectoria) or None)
    if bbox:
        _corners(frame, *map(int, bbox))

    _panel(frame, x1, y1, x2, y2)

    cy = y1 + pad + _s(frame, 8)
    cv2.circle(frame, (x1 + pad + _s(frame, 5), cy - _s(frame, 5)),
               _s(frame, 5), _HUB["accent"], -1, cv2.LINE_AA)
    _txt(frame, titulo, (x1 + pad + _s(frame, 20), cy), scale=0.55,
         color=_HUB["white"], font=_FONT_UI)
    cy += _s(frame, 16)
    cv2.line(frame, (x1 + pad, cy), (x2 - pad, cy), _HUB["sep"],
             _s(frame, 1), cv2.LINE_AA)

    cy += _s(frame, 34)
    _metric(frame, "reps", str(reps), x1 + pad, cy,
            vcolor=_HUB["green"], vscale=1.15)
    _fase_pill(frame, fase, x1 + pad + _s(frame, 130), cy + _s(frame, 26))

    cy += _s(frame, 78)
    vcol = _color_por_velocidad(velocidad)
    _metric(frame, "velocidad media", f"{velocidad:.2f}", x1 + pad, cy,
            vcolor=vcol, unit="m/s")
    cy += _s(frame, 44)
    _bar(frame, x1 + pad, cy, pw - pad * 2, _s(frame, 10),
         velocidad, vcol, threshold=0.75)

    cy += _s(frame, 40)
    fcol = _HUB["green"] if fatiga < 0.2 else (
        _HUB["amber"] if fatiga < 0.4 else _HUB["red"])
    _txt(frame, "FATIGA", (x1 + pad, cy), scale=0.34, color=_HUB["muted"],
         shadow=False, font=_FONT_UI)
    _txt(frame, f"{fatiga * 100:.0f}%", (x2 - pad - _s(frame, 58), cy),
         scale=0.44, color=fcol, font=_FONT_UI)
    cy += _s(frame, 12)
    _bar(frame, x1 + pad, cy, pw - pad * 2, _s(frame, 10), fatiga, fcol)

    if len(historial_vel) >= 2:
        cy += _s(frame, 36)
        _txt(frame, "VELOCIDAD POR REP", (x1 + pad, cy), scale=0.32,
             color=_HUB["muted"], shadow=False, font=_FONT_UI)
        _sparkline(frame, x1 + pad, cy + _s(frame, 10),
                   pw - pad * 2, _s(frame, 42), list(historial_vel),
                   color=_HUB["accent"])

    _render_watermark_pill(frame, text="AbiSchulz")


def renderizar_frame(frame: np.ndarray, det: Optional[Deteccion], trayectoria: deque,
                     v_inst: float, reps: int, fase: str,
                     registro: list, vl_actual: Optional[float],
                     rir_txt: Optional[str] = None,
                     active_area: Optional[tuple] = None,
                     nombre_ejercicio: Optional[str] = None,
                     carga_kg: Optional[float] = None) -> None:
    """Renderizado por cuadro optimizado por ROI con HUD modular, ejercicio confirmado y firma AbiSchulz."""
    n = len(trayectoria)
    for j in range(1, n):
        t = j / n
        col = (0, int(140 + 100 * t), int(190 + 65 * t))
        grosor = max(1, _s(frame, 2 if t > 0.6 else 1))
        cv2.line(frame, trayectoria[j - 1], trayectoria[j], col, grosor, cv2.LINE_AA)

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

    PX = _s(frame, 20)
    PW = _s(frame, 330)
    PH = _s(frame, 355)

    if active_area and active_area[1] > _s(frame, 180):
        PY = max(_s(frame, 20), (active_area[1] - PH) // 2)
    else:
        PY = _s(frame, 20)

    _panel(frame, PX, PY, PX + PW, PY + PH, alpha=0.76, radius=12, accent_top=True)
    cv2.line(frame, (PX + _s(frame, 10), PY + PH), (PX + PW - _s(frame, 10), PY + PH), _HUB["sep"], _s(frame, 1), cv2.LINE_AA)

    _txt(frame, "IRON PROGRESS", (PX + _s(frame, 18), PY + _s(frame, 22)),
         scale=0.42, color=_HUB["accent"], shadow=True, thickness=1)

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

    _txt(frame, "REPETICIONES", (PX + _s(frame, 18), PY + _s(frame, 58)),
         scale=0.34, color=_HUB["muted"])
    _txt(frame, str(reps), (PX + _s(frame, 16), PY + _s(frame, 122)),
         scale=2.5, color=_HUB["green"], thickness=2, shadow=True)

    cv2.line(frame, (PX + _s(frame, 12), PY + _s(frame, 132)), (PX + PW - _s(frame, 12), PY + _s(frame, 132)), _HUB["sep"], _s(frame, 1), cv2.LINE_AA)

    _fase_pill(frame, fase, PX + _s(frame, 18), PY + _s(frame, 157), scale=0.46)
    cv2.line(frame, (PX + _s(frame, 12), PY + _s(frame, 172)), (PX + PW - _s(frame, 12), PY + _s(frame, 172)), _HUB["sep"], _s(frame, 1), cv2.LINE_AA)

    if registro:
        v_last = registro[-1].v_avg
        col_last = _color_por_velocidad(v_last)
        _metric(frame, "Ultima rep", f"{v_last:.2f} m/s",
                PX + _s(frame, 18), PY + _s(frame, 190), vcolor=col_last)
        _bar(frame, PX + _s(frame, 18), PY + _s(frame, 220), PW - _s(frame, 36), _s(frame, 6), v_last / 1.5, col_last)

    cv2.line(frame, (PX + _s(frame, 12), PY + _s(frame, 236)), (PX + PW - _s(frame, 12), PY + _s(frame, 236)), _HUB["sep"], _s(frame, 1), cv2.LINE_AA)

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

    if rir_txt:
        cv2.line(frame, (PX + _s(frame, 12), PY + _s(frame, 300)), (PX + PW - _s(frame, 12), PY + _s(frame, 300)),
                 _HUB["sep"], _s(frame, 1), cv2.LINE_AA)
        _txt(frame, "EN RESERVA (RIR est.)", (PX + _s(frame, 18), PY + _s(frame, 318)),
             scale=0.34, color=_HUB["muted"], shadow=False, font=_FONT_UI)
        _txt(frame, rir_txt, (PX + PW - _s(frame, 72), PY + _s(frame, 320)),
             scale=0.52, color=_HUB["accent"], font=_FONT_UI)

    _render_watermark_pill(frame, text="AbiSchulz", active_area=active_area)
