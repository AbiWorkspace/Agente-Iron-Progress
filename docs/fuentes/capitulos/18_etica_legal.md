# Cap. 18 · Consideraciones éticas y legales

> Estado: **hecho (borrador)** · Tareas: F10.1 a F10.4 · Fecha: 2026-08-27
> Ficheros fuente: `core/prompt.json` (sección `salud_del_atleta`, cita textual verificada),
> `docs/fuentes/capitulos/f1_6_inventario_biblioteca_rag.md`

---

## 18.1 Tratamiento de datos personales de salud (F10.1)

El sistema almacena, asociados a un número de teléfono (identificador directo de una persona
física): peso, altura, edad, género, porcentaje de grasa corporal, marcas de fuerza,
molestias y lesiones reportadas en lenguaje libre, y el historial completo de entrenamiento
(Cap. 10). Bajo el Reglamento General de Protección de Datos de la UE (RGPD, 2016/679), los
datos de salud son una **categoría especial de datos** (art. 9), sujeta a condiciones de
tratamiento más estrictas que los datos personales ordinarios; bajo la Ley Federal de
Protección de Datos Personales en Posesión de los Particulares de México (2010), estos datos
se clasifican igualmente como "datos sensibles". Ninguno de los dos marcos se aplica
automáticamente a un proyecto personal sin usuarios de terceros, pero ambos son la referencia
normativa correcta si el sistema pasara a operar con atletas ajenos al desarrollador — algo que
el propio README dice explícitamente que ya ocurre ("agente conversacional... en desarrollo
activo", con un número real de WhatsApp).

**La decisión de arquitectura más relevante para este capítulo es que la inferencia, los
embeddings, la transcripción de audio y el análisis de vídeo corren íntegramente en local**
(Cap. 5 §5.3): ningún dato de salud del atleta se envía a un tercero para su procesamiento por
IA. Esto reduce sustancialmente la superficie de exposición frente a un sistema equivalente
que dependiera de una API de LLM en la nube, aunque **no elimina** la necesidad de una base
legal para el tratamiento, de un aviso de privacidad, ni de medidas de seguridad sobre los
ficheros SQLite y JSON que sí contienen estos datos en texto plano en disco — no se encontró en
el código ningún mecanismo de cifrado en reposo para `iron_progress.db` ni para los ficheros de
estado en JSON.

## 18.2 Límites del consejo y descargo de responsabilidad, verificados textualmente (F10.2)

La sección `salud_del_atleta` del prompt del sistema (`core/prompt.json`, texto extraído y
verificado en esta sesión, no parafraseado) dice, íntegramente:

> *"Cuando el atleta mencione una molestia o lesión, reporta ÚNICAMENTE lo que él dijo. Tienes
> PROHIBIDO inventar o exagerar gravedad médica: nunca digas que está 'en el hospital', que
> necesita 'el alta del médico' ni que debe 'reposar' si él no lo dijo. Sugiere ver a un
> profesional solo si él describe algo serio, y hazlo una sola vez, sin dramatizar. Si el
> atleta dice que YA SE RECUPERÓ o que ya no le duele, trata esa lesión como resuelta a partir
> de ese momento y no la vuelvas a sacar en turnos siguientes a menos que él la mencione de
> nuevo."*

Esta cita confirma con evidencia textual directa —no inferida— que el sistema **sí** codifica
una frontera explícita entre acompañar el entrenamiento y practicar medicina: no diagnostica,
no inventa gravedad, y solo sugiere consulta profesional ante síntomas que el propio atleta ya
describió como serios, una única vez. Es una salvaguarda de prompt, no de código determinista
—a diferencia de las redes de validación del Cap. 7, no hay un mecanismo que verifique
después de generada la respuesta si el modelo violó esta instrucción—, lo que la hace más
frágil en principio que las defensas contra alucinación numérica. No se encontró en el código
una red de validación dedicada a detectar una violación de esta regla concreta (por ejemplo,
el modelo mencionando "hospital" o "alta médica" sin que el atleta lo haya dicho) — es una
brecha de cobertura real entre lo que el prompt prohíbe y lo que el sistema verifica
después, que se añade al inventario de limitaciones del Cap. 19.

## 18.3 Riesgos del sistema y su mitigación (F10.3)

| Riesgo | Mecanismo que lo mitiga | ¿Verificado en esta sesión? |
|---|---|---|
| Alucinación de cargas de entrenamiento | Separación cálculo/lenguaje + red 1 (Cap. 1, 7) | Sí — arquitectura confirmada, sin caso de falla observado en esta sesión |
| Prescripción de ejercicio sobre una lesión no declarada | El sistema solo conoce lo que el atleta reporta explícitamente; no hay mecanismo de detección de lesión no declarada, ni podría haberlo sin evaluación médica real | No aplica — es un límite estructural, no un mecanismo pendiente de implementar |
| Exageración o invención de gravedad médica | Sección `salud_del_atleta` del prompt (§18.2) | **Parcialmente** — regla de prompt confirmada, sin red de validación posterior que la audite |
| Dependencia excesiva del atleta en el sistema | No hay mecanismo explícito en el código; es un riesgo psicosocial fuera del alcance técnico del sistema | No — riesgo reconocido, no mitigado, señalado aquí por completitud |
| Sesgo del clasificador de ejercicios por dataset desbalanceado | Ninguno — el desbalance existe y no se corrige (Cap. 12 §12.2) | Sí, el riesgo está confirmado; la mitigación no existe |
| Falso positivo de una red de validación que degrada una respuesta correcta | Ninguno a nivel de red; el mecanismo de reescritura no verifica si mejora la respuesta (Cap. 7 §7.5) | **Sí — observado en vivo en esta sesión** |
| Exposición de datos de salud por falta de cifrado en reposo | Ninguno encontrado en el código | Sí, la ausencia está confirmada |

## 18.4 Cumplimiento de terceros y propiedad intelectual (F10.4)

### Políticas de la plataforma de mensajería

El sistema opera sobre WhatsApp Business Cloud API, sujeto a la política de mensajería de Meta
(*WhatsApp Business Messaging Policy*) y a la ventana de 24 horas ya documentada como
restricción legal, no técnica (Cap. 13 §13.2). El cumplimiento de esta política es una
condición de funcionamiento del sistema, no una elección — perder acceso a la API
interrumpiría el canal completo.

### Licencias del software utilizado

El stack tecnológico (`requirements.txt`, Cap. 4 §4.2) combina librerías con licencias
permisivas estándar del ecosistema Python científico (MIT, BSD, Apache 2.0 en su mayoría);
no se realizó en esta sesión una auditoría de licencias exhaustiva fichero por fichero — se
señala como tarea pendiente si el proyecto se distribuye o comercializa.

### El hallazgo más serio de esta sesión: PII de un tercero en un fichero versionado

Documentado en detalle en F1.6 (`docs/fuentes/capitulos/f1_6_inventario_biblioteca_rag.md`) y
se reproduce aquí por pertenecer estructuralmente a este capítulo: el fichero
`The-Muscle-and-Strength-Pyramid-Entrenamiento-espa.pdf`, indexado en el RAG del sistema y
**versionado en git**, contiene una marca de agua con el nombre completo y el correo personal
de un tercero ajeno al proyecto —evidencia de que es una copia de distribución personalizada
de un libro con derechos de autor—. Si el repositorio es o llega a ser público, esto expone
simultáneamente **datos personales de un tercero sin su consentimiento** (relevante bajo
RGPD/LFPDPPP pese a no ser el propio atleta) y **una infracción de propiedad intelectual**
sobre la obra original. Ninguna acción correctiva se tomó sobre este fichero durante esta
sesión de documentación — es una decisión de producto/legal que corresponde al propietario del
repositorio, no a este ejercicio de documentación.

### Encaje prospectivo con el Reglamento de IA de la UE

El Reglamento (UE) 2024/1689 clasifica los sistemas de IA por nivel de riesgo. Un sistema que
recolecta datos de salud y da recomendaciones de entrenamiento físico, sin llegar a ser un
dispositivo médico, se situaría previsiblemente en una categoría de riesgo limitado o
transparencia (obligación de informar al usuario de que interactúa con un sistema de IA) más
que de alto riesgo (reservado a usos como diagnóstico médico o decisiones con efecto legal
significativo) — esta clasificación es una lectura razonada de este documento, no una opinión
legal formal, y se señala explícitamente como tal.
