# Cap. 19 · Conclusiones y trabajo futuro

> Estado: **hecho (borrador)** · Tareas: F9.3, F9.4 · Fecha: 2026-08-27

---

## 19.1 Limitaciones del trabajo (F9.3)

Consolidado de todas las limitaciones identificadas a lo largo de este documento, con su
capítulo de origen:

| Limitación | Origen | Severidad |
|---|---|---|
| Sin ensayo controlado con múltiples atletas independientes | Cap. 1 §1.6 | Alta — limita la generalización de cualquier cifra de eficacia |
| Sin patrón de oro para el motor VBT (sin encoder de referencia) | Cap. 1 §1.6, Cap. 3 §3.3, Cap. 16 §16.6 | Alta — las métricas de velocidad no tienen validación de concordancia |
| Redes de validación sin telemetría de activación en producción | Cap. 7 §7.4, Cap. 16 §16.7 | Alta — impide cuantificar su eficacia real |
| **Falso positivo observado en vivo en la red 4, con reescritura que empeora la respuesta y filtra secreto profesional** | Cap. 7 §7.5, Cap. 16 §16.3 | **Alta — hallazgo nuevo de esta sesión, no corregido en código** |
| El banco de QA no detecta calidad textual, solo qué herramientas se dispararon | Cap. 15 §15.4, Cap. 16 §16.3 | Media-alta — deja pasar exactamente el tipo de fallo anterior |
| Techo de razonamiento del modelo, mitigado no resuelto | Cap. 1 §1.6, PDF v9.1 Cap. 14 | Media |
| Dataset del clasificador pequeño (49 vídeos) y desbalanceado | Cap. 12 §12.2 | Media |
| Retroalimentación de error no mitigada en el dataset de recolección continua | Cap. 13 §13.3 | Media |
| Sin reconciliación tras un fallo de envío a la API de Meta | Cap. 5 §5.4 | Media |
| Ausencia de claves foráneas explícitas en el esquema SQLite | Cap. 10 §10.2 | Baja-media |
| Lista blanca de cifras acumulativa: un número colado una vez queda legitimado | PDF v9.1 Cap. 16 | Media |
| Un solo idioma (español), un solo canal (WhatsApp) | Cap. 1 §1.6 | Media — limita la generalización directa a otros contextos |
| El patrón de verificación posterior exige una fuente de verdad determinista, no siempre disponible en otros dominios | Cap. 17 §17.2 | Estructural — límite del propio patrón propuesto, no un defecto de esta implementación |
| Bibliografía de estado del arte (Cap. 3) redactada sin verificación web en esta sesión | Cap. 3, nota metodológica | Media — pendiente de verificación antes de F12.5 |
| Un PDF de la biblioteca científica contiene PII de un tercero | F1.6 | **Alta — fuera del alcance de esta documentación, decisión pendiente del propietario del repositorio** |

## 19.2 Trabajo futuro, organizado por horizonte (F9.4)

### Corto plazo — ingeniería pendiente, sin investigación nueva requerida

- Instrumentar un contador persistente de activaciones por red de validación (resuelve F8.7 y
  permite una tabla de eficacia real en una futura revisión de este documento).
- Investigar y corregir la causa del falso positivo observado en la red 4 sobre respuestas
  honestas que mencionan la ausencia de datos (Cap. 7 §7.5) — es el hallazgo más urgente de
  esta sesión.
- Extender el banco de QA para verificar calidad textual además de qué herramientas se
  dispararon (por ejemplo, un chequeo adicional del filtro de secreto profesional sobre la
  respuesta final de cada caso, no solo sobre las reglas de negocio).
- Cerrar los mesociclos automáticamente en la bitácora narrativa (heredado del PDF v9.1).
- Admitir el registro de series agrupadas en un solo mensaje (heredado del PDF v9.1).
- Añadir el cálculo inverso de repetición máxima (heredado del PDF v9.1).
- Mitigar la retroalimentación de error del dataset de recolección continua (Cap. 13 §13.3):
  descartar o marcar para revisión humana los vídeos archivados con confianza de clasificación
  baja, no solo los marcados como "desconocido".

### Medio plazo — requiere datos o infraestructura adicional

- Ampliar el dataset del clasificador de ejercicios más allá de 49 vídeos, con reparto más
  equilibrado entre clases (Cap. 12 §12.2).
- Reejecutar y actualizar la tabla de latencia del Cap. 16 §16.2 con el estado actual del
  código, dado que las cifras heredadas son de julio de 2026.
- Verificar contra la documentación oficial vigente cada afirmación del Cap. 3 (estado del
  arte) sobre productos de terceros, con fecha de consulta.

### Largo plazo — investigación nueva

- Validar el motor VBT por concordancia contra un encoder lineal de referencia, para poder
  reportar el error de medición de velocidad frente al estándar de la literatura (Cap. 2 §2.3)
  en vez de solo evidencia indirecta de detección.
- Evaluar la generalización del patrón "verificación posterior contra fuente de verdad
  determinista" (Cap. 17 §17.2) a un segundo dominio con las mismas características —usuario
  incapaz de verificar, fuente de verdad computable disponible— para comprobar si el patrón
  es efectivamente transferible o si depende de particularidades del entrenamiento de fuerza.
- Estudiar la extensión del sistema a otros idiomas, evaluando específicamente si los patrones
  de detección lingüística (Cap. 7 §7.2, el caso del subjuntivo) requieren rediseño completo o
  son adaptables por traducción de las expresiones regulares.

## 19.3 Conclusión

Este trabajo demuestra que es posible construir un agente conversacional sobre un modelo de
lenguaje local que sostenga el rol de entrenador de fuerza sin que el modelo sea nunca la
fuente de una cifra o una afirmación de estado — no como principio de diseño incumplido en la
práctica, sino verificado en miles de líneas de código real. Demuestra igualmente, con la
misma honestidad que exige su propio objeto de estudio, que la capa de verificación posterior
que protege ese principio **no es infalible**: la propia sesión de documentación de este
trabajo capturó en vivo un falso positivo real que degradó una respuesta correcta en una
incorrecta. Esa doble conclusión —el patrón funciona en lo estructural y falla parcialmente en
lo puntual, de forma diagnosticable— es, en última instancia, el resultado más honesto que
este trabajo puede ofrecer, y es preferible a cualquiera de las dos afirmaciones más simples y
menos verdaderas: que el sistema nunca alucina, o que la ingeniería contra la alucinación no
sirve de nada.
