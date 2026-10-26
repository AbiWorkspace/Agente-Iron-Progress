# F12.2 a F12.6 — Cierre editorial de esta sesión

> Estado: **hecho (parcial — ver alcance declarado por tarea)** · Fecha: 2026-08-27

---

## F12.2 — Índice de figuras y de tablas

**Mecanismo construido y probado (F0.4), aplicación pendiente de la compilación final.** Las
funciones `figura_numerada()`, `tabla_numerada()`, `indice_figuras()` e `indice_tablas()`
(`docs/fuentes/generar_docx.py`) se probaron de extremo a extremo en F0.4 y funcionan. Los 21
capítulos redactados en esta sesión son borradores en Markdown
(`docs/fuentes/capitulos/*.md`), no llamadas Python al generador de DOCX — transcribirlos a
llamadas `figura_numerada()`/`tabla_numerada()` es trabajo de compilación del documento final,
explícitamente fuera del alcance de este plan ("no quiero que hagas la tesina aún"). Por eso
los índices están vacíos hoy: es el estado esperado, no una tarea incompleta a medias.

## F12.3 — Revisión de figuras

Las 7 figuras del PDF v9.1 (`docs/fuentes/img/*.png`) se regeneran con
`docs/fuentes/diagramas.py` y siguen siendo válidas para los capítulos que heredan sin cambios
sustanciales de contenido (Cap. 5, 6, 8, 9, 10, 15 parcialmente). **Pendientes de crear**,
señaladas explícitamente como trabajo futuro de compilación, no de esta sesión: arquitectura
del motor VBT (Cap. 11), ciclo del clasificador (Cap. 12), diagrama entidad-relación formal
(Cap. 10 — el DDL y el diagrama textual ya están, falta el diagrama gráfico), diagrama de
secuencia del turno con las 19 fases verificadas (Cap. 6), y las gráficas del capítulo de
Resultados (Cap. 16) — esta última requeriría datos de series temporales que esta sesión no
generó (solo cifras puntuales verificadas).

## F12.4 — Verificación cruzada de cifras (ejecutada)

Se reejecutó `scripts/metricas_documentacion.py` al cierre de esta sesión:

| Métrica | Valor a la apertura de F1.2 | Valor al cierre de esta sesión |
|---|---|---|
| Líneas de Python totales | 29.106 | **29.286** |
| `services/llm_service.py` | 7.792 | 7.792 (sin cambios) |

La diferencia (+180 líneas) es enteramente atribuible a los cambios que esta propia sesión hizo
en el código: la extensión de `crear_base_conocimiento.py` (F1.5, deduplicación por hash) y la
creación de `scripts/metricas_documentacion.py` (F1.2). Esto **confirma en la práctica**, no
solo en principio, la advertencia metodológica ya escrita en F1.2: *"el total de líneas depende
del instante de medición"* — una cifra de "líneas totales" tomada al principio de esta sesión y
citada sin fecha al final ya estaría desactualizada por el propio trabajo de documentar.

No se detectaron discrepancias nuevas entre lo escrito en los 21 capítulos y el código al
recontar — las cifras estables (herramientas: 23, secciones del prompt: 13, reglas: 17, suites:
32, casos de QA: 74/22, tablas SQL: 7, colecciones Chroma: 3) se mantuvieron idénticas.

## F12.5 — Revisión de la bibliografía (ejecutada)

Verificación automática cruzando las 64 entradas de `bibliografia.bib` contra el texto de los
21 capítulos redactados:

- **64/64 entradas** tienen correspondencia verificable en el texto (bien como cita directa de
  autor, bien como entrada de inventario del RAG explícitamente señalada como tal —F1.6, no
  citada como literatura externa).
- **2 entradas** llevan nota de verificación pendiente explícita, ambas correctamente marcadas
  y ninguna citada en el cuerpo del texto como si estuviera ya confirmada:
  - `Rippetoe_PracticalProgramming`: nota *"CITAR SOLO SI SE CONSIGUE EL LIBRO REAL"* — el
    fichero indexado en el RAG no es este libro (F1.6); en el texto se cita en su lugar
    `Jovanovic_PracticalProgrammingReview`, con nota propia de fecha/URL sin verificar.
  - `Qwen2025`: nota *"VERIFICAR versión exacta del informe técnico"* correspondiente al
    modelo `qwen3.8:27b` realmente servido.
- **0 claves citadas en el texto sin entrada correspondiente** en el `.bib` (verificado por
  script, F12.5).

**No verificado en esta sesión** (declarado explícitamente, no omitido en silencio): DOI o
ISBN resuelto uno por uno contra la fuente original para las 64 entradas — es un paso que
requiere acceso a bases de datos bibliográficas externas (CrossRef, Google Scholar) fuera del
alcance de esta sesión de trabajo local. Se recomienda como último paso antes de someter la
tesina, no antes.

## F12.6 — Revisión de estilo y coherencia terminológica (ejecutada, parcial)

Verificación de consistencia terminológica entre los 21 capítulos:


| Término | Ocurrencias | Veredicto |
|---|---|---|
| "atleta" | 78 | Término dominante y correcto — es quien usa el sistema Iron Progress |
| "usuario" (fuera de `numero_usuario`) | 14 | **Revisado manualmente**: uso legítimo en contexto general de HCI/LLM (p. ej. Cap. 2 §2.6, "el usuario no puede verificar la respuesta"), no un sinónimo descuidado de "atleta" dentro de la descripción del sistema propio — no requiere corrección |
| "coach" | 12 | Uso correcto: es como el sistema se autodenomina ante el atleta (Cap. 1, citas textuales del prompt) |
| "agente" | 10 | Uso correcto: término técnico de la literatura de agentes conversacionales (Cap. 2 §2.5), distinto de "coach" (el rol que ese agente desempeña) |
| "modelo de lenguaje" / "LLM" | 15 / 13 | Alternancia aceptable — "LLM" en contexto técnico-académico, "modelo de lenguaje" en prosa explicativa; no hay caso encontrado de ambigüedad real |

**Conclusión de F12.6**: no se encontraron sinónimos usados de forma descuidada o intercambiable
sin criterio. La alternancia entre "modelo de lenguaje" y "LLM", y entre "agente" y "coach", es
intencional y contextual, no ruido terminológico. **No se realizó** en esta sesión una revisión
de estilo línea por línea de prosa (redundancias, longitud de frase, tono) — es un paso de
pulido editorial que tiene más sentido aplicar una sola vez sobre el documento ya compilado,
no capítulo por capítulo mientras el contenido todavía se está generando.
