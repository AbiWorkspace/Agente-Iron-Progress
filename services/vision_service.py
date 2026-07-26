import sys

sys.modules['tensorflow'] = None

import cv2
import numpy as np
import pandas as pd
import joblib
import mediapipe as mp
import os
import math
import threading

# 1. Inicializar MediaPipe Pose
mp_pose = mp.solutions.pose
pose = mp_pose.Pose(static_image_mode=False, min_detection_confidence=0.5)

# El objeto Pose de MediaPipe NO es reentrante: mantiene estado interno de
# tracking entre frames. `predecir_ejercicio` se invoca desde `asyncio.to_thread`,
# así que dos atletas mandando video a la vez lo usaban en paralelo y el grafo
# se corrompía (predicciones absurdas en el mejor caso, segfault en el peor).
# El candado serializa la inferencia, que además es CPU-bound: no se pierde
# rendimiento real, solo se evita el pisotón.
_candado_pose = threading.Lock()

# 2. Rutas dinámicas
RUTA_BASE_PROYECTO = os.path.dirname(os.path.dirname(__file__))
RUTA_MODELO = os.path.join(RUTA_BASE_PROYECTO, "Entrenamiento_modelo_media", "clasificador_ejercicios.pkl")

# 3. Cargar el Cerebro Campeón
try:
    modelo_clasificador = joblib.load(RUTA_MODELO)
    print("[VISION SERVICE]: 🏆 Cerebro de 23 variables cargado correctamente. ✅")
except Exception as e:
    print(f"[VISION SERVICE CRÍTICO]: No se pudo cargar el modelo: {e}")
    modelo_clasificador = None


def calcular_angulo(a, b, c):
    """Calcula el ángulo entre 3 puntos (x, y)"""
    a, b, c = np.array(a), np.array(b), np.array(c)
    radianes = np.arctan2(c[1] - b[1], c[0] - b[0]) - np.arctan2(a[1] - b[1], a[0] - b[0])
    angulo = np.abs(radianes * 180.0 / np.pi)
    if angulo > 180.0:
        angulo = 360 - angulo
    return angulo


def calcular_distancia(a, b):
    """Calcula la distancia Euclidiana entre 2 puntos"""
    return math.dist(a, b)


def limpiar_datos(arreglo):
    """Filtra glitches y devuelve min, max, rango, media y std"""
    if not arreglo:
        return 0, 0, 0, 0, 0
    # Usamos percentiles para ignorar "parpadeos" de la cámara (glitches a 0)
    val_min = np.percentile(arreglo, 5)
    val_max = np.percentile(arreglo, 95)
    rango = val_max - val_min
    media = np.mean(arreglo)
    std = np.std(arreglo)
    return val_min, val_max, rango, media, std


