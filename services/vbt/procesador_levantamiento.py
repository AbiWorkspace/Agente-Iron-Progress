# -*- coding: utf-8 -*-
"""
services/vbt/procesador_levantamiento.py — Orquestador principal del pipeline de video VBT.
"""
from __future__ import annotations

import os
import traceback
from collections import deque
from typing import Optional, Callable

import cv2
import numpy as np

from services.vbt.configuracion import CONFIG, logger, _silenciar_avisos_ultralytics
from services.vbt.utilidades_video import (
    _preprocesar_video_hdr_si_es_necesario,
    inicializar_video,
    _inicializar_escritor_video,
    _convertir_video_h264,
    _notificar_whatsapp_seguro,
)
from services.vbt.perfiles_biomecanicos import (
    obtener_perfil_ejercicio,
    _normalizar_nivel,
    estimar_rir_por_velocidad,
    calcular_velocity_loss,
    _recalcular_vl_por_rep,
)
from services.vbt.modelos_datos import Deteccion, Repeticion
from services.vbt.rastreo_disco import RastreadorDisco
from services.vbt.cinematica_pose import EstimadorCinematica, EstimadorPose
from services.vbt.maquina_estados import MaquinaEstadosRep
from services.vbt.analisis_reportes import (
    analizar_serie,
    _construir_reporte_whatsapp,
    _construir_reporte_tecnico,
    _generar_autopsia_vbt,
    _generar_contexto_llm,
)
from services.vbt.renderizado_hud import renderizar_frame, _HUB


