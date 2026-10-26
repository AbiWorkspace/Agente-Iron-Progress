# Cap. 1 · Introducción

> Estado: **hecho (borrador)** · Tareas: F2.3, F2.4, F2.5, F2.6, F2.7 · Fecha: 2026-08-27
> Ficheros fuente: `docs/fuentes/generar_docx.py:523-528` (caso real), `README.md`,
> capítulo 16 del PDF v9.1 (deuda técnica), `docs/fuentes/capitulos/f1_3_seis_redes_validacion.md`

---

## 1.1 Antecedentes

Las aplicaciones de seguimiento de entrenamiento de fuerza —Strong, Hevy, y decenas de
equivalentes— resuelven un problema de registro: el atleta introduce peso, repeticiones y
series, y la aplicación las guarda, las grafica y, en el mejor de los casos, calcula una
repetición máxima estimada con una fórmula fija. Ese es el techo de la categoría: registrar
lo que el usuario ya decidió hacer.

Un entrenador humano hace algo cualitativamente distinto. No solo anota: **recuerda** que a
un atleta le molestó el hombro hace tres semanas y evita cargarlo sin preguntar; **detecta**
un estancamiento en un movimiento concreto aunque el resto del cuerpo progrese con
normalidad; **decide**, con datos y sin que se lo pidan, bajar la carga el día que el atleta
llega visiblemente fatigado. Esa capacidad de interpretar, recordar y decidir es precisamente
lo que separa acompañar un entrenamiento de solo registrarlo, y es el hueco que las
aplicaciones convencionales no cubren porque no está en su alcance de diseño: no interpretan
lenguaje natural, no mantienen estado interpretativo sobre el atleta más allá de una tabla de
series, y no toman decisiones prescriptivas de forma proactiva.

Los modelos de lenguaje de gran escala hacen técnicamente posible cerrar ese hueco por
primera vez: interpretan lenguaje natural con soltura suficiente para sostener una
conversación de entrenamiento, y pueden orquestar herramientas externas para actuar sobre un
sistema real (Yao et al., 2023; Schick et al., 2023). Iron Progress es un intento de
construir ese "hueco cerrado" como sistema en producción: un agente conversacional que
gestiona la relación completa del entrenamiento de fuerza —planificación, registro,
adaptación y análisis de fatiga— a través del canal de mensajería que el atleta ya usa a
diario, WhatsApp, con inferencia íntegramente local.

## 1.2 Planteamiento del problema

Hacer eso mismo con un modelo de lenguaje introduce un riesgo que las aplicaciones
convencionales no tienen: un modelo generativo produce texto **plausible**, no texto
**verdadero** (Ji et al., 2023; Huang et al., 2025). En la mayoría de las aplicaciones de un
LLM, una alucinación degrada la experiencia del usuario — una respuesta rara, una cita
inventada que se puede verificar en otra pestaña. En este dominio, degrada directamente el
entrenamiento físico de una persona, y el atleta no tiene forma de auditar la cifra que
recibe: no sabe si "sube a 95 kg" viene de su marca real o de una alucinación del modelo.

El riesgo no es hipotético. El 29 de julio de 2026, ante el mensaje del atleta *"llevo toda
la semana molido, ¿me toca descarga?"*, el sistema —en una versión anterior, sin la red de
validación que este incidente motivó— consultó el análisis de fatiga, **no ejecutó** ninguna
aplicación de descarga, y sin embargo respondió *"Ajuste aplicado: reducción del 10 % en la
carga, una serie menos"*. No existía ninguna descarga real. Los cinco turnos siguientes de la
conversación siguieron refiriéndose a "la descarga que aplicamos", reforzando la alucinación
en cada turno. De no haberse detectado, el atleta habría entrenado un mesociclo entero con
menos carga de la que le correspondía, sin que nada en la conversación lo delatara.

Esto plantea la pregunta que estructura el trabajo:

