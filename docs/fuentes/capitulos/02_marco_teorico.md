# Cap. 2 · Marco teórico

> Estado: **hecho (borrador)** · Tareas: F3.1 a F3.10 · Fecha: 2026-08-27
> Ficheros fuente: `services/calculadora_rm.py`, `motor_predictivo.py`,
> `scripts/Entrenamiento.py`, `services/Creardataset.py`, `services/llm_service.py`

---

## 2.1 Entrenamiento de fuerza: fundamentos

### 2.1.1 Repetición máxima y su estimación indirecta

La repetición máxima (1RM) —la carga con la que un atleta puede completar exactamente una
repetición con técnica válida— es la unidad de referencia estándar para prescribir
intensidad relativa en entrenamiento de fuerza. Medirla directamente implica un riesgo de
lesión y un coste de fatiga que no se justifican en el uso rutinario, así que la práctica
habitual es **estimarla** a partir de series submáximas mediante ecuaciones de regresión
ajustadas empíricamente sobre la relación entre carga, repeticiones completadas y fatiga.

El sistema implementa seis de estas ecuaciones —Brzycki (1993), Epley (1985), Lombardi
(1989), Mayhew et al. (1992), O'Conner et al. (1989) y Wathan (1994)— con las siguientes
formulaciones (`services/calculadora_rm.py:17-42`):

| Fórmula | Expresión | Comportamiento |
|---|---|---|
| Brzycki | `peso × 36 / (37 − reps)` | Conservadora a bajas repeticiones; el denominador se acota para evitar división por valores negativos por encima de 36 reps |
| Epley | `peso × (1 + reps/30)` | Lineal, agresiva a altas repeticiones |
| Lombardi | `peso × reps^0.10` | Crecimiento muy suave, pensada para rangos altos |
| Mayhew | `100·peso / (52,2 + 41,9·e^(−0,055·reps))` | No lineal, ajustada sobre press de banca |
| O'Conner | `peso × (1 + reps/40)` | Lineal, más conservadora que Epley |
| Wathan | `100·peso / (48,8 + 53,8·e^(−0,075·reps))` | No lineal, curva similar a Mayhew con distinta pendiente |

Ninguna fórmula individual es precisa en todo el rango de repeticiones: la literatura que
las compara (LeSuer et al., 1997) muestra que el error de cada ecuación varía con el rango de
repeticiones evaluado y con el ejercicio. La decisión de diseño del sistema —**no elegir una
fórmula fija, sino ponderarlas dinámicamente según el rango de repeticiones de la serie
reportada** (`_hibrida_cruda`, líneas 68-131: Brzycki domina en 2-3 repeticiones, mezcla con
Epley/Wathan en 7-10, Lombardi gana peso en 11-15)— es una síntesis práctica de ese hallazgo
de la literatura, no una fórmula publicada como tal. Se documenta aquí como una aportación de
ingeniería original del proyecto, justificada por, pero no equivalente a, la evidencia de
LeSuer et al. (1997).

### 2.1.2 Esfuerzo percibido y repeticiones en reserva

La escala de esfuerzo percibido específica de fuerza (RPE, *Rating of Perceived Exertion*) y
su métrica complementaria de repeticiones en reserva (RIR, *Reps in Reserve*) permiten
autorregular la carga sin necesidad de un porcentaje fijo de 1RM: el atleta reporta cuánto
esfuerzo le costó la serie, y de ahí se infiere cuántas repeticiones más habría podido hacer
(Zourdos et al., 2016; Helms et al., 2016). Es una escala validada específicamente para
entrenamiento de fuerza —a diferencia de la escala de Borg, de origen cardiovascular
(Hackett et al., 2012)— y es el mecanismo que el sistema usa tanto para calcular el 1RM del
día (`motor_predictivo.py:259`, `e1rm_serie`) como para decidir si autorregular la sesión en
curso: un RPE reportado ≥ 9,5 dispara la cuarta red de interceptores deterministas
(Cap. 6). La correspondencia entre RIR y porcentaje de 1RM prescrito
(`pct_prescripcion`, línea 277) sigue las tablas de conversión estándar de la literatura de
RPE, no una tabla propia del proyecto.

### 2.1.3 Volumen de entrenamiento: MEV, MAV y MRV

