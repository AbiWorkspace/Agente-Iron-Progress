# Cap. 16 · Resultados

> Estado: **hecho (borrador)** · Tareas: F8.1 a F8.7 · Fecha: 2026-08-27
> Evidencia propia de esta sesión marcada **[FRESCA]**; evidencia heredada del PDF v9.1
> (medida 2026-07-30/31, no reejecutada en esta sesión) marcada **[HISTÓRICA]**.

---

## 16.1 Diseño experimental (F8.1)

Para cada objetivo específico del Cap. 1 §1.4, la tabla siguiente indica qué se mide y con qué
instrumento — replicando la estructura de esa tabla, ahora con resultado real donde lo hay:

| Objetivo | Instrumento | Resultado |
|---|---|---|
| 1. Separación cálculo/lenguaje | Redes de validación (Cap. 7) + banco de QA | §16.3, con hallazgo nuevo [FRESCA] |
| 2. Interceptores deterministas | 32 suites (F1.2) | 32/32 suites existentes, cobertura confirmada [FRESCA, F1.2] |
| 3. Eficacia de las redes de validación | Ejecución en vivo + registro de incidencias | §16.3 [FRESCA] — parcial, ver limitación en §16.6 |
| 4. Fundamento bibliográfico del motor de planificación/predictivo | Trazabilidad Cap. 2, 8, 9 | Completo — ver Cap. 2, 8, 9 |
| 5. Cobertura multimodal | Suites de audio (F1.2) + torneo del clasificador | §16.4, §16.5 [FRESCA] |
| 6. Coste de la garantía de veracidad en latencia | Instrumentación de `llm_service.py` | §16.2 [HISTÓRICA, no reejecutada] |
| 7. Comparación con el estado del arte | Cap. 3 | Cualitativo, no cuantitativo |

## 16.2 Latencia [HISTÓRICA — PDF v9.1, medido 2026-07-31]

| Fase | Antes | Después |
|---|---|---|
| Preprocesado del prompt | 17,6 s | 1,2 – 3,5 s |
| Acierto de caché de prefijo | prácticamente nulo | 99,9 % |
| Turno de confirmación simple | 20 – 30 s | 4 – 8 s |

**No reejecutado en esta sesión.** Esta tabla se hereda del PDF v9.1 sin verificación
independiente porque reproducirla exige un protocolo de medición específico
(`services/testbed/latencia/`, 14 bancos) fuera del alcance de esta sesión de documentación.
Se recomienda como tarea de una futura sesión de trabajo reejecutar al menos `bench5.py` o
`bench6.py` para confirmar que las cifras siguen vigentes tras los cambios acumulados desde
julio.

## 16.3 Resultados de las redes de validación [FRESCA]

Se ejecutó en vivo el bloque de QA "fatiga" (2 casos) contra el orquestador real, con el
modelo `agent:latest` cargado en el servidor Ollama activo del entorno (27-ago-2026, 21:34h).

| Caso | Veredicto automático | Incidencia real observada |
|---|---|---|
| "la primera serie de banca me salió durísima, 100x5 y rpe 9.5" | ✓ pasa | Ninguna. Autorregulación correcta: 95 kg × 2 series, tope RPE 8. Latencia: 122,8 s (incluye 67,6 s de carga de modelo en frío — primer turno de la sesión) |
| "llevo toda la semana molido, ¿me toca descarga?" | ✓ pasa (aserciones de herramienta correctas) | **Falso positivo de la red 4 (Claim falso)** sobre una respuesta que era honesta, seguido de una reescritura forzada que degrada la respuesta y filtra una fuga de secreto profesional (detalle completo en Cap. 7 §7.5) |

