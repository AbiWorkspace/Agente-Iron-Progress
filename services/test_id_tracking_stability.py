#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de Estabilidad de ID Tracking para el Motor VBT
=====================================================
Instrumenta RastreadorDisco para medir ID switches, cobertura,
gaps y discontinuidades de posición en videos de prueba.

Uso:
    python test_id_tracking_stability.py [--video RUTA] [--all] [--json]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

# Agregar el directorio padre al path para importar Creardataset
sys.path.insert(0, str(Path(__file__).resolve().parent))

from Creardataset import (
    RastreadorDisco, ConfigVBT, CONFIG, _silenciar_avisos_ultralytics,
    _preprocesar_video_hdr_si_es_necesario,
)


# ==============================================================================
# MÉTRICAS
# ==============================================================================
@dataclass
class TrackingMetrics:
    """Métricas de estabilidad del tracking para un video."""
    video: str = ""
    frames_totales: int = 0
    frames_con_deteccion: int = 0
    coverage_pct: float = 0.0

    id_switches: int = 0              # Veces que id_fijado cambió
    ids_unicos: int = 0               # Número de IDs diferentes usados
    id_stability_score: float = 0.0   # 1 - (switches / frames_con_det)

    max_gap_frames: int = 0           # Gap más largo sin detección
    avg_gap_frames: float = 0.0       # Gap promedio
    gaps_count: int = 0               # Número total de gaps

    pos_jumps_large: int = 0          # Saltos de posición > umbral
    pos_continuity_score: float = 0.0 # 1 - (jumps / frames_con_det)
    max_jump_px: float = 0.0          # Salto más grande en px

    reenganche_count: int = 0         # Re-enganches cinemáticos
    rechazos_fisicos: int = 0         # Candidatos rechazados
    salidas_borde: int = 0            # Frames donde salió por borde

    fps_procesamiento: float = 0.0    # FPS de procesamiento
    duracion_s: float = 0.0           # Duración del video

    # Detalle de cada switch
    switch_events: list = field(default_factory=list)

    def resumen(self) -> str:
        lines = [
            f"{'═' * 60}",
            f"  VIDEO: {os.path.basename(self.video)}",
            f"{'─' * 60}",
            f"  Frames:     {self.frames_totales} ({self.duracion_s:.1f}s)",
            f"  Cobertura:  {self.coverage_pct:.1f}% ({self.frames_con_deteccion}/{self.frames_totales})",
            f"  ID Switches:{self.id_switches}  (IDs únicos: {self.ids_unicos})",
            f"  Stability:  {self.id_stability_score:.4f}",
            f"  Max Gap:    {self.max_gap_frames} frames",
            f"  Pos Jumps:  {self.pos_jumps_large} (max {self.max_jump_px:.1f}px)",
            f"  Continuity: {self.pos_continuity_score:.4f}",
            f"  Re-enganch: {self.reenganche_count}",
            f"  Rechazos:   {self.rechazos_fisicos}",
            f"  Borde:      {self.salidas_borde}",
            f"  FPS proc:   {self.fps_procesamiento:.1f}",
        ]
        if self.switch_events:
            lines.append(f"  Switch Events:")
            for ev in self.switch_events[:10]:
                lines.append(
                    f"    Frame {ev['frame']:4d}: ID {ev['id_from']} → {ev['id_to']}  "
                    f"pos ({ev.get('pos_before', '?')}) → ({ev.get('pos_after', '?')})"
                )
            if len(self.switch_events) > 10:
                lines.append(f"    ... y {len(self.switch_events) - 10} más")
        lines.append(f"{'═' * 60}")
        return "\n".join(lines)


