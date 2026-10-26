# F1.2 — Cifras del sistema, recontadas y verificadas en vivo

> Estado: **hecho** · Fecha: 2026-08-27 · Commit: `1ec5c89`
> Entregable: `scripts/metricas_documentacion.py` (ejecutable, reproducible).
> Ejecución: `python3 scripts/metricas_documentacion.py --smoke`

## Tabla "Cifras del sistema", con procedencia de cada valor

| Métrica | PDF v9.1 | **Verificado ahora** | Método |
|---|---|---|---|
| Líneas de Python (total) | ≈28.500 | **29.106** (antes de esta sesión) | `wc -l` sobre todos los `.py`, excluyendo `.git/`, `torch/`, `__pycache__/` |
| Orquestador (`llm_service.py`) | 7.749 | **7.792** | idem, un solo fichero |
| Herramientas expuestas al modelo | 23 | **23** ✓ | `len(json.load(open('core/tools.json')))` |
| Reglas de decisión del prompt | 17 | **17** ✓ | Conteo de líneas numeradas dentro de `<herramientas>` en `core/prompt.json` |
| Secciones del prompt | 12 | **13** (ver F1.4) | `re.findall(r"<([a-z_]+)>", prompt)` |
| Suites deterministas | 32 | **32** ✓ | `len(glob('services/testbed/test_*.py'))` |
| Banco de casos de uso | 74 casos, 22 bloques | **74 / 22** ✓ | Import real de `qa_casos_uso.py`, `len(BLOQUES)` y suma de casos por bloque |
| Aserciones de humo (`smoke_v8.py`) | 72 | **72 OK, 0 FALLAS** ✓ | **Ejecución real** del módulo — ver nota abajo |
| Tablas relacionales | 7 en dos bases SQLite | **7** ✓ (3 + 4) | `SELECT name FROM sqlite_master WHERE type='table'` sobre ambas `.db` |
| Colecciones vectoriales | 3 en ChromaDB | **3** ✓ | `chromadb.PersistentClient(...).list_collections()` |
| Fragmentos de literatura indexada | 3.852 de 1.007 páginas | **3.811 de 997 páginas únicas** | Ejecución real de `crear_base_conocimiento.py` tras deduplicar (F1.5) |

## Notas metodológicas que vale la pena que la tesina herede (§4.3 Gestión de la evidencia)

1. **Un contador ingenuo por subcadena falla.** La primera versión de este script excluía
   rutas que contuvieran la subcadena `"data"`, y descartaba por error
   `services/Creardataset.py` y `Entrenamiento_modelo_media/1_crear_dataset.py` — ambos
   contienen la subcadena `"data"` sin estar bajo el directorio `data/`. El error se detectó
   al reconciliar contra un segundo método (`find | xargs wc -l`) y se corrigió comparando
   por **componente completo de ruta**, no por subcadena. Es la misma clase de error —
   coincidencia parcial de texto en vez de coincidencia estructural— que el propio proyecto
   combate en sus redes de validación (F1.3).
2. **Contar líneas de código estático no basta cuando el patrón es un helper en bucle.**
   `services/smoke_v8.py` tiene solo 33 líneas con la llamada `chk(...)`, pero produce
   **72** verificaciones reales al ejecutarse, porque varias llamadas iteran sobre listas de
   casos. El número correcto solo se obtiene **ejecutando** el módulo y leyendo su propio
   resumen (`RESULTADO: 72 OK, 0 FALLAS`), no leyendo el fichero fuente.
3. **El total de líneas depende del instante de medición.** Los 29.106 de esta tabla son
   anteriores a esta misma sesión de documentación (que añadió `metricas_documentacion.py` y
   amplió `crear_base_conocimiento.py`). Cualquier cifra de "líneas totales" en la tesina
   debe llevar fecha o número de commit, nunca presentarse como constante — exactamente el
   error que tenía el PDF v9.1, fechado el 31 de julio de 2026 pero citado después sin
   volver a medir.

## Redacción para la tesina (§4.3 Gestión de la evidencia, y tabla de Resumen ejecutivo)

> Todas las cifras cuantitativas de este documento proceden de `scripts/metricas_documentacion.py`,
> un script que las deriva del propio repositorio en el momento de ejecutarse — commit
> `1ec5c89`, 2026-08-27 — en vez de transcribirlas a mano. Dos de sus resultados exigieron
> corregir el propio método de medición durante su desarrollo (véase nota 1 y 2 arriba), lo
> que se documenta aquí como evidencia de que la cifra final fue verificada por reconciliación
> cruzada, no aceptada en la primera pasada.