def predecir_ejercicio(ruta_video: str) -> str:
    """Procesa el video y extrae las 23 variables exactas para el modelo."""
    if modelo_clasificador is None:
        return "Ejercicio_Desconocido"

    # Listas para almacenar la biometría frame a frame
    distancias_muneca_cadera = []
    angulos_rodilla = []
    angulos_cadera = []
    angulos_codo = []
    angulos_hombro = []

    cap = cv2.VideoCapture(ruta_video)
    if not cap.isOpened():
        print(f"[VISION SERVICE]: OpenCV no pudo abrir el video {ruta_video}")
        return "Ejercicio_Desconocido"

    try:
        with _candado_pose:
            frame_count = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                frame_count += 1
                if frame_count % 3 != 0:  # Saltamos frames para velocidad
                    continue

                frame = cv2.resize(frame, (640, 480))
                image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = pose.process(image_rgb)

                if not results.pose_landmarks:
                    continue

                landmarks = results.pose_landmarks.landmark

                # Coordenadas (Lado derecho)
                cadera = [landmarks[mp_pose.PoseLandmark.RIGHT_HIP.value].x,
                          landmarks[mp_pose.PoseLandmark.RIGHT_HIP.value].y]
                rodilla = [landmarks[mp_pose.PoseLandmark.RIGHT_KNEE.value].x,
                           landmarks[mp_pose.PoseLandmark.RIGHT_KNEE.value].y]
                tobillo = [landmarks[mp_pose.PoseLandmark.RIGHT_ANKLE.value].x,
                           landmarks[mp_pose.PoseLandmark.RIGHT_ANKLE.value].y]
                hombro = [landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER.value].x,
                          landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER.value].y]
                codo = [landmarks[mp_pose.PoseLandmark.RIGHT_ELBOW.value].x,
                        landmarks[mp_pose.PoseLandmark.RIGHT_ELBOW.value].y]
                muneca = [landmarks[mp_pose.PoseLandmark.RIGHT_WRIST.value].x,
                          landmarks[mp_pose.PoseLandmark.RIGHT_WRIST.value].y]

                # 1. Distancia
                distancias_muneca_cadera.append(calcular_distancia(muneca, cadera))
                # 2. Ángulos
                angulos_rodilla.append(calcular_angulo(cadera, rodilla, tobillo))
                angulos_cadera.append(calcular_angulo(hombro, cadera, rodilla))
                angulos_codo.append(calcular_angulo(hombro, codo, muneca))
                angulos_hombro.append(calcular_angulo(cadera, hombro, codo))  # Ángulo del hombro
    finally:
        # `cap.release()` estaba después del bucle: cualquier excepción a mitad
        # del video dejaba el descriptor del archivo abierto para siempre.
        cap.release()

    if len(angulos_rodilla) < 5:
        return "Ejercicio_Desconocido"

    # =========================================================
    # EXTRACCIÓN DE LAS 23 VARIABLES (En el orden estricto)
    # =========================================================
    min_dist, max_dist, _, media_dist, _ = limpiar_datos(distancias_muneca_cadera)

    min_rod, max_rod, rng_rod, med_rod, std_rod = limpiar_datos(angulos_rodilla)
    min_cad, max_cad, rng_cad, med_cad, std_cad = limpiar_datos(angulos_cadera)
    min_cod, max_cod, rng_cod, med_cod, std_cod = limpiar_datos(angulos_codo)
    min_hom, max_hom, rng_hom, med_hom, std_hom = limpiar_datos(angulos_hombro)

    # Nombres EXACTOS de las columnas de tu dataset
    columnas_exactas = [
        'media_dist_muneca_cadera', 'max_dist_muneca_cadera', 'min_dist_muneca_cadera',
        'min_rodilla', 'max_rodilla', 'rango_rodilla', 'media_rodilla', 'std_rodilla',
        'min_cadera', 'max_cadera', 'rango_cadera', 'media_cadera', 'std_cadera',
        'min_codo', 'max_codo', 'rango_codo', 'media_codo', 'std_codo',
        'min_hombro', 'max_hombro', 'rango_hombro', 'media_hombro', 'std_hombro'
    ]

    # Valores mapeados EXACTAMENTE a las columnas
    valores = [[
        media_dist, max_dist, min_dist,
        min_rod, max_rod, rng_rod, med_rod, std_rod,
        min_cad, max_cad, rng_cad, med_cad, std_cad,
        min_cod, max_cod, rng_cod, med_cod, std_cod,
        min_hom, max_hom, rng_hom, med_hom, std_hom
    ]]

    try:
        datos_nuevos = pd.DataFrame(valores, columns=columnas_exactas)

        # 🧠 PREDICTOR DE IA CON UMBRAL DE CONFIANZA
        probabilidades = modelo_clasificador.predict_proba(datos_nuevos)[0]
        max_prob = np.max(probabilidades)

        # Umbral del 45% (igual que en tu entrenamiento)
        if max_prob >= 0.45:
            return str(modelo_clasificador.classes_[np.argmax(probabilidades)])
        else:
            return "Ejercicio_Desconocido"

    except Exception as e:
        print(f"[VISION ERROR CRÍTICO]: {e}")
        return "Ejercicio_Desconocido"