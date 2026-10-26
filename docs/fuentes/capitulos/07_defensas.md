# Cap. 7 · Defensas contra la alucinación

> Estado: **hecho (borrador)** · Tareas: F1.3 (heredada), F6.6 · Fecha: 2026-08-27
> Ver también: `docs/fuentes/capitulos/f1_3_seis_redes_validacion.md` (evidencia de código
> línea por línea, base de este capítulo) y `docs/fuentes/capitulos/02_marco_teorico.md` §2.6

---

## 7.1 Encaje de las seis redes en la taxonomía de mitigación de alucinaciones

El marco teórico (§2.6) distingue tres familias de mitigación de alucinaciones: anclaje,
verificación posterior y restricción de la generación. Las seis redes del sistema son, sin
excepción, **verificación posterior contra una fuente de verdad externa y determinista** —no
contra otra pasada del propio modelo (a diferencia de SelfCheckGPT, Manakul et al., 2023)—.
La tabla siguiente retoma el inventario de F1.3 y añade, para cada red, el incidente concreto
que la originó y su coste conocido:

| Red | Incidente de origen documentado | Coste conocido |
|---|---|---|
| 1. Kilajes inventados | Motivación general del proyecto (Cap. 1 §1.2); es la primera red implementada, anterior a un incidente puntual registrado | Falso positivo posible con cifras triviales de disco (2,5, 5, 10, 20 kg) — mitigado con una lista de exención explícita (F1.3) |
| 2. Rutina fantasma | El coach, ante un atleta sin plan, "improvisa algo provisional mientras tanto" (PDF v9.1, Cap. 8) | Umbral de 2+ líneas con forma de prescripción — un mensaje que cita accidentalmente dos ejercicios con números (p. ej. citando una sesión pasada) podría dispararla; no se encontró en el código un caso de prueba específico para ese falso positivo |
| 3. Reparador de onboarding | El modelo pregunta "nivel/experiencia" (campo que el sistema no recolecta) o repregunta un dato ya guardado | Bajo riesgo de falso positivo por diseño explícito (`_reparar_onboarding`, docstring: "bajo riesgo de falsos positivos") — solo interviene ante desvíos claros |
| 4. Claim falso | Ver F1.3: el propio código documenta que permitir el subjuntivo sin tilde disparaba la red ante ofertas ("y lo guardo") en vez de acciones consumadas | **Corregido**: la versión actual excluye explícitamente construcciones con "y" antes del verbo de rectificación |
| 5. Estado del plan | El incidente del 29 de julio de 2026 (Cap. 1 §1.2): la descarga fantasma que persistió cinco turnos | Deliberadamente **no** cubre formas ambiguas de tiempo verbal ("cuando vuelvas reanudamos el plan") — el propio diseño documenta que reescribir un mensaje honesto por error es peor que dejar escapar una construcción infrecuente |
| 6. Anti-eco | No documentado como incidente específico, mecanismo de calidad conversacional general | **Hallazgo de esta sesión (F1.3): solo detecta repetición textual exacta**, no la paráfrasis que el README afirma detectar — límite de cobertura no corregido en el código, solo documentado aquí |

## 7.2 El hallazgo lingüístico del subjuntivo como aportación original

El caso de la red 4 (Claim falso) merece tratamiento aparte porque ilustra un problema de
detección **específico del español** que no tiene equivalente directo en la literatura de
alucinación en inglés consultada para este trabajo (Ji et al., 2023; Huang et al., 2025; Ji
et al. escriben sobre generación en general, no sobre morfología verbal española). El
regex `_CLAIM_ACCION_RE` (`llm_service.py:558-570`, ver F1.3) ancla los verbos de
rectificación a las terminaciones `[éó]|amos|aste|aron|ad[oa]s?|id[oa]s?`: pretérito y
participio, con **tilde obligatoria** en la primera persona del singular. La razón es que
español distingue "aplique" (subjuntivo, sin tilde: *"que yo aplique una descarga"*, una
oferta) de "apliqué" (pretérito, con tilde: *"ya apliqué la descarga"*, una afirmación de
hecho consumado) **únicamente por la tilde** — una señal ortográfica mínima que, si se ignora,
convierte cada propuesta condicional del coach en un falso positivo de afirmación.

Esto es relevante para la tesina en dos sentidos: primero, como evidencia concreta de que las
técnicas de detección de alucinación desarrolladas y evaluadas mayoritariamente en inglés
(la literatura de §2.6 es predominantemente angloparlante) no son transferibles sin
adaptación a morfologías verbales distintas — un punto que se retoma en la Discusión (Cap.
17) como limitación de la generalización del patrón propuesto a otros idiomas. Segundo, como
ejemplo de que una regla de detección basada en expresiones regulares, correctamente
adaptada, puede ser más fiable en este dominio específico que un segundo paso de inferencia
del propio modelo — que heredaría el mismo desconocimiento morfológico si no se le instruye
explícitamente sobre esta distinción.

## 7.3 El mecanismo de reescritura y terminación garantizada

Las cinco primeras redes comparten el mismo mecanismo de reacción: si una red salta, se
fuerza **una** reescritura inyectando la verdad del turno en el contexto del modelo; si el
modelo reincide en la segunda pasada, la respuesta final es una plantilla honesta escrita en
Python, no una tercera llamada al modelo. Esta garantía de terminación en como máximo dos
pasadas (generación original + una reescritura) es una decisión de diseño explícita que evita
el riesgo de bucles de reescritura indefinidos — un problema conocido en sistemas de
verificación posterior que reintentan hasta que la salida "pasa" la validación sin límite de
intentos. El coste de esta garantía es que la plantilla de Python, cuando se activa, es
genérica frente a la variedad de contextos conversacionales posibles — una limitación
aceptada a cambio de previsibilidad total del peor caso.

## 7.4 Lo que queda pendiente de medir (traspaso a F8.7)

Ninguna de las seis redes tiene, en el estado actual del sistema, un contador persistente de
cuántas veces se activó ni de su tasa de falsos positivos en producción — la evidencia de esta
sección es de código (qué detecta cada red, por qué) y de un incidente documentado por red
como mucho, no de telemetría agregada. Instrumentar esa telemetría es un requisito previo para
que el Cap. 16 (Resultados, F8.7) pueda reportar una tabla de eficacia con cifras reales en
vez de solo con evidencia de diseño. Se señala aquí como una dependencia explícita entre F6.6
(este capítulo) y F8.7, no como una tarea resuelta.

---

## 7.5 Adenda — Incidente real capturado durante esta sesión de documentación

Al ejecutar en vivo el bloque de QA "fatiga" contra el orquestador real, con Ollama activo, el
27 de agosto de 2026 a las 21:36 (registro completo en
`services/testbed/qa/qa_fatiga_2137.json`), se observó lo siguiente ante el mensaje *"llevo
toda la semana molido, ¿me toca descarga?"*:

1. **La respuesta original del modelo era honesta y correcta**: *"Entiendo la sensación de
   estar 'molido', pero ahora mismo no tengo suficientes datos registrados para medir tu
   fatiga acumulada con rigor..."* — no afirma ninguna acción que no ocurriera.
2. **La red 4 (Claim falso) se disparó de todos modos**, con el log
   `[CLAIM-FALSO] afirma una acción NO ejecutada; reescritura honesta`, y forzó una
   reescritura vía `_forzar_respuesta_texto`.
3. **La reescritura forzada produjo un texto peor que el original**, con gramática rota y una
   fuga clara de instrucciones internas al canal del atleta: *"mis métodos de coach no falló:
   devolvió que aún NO hay suficientes sesiones registradas... Díselo así, como coach [...]
   PROHIBIDO hablar de fallos técnicos [...] Dime si quieres que lo intentemos de otra forma y
   le entro."* Ese texto —claramente instrucciones para el modelo, no una respuesta al
   atleta— es exactamente lo que habría llegado a un atleta real, y viola directamente la
   sección `secreto_profesional` del prompt (Cap. 6 §6.3): un atleta que lea "mis métodos de
   coach" infiere la existencia de mecanismos internos que el sistema tiene la instrucción
   explícita de ocultar.
4. **El caso, pese a esto, se marcó como "veredicto en verde"** en el banco de QA: las
   aserciones automáticas (`tools_esperadas`) solo comprueban qué herramientas se dispararon
   (`analizar_mrv`, correcto), no la calidad textual de la respuesta final. La incidencia
   quedó registrada como advertencia, no como fallo — exactamente el modo de "banco de pruebas
   que no ve lo que no se le pidió mirar" descrito en el Cap. 15 §15.4.

**Interpretación para la tesina.** Este incidente es evidencia directa y fresca de dos cosas a
la vez: (a) que la red 4 tiene una tasa de falsos positivos no nula sobre casos reales —el
propio Cap. 7 §7.4 señalaba que esto no estaba instrumentado, y aquí se capturó un ejemplo
concreto sin necesidad de instrumentación adicional—, y (b) que el mecanismo de rescate del
Cap. 6 §6.1 (`_forzar_respuesta_texto`, sin herramientas ni razonamiento) **no garantiza que
la reescritura sea mejor que el original**, solo garantiza que el turno termina. Ambos puntos
se trasladan al Cap. 16 (Resultados, F8.3) y al Cap. 17 (Discusión) como limitación medida, no
solo argumentada.
