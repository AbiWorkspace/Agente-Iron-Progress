# Cap. 13 · Voz y proactividad

> Estado: **hecho (borrador)** · Tareas: F7.3, F7.4, F7.5 · Fecha: 2026-08-27
> Ficheros fuente: `services/audio_service.py`, `services/proactividad.py` (545 líneas),
> `services/proactive_coach.py` (209 líneas), `services/testbed/test_rpe_hablado.py` (46 casos)

---

## 13.1 Transcripción de voz: una decisión forzada por hardware, no por calidad (F7.3)

`services/audio_service.py` carga `WhisperModel` (faster-whisper) con `device="cpu"` y
`compute_type="int8"` (líneas 11-14). Esta configuración no es la de mayor calidad disponible
—Whisper ofrece variantes más grandes y con mayor precisión que corren mejor en GPU—, y la
razón es una restricción de recursos ya documentada en el Cap. 5 y el Cap. 14: la GPU de 24 GB
está ocupada casi en su totalidad por el modelo de lenguaje principal (18,6 de los 24 GB, PDF
v9.1 Cap. 3), sin margen para cargar un segundo modelo de tamaño considerable. Ejecutar Whisper
en CPU con cuantización de 8 bits es la única configuración que no compite por VRAM con el
componente que domina la latencia del sistema.

Sobre el transcrito —habitualmente caótico, grabado entre series, con muletillas y sin
puntuación— opera un extractor (`extraer_triada_audio`) que busca la tríada ejercicio-peso-
repeticiones junto al esfuerzo percibido, apoyándose en `normalizar_numeros_es` (línea 422)
para resolver números dichos en palabras ("cien por cinco" → 100x5). La cobertura de este
extractor frente a formas habladas de RPE está cubierta por 46 casos deterministas
(`test_rpe_hablado.py`, cifra confirmada en F1.2), y devuelve un eco de confirmación al atleta
—la misma disciplina de "nunca confirmar sin resultado verificable" del Cap. 1 §1.2, aplicada
aquí a un canal con más ruido de entrada que el texto.

## 13.2 Proactividad: el cuestionario matutino y sus restricciones legales (F7.4)

### La ventana de 24 horas de Meta como restricción legal, no técnica

WhatsApp Business Cloud API prohíbe a una aplicación abrir una conversación con un usuario que
no le haya escrito en las últimas 24 horas, salvo mediante una plantilla de mensaje
pre-aprobada por Meta. El cuestionario matutino de disponibilidad (`iniciar_scheduler`,
`services/proactividad.py:177`, por defecto a las 6:30 —`hora=6, minuto=30`— verificado en el
código) usa exactamente ese mecanismo: es el único punto del sistema donde el coach inicia la
conversación sin que el atleta haya escrito primero, y solo puede hacerlo porque el mensaje es
una plantilla aprobada, no texto libre generado por el modelo. Esta restricción no es una
elección de diseño del proyecto — es una condición impuesta por la plataforma sobre la que
opera, y se documenta aquí explícitamente porque tiene implicaciones directas para el Cap. 18
(cumplimiento de terceros).

### Filtrado de destinatarios

`broadcast_readiness` (línea 167) excluye explícitamente a los atletas con el plan pausado y a
los que están en pleno proceso de onboarding — a ambos les preguntar por su "readiness" de hoy
no tiene sentido: uno no tiene sesión programada, el otro todavía no tiene plan. El PDF v9.1
(Cap. 13) ya documenta un hallazgo real sobre este filtro: la ruta del fichero de estado que
alimenta el filtro es relativa, no absoluta, y un arranque del servidor desde un directorio de
trabajo distinto al esperado lo dejaba sin encontrar el fichero — el error se absorbía en
silencio y el envío matutino simplemente no salía para nadie, sin ninguna señal de fallo
visible. Se cita aquí porque es un ejemplo claro de un modo de fallo silencioso —el mismo
patrón que motivó varias de las redes de validación del Cap. 7, aunque en una capa distinta del
sistema (infraestructura, no generación de lenguaje).

### El riesgo de duplicación con recarga automática

Un servidor de desarrollo con recarga automática (`--reload` o equivalente) levanta dos
procesos: el proceso supervisor y el proceso de trabajo real. Si el planificador se inicializa
en ambos, cada atleta recibe el mensaje matutino **dos veces**. La solución del proyecto —forzar
un único proceso de trabajo en producción, dado que el estado del planificador vive en memoria
y escalar horizontalmente significaría duplicar mensajes— es una limitación de arquitectura
explícita, no un descuido: el estado del scheduler no está diseñado para coordinarse entre
múltiples procesos o instancias.

## 13.3 El dataset que se alimenta solo: bucle de mejora continua y su riesgo (F7.5)

Cada vídeo que un atleta envía por WhatsApp se archiva automáticamente en
`data/dataset_recoleccion/<ejercicio_predicho>/` (verificado en esta sesión: el directorio
existe y contiene subcarpetas por ejercicio con vídeos reales de uso — ver `git status` al
inicio de esta sesión de documentación, que lista más de una docena de vídeos nuevos sin
commitear en `Sentadilla_` y `Peso_muerto_`). Es un mecanismo de mejora continua sin
intervención manual: el dataset de entrenamiento del clasificador (Cap. 12) crece con cada uso
real del sistema, no solo con sesiones de grabación dedicadas.

Este mecanismo tiene, sin embargo, una cara de riesgo que no está mitigada en el código
actual: **el vídeo se archiva bajo la carpeta del ejercicio que el propio clasificador
predijo**, no bajo una etiqueta verificada por un humano. Si el clasificador se equivoca —algo
que ocurre con una precisión medida del 80 % sobre un conjunto de prueba pequeño, Cap. 12
§12.3—, el vídeo mal etiquetado pasa a formar parte del conjunto de entrenamiento de la
siguiente ronda, reforzando el error en vez de corregirlo. No se encontró en el código ningún
paso de verificación humana o de descarte de predicciones de baja confianza antes de archivar
el vídeo para reentrenamiento — los vídeos marcados como "ejercicio desconocido" (Cap. 12
§12.1) sí quedan fuera de las cuatro carpetas de clase, pero cualquier predicción por encima
del umbral de 0,45, acertada o no, se usa igual. Se documenta aquí como un riesgo real de
retroalimentación de error no mitigado, candidato directo para el capítulo de limitaciones
(Cap. 19) y para el de consideraciones éticas si se considera el efecto acumulado sobre la
fiabilidad del sistema a largo plazo.
