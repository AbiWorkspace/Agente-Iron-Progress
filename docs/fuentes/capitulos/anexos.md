# Anexos

> Estado: **hecho (borrador)** · Tareas: F11.1 a F11.8 · Fecha: 2026-08-27

---

## Anexo A · Mapa de ficheros (actualizado desde el PDF v9.1)

El Anexo A del PDF v9.1 ya cubre los ficheros centrales. Esta versión añade los módulos que
esta sesión documentó y que faltaban en la tabla original:

| Fichero | Responsabilidad | Capítulo de esta tesina |
|---|---|---|
| `api/cola_mensajes.py` | Cola FIFO por atleta, fusión de ráfaga de 2 s | Cap. 14 §14.2 |
| `services/Creardataset.py` | Motor VBT: seguimiento de disco, máquina de estados de repetición, estimadores de RIR/e1RM/velocity loss | Cap. 11 |
| `services/vision_service.py` | Carga del clasificador de ejercicios entrenado, MediaPipe Pose | Cap. 12 |
| `Entrenamiento_modelo_media/1_crear_dataset.py` | Extracción de 23 variables de pose por vídeo | Cap. 12 §12.1 |
| `Entrenamiento_modelo_media/2_entrenar_modelo.py` | Torneo de 5 algoritmos, empaquetado del ganador | Cap. 12 §12.3 |
| `services/proactive_coach.py` | Detección de alertas y avisos no solicitados | Cap. 13 |
| `scripts/metricas_documentacion.py` | **Nuevo en esta sesión (F1.2)**: recuento reproducible de cifras del sistema | F1.2 |
| `docs/fuentes/bibliografia.bib` | **Nuevo en esta sesión (F0.2)**: 64 referencias bibliográficas | Todo el marco teórico |
| `docs/fuentes/capitulos/*.md` | **Nuevo en esta sesión (F0.3)**: 21 ficheros de capítulo de esta tesina | Este documento |

## Anexo B · Catálogo completo de las 23 herramientas, con argumentos verificados

Extraído programáticamente de `core/tools.json` en esta sesión (no transcrito de la
documentación previa):

| Herramienta | Argumentos |
|---|---|
| `generar_rutina` | dias, meta, tipo_periodizacion, peso_kg, porcentaje_grasa, rm_banca_kg, rm_sentadilla_kg, rm_peso_muerto_kg, rm_dominadas_kg, nivel, contexto_usuario |
| `evaluar_nivel_fuerza` | peso_kg, genero, rm_banca_kg, rm_sentadilla_kg, rm_peso_muerto_kg |
| `consultar_rutina_futura` | semana, dia |
| `avanzar_dia` | forzar |
| `calcular_rm_exacto` | nombre_ejercicio, tipo_ejercicio, repeticiones, peso_levantado |
| `consultar_libro_cientifico` | pregunta_teorica |
| `consultar_historial_narrativo` | contexto_a_buscar |
| `evaluar_perfil_fisico` | peso_kg, altura_m, porcentaje_grasa, genero |
| `consultar_expediente_personal` | concepto_a_buscar |
| `guardar_en_expediente_personal` | dato_a_guardar |
| `sustituir_ejercicio` | ejercicio_original, ejercicio_nuevo, motivo, alcance |
| `aplicar_descarga` | semana, reduccion_intensidad_pct, reduccion_series |
| `consultar_progreso` | metrica, ejercicio |
| `explicar_tecnica_ejercicio` | ejercicio |
| `registrar_peso_corporal` | peso_kg |
| `pausar_plan` | motivo |
| `reanudar_plan` | (sin argumentos) |
| `registrar_serie_completada` | ejercicio, peso_kg, repeticiones, rpe, dolor_articular |
| `predecir_carga_optima` | ejercicio, repeticiones_objetivo, rpe_objetivo |
| `analizar_mrv` | grupo_muscular, ventana_dias |
| `autorregular_dia` | ejercicio, rpe_reportado, rpe_objetivo, series_restantes, carga_actual_kg |
| `enviar_grafica_progreso` | ejercicio, periodo_dias, caption |
| `consultar_volumen_plan` | semana, grupo_muscular |

## Anexo C · Esquema de datos completo

Ver Cap. 10 §10.1 para el DDL SQL completo y verificado de las 7 tablas (extraído en vivo con
`sqlite3` en esta sesión). El esquema JSON de planes y estado sigue la estructura documentada
en el PDF v9.1 (Cap. 11): `resources/rutinas_generadas/<numero>/{1_acumulamiento,2_fuerza,3_realizacion}.json`
para el plan (porcentajes de 1RM, no kilos — Cap. 8 §8.3), y `data/estado_usuarios_<numero>.json`
para el cursor de semana/día, el estado de onboarding y las anulaciones activas (descargas,
sustituciones).

## Anexo D · Prompt del sistema íntegro

### Las 13 secciones (Cap. 6 §6.3, F1.4)

identidad · capacidades · secreto_profesional · fuente_de_verdad · telemetria_vigente ·
herramientas · criterio_ante_dudas · honestidad_con_resultados · sentido_comun_numerico ·
salud_del_atleta (cita textual completa en Cap. 18 §18.2) · creacion_de_plan · memoria_viva ·
presupuesto_de_razonamiento (cita textual completa en F1.4)

### Las 17 reglas de decisión de la sección `<herramientas>` (extraídas del prompt real)