El marco de volumen mínimo efectivo (MEV), volumen máximo adaptativo (MAV) y máximo volumen
recuperable (MRV) —popularizado por Israetel et al. (2015) y con evidencia de respaldo en
revisiones sistemáticas sobre la relación dosis-respuesta entre volumen semanal e hipertrofia
(Schoenfeld et al., 2017; Baz-Valle et al., 2022)— es el marco que gobierna tanto la
generación del plan (`scripts/Entrenamiento.py:306`, `VOLUMEN_SEMANAL_POR_PATRON`) como el
análisis de si el volumen ejecutado ha superado lo recuperable
(`motor_predictivo.py:619`, `analizar_mrv`). Es importante precisar su estatus epistémico
para la tesina: MEV/MAV/MRV es un **marco práctico de coaching**, derivado de la síntesis de
evidencia y experiencia de campo, no un conjunto de umbrales establecido por consenso
experimental cerrado — las revisiones sistemáticas citadas confirman la relación
dosis-respuesta general, pero no fijan valores universales de MEV/MAV/MRV por grupo
muscular. El sistema declara sus umbrales explícitamente en código (asimetría por patrón de
movimiento, no volumen global — véase Cap. 8), lo que los hace auditables aunque no estén
respaldados por un único estudio que los determine con esa granularidad.

### 2.1.4 Periodización ondulante diaria y fusión lineal-ondulante

La periodización ondulante diaria (DUP, *Daily Undulating Periodization*) varía el énfasis de
intensidad y volumen de sesión a sesión, en contraste con la periodización lineal clásica que
lo varía de bloque a bloque; existe evidencia de que iguala o supera a la periodización
lineal en ganancias de fuerza a igualdad de volumen e intensidad totales (Rhea et al., 2002).
El sistema implementa una variante propia —"Fusión Lineal-Ondulante"— que combina bloques
macro (acumulación, fuerza, realización) con una matriz de proporción de días DUP por bloque
(`scripts/Entrenamiento.py:258`, `MATRIZ_FUSION_DUP`) y una guardia explícita que prohíbe
programar dos días de fuerza pesada en fechas de calendario consecutivas —una protección
sobre la fatiga del sistema nervioso central que no está tomada literalmente de una fuente
única, sino que sintetiza el principio de gestión de fatiga presente en la literatura de
periodización en bloques (Issurin, 2010) con la lógica específica de alternancia de énfasis
de la DUP (Zourdos et al., 2016). Esta fusión, igual que la ponderación de fórmulas de 1RM en
§2.1.1, se documenta como síntesis de ingeniería del proyecto, no como técnica publicada bajo
ese nombre.

## 2.2 Carga de entrenamiento y riesgo: la ratio aguda/crónica

La ratio de carga aguda/crónica (ACWR) compara la carga de entrenamiento reciente (habitual-
mente una ventana de 7 días) contra la carga acumulada de fondo (28 días), con el objetivo de
detectar picos de carga asociados a mayor riesgo de lesión (Gabbett, 2016; Hulin et al.,
2016). El sistema la calcula con medias móviles exponenciales de semivida λ=2/(N+1), N=7 y
N=28 (`motor_predictivo.py:367`, `acwr_usuario`), siguiendo la formulación de Williams et al.
(2017) que corrige el sesgo de las medias móviles simples originales.

El ACWR es, sin embargo, una métrica activamente cuestionada en la literatura reciente:
Impellizzeri et al. (2020) documentan problemas conceptuales de fondo —entre ellos, que la
ratio no distingue si el riesgo proviene de carga alta o de carga baja reciente, y que su
poder predictivo se ha sobreestimado en varios de los estudios que la popularizaron—. El
propio código del sistema declara esta cautela explícitamente en un comentario
(`motor_predictivo.py:12`): *"Fatiga = ACWR con EWMA 7d/28d (Williams/Gabbett; heurística,
no oráculo)"*. Esta tesina adopta la misma postura que el código: el ACWR se usa como una
señal más entre varias —junto al esfuerzo a cargas emparejadas y los reportes de dolor
articular— para alimentar el análisis de volumen recuperable, no como un umbral de decisión
autónomo. Documentar la crítica de Impellizzeri junto a la métrica no debilita el trabajo:
demuestra que la cautela del sistema está justificada por la literatura, no es una omisión.

## 2.3 Entrenamiento basado en velocidad

