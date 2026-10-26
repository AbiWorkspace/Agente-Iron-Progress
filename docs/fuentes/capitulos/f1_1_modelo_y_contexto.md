# F1.1 — Reconciliación: modelo de inferencia y ventana de contexto

> Estado: **hecho** · Fecha: 2026-08-27
> Fuente: `services/llm_service.py:1511`, `:1580-1601`; `Modelfile`;
> `scripts/resources/Entrenamiento_llm_peticion.py:193-197`; `ollama show agent:latest`
> (consulta en vivo al servidor Ollama activo en `localhost:11434`).

## Veredicto

**No hay contradicción real — hay dos capas de configuración que el PDF v9.1 presentó como
si fueran una sola cifra.** El `Modelfile` fija el contexto "en reposo" del modelo (16.384
tokens); cada llamada real desde el código pide explícitamente 40.960 y Ollama honra esa
petición recargando el modelo con esa ventana. La tesina debe describir ambas capas, no
elegir una.

## Evidencia

1. **El modelo real corriendo es `qwen3.8:27b`**, no "Qwen3.6" como dice el PDF v9.1. Se
   confirma en `services/llm_service.py:1511` (`MODELO_LLM = "qwen3.8:27b"`) y en la
   respuesta en vivo de `ollama show agent:latest` (el modelo servido en producción es una
   variante afinada — `agent:latest` — cuyo `parent_model` es `Qwen3.8:27b`).
2. **El `Modelfile` del repositorio declara `PARAMETER num_ctx 16384`.** Es el valor con el
   que Ollama carga el modelo por defecto si nadie pide otra cosa — se confirmó
   consultando `ollama show agent:latest`, que devuelve ese mismo 16.384 como parámetro
   "en reposo".
3. **El código nunca deja que se use ese valor por defecto.** Tanto `llm_service.py` (línea
   1597, `"num_ctx": 40960`) como el generador de planes
   (`Entrenamiento_llm_peticion.py:197`, `options={"num_ctx": 40960, ...}`) pasan el
   contexto explícitamente en cada petición a la API `/api/chat` de Ollama. Cuando el
   `num_ctx` solicitado difiere del que el modelo tiene cargado, Ollama **recarga** el
   modelo con la nueva ventana — es exactamente el comportamiento que el propio código
   documenta y teme en un comentario de `llm_service.py` (línea ~1512): *"usa el MISMO
   nombre + num_ctx en el servicio generador, o tendrás dos modelos rotando en 24 GB y
   vuelve el thrashing de recargas"*.
4. **El valor 40.960 no es arbitrario: está fechado y medido.** El comentario de
   `llm_service.py` (líneas ~1590-1601) documenta la progresión real: 16.384 → 32.768 (para
   que cupieran dos prompts en la caché de Ollama y no se desalojaran entre sí) → 40.960,
   este último tras medir en el banco de QA de 74 turnos (30 de julio de 2026) que el prompt
   real llegaba a 32.699 de los 32.768 tokens disponibles, dejando 69 tokens para responder
   — causa directa de 5 de 7 respuestas cortadas a media frase. Subir a 40.960 dejó ~8.000
   tokens de margen, a un coste medido de ~512 MiB adicionales de VRAM.

## Por qué el `Modelfile` queda en 16.384 y no se sube a 40.960

No se investigó una razón documentada en el código para esta asimetría; es plausible que
sea simplemente un valor de arranque conservador que nunca se actualizó porque en la
práctica el override por API lo hace irrelevante. **Se recomienda** (fuera del alcance de
este plan de documentación, es una decisión de ingeniería) igualar el `Modelfile` a 40.960
para que un arranque en frío sin la ruta habitual de `llm_service.py` no cargue el modelo
con una ventana insuficiente por accidente.

## Redacción para la tesina (§5.2, entorno experimental / Cap. 6 el contrato con el modelo)

> El sistema sirve **Qwen3.8 27B** a través de Ollama. El fichero `Modelfile` fija una
> ventana de contexto base de 16.384 tokens, pero todas las llamadas de la aplicación
> —tanto el orquestador conversacional como el generador de mesociclos— solicitan
> explícitamente 40.960 tokens en cada petición, lo que fuerza a Ollama a recargar el
> modelo con esa ventana ampliada. Ambos puntos de llamada deben coincidir exactamente: si
> se solicitan valores distintos desde distintas partes del código, Ollama alterna recargas
> completas del modelo en la GPU, con un coste medido de recarga de varios segundos por
> turno (véase Cap. 14, Concurrencia y rendimiento).
