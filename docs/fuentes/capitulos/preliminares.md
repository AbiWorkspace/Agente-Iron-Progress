# Preliminares

> Estado: **hecho (borrador)** · Tareas: F2.1, F2.2 · Fecha: 2026-08-27
> Ficheros fuente: `services/llm_service.py`, `README.md`,
> `docs/fuentes/capitulos/f1_2_cifras_del_sistema.md`, `f1_3_seis_redes_validacion.md`

---

## Resumen

Las aplicaciones de entrenamiento de fuerza registran datos; un entrenador humano, además,
recuerda, interpreta y decide. Este trabajo presenta Iron Progress, un agente conversacional
que ejerce de entrenador de fuerza a través de WhatsApp, con un modelo de lenguaje de gran
escala (Qwen3.8, 27B de parámetros) ejecutado íntegramente en local. El problema central que
aborda es estructural: en un dominio donde el atleta no puede verificar lo que el sistema le
dice, una alucinación numérica del modelo —una carga inventada, una descarga que nunca se
aplicó— no degrada la experiencia, degrada el entrenamiento. La solución propuesta separa de
forma explícita el cálculo del lenguaje: código Python determinista interpreta el mensaje,
ejecuta las escrituras y calcula toda cifra que llega al atleta; el modelo de lenguaje nunca
calcula ni decide sobre datos numéricos, solo elige la herramienta pertinente y comunica el
resultado ya producido. Sobre esa base se implementan seis redes de validación de salida que
auditan la respuesta generada contra el estado real del sistema —el plan en disco, el
historial de series, las herramientas efectivamente ejecutadas— antes de enviarla al atleta.
El sistema integra además un motor de planificación con periodización ondulante diaria, un
motor predictivo que estima la repetición máxima y el volumen recuperable, un motor de visión
por computadora que extrae métricas de entrenamiento basado en velocidad a partir de vídeo sin
hardware adicional, un clasificador de ejercicios entrenado sobre un dataset propio y tres
memorias vectoriales de propósito distinto. Se documentan de forma auditable, con referencia
directa al código, los incidentes reales que motivaron cada mecanismo de defensa, y se mide
—no se estima— el coste y la eficacia de cada uno. El resultado es un patrón de diseño
generalizable: en cualquier dominio donde el usuario no pueda verificar la respuesta de un
modelo de lenguaje, la fuente de verdad debe vivir fuera del modelo.

**Palabras clave:** agente conversacional, modelo de lenguaje local, mitigación de
alucinaciones, entrenamiento de fuerza, periodización, entrenamiento basado en velocidad.

---

## Abstract

Fitness-tracking applications record data; a human coach also remembers, interprets, and
decides. This work presents Iron Progress, a conversational agent that acts as a strength
coach over WhatsApp, powered by a large language model (Qwen3.8, 27B parameters) running
entirely on local hardware. The central problem it addresses is structural: in a domain
where the athlete cannot verify what the system tells them, a numerical hallucination from
the model — an invented load, a deload that was never applied — does not merely degrade the
experience, it degrades the training itself. The proposed solution explicitly separates
computation from language: deterministic Python code parses the message, executes every
write, and computes every number the athlete receives; the language model never calculates
or decides on numerical data, it only selects the relevant tool and communicates an
already-produced result. On top of that separation, six output-validation networks audit the
generated response against the system's real state — the plan on disk, the training log, the
tools actually executed — before it reaches the athlete. The system further integrates a
periodized programming engine using daily undulating periodization, a predictive engine that
estimates one-repetition maximum and recoverable volume, a computer-vision engine that
extracts velocity-based-training metrics from unassisted smartphone video, an exercise
classifier trained on a purpose-built dataset, and three vector memories with distinct
retrieval purposes. The real incidents that motivated each safeguard are documented in an
auditable way, with direct reference to the source code, and the cost and effectiveness of
each mechanism is measured, not estimated. The result is a generalizable design pattern: in
any domain where the user cannot verify a language model's answer, the source of truth must
live outside the model.

**Keywords:** conversational agent, local language model, hallucination mitigation, strength
training, periodization, velocity-based training.

---

## Nota de verificación de esta sección

El resumen y el abstract se redactaron después de cerrar F1 (auditoría de cifras) para no
repetir el error del PDF v9.1: describir el sistema con datos que ya habían cambiado en el
código en el momento de citarlos. No incluyen cifras puntuales (número de líneas, de
herramientas, de fragmentos indexados) precisamente porque esas cifras están sujetas a
cambio de commit a commit — viven en el capítulo de Resultados (F8) y en
`scripts/metricas_documentacion.py` (F1.2), no en el resumen.
