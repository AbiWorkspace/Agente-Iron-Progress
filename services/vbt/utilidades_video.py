# -*- coding: utf-8 -*-
"""
services/vbt/utilidades_video.py — Utilidades de decodificación, preprocesamiento HDR y codificación FFmpeg.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from typing import Optional, Callable

import cv2
import imageio_ffmpeg
import numpy as np
from ultralytics import YOLO

from services.vbt.configuracion import logger

try:
    from data.meta_service import enviar_mensaje_whatsapp
except Exception:
    try:
        from services.data.meta_service import enviar_mensaje_whatsapp
    except Exception:
        def enviar_mensaje_whatsapp(texto: str, telefono_destino: str):
            pass


# ==============================================================================
# CACHÉ GLOBAL DE MODELOS YOLO
# ==============================================================================
_CACHE_MODELOS_YOLO: dict[str, YOLO] = {}


def _obtener_modelo_yolo(ruta_modelo: str) -> YOLO:
    """Devuelve una instancia cacheada del modelo YOLO indexada por ruta para evitar
    recargas repetitivas desde disco y fragmentación de memoria GPU/VRAM."""
    if ruta_modelo not in _CACHE_MODELOS_YOLO:
        logger.info(f"Cargando modelo YOLO en caché global: {ruta_modelo}")
        _CACHE_MODELOS_YOLO[ruta_modelo] = YOLO(ruta_modelo)
    return _CACHE_MODELOS_YOLO[ruta_modelo]


def _notificar_whatsapp_seguro(texto: str, telefono_destino: str, callback: Optional[Callable] = None) -> None:
    """Desacopla las llamadas a la API de WhatsApp en un hilo en segundo plano para
    evitar que la latencia de red de Meta bloquee el motor de visión artificial."""
    def _tarea():
        try:
            if callback:
                callback(texto=texto, telefono_destino=telefono_destino)
            else:
                enviar_mensaje_whatsapp(texto=texto, telefono_destino=telefono_destino)
        except Exception as e:
            logger.warning(f"Error en notificación WhatsApp en segundo plano: {e}")

    threading.Thread(target=_tarea, daemon=True).start()


def _analizar_propiedades_video(ruta_video: str) -> tuple[bool, bool]:
    """Detecta si el video de entrada es HDR y/o entrelazado (interlaced) sin falsos positivos."""
    es_hdr = False
    es_entrelazado = False

    # 1. Intentar con ffprobe consultando los campos exactos del stream de video
    ffprobe_exe = shutil.which("ffprobe")
    if not ffprobe_exe:
        try:
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            posible_ffprobe = os.path.join(os.path.dirname(ffmpeg_exe), "ffprobe")
            if os.path.exists(posible_ffprobe):
                ffprobe_exe = posible_ffprobe
        except Exception:
            pass

    if ffprobe_exe:
        try:
            res = subprocess.run(
                [
                    ffprobe_exe, "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=field_order,color_transfer,pix_fmt",
                    "-of", "default=noprint_wrappers=1:nokey=0", ruta_video
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8
            )
            props = dict(line.strip().split("=", 1) for line in res.stdout.splitlines() if "=" in line)
            fo = props.get("field_order", "").lower().strip()
            ct = props.get("color_transfer", "").lower().strip()
            pf = props.get("pix_fmt", "").lower().strip()

            if fo in ("tt", "bb", "tb", "bt") or "interlaced" in fo:
                es_entrelazado = True

            hdr_transfers = ("smpte2084", "arib-std-b67", "linear", "bt2020-10", "bt2020-12")
            if ct in hdr_transfers or "10le" in pf or "p010" in pf:
                es_hdr = True

            return es_hdr, es_entrelazado
        except Exception as e:
            logger.debug(f"ffprobe falló al analizar video ({e}); usando fallback ffmpeg.")

    # 2. Fallback a ffmpeg -i inspeccionando exclusivamente la línea del Stream de video
    try:
        try:
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg_exe = "ffmpeg"

        res = subprocess.run([ffmpeg_exe, "-i", ruta_video], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8)
        err = res.stderr

        match_stream = re.search(r"Stream #\d+:\d+.*?: Video:.*", err)
        if match_stream:
            linea_video = match_stream.group(0).lower()
            if re.search(r"\b(interlaced|tff|bff)\b", linea_video):
                es_entrelazado = True
            if re.search(r"\b(bt2020|arib-std-b67|smpte2084|hlg|hdr10|yuv420p10le|yuv422p10le|p010le)\b", linea_video):
                es_hdr = True
    except Exception as e:
        logger.warning(f"No se pudieron verificar las propiedades del video: {e}")

    return es_hdr, es_entrelazado


def _preprocesar_video_hdr_si_es_necesario(ruta_video: str) -> tuple[str, bool]:
    """Si el video es HDR o entrelazado, lo preprocesa con FFmpeg (bwdif / zscale) a Rec. 709 SDR progresivo sin dientes de sierra."""
    es_hdr, es_entrelazado = _analizar_propiedades_video(ruta_video)
    if not es_hdr and not es_entrelazado:
        return ruta_video, False

    logger.info(f"Preprocesando video (HDR={es_hdr}, Entrelazado={es_entrelazado}) para eliminar dientes de sierra...")
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg_exe = "ffmpeg"

    dir_name, base_name = os.path.split(ruta_video)
    ruta_proc = os.path.join(dir_name, "proc_" + base_name)

    filtros = []
    if es_entrelazado:
        filtros.append("bwdif=mode=0:parity=-1:deint=1")
    if es_hdr:
        filtros.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    else:
        filtros.append("format=yuv420p")

    vf_filter = ",".join(filtros)

    cmd = [
        ffmpeg_exe, "-y",
        "-i", ruta_video,
        "-vf", vf_filter,
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
        ruta_proc
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        logger.info("Preprocesamiento de video completado.")
        return ruta_proc, True
    except Exception as e:
        logger.error(f"Fallo en preprocesamiento de video via FFmpeg: {e}. Se utilizará el video original.")
        return ruta_video, False


def inicializar_video(ruta_video: str, numero_usuario: Optional[str] = None,
                      callback_notificacion: Optional[Callable] = None):
    """Inicializa la captura de video vía OpenCV comprobando dimensiones y framerate."""
    if numero_usuario:
        _notificar_whatsapp_seguro("Procesando video...", numero_usuario, callback_notificacion)

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


class EscritorVideoFFmpeg:
    """Escritor de video de alta calidad mediante canal (pipe) directo a FFmpeg.
    - Preserva el audio ambiente original en AAC a 192 kbps y 48 kHz (-shortest).
    - Codificación en una pasada con CRF 18 y VBV Constrained Encoding (-maxrate / -bufsize).
    - Elimina deadlock en el pipe con loglevel error, stdout DEVNULL y drenaje en hilo background.
    - Flags específicos para Reels/Stories: Profile High 4.1, GOP cerrado de 2s y rango limitado TV."""

    def __init__(self, ruta_salida: str, fps: float, ancho: int = 1080, alto: int = 1920,
                 crf: int = 18, ruta_audio_original: Optional[str] = None,
                 duracion_s: Optional[float] = None):
        self.ruta_salida = ruta_salida
        # Limitar framerate a un máximo de 60 fps constantes para Meta
        self.fps = min(float(fps), 60.0)
        self.ancho = ancho
        self.alto = alto
        directorio, nombre = os.path.split(ruta_salida)
        self.ruta_temporal = os.path.join(directorio, "temp_" + nombre)

        try:
            self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            self.ffmpeg_exe = "ffmpeg"

        # Cálculo de Bitrate VBV para garantizar matemáticamente peso < 15 MB
        if duracion_s and duracion_s > 0:
            calc_max = int((14.2 * 8192) / duracion_s - 192)
            maxrate_kbps = min(7500, max(1500, calc_max))
        else:
            maxrate_kbps = 6500
        bufsize_kbps = maxrate_kbps * 2

        gop_size = max(1, int(round(self.fps * 2)))
        keyint_min = max(1, int(round(self.fps)))

        cmd = [
            self.ffmpeg_exe, "-y",
            "-loglevel", "error",
            "-nostats",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{ancho}x{alto}",
            "-pix_fmt", "bgr24",
            "-r", f"{self.fps:.3f}",
            "-i", "-",
        ]

        if ruta_audio_original and os.path.exists(ruta_audio_original):
            cmd.extend(["-i", ruta_audio_original])
            cmd.extend(["-map", "0:v:0", "-map", "1:a:0?"])
            cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest"])
        else:
            cmd.extend(["-an"])

        cmd.extend([
            "-c:v", "libx264",
            "-crf", str(crf),
            "-maxrate", f"{maxrate_kbps}k",
            "-bufsize", f"{bufsize_kbps}k",
            "-preset", "fast",
            "-profile:v", "high",
            "-level:v", "4.1",
            "-g", str(gop_size),
            "-keyint_min", str(keyint_min),
            "-sc_threshold", "0",
            "-pix_fmt", "yuv420p",
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-colorspace", "bt709",
            "-color_range", "tv",
            "-movflags", "+faststart",
            self.ruta_temporal
        ])

        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
            self._usando_pipe = True
            self._stderr_lines = []

            def _drain():
                try:
                    for line in iter(self.proc.stderr.readline, b''):
                        self._stderr_lines.append(line.decode('utf-8', errors='ignore'))
                except Exception:
                    pass

            self._thread_stderr = threading.Thread(target=_drain, daemon=True)
            self._thread_stderr.start()
        except Exception as e:
            logger.warning(f"No se pudo iniciar canal directo de FFmpeg: {e}. Usando fallback OpenCV VideoWriter.")
            self._usando_pipe = False
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self.cv_writer = cv2.VideoWriter(self.ruta_temporal, fourcc, self.fps, (ancho, alto))

    def write(self, frame: np.ndarray) -> None:
        if self._usando_pipe and self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(frame.tobytes())
            except Exception as e:
                logger.error(f"Fallo al escribir cuadro en canal FFmpeg: {e}")
        elif hasattr(self, "cv_writer") and self.cv_writer and self.cv_writer.isOpened():
            self.cv_writer.write(frame)

    def write_raw_bytes(self, raw_bytes: bytes) -> None:
        """Escribe bytes crudos directamente al pipe sin serializar en cada llamada."""
        if self._usando_pipe and self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(raw_bytes)
            except BrokenPipeError:
                self._usando_pipe = False
            except Exception as e:
                logger.error(f"Fallo al escribir bytes en canal FFmpeg: {e}")

    def release(self) -> None:
        if self._usando_pipe and self.proc:
            try:
                if self.proc.stdin:
                    try:
                        self.proc.stdin.close()
                    except Exception:
                        pass
                self.proc.wait(timeout=20)
                if hasattr(self, "_thread_stderr") and self._thread_stderr.is_alive():
                    self._thread_stderr.join(timeout=2)
                if self.proc.returncode != 0:
                    err_txt = "".join(getattr(self, "_stderr_lines", []))
                    logger.error(f"FFmpeg devolvió código {self.proc.returncode}: {err_txt}")
            except Exception as e:
                logger.error(f"Error al cerrar canal de FFmpeg: {e}")
                try:
                    self.proc.kill()
                except Exception:
                    pass
            finally:
                self.proc = None
        elif hasattr(self, "cv_writer") and self.cv_writer:
            try:
                self.cv_writer.release()
            except Exception:
                pass


def _inicializar_escritor_video(ruta_salida: str, fps: float,
                                ruta_audio_original: Optional[str] = None,
                                duracion_s: Optional[float] = None):
    writer = EscritorVideoFFmpeg(
        ruta_salida=ruta_salida,
        fps=fps,
        ancho=1080,
        alto=1920,
        crf=18,
        ruta_audio_original=ruta_audio_original,
        duracion_s=duracion_s
    )
    return writer, writer.ruta_temporal


def _convertir_video_h264(ruta_temporal: str, ruta_salida: str):
    logger.info("Finalizando exportación en alta calidad para Instagram Stories/Reels (Rec. 709 SDR)...")
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg_exe = "ffmpeg"

    if os.path.exists(ruta_temporal) and os.path.abspath(ruta_temporal) != os.path.abspath(ruta_salida):
        try:
            os.replace(ruta_temporal, ruta_salida)
        except Exception:
            cmd = [
                ffmpeg_exe, "-y",
                "-i", ruta_temporal,
                "-c", "copy",
                "-movflags", "+faststart",
                ruta_salida
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if os.path.exists(ruta_temporal):
                try:
                    os.remove(ruta_temporal)
                except OSError:
                    pass

    # Salvaguarda de tamaño para WhatsApp Cloud API (Límite máximo de Meta: 16 MB)
    MAX_BYTES = int(15.5 * 1024 * 1024)
    if os.path.exists(ruta_salida):
        tam_actual = os.path.getsize(ruta_salida)
        if tam_actual > MAX_BYTES:
            logger.warning(f"Video ({tam_actual / (1024*1024):.2f} MB) excede 15.5 MB. Re-comprimiendo en una sola pasada VBV...")
            ruta_comp = ruta_salida + ".comp.mp4"
            cmd_comp = [
                ffmpeg_exe, "-y",
                "-i", ruta_salida,
                "-c:v", "libx264",
                "-crf", "22",
                "-maxrate", "3000k",
                "-bufsize", "6000k",
                "-preset", "fast",
                "-profile:v", "high",
                "-level:v", "4.1",
                "-pix_fmt", "yuv420p",
                "-c:a", "copy",
                "-movflags", "+faststart",
                ruta_comp
            ]
            res_comp = subprocess.run(cmd_comp, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if res_comp.returncode == 0 and os.path.exists(ruta_comp):
                os.replace(ruta_comp, ruta_salida)
            elif os.path.exists(ruta_comp):
                try:
                    os.remove(ruta_comp)
                except OSError:
                    pass