1. Reporta una serie que acaba de hacer → `registrar_serie_completada`
2. Da peso + repeticiones fuera de creación de plan (PR, marca, duda) → `calcular_rm_exacto` — **prohibido que el modelo aplique Epley/Brzycki o multiplique porcentajes por su cuenta**
3. Pide ver su rutina, de hoy, de una semana o un día → `consultar_rutina_futura`
4. Terminó la sesión o pide avanzar/saltar el día → `avanzar_dia`
5. Menciona lesiones, molestias, equipo, hábitos, sueño, horarios, gustos, nutrición o cambios de entorno → `consultar_expediente_personal` / `guardar_en_expediente_personal`
6. Pregunta de teoría o se le nota estancado/fatigado/perdiendo velocidad → `consultar_libro_cientifico`
7. Referencias a su pasado lejano (mesociclos anteriores, lesiones viejas) → `consultar_historial_narrativo`
8. Composición corporal o "¿qué tan fuerte soy para mi peso?" → `evaluar_perfil_fisico` / `evaluar_nivel_fuerza`
9. Quiere cambiar un ejercicio del plan → `sustituir_ejercicio`
10. RPE ≥ 9,5 o se siente fatal en su primera serie del día → `autorregular_dia`
11. Se siente molido, varios RPE altos seguidos, pregunta si debe descargar, o cierra microciclo → `analizar_mrv` **primero**, nunca descargar a ciegas
12. Pregunta por volumen semanal ("¿cuántas series de pecho hago?") → `consultar_volumen_plan`
13. Pregunta por su evolución ("¿cómo va mi banca?") → `consultar_progreso`
14. Pregunta cómo se ejecuta un ejercicio o cómo mejorar su técnica → `explicar_tecnica_ejercicio`
15. Reporta su peso corporal actual → `registrar_peso_corporal`
16. Se va de viaje, se enferma o necesita parar sin perder el plan → `pausar_plan` / `reanudar_plan`
17. Pregunta con cuánto peso entrarle hoy o se le va a proponer la carga de su serie top → `predecir_carga_optima`

*(Texto resumido para el anexo; el texto íntegro y literal de cada regla, con sus matices y
excepciones, vive en `core/prompt.json` y se cita textualmente en el cuerpo de la tesina
donde es relevante para el argumento — p. ej. la regla 4 y su manejo del "CANDADO" honesto,
Cap. 6.)*

## Anexo E · Manual de instalación y puesta en marcha

Derivado del `README.md` del repositorio, sección "Puesta en marcha" — **no verificado paso a
paso en una máquina limpia durante esta sesión** (el entorno de esta sesión ya tenía el
sistema instalado y Ollama corriendo con los modelos necesarios). Se reproduce la secuencia
documentada, marcada como pendiente de verificación end-to-end en un entorno nuevo:

```bash
# 1. Entorno
python -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/rocm6.2
pip install -r requirements.txt

# 2. Modelos
ollama pull qwen3.6:27b   # el modelo realmente servido en este entorno es qwen3.8:27b — ver F1.1
ollama pull nomic-embed-text

# 3. Credenciales
cp .env.example .env

# 4. Biblioteca científica (NO opcional)
python scripts/crear_base_conocimiento.py   # ahora dedupe por hash — ver F1.5

# 5. Arrancar
python main.py
```

**Nota de esta sesión:** el paso 2 del README todavía dice `qwen3.6:27b`; el modelo realmente
en uso es `qwen3.8:27b` (F1.1). Es una inconsistencia menor del manual de instalación que se
señala aquí y que convendría corregir en el README directamente, fuera del alcance de este
plan de documentación.

## Anexo F · Banco de pruebas (74 casos)

El banco completo vive en `services/testbed/qa_casos_uso.py`, organizado en 22 bloques
temáticos (confirmado programáticamente en F1.2). No se transcriben aquí los 74 casos
completos por extensión; el Cap. 16 §16.3 y §16.4 documentan una ejecución real de una muestra
(el bloque "fatiga") con su resultado íntegro, y el fichero
`services/testbed/qa/qa_fatiga_2137.json` generado en esta sesión queda como evidencia
reproducible adjunta al repositorio.

## Anexo G · Dataset del clasificador de ejercicios

Ver Cap. 12 §12.1-§12.2 para el detalle completo: 49 vídeos, 4 clases (Press de banca 19,
Sentadilla 12, Peso muerto 11, Dominadas 7 — conteo real de esta sesión), 23 variables de pose
extraídas por vídeo con MediaPipe. Protocolo de grabación: no documentado explícitamente en el
código más allá de "un subdirectorio por ejercicio"; se recomienda como tarea pendiente
documentar las condiciones de grabación (ángulo de cámara, iluminación, encuadre) si se
pretende que el dataset sea reproducible por terceros.

## Anexo H · Reproducibilidad

```bash
# 1. Atleta con 16 semanas de historial creíble (~15 s)
python3 scripts/sembrar_demo.py --limpiar

# 2. Conversación previa y bitácora de mesociclos cerrados (~3 s)
python3 scripts/sembrar_conversacion.py

# 3. Pasar lista a 21 comprobaciones (~20 s)
python3 scripts/verificar_demo.py

# 4. Recontar las cifras del sistema en vivo (nuevo en esta sesión, F1.2)
python3 scripts/metricas_documentacion.py --smoke

# 5. Reindexar la biblioteca científica si hace falta (nuevo en esta sesión, F1.5)
python3 scripts/crear_base_conocimiento.py
```

Todas las cifras del Cap. 16 (Resultados) marcadas **[FRESCA]** se reprodujeron exactamente con
estos comandos durante esta sesión de documentación (27 de agosto de 2026) y deben poder
reproducirse de nuevo con el mismo procedimiento.