El entrenamiento basado en velocidad (VBT, *Velocity-Based Training*) parte de que la
velocidad de ejecución de una repetición está inversamente relacionada con el porcentaje de
1RM que representa la carga movida, de forma razonablemente estable entre sesiones para un
mismo atleta y ejercicio (González-Badillo & Sánchez-Medina, 2010). Esto permite dos usos que
el motor VBT del sistema implementa: estimar el %1RM y el 1RM del día a partir de la
velocidad de la primera repetición de una serie (`services/Creardataset.py:441`,
`estimar_pct_1rm`), y usar la **pérdida de velocidad intra-serie** como indicador directo de
fatiga neuromuscular acumulada, sin depender de que el atleta reporte un RPE
(Sánchez-Medina & González-Badillo, 2011; `Creardataset.py:593`, `calcular_velocity_loss`).
La pérdida de velocidad como criterio de parada de serie tiene además evidencia de que
modula el balance entre ganancia de rendimiento y fatiga acumulada del mesociclo
(Pareja-Blanco et al., 2017), lo que fundamenta su uso como criterio de recorte de serie del
sistema, no solo como métrica descriptiva (Weakley et al., 2021, para una revisión aplicada
del método completo).

## 2.4 Modelos de lenguaje: transformer, cuantización, inferencia local y caché de prefijo

Los modelos de lenguaje de gran escala modernos se basan en la arquitectura *transformer*
(Vaswani et al., 2017), escalada a miles de millones de parámetros y entrenada sobre corpus
masivos con capacidad emergente de seguir instrucciones en pocos ejemplos (Brown et al.,
2020). Ejecutar un modelo de 27.000 millones de parámetros en una GPU de consumo de 24 GB de
VRAM exige **cuantización**: reducir la precisión numérica de los pesos del modelo (de 16 o 32
bits a 4 bits en este caso, formato Q4_K_M) a costa de una pérdida de precisión acotada
(Dettmers et al., 2022; Frantar et al., 2023). El sistema sirve el modelo mediante Ollama, que
gestiona además la **caché de prefijo**: el motor de inferencia reutiliza el cómputo de
atención de los tokens que dos peticiones consecutivas comparten al principio del prompt, en
vez de recalcularlo — una técnica de la misma familia que PagedAttention (Kwon et al., 2023)
y RadixAttention (Zheng et al., 2024), aunque implementada de forma más simple en llama.cpp
(el motor subyacente a Ollama) que en esos sistemas de servido especializado.

Esta caché no es un detalle de rendimiento incidental en este proyecto: es una restricción de
diseño de primer orden. Para que la caché acierte, el prompt del turno N debe ser **prefijo
textual exacto** del prompt del turno N+1; cualquier dato que se pode, reordene o normalice de
forma distinta entre turnos rompe esa condición y fuerza un reprocesado completo del prompt.
Esta restricción explica por qué el sistema conserva íntegra la telemetría de turnos
anteriores en el historial en vez de podarla (Cap. 5) y por qué persiste los mensajes de
herramientas que el orquestador genera internamente (Cap. 14) — ambas decisiones se
documentan con su coste medido en el capítulo de concurrencia y rendimiento.

## 2.5 Agentes conversacionales y llamada a herramientas

Un agente basado en LLM que puede invocar funciones externas —*tool calling* o *function
calling*— combina la comprensión de lenguaje del modelo con la capacidad de actuar sobre un
sistema real, típicamente mediante un ciclo de razonar y actuar (ReAct: Yao et al., 2023) o
mediante modelos entrenados explícitamente para decidir cuándo y cómo invocar una herramienta
(Toolformer: Schick et al., 2023; ToolLLM: Qin et al., 2024). El patrón habitual delega en el
modelo tanto la decisión de qué herramienta invocar como la síntesis del resultado.