def procesar_levantamiento_vbt(
        ruta_modelo: str,
        ruta_video: str,
        media_id: str,
        numero_usuario: str,
        nombre_ejercicio: str = "Ejercicio No Especificado",
        ruta_salida: str = "output_vbt.mp4",
        carga_kg: Optional[float] = None,
        nivel_atleta: str = "Avanzado",
        ruta_modelo_pose: Optional[str] = None,
        callback_notificacion: Optional[Callable] = None,
) -> dict:
    """Orquestador completo de análisis de video VBT: detección YOLO, pose, cinemática, HUD y reporte."""
    if not nombre_ejercicio:
        nombre_ejercicio = "Movimiento Libre"

    logger.info(f"Iniciando Motor VBT v3 para: {nombre_ejercicio}...")
    _silenciar_avisos_ultralytics()

    ruta_video_proc, es_temp_sdr = _preprocesar_video_hdr_si_es_necesario(ruta_video)

    perfil = obtener_perfil_ejercicio(nombre_ejercicio)
    nivel = _normalizar_nivel(nivel_atleta)

    cap = None
    video_writer = None
    ruta_temporal = ruta_salida
    maquina: Optional[MaquinaEstadosRep] = None
    registro_repeticiones: list = []
    frames_totales = 0
    frames_con_disco = 0
    timestamp_actual = 0.0

    TARGET_W, TARGET_H = 1080, 1920

    try:
        rastreador = RastreadorDisco(ruta_modelo)
        pose = EstimadorPose(ruta_modelo_pose)
        cap, fps, dt_nominal, ancho_orig, alto_orig = inicializar_video(ruta_video_proc, numero_usuario, callback_notificacion)

        total_frames_est = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duracion_s = (total_frames_est / fps) if (fps and fps > 0 and total_frames_est and total_frames_est > 0) else None
        video_writer, ruta_temporal = _inicializar_escritor_video(
            ruta_salida=ruta_salida,
            fps=fps,
            ruta_audio_original=ruta_video,
            duracion_s=duracion_s
        )

        scale_canvas = min(TARGET_W / ancho_orig, TARGET_H / alto_orig)
        new_w = int(round(ancho_orig * scale_canvas))
        new_h = int(round(alto_orig * scale_canvas))
        pad_x = (TARGET_W - new_w) // 2
        pad_y = (TARGET_H - new_h) // 2
        active_area = (pad_x, pad_y, new_w, new_h)

        estimador = EstimadorCinematica(CONFIG, fps)
        maquina = MaquinaEstadosRep(CONFIG, fps)

        puntos_trayectoria = deque(maxlen=30)
        puntos_trayectoria_canvas = deque(maxlen=30)
        tiempo_anterior = 0.0
        rir_live: Optional[str] = None
        ultima_det_canvas = None
        ultimo_frame_orig = None

        def _adjuntar_pose(rep: Repeticion) -> None:
            if not pose.activo:
                return
            t_conc_ini = (rep.t_inicio_s + rep.t_excentrica_s
                          + (rep.t_amortiguacion_s or 0.0))
            met = pose.metricas_rep(rep.t_inicio_s, rep.t_fin_s, t_conc_ini)
            rep.angulo_rodilla_min = met.get("angulo_rodilla_min")
            rep.angulo_cadera_min = met.get("angulo_cadera_min")
            rep.vel_angular_rodilla_max = met.get("vel_angular_rodilla_max")

        logger.info("Analizando video frame a frame...")
        interp = cv2.INTER_AREA if scale_canvas < 1.0 else cv2.INTER_LINEAR

        while cap.isOpened():
            exito, frame_orig = cap.read()
            if not exito:
                break

            frames_totales += 1
            ultimo_frame_orig = frame_orig

            timestamp_actual = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if timestamp_actual <= tiempo_anterior:
                timestamp_actual = tiempo_anterior + dt_nominal
            tiempo_anterior = timestamp_actual

            det = rastreador.procesar(frame_orig)
            v_inst = 0.0

            pos_disco_actual = (det.cx, det.cy) if det else rastreador.ultima_pos
            if pose.activo and frames_totales % CONFIG.pose_cada_n_frames == 0:
                pose.procesar(frame_orig, timestamp_actual, pos_disco=pos_disco_actual)

            det_canvas = None
            if det is not None:
                frames_con_disco += 1
                puntos_trayectoria.append((det.cx, det.cy))

                det_canvas = Deteccion(
                    cx=int(det.cx * scale_canvas + pad_x),
                    cy=int(det.cy * scale_canvas + pad_y),
                    x_min=det.x_min * scale_canvas + pad_x,
                    y_min=det.y_min * scale_canvas + pad_y,
                    x_max=det.x_max * scale_canvas + pad_x,
                    y_max=det.y_max * scale_canvas + pad_y,
                    alto_caja=det.alto_caja * scale_canvas,
                    m_por_px=det.m_por_px / scale_canvas,
                )
                ultima_det_canvas = det_canvas
                puntos_trayectoria_canvas.append((det_canvas.cx, det_canvas.cy))

                t_eval, y_suave, v_inst = estimador.actualizar(timestamp_actual, det.cy, det.m_por_px)
                rep_cerrada = maquina.actualizar(t_eval, det.cx,
                                                 y_suave, v_inst, det.alto_caja)

                if rep_cerrada:
                    _adjuntar_pose(rep_cerrada)
                    registro_repeticiones.append(rep_cerrada)
                    _recalcular_vl_por_rep(registro_repeticiones)
                    est = estimar_rir_por_velocidad(rep_cerrada.v_avg, perfil, nivel)
                    rir_live = f"~{est:.0f}" if est is not None else None

            resized_orig = cv2.resize(frame_orig, (new_w, new_h), interpolation=interp)
            if pad_x > 0 or pad_y > 0:
                small = cv2.resize(frame_orig, (108, 192), interpolation=cv2.INTER_LINEAR)
                small_dark = cv2.convertScaleAbs(small, alpha=0.35)
                blurred = cv2.GaussianBlur(small_dark, (15, 15), 0)
                canvas_frame = cv2.resize(blurred, (TARGET_W, TARGET_H), interpolation=cv2.INTER_LINEAR)
            else:
                canvas_frame = np.empty((TARGET_H, TARGET_W, 3), dtype=np.uint8)

            canvas_frame[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized_orig

            if (maquina.estado != "calibrando"
                    and maquina.frames_quietos > fps * CONFIG.seg_inactividad_fin
                    and (maquina.reps > 0 or maquina.estado == "concentrica")):
                rep_final = maquina.cerrar_por_inactividad(timestamp_actual)
                if rep_final:
                    _adjuntar_pose(rep_final)
                    registro_repeticiones.append(rep_final)
                    _recalcular_vl_por_rep(registro_repeticiones)
                break

            vl_actual = (calcular_velocity_loss(registro_repeticiones)
                         if len(registro_repeticiones) >= 2 else None)

            renderizar_frame(
                frame=canvas_frame,
                det=det_canvas,
                trayectoria=puntos_trayectoria_canvas,
                v_inst=v_inst,
                reps=maquina.reps,
                fase=maquina.estado,
                registro=registro_repeticiones,
                vl_actual=vl_actual,
                rir_txt=rir_live,
                active_area=active_area,
                nombre_ejercicio=nombre_ejercicio,
                carga_kg=carga_kg,
            )

            video_writer.write(canvas_frame)

        if maquina is not None:
            rep_final = maquina.cerrar_por_inactividad(timestamp_actual)
            if rep_final:
                _adjuntar_pose(rep_final)
                registro_repeticiones.append(rep_final)
                _recalcular_vl_por_rep(registro_repeticiones)
                logger.info(f"Rep #{rep_final.rep} cerrada al terminar el "
                            f"video (grabación cortada en el lockout).")

        reps_finales = maquina.reps if maquina else len(registro_repeticiones)
        vl_final = (calcular_velocity_loss(registro_repeticiones)
                    if len(registro_repeticiones) >= 2 else None)
        if registro_repeticiones:
            est_fin = estimar_rir_por_velocidad(registro_repeticiones[-1].v_avg, perfil, nivel)
            rir_live = f"~{est_fin:.0f}" if est_fin is not None else rir_live

        if ultimo_frame_orig is not None:
            resized_fin = cv2.resize(ultimo_frame_orig, (new_w, new_h), interpolation=interp)
            if pad_x > 0 or pad_y > 0:
                small = cv2.resize(ultimo_frame_orig, (108, 192), interpolation=cv2.INTER_LINEAR)
                small_dark = cv2.convertScaleAbs(small, alpha=0.35)
                blurred = cv2.GaussianBlur(small_dark, (15, 15), 0)
                final_canvas = cv2.resize(blurred, (TARGET_W, TARGET_H), interpolation=cv2.INTER_LINEAR)
            else:
                final_canvas = np.empty((TARGET_H, TARGET_W, 3), dtype=np.uint8)
            final_canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized_fin
        else:
            final_canvas = np.full((TARGET_H, TARGET_W, 3), _HUB["bg"], dtype=np.uint8)

        renderizar_frame(
            frame=final_canvas,
            det=ultima_det_canvas,
            trayectoria=puntos_trayectoria_canvas,
            v_inst=0.0,
            reps=reps_finales,
            fase="finalizado",
            registro=registro_repeticiones,
            vl_actual=vl_final,
            rir_txt=rir_live,
            active_area=active_area,
            nombre_ejercicio=nombre_ejercicio,
            carga_kg=carga_kg,
        )

        frames_congelados = int(round(video_writer.fps * 2.0))
        raw_final_bytes = final_canvas.tobytes()
        for _ in range(frames_congelados):
            video_writer.write_raw_bytes(raw_final_bytes)

    except Exception as e:
        logger.error(f"Crasheo en ciclo principal: {e}")
        traceback.print_exc()
    finally:
        if cap:
            cap.release()
        if video_writer:
            video_writer.release()

        if es_temp_sdr and os.path.exists(ruta_video_proc):
            try:
                os.remove(ruta_video_proc)
            except OSError:
                pass

    reps_ok = maquina.reps if maquina else 0
    if reps_ok == 0 or not registro_repeticiones:
        max_vel = max(maquina.vels_serie) if (maquina and maquina.vels_serie) else 0.0
        fase_max = maquina.fase_maxima if maquina else "init"
        descenso = maquina.max_descenso_px if maquina else 0.0
        msg_error = _generar_autopsia_vbt(
            frames_totales, frames_con_disco, fase_max, descenso, max_vel)
        _convertir_video_h264(ruta_temporal, ruta_salida)
        return {"estado": "error", "mensaje": msg_error,
                "ruta_video_renderizado": ruta_salida}

    analisis = analizar_serie(registro_repeticiones, perfil, nivel_atleta, carga_kg)

    reporte_whatsapp = _construir_reporte_whatsapp(
        nombre_ejercicio, registro_repeticiones, analisis, carga_kg)
    reporte_tecnico = _construir_reporte_tecnico(
        nombre_ejercicio, registro_repeticiones, analisis, carga_kg)

    if numero_usuario:
        _notificar_whatsapp_seguro(reporte_whatsapp, numero_usuario, callback_notificacion)

    contexto_base = _generar_contexto_llm(
        reps_completadas=reps_ok,
        velocidades_globales=maquina.vels_serie,
        registro=registro_repeticiones,
        ejercicio=nombre_ejercicio,
        nivel_atleta=nivel_atleta,
        numero_usuario=numero_usuario,
        analisis=analisis,
        carga_kg=carga_kg,
    )
    contexto_agente = (f"EJERCICIO DE ESTA SERIE: "
                       f"{nombre_ejercicio.upper()}\n\n{contexto_base}")

    _convertir_video_h264(ruta_temporal, ruta_salida)

    v_prom = round(float(np.mean([r.v_avg for r in registro_repeticiones])), 3)

    return {
        "estado": "exito",
        "reps": reps_ok,
        "v_promedio_global": v_prom,
        "detalle_repeticiones": [r.a_dict() for r in registro_repeticiones],
        "ruta_video_renderizado": ruta_salida,
        "contexto_agente": contexto_agente,
        "analisis_serie": analisis,
        "reporte_whatsapp": reporte_whatsapp,
        "reporte_tecnico": reporte_tecnico,
        "carga_kg": carga_kg,
        "e1rm_estimado_kg": analisis.get("e1rm_kg"),
        "vl_pct": analisis.get("vl_pct"),
        "reps_en_reserva": analisis.get("reps_restantes"),
    }
