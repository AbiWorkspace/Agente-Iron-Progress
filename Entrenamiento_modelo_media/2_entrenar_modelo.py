import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.svm import SVC
from sklearn.neighbors import KNeighborsClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import accuracy_score
import joblib
import os
import matplotlib.pyplot as plt

print("=======================================")
print("⚔️ INICIANDO MEGA TORNEO DE MODELOS (TOP 5) ⚔️")
print("=======================================")

# 1. Cargar el dataset que creaste
try:
    df = pd.read_csv("datos_entrenamiento1.csv")
    print(f"Dataset cargado con éxito. Total de videos analizados: {len(df)}")
except FileNotFoundError:
    print("Error: No se encontró 'datos_entrenamiento1.csv'. Asegúrate de estar en la carpeta correcta.")
    exit()

# 2. Separar las características (X) y la respuesta correcta (y)
columnas_a_ignorar = ['etiqueta']
if 'video' in df.columns:
    columnas_a_ignorar.append('video')
X = df.drop(columns=columnas_a_ignorar)
y = df['etiqueta']

# 3. Dividir los datos (80% para estudiar, 20% para hacerle un examen final)
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, shuffle=True)

# 4. Definir el "Top 5" de competidores
# 4. Definir el "Top 5" de competidores
modelos = {
    "Gradient Boosting Clásico": Pipeline([
        ("scaler", StandardScaler()),
        ("clf", GradientBoostingClassifier(random_state=42))
    ]),
    "Random Forest": Pipeline([
        ("scaler", StandardScaler()),
        ("clf", RandomForestClassifier(n_estimators=100, random_state=42))
    ]),
    "Regresión Logística": Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=1000, random_state=42))
    ]),
    "SVC (Vectores de Soporte)": Pipeline([
        ("scaler", StandardScaler()),
        ("clf", SVC(probability=True, kernel='rbf', random_state=42))
    ]),
    "KNN (Vecinos cercanos)": Pipeline([
        ("scaler", StandardScaler()),
        ("clf", KNeighborsClassifier(n_neighbors=3))
    ])
}

UMBRAL_CONFIANZA = 0.45
resultados_precision = {}

mejor_precision = 0
mejor_nombre = ""
mejor_modelo = None

# 5. Entrenar y evaluar cada modelo
print("\nIniciando evaluaciones...")

for nombre, pipeline in modelos.items():
    # Entrenar
    pipeline.fit(X_train, y_train)

    # Examen final
    probabilidades = pipeline.predict_proba(X_test)
    etiquetas_clases = pipeline.classes_

    predicciones_finales = []

    for prob in probabilidades:
        max_prob = np.max(prob)
        if max_prob >= UMBRAL_CONFIANZA:
            clase_predicha = etiquetas_clases[np.argmax(prob)]
            predicciones_finales.append(clase_predicha)
        else:
            predicciones_finales.append("Ejercicio desconocido")

    # Resultados del modelo actual
    precision = accuracy_score(y_test, predicciones_finales)
    desconocidos = predicciones_finales.count("Ejercicio desconocido")

    # Guardamos porcentaje para la gráfica
    resultados_precision[nombre] = precision * 100

    print(f"\n--- {nombre} ---")
    print(f"Precisión estricta: {precision * 100:.2f}%")
    print(f"Marcados como desconocidos: {desconocidos} de {len(X_test)}")

    # Seleccionamos al mejor
    if precision > mejor_precision:
        mejor_precision = precision
        mejor_nombre = nombre
        mejor_modelo = pipeline

# 6. Coronar al campeón y exportarlo
print("\n=======================================")
print(f"🏆 EL GANADOR ES: {mejor_nombre} con {mejor_precision * 100:.2f}% de precisión! 🏆")
print("=======================================")

ruta_guardado = "clasificador_ejercicios.pkl"
joblib.dump(mejor_modelo, ruta_guardado)
print(f"📦 El modelo campeón ha sido empaquetado como: {ruta_guardado}")

# ==========================================
# 7. GENERACIÓN DE LA GRÁFICA COMPARATIVA
# ==========================================
print("\nGenerando gráfica comparativa...")
nombres = list(resultados_precision.keys())
valores = list(resultados_precision.values())

# Hacemos la figura un poco más ancha para que quepan los 5 nombres
plt.figure(figsize=(12, 6))

# Paleta de 5 colores
colores = ['#9b59b6', '#3498db', '#e74c3c', '#2ecc71', '#f1c40f']
barras = plt.bar(nombres, valores, color=colores)

# Añadir el número de porcentaje encima de cada barra
for barra in barras:
    yval = barra.get_height()
    plt.text(barra.get_x() + barra.get_width() / 2, yval + 1, f'{yval:.1f}%', ha='center', va='bottom',
             fontweight='bold')

plt.ylim(0, 105)
plt.ylabel('Precisión (%)', fontweight='bold')
plt.title('Top 5 Modelos: Comparación de Precisión', fontweight='bold', fontsize=14)

# Rotamos ligeramente los nombres para que no se amontonen
plt.xticks(rotation=15, ha='right')
plt.grid(axis='y', linestyle='--', alpha=0.7)

# Ajustar los márgenes para que el texto rotado no se corte
plt.tight_layout()

# Exportar
ruta_grafica = "top5_comparacion_modelos.png"
plt.savefig(ruta_grafica)
print(f"📊 ¡Gráfica exportada exitosamente como: {ruta_grafica}!")

plt.show()