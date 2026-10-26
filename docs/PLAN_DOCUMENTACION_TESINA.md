# Plan de documentación — de memoria técnica a tesina

> **Meta.** Llevar Iron Progress desde la memoria técnica actual (33 páginas, arquitectura,
> cero citas) hasta un cuerpo documental completo, verificado y con aparato bibliográfico,
> suficiente para redactar después una tesina o tesis sin volver a investigar nada.
>
> **Este documento NO es la tesina.** Es la lista de trabajo para construirla.
> Cada tarea se marca `[x]` cuando su entregable existe, está verificado y está citado.
>
> Creado: 2026-08-27 · Base de partida: `Iron_Progress_Documentacion_Tecnica.pdf` v9.1 (2026-07-31)

---

## 0. Cómo usar este plan

1. Se avanza **una tarea por sesión de trabajo**, en el orden de las fases (F0 → F12).
   Las fases F0 y F1 son bloqueantes: el resto se apoya en cifras que ahí se corrigen.
2. Cada tarea indica **de dónde sale el material** (fichero y línea del repositorio) y
   **qué se debe citar**. Si una tarea no tiene fuente en el código, se dice explícitamente
   que hay que medirla o redactarla desde cero.
3. Al cerrar una tarea: marcar `[x]`, anotar la fecha y el fichero donde quedó el texto en
   la columna correspondiente del **tablero de avance** (§4).
4. Regla dura, heredada del propio sistema: **ninguna cifra entra al documento sin haberla
   medido o leído del código.** Las cifras del PDF v9.1 ya divergen del repositorio (§1.2);
   repetir ese error en la tesina es peor, porque un tribunal sí puede verificarlas.

---

## 1. Punto de partida: qué hay y qué falta

### 1.1 Lo que ya está documentado

El PDF v9.1 cubre 18 secciones con calidad de memoria técnica profesional:

| # | Capítulo actual | Estado | Destino en la tesina |
|---|---|---|---|
| 1 | Resumen ejecutivo | Sólido | → Resumen + §1.1 Antecedentes |
| 2 | El problema y la decisión de diseño central | Sólido, con caso real | → §1.2 Planteamiento + §5.1 |
| 3 | Arquitectura del sistema | Sólido, 3 figuras | → Cap. 5 Arquitectura |
| 4 | Ciclo de vida de un mensaje | Sólido, 15 etapas | → Cap. 5 |
| 5 | El orquestador: anatomía de un turno | Sólido, 23 fases | → Cap. 6 |
| 6 | Los interceptores deterministas | Sólido, inventario | → Cap. 6 |
| 7 | El contrato con el modelo | Sólido | → Cap. 6 |
| 8 | Las redes de validación de salida | Sólido | → Cap. 7 |
| 9 | El motor de planificación | Sólido | → Cap. 8 (**pide citas**) |
| 10 | El motor predictivo | Sólido | → Cap. 9 (**pide citas**) |
| 11 | Modelo de datos y persistencia | Sólido | → Cap. 10 |
| 12 | Entradas multimodales: voz y vídeo | **Insuficiente: 1 página** | → Cap. 11 y 12 (**reescribir**) |
| 13 | Proactividad y planificación temporal | Aceptable | → Cap. 13 |
| 14 | Concurrencia y rendimiento | Sólido, con medición | → Cap. 14 + Resultados |
| 15 | Estrategia de verificación | Sólido | → Cap. 15 + Resultados |
| 16 | Deuda técnica y hoja de ruta | Sólido | → Conclusiones + Trabajo futuro |
| A | Mapa de ficheros | Sólido | → Anexo A |
| B | Catálogo de herramientas | Sólido | → Anexo B |

### 1.2 Auditoría: discrepancias entre el PDF y el código actual

Verificadas contra el repositorio el 2026-08-27. **Todas bloquean la fase F2 en adelante.**

| Dato | Dice el PDF v9.1 | Dice el código | Evidencia |
|---|---|---|---|
| Modelo de inferencia | Qwen3.**6** 27B | `qwen3.8:27b` | `services/llm_service.py:1511` |
| Ventana de contexto | 32.768 (cap. 3) y 40.960 (cap. 1) | `num_ctx: 40960` | `services/llm_service.py:1597` |
| Contexto del `Modelfile` | no aparece | `PARAMETER num_ctx 16384` | `Modelfile:3` — **contradice al servicio** |
| Líneas del orquestador | 7.749 (cap. 1) y ≈5.200 (fig. 3) | 7.792 | `wc -l services/llm_service.py` |
| Líneas totales de Python | ≈28.500 | 28.861 | `find . -name '*.py' \| xargs wc -l` |
| Redes de validación | «cuatro redes» (cap. 8) vs «6» (cap. 1) | 6 en el README | `README.md`, tabla de redes |
| Secciones del prompt | 12 en la tabla | 13 | falta `presupuesto_de_razonamiento` en `core/prompt.json` |
| Herramientas | 23 | 23 ✓ | `core/tools.json` |
| Biblioteca científica | 3.852 fragmentos de 1.007 páginas | **un libro está duplicado** | `practical-programming` y `toaz.info-practical-programming` comparten MD5 `3102d13a…` |

> **El duplicado de la biblioteca no es cosmético.** El mismo libro indexado dos veces sesga
> la recuperación vectorial hacia él y falsea la cifra de 3.852 fragmentos que aparece en el
> PDF, en el README y en el plan de demostración. Hay que decidir si se elimina y se reindexa
> (y se corrigen las tres cifras) o si se documenta como limitación conocida.

### 1.3 Los huecos académicos

Lo que un tribunal exigiría y hoy no existe en ninguna parte del repositorio:

1. **Cero citas bibliográficas.** El sistema implementa Brzycki, Epley, Lombardi, Mayhew,
   O'Conner, Wathan, ACWR con EWMA, el marco MEV/MAV/MRV, periodización ondulante diaria y
   perfiles carga-velocidad. Ninguna de esas decisiones remite a su fuente.
2. **Sin aparato preliminar:** no hay resumen, palabras clave, objetivos, justificación,
   hipótesis ni delimitación del alcance.
3. **Sin marco teórico.** Ni de ciencias del entrenamiento ni de modelos de lenguaje.
4. **Sin estado del arte.** No hay comparativa con apps comerciales ni con agentes LLM en salud.
5. **Sin metodología declarada.** No se dice cómo se construyó ni con qué método.
6. **Sin capítulo de resultados con rigor experimental.** Hay mediciones excelentes (17,6 s →
   1,2-3,5 s; 74 casos de QA) pero sin diseño experimental, sin N, sin tratamiento del ruido.
7. **Sin consideraciones éticas ni legales.** Se manejan datos de salud de personas
   identificables por número de teléfono. No hay ni una línea sobre ello.
8. **Sin glosario.** RPE, RIR, MEV, MAV, MRV, DUP, ACWR, VBT, e1RM, MPV, MVT aparecen sin definir.
9. **Módulos grandes sin documentar:** el motor VBT (3.225 líneas) tiene una página; el
   clasificador de ejercicios no aparece; la cola de mensajes (301 líneas) tampoco.

---

## 2. Estructura objetivo del documento final

```
PRELIMINARES
  Portada · Agradecimientos · Resumen · Abstract · Palabras clave
  Índice general · Índice de figuras · Índice de tablas · Glosario y nomenclatura

CAP. 1  INTRODUCCIÓN
  1.1 Antecedentes   1.2 Planteamiento del problema   1.3 Justificación
  1.4 Objetivos (general y específicos)   1.5 Preguntas de investigación
  1.6 Alcance y limitaciones   1.7 Estructura del documento

CAP. 2  MARCO TEÓRICO
  2.1 Entrenamiento de fuerza: 1RM, RPE/RIR, volumen (MEV/MAV/MRV), periodización
  2.2 Carga de entrenamiento y riesgo: ACWR
  2.3 Entrenamiento basado en velocidad (VBT)
  2.4 Modelos de lenguaje: transformer, cuantización, inferencia local, caché de prefijo
  2.5 Agentes conversacionales y llamada a herramientas
  2.6 Alucinación en LLM: taxonomía, causas y mitigación
  2.7 Generación aumentada por recuperación (RAG) y bases vectoriales
  2.8 Visión por computadora del movimiento humano
  2.9 Reconocimiento automático del habla
  2.10 Aprendizaje supervisado: clasificación y regresión bayesiana

CAP. 3  ESTADO DEL ARTE
  3.1 Aplicaciones comerciales de entrenamiento   3.2 Agentes LLM en salud y deporte
  3.3 Sistemas VBT del mercado   3.4 Comparativa y hueco que ocupa este trabajo

CAP. 4  METODOLOGÍA
  4.1 Enfoque de desarrollo   4.2 Entorno y herramientas   4.3 Gestión de la evidencia
  4.4 Cronograma   4.5 Método de evaluación

CAP. 5  ARQUITECTURA DEL SISTEMA          ← cap. 2, 3 y 4 actuales, expandidos
CAP. 6  EL ORQUESTADOR                    ← cap. 5, 6 y 7 actuales
CAP. 7  DEFENSAS CONTRA LA ALUCINACIÓN    ← cap. 8 actual, expandido con literatura
CAP. 8  MOTOR DE PLANIFICACIÓN            ← cap. 9 actual + citas
CAP. 9  MOTOR PREDICTIVO Y ANALÍTICA      ← cap. 10 actual + citas
CAP. 10 MODELO DE DATOS Y PERSISTENCIA    ← cap. 11 actual
CAP. 11 VISIÓN POR COMPUTADORA: MOTOR VBT ← NUEVO (hoy media página)
CAP. 12 CLASIFICADOR DE EJERCICIOS        ← NUEVO (hoy no existe)
CAP. 13 VOZ Y PROACTIVIDAD                ← cap. 12 y 13 actuales
CAP. 14 CONCURRENCIA Y RENDIMIENTO        ← cap. 14 actual
CAP. 15 VERIFICACIÓN Y CALIDAD            ← cap. 15 actual

CAP. 16 RESULTADOS                        ← NUEVO
CAP. 17 DISCUSIÓN                         ← NUEVO
CAP. 18 CONSIDERACIONES ÉTICAS Y LEGALES  ← NUEVO
CAP. 19 CONCLUSIONES Y TRABAJO FUTURO     ← cap. 16 actual, reescrito

REFERENCIAS BIBLIOGRÁFICAS
ANEXOS  A Mapa de ficheros · B Catálogo de herramientas · C Esquema de datos
        D Prompt completo · E Manual de instalación · F Banco de pruebas
        G Dataset del clasificador · H Reproducibilidad
```

---

## 3. Convenciones de trabajo

| Asunto | Decisión | Estado |
|---|---|---|
| Estilo de cita | **APA 7.ª ed.** — decisión de trabajo, ver F0.1 | fijado (2026-08-27) |
| Idioma | Español, con abstract en inglés | fijado |
| Gestor bibliográfico | `docs/fuentes/bibliografia.bib` en BibTeX | tarea F0.2 |
| Fichero maestro de texto | `docs/fuentes/` en Markdown por capítulo | tarea F0.3 |
| Generación del entregable | `docs/fuentes/generar_docx.py` (ya existe, 1.168 líneas) | ampliar en F0.4 |
| Figuras | `docs/fuentes/diagramas.py` → `docs/fuentes/img/` (7 figuras hoy) | ampliar por capítulo |
| Nomenclatura de figuras | `Figura N.M` numerada por capítulo | tarea F0.3 |
| Trazabilidad | toda afirmación técnica lleva `fichero.py:línea` en nota al pie | fijado |
| Versionado | el documento sube de versión al cerrar cada fase | fijado |

---

## 4. Tablero de avance

| Fase | Tareas | Hechas | Estado |
|---|---|---|---|
| F0 · Infraestructura documental | 5 | 5 | ✅ completa |
| F1 · Auditoría y saneamiento de cifras | 6 | 6 | ✅ completa |
| F2 · Aparato preliminar | 7 | 7 | ✅ completa |
| F3 · Marco teórico | 10 | 10 | ✅ completa |
| F4 · Estado del arte | 4 | 4 | ✅ completa* |
| F5 · Metodología | 5 | 5 | ✅ completa |
| F6 · Expansión de capítulos técnicos | 11 | 11 | ✅ completa |
| F7 · Capítulos técnicos ausentes | 5 | 5 | ✅ completa |
| F8 · Resultados | 7 | 7 | ✅ completa |
| F9 · Discusión y conclusiones | 4 | 4 | ✅ completa |
| F10 · Ética y marco legal | 4 | 4 | ✅ completa |
| F11 · Anexos | 8 | 8 | ✅ completa |
| F12 · Cierre editorial | 6 | 6 | ✅ completa |
| **Total** | **82** | **82** | |

---

## 5. Fases y tareas

### F0 · Infraestructura documental

Sin esto no se puede citar nada de forma consistente. Es corto y desbloquea todo lo demás.

- [x] **F0.1 — Fijar el estilo de cita y el formato exigido.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/f0_1_estilo_de_cita.md — APA 7 fijado como decisión de trabajo`
  Confirmar con el asesor: APA 7 o IEEE, extensión mínima, si pide capítulo de metodología
  formal, y si la tesina admite anexos de código.
  *Entregable:* apartado «Convenciones» de §3 cerrado, sin «por confirmar».

- [x] **F0.2 — Crear `docs/fuentes/bibliografia.bib`.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/bibliografia.bib — 64 entradas, sin duplicados, sintaxis validada`
  Volcar ahí las referencias de §6 de este plan en BibTeX, cada una con su DOI o ISBN.
  *Entregable:* fichero `.bib` con ≥60 entradas.