# ==============================================================================
# RASTREADOR INSTRUMENTADO
# ==============================================================================
class RastreadorDiscoInstrumentado(RastreadorDisco):
    """Wrapper que registra todos los eventos de tracking para análisis."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._frame_idx = 0
        self._id_anterior: Optional[int] = None
        self._pos_anterior: Optional[tuple] = None
        self._en_gap = False
        self._gap_actual = 0

        # Contadores
        self.total_id_switches = 0
        self.total_reenganche = 0
        self.total_rechazos = 0
        self.total_salidas_borde = 0
        self.ids_vistos: set = set()
        self.switch_events: list = []
        self.gaps: list = []  # lista de longitudes de gap
        self.jumps: list = []  # lista de magnitudes de salto en px

    def procesar(self, frame):
        """Procesa un frame y registra eventos de tracking."""
        id_antes = self.id_fijado
        pos_antes = self.ultima_pos

        det = super().procesar(frame)

        id_despues = self.id_fijado

        # Registrar IDs vistos
        if id_despues is not None:
            self.ids_vistos.add(id_despues)

        # Detectar ID switch
        if (id_antes is not None and id_despues is not None
                and id_antes != id_despues):
            self.total_id_switches += 1
            self.switch_events.append({
                "frame": self._frame_idx,
                "id_from": id_antes,
                "id_to": id_despues,
                "pos_before": pos_antes,
                "pos_after": self.ultima_pos,
            })

        # Registrar gaps
        if det is None:
            if not self._en_gap:
                self._en_gap = True
                self._gap_actual = 1
            else:
                self._gap_actual += 1
        else:
            if self._en_gap:
                self.gaps.append(self._gap_actual)
                self._en_gap = False
                self._gap_actual = 0

        # Registrar saltos de posición
        if det is not None and self._pos_anterior is not None:
            dx = det.cx - self._pos_anterior[0]
            dy = det.cy - self._pos_anterior[1]
            jump = math.hypot(dx, dy)
            self.jumps.append(jump)
            self._pos_anterior = (det.cx, det.cy)
        elif det is not None:
            self._pos_anterior = (det.cx, det.cy)

        # Registrar salidas por borde
        if self.salio_por_borde:
            self.total_salidas_borde += 1

        self._frame_idx += 1
        self._id_anterior = id_despues
        return det


# ==============================================================================
# PROCESAMIENTO DE VIDEO
# ==============================================================================
def procesar_video_diagnostico(
    ruta_video: str,
    ruta_modelo: str,
    cfg: ConfigVBT = CONFIG,
    verbose: bool = True,
) -> TrackingMetrics:
    """Procesa un video completo y retorna métricas de tracking."""

    _silenciar_avisos_ultralytics()

    # Preprocesar HDR si es necesario
    ruta_proc, es_temp = _preprocesar_video_hdr_si_es_necesario(ruta_video)

    cap = cv2.VideoCapture(ruta_proc)
    if not cap.isOpened():
        raise ValueError(f"No se pudo abrir: {ruta_video}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 30.0
    total_est = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    rastreador = RastreadorDiscoInstrumentado(ruta_modelo, cfg)
    metrics = TrackingMetrics(video=ruta_video)

    t_start = time.time()
    frame_count = 0
    frames_con_det = 0

    if verbose:
        print(f"\n🔍 Procesando: {os.path.basename(ruta_video)}")
        print(f"   Frames estimados: {total_est}, FPS: {fps:.1f}")

    while cap.isOpened():
        ok, frame = cap.read()
        if not ok:
            break
        frame_count += 1

        det = rastreador.procesar(frame)
        if det is not None:
            frames_con_det += 1

        if verbose and frame_count % 50 == 0:
            pct = (frame_count / max(total_est, 1)) * 100
            print(f"   [{pct:5.1f}%] Frame {frame_count}/{total_est} | "
                  f"Det: {frames_con_det} | Switches: {rastreador.total_id_switches} | "
                  f"ID: {rastreador.id_fijado}", end="\r")

    cap.release()
    t_elapsed = time.time() - t_start

    # Cerrar gap final si existe
    if rastreador._en_gap:
        rastreador.gaps.append(rastreador._gap_actual)

    # Limpiar temp
    if es_temp and os.path.exists(ruta_proc):
        try:
            os.remove(ruta_proc)
        except OSError:
            pass

    # Calcular métricas
    metrics.frames_totales = frame_count
    metrics.frames_con_deteccion = frames_con_det
    metrics.coverage_pct = (frames_con_det / max(frame_count, 1)) * 100.0
    metrics.duracion_s = frame_count / fps

    metrics.id_switches = rastreador.total_id_switches
    metrics.ids_unicos = len(rastreador.ids_vistos)
    metrics.id_stability_score = (
        1.0 - (rastreador.total_id_switches / max(frames_con_det, 1))
    )

    if rastreador.gaps:
        metrics.max_gap_frames = max(rastreador.gaps)
        metrics.avg_gap_frames = float(np.mean(rastreador.gaps))
        metrics.gaps_count = len(rastreador.gaps)

    # Saltos de posición grandes (> 50px o > 0.5 * alto_ema)
    jump_threshold = 50.0
    if rastreador.alto_ema and rastreador.alto_ema > 0:
        jump_threshold = rastreador.alto_ema * 0.5

    if rastreador.jumps:
        large_jumps = [j for j in rastreador.jumps if j > jump_threshold]
        metrics.pos_jumps_large = len(large_jumps)
        metrics.max_jump_px = max(rastreador.jumps)
        metrics.pos_continuity_score = (
            1.0 - (len(large_jumps) / max(len(rastreador.jumps), 1))
        )

    metrics.reenganche_count = rastreador.total_reenganche
    metrics.rechazos_fisicos = rastreador.total_rechazos
    metrics.salidas_borde = rastreador.total_salidas_borde

    metrics.fps_procesamiento = frame_count / max(t_elapsed, 0.001)
    metrics.switch_events = rastreador.switch_events

    if verbose:
        print()  # newline after \r
        print(metrics.resumen())

    return metrics


# ==============================================================================
# MAIN
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Test de estabilidad de ID tracking VBT")
    parser.add_argument("--video", type=str, help="Ruta a un video específico")
    parser.add_argument("--all", action="store_true", help="Procesar todos los videos de prueba")
    parser.add_argument("--json", action="store_true", help="Guardar resultados en JSON")
    parser.add_argument("--model", type=str,
                        default="/home/abimael/PycharmProjects/Agente-Iron-Progress/models/best.pt",
                        help="Ruta al modelo YOLO")
    parser.add_argument("--tag", type=str, default="baseline",
                        help="Etiqueta para el reporte (baseline/improved)")
    args = parser.parse_args()

    videos_dir = Path(__file__).resolve().parent / "Videos para probar"
    results = []

    if args.video:
        videos = [args.video]
    elif args.all:
        videos = sorted(str(p) for p in videos_dir.glob("*.mp4"))
    else:
        # Por defecto: primeros 5 videos (rápido)
        videos = sorted(str(p) for p in videos_dir.glob("*.mp4"))[:5]

    if not videos:
        print("❌ No se encontraron videos para procesar")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  TEST DE ESTABILIDAD DE ID TRACKING — [{args.tag.upper()}]")
    print(f"  Videos: {len(videos)}")
    print(f"  Modelo: {args.model}")
    print(f"{'='*60}")

    for vpath in videos:
        try:
            m = procesar_video_diagnostico(vpath, args.model, verbose=True)
            results.append(m)
        except Exception as e:
            print(f"❌ Error en {os.path.basename(vpath)}: {e}")

    # Resumen global
    if results:
        print(f"\n{'='*60}")
        print(f"  RESUMEN GLOBAL — [{args.tag.upper()}]")
        print(f"{'─'*60}")

        total_switches = sum(r.id_switches for r in results)
        avg_coverage = np.mean([r.coverage_pct for r in results])
        avg_stability = np.mean([r.id_stability_score for r in results])
        avg_continuity = np.mean([r.pos_continuity_score for r in results])
        max_gap_all = max(r.max_gap_frames for r in results) if results else 0
        total_jumps = sum(r.pos_jumps_large for r in results)

        print(f"  Videos procesados:   {len(results)}")
        print(f"  Total ID switches:   {total_switches}")
        print(f"  Cobertura media:     {avg_coverage:.1f}%")
        print(f"  Stability media:     {avg_stability:.4f}")
        print(f"  Continuity media:    {avg_continuity:.4f}")
        print(f"  Max gap (frames):    {max_gap_all}")
        print(f"  Total saltos grandes:{total_jumps}")

        # Videos problemáticos (ordenados por switches)
        problematicos = sorted(results, key=lambda r: r.id_switches, reverse=True)
        print(f"\n  Top videos problemáticos:")
        for r in problematicos[:5]:
            print(f"    {r.id_switches:3d} switches | {r.coverage_pct:5.1f}% cov | "
                  f"{r.ids_unicos} IDs | {os.path.basename(r.video)}")

        print(f"{'='*60}\n")

        if args.json:
            output_path = Path(__file__).resolve().parent / f"tracking_report_{args.tag}.json"
            safe_results = []
            for r in results:
                d = asdict(r)
                # Convertir tuples a strings para JSON
                for ev in d.get("switch_events", []):
                    if ev.get("pos_before"):
                        ev["pos_before"] = str(ev["pos_before"])
                    if ev.get("pos_after"):
                        ev["pos_after"] = str(ev["pos_after"])
                safe_results.append(d)

            report = {
                "tag": args.tag,
                "model": args.model,
                "summary": {
                    "videos": len(results),
                    "total_id_switches": total_switches,
                    "avg_coverage_pct": round(avg_coverage, 2),
                    "avg_stability": round(avg_stability, 5),
                    "avg_continuity": round(avg_continuity, 5),
                    "max_gap_frames": max_gap_all,
                    "total_pos_jumps": total_jumps,
                },
                "per_video": safe_results,
            }
            with open(output_path, "w") as f:
                json.dump(report, f, indent=2, default=str)
            print(f"📄 Reporte guardado en: {output_path}")


if __name__ == "__main__":
    main()
