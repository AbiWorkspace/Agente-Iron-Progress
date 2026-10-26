# Cap. 6 · El orquestador

> Estado: **hecho (borrador)** · Tareas: F6.3, F6.4, F6.5 · Fecha: 2026-08-27
> Ficheros fuente: `services/llm_service.py` (7.792 líneas), `services/handlers_v9.py`,
> `core/prompt.json`, `core/tools.json`, `services/testbed/test_contrato_tools.py`

---

## 6.1 Anatomía del turno: bloques y fases reales (F6.3)

`services/llm_service.py` es el componente central del sistema (7.792 líneas — F1.2). No
implementa el turno como una única función lineal de 23 pasos numerados: la lógica se reparte
entre `generar_respuesta_coach` (la función de entrada, con lock por atleta) y una veintena de
funciones auxiliares a las que delega en secuencia. La tabla siguiente documenta esa secuencia
real, agrupada en los cinco bloques funcionales que ya proponía el PDF v9.1, pero ahora con el
nombre de función y la línea de cada fase verificados directamente en el código (no como
etiquetas A-W literales, que no existen como comentarios en el fichero — es una decomposición
analítica de este documento, apoyada en anclas de código reales).

| Bloque | Fase | Función / mecanismo | Evidencia |
|---|---|---|---|
| **Preparación** | Carga de historial con detección de modificación externa | Comparación de firma de fichero (F6.9, caché por firma) | `llm_service.py` (según F1.2, sección de persistencia) |
| | Espera de compresión en curso | `_esperar_compresion_en_curso` | línea ~5871 (offset 804 desde 5068) |
| | Reparación de estados corruptos | `_actualizar_estado_json` (múltiples puntos de guarda) | primeras ~700 líneas de la función, invocada 8+ veces |
| **Máquinas de estado** | Cálculo del campo de onboarding pendiente | `calcular_faltantes` | líneas ~5360, 5376, 5589 y ss. |
| | Disparo de la generación del plan | `_disparar_pipeline_generacion` (tarea de fondo) | líneas ~5378, 5519, 5597, 5688 |
| | Sello del ejercicio en curso | `fijar_ejercicio_confirmado` / `leer_ejercicio_confirmado` | líneas ~6025-6148 (`handlers_v9.py:60-84`) |
| **Contexto** | Construcción de la telemetría del turno | `construir_telemetria` | línea ~5849 |
| | Recuperación vectorial (RAG) | `construir_contexto_enriquecido` | línea ~5876 |
| | Lectura de la rutina resuelta (% → kg) | `obtener_rutina_del_json` | líneas ~5946, 5954, 6446 |
| | Clasificación de la intención del turno (meta) | `_clasificar_meta` | línea ~6605 |
| **Ejecución** | Inferencia principal (con herramientas) | `await chat_llm(...)` | línea ~6481 |
| | Bucle de herramientas (máx. 3 vueltas) | segunda llamada a `chat_llm` dentro del bucle | línea ~7103 |
| | Rescate ante respuesta truncada | `_forzar_respuesta_texto` (sin herramientas, `pensar=False`) | definida inmediatamente después de `generar_respuesta_coach`, línea ~5094 |
| **Validación** | Post-proceso para WhatsApp | `limpiar_texto_para_whatsapp` (7 invocaciones tras cada red) | líneas 7151-7333 |
| | Red 1 — Kilajes inventados | `_verificar_kgs_salida` | líneas 7188, 7198 (ver F1.3) |
| | Red 2 — Rutina fantasma | `_parece_rutina_inventada` | línea 7225 (ver F1.3) |
| | Red 3 — Reparador de onboarding | `_reparar_onboarding` | línea 7241 (ver F1.3) |
| | Redes 4 y 5 — Claim falso / estado del plan | `_CLAIM_ACCION_RE`, `_claims_plan_desmentidos` | líneas 7258-7325 (ver F1.3) |
| | Red 6 — Anti-eco | comparación textual exacta | línea ~7338 (ver F1.3) |
| | Persistencia del orquestador | mensajes de herramienta guardados tal cual se enviaron | líneas 7345 y ss. (ver Cap. 14) |
| | Compresión diferida del historial | `_lanzar_compresion_diferida` | línea ~7370 |

Esta tabla sustituye a la referencia genérica a "23 fases" del PDF v9.1: cuenta **19 fases
identificadas con evidencia de línea**, organizadas en los mismos cinco bloques. La diferencia
con el número original no es una corrección de un error, sino un cambio de método —contar
funciones con ancla verificable en vez de un número redondeado de memoria—, y se declara así
explícitamente siguiendo la misma disciplina aplicada en F1.2.

## 6.2 Los interceptores deterministas, con su algoritmo (F6.4)

