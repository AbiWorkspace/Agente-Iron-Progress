# -*- coding: utf-8 -*-
"""
Paquete VBT (Velocity Based Training) — Iron Progress
======================================================
Módulos especializados en español:
  - configuracion.py: Parámetros globales, constantes físicas y compatibilidad YOLO.
  - modelos_datos.py: Estructuras de datos (Deteccion, Repeticion).
  - perfiles_biomecanicos.py: Perfiles por ejercicio, zonas de intensidad y fórmulas VBT.
  - utilidades_video.py: Captura OpenCV, preprocesamiento HDR y codificador FFmpeg.
  - rastreo_disco.py: Seguimiento visual del disco con YOLO y ByteTrack.
  - cinematica_pose.py: Estimación de velocidades, derivadas y pose articular.
  - maquina_estados.py: Máquina de estados finitos para segmentación de repeticiones.
  - analisis_reportes.py: Análisis determinista de series y reportes técnicos/WhatsApp.
  - renderizado_hud.py: Motor gráfico y renderizado visual del HUD sobre video.
  - procesador_levantamiento.py: Orquestador integral del análisis VBT.
"""
from __future__ import annotations

from services.vbt.configuracion import (
    ConfigVBT,
    CONFIG,
    GRAVEDAD,
    logger,
    _kwargs_fp16,
    _silenciar_avisos_ultralytics,
)
from services.vbt.modelos_datos import (
    Deteccion,
    Repeticion,
)
from services.vbt.perfiles_biomecanicos import (
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
)
from services.vbt.utilidades_video import (
    _CACHE_MODELOS_YOLO,
    _obtener_modelo_yolo,
    _notificar_whatsapp_seguro,
    _analizar_propiedades_video,
    _preprocesar_video_hdr_si_es_necesario,
    inicializar_video,
    EscritorVideoFFmpeg,
    _inicializar_escritor_video,
    _convertir_video_h264,
)
from services.vbt.rastreo_disco import (
    RastreadorDisco,
)
from services.vbt.cinematica_pose import (
    EstimadorCinematica,
    EstimadorPose,
    _angulo_3p,
)
from services.vbt.maquina_estados import (
    MaquinaEstadosRep,
)
from services.vbt.analisis_reportes import (
    analizar_serie,
    _construir_recomendacion,
    _lineas_por_rep,
    _construir_reporte_tecnico,
    _semaforo_vl,
    _construir_reporte_whatsapp,
    _generar_autopsia_vbt,
    _generar_contexto_llm,
)
from services.vbt.renderizado_hud import (
    _HUB,
    _FASES,
    render_hud,
    renderizar_frame,
)
from services.vbt.procesador_levantamiento import (
    procesar_levantamiento_vbt,
)

__all__ = [
    # Configuración
    "ConfigVBT",
    "CONFIG",
    "GRAVEDAD",
    "logger",
    "_kwargs_fp16",
    "_silenciar_avisos_ultralytics",
    # Modelos de datos
    "Deteccion",
    "Repeticion",
    # Perfiles biomecánicos
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
    # Utilidades de video
    "_CACHE_MODELOS_YOLO",
    "_obtener_modelo_yolo",
    "_notificar_whatsapp_seguro",
    "_analizar_propiedades_video",
    "_preprocesar_video_hdr_si_es_necesario",
    "inicializar_video",
    "EscritorVideoFFmpeg",
    "_inicializar_escritor_video",
    "_convertir_video_h264",
    # Rastreo de disco
    "RastreadorDisco",
    # Cinemática y Pose
    "EstimadorCinematica",
    "EstimadorPose",
    "_angulo_3p",
    # Máquina de estados
    "MaquinaEstadosRep",
    # Análisis y reportes
    "analizar_serie",
    "_construir_recomendacion",
    "_lineas_por_rep",
    "_construir_reporte_tecnico",
    "_semaforo_vl",
    "_construir_reporte_whatsapp",
    "_generar_autopsia_vbt",
    "_generar_contexto_llm",
    # Renderizado y HUD
    "_HUB",
    "_FASES",
    "render_hud",
    "renderizar_frame",
    # Orquestador
    "procesar_levantamiento_vbt",
]