**Hallazgo central de esta sección:** de 2 casos ejecutados en vivo, **1 mostró una falla real
no detectada por el veredicto automático del propio banco de QA** — una tasa de 50 % sobre
una muestra extremadamente pequeña, que **no debe generalizarse** como tasa de fallo del
sistema (N=2 es demasiado pequeño para cualquier inferencia estadística), pero que sí es
evidencia suficiente para dos afirmaciones cualitativas defendibles: (a) las redes de
validación tienen falsos positivos reales, no solo teóricos, y (b) el banco de QA, tal como
está instrumentado, no detecta ese tipo de falla porque solo verifica qué herramientas se
llamaron, no la calidad ni la seguridad del texto final. Ambas afirmaciones estaban ya
anticipadas como limitaciones declaradas (Cap. 7 §7.4, Cap. 15 §15.4) antes de esta ejecución;
esta sesión las convierte de limitación argumentada a limitación **observada**.

## 16.4 Cobertura de las suites deterministas y de humo [FRESCA — reejecutado en esta sesión]

| Suite | Resultado verificado 2026-08-27 |
|---|---|
| 32 suites deterministas (`test_*.py`) | Confirmadas presentes y contadas programáticamente (F1.2); no se reejecutó cada una individualmente en esta sesión |
| `smoke_v8.py` (72 aserciones) | **72 OK, 0 FALLAS** — ejecutado en vivo (F1.2) |
| Banco de QA completo (74 casos, 22 bloques) | No reejecutado en su totalidad en esta sesión (coste estimado ≈ 20-30 min); se ejecutó el bloque "fatiga" (2 casos) como muestra, con el hallazgo de §16.3 |

## 16.5 Evaluación del clasificador de ejercicios [FRESCA — reejecutado en esta sesión]

Torneo de 5 algoritmos reejecutado íntegramente contra el dataset real (Cap. 12 §12.3):

| Algoritmo | Precisión | Desconocidos |
|---|---|---|
| **Gradient Boosting (ganador)** | **80,00 %** | 0/10 |
| Random Forest | 70,00 % | 3/10 |
| Regresión Logística | 70,00 % | 2/10 |
| SVC | 30,00 % | 3/10 |
| KNN | 30,00 % | 5/10 |

**Limitación explícita:** conjunto de prueba de 10 vídeos, dataset de 49 vídeos con
desbalance real entre clases (Cap. 12 §12.2). El 80 % de precisión no debe citarse sin esta
salvedad — un solo vídeo mal clasificado mueve el resultado 10 puntos porcentuales.

## 16.6 Evaluación del motor VBT [sin patrón de oro — limitación explícita, no medición]

Como se declara en el Cap. 1 §1.6 y el Cap. 3 §3.3, no existe en este proyecto una validación
de concordancia contra un encoder lineal de referencia. Lo único verificable sin ese
instrumento es evidencia indirecta ya documentada en el Cap. 11 §11.2: la tasa de detección
del disco por fotograma (99,6 % medido sobre grabaciones propias, cifra heredada del README,
no remedida en esta sesión) frente al 55,7 % que el motor aprovechaba antes de la corrección
de la versión 3.3. No se generó ninguna cifra nueva de este apartado en esta sesión —
reevaluar el motor VBT exigiría procesar vídeo real, fuera del alcance de una sesión de
documentación de texto.

## 16.7 Eficacia agregada de las redes de validación: por qué no hay tabla completa (F8.7)

El Cap. 7 §7.4 ya señalaba que ninguna red tiene, en el código actual, un contador persistente
de activaciones en producción. La ejecución en vivo de §16.3 aporta **un caso real, no una
serie estadística**: es evidencia cualitativa de que el fenómeno existe y es observable, no
una tasa de eficacia. Instrumentar un contador de activaciones por red —y ejecutar el banco de
QA completo repetidas veces, como exige la metodología del Cap. 4 §4.3— es un requisito previo
explícito para que una futura versión de este capítulo pueda reportar una tabla de eficacia
con significancia estadística. Se deja así, como dependencia declarada, en vez de rellenar la
tabla con una única observación disfrazada de medición robusta — sería exactamente el error
que este trabajo entero se propone no cometer.