> **¿Puede un modelo de lenguaje ejecutado en local sostener el rol de entrenador de fuerza
> —interpretando lenguaje natural, decidiendo cargas y comunicando resultados— sin que sus
> alucinaciones se traduzcan en decisiones de entrenamiento incorrectas para el atleta, y sin
> enviar los datos de salud del usuario fuera de su propia máquina?**

De esta pregunta general se derivan tres subpreguntas que el resto del documento responde
capítulo a capítulo:

1. ¿Qué arquitectura permite que un LLM orqueste un sistema de decisiones sin ser la fuente
   de las cifras que comunica? (Cap. 5-9, respuesta arquitectónica.)
2. ¿Qué mecanismos detectan una alucinación *después* de generada, cuando la prevención
   arquitectónica no basta? (Cap. 7, respuesta de validación de salida.)
3. ¿A qué coste de latencia y complejidad se puede sostener esa doble garantía —arquitectónica
   y de validación— en hardware de consumo, con más de un atleta a la vez? (Cap. 14, 16.)

## 1.3 Justificación

La justificación de este trabajo se apoya en tres ejes:

**Eje clínico-deportivo.** Una cifra de carga mal calculada no es un error cosmético: se
traduce en semanas de entrenamiento con un estímulo distinto del pretendido, sea por exceso
—con el consiguiente riesgo de lesión o sobreentrenamiento (Gabbett, 2016; Hulin et al.,
2016)— o por defecto, con pérdida de progreso. En un sistema que además detecta y registra
molestias articulares, una alucinación sobre el estado de una lesión tiene consecuencias
potencialmente más serias que una alucinación sobre una cifra de peso.

**Eje de privacidad.** El sistema procesa datos de salud identificables —peso, composición
corporal, lesiones, rendimiento físico, asociados a un número de teléfono— y los procesa
íntegramente en local: ningún dato del atleta sale de la máquina que ejecuta el modelo. Esta
decisión de arquitectura, poco común en productos de IA conversacional comerciales, tiene
implicaciones directas de cumplimiento normativo que se desarrollan en el capítulo 18
(Consideraciones éticas y legales).

**Eje de ingeniería de software.** El patrón central del proyecto —código determinista como
única fuente de cálculo, el modelo de lenguaje restringido a comunicar resultados ya
producidos, y redes de validación que auditan la salida contra el estado real del sistema— no
es específico del entrenamiento de fuerza. Es un patrón aplicable a cualquier agente
conversacional que opere sobre un dominio donde el usuario no puede verificar por sí mismo lo
que se le dice: asesoría financiera, salud, sistemas legales. Documentar ese patrón con
evidencia medida —no solo argumentada— es la aportación de ingeniería que este trabajo
defiende (véase la formulación completa en el Cap. 17, Discusión, §9.2 del plan de
documentación).

## 1.4 Objetivos

### Objetivo general

Diseñar, implementar y evaluar un agente conversacional de entrenamiento de fuerza sobre un
modelo de lenguaje ejecutado en local, cuya arquitectura garantice que ninguna cifra o
afirmación de estado comunicada al atleta se origine en el modelo de lenguaje sin haber sido
calculada o verificada por código determinista, y medir el coste de esa garantía en latencia,
complejidad y cobertura de casos.

### Objetivos específicos

