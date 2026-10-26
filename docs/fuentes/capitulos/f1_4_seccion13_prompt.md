# F1.4 — La 13.ª sección del prompt: `presupuesto_de_razonamiento`

> Estado: **hecho** · Fecha: 2026-08-27
> Fuente: `core/prompt.json` (clave `promt`); uso en `services/llm_service.py:4418`.

## Las trece secciones, en su orden real dentro de `core/prompt.json`

| # | Sección | Función |
|---|---|---|
| 1 | `identidad` | Tono de entrenador de élite y formato compatible con WhatsApp |
| 2 | `capacidades` | Qué responder cuando el atleta pregunta para qué sirve el sistema |
| 3 | `secreto_profesional` | Prohibición absoluta de mencionar herramientas, bases de datos o términos técnicos |
| 4 | `fuente_de_verdad` | El modelo no crea rutinas ni calcula cargas; transcribe lo que Python inyecta |
| 5 | `telemetria_vigente` | Solo vale el bloque de datos del último mensaje; los anteriores han caducado |
| 6 | `herramientas` | Diecisiete reglas que asocian situación con herramienta |
| 7 | `criterio_ante_dudas` | Cinco principios ordenados para los casos que las reglas no cubren |
| 8 | `honestidad_con_resultados` | Nunca confirmar una acción sin un resultado del sistema que la respalde |
| 9 | `sentido_comun_numerico` | Detección de cifras implausibles y confirmación con naturalidad |
| 10 | `salud_del_atleta` | Prohibición de inventar o exagerar gravedad médica |
| 11 | `creacion_de_plan` | El onboarding lo gobierna Python; el modelo solo redacta la pregunta |
| 12 | `memoria_viva` | Uso del expediente para personalizar sin preguntar dos veces |
| 13 | **`presupuesto_de_razonamiento`** | **Ausente en la tabla del PDF v9.1** — límite explícito al razonamiento intermedio |

## Contenido íntegro de la sección 13

```
[PRESUPUESTO DE RAZONAMIENTO]: piensa en 80 palabras COMO MÁXIMO antes de actuar.
Decide la herramienta y emítela; no repases el historial entero ni enumeres
alternativas que ya descartaste.
```

## Por qué falta en el PDF v9.1 y qué significa

El PDF v9.1 (31 de julio de 2026) documenta 12 secciones porque, con toda probabilidad, esta
sección se añadió después de esa fecha de corte — es coherente con que el capítulo 14 del
propio PDF ya describa el "techo de razonamiento" (por encima de ~2.500 tokens el modelo
degrada) como una limitación conocida. `presupuesto_de_razonamiento` es la mitigación de
prompt para ese problema: un tope duro de 80 palabras de pensamiento antes de decidir,
independiente del tope de tokens de la ventana de contexto. Complementa, no sustituye, el
mecanismo de rescate que reintenta sin razonamiento cuando la respuesta llega truncada y
vacía (capítulo 14 del PDF).

## Nota sobre la clave `promt` (con errata)

`core/prompt.json` almacena todo el system prompt bajo la clave `"promt"` (sin la segunda
"o"). No es un bug: es el único punto de lectura en toda la base de código
(`services/llm_service.py:4418`, `json.load(f)["promt"]`) y ambos coinciden, así que el
sistema funciona con normalidad. Se documenta aquí porque es exactamente el tipo de
desajuste nombre-de-clave que en otras partes del sistema (el contrato `tools.json` ↔
handlers) sí ha causado fallos silenciosos — en este caso no ocurrió porque solo hay un
lector. **No se corrige** en este plan de documentación: es una decisión de código, no de
documentación, y renombrarla sin motivo no aporta nada a la tesina.

## Redacción para la tesina (Cap. 6, El contrato con el modelo)

> El prompt del sistema se organiza en trece secciones etiquetadas, no doce: a las doce que
> definen identidad, límites y las diecisiete reglas de decisión se suma
> `presupuesto_de_razonamiento`, que acota a 80 palabras el razonamiento intermedio del
> modelo antes de decidir una herramienta. Esta sección es la mitigación a nivel de prompt
> del techo de razonamiento descrito en el capítulo de concurrencia y rendimiento: por
> encima de cierto presupuesto de tokens de pensamiento, el modelo degrada su capacidad de
> construir invocaciones de herramientas válidas.
