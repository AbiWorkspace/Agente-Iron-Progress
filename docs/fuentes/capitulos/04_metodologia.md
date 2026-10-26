# Cap. 4 · Metodología

> Estado: **hecho (borrador)** · Tareas: F5.1 a F5.5 · Fecha: 2026-08-27
> Ficheros fuente: `git log` (38 commits, 2026-07-15 a 2026-08-03), `requirements.txt`,
> `core/config.py`, `services/llm_service.py:1556,1597,1712`, `services/testbed/latencia/`

---

## 4.1 Enfoque de desarrollo

El desarrollo de Iron Progress no siguió un diseño experimental clásico de hipótesis fijada
de antemano y contraste estadístico. Siguió un ciclo iterativo guiado por fallos observados en
conversación real, reconstruible con precisión a partir del propio historial de control de
versiones (§4.4) y de los comentarios que el código conserva junto a cada corrección:

```
  1. INCIDENTE      → se observa un fallo real en conversación (p. ej. el caso del
                       29 de julio de 2026 descrito en el Cap. 1, §1.2)
  2. DIAGNÓSTICO     → se localiza la causa en el código, casi siempre documentada
                       en un comentario que explica el porqué, no solo el qué
                       (ver, p. ej., services/llm_service.py, decenas de comentarios
                       fechados y con la cifra medida que motivó el cambio)
  3. SUITE QUE LO REPRODUCE → se escribe un caso determinista o de interacción que
                       falla con el comportamiento anterior y pasa con la corrección
  4. CORRECCIÓN      → se implementa el mecanismo (interceptor, red de validación,
                       ajuste de prompt)
  5. SUITE AL BANCO   → el caso queda en services/testbed/ para que una regresión
                       futura lo vuelva a detectar automáticamente
```

Esta metodología es defendible académicamente como una variante de **desarrollo dirigido por
incidentes con regresión automatizada** — más cercana en espíritu al desarrollo dirigido por
pruebas (TDD) que a un proceso de investigación con hipótesis a priori, y coherente con la
literatura de ingeniería de software para sistemas de aprendizaje automático, que documenta
que la deuda técnica y el comportamiento de estos sistemas rara vez se anticipan completamente
por diseño y con frecuencia se descubren en producción (Sculley et al., 2015; Amershi et al.,
2019). La contrapartida honesta de este enfoque, que se declara explícitamente como
limitación (Cap. 1 §1.6 y Cap. 19), es que las seis redes de validación del Cap. 7 cubren los
incidentes que **ocurrieron y se observaron**, no el espacio completo de alucinaciones
posibles — no hay garantía formal de cobertura exhaustiva.

## 4.2 Entorno experimental

| Componente | Valor | Fuente |
|---|---|---|
| Hardware GPU | AMD Radeon RX 7900 XTX, 24 GB VRAM | README, stack tecnológico |
| Backend de cómputo GPU | PyTorch con ROCm 6.2 | README |
| Python | 3.12.3 | `python3 --version`, verificado en esta sesión |
| Framework API | FastAPI 0.139.2 sobre Uvicorn 0.51.0 | `requirements.txt` |
| Servidor de inferencia | Ollama, modelo `qwen3.8:27b` (variante servida: `agent:latest`) | `services/llm_service.py:1511`; verificado en vivo (F1.1) |
| Cuantización del modelo | Q4_K_M | README |
| Ventana de contexto en inferencia | 40.960 tokens (`options.num_ctx` por petición) | `llm_service.py:1597`; ver F1.1 sobre la discrepancia con el `Modelfile` |
| Parámetros de muestreo | Temperatura 0,3 (redacción) / 0,6 (razonamiento); `top_k=20`, `top_p=0,95` | `llm_service.py:1556` y ss.; confirmado con `ollama show agent:latest` |
| Modelo de embeddings | `nomic-embed-text` | `crear_base_conocimiento.py`; confirmado en vivo, modelo presente en el servidor |
| Persistencia relacional | SQLite (2 bases, 7 tablas) | verificado en F1.2 |
| Persistencia vectorial | ChromaDB (3 colecciones) | verificado en F1.2 |
| Transcripción de audio | faster-whisper 1.2.1, modelo `small`, int8, CPU | `requirements.txt` |
| Visión | OpenCV 5.0.0.93, MediaPipe 0.10.14, Ultralytics YOLO 8.4.104 | `requirements.txt` |
| Planificación temporal | APScheduler 3.11.3 | `requirements.txt` |

## 4.3 Método de recolección de evidencia

Tres tipos de evidencia sostienen las cifras que este trabajo reporta, cada una con su propio
método de captura:

