# Plan de latencia del coach — estado

Última sesión: **2026-07-28**. Equipo: RX 7900 XTX 24 GB, `qwen3.6:27b` (27.3B denso,
Q4_K_M) vía Ollama 0.32.1, 15 GB de RAM.

---

## Diagnóstico base (medido, no estimado)

| Dato | Valor |
|---|---|
| Decode | 31–33 tok/s (techo teórico ~63; ~52 % de eficiencia por los kernels recurrentes en ROCm) |
| Prefill | 760–900 tok/s |
| System prompt (`core/prompt.json`) | 3.075 tok |
| Tools (`core/tools.json`, 22 herramientas) | 6.299 tok |
| **Fijo por llamada, antes de conversación** | **9.374 tok** |

**El modelo es híbrido recurrente** (`llama_memory_recurrent: RS buffer 149,62 MiB`;
solo 16 de sus 64 capas llevan KV). Esto gobierna todo: el estado NO se restaura token a
token como un KV cache normal, **solo desde checkpoints completos de 149,6 MiB**.

---

## FASE 1 — Config de Ollama ✅ APLICADA

Archivo: `/etc/systemd/system/ollama.service.d/perf.conf` (copia en este directorio:
`perf.conf`). Backups con fecha en la misma carpeta del sistema.

El default `--checkpoint-min-step` es 8192 tokens, así que solo existía **un** checkpoint
por prompt, clavado en la posición 8518. El log lo mostraba crudo:

```
checking checkpoint with [8518, 8518] against 15459...   ← prefijo válido hasta 15.459
prompt eval time = 8930.96 ms / 6944 tokens              ← reprocesa 6.944 igualmente
```

| Métrica | Antes | Después |
|---|---|---|
| Tokens reprocesados por llamada | 6.944 | **1.026** (−85 %) |
| Reprefill | 8,93 s | **1,4–1,7 s** |
| Decode (por KV f16) | 31,1–31,4 tok/s | **32,7–33,1 tok/s** |

**No aplicó:** `LLAMA_ARG_BATCH` / `LLAMA_ARG_UBATCH`. Ollama pasa `-b 1024 -ub 1024`
en argv y argv gana sobre la variable de entorno. Quedan inertes, no molestan.

**Rollback:** `sudo cp perf.conf.bak-<fecha> perf.conf && sudo systemctl restart ollama`

---

## FASE 2.1 — `num_ctx` 16384 → 32768 ✅ APLICADA

Tocado en **dos sitios que deben ir siempre iguales** (si difieren, Ollama recarga el
modelo entero al alternar, ~40 s de thrashing):
- `services/llm_service.py` → `OPCIONES_QWEN_32B["num_ctx"]`
- `scripts/resources/Entrenamiento_llm_peticion.py:197`

Arregla dos cosas a la vez:

**Calidad** — el prompt llegaba a 15.234 tok de 16.384, dejando 1.150 para generar. Con
thinking el razonamiento se los comía y la respuesta salía **vacía**. Esa era la causa
real del "bug 3.1", no el `num_predict` corto. Y Ollama marcaba `truncated = 1`: con
`n_keep = 4` se comía trozos del system prompt en silencio.

| | Antes | Después |
|---|---|---|
| `done_reason` | `length` | **`stop`** |
| `tool_calls` emitidas | 0 | **3** |
| `truncated` en el log | **1** | **0** |

**Latencia** — la caché de prompts está topada al tamaño del contexto, así que su techo
subió de 16.384 a 32.768 tokens y por fin **caben 2 prompts**. Antes, cualquier llamada
intercalada (el clasificador de intención, un `_forzar_respuesta_texto`) desalojaba al
principal y el siguiente turno pagaba reprefill completo (~16 s).

Turno completo de 4 llamadas incluida una intercalada: **127,3 s → 17,2 s**.

Coste en VRAM: KV de 1.024 → 2.048 MiB. Total 20,17 GB de 25,75. Sobra.

---

## FASE 3.1 — Thinking selectivo de verdad ✅ APLICADA

