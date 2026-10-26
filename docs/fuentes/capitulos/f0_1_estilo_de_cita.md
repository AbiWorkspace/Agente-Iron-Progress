# F0.1 — Estilo de cita y formato

> Estado: **hecho (decisión de trabajo)** · Fecha: 2026-08-27

## Decisión

**APA 7.ª edición**, en español, con abstract en inglés.

## Justificación de la decisión tomada sin asesor presente

El plan preveía confirmar esto con el asesor de tesina. No hay forma de obtener esa
confirmación dentro de esta sesión de trabajo, así que se toma una decisión razonada y
**reversible** en vez de bloquear las 76 tareas restantes:

1. APA 7 es el estándar de facto en programas de ingeniería y ciencias de la computación en
   Latinoamérica para trabajos que combinan literatura técnica (papers de arXiv, conferencias
   ACM/IEEE) con literatura aplicada (libros de fuerza y acondicionamiento). IEEE es más
   común en trabajos puramente de ingeniería eléctrica/sistemas con predominio de papers de
   conferencia.
2. La bibliografía de este proyecto es mixta a propósito: papers de NeurIPS/ACL/ICLR,
   libros de ciencias del deporte, normativa legal (RGPD, LFPDPPP) y documentación de
   producto (Meta, Qwen). APA maneja con más naturalidad la mezcla de tipos de fuente que
   IEEE, que está pensado sobre todo para artículos numerados.
3. **Es reversible sin rehacer trabajo.** Todas las referencias de este plan (§6) y las que
   se añadan en `bibliografia.bib` (F0.2) llevan los datos completos (autor, año, título,
   fuente, DOI/ISBN cuando existe). Migrar de APA a IEEE en el `.bib` es un cambio de estilo
   de salida (`\bibliographystyle` o el equivalente en `generar_docx.py`), no una
   reinvestigación de fuentes.

## Qué falta de este punto y que SÍ requiere al asesor

- Extensión mínima/máxima exigida por el programa.
- Si el programa exige un capítulo de "Metodología" con estructura fija (hipótesis
  formales, variables dependiente/independiente) — el capítulo 4 de §2 de este plan asume
  una metodología de desarrollo de software, no un diseño experimental clásico, y esa
  discrepancia debe resolverse con el asesor antes de cerrar F5.
- Si se admite código como anexo o debe ir en repositorio aparte referenciado por URL.

**Acción pendiente para el usuario:** confirmar estos tres puntos con el asesor antes de
cerrar F12 (revisión editorial final). No bloquea el resto del plan.