| # | Objetivo específico | Capítulo que lo desarrolla | Métrica de éxito (Cap. 16) |
|---|---|---|---|
| 1 | Diseñar una arquitectura que separe el cálculo determinista de la generación de lenguaje en cada turno de conversación | Cap. 5-6 | 0 cifras generadas fuera de la whitelist auditada en el banco de QA |
| 2 | Implementar interceptores deterministas que resuelvan del lenguaje natural las acciones de registro, sin depender de que el modelo decida ejecutarlas | Cap. 6 | Cobertura de las 46+46 suites de parseo de series y RPE (F1.2) |
| 3 | Diseñar y medir la eficacia de redes de validación de salida que detecten alucinaciones específicas del dominio tras la generación | Cap. 7 | Tasa de detección y de falsos positivos por red (F8.7) |
| 4 | Implementar un motor de planificación y un motor predictivo que fundamenten sus decisiones en marcos de la ciencia del entrenamiento (MEV/MAV/MRV, ACWR, periodización ondulante) | Cap. 8-9 | Trazabilidad de cada regla de negocio a su fuente bibliográfica (F3.3-F3.5) |
| 5 | Extender la interpretación multimodal del sistema (voz, vídeo) para que el registro de entrenamiento no dependa de texto tecleado | Cap. 11-13 | Cobertura de casos de audio (F7.3) y de vídeo (F8.6) |
| 6 | Medir el coste de la garantía arquitectónica de veracidad en latencia y en concurrencia de múltiples atletas | Cap. 14, 16 | Tabla de latencia antes/después medida (F8.2) |
| 7 | Situar el patrón de diseño resultante frente al estado del arte de agentes conversacionales en salud y deporte, y frente a los sistemas VBT comerciales | Cap. 3, 17 | Tabla comparativa (F4.4) |

## 1.5 Preguntas de investigación

Formuladas explícitamente a partir del planteamiento del problema (§1.2):

- **PI1.** ¿Qué proporción de las decisiones que un entrenador de fuerza toma en una
  conversación real puede resolverse con código determinista antes de que el modelo de
  lenguaje intervenga, y cuáles requieren necesariamente comprensión de lenguaje natural?
- **PI2.** ¿Qué patrones de alucinación son detectables mediante verificación posterior
  contra el estado del sistema, y cuáles requerirían en cambio restringir la generación desde
  el propio muestreo del modelo?
- **PI3.** ¿Es sostenible esta arquitectura de doble garantía en hardware de consumo (una
  GPU de 24 GB) cuando el número de atletas activos simultáneamente crece más allá de uno?

## 1.6 Alcance y limitaciones

Se declara explícitamente lo que este sistema **no** es, para que ninguna afirmación de los
capítulos siguientes se lea con un alcance mayor del que tiene:

- **No diagnostica ni trata condiciones médicas.** El sistema detecta y registra molestias
  reportadas por el atleta y ajusta el entrenamiento en consecuencia; no sustituye la
  evaluación de un profesional de la salud. Esta frontera está codificada explícitamente en
  la sección `salud_del_atleta` del prompt del sistema (verificar cita textual en Cap. 18).
- **Un solo idioma, un solo canal.** El sistema opera en español y sobre WhatsApp Cloud API
  exclusivamente; no se ha evaluado su comportamiento en otros idiomas ni sobre otros canales
  de mensajería.
- **Validado con un número reducido de atletas reales**, no con un ensayo controlado. Las
  cifras del capítulo de Resultados (Cap. 16) proceden de un banco de casos de uso simulados
  (74 casos) y de uso real limitado, no de un estudio con múltiples participantes
  independientes — se declara así explícitamente para no sobreinterpretar su significancia
  estadística.
- **Sin patrón de oro para el motor de visión.** Las métricas de velocidad y trayectoria de
  barra del motor VBT no se han validado por concordancia contra un encoder lineal de
  referencia, la tecnología estándar en la literatura de VBT (González-Badillo &
  Sánchez-Medina, 2010). Se reporta lo que sí se puede medir sin ese instrumento (Cap. 16,
  F8.6) y se declara la ausencia como limitación, no se disfraza.
- **El techo de razonamiento del modelo es una limitación conocida y solo parcialmente
  mitigada**, no resuelta: por encima de cierto presupuesto de tokens de pensamiento el
  modelo degrada su capacidad de construir invocaciones de herramientas válidas (Cap. 14).
- **La garantía de veracidad es empírica, no formalmente verificada.** Las seis redes de
  validación (Cap. 7) se diseñaron a partir de incidentes observados y se prueban contra un
  banco de casos; no existe una prueba matemática de que cubran el espacio completo de
  alucinaciones posibles, y el propio proyecto documenta casos límite no cubiertos (la lista
  blanca de cifras acumulativa admite un número una vez colado — véase Cap. 19).