**El thinking NO se puede quitar.** Medido sobre 8 intenciones reales
(`bench_calidad.py`, salida en `resultado_calidad.txt`):

| Modo | Aciertos de tool call | Media |
|---|---|---|
| `think=True` | **6/8** | 48,8 s |
| `think="low"` | **6/8** | 63,4 s ← más lento, descartado |
| `think=False` | **3/8** | 16,3 s |

Falla justo en las inferencias: *"ya terminé mi sesión"* → `avanzar_dia`, *"cambia el peso
muerto por rumano"* → `sustituir_ejercicio`.

Dato colateral: cuando el modelo razona **>2.500 tokens, falla**. Los aciertos salen con
73–1.653 tok. Razonar de más no ayuda, despista.

Lo que sí sobraba: el `pensar_turno` de v5 usaba `tools_de_turno is not None`, cierto en
casi todos los turnos reales. Se razonaba incluso cuando Python ya había ejecutado la
acción e inyectado `[SISTEMA — SERIE YA REGISTRADA]`. Eso es redacción, no decisión.

Ahora (`llm_service.py`, constante `PENSAR_AUNQUE_PYTHON_RESUELVA` para A/B):

```python
solo_redaccion = (not PENSAR_AUNQUE_PYTHON_RESUELVA
                  and (accion_ejecutada or bool(consulta_rutina))
                  and not FIN_SESION_RE.search(texto_puro))
```

La guarda de `FIN_SESION_RE` es deliberada: ahí, aunque Python haya registrado la serie,
el modelo todavía debe decidir si llama `avanzar_dia`, y sin razonar falla ese caso.

**Turno de solo redacción: 80,1 s → 15,6 s (−64,5 s, −81 %)**, con respuesta igual de
buena o mejor.

---

## VERIFICACIÓN EN PRODUCCIÓN — 2026-07-28 12:35-12:44 ✅ HECHA (y cambia el diagnóstico)

Cuatro turnos reales de WhatsApp, log en `/tmp/iron.log` + `journalctl -u ollama`.

| Hora | Mensaje | Think | Prefill | Decode | Total |
|---|---|---|---|---|---|
| 12:35 | "tenía mucho más margen" | no | 5,3 s | 18,3 s (604 tok) | 32,5 s (+8,2 s compresor) |
| 12:41 | "mi máxima marca es 205×8" | **sí** | 25,6 s | 40,5 s (1304 tok) | **67,1 s** |
| 12:43 | "a peso muerto 205×8" | no | 25,9 s | 3,5 s (113 tok) | 29,7 s |

**Lo que se confirma:** el thinking selectivo (Fase 3.1) dispara bien en real — se vio
`desactivado (solo redacción)`, `desactivado (Python ya resolvió)` y `activado este turno`
en los casos correctos. Decode estable en 32,2-33,2 tok/s. `truncated = 0` siempre.

**Lo que NO se sostiene: la caché de prompts no acierta ni una vez.**

```
task 1883 | checking checkpoint with [22221, 22221] against 19009...
task 3228 | checking checkpoint with [19847, 19847] against 19787...
task 3383 | checking checkpoint with [20125, 20125] against 20063...
```

El checkpoint disponible queda **siempre por encima del prefijo válido**, así que es
inservible y se reprocesa el prompt entero: `prompt eval time = 25881 ms / 20638 tokens`.

El desfase es de **60 y 62 tokens** en dos turnos consecutivos. Demasiado consistente para
ser azar: el checkpoint se graba al final del turno anterior y el prompt siguiente diverge
~60 tokens *antes* de ese punto. Algo reescribe la cola del prompt entre turnos → es
exactamente el síntoma de la **Fase 2.3 (telemetría podada que sustituye a la memoria
viva)**. Sube de prioridad "media" a **la que más cuesta: ~25 s por turno**.

**Correcciones al diagnóstico base:**

1. **"El decode manda" ya no vale.** En el turno de 12:43 el prefill fue el **88 %** del
   tiempo (25,9 s de 29,7 s). El reparto se invirtió.
2. **La Fase 1 no rinde en real.** Los 1,4-1,7 s eran ciertos en benchmark, donde el
   prefijo no divergía. Con divergencia real se vuelve a ~26 s.
