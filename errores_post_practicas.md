# Errores a Solucionar Post-Prácticas

Este documento contiene el registro de errores identificados durante la ejecución que requieren solución en un futuro.

## 1. Registro Parcial de Volumen en Series Agrupadas (Falsa Confirmación del Bot)

**Cuándo sucede:**
El error ocurre durante el registro de entrenamiento, específicamente cuando el atleta reporta en un solo mensaje que completó múltiples series idénticas (por ejemplo: *"ya hice press con mancuernas, las 4 series me salieron con 30 kilos y 10 reps a RPE 7.5"*).

**Cómo sucede:**
1. El modelo de lenguaje (LLM) comprende perfectamente que el usuario hizo 4 series.
2. Sin embargo, la herramienta `registrar_serie_completada` está diseñada estructuralmente para recibir los datos de **UNA sola serie** (no tiene parámetro de "cantidad").
3. Al ver esto, el LLM invoca la herramienta `registrar_serie_completada` **una única vez** pasando los datos (`peso_kg: 30`, `repeticiones: 10`, `rpe: 7.5`).
4. Inmediatamente después, el LLM responde al atleta con tono complaciente: *"Perfecto, anotado. Las 4 series con 30 kg a RPE 7.5 quedaron registradas."*
5. **Consecuencia (Desconexión de estado):** La base de datos y la métrica de volumen (MRV) solo registraron 1 serie. Se pierde el 75% del volumen real ejecutado por el usuario en el historial porque el bot alucinó una acción que el sistema backend no pudo procesar en lote.

**Solución Documentada (Para implementar después):**
* **Opción A (Recomendada):** Modificar el archivo `core/tools.json` añadiendo un parámetro opcional a la herramienta `registrar_serie_completada` llamado `"cantidad_series": {"type": "integer", "default": 1}`. Luego, en el código Python que procesa esta función, envolver el guardado en un ciclo `for` que itere según `cantidad_series`.


## 2. Alucinación Cruzada de Ejercicio (Ignora el Sello del Backend)

**Cuándo sucede:**
Cuando el usuario reporta un récord usando jerga de gimnasio o con errores de transcripción (ej: *"precinclinado"* en lugar de Press Inclinado).

**Cómo sucede:**
1. El script de Python en el backend extrae correctamente el nombre del ejercicio (en el log se ve la etiqueta de éxito `[EJ-SELLO] el atleta nombró 'Press Inclinado'`).
2. Sin embargo, el LLM (probablemente traduciendo internamente a un concepto en inglés como *Bent Over Barbell Row*) ignora el sello del backend y deduce que el usuario se refería a un "Remo con Barra".
3. El LLM invoca la herramienta `calcular_rm_exacto` pasando como argumento `nombre_ejercicio: "Remo con Barra"`.
4. **Consecuencia:** El récord y progreso del usuario se sobrescriben en el músculo equivocado (Espalda en lugar de Pecho).

**Solución Documentada:**
* Reforzar el System Prompt indicando que si el sistema anexa la etiqueta `[EJ-SELLO]`, el LLM **debe** priorizar el nombre de ejercicio que dicte esa etiqueta sobre su propia deducción semántica.

## 3. Limitación de Herramienta (Cálculo Inverso) y Desobediencia Matemática

**Cuándo sucede:**
Cuando el atleta, en lugar de preguntar su 1RM, pregunta cuántas repeticiones necesita sacar con cierto peso para **alcanzar** un 1RM objetivo (Ej: *"si quisiera obtener mi 1RM de 140kg, ¿cuántas repeticiones tendría que hacer con 110kg?"*).

**Cómo sucede:**
1. La herramienta `calcular_rm_exacto` está programada en una sola dirección (Peso + Reps = 1RM), por lo que el LLM se da cuenta que no le sirve.
2. Ante la falta de herramienta, el LLM **rompe la regla estricta de no hacer matemáticas** y decide invocar el libro científico para buscar la tabla de RPE/Porcentajes.
3. El LLM calcula mentalmente la división `110 / 140 = 78.6%` y extrapola que debe hacer 5 repeticiones.
4. **Consecuencia:** Aunque la respuesta final resultó ser sorprendentemente correcta, esto es un riesgo gravísimo. Los LLMs alucinan frecuentemente con los números, y este exceso de iniciativa disparó el filtro de seguridad interno (`[CLAIM-FALSO]`), forzando al orquestador a descartar el mensaje original y usar un modelo de repuesto más lento (el *fallback-texto* tardó 25 segundos extra).

**Solución Documentada:**
* **Opción A:** Agregar soporte de cálculo inverso al backend Python y a la herramienta `calcular_rm_exacto` (añadiendo parámetros opcionales como `rm_objetivo`).
* **Opción B:** Si no se quiere modificar la herramienta, añadir una instrucción al prompt indicando que si el usuario pide repeticiones objetivo para un PR, el bot debe informarle amablemente que su sistema de cómputo solo funciona ingresando series ya completadas.
