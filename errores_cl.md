# Registro de Errores: Clasificadores y Filtros (CL)

Este documento centraliza los errores relacionados con los clasificadores, expresiones regulares y filtros de seguridad del sistema.

## 1. Falso Positivo del Guardia de Seguridad (CLAIM-FALSO)

**Cuándo sucede:**
Ocurre cuando el LLM redacta una explicación legítima y honesta sobre por qué una herramienta falló o rechazó una acción (ej: falta de datos para una gráfica), pero en su explicación utiliza palabras clave que suenan a registro o guardado.

**Cómo sucede:**
1. El usuario pide una gráfica de evolución.
2. El LLM llama a la herramienta `enviar_grafica_progreso`.
3. La herramienta rechaza la petición porque necesita un mínimo de 3 sesiones registradas (solo había 1).
4. El LLM acata la regla de honestidad y redacta: *"Aún no tengo suficiente historial... necesito al menos 3 sesiones completas **registradas**... Por ahora, confía en los números que vamos **guardando** hoy."*
5. El guardia `CLAIM-FALSO` (Python) evalúa el texto mediante la expresión regular `_CLAIM_ACCION_RE`.
6. Al detectar las palabras `"registradas"` y `"guardando"`, la regex asume erróneamente que el bot está mintiendo (confirmando un éxito falso).
7. **Consecuencia:** Python bloquea el mensaje del LLM (que era perfecto) y fuerza la ejecución de un `fallback-texto`, lo que resulta en un mensaje lento, frío, robótico y con problemas de redacción ("*mis métodos de coach no falló...*").

**Solución Documentada:**
* Refinar la expresión regular `_CLAIM_ACCION_RE` en `services/llm_service.py` (línea ~6864).
* En lugar de atrapar raíces sueltas como `(guardad|registrad)`, debe exigir anclajes afirmativos (ej: `r"(ya lo he guardado|ha quedado registrado|lo acabo de anotar)"`).
* Alternativamente, usar un clasificador semántico superligero en vez de regex para determinar la verdadera intención de la frase (Afirmación vs. Condicional/Explicación).