1. **Latencia de inferencia**, instrumentada directamente en el código
   (`services/llm_service.py:1712`): cada llamada al modelo registra tokens y segundos
   consumidos por separado en la fase de *prefill* (procesar el prompt) y de *decode*
   (generar la respuesta), con su throughput en tokens por segundo. Esta instrumentación es
   la que permitió diagnosticar, por ejemplo, que el preprocesado del prompt pasó de 17,6 s a
   1,2-3,5 s al dejar de podar la telemetría del historial (Cap. 14).
2. **Bancos de latencia dedicados** (`services/testbed/latencia/`, 14 scripts): miden
   escenarios específicos de forma aislada y repetible —acierto de caché de prefijo, efecto
   del techo de razonamiento, comparación antes/después de un cambio— fuera del flujo
   conversacional completo, para que un cambio de una variable no se confunda con ruido de
   otras.
3. **Calidad de comportamiento**, mediante el banco de casos de uso (`qa_casos_uso.py`) y las
   32 suites deterministas. El propio proyecto documenta y advierte sobre el ruido inherente a
   evaluar un sistema no determinista: **la recomendación operativa del equipo, que esta
   tesina adopta como criterio metodológico, es repetir la ejecución del banco antes de
   atribuir una variación de resultado a un cambio de código**, en vez de interpretar una sola
   corrida como una medición definitiva.

## 4.4 Cronograma del desarrollo

Reconstruido de `git log` (38 commits, del `HEAD` de esta sesión hasta el primer commit del
repositorio). El desarrollo activo documentado en el historial abarca **del 15 de julio al 3
de agosto de 2026** (19 días), con las siguientes fases identificables por la naturaleza de
los mensajes de commit:

| Periodo | Hitos (mensajes de commit representativos) |
|---|---|
| 15-18 jul | Arranque del repositorio; primeras correcciones del orquestador |
| 21-23 jul | Recuperación de un backup tras trabajo en otra máquina; primera implementación del motor VBT y del motor predictivo |
| 26-28 jul | Primeros hitos marcados explícitamente "Produccion 1/2/3"; resolución de un merge conflictivo que dañó archivos |
| 29 jul | *"Solucionando errores con la charla del modelo"*, cierre de una primera ronda de QA de interacción |
| 30 jul | Jornada de mayor densidad de correcciones: parseo de series, persistencia de historial, deduplicación de nombres de ejercicio, blindaje de lectura/escritura de estado, cálculo de e1RM |
| 31 jul | *"arreglando los primeros 7 errores criticos y altos"* y *"...los ultimos errores antes de la demostracion"* — la fecha de corte del PDF v9.1 (31-jul-2026) coincide con el cierre de esta ronda; iteración sobre el HUD del motor VBT |
| 1-3 ago | Incorporación de scripts de siembra/demo y de las suites de prueba faltantes; últimos commits "PRODUCION" |

Esta sesión de documentación (F0-F3 de este plan) se realiza el **27 de agosto de 2026**, 24
días después del último commit registrado en el historial — el propio README y el estado de
`git status` al inicio de esta sesión muestran cambios de trabajo en curso no reflejados aún
en un commit, coherente con desarrollo activo continuado más allá del historial capturado
aquí.

## 4.5 Criterios de éxito

Definidos para que el capítulo de Resultados (Cap. 16) tenga un umbral contra el que
contrastar, no solo una lista de cifras:

| Dimensión | Criterio de éxito | Justificación |
|---|---|---|
| Fidelidad numérica | 0 cifras en la salida del modelo fuera de la whitelist auditada, medido sobre el banco de QA | Es la garantía central que el trabajo defiende (Cap. 1 §1.2) |
| Cobertura del banco de QA | ≥ 90 % de los 74 casos con veredicto automático correcto, **repetido en al menos 2 corridas** para descontar el ruido documentado del banco | El propio proyecto mide un ruido de ±1 acierto entre corridas — un único resultado no es concluyente |
| Latencia de turno de confirmación | ≤ 10 s en el percentil 50, con el modelo ya cargado | Umbral de usabilidad conversacional razonable para un canal de mensajería; el sistema mide 4-8 s tras las optimizaciones de caché (Cap. 14) |
| Falsos positivos de las redes de validación | Reportado explícitamente por red, no agregado — una red con alta tasa de falsos positivos (p. ej. la de claim falso antes de la corrección del subjuntivo, Cap. 7) es un defecto aunque su tasa de detección sea alta | Evita que una cifra agregada de "eficacia" oculte un mecanismo que reescribe respuestas correctas |
| Disponibilidad del RAG | Las tres colecciones vectoriales devuelven resultados no vacíos ante una consulta de prueba | Verificado como requisito mínimo en F1.5/F1.6 — una colección vacía (el estado real encontrado al empezar F1.5) es una falla silenciosa que el propio sistema no reporta como error |
