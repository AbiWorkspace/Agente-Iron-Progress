# F1.3 — Las seis redes de validación de salida, verificadas en código

> Estado: **hecho** · Fecha: 2026-08-27
> Fuente: `services/llm_service.py`. El PDF v9.1 documenta cuatro (cap. 8) y la tabla
> resumen del mismo PDF (cap. 1) y el README dicen seis. Esta tarea resuelve la
> discrepancia localizando las seis, en el orden real en que se encadenan.

## Las seis redes, con función, disparador y orden de ejecución

| # | Red | Función / variable | Línea de definición | Línea de invocación | Qué caza |
|---|---|---|---|---|---|
| 1 | Kilajes inventados | `_verificar_kgs_salida` | `llm_service.py:5046` | `:7188` (y `:7198` tras reescritura) | Cifras en kg en el texto de salida que no están en la whitelist dinámica del turno (plan, panel de series, resultados de herramientas, historial), con tolerancia ±0,5 kg y exención de discos/barra vacía |
| 2 | Rutina fantasma | `_parece_rutina_inventada` | `:1133` | `:7225` | ≥2 líneas con forma de prescripción (`"4 series de 8"`, `"5x5"`, `"8 reps"`) cuando no hay plan generado ni generación en curso |
| 3 | Reparador de onboarding | `_reparar_onboarding` | `:1092` | `:7241` | El modelo pregunta un campo inexistente ("nivel/experiencia") o repregunta un dato ya guardado durante el turno de recolección de la ficha |
| 4 | Claim falso | `_CLAIM_ACCION_RE` | `:558` | `:7258`, `:7264` | Afirmar "ya lo registré/apliqué/guardé" (participio o pretérito) sin que ninguna escritura se haya completado realmente en el turno |
| 5 | Estado del plan | `_claims_plan_desmentidos` | `:671` | `:7313`, `:7325` | Dar por aplicada una descarga, una pausa, una reanudación o un avance de día, contrastando contra el estado real en disco (no contra qué se ejecutó en el turno) |
| 6 | Anti-eco | inline, sin función propia | — | `:7338` | Respuesta de este turno **idéntica carácter a carácter** a la última respuesta del asistente en el historial |

## Mecanismo común

Las redes 1-5 comparten el patrón: si saltan, se fuerza **una** reescritura inyectando la
verdad del turno; si el modelo reincide, se sustituye la respuesta por una plantilla honesta
escrita en Python (ver ejemplo textual en el código, línea ~7332: *"Ojo bro, me adelanté 🙏
Eso todavía NO está hecho..."*). Cero bucles: como mucho una vuelta de reescritura por red.
La red 6 no reescribe con el modelo — simplemente le añade una coletilla de variación
(`"\n\n¿Seguimos bro? 💪"`) al texto ya generado.

## Hallazgo: el README sobreclama la red anti-eco

El README describe la red 6 como: *"Repetir el mensaje anterior con otras palabras"* —
es decir, da a entender una detección semántica/parafraseada. **El código no hace eso.**
La comprobación real (línea 7339) es una igualdad de cadenas exacta:

```python
if texto_final and texto_final.strip() == ultimo_asst.strip():
```

Dos respuestas que dicen lo mismo con palabras distintas —el caso que el README describe—
**no disparan esta red**. Solo la detecta si el modelo devuelve el texto literalmente
repetido. Esto debe documentarse en la tesina como una limitación conocida de la red 6, no
como su comportamiento (Cap. 7, Defensas contra la alucinación, y F9.3 Limitaciones).

## Confirmación del hallazgo lingüístico del subjuntivo (regla 4)

`_CLAIM_ACCION_RE` (línea 559-570) ancla los verbos de acción a las terminaciones
`[éó]|amos|aste|aron|ad[oa]s?|id[oa]s?` — es decir, pretérito y participio, con acento
obligatorio en la primera persona del singular. `aplique` (subjuntivo, sin tilde, "que yo
aplique una descarga") **no** coincide con el patrón porque el patrón exige `[éó]` con
tilde; `apliqué` (pretérito, "ya apliqué la descarga") sí coincide. Es la evidencia de
código que respalda la afirmación del PDF v9.1 (cap. 8) sobre por qué el subjuntivo español
es fuente de falsos positivos/negativos en este tipo de detección.

## Redacción para la tesina (Cap. 7, Defensas contra la alucinación)

> El sistema encadena seis redes de validación tras la redacción del modelo y antes del
> envío al canal, cada una diseñada a partir de un incidente observado en conversación
> real. Cinco de las seis auditan afirmaciones textuales contra una fuente de verdad
> explícita —una whitelist de cifras legítimas, el estado real del plan en disco, o el
> registro de qué escrituras se completaron efectivamente en el turno—; la sexta es una
> comprobación de igualdad textual, más limitada de lo que la documentación de producto del
> proyecto describe, y se reporta aquí como tal.