Iron Progress invierte el orden habitual para las acciones de escritura: en vez de que el
modelo decida invocar una herramienta y el sistema ejecute lo que decide, código Python
determinista **detecta la intención del mensaje, ejecuta la acción y solo entonces** le pasa
al modelo el resultado ya producido para que lo redacte (Cap. 6, "interceptores
deterministas"). El modelo conserva la decisión de herramienta únicamente en los dominios que
código determinista no puede resolver sin comprensión de lenguaje natural — por ejemplo,
decidir si algo es una pregunta genuina de "¿debería descargar?" frente a una afirmación de
que ya se descargó. Esta inversión parcial del patrón agente-estándar es la que motiva la
pregunta de investigación PI1 (§1.5) y se justifica extensamente en el Cap. 6.

## 2.6 Alucinación en modelos de lenguaje: taxonomía, causas y mitigación

La alucinación —contenido generado que no está respaldado por la entrada, el conocimiento
verificable, o ambos— es un problema estructural de los modelos generativos, no un defecto de
implementación corregible con más datos de entrenamiento (Ji et al., 2023; Huang et al.,
2025). Las estrategias de mitigación documentadas en la literatura se agrupan, a grandes
rasgos, en tres familias: **anclaje** (proveer al modelo el contexto factual correcto y
restringirlo a usarlo, la base de RAG — §2.7), **verificación posterior** (auditar la
respuesta generada contra una fuente de verdad externa, con métodos que van desde consistencia
entre múltiples muestreos —SelfCheckGPT, Manakul et al., 2023— hasta medidas de incertidumbre
semántica del propio modelo —Farquhar et al., 2024—), y **restricción de la generación**
(limitar el espacio de salidas posibles del modelo, por ejemplo con gramáticas o esquemas
forzados).

Las seis redes de validación del sistema (Cap. 7) son un caso de la segunda familia:
verificación posterior contra una fuente de verdad externa **objetiva y determinista** —el
estado real del sistema en disco, no otra pasada del propio modelo generando una segunda
opinión (a diferencia de SelfCheckGPT)—. Esta elección de diseño tiene una justificación
práctica directa: en este dominio existe una fuente de verdad determinista disponible (el
plan, el historial, el registro de qué herramientas se ejecutaron), así que verificar contra
ella es estrictamente más fiable que verificar la consistencia entre muestras del mismo
modelo, que hereda los mismos sesgos que produjeron la alucinación en primer lugar. Esto se
retoma como argumento central en la discusión (Cap. 17, §9.2 del plan).

## 2.7 Generación aumentada por recuperación (RAG) y bases vectoriales

RAG combina un modelo generativo con un componente de recuperación que inyecta en el contexto
del modelo pasajes relevantes de un corpus externo, en vez de depender únicamente del
conocimiento paramétrico del modelo (Lewis et al., 2020; Gao et al., 2023 para una revisión
del estado de la técnica). El sistema mantiene tres colecciones vectoriales con propósitos
distintos —expediente del atleta, bitácora narrativa de mesociclos cerrados y biblioteca
científica— indexadas con `nomic-embed-text` sobre ChromaDB. La composición real y verificada
de la biblioteca científica (cuatro obras, no todas de teoría del entrenamiento, una de ellas
mal atribuida en la documentación original del proyecto) se documenta con evidencia en F1.6 y
se traslada al Cap. 10 (Modelo de datos).

## 2.8 Visión por computadora del movimiento humano

La estimación de pose humana a partir de vídeo permite extraer cinemática articular sin
sensores adicionales. El sistema usa MediaPipe Pose (Lugaresi et al., 2019), basado en la
arquitectura BlazePose (Bazarevsky et al., 2020), para los ángulos articulares que alimentan
tanto el clasificador de ejercicios (Cap. 12) como el análisis técnico opcional del motor VBT
(Cap. 11); y un detector YOLO (Redmon et al., 2016, en su implementación Ultralytics) para el
seguimiento del disco de la barra, la señal primaria de la que se derivan las métricas de
velocidad. Ambos son modelos preentrenados de propósito general, no entrenados específicamente
para este dominio — a diferencia del clasificador de ejercicios (Cap. 12), que sí es un
modelo entrenado sobre datos propios del proyecto.

## 2.9 Reconocimiento automático del habla

La transcripción de notas de voz usa Whisper (Radford et al., 2023), un modelo entrenado con
aprendizaje débilmente supervisado sobre grandes volúmenes de audio con transcripción
emparejada, en su variante `small` cuantizada a 8 bits, ejecutada sobre CPU — una decisión de
asignación de recursos forzada por la restricción de hardware (la GPU está ocupada por el
modelo de lenguaje principal, Cap. 14), no una elección de calidad.

## 2.10 Aprendizaje supervisado: clasificación y regresión bayesiana

El clasificador de ejercicios del sistema (Cap. 12) enfrenta cinco algoritmos de clasificación
supervisada clásica —entre ellos Gradient Boosting (Friedman, 2001), el que resulta ganador—
implementados con scikit-learn (Pedregosa et al., 2011). El motor predictivo, por su parte,
combina un piso determinista con un componente de **regresión bayesiana** que modela la
incertidumbre de la predicción de carga óptima cuando hay suficiente historial disponible
(`motor_predictivo.py:412`, `_modelo_bayesiano`) — un enfoque que sigue el principio general
de inferencia bayesiana en modelos lineales descrito por MacKay (1992) y desarrollado en
detalle en Bishop (2006). La combinación de un piso determinista con un componente
probabilístico que solo se activa con suficiente evidencia —y que se descarta si su
incertidumbre excede un umbral relativo— es, igual que en §2.1.1 y §2.1.4, una decisión de
ingeniería del proyecto que aplica principios establecidos de la literatura sin ser en sí
misma una técnica publicada bajo ese nombre.