3. **La Fase 2.1 se quedó corta:** el prompt real va por **20.638-23.072 tok**, no los
   13-15k que asumía el plan. Con `num_ctx` 32768 vuelve a caber solo uno.
4. **El compresor de historial cuesta 8,2 s** y no está en ninguna fase. Todos los
   benchmarks midieron la llamada `principal` aislada; en real hay una previa que además
   ocupa el slot en medio.
5. **`[LLM-PERF]` miente en el `tok/s` de prefill.** Reportó "12822 tok en 5,3 s
   (2418 tok/s)" cuando Ollama solo procesó 4.118 en ese tiempo: divide tokens totales
   entre el tiempo de los reprocesados. Siempre se verá mejor de lo real. Arreglarlo antes
   de tomar decisiones con ese número.

### Causa raíz CONFIRMADA — 2026-07-28 13:15 (volcado de prompts)

Instrumentado `chat_llm` con `_volcar_huella_prompt()` (sha1 por mensaje, activo solo
con `IRON_DEBUG_PROMPT=1`) y comparado con `diff_prompts.py`. Cuatro turnos reales:

| Par de turnos | Prefijo idéntico | Primer mensaje que cambia |
|---|---|---|
| 12:53 → 13:01 | 25/26 msgs (**96,0 %**) | idx 25, rol `user`: 1600 → **374** chars |
| 13:01 → 13:05 | 27/28 msgs (**95,1 %**) | idx 27, rol `user`: 2090 → **864** chars |
| 13:05 → 13:09 (con compresor) | 1/30 msgs (**27,7 %**) | idx 1 — el compresor reescribe todo |

**Es la poda de telemetría.** El mensaje se envía CON el bloque `--- TELEMETRÍA ---` y se
persiste SIN él, así que el turno siguiente reconstruye el prompt desde la versión podada:

```
cola ANTES:    📉 2026-07-28 | press banco plano: 105.0kg x 11 | RPE N/A
               --- FIN TELEMETRÍA ---
cola DESPUÉS:  - El atleta sustituyó 'sentadilla hack' por 'prensa de piernas'.
               --- FIN DEL CONTEXTO ---
```

`Mensajes posteriores que también cambian: 0` → es solo esa poda, nada más se reescribe.

**Contraprueba en el mismo log:** sin divergencia la caché funciona perfectamente.

```
[LLM-PERF] 'post-tools-1' prefill 21260 tok en 1.3s (15882 tok/s)   ← continuación inmediata
[LLM-PERF] 'principal'    prefill 19840 tok en 24.7s (803 tok/s)    ← turno nuevo
```

Mismo tamaño de prompt: **1,3 s vs 24,7 s**. El mecanismo de checkpoints está sano.

**Segundo problema, independiente:** la divergencia está en el 96 % del prompt, así que
reprocesar desde ahí serían ~800 tok, no 20.638. No hay **ningún checkpoint por debajo**
del punto de divergencia (el único está en 19.847, 60 tok *por encima*). Con
`CHECKPOINT_MIN_SPACING_NT=2048` debería existir una escalera (2048, 4096… 18432) y no
está. Arreglar la Fase 2.3 quita la divergencia; revisar los checkpoints es el colchón
para las divergencias legítimas e inevitables (compresión, edición de historial).

**Tercer hallazgo: el compresor es más caro de lo apuntado.** No cuesta 7,9 s: cuesta
7,9 s **más invalidar el 72 % de la caché** del turno siguiente.

---

## FASE 2.3 — Telemetría efímera ❌ APLICADA Y NO FUNCIONÓ (2026-07-28 13:45)

