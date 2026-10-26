# F0.5 — Destino del PDF v9.1

> Estado: **hecho (decisión)** · Fecha: 2026-08-27

## Decisión

**Se conserva como memoria técnica independiente.** La tesina es un documento distinto y
más amplio que la absorbe y la referencia, no la reemplaza. El PDF v9.1 sigue siendo el
entregable rápido para enseñar el proyecto a alguien que no necesita el aparato académico
completo (recruiters, colegas, una demo).

## Por qué no fusionar ambos documentos en uno solo

- Tienen audiencias distintas: el PDF es una memoria de ingeniería para quien ya sabe qué es
  un LLM y quiere el "cómo"; la tesina necesita explicar los fundamentos desde cero para un
  tribunal que evalúa rigor académico, no solo resultado técnico.
  `docs/PLAN_DEMOSTRACION.md` ya asume y depende de la existencia de un documento corto
  autocontenido — fusionarlo rompería ese flujo de demostración de 20-25 minutos.

## Corrección al condicionamiento original de esta tarea

El plan original decía *"versionarlo como v10 al terminar F1"*. F1 ya está cerrado (2026-08-27,
esta misma sesión) y corrigió, entre otras cosas: el nombre del modelo (Qwen3.6 → 3.8), la
cifra de fragmentos de la biblioteca (3.852 → 3.811), el número de secciones del prompt
(12 → 13) y aclaró que las redes de validación son seis con un detalle de comportamiento
distinto al descrito (F1.3). **No se regenera el PDF v10 dentro de esta sesión** porque
`generar_docx.py` contiene el texto de cada capítulo escrito directamente en llamadas Python
(no lee de `docs/fuentes/capitulos/*.md`), así que aplicar las correcciones de F1 exige
editar ese script capítulo por capítulo — es trabajo de **F6** (expansión de capítulos
existentes), no de esta tarea de infraestructura. Verificado en esta sesión que
`libreoffice`/`soffice` SÍ está disponible en el entorno para la conversión DOCX→PDF cuando
llegue ese momento.

## Qué pasa cuando la tesina esté lista

En ese punto el PDF puede: (a) retirarse en favor de la tesina completa, o (b) mantenerse
como versión "resumen ejecutivo para no especialistas" derivada de los primeros capítulos de
la tesina. Esa decisión se toma al cerrar F12, con el documento completo ya sobre la mesa —
no tiene sentido decidirlo ahora.
