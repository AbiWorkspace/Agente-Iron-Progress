import cv2
import mediapipe as mp
import numpy as np
import pandas as pd
import os
import glob

# Inicializar MediaPipe Pose (Ahora sí funcionará perfecto)
mp_pose = mp.solutions.pose
pose = mp_pose.Pose(static_image_mode=False, min_detection_confidence=0.5)


def calcular_angulo(a, b, c):
    """Calcula el ángulo entre 3 puntos (x, y)"""
    a = np.array(a)
    b = np.array(b)
    c = np.array(c)

    radianes = np.arctan2(c[1] - b[1], c[0] - b[0]) - np.arctan2(a[1] - b[1], a[0] - b[0])
    angulo = np.abs(radianes * 180.0 / np.pi)

    if angulo > 180.0:
        angulo = 360 - angulo
    return angulo


def extraer_caracteristicas_video(ruta_video):
    cap = cv2.VideoCapture(ruta_video)

    angulos_rodilla, angulos_cadera, angulos_codo, angulos_hombro = [], [], [], []
    distancias_muneca_cadera = []
    frame_count = 0

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1
        # OPTIMIZACIÓN: Procesar solo 1 de cada 3 frames (ideal para producción rápida)
        if frame_count % 3 != 0:
            continue

        frame = cv2.resize(frame, (640, 480))
        image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(image_rgb)

        if results.pose_landmarks:
            landmarks = results.pose_landmarks.landmark

            # Coordenadas (Considera promediar lado izquierdo y derecho en el futuro)
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

            angulos_rodilla.append(calcular_angulo(cadera, rodilla, tobillo))
            angulos_cadera.append(calcular_angulo(hombro, cadera, rodilla))
            angulos_codo.append(calcular_angulo(hombro, codo, muneca))
            # NUEVO ÁNGULO: Útil para distinguir dominadas y press banca
            angulos_hombro.append(calcular_angulo(cadera, hombro, codo))
            dist_y = cadera[1] - muneca[1]
            distancias_muneca_cadera.append(dist_y)

    cap.release()

    if len(angulos_rodilla) < 5:
        return None  # Retornamos None si no es válido

    # NUEVAS CARACTERÍSTICAS: Le damos al modelo el contexto completo del movimiento
    caracteristicas = {}
    if distancias_muneca_cadera:
        caracteristicas['media_dist_muneca_cadera'] = round(np.mean(distancias_muneca_cadera), 3)
        caracteristicas['max_dist_muneca_cadera'] = round(np.max(distancias_muneca_cadera), 3)
        caracteristicas['min_dist_muneca_cadera'] = round(np.min(distancias_muneca_cadera), 3)

    for nombre, lista_angulos in [("rodilla", angulos_rodilla), ("cadera", angulos_cadera),
                                  ("codo", angulos_codo), ("hombro", angulos_hombro)]:

        # Filtramos glitches
        q5, q95 = np.percentile(lista_angulos, 5), np.percentile(lista_angulos, 95)
        lista_limpia = [a for a in lista_angulos if q5 <= a <= q95]

        if not lista_limpia: lista_limpia = lista_angulos  # Fallback

        caracteristicas[f'min_{nombre}'] = round(np.min(lista_limpia), 2)
        caracteristicas[f'max_{nombre}'] = round(np.max(lista_limpia), 2)
        caracteristicas[f'rango_{nombre}'] = round(np.max(lista_limpia) - np.min(lista_limpia), 2)
        caracteristicas[f'media_{nombre}'] = round(np.mean(lista_limpia), 2)
        caracteristicas[f'std_{nombre}'] = round(np.std(lista_limpia), 2)

    return caracteristicas


# ==========================================
# RUTINA DE ESCANEO DE CARPETAS (ACTUALIZADA)
# ==========================================
if __name__ == "__main__":
    print("Iniciando extracción biomecánica avanzada...")

    # Ajusta esta ruta si es necesario
    ruta_base = "/home/abimael/Escritorio/Entrenamiento"

    datos = []

    for etiqueta in os.listdir(ruta_base):
        ruta_clase = os.path.join(ruta_base, etiqueta)
        if os.path.isdir(ruta_clase):
            for video_path in glob.glob(os.path.join(ruta_clase, "*.mp4")):
                print(f"Procesando: {video_path}...")

                # AHORA RECIBIMOS UN DICCIONARIO COMPLETO, NO 3 VARIABLES
                caracteristicas = extraer_caracteristicas_video(video_path)

                # FILTRO ANTI-BASURA: Si la función devolvió None (video corto o sin cuerpo)
                if caracteristicas is None:
                    print(f"⚠️ Video descartado por falta de visibilidad: {video_path}")
                    continue

                # Agregamos los metadatos al diccionario
                caracteristicas["video"] = os.path.basename(video_path)
                caracteristicas["etiqueta"] = etiqueta

                # Lo añadimos a la lista principal
                datos.append(caracteristicas)

    # Creamos el DataFrame mágico
    df = pd.DataFrame(datos)

    # Reorganizamos las columnas para que se vea ordenado en el CSV (video al inicio, etiqueta al final)
    columnas = ['video'] + [c for c in df.columns if c not in ['video', 'etiqueta']] + ['etiqueta']
    df = df[columnas]

    df.to_csv("datos_entrenamiento1.csv", index=False)
    print(f"¡Dataset Creado! Guardado con {len(df.columns) - 2} características en datos_entrenamiento1.csv")