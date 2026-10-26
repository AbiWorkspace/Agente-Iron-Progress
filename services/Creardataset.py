# -*- coding: utf-8 -*-
"""
services/Creardataset.py — Fachada pública y retrocompatible del Motor VBT.

El monolito original ha sido descompuesto y modularizado en el paquete `services.vbt`:
  1. `services.vbt.configuracion`: Constantes físicas, parámetros del sistema y compatibilidad Ultralytics.
  2. `services.vbt.modelos_datos`: Clases de datos (`Deteccion`, `Repeticion`).
  3. `services.vbt.perfiles_biomecanicos`: Perfiles de fuerza-velocidad y fórmulas VBT (MVT, e1RM, RIR, VL%).
  4. `services.vbt.utilidades_video`: I/O de video con OpenCV, detección/conversión HDR y pipe FFmpeg.
  5. `services.vbt.rastreo_disco`: Rastreador de disco olímpico con YOLO, ByteTrack y anclaje espacial.
  6. `services.vbt.cinematica_pose`: Estimación cinemática (Savitzky-Golay / polinomios locales) y pose articular.
  7. `services.vbt.maquina_estados`: Máquina de estados finitos para segmentación y cronometraje de repeticiones.
  8. `services.vbt.analisis_reportes`: Motor analítico determinista, recomendaciones técnicas y reportes.
  9. `services.vbt.renderizado_hud`: Primitivas gráficas y renderizado visual del HUD sobre video.
  10. `services.vbt.procesador_levantamiento`: Orquestador principal del pipeline (`procesar_levantamiento_vbt`).
"""
from __future__ import annotations

from services.vbt import (
    ConfigVBT,
    CONFIG,
    GRAVEDAD,
    logger,
    _kwargs_fp16,
    _silenciar_avisos_ultralytics,
    Deteccion,
    Repeticion,
    PerfilEjercicio,
    PERFILES_EJERCICIO,
    obtener_perfil_ejercicio,
    _normalizar_nivel,
    estimar_rir_por_velocidad,
    estimar_rir_por_vl,
    estimar_pct_1rm,
    estimar_1rm_diario,
    estimar_e1rm_serie,
    estimar_reps_restantes,
    estimar_zona_intensidad,
    calcular_velocity_loss,
    _recalcular_vl_por_rep,
    _clasificar_estimulo,
    _json_safe,
    _CACHE_MODELOS_YOLO,
    _obtener_modelo_yolo,
    _notificar_whatsapp_seguro,
    _analizar_propiedades_video,
    _preprocesar_video_hdr_si_es_necesario,
    inicializar_video,
    EscritorVideoFFmpeg,
    _inicializar_escritor_video,
    _convertir_video_h264,
    RastreadorDisco,
    EstimadorCinematica,
    EstimadorPose,
    _angulo_3p,
    MaquinaEstadosRep,
    analizar_serie,
    _construir_recomendacion,
    _lineas_por_rep,
    _construir_reporte_tecnico,
    _semaforo_vl,
    _construir_reporte_whatsapp,
    _generar_autopsia_vbt,
    _generar_contexto_llm,
    _HUB,
    _FASES,
    render_hud,
    renderizar_frame,
    procesar_levantamiento_vbt,
)

__all__ = [
    "ConfigVBT",
    "CONFIG",
    "GRAVEDAD",
    "logger",
    "_kwargs_fp16",
    "_silenciar_avisos_ultralytics",
    "Deteccion",
    "Repeticion",
    "PerfilEjercicio",
    "PERFILES_EJERCICIO",
    "obtener_perfil_ejercicio",
    "_normalizar_nivel",
    "estimar_rir_por_velocidad",
    "estimar_rir_por_vl",
    "estimar_pct_1rm",
    "estimar_1rm_diario",
    "estimar_e1rm_serie",
    "estimar_reps_restantes",
    "estimar_zona_intensidad",
    "calcular_velocity_loss",
    "_recalcular_vl_por_rep",
    "_clasificar_estimulo",
    "_json_safe",
    "_CACHE_MODELOS_YOLO",
    "_obtener_modelo_yolo",
    "_notificar_whatsapp_seguro",
    "_analizar_propiedades_video",
    "_preprocesar_video_hdr_si_es_necesario",
    "inicializar_video",
    "EscritorVideoFFmpeg",
    "_inicializar_escritor_video",
    "_convertir_video_h264",
    "RastreadorDisco",
    "EstimadorCinematica",
    "EstimadorPose",
    "_angulo_3p",
    "MaquinaEstadosRep",
    "analizar_serie",
    "_construir_recomendacion",
    "_lineas_por_rep",
    "_construir_reporte_tecnico",
    "_semaforo_vl",
    "_construir_reporte_whatsapp",
    "_generar_autopsia_vbt",
    "_generar_contexto_llm",
    "_HUB",
    "_FASES",
    "render_hud",
    "renderizar_frame",
    "procesar_levantamiento_vbt",
]

# ==============================================================================
# DEMO RÁPIDA DEL HUD
# ==============================================================================
if __name__ == "__main__":
    import cv2
    import numpy as np

    demo = np.full((1080, 1920, 3), 60, np.uint8)
    demo[:] = (70, 60, 50)
    cv2.circle(demo, (1200, 540), 320, (90, 80, 70), -1)

    traj = [(1200 + int(24 * np.sin(i / 5)), 760 - i * 9) for i in range(45)]
    vels = list(np.clip(np.linspace(0.9, 0.35, 45) + np.random.rand(45) * .05, 0, 1))

    render_hud(
        demo, reps=6, fase="concentrica", velocidad=0.62, fatiga=0.31,
        historial_vel=[0.88, 0.85, 0.79, 0.74, 0.68, 0.62],
        bbox=(980, 260, 1430, 900), trayectoria=traj, vel_trayectoria=vels,
    )
    cv2.imwrite("hud_demo.png", demo)
    print("Demo guardada en hud_demo.png")
