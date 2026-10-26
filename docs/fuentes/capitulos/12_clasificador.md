# Cap. 12 · Clasificador de ejercicios

> Estado: **hecho (borrador)** · Tarea: F7.2 · Fecha: 2026-08-27
> Ficheros fuente: `Entrenamiento_modelo_media/1_crear_dataset.py` (142 líneas),
> `2_entrenar_modelo.py` (151 líneas), `datos_entrenamiento1.csv`,
> `services/vision_service.py` (176 líneas)
>
> **Este capítulo no existe en el PDF v9.1.** El clasificador tampoco aparece mencionado en
> su índice pese a ser un modelo de aprendizaje automático entrenado desde cero por el propio
> proyecto — a diferencia de MediaPipe y YOLO (Cap. 11), que son modelos preentrenados de
> terceros.

---

## 12.1 Del vídeo a 23 variables numéricas

`1_crear_dataset.py` recorre una carpeta con un subdirectorio por ejercicio, pasa cada vídeo
por MediaPipe Pose y comprime la serie temporal completa de cada vídeo en un vector fijo de
características: mínimo, máximo, rango, media y desviación estándar de los ángulos de rodilla,
cadera, codo y hombro (calculados con `calcular_angulo`, geometría de tres puntos vía producto
escalar), más tres estadísticos de distancia muñeca-cadera. El vídeo se descarta solo si el
cuerpo no es visible en un número suficiente de fotogramas.

**Verificado en esta sesión, contando columnas reales del CSV de entrenamiento**
(`datos_entrenamiento1.csv`): 25 columnas totales, de las cuales `video` y `etiqueta` no son
características — quedan exactamente **23 variables de entrada**, confirmando la cifra que el
README ya declaraba, ahora contrastada contra el dato en disco en vez de contra el texto del
script.

## 12.2 El dataset: tamaño y composición real

| Clase | Vídeos | % del total |
|---|---|---|
| Press de banca | 19 | 38,8 % |
| Sentadilla | 12 | 24,5 % |
| Peso muerto | 11 | 22,4 % |
| Dominadas | 7 | 14,3 % |
| **Total** | **49** | 100 % |

Distribución extraída directamente del CSV en esta sesión (`pandas.value_counts()`), no
tomada del README. El dataset está **desbalanceado**: press de banca tiene 2,7 veces más
ejemplos que dominadas. Esto es una limitación real que debe declararse en el Cap. 16
(Resultados) y no se documentaba explícitamente en ninguna parte del proyecto hasta esta
sesión — con 49 ejemplos totales y una partición de prueba de solo 10 vídeos (20 %, ver
§12.3), el desbalance entre clases pesa proporcionalmente más que en un dataset grande.

## 12.3 El torneo de algoritmos: resultados reales, reproducidos en esta sesión

`2_entrenar_modelo.py` enfrenta cinco algoritmos de clasificación —todos dentro de un
`Pipeline` con `StandardScaler`, sobre el mismo particionamiento 80/20 con
`random_state=42`— y aplica un umbral de confianza (`UMBRAL_CONFIANZA = 0,45`) por debajo del
cual la predicción se descarta como *"Ejercicio desconocido"* en vez de forzar una etiqueta.

Esta sesión de documentación **reejecutó el script de entrenamiento completo** contra el CSV
real (no se transcribieron cifras de memoria ni de la documentación previa):

| Algoritmo | Precisión estricta | Marcados como "desconocido" (de 10) |
|---|---|---|
| **Gradient Boosting** (ganador) | **80,00 %** | 0 |
| Random Forest | 70,00 % | 3 |
| Regresión Logística | 70,00 % | 2 |
| SVC (kernel RBF) | 30,00 % | 3 |
| KNN (k=3) | 30,00 % | 5 |

Gradient Boosting (Friedman, 2001) gana con el conjunto de prueba completo (10 de 10
predicciones con confianza suficiente para no marcarse como desconocido) y la mayor precisión.
Es el modelo que se empaqueta como `clasificador_ejercicios.pkl` y el que
`services/vision_service.py` carga en producción — confirmado en esta sesión que la
reejecución produce un `.pkl` **bit a bit idéntico** al ya versionado en el repositorio
(`git status` no detectó cambio en los artefactos binarios tras la reejecución), evidencia de
que el proceso de entrenamiento es completamente determinista dado el mismo dataset.

### Advertencia metodológica para el Cap. 16 (Resultados)

El 80 % de precisión de Gradient Boosting se mide sobre **10 vídeos de prueba**. Un solo
vídeo mal clasificado cambia el resultado en 10 puntos porcentuales. Esta cifra debe
presentarse en la tesina con esa salvedad explícita —no como una precisión estimada con
robustez estadística—, y se recomienda, como trabajo futuro del propio proyecto (Cap. 19),
ampliar el dataset antes de reportar esta métrica con mayor peso argumental.

## 12.4 El umbral de rechazo por confianza como decisión de diseño

Que el clasificador pueda responder "no sé qué ejercicio es este" en vez de forzar siempre una
de las cuatro clases conocidas es la misma filosofía que gobierna el resto del sistema: es
preferible una incertidumbre declarada a una afirmación falsa con apariencia de certeza (Cap.
1 §1.2; Cap. 7 §7.3, plantilla honesta ante reincidencia; Cap. 11 §11.4, rango "incierto" del
doble estimador de RIR). El umbral de 0,45 es, sin embargo, un valor fijado por el
desarrollador sin una búsqueda de hiperparámetro documentada en el código — se señala así para
que la tesina no le atribuya más rigor estadístico del que tiene.

## 12.5 El clasificador en producción: el dataset que se alimenta solo

Cada vídeo que entra por WhatsApp se archiva en `data/dataset_recoleccion/` bajo la carpeta del
ejercicio que el propio clasificador predijo, alimentando la siguiente ronda de reentrenamiento
sin intervención manual. Esto se desarrolla como sección propia en el Cap. 13 (F7.5) porque
introduce un riesgo de retroalimentación (una predicción errónea contamina el dataset del
ejercicio equivocado) que merece tratamiento separado del clasificador en sí.