Los interceptores son el mecanismo que materializa la decisión de diseño central del proyecto
(Cap. 1, §1.2): antes de que el modelo intervenga, código Python ya ha decidido y ejecutado la
acción. El inventario del PDF v9.1 lista nueve interceptores; aquí se detalla el algoritmo de
los dos más complejos, que son también los que más incidentes de diseño han motivado.

### Herencia del ejercicio entre turnos: doble ventana temporal

El atleta rara vez repite el nombre del ejercicio en mensajes consecutivos ("otra igual, 100
por 4"). El sistema resuelve esta elipsis con **dos ventanas de tiempo distintas**, no una
(`services/handlers_v9.py:60-61`):

```python
_TTL_CONFIRMADO_S = 30 * 60      # ventana de HERENCIA
_TTL_IMPONER_S = 3 * 60          # ventana de IMPOSICIÓN sobre lo que trae el modelo
```

- **Ventana de herencia (30 min):** si el atleta escribe una serie sin nombrar ejercicio, el
  sistema hereda el último ejercicio confirmado dentro de esa ventana
  (`leer_ejercicio_confirmado`, `handlers_v9.py:72`).
- **Ventana de imposición (3 min):** cuando el *modelo* propone un nombre de ejercicio
  distinto del sellado, el sistema solo permite que ese nombre sobrescriba el sello si han
  pasado menos de 3 minutos desde que se confirmó — pasado ese margen, se asume que el atleta
  cambió de ejercicio en el gimnasio y el sello previo ya no es fiable, así que el nombre
  entrante gana sin restricción.

Esta asimetría (30 min para heredar, solo 3 min para que algo la sobrescriba) es la respuesta
de diseño a un hallazgo documentado en el propio PDF v9.1 (Cap. 6): el mecanismo de sellado
existía desde la versión 9 pero **ninguna función en toda la base de código lo leía** — la
función de lectura devolvía siempre `None`, y las series se atribuían silenciosamente al
ejercicio que el modelo alucinara. Conectar la lectura resolvió esa pérdida silenciosa.

### El tokenizador de series: anclaje por unidad, no por posición

El interceptor de registro de series (Cap. 6 del PDF v9.1) no asume un orden fijo de los
valores en el mensaje del atleta — "100x5", "100 por 5" y "saqué 6 con 20 de lastre" deben
resolver al mismo triplete (peso, repeticiones, esfuerzo) pese a tener el peso y las
repeticiones en posiciones distintas del texto, y en el tercer caso en orden invertido. El
tokenizador ancla cada valor numérico a su unidad o a la palabra que lo precede/sigue (kg, x,
por, "de lastre", "en el tanque" para el esfuerzo), no a su posición en la oración — es la
misma estrategia de anclaje semántico, no posicional, que usa la ventana de imposición para el
ejercicio: preferir una señal de contenido verificable a una convención de formato frágil.

## 6.3 El contrato con el modelo: las 13 secciones y el razonamiento selectivo (F6.5)

El prompt del sistema (`core/prompt.json`) tiene **13 secciones**, no 12 como documentaba el
PDF v9.1 — la sección `presupuesto_de_razonamiento` se documenta en detalle en F1.4 y no se
repite aquí. El catálogo de herramientas (`core/tools.json`) declara **23 herramientas**, cada
una con su contrato de argumentos verificado por `services/testbed/test_contrato_tools.py`
contra tres cosas a la vez: el catálogo, los handlers que las ejecutan, y el filtro de secreto
profesional del prompt (que debe reconocer y censurar el nombre de cada herramienta si
apareciera por error en el texto de salida).

### Razonamiento selectivo: cuándo piensa el modelo y cuándo solo redacta

El modelo activa su modo de razonamiento intermedio (*thinking*) únicamente en los turnos
donde aporta valor real: decidir qué herramienta invocar, o sintetizar el resultado de varias
herramientas ejecutadas. En los turnos donde Python ya resolvió todo —onboarding, charla
casual, o cualquier turno donde un interceptor ya ejecutó la acción— el modelo solo redacta,
y razonar sería latencia desperdiciada sin ninguna decisión que tomar. Los parámetros de
muestreo cambian entre ambos modos (`llm_service.py:1556` y ss.): temperatura 0,3 para
redacción, con un presupuesto de tokens mayor y temperatura 0,6 para razonamiento — valores
verificados en vivo en esta sesión contra el servidor Ollama real (F5.2).

Esta selectividad es, junto con la separación cálculo/lenguaje del Cap. 1, la segunda decisión
de diseño de mayor impacto en latencia del sistema: se retoma con cifras medidas en el Cap. 14
(Concurrencia y rendimiento).