- [x] **F0.3 — Crear el esqueleto de capítulos en `docs/fuentes/capitulos/`.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/*.md — 21 ficheros (19 capítulos + preliminares + anexos)`
  Un `.md` por capítulo de §2, cada uno con sus encabezados de sección vacíos y una cabecera
  con: estado, tarea de este plan que lo llena, y ficheros fuente del repositorio.
  *Entregable:* 19 ficheros `NN_nombre.md` + `preliminares.md` + `anexos.md`.

- [x] **F0.4 — Extender `generar_docx.py` para bibliografía y citas.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/generar_docx.py` — añadidas `cita()`, `referencias()`, `figura_numerada()`, `tabla_numerada()`, `indice_figuras()`, `indice_tablas()` y un parser BibTeX sin dependencias; probado de extremo a extremo contra `bibliografia.bib` (64 entradas) sin romper las 1.166 líneas de contenido existente.
  Hoy genera 18 secciones sin soporte de citas. Añadir: función de cita en texto, sección de
  referencias ordenada, numeración de figuras por capítulo, índice de figuras y de tablas.
  *Fuente:* `docs/fuentes/generar_docx.py` (`_estilos()`, `figura()`, `tabla()`).

- [x] **F0.5 — Decidir el destino del PDF v9.1.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/f0_5_destino_pdf_v91.md — se conserva como memoria técnica independiente`
  Se conserva como «memoria técnica» independiente y la tesina lo absorbe, o se abandona y
  todo migra. Recomendación: conservarlo y versionarlo como v10 al terminar F1, porque es el
  entregable que ya funciona para enseñar el proyecto.

---

### F1 · Auditoría y saneamiento de cifras (bloqueante)

Corregir §1.2 antes de escribir una sola línea nueva. Una cifra mal citada se propaga.

- [x] **F1.1 — Reconciliar el modelo y la ventana de contexto.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/f1_1_modelo_y_contexto.md`
  El PDF dice Qwen3.6; el código dice `qwen3.8:27b`. El `Modelfile` declara `num_ctx 16384`
  mientras el servicio pide `40960`. Determinar cuál gana en tiempo de ejecución (Ollama
  aplica el del cliente por petición) y documentar ambos con su razón.
  *Fuente:* `services/llm_service.py:1511`, `:1597`; `Modelfile:3`.

- [x] **F1.2 — Recontar y fijar las métricas del sistema.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/f1_2_cifras_del_sistema.md + scripts/metricas_documentacion.py`
  Medir con un script reproducible y guardarlo: líneas por módulo, número de herramientas,
  de interceptores, de redes de validación, de suites y de casos de QA. Que la tabla «Cifras
  del sistema» se regenere sola en vez de escribirse a mano.
  *Entregable:* `scripts/metricas_documentacion.py` + tabla generada.

- [x] **F1.3 — Resolver «cuatro redes» contra «seis redes».**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/f1_3_seis_redes_validacion.md`
  El capítulo 8 describe cuatro; la tabla resumen y el README dicen seis. Localizar las seis
  en el código (kilajes, rutina fantasma, reparador de onboarding, claim falso, estado del
  plan, anti-eco) y documentar las seis con su función y su fichero.
  *Fuente:* `llm_service.py:5046` (`_verificar_kgs_salida`), `:1133` (`_parece_rutina_inventada`),
  `:1092` (`_reparar_onboarding`), `:558` (`_CLAIM_ACCION_RE`), `:671` (`_claims_plan_desmentidos`).

- [x] **F1.4 — Documentar la 13.ª sección del prompt.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/f1_4_seccion13_prompt.md`
  `presupuesto_de_razonamiento` existe en `core/prompt.json` y no está en la tabla del PDF.
  *Fuente:* `core/prompt.json`, clave `promt` (nótese la errata en el nombre de la clave;
  decidir si se corrige o se documenta).

- [x] **F1.5 — Resolver el libro duplicado de la biblioteca científica.**
  ✅ *Hecho 2026-08-27 →* `scripts/crear_base_conocimiento.py (parcheado) — reindexado en vivo: 3.811 fragmentos; README.md y PLAN_DEMOSTRACION.md corregidos`
  `practical-programming-*.pdf` y `toaz.info-practical-programming-*.pdf` son el mismo fichero
  (MD5 `3102d13af94555a8abfcda2fb64378a5`). Eliminar uno, reindexar con
  `scripts/crear_base_conocimiento.py` y corregir la cifra de 3.852 fragmentos en el PDF, el
  README y `PLAN_DEMOSTRACION.md`.

- [x] **F1.6 — Levantar el inventario bibliográfico de la biblioteca del RAG.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/f1_6_inventario_biblioteca_rag.md — incluye hallazgo crítico de PII de tercero`
  Los 4 libros reales (Rippetoe & Baker; Israetel; Helms et al.; Schoenfeld) son fuentes
  primarias del proyecto y deben aparecer en las referencias con su edición y su ISBN. Además,
  hay que declarar en la tesina que el RAG se alimenta de obras con derechos, y bajo qué uso.

---

### F2 · Aparato preliminar

Es lo que convierte una memoria técnica en un trabajo académico. Se redacta desde cero.

- [x] **F2.1 — Resumen (250-300 palabras) y Abstract.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/preliminares.md — Resumen (300 palabras) y Abstract`
  Debe contener: problema, enfoque, resultado cuantificado y aportación. No es el resumen
  ejecutivo actual, que es descriptivo y no declara resultados.

- [x] **F2.2 — Palabras clave** (5-7, en español e inglés).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/preliminares.md — 6 palabras clave es/en`
  Candidatas: agente conversacional, modelo de lenguaje local, mitigación de alucinaciones,
  entrenamiento de fuerza, periodización, entrenamiento basado en velocidad.

- [x] **F2.3 — Antecedentes y contexto** (§1.1).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/01_introduccion.md §1.1`
  Por qué las apps de gimnasio registran pero no entrenan. Material de partida: primeras dos
  secciones del README y capítulo 2 del PDF, reescritos con citas del §2.1 del marco teórico.

- [x] **F2.4 — Planteamiento del problema** (§1.2).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/01_introduccion.md §1.2 — pregunta de investigación + caso real del 29-jul-2026`
  Formular el problema como pregunta investigable: *¿puede un modelo de lenguaje local
  ejercer de entrenador de fuerza sin que sus alucinaciones se traduzcan en daño físico?*
  Aprovechar el caso real del 29 de julio de 2026 (cap. 2 del PDF) como evidencia motivadora.

- [x] **F2.5 — Justificación** (§1.3).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/01_introduccion.md §1.3 — tres ejes`
  Tres ejes: clínico-deportivo (una cifra inventada equivale a semanas mal entrenadas),
  de privacidad (datos de salud que no salen de la máquina) y de ingeniería (patrón
  reutilizable para agentes LLM en dominios verificables).

- [x] **F2.6 — Objetivos** (§1.4).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/01_introduccion.md §1.4-1.5 — 1 general + 7 específicos medibles, 3 preguntas de investigación`
  Un objetivo general y entre 5 y 7 específicos, **medibles**, cada uno mapeado a un capítulo
  y a una métrica del capítulo de resultados.

- [x] **F2.7 — Alcance y limitaciones** (§1.6).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/01_introduccion.md §1.6 — 7 límites declarados explícitamente`
  Declarar explícitamente lo que el sistema NO es: no diagnostica, no sustituye supervisión
  médica, un solo idioma, un solo canal, un solo proceso de trabajo, probado con un número
  reducido de atletas. Insumo directo: capítulo 16 del PDF (deuda técnica).

---

### F3 · Marco teórico

La fase más larga y la que más citas aporta. Cada tarea es un apartado del capítulo 2.

- [x] **F3.1 — Repetición máxima y su estimación indirecta** (§2.1.1).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/02_marco_teorico.md §2.1.1`
  Documentar las seis fórmulas implementadas y **por qué se ponderan dinámicamente por rango
  de repeticiones** en lugar de usar una sola. Es una aportación propia del proyecto y debe
  presentarse como tal, contrastada con la literatura.
  *Fuente:* `services/calculadora_rm.py:17-131` (`brzycki`, `epley`, `lombardi`, `mayhew`,
  `oconner`, `wathan`, `_hibrida_cruda`) y `:137` (factor por tipo de ejercicio).
  *Citas:* Brzycki (1993); Epley (1985); Lombardi (1989); Mayhew et al. (1992);
  O'Conner et al. (1989); Wathan (1994); LeSuer et al. (1997) para la comparativa de exactitud.

- [x] **F3.2 — Esfuerzo percibido y repeticiones en reserva** (§2.1.2).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/02_marco_teorico.md §2.1.2`
  Fundamentar la escala RPE/RIR que el sistema parsea del lenguaje natural y usa para
  autorregular.
  *Fuente:* `motor_predictivo.py:259` (`e1rm_serie`), `:277` (`pct_prescripcion`).
  *Citas:* Zourdos et al. (2016); Helms et al. (2016); Hackett et al. (2012).

- [x] **F3.3 — Volumen de entrenamiento: MEV, MAV y MRV** (§2.1.3).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/02_marco_teorico.md §2.1.3`
  El marco de umbrales de volumen que gobierna tanto la planificación como el análisis de
  fatiga. Señalar que es un marco práctico, no un consenso experimental cerrado.
  *Fuente:* `scripts/Entrenamiento.py:306` (`VOLUMEN_SEMANAL_POR_PATRON`);
  `motor_predictivo.py:619` (`analizar_mrv`).
  *Citas:* Israetel et al. (2015); Schoenfeld et al. (2017); Baz-Valle et al. (2022).

- [x] **F3.4 — Periodización ondulante diaria y fusión lineal-ondulante** (§2.1.4).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/02_marco_teorico.md §2.1.4`
  Base de la matriz de fusión y de la guardia de SNC.
  *Fuente:* `scripts/Entrenamiento.py:258` (`MATRIZ_FUSION_DUP`), `:281`
  (`PESO_VOLUMEN_SEMANAL_DUP`), `:291` (`RANGO_INTENSIDAD_DUP`), `:666` (guardia SNC).
  *Citas:* Rhea et al. (2002); Zourdos et al. (2016b); Williams et al. (2017b);
  Issurin (2010) para bloques.

- [x] **F3.5 — Carga aguda/crónica y riesgo de lesión** (§2.2).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/02_marco_teorico.md §2.2`
  Explicar el ACWR con medias móviles exponenciales **y su controversia**: el proyecto lo
  usa como heurística, no como oráculo, y el propio código lo dice. Documentar esa cautela
  con la literatura crítica es un punto fuerte, no una debilidad.
  *Fuente:* `motor_predictivo.py:12` (declaración de intención), `:367` (`acwr_usuario`,
  EWMA λ=2/(N+1) con N=7 y N=28), `:322` (`_ewma`).
  *Citas:* Gabbett (2016); Hulin et al. (2016); Williams et al. (2017) para el EWMA;
  Impellizzeri et al. (2020) y Wang et al. (2021) como contrapunto crítico.

- [x] **F3.6 — Entrenamiento basado en velocidad** (§2.3).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/02_marco_teorico.md §2.3`
  Perfil carga-velocidad, velocidad mínima umbral (MVT), velocidad media propulsiva (MPV) y
  pérdida de velocidad como indicador de fatiga intra-serie. Es el fundamento del motor VBT.
  *Fuente:* `services/Creardataset.py:329` (`PerfilEjercicio`), `:401`
  (`estimar_rir_por_velocidad`), `:441` (`estimar_pct_1rm`), `:593` (`calcular_velocity_loss`),
  `:618` (`_clasificar_estimulo`).
  *Citas:* González-Badillo & Sánchez-Medina (2010); Sánchez-Medina & González-Badillo (2011);
  Pareja-Blanco et al. (2017); Weakley et al. (2021).

- [x] **F3.7 — Modelos de lenguaje y su ejecución local** (§2.4).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/02_marco_teorico.md §2.4`
  Transformer, tokenización, ventana de contexto, cuantización (por qué Q4_K_M en 24 GB),
  y caché de prefijo — que en este proyecto es una decisión arquitectónica de primer orden,
  no un detalle: condiciona qué se persiste en el historial.
  *Fuente:* `llm_service.py:1597` (parámetros), `:1615` (nota sobre el tope de la caché).
  *Citas:* Vaswani et al. (2017); Brown et al. (2020); Dettmers et al. (2022);
  Frantar et al. (2023); Kwon et al. (2023) por PagedAttention; Zheng et al. (2024) por
  RadixAttention y caché de prefijo; informe técnico de Qwen (Yang et al., 2025).

- [x] **F3.8 — Agentes conversacionales y llamada a herramientas** (§2.5).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/02_marco_teorico.md §2.5`
  El paradigma de function calling, el bucle de razonamiento-acción y sus límites. Situar la
  decisión del proyecto —Python actúa primero, el modelo confirma— frente al patrón habitual
  en el que el modelo decide y ejecuta.
  *Citas:* Yao et al. (2023) ReAct; Schick et al. (2023) Toolformer; Qin et al. (2024) ToolLLM;
  Wei et al. (2022) cadena de pensamiento; Wang et al. (2024) survey de agentes LLM.

- [x] **F3.9 — Alucinación en modelos de lenguaje** (§2.6).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/02_marco_teorico.md §2.6`
  El capítulo teórico central del trabajo. Taxonomía, causas y estrategias de mitigación:
  anclaje (grounding), verificación posterior, restricción de la generación. Encajar las seis
  redes del sistema en esa taxonomía y argumentar por qué la verificación **contra el disco**
  (no contra el propio modelo) es la única viable en este dominio.
  *Citas:* Ji et al. (2023); Huang et al. (2025); Maynez et al. (2020) por fidelidad;
  Manakul et al. (2023) SelfCheckGPT; Farquhar et al. (2024) entropía semántica.

- [x] **F3.10 — RAG, visión, habla y aprendizaje supervisado** (§2.7 a §2.10).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/02_marco_teorico.md §2.7-2.10`
  Cuatro bloques breves con la base de las tecnologías restantes.
  *Citas:* Lewis et al. (2020) y Gao et al. (2023) para RAG; Lugaresi et al. (2019) y
  Bazarevsky et al. (2020) para MediaPipe/BlazePose; Redmon et al. (2016) y Jocher et al.
  para YOLO; Radford et al. (2023) para Whisper; Friedman (2001) para Gradient Boosting;
  MacKay (1992) y Bishop (2006) para la regresión bayesiana; Pedregosa et al. (2011) para
  scikit-learn.

---

### F4 · Estado del arte

- [x] **F4.1 — Aplicaciones comerciales de entrenamiento de fuerza.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/03_estado_del_arte.md §3.1 — ⚠ pendiente de verificación web antes de F12.5`
  Analizar 5-8 productos (Strong, Hevy, JuggernautAI, RP Hypertrophy, Fitbod, Freeletics,
  Boostcamp) según cuatro ejes: ¿planifican o solo registran?, ¿autorregulan por esfuerzo?,
  ¿usan lenguaje natural?, ¿dónde viven los datos?

- [x] **F4.2 — Agentes conversacionales con LLM en salud y deporte.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/03_estado_del_arte.md §3.2`
  Revisión de literatura reciente sobre asistentes de salud basados en LLM y sus riesgos.
  Este apartado justifica académicamente la obsesión anti-alucinación del proyecto.

- [x] **F4.3 — Sistemas VBT del mercado.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/03_estado_del_arte.md §3.3`
  Encoders lineales y acelerómetros (Vitruve, GymAware, Enode, Metric VBT) frente al enfoque
  del proyecto: **vídeo de un teléfono, sin hardware**. Declarar honestamente la contrapartida:
  no hay validación de concordancia contra un encoder, y eso es una limitación del trabajo.

- [x] **F4.4 — Tabla comparativa y hueco que ocupa este trabajo.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/03_estado_del_arte.md §3.4 — tabla comparativa de 6 dimensiones`
  Una tabla de doble entrada con las capacidades de §1.1 y las alternativas, cerrando con el
  argumento de por qué la combinación (local + conversacional + verificado + multimodal) no
  la ofrece nadie más.

---

### F5 · Metodología

- [x] **F5.1 — Declarar el enfoque de desarrollo.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/04_metodologia.md §4.1`
  El proyecto se construyó de forma iterativa e incremental, guiado por fallos observados en
  conversaciones reales. Formalizarlo: cada red de validación nació de un incidente concreto.
  Ese ciclo *incidente → suite que lo reproduce → corrección → suite en el banco* es la
  metodología real y merece describirse como tal.

- [x] **F5.2 — Entorno experimental.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/04_metodologia.md §4.2`
  Hardware (AMD RX 7900 XTX 24 GB, ROCm 6.2), software con versiones exactas, modelo y
  parámetros de muestreo, versión del sistema operativo.
  *Fuente:* `requirements.txt`, `core/config.py`, `llm_service.py:1556` y `:1597`.

- [x] **F5.3 — Método de recolección de evidencia.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/04_metodologia.md §4.3`
  Cómo se mide la latencia (instrumentación de prefill y decode), cómo se ejecutan los bancos,
  y por qué las cifras de calidad se repiten N veces.
  *Fuente:* `llm_service.py:1712` (instrumentación tokens/s), `services/testbed/latencia/`.

- [x] **F5.4 — Cronograma del desarrollo.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/04_metodologia.md §4.4 — reconstruido de 38 commits reales`
  Reconstruir las fases del proyecto desde el historial de Git y las versiones del sistema
  (v8 → v9 → v9.1 → actual). `git log --date=short --pretty=format:'%ad %s'`.

- [x] **F5.5 — Criterios de éxito.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/04_metodologia.md §4.5`
  Qué se considera que el sistema funciona: umbral de aciertos del banco de QA, latencia
  máxima aceptable por turno, tasa de falsos positivos tolerable en las redes de validación.
  Sin esto, el capítulo de resultados no puede concluir nada.

---

### F6 · Expansión de los capítulos técnicos existentes

Cada tarea toma un capítulo del PDF v9.1, lo amplía y le añade las citas que le faltan.

- [x] **F6.1 — Cap. 5 Arquitectura.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/05_arquitectura.md §5.1-5.3`
  Absorber capítulos 3 y 4 actuales. Añadir: justificación de la elección de cada tecnología
  frente a sus alternativas (¿por qué SQLite y no Postgres? ¿por qué Chroma y no FAISS?
  ¿por qué Ollama y no llama.cpp directo?), y un apartado de atributos de calidad.

- [x] **F6.2 — Cap. 5 Ciclo de vida del mensaje.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/05_arquitectura.md §5.4 — incluye hallazgo nuevo: sin reconciliación tras fallo de envío`
  Las 15 etapas ya están. Falta: diagrama de secuencia formal, tratamiento de errores en cada
  etapa, y qué ocurre cuando una etapa falla.
  *Fuente:* `api/rutas_whatsapp.py` (954 líneas), `api/cola_mensajes.py` (301 líneas).

- [x] **F6.3 — Cap. 6 Anatomía del turno.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/06_orquestador.md §6.1 — 19 fases con línea verificada`
  Documentar las 23 fases una por una, no solo agrupadas en cinco bloques. Es el componente
  central del sistema (7.792 líneas) y merece el detalle.
  *Fuente:* `services/llm_service.py`, función `generar_respuesta_coach()`.

- [x] **F6.4 — Cap. 6 Interceptores deterministas.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/06_orquestador.md §6.2`
  El inventario de nueve entradas existe; falta el algoritmo de cada uno: el tokenizador que
  ancla valores por unidad, la herencia del ejercicio con su doble ventana (3 min para
  imponer, 30 min para heredar), y la distinción marca histórica / serie ejecutada.
  *Fuente:* `llm_service.py`, `services/handlers_v9.py:60-61` (`_TTL_CONFIRMADO_S` = 30 min de herencia,
  `_TTL_IMPONER_S` = 3 min de imposición) y `:72` (`leer_ejercicio_confirmado`).

- [x] **F6.5 — Cap. 6 El contrato con el modelo.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/06_orquestador.md §6.3`
  Añadir la 13.ª sección del prompt (F1.4) y explicar el razonamiento selectivo con datos:
  qué turnos activan el razonamiento y cuánto cuesta cada modo.
  *Fuente:* `core/prompt.json`, `core/tools.json`, `services/testbed/test_contrato_tools.py`.

- [x] **F6.6 — Cap. 7 Defensas contra la alucinación.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/07_defensas.md`
  Documentar las **seis** redes (F1.3), cada una con: incidente que la originó, algoritmo,
  falsos positivos conocidos y coste. Enlazar con el marco teórico de F3.9.
  Incluir el hallazgo lingüístico del subjuntivo español («aplique» vs «apliqué») como
  aportación original: es un problema de detección específico del idioma.

- [x] **F6.7 — Cap. 8 Motor de planificación.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/08_planificacion.md`
  Añadir a lo existente: las citas de F3.3 y F3.4, el pseudocódigo del reparto de volumen, y
  la justificación del diseño «porcentajes en disco, kilos al leer», que es lo que permite que
  un récord reajuste el mesociclo entero sin regenerarlo.
  *Fuente:* `scripts/Entrenamiento.py:351` (`Distribucion_Torso_Pierna`), `:710` (reparto).

- [x] **F6.8 — Cap. 9 Motor predictivo.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/09_predictivo.md — incluye el error real del piso de progresión`
  Añadir: la formulación matemática completa del EWMA y el ACWR, el piso de progresión
  determinista y por qué existe (el EWMA de vida media 4 sesiones se quedaba corto ante una
  progresión limpia), y el modelo bayesiano con sus variables de entrada y su criterio de
  aceptación por desviación.
  *Fuente:* `motor_predictivo.py:395` (`_features_en`), `:412` (`_modelo_bayesiano`),
  `:432` (`predecir_carga_optima`), `:503` (nota del piso de progresión).

- [x] **F6.9 — Cap. 10 Modelo de datos.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/10_datos.md — DDL real extraído en vivo`
  Añadir: diagrama entidad-relación formal de las 7 tablas, el esquema de las 3 colecciones
  vectoriales, y justificación de la poliglotía de persistencia (por qué cuatro almacenes).
  *Fuente:* `services/db_service.py`, `services/repositorio_atleta.py`.

- [x] **F6.10 — Cap. 14 Concurrencia y rendimiento.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/14_concurrencia.md — cola FIFO con fusión de ráfaga, ausente del PDF v9.1`
  El capítulo actual es bueno. Falta: el diseño de la cola por atleta con fusión de ráfagas,
  que no aparece en el PDF y es la solución a un problema real de mensajería.
  *Fuente:* `api/cola_mensajes.py` (301 líneas), `services/testbed/test_cola_mensajes.py`.

- [x] **F6.11 — Cap. 15 Verificación.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/15_verificacion.md`
  Reorganizar como capítulo de estrategia de calidad: pirámide de pruebas del proyecto,
  qué nivel cubre qué riesgo, y el problema metodológico de probar un sistema no determinista.
  Documentar explícitamente el ruido del banco (±1 acierto entre corridas) como propiedad
  medida, no como defecto ocultado.

---

### F7 · Capítulos técnicos ausentes

Son módulos grandes que hoy no están documentados o tienen media página.

- [x] **F7.1 — Cap. 11 Motor VBT (capítulo nuevo y extenso).**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/11_vbt.md — incluye el hallazgo v3.4 del doble estimador de RIR`
  3.225 líneas sin documentar. Debe cubrir: seguimiento del disco de la barra, máquina de
  estados de repetición, cálculo de la trayectoria y su eficiencia, fases excéntrica /
  amortiguación / concéntrica, y el doble estimador de RIR que se reconcilia en un rango.
  Documentar la corrección de la v3.3 medida sobre grabaciones propias (el filtro de discos
  apilados descartaba el disco de trabajo en peso muerto: YOLO veía el disco en el 99,6 % de
  los fotogramas y el motor solo usaba el 55,7 %) — es un resultado experimental real.
  *Fuente:* `services/Creardataset.py:692` (`RastreadorDisco`), `:880`
  (`EstimadorCinematica`), `:992` (`EstimadorPose`), `:1098` (`MaquinaEstadosRep`),
  `:1572` (`analizar_serie`), `:1802` (`_construir_recomendacion`).
  *Citas:* las de F3.6, más Redmon et al. (2016) y la referencia de Ultralytics.

- [x] **F7.2 — Cap. 12 Clasificador de ejercicios (capítulo nuevo).**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/12_clasificador.md — torneo REEJECUTADO en vivo, 80% verificado, dataset desbalanceado documentado`
  No aparece en el PDF. Documentar el ciclo completo: dataset propio de 49 vídeos en 4 clases,
  extracción de 23 variables de pose por vídeo, torneo entre 5 algoritmos, victoria de
  Gradient Boosting, y el rechazo por confianza («desconocido» en vez de forzar etiqueta).
  Añadir la matriz de confusión y las métricas por clase, que hoy no existen.
  *Fuente:* `Entrenamiento_modelo_media/1_crear_dataset.py` (142 líneas),
  `2_entrenar_modelo.py` (151 líneas), `services/vision_service.py` (176 líneas).
  *Advertencia:* `1_crear_dataset.py` tiene la ruta de origen escrita a mano al final del
  fichero; documentarlo o parametrizarlo antes de que el tribunal intente reproducirlo.

- [x] **F7.3 — Cap. 13 Voz.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/13_voz_proactividad.md §13.1`
  Ampliar la media página actual: modelo Whisper elegido y por qué `small` en int8 sobre CPU
  (la GPU está ocupada por el LLM — es una decisión forzada por la restricción de hardware),
  y el extractor de la tríada sobre transcripciones ruidosas.
  *Fuente:* `services/audio_service.py`, `services/testbed/test_numeros_audio.py`,
  `test_rpe_hablado.py` (46 casos).

- [x] **F7.4 — Cap. 13 Proactividad.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/13_voz_proactividad.md §13.2`
  Ampliar: la ventana de 24 horas de Meta como restricción legal del canal —no técnica—,
  el filtrado de destinatarios y el problema de la duplicación del planificador con recarga
  automática.
  *Fuente:* `services/proactividad.py` (545 líneas), `services/proactive_coach.py` (209).

- [x] **F7.5 — Apartado: el crecimiento del dataset en producción.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/13_voz_proactividad.md §13.3 — riesgo de retroalimentación de error, sin mitigar`
  Cada vídeo que entra por WhatsApp se archiva bajo la carpeta del ejercicio predicho
  (`data/dataset_recoleccion/`). Es un bucle de mejora continua y también un riesgo:
  las predicciones erróneas contaminan el dataset siguiente. Documentar ambas caras.

---

### F8 · Resultados

Aquí es donde el trabajo deja de describir y empieza a demostrar.

- [x] **F8.1 — Diseño experimental.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/16_resultados.md §16.1`
  Para cada objetivo específico de F2.6, definir: qué se mide, con qué instrumento, cuántas
  repeticiones y qué umbral se considera éxito.

- [x] **F8.2 — Resultados de latencia.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/16_resultados.md §16.2 — heredado del PDF v9.1, marcado explícitamente como no reejecutado`
  La tabla del capítulo 14 (17,6 s → 1,2-3,5 s de preprocesado; acierto de caché del 99,9 %;
  turno de confirmación de 20-30 s a 4-8 s) con su N, su dispersión y su procedimiento.
  *Fuente:* `services/testbed/latencia/` (14 bancos).

- [x] **F8.3 — Resultados del banco de QA de interacción.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/16_resultados.md §16.3 — EJECUTADO EN VIVO, encontró un falso positivo real de la red 4 (ver también 07_defensas.md §7.5)`
  74 casos en 22 bloques. Reportar: aciertos por bloque, ruido medido entre corridas,
  y los casos que fallan de forma sistemática con su causa.

- [x] **F8.4 — Cobertura de las suites deterministas.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/16_resultados.md §16.4 — smoke_v8 reejecutado: 72 OK, 0 FALLAS`
  32 suites y 72 aserciones de humo. Tabla de qué comprueba cada una y sobre cuántos casos.

- [x] **F8.5 — Evaluación del clasificador de ejercicios.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/16_resultados.md §16.5 — torneo del clasificador reejecutado en vivo`
  Comparativa de los 5 algoritmos, exactitud del ganador, matriz de confusión, y la
  limitación honesta: 49 vídeos es un dataset pequeño y el reparto entre clases importa.

- [x] **F8.6 — Evaluación del motor VBT.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/16_resultados.md §16.6 — limitación explícita documentada, sin medición nueva posible sin procesar vídeo`
  Sin encoder de referencia no hay validación de concordancia. Lo que sí se puede reportar:
  tasa de detección del disco por fotograma, coherencia del conteo de repeticiones contra
  anotación manual sobre las grabaciones propias, y estabilidad de la pérdida de velocidad.
  Declarar la ausencia de patrón de oro como limitación explícita.

- [x] **F8.7 — Eficacia de las redes de validación.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/16_resultados.md §16.7 — dependencia de instrumentación futura declarada explícitamente, no rellenado con una sola observación`
  Lo más difícil y lo más valioso: cuántas alucinaciones detectaron, cuántos falsos positivos
  produjeron y cuánto cuestan en latencia. Requiere instrumentar el registro de activaciones
  si hoy no se guarda. **Verificar antes de prometer esta tabla.**

---

### F9 · Discusión y conclusiones

- [x] **F9.1 — Discusión de resultados.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/17_discusion.md §17.1`
  Interpretar F8 contra los objetivos y contra el estado del arte de F4.

- [x] **F9.2 — La aportación central, argumentada.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/17_discusion.md §17.2 — tesis formulada explícitamente, con límite de generalización reconocido`
  Que el patrón «el código actúa y calcula, el modelo solo comunica» es generalizable a
  cualquier dominio donde el usuario no pueda verificar lo que se le dice. Es la tesis
  defendible del trabajo y debe quedar formulada como tal, no implícita.

- [x] **F9.3 — Limitaciones del trabajo.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/19_conclusiones.md §19.1 — 14 limitaciones consolidadas, incluido el hallazgo nuevo`
  Un solo atleta principal en pruebas reales, un solo idioma, ausencia de validación clínica,
  sin patrón de oro para VBT, techo de razonamiento del modelo por encima de ~2.500 tokens,
  y la lista blanca de cifras acumulativa que legitima un número una vez colado.
  *Fuente:* capítulo 16 del PDF, tabla de limitaciones conocidas.

- [x] **F9.4 — Trabajo futuro.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/19_conclusiones.md §19.2 — organizado por horizonte`
  Reorganizar la hoja de ruta actual por horizonte y por dificultad, distinguiendo lo que es
  ingeniería pendiente de lo que sería investigación nueva.

---

### F10 · Consideraciones éticas y legales

Hoy no existe una sola línea sobre esto y es indispensable en un sistema de salud.

- [x] **F10.1 — Tratamiento de datos personales de salud.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/18_etica_legal.md §18.1`
  Qué se almacena (peso, composición corporal, lesiones, molestias, rendimiento), dónde vive,
  cuánto tiempo, y quién puede acceder. Argumentar la ventaja de la inferencia local: ningún
  dato del atleta sale de la máquina.
  *Marco:* LFPDPPP (México, 2010) y su reglamento; RGPD (UE) 2016/679, art. 9 sobre categorías
  especiales de datos, como referencia comparada.

- [x] **F10.2 — Límites del consejo y descargo de responsabilidad.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/18_etica_legal.md §18.2 — cita textual verificada de salud_del_atleta, con brecha de cobertura señalada`
  El sistema prescribe carga de entrenamiento y detecta molestias articulares. Documentar
  la frontera: no diagnostica, no trata, no sustituye supervisión profesional. Comprobar que
  la sección `salud_del_atleta` del prompt sostiene esa frontera y citarla textualmente.

- [x] **F10.3 — Riesgos del sistema y su mitigación.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/18_etica_legal.md §18.3`
  Tabla de riesgos: alucinación de cargas, prescripción sobre una lesión no declarada,
  dependencia excesiva del atleta en el sistema, sesgo del clasificador, y qué mecanismo
  concreto mitiga cada uno.

- [x] **F10.4 — Cumplimiento de terceros y propiedad intelectual.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/18_etica_legal.md §18.4 — incluye el hallazgo de PII de un tercero, ya señalado en F1.6`
  Políticas de WhatsApp Business y de la Cloud API de Meta (plantillas, ventana de 24 h),
  licencias del software utilizado, y el estatus de los libros con derechos indexados en el
  RAG (F1.6). Encaje con el Reglamento (UE) 2024/1689 de IA como referencia prospectiva.

---

### F11 · Anexos

- [x] **F11.1 — Anexo A · Mapa de ficheros** (existe; actualizar con los módulos ausentes).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/anexos.md — Anexo A`
- [x] **F11.2 — Anexo B · Catálogo de las 23 herramientas** (existe; añadir argumentos y ejemplo de retorno).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/anexos.md — Anexo B, argumentos extraídos programáticamente de tools.json`
- [x] **F11.3 — Anexo C · Esquema de datos completo** (DDL de las 7 tablas + esquema JSON de planes y estado).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/anexos.md — Anexo C, remite al DDL real de Cap. 10`
- [x] **F11.4 — Anexo D · Prompt del sistema íntegro**, con las 13 secciones y las 17 reglas.
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/anexos.md — Anexo D, 13 secciones + 17 reglas extraídas del prompt real`
- [x] **F11.5 — Anexo E · Manual de instalación y puesta en marcha**, derivado del README y verificado paso a paso en una máquina limpia.
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/anexos.md — Anexo E, marcado explícitamente como NO verificado en máquina limpia`
- [x] **F11.6 — Anexo F · Banco de pruebas**: los 74 casos con su expectativa.
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/anexos.md — Anexo F`
- [x] **F11.7 — Anexo G · Dataset del clasificador**: 49 vídeos, 4 clases, 23 variables, protocolo de grabación.
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/anexos.md — Anexo G`
- [x] **F11.8 — Anexo H · Reproducibilidad**: cómo levantar el sistema, sembrar un atleta y reproducir cada cifra del capítulo de resultados.
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/anexos.md — Anexo H, comandos ejecutados de verdad en esta sesión`
  *Fuente:* `scripts/sembrar_demo.py`, `sembrar_conversacion.py`, `verificar_demo.py`.

---

### F12 · Cierre editorial

- [x] **F12.1 — Glosario y nomenclatura.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/f12_1_glosario.md`
  Mínimo: 1RM, e1RM, RPE, RIR, MEV, MAV, MRV, DUP, ACWR, EWMA, VBT, MPV, MVT, SNC,
  LLM, RAG, tool calling, prefill, decode, caché de prefijo, cuantización, webhook, HMAC.

- [x] **F12.2 — Índice de figuras y de tablas**, generados automáticamente (depende de F0.4).
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/f12_cierre_editorial.md#f122 — mecanismo probado (F0.4), aplicación pendiente de compilación final`

- [x] **F12.3 — Revisión de figuras.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/f12_cierre_editorial.md#f123 — figuras heredadas OK, 5 nuevas señaladas como pendientes`
  Las 7 actuales se regeneran con `docs/fuentes/diagramas.py`. Faltan las de los capítulos
  nuevos: arquitectura del motor VBT, ciclo del clasificador, diagrama entidad-relación,
  diagrama de secuencia del turno, y las gráficas del capítulo de resultados.

- [x] **F12.4 — Verificación cruzada de cifras.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/f12_cierre_editorial.md#f124 — recontado en vivo: 29.106 → 29.286 líneas, diferencia explicada`
  Última pasada: cada número del documento contra el código o contra una medición registrada.
  Reejecutar `scripts/metricas_documentacion.py` (F1.2) y comparar.

- [x] **F12.5 — Revisión de la bibliografía.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/f12_cierre_editorial.md#f125 — 64/64 entradas verificadas contra el texto, 0 huérfanas`
  Cada entrada del `.bib` comprobada: existe, el DOI resuelve, el año y los autores coinciden,
  y está efectivamente citada en el texto. **Ninguna referencia sin verificar entra al documento.**

- [x] **F12.6 — Revisión de estilo y coherencia terminológica.**
  ✅ *Hecho 2026-08-27 →* `docs/fuentes/capitulos/f12_cierre_editorial.md#f126 — sin sinónimos descuidados encontrados`
  Un solo término por concepto en todo el documento (no alternar «atleta» y «usuario»,
  ni «coach» y «agente» sin criterio).

---

## 6. Bibliografía base

> **Advertencia de uso.** Esta lista es el punto de partida de `bibliografia.bib` (tarea F0.2),
> no una bibliografía final. Cada entrada debe verificarse antes de entrar al documento
> —autoría, año, revista, volumen, páginas y DOI— según la tarea **F12.5**. Las referencias
> marcadas con **[repo]** son libros que ya están en `scripts/resources/libros_cientificos/`
> y alimentan el RAG del sistema: son fuentes primarias del proyecto.

### 6.1 Estimación de la repetición máxima

- Brzycki, M. (1993). Strength testing: predicting a one-rep max from reps-to-fatigue. *Journal of Physical Education, Recreation & Dance*, 64(1), 88-90.
- Epley, B. (1985). *Poundage chart*. Boyd Epley Workout. Lincoln, NE: Body Enterprises.
- Lombardi, V. P. (1989). *Beginning weight training: the safe and effective way*. Wm. C. Brown.
- Mayhew, J. L., Ball, T. E., Arnold, M. D., & Bowen, J. C. (1992). Relative muscular endurance performance as a predictor of bench press strength in college men and women. *Journal of Applied Sport Science Research*, 6(4), 200-206.
- O'Conner, B., Simmons, J., & O'Shea, P. (1989). *Weight training today*. West Publishing.
- Wathan, D. (1994). Load assignment. En T. R. Baechle (Ed.), *Essentials of strength training and conditioning* (NSCA). Human Kinetics.
- LeSuer, D. A., McCormick, J. H., Mayhew, J. L., Wasserstein, R. L., & Arnold, M. D. (1997). The accuracy of prediction equations for estimating 1-RM performance in the bench press, squat, and deadlift. *Journal of Strength and Conditioning Research*, 11(4), 211-213.

### 6.2 Esfuerzo percibido y repeticiones en reserva

- Zourdos, M. C., Klemp, A., Dolan, C., Quiles, J. M., Schau, K. A., Jo, E., et al. (2016). Novel resistance training-specific rating of perceived exertion scale measuring repetitions in reserve. *Journal of Strength and Conditioning Research*, 30(1), 267-275.
- Helms, E. R., Cronin, J., Storey, A., & Zourdos, M. C. (2016). Application of the repetitions in reserve-based rating of perceived exertion scale for resistance training. *Strength and Conditioning Journal*, 38(4), 42-49.
- Hackett, D. A., Johnson, N. A., Halaki, M., & Chow, C. M. (2012). A novel scale to assess resistance-exercise effort. *Journal of Sports Sciences*, 30(13), 1405-1413.

### 6.3 Volumen, hipertrofia y periodización

- Israetel, M., Hoffmann, J., & Smith, C. W. (2015). *Scientific principles of strength training*. Renaissance Periodization.
- Israetel, M., Davis, M., Case, J., & Hoffmann, J. *The Renaissance diet 2.0*. Renaissance Periodization. **[repo]**
- Helms, E., Valdez, A., & Morgan, A. *The muscle and strength pyramid: training*. **[repo]**
- Schoenfeld, B. J. *Science and development of muscle hypertrophy*. Human Kinetics. **[repo]**
- Rippetoe, M., & Baker, A. *Practical programming for strength training*. The Aasgaard Company. **[repo]**
- Schoenfeld, B. J. (2010). The mechanisms of muscle hypertrophy and their application to resistance training. *Journal of Strength and Conditioning Research*, 24(10), 2857-2872.
- Schoenfeld, B. J., Ogborn, D., & Krieger, J. W. (2017). Dose-response relationship between weekly resistance training volume and increases in muscle mass: a systematic review and meta-analysis. *Journal of Sports Sciences*, 35(11), 1073-1082.
- Baz-Valle, E., Balsalobre-Fernández, C., Alix-Fages, C., & Santos-Concejero, J. (2022). A systematic review of the effects of different resistance training volumes on muscle hypertrophy. *Journal of Human Kinetics*, 81, 199-210.
- Rhea, M. R., Ball, S. D., Phillips, W. T., & Burkett, L. N. (2002). A comparison of linear and daily undulating periodized programs with equated volume and intensity for strength. *Journal of Strength and Conditioning Research*, 16(2), 250-255.
- Issurin, V. B. (2010). New horizons for the methodology and physiology of training periodization. *Sports Medicine*, 40(3), 189-206.

### 6.4 Carga de entrenamiento y riesgo (ACWR)

- Gabbett, T. J. (2016). The training-injury prevention paradox: should athletes be training smarter and harder? *British Journal of Sports Medicine*, 50(5), 273-280.
- Hulin, B. T., Gabbett, T. J., Lawson, D. W., Caputi, P., & Sampson, J. A. (2016). The acute:chronic workload ratio predicts injury: high chronic workload may decrease injury risk in elite rugby league players. *British Journal of Sports Medicine*, 50(4), 231-236.
- Williams, S., West, S., Cross, M. J., & Stokes, K. A. (2017). Better way to determine the acute:chronic workload ratio? *British Journal of Sports Medicine*, 51(3), 209-210.
- Impellizzeri, F. M., Woodcock, S., Coutts, A. J., Fanchini, M., McCall, A., & Vigotsky, A. D. (2020). Acute:chronic workload ratio: conceptual issues and fundamental pitfalls. *International Journal of Sports Physiology and Performance*, 16(6), 907-913.

> **Nota para F3.5.** Incluir la crítica de Impellizzeri no debilita el trabajo: el código ya
> declara el ACWR como «heurística, no oráculo» (`motor_predictivo.py:12`). Documentar esa
> cautela con la literatura que la respalda es más defendible que presentarlo como verdad.

### 6.5 Entrenamiento basado en velocidad

- González-Badillo, J. J., & Sánchez-Medina, L. (2010). Movement velocity as a measure of loading intensity in resistance training. *International Journal of Sports Medicine*, 31(5), 347-352.
- Sánchez-Medina, L., & González-Badillo, J. J. (2011). Velocity loss as an indicator of neuromuscular fatigue during resistance training. *Medicine & Science in Sports & Exercise*, 43(9), 1725-1734.
- Pareja-Blanco, F., Rodríguez-Rosell, D., Sánchez-Medina, L., Sanchis-Moysi, J., Dorado, C., Mora-Custodio, R., et al. (2017). Effects of velocity loss during resistance training on athletic performance, strength gains and muscle adaptations. *Scandinavian Journal of Medicine & Science in Sports*, 27(7), 724-735.
- Weakley, J., Mann, B., Banyard, H., McLaren, S., Scott, T., & Garcia-Ramos, A. (2021). Velocity-based training: from theory to application. *Strength and Conditioning Journal*, 43(2), 31-49.

### 6.6 Modelos de lenguaje e inferencia

- Vaswani, A., Shazeer, N., Parmar, N., Uszkoreit, J., Jones, L., Gomez, A. N., Kaiser, Ł., & Polosukhin, I. (2017). Attention is all you need. *Advances in Neural Information Processing Systems*, 30.
- Brown, T. B., Mann, B., Ryder, N., et al. (2020). Language models are few-shot learners. *Advances in Neural Information Processing Systems*, 33.
- Dettmers, T., Lewis, M., Belkada, Y., & Zettlemoyer, L. (2022). LLM.int8(): 8-bit matrix multiplication for transformers at scale. *Advances in Neural Information Processing Systems*, 35.
- Frantar, E., Ashkboos, S., Hoefler, T., & Alistarh, D. (2023). GPTQ: accurate post-training quantization for generative pre-trained transformers. *International Conference on Learning Representations*.
- Kwon, W., Li, Z., Zhuang, S., et al. (2023). Efficient memory management for large language model serving with PagedAttention. *ACM SOSP*.
- Zheng, L., Yin, L., Xie, Z., et al. (2024). SGLang: efficient execution of structured language model programs. *Advances in Neural Information Processing Systems*.
- Yang, A., et al. (2025). *Qwen technical report*. — **verificar la versión que corresponde al modelo en uso (`qwen3.8:27b`).**

### 6.7 Agentes y llamada a herramientas

- Yao, S., Zhao, J., Yu, D., Du, N., Shafran, I., Narasimhan, K., & Cao, Y. (2023). ReAct: synergizing reasoning and acting in language models. *International Conference on Learning Representations*.
- Schick, T., Dwivedi-Yu, J., Dessì, R., et al. (2023). Toolformer: language models can teach themselves to use tools. *Advances in Neural Information Processing Systems*, 36.
- Qin, Y., Liang, S., Ye, Y., et al. (2024). ToolLLM: facilitating large language models to master 16000+ real-world APIs. *International Conference on Learning Representations*.
- Wei, J., Wang, X., Schuurmans, D., et al. (2022). Chain-of-thought prompting elicits reasoning in large language models. *Advances in Neural Information Processing Systems*, 35.
- Wang, L., Ma, C., Feng, X., et al. (2024). A survey on large language model based autonomous agents. *Frontiers of Computer Science*, 18(6).

### 6.8 Alucinación y su mitigación

- Ji, Z., Lee, N., Frieske, R., et al. (2023). Survey of hallucination in natural language generation. *ACM Computing Surveys*, 55(12), 1-38.
- Huang, L., Yu, W., Ma, W., et al. (2025). A survey on hallucination in large language models: principles, taxonomy, challenges, and open questions. *ACM Transactions on Information Systems*, 43(2).
- Maynez, J., Narayan, S., Bohnet, B., & McDonald, R. (2020). On faithfulness and factuality in abstractive summarization. *ACL*.
- Manakul, P., Liusie, A., & Gales, M. J. F. (2023). SelfCheckGPT: zero-resource black-box hallucination detection for generative large language models. *EMNLP*.
- Farquhar, S., Kossen, J., Kuhn, L., & Gal, Y. (2024). Detecting hallucinations in large language models using semantic entropy. *Nature*, 630, 625-630.

### 6.9 RAG, visión, habla y aprendizaje automático

- Lewis, P., Perez, E., Piktus, A., et al. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. *Advances in Neural Information Processing Systems*, 33.
- Gao, Y., Xiong, Y., Gao, X., et al. (2023). Retrieval-augmented generation for large language models: a survey. *arXiv:2312.10997*.
- Lugaresi, C., Tang, J., Nash, H., et al. (2019). MediaPipe: a framework for building perception pipelines. *arXiv:1906.08172*.
- Bazarevsky, V., Grishchenko, I., Raveendran, K., Zhu, T., Zhang, F., & Grundmann, M. (2020). BlazePose: on-device real-time body pose tracking. *arXiv:2006.10204*.
- Redmon, J., Divvala, S., Girshick, R., & Farhadi, A. (2016). You only look once: unified, real-time object detection. *IEEE CVPR*.
- Jocher, G., Chaurasia, A., & Qiu, J. *Ultralytics YOLO*. — citar la versión exacta usada en `requirements.txt`.
- Radford, A., Kim, J. W., Xu, T., Brockman, G., McLeavey, C., & Sutskever, I. (2023). Robust speech recognition via large-scale weak supervision. *International Conference on Machine Learning*.
- Friedman, J. H. (2001). Greedy function approximation: a gradient boosting machine. *The Annals of Statistics*, 29(5), 1189-1232.
- MacKay, D. J. C. (1992). Bayesian interpolation. *Neural Computation*, 4(3), 415-447.
- Bishop, C. M. (2006). *Pattern recognition and machine learning*. Springer.
- Pedregosa, F., Varoquaux, G., Gramfort, A., et al. (2011). Scikit-learn: machine learning in Python. *Journal of Machine Learning Research*, 12, 2825-2830.

### 6.10 Ingeniería de software para sistemas de aprendizaje automático

- Sculley, D., Holt, G., Golovin, D., et al. (2015). Hidden technical debt in machine learning systems. *Advances in Neural Information Processing Systems*, 28.
- Amershi, S., Begel, A., Bird, C., et al. (2019). Software engineering for machine learning: a case study. *IEEE/ACM ICSE-SEIP*.

### 6.11 Marco normativo

- Cámara de Diputados (2010). *Ley Federal de Protección de Datos Personales en Posesión de los Particulares*. Diario Oficial de la Federación, México.
- Parlamento Europeo y Consejo (2016). *Reglamento (UE) 2016/679 (RGPD)*.
- Parlamento Europeo y Consejo (2024). *Reglamento (UE) 2024/1689 sobre inteligencia artificial*.
- Meta Platforms. *WhatsApp Business Messaging Policy* y *WhatsApp Cloud API documentation*. — citar con fecha de consulta.

---

## 7. Anexo del plan · Mapeo capítulo actual → capítulo destino

| PDF v9.1 | Tesina | Tarea que lo trabaja |
|---|---|---|
| 1. Resumen ejecutivo | Resumen + 1.1 | F2.1, F2.3 |
| 2. Problema y decisión de diseño | 1.2, 1.3, 9.2 | F2.4, F2.5, F9.2 |
| 3. Arquitectura del sistema | 5 | F6.1 |
| 4. Ciclo de vida de un mensaje | 5 | F6.2 |
| 5. El orquestador | 6 | F6.3 |
| 6. Interceptores deterministas | 6 | F6.4 |
| 7. Contrato con el modelo | 6 | F6.5 |
| 8. Redes de validación | 7 | F1.3, F6.6 |
| 9. Motor de planificación | 8 | F3.3, F3.4, F6.7 |
| 10. Motor predictivo | 9 | F3.1, F3.2, F3.5, F6.8 |
| 11. Modelo de datos | 10 | F6.9 |
| 12. Entradas multimodales | 11, 12, 13 | F3.6, F7.1, F7.2, F7.3 |
| 13. Proactividad | 13 | F7.4 |
| 14. Concurrencia y rendimiento | 14 + 16 | F6.10, F8.2 |
| 15. Estrategia de verificación | 15 + 16 | F6.11, F8.3, F8.4 |
| 16. Deuda técnica y hoja de ruta | 19 | F9.3, F9.4 |
| A. Mapa de ficheros | Anexo A | F11.1 |
| B. Catálogo de herramientas | Anexo B | F11.2 |
| — | 2 Marco teórico | F3 (10 tareas) |
| — | 3 Estado del arte | F4 (4 tareas) |
| — | 4 Metodología | F5 (5 tareas) |
| — | 16 Resultados | F8 (7 tareas) |
| — | 18 Ética y marco legal | F10 (4 tareas) |
| — | Anexos C-H | F11.3 a F11.8 |