Implementada tal como la especificaba el plan ("mandarla como mensaje aparte que se
descarta"): `_con_telemetria()` + `msgs_llm()` en `llm_service.py`. El código hace lo que
debe — los volcados confirman que el último mensaje es solo el bloque de telemetría y que
el historial persistido ya es idéntico a lo enviado. **Pero no mejora la latencia.**

| | Tokens | Prefill | tok/s |
|---|---|---|---|
| Antes (3 turnos) | 19.725-20.412 | 24,6-25,7 s | 796-803 |
| Después (3 turnos) | 14.439-15.271 | 17,3-18,3 s | 833-841 |

Los ~833 tok/s son la velocidad de procesar **desde cero**: `prompt eval time =
17320 ms / 14439 tokens` sobre un prompt de 14.439 → el 100 %, igual que antes. La bajada
de 25 s a 17 s es del compresor, que achicó el prompt a las 13:32; normalizado por token
la mejora es **cero**.

`diff_prompts.py` explica por qué: el prefijo pasó de 96,0 % a **95,6 %**. El mensaje de
telemetría del turno N ocupa la posición que en el N+1 ocupa la respuesta del assistant.

```
*** PRIMER MENSAJE QUE CAMBIA: índice 24, rol 'user' ***
    cabeza ANTES:   --- TELEMETRÍA --- FECHA: 2026-07-28 13:32:39
    cabeza DESPUÉS: ¡Quieto ahí, bro! 🛑 Revisa el calendario...
```

**El principio que se nos escapó:** cualquier contenido efímero al final del prompt rompe
el prefijo, vaya concatenado o como mensaje suelto. Si el turno N termina con algo que el
N+1 no tiene en esa posición, el estado a partir de ahí es inválido. Moverlo de sitio no
cambia nada. **No volver a proponer variantes de "dónde colocar la telemetría": todas
fallan por la misma razón.**

Solo hay dos salidas reales:

- **Opción B — no podar: la telemetría se queda en el historial.** Entonces el prompt N
  sí es prefijo exacto del N+1 y la caché acierta al 100 %. Es la única vía a prefijo
  perfecto. Coste: ~600 tok/turno acumulados y el modelo ve calendarios viejos (habría que
  decirle en el system prompt que solo vale el último bloque). Es un trade-off de CALIDAD,
  que es justo lo que la poda evitaba: decisión del usuario, no técnica.
- **Opción C — arreglar la escalera de checkpoints.** No evita la divergencia; la abarata.
  Con un checkpoint en ~12.000, reprocesar desde el punto de divergencia serían ~2.800 tok
  (≈3,4 s) en vez de 14.793 (17,6 s). No toca la calidad y además cubre la divergencia
  inevitable del compresor (72 %). Incierta: falta saber por qué con `CTX_CHECKPOINTS=8` y
  `MIN_SPACING_NT=2048` solo aparece UN checkpoint, siempre ~96 tok por encima del punto
  de divergencia.

**El cambio NO se revirtió:** no mejora la latencia pero tampoco la empeora, deja el
historial persistido idéntico a lo enviado y es el paso previo exacto a la opción B
(que consiste en dejar de separar la telemetría). Calidad verificada en 3 turnos: el
modelo siguió usando bien el calendario.

---

## OPCIÓN C — Escalera de checkpoints ❌ NO ES ALCANZABLE (2026-07-28 14:00)

Investigado en `journalctl -u ollama` + `llama-server --help`. **La config está bien
puesta y activa**: `context checkpoints enabled, max = 8, min spacing = 2048`.

**llama.cpp crea UN solo checkpoint por petición, al terminar de procesar el prompt.
No crea intermedios durante el prefill.** Prueba: la task 643 procesó **22.734 tokens** y
creó `context checkpoint 1 of 8` — con espaciado 2048 habría creado ocho.

Histograma de 8 h: `21 × "1 of 8"`, `1 × "2 of 8"`. 22 creados, 20 borrados.

Y ese único checkpoint se graba **después** del bloque efímero del final:

```
posición del checkpoint → final del prompt (después de la telemetría)
punto de divergencia    → donde empieza la telemetría
                          ⇒ el checkpoint SIEMPRE queda por encima ⇒ inválido ⇒ borrado
```

**Por eso C depende de B y no al revés.** Bajar `--checkpoint-min-step` no crea
checkpoints intermedios en esta versión; lo único que haría útil al checkpoint existente
es que dejara de haber contenido efímero al final. **No perder más tiempo tuneando
checkpoints mientras la telemetría siga fuera del historial.**

**Sub-palanca real dentro de C (independiente, pero menor):**

```
prompt 0x3f324bc0: 23072 tokens, checkpoints: 1, 1741.693 MiB
```

Cada prompt cacheado ocupa **1.741 MiB** y `LLAMA_ARG_CACHE_RAM` está en 3072 → caben
1,7 prompts, así que cualquier intercalada (compresor, fallback) desaloja al principal.
Además `--ctx-checkpoints` está en 8 cuando el **default es 32**: la Fase 1 lo bajó sin
ganancia. RAM disponible al medir: 4,0 GB de 15,8 (zram con 2 GB). Subir a 5120 MiB es
viable pero ajustado, y solo ayuda a las intercaladas.

**La prueba de que B SÍ funcionará:** las llamadas `post-tools` son el caso análogo
—el prompt anterior es prefijo exacto del nuevo— y ahí el checkpoint se restaura
(`restored context checkpoint`, 11 veces) con **prefill de 1,3 s sobre 21.260 tokens**.
Con B, reprocesar sería solo la respuesta del assistant + mensaje nuevo + telemetría
(~600-3.000 tok = 0,7-3,6 s a 830 tok/s) en vez de 14.793 tok = 17,6 s.

---

## OPCIÓN B — No podar la telemetría ✅ APLICADA Y FUNCIONA (2026-07-28 14:15)

Dos cambios:
1. `llm_service.py` — se revirtió la Fase 2.3 (la telemetría vuelve a concatenarse al
   mensaje del atleta) y **se eliminó la poda al persistir**: el disco guarda exactamente
   lo que se envió.
2. `core/prompt.json` — nueva sección `<telemetria_vigente>` (+637 chars) tras
   `<fuente_de_verdad>`: solo vale el ÚLTIMO bloque, los anteriores son históricos, y ante
   contradicción gana el más reciente sin comentárselo al atleta.

**Prefijo (`diff_prompts.py`), dos pares consecutivos:**

```
140527 -> 141029 : Prefijo idéntico: 24/24 mensajes = 99,9%
141029 -> 141232 : Prefijo idéntico: 26/26 mensajes = 99,9%
  -> El prefijo entero sobrevive; los mensajes viejos NO se reescriben.
```

**Latencia:**

| Turno | Reprocesado | Prefill | Turno completo |
|---|---|---|---|
| Antes (3 turnos) | 14.439-15.271 tok (**100 %**) | 17,3-18,3 s | 61-99 s |
| 14:12 | **902** tok de 9.305 | **1,2 s** | **8,7 s** |
| 14:13 | **2.476** tok de 16.572 | **3,5 s** | 55,9 s (decode con thinking) |

**Ganancia: 14-16 s por turno.** Y en el log de Ollama, por fin:

```
restored context checkpoint (pos_min = 8402, n_past = 8403)   ← restaura, ya no borra
created context checkpoint 2 of 8                              ← primera vez que se acumulan
```

Calidad verificada en 3 turnos: respuestas correctas y actuales, sin citar calendarios ni
series caducadas.

**CASO RESIDUAL — turnos que siguen a uno con tool calls.** El turno de las 14:10 pagó
10,1 s. Los mensajes del orquestador (`assistant` con `tool_calls` + el `[TOOL-RESULT]`)
se mandan al modelo pero NO se persisten, así que el checkpoint se crea sobre un prompt
que el turno siguiente no reproduce — el mismo patrón, aplicado a las herramientas:
`checking checkpoint with [8322] against 3186`. Ese `3186` tan bajo apunta también a
`CACHE_RAM=3072` con 1.741 MiB por prompt: solo cabe uno y las intercaladas lo desalojan.

---

## VERIFICACIÓN DE CALIDAD DE LA OPCIÓN B ✅ (2026-07-28 15:30)

`bench_calidad.py` con el system prompt nuevo dio **think=True 5/8** frente al 6/8 de la
línea base (`resultado_calidad.txt`). Caso por caso, **7 de 8 dan el mismo veredicto**; el
único que cambió fue *"que me toca entrenar manana?"* (OK con 421 tok de razonamiento →
NO con 2853). Como es justo el caso que trata de calendario, se sospechó del bloque
`<telemetria_vigente>` y se aisló con repeticiones.

**A/B del bloque `<telemetria_vigente>`** (`bench_ab_telemetria_vigente.py`, 5 tiradas):

| | "que me toca entrenar manana?" | Control: "pausa mi plan" |
|---|---|---|
| **CON** bloque | **2/5** (think medio 1.743 tok) | 5/5 (124 tok) |
| **SIN** bloque | **0/5** (think medio 2.201 tok) | 5/5 (106 tok) |

**El bloque no degrada: sin él ese caso falla las 5 veces.** Lo que ocurre es que el caso
es **inherentemente inestable** (~2/5 con think=True). El 6/8 de la línea base tuvo suerte
ahí; el 5/8 de hoy no. El control 5/5 en ambas confirma que no afecta a lo demás.

**A/B del historial con/sin telemetría acumulada** (`bench_calidad_hist.py`, mismo
historial real, única diferencia la poda):

| Condición | Ventana de 10 msgs | Aciertos |
|---|---|---|
| Con telemetría | 11.235 chars, 5 bloques | **5/8** |
| Podado | 6.217 chars, 0 bloques | **5/8** |

Idéntico **caso por caso**: mismos 5 aciertos, mismos 3 fallos. La telemetría acumulada
no degrada el uso de herramientas ni con un 80 % más de texto en la ventana.

**En producción (6 turnos reales):** los 2 turnos con herramientas disponibles las usaron
bien (`consultar_libro_cientifico`, `evaluar_nivel_fuerza`); guardias en tasa comparable
(1 KG-INVENTADOS antes / 1 después, 1 CLAIM-FALSO antes / 0 después); `done_reason=stop`
en todos; y ninguna respuesta citó telemetría caducada.

**Conclusión: la opción B no costó calidad.**

### Puntos débiles REALES que estas corridas destaparon (preexistentes, no regresiones)

- ***"que me toca entrenar manana?"* acierta solo ~2/5.** Es una de las preguntas más
  comunes de un atleta real. La línea base lo ocultaba por medirse una sola vez.
- ***"me siento fatal, rpe 10"*** (→ `autorregular_dia`/`aplicar_descarga`) y
  ***"hazme una rutina nueva desde cero"*** (→ `generar_rutina`) fallan en **todas** las
  corridas, línea base incluida. Cero aciertos en cualquier modo.
- **Patrón confirmado:** los aciertos razonan 58-1.409 tok; los fallos, 1.933-2.972.
  Razonar de más sigue siendo el predictor de fallo.
- **El modelo dice "domingo 30" y el 2026-07-30 es jueves.** La telemetría solo inyecta la
  fecha ISO y el modelo deduce mal el día de la semana. Arreglo trivial: dar el nombre del
  día ya hecho en `construir_telemetria()`.
- **El clasificador `es_casual` quita las herramientas a preguntas legítimas:** marcó
  *"¿cuál es mi nivel como usuario de pesas?"* como casual → sin tools. Al reformular el
  atleta, casual=False y el modelo llamó `evaluar_nivel_fuerza` al instante. Falla el
  clasificador, no el modelo.

---

## ARREGLOS DE CALIDAD v11 ✅ (2026-07-28 16:30)

Seis arreglos. Los deterministas se verificaron con lógica; los que dependen del modelo,
repitiendo 5 veces (una tirada no distingue señal de ruido con `temperature 0.6`).

| # | Error | Arreglo | Verificación |
|---|---|---|---|
| 1 | Decía "domingo 30" y el 2026-07-30 es **jueves** | `fecha_humana()` da el día ya calculado en los 7 puntos donde una fecha viaja al modelo (telemetría, los 3 `estatus`, `avanzar_dia`) | `2026-07-30 → (jueves)` ✓ |
| 2 | `es_casual` quitaba las tools a preguntas legítimas | +25 palabras al set (`pesas`, `nivel`, `ffmi`, `grasa`, `progreso`…) | "cual es mi nivel…" y "Pero y mi ffmi" ya activan tools; "Hola" y "que tal el clima" siguen casual ✓ |
| 3 | El refuerzo de emisión nativa **nunca llegaba al modelo** | El `+=` iba DESPUÉS del `append` al historial; como las cadenas son inmutables, el dict guardaba la versión vieja. Ahora se compone antes | revisión de código ✓ |
| 4 | El coach **inventaba** "hubo un error técnico en el guardado" | `_instruccion_fallback` ahora dice explícitamente que NO hubo error y prohíbe inventarlo; si falta un dato, que lo pida | ver abajo |
| 5 | `autorregular_dia` **no se llamaba jamás** | Aparecía **0 veces** en el system prompt: sin regla en la guía, el modelo no la encontraba entre 22 tools. Añadida la regla 10 | **0/5 → 3/5** ✓ |
| 6 | "qué me toca **mañana**" fallaba | La regla 3 decía "si ya viene el bloque, no llames nada" y la telemetría SIEMPRE trae `RUTINA DEL DÍA`… la de HOY. Precisada: otro día ⇒ `consultar_rutina_futura` obligatoria | **1/5 → 4/5** ✓ |

### El "bug de registro de series" NO era un bug de guardado

El log lo desmiente:

```
13:01:01  serie detectada {'peso': 205.0, 'reps': 8}, THINK activado
          respondió en 121.7s ... done_reason=length, thinking=sí, 10428 chars
          TRUNCADO por presupuesto (contenido=0 chars); reintento sin thinking
13:03:18  CLAIM-FALSO: afirma una acción NO ejecutada; reescritura honesta
```

El modelo gastó los 3.072 tokens **enteros razonando** y llegó a `contenido=0`, así que
nunca emitió la tool call. Y el sistema **deliberadamente** no había registrado nada
porque no sabía el ejercicio (`[SISTEMA]: reportó series sin decir de qué ejercicio...
Pregúntale a cuál corresponden`). CLAIM-FALSO hizo bien su trabajo; lo que falló es que
el modelo, sin saber la causa, **se la inventó** y culpó al sistema (arreglo #4).

La causa de fondo —el thinking se come el `num_predict`— sigue viva y es la misma que
las respuestas de 3.072 tok. No se tocó el sampling sin medir.

### `generar_rutina` NO era un bug: era el test

El system prompt ordena *"Tú NO gestionas el orden, NO calculas marcas y **NO invocas
herramientas de generación**"* porque el flujo lo controla Python
(`clasificar_intencion_plan`, `CREACION_FUERTE_RE`). El modelo acierta **5/5** al NO
llamarla. `bench_calidad.py` esperaba lo contrario, así que su **máximo real es 7/8, no
8/8** — avisado en el propio script y corregido en `bench_calidad_hist.py`. Hacer que la
llamara habría roto el onboarding.

**Patrón reconfirmado con datos nuevos:** aciertos con 850-1.946 tok de thinking, fallos
con 2.548-2.958. Razonar de más sigue siendo el predictor de fallo.

---

## PENDIENTE

| Tarea | Impacto | Notas |
|---|---|---|
| **Persistir los mensajes del orquestador** | ~10 s en turnos con tools | Caso residual de la opción B: los `tool_calls` y el `[TOOL-RESULT]` se envían pero no se guardan, así que el turno siguiente diverge desde ahí |
| Subir `CACHE_RAM` a 5120 | Medio | Cada prompt ocupa 1.741 MiB y el techo son 3072: solo cabe UNO, las intercaladas lo desalojan. Necesita sudo. RAM justa (4,0 GB libres de 15,8) |
| Verificar los arreglos v11 en producción | — | Los 6 arreglos están medidos en banco pero solo el #1 y #2 se han visto en tráfico real. Vigilar que el coach diga bien el día de la semana y que pida el ejercicio en vez de culpar a un "error técnico" |
| Techo del thinking | Latencia **y** calidad | Es la causa común de las respuestas de 3.072 tok Y de los fallos de tool call (>2.500 tok razonando ⇒ falla). Atacarlo arregla las dos cosas a la vez |
| **Fase 4: typing indicator** | Alto en percepción, riesgo cero | `POST /messages` con `{"status":"read","message_id":…,"typing_indicator":{"type":"text"}}` al entrar el webhook (`api/rutas_whatsapp.py:510`). El atleta ve "escribiendo…" en <1 s |
| **Fase 3.2: evitar doble generación** | −6 a −13 s | Cuando `consulta_rutina` ya se resolvió en Python, `obtener_rutina_del_json` devuelve texto listo; la síntesis post-tools solo lo reescribe |
| **Fase 2.3: telemetría efímera** | Medio | `llm_service.py` poda la telemetría *después* de enviarla y reemplaza la memoria viva con la versión podada → el prefijo diverge entre turnos. Mandarla como mensaje aparte que se descarta |
| Fase 2.2: perfiles de tools | **Revisar al alza** | Se había descartado "porque la caché arreglada quita el coste de prefill". Medido: la caché NO acierta, así que las tools sí se están reprocesando cada turno |
| Coste del compresor | 8,2 s **+ 72 % de caché invalidada** | No estaba en el plan. Evaluar si puede ir fuera del camino crítico de la respuesta |
| Escalera de checkpoints | Colchón para divergencias legítimas | No hay checkpoints por debajo del punto de divergencia pese a `MIN_SPACING_NT=2048`. Investigar si `CTX_CHECKPOINTS=8` los desaloja |
| **Acortar las respuestas** | **Alto y sin tocar caché** | Dos turnos toparon `num_predict` con 3072 tok de decode = 95,8 s solo generando (turno de 121,8 s) |
| Bug funcional (no latencia) | — | El bot dijo "no pude registrar esa serie ni la de 210 kg, hubo un error técnico en el guardado". Fallo real de registro, fuera de este plan |
| Speculative decoding | 1,5–2× decode, salida idéntica | Requiere `llama-server` directo (Ollama no expone `--model-draft`) y un draft compatible con el tokenizer `qwen35` |

---

## Cómo medir

```bash
cd /home/abimael/PycharmProjects/Agente-Iron-Progress/services/testbed/latencia
python3 bench6.py "etiqueta"        # turno completo con llamada intercalada
python3 bench_calidad.py            # aciertos de tool call por modo de thinking
python3 bench_redaccion.py          # ahorro en turno de solo redacción
```

**Avisos de método aprendidos a golpes:**
- Los *wall times* se contaminan si el bot está corriendo en paralelo. **El dato fiable
  está en el log de Ollama**, no en el cliente:
  `journalctl -u ollama -f | grep -E "checking checkpoint|prompt eval time|truncated"`
- `data/historial_*.json` cambia solo mientras el bot corre (se comprime al pasar
  `UMBRAL_COMPRESION`). Por eso los benchmarks usan `hist_congelado.json`.
- Vigilar RAM: solo hay 15 GB y zram llegó a 3,8 GB durante las pruebas. Si el sistema se
  pone pesado, bajar `LLAMA_ARG_CACHE_RAM` a 2048.

## Cómo ver los logs del bot

El proceso escribe a un pipe y se pierde. Arrancar así:

```bash
python main.py 2>&1 | tee -a /tmp/iron.log
```

`[LLM-PERF]` (en `chat_llm`) ya imprime el desglose prefill/decode por llamada, y `[THINK]`
dice si el turno razonó y por qué.

## Reinicios: qué exige cada cambio

`.env` no define `ENTORNO`, así que `core/config.py` cae a `"dev"` y uvicorn corre con
`reload=True` (por eso hay dos procesos).

| Cambio | ¿Reiniciar? |
|---|---|
| `services/*.py` | No — uvicorn recarga solo |
| `core/prompt.json`, `core/tools.json` | **Sí** — `reload_excludes` excluye `*.json` y además `_system_prompt_cache` / `_herramientas_cache` son globales |
| `num_ctx` | Ollama recarga el modelo solo (~40 s en la primera petición) |
| `perf.conf` de Ollama | `sudo systemctl restart ollama` |
