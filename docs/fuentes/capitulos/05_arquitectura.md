# Cap. 5 · Arquitectura del sistema

> Estado: **hecho (borrador)** · Tareas: F6.1, F6.2 · Fecha: 2026-08-27
> Ficheros fuente: `api/rutas_whatsapp.py` (954 líneas), `api/cola_mensajes.py` (301 líneas),
> `main.py`, `requirements.txt`

---

## 5.1 Arquitectura en capas

El sistema se organiza en cinco capas con dependencias unidireccionales: entrada/API,
orquestación, motores de dominio, y persistencia, con el canal de WhatsApp como frontera de
entrada y salida. El orquestador (`services/llm_service.py`) es deliberadamente el único punto
de acoplamiento: los motores de dominio (planificación, predictivo, proactividad) no se
conocen entre sí ni conocen la existencia de WhatsApp — toda coordinación pasa por él. Esta
descripción hereda y no repite la Figura 2 y 3 del PDF v9.1 (arquitectura en capas y mapa de
acoplamiento), que siguen siendo válidas; esta sección añade la justificación de cada elección
tecnológica, ausente en el documento original.

## 5.2 Justificación de las elecciones tecnológicas

| Decisión | Alternativa considerada | Por qué se descartó la alternativa |
|---|---|---|
| **SQLite** para series y perfil | PostgreSQL / MySQL | El sistema opera con concurrencia de escritura baja (un semáforo serializa la inferencia por diseño, Cap. 14) y un único proceso de trabajo. Un motor cliente-servidor añadiría un componente de infraestructura a operar y asegurar sin resolver ningún cuello de botella real: el cuello de botella medido está en la inferencia del LLM (Cap. 14), no en la base de datos. SQLite además simplifica el despliegue local-first que exige la restricción de privacidad del Cap. 1 §1.3. |
| **ChromaDB** para las colecciones vectoriales | FAISS, Milvus, pgvector | FAISS es una librería de índice, no un almacén persistente con gestión de colecciones — habría exigido construir esa capa a mano. Milvus es un sistema distribuido, sobredimensionado para tres colecciones de bajo volumen (3.811 fragmentos en la mayor, F1.2) en una sola máquina. ChromaDB ofrece persistencia en disco, colecciones con nombre y una integración directa con LangChain que evitó escribir el pipeline de embeddings desde cero. |
| **Ollama** para servir el modelo | llama.cpp directo, vLLM, TGI | vLLM y TGI están optimizados para servir múltiples réplicas o lotes grandes en GPU de centro de datos; en una sola GPU de 24 GB con una única inferencia activa a la vez (Cap. 14), esa ventaja no aplica. Ollama expone la gestión de modelos (carga, descarga, caché de prefijo) sobre llama.cpp con una API HTTP simple, evitando reimplementar esa capa de gestión que sí se necesita —cambiar de modelo, controlar `num_ctx` por petición— sin el coste operativo de un servidor de inferencia de producción. |
| **FastAPI + Uvicorn** para la API | Flask, Django | El webhook de WhatsApp exige responder de inmediato y procesar en segundo plano (Cap. 5.3); FastAPI ofrece `BackgroundTasks` y soporte async nativo sin plugins adicionales, y el equipo ya lo usa en el resto de servicios internos (`httpx` con pool compartido, Cap. 5.3). |
| **APScheduler** para el envío proactivo | Cron del sistema operativo, Celery beat | El planificador necesita compartir estado en memoria de proceso con el resto de la aplicación (el estado de qué atletas tienen sesión hoy) sin la complejidad operativa de una cola de tareas externa como Celery, que exigiría un broker adicional (Redis/RabbitMQ) para un caso de uso de un solo disparo diario. |

## 5.3 Atributos de calidad

| Atributo | Cómo se sostiene en la arquitectura | Evidencia |
|---|---|---|
| **Privacidad** | Ningún dato del atleta sale de la máquina: inferencia, embeddings, transcripción y visión corren en local | Stack tecnológico completo (F5.2), sin llamadas salientes salvo la API de WhatsApp/Meta |
| **Auditabilidad** | Toda cifra que llega al atleta es trazable a código determinista o a una herramienta con resultado registrado | Cap. 1 §1.2, seis redes de validación (Cap. 7) |
| **Disponibilidad ante fallos parciales** | El webhook responde de inmediato y encola el procesamiento; un fallo en una etapa (p. ej. la transcripción de audio) no bloquea la recepción de nuevos mensajes | `api/rutas_whatsapp.py`: 35 bloques `try/except` distribuidos por función, uno por punto de fallo externo (API de Meta, modelo de audio, modelo de visión) |
| **Recuperabilidad del estado** | El sistema asume que el estado en disco puede estar corrupto (edición manual, escritura interrumpida) y lo repara en el arranque de cada turno, no solo en el arranque del proceso | Cap. 11 del PDF v9.1 (integridad y reparación); confirmado en el bloque "Preparación" del Cap. 6 §6.1 de este documento |
| **Coste operativo bajo** | Todos los componentes (SQLite, ChromaDB embebido, Ollama) corren en el mismo proceso o en procesos locales sin infraestructura de red adicional | §5.2 de este capítulo |

## 5.4 Ciclo de vida de un mensaje: manejo de errores por etapa

Las quince etapas descritas en el PDF v9.1 (Cap. 4) cubren el flujo de éxito. Esta sección
documenta qué ocurre cuando una etapa falla, verificado contra `api/rutas_whatsapp.py`:

| Etapa que puede fallar | Comportamiento ante el fallo |
|---|---|
| Validación de firma HMAC | Si el secreto no está configurado, el webhook acepta cualquier petición (configuración válida en desarrollo, riesgo explícito en producción — PDF v9.1, Cap. 4). Si el secreto existe y la firma no coincide, la petición se rechaza antes de tocar ningún dato del atleta. |
| Descarga de medios (audio/vídeo) desde la Graph API de Meta | Encapsulada en `try/except` propio; un fallo de red no propaga una excepción no controlada al bucle de eventos — se registra y el atleta recibe un mensaje de error genérico en vez de quedarse sin respuesta. |
| Transcripción de audio (Whisper) | `_transcribir_adelantado` se lanza como tarea adelantada (`lanzar_transcripcion`) para solapar la descarga con el cómputo; un fallo del modelo de audio no bloquea el resto del turno — el sistema tiene un camino de `filtrar_audio_triada` que opera sobre lo que sí se transcribió. |
| Clasificación de vídeo / motor VBT | Al ser un trabajo limitado por CPU con un cerrojo de serialización (PDF v9.1, Cap. 12, "el objeto de estimación de pose no es reentrante"), un fallo de un atleta no corrompe el análisis de otro — el cerrojo aísla el fallo a un único turno. |
| Inferencia principal (`chat_llm`) | Con `TIMEOUT_CHAT = 180.0` (`llm_service.py`), ninguna llamada al modelo puede colgar el orquestador indefinidamente; ante timeout o respuesta truncada, el mecanismo de rescate (`_forzar_respuesta_texto`, Cap. 6 §6.1) reintenta sin herramientas ni razonamiento. |
| Envío a la Graph API de Meta | `enviar_whatsapp` es la última etapa; si falla el envío tras haber persistido ya la respuesta en el historial, existe una divergencia potencial entre "lo que el sistema cree haber dicho" y "lo que el atleta recibió" — **no se encontró en el código un mecanismo de reconciliación explícito para este caso concreto**; se documenta aquí como hallazgo para la hoja de ruta (Cap. 19), no como algo ya resuelto. |

Este último punto — ausencia de reconciliación tras un fallo de envío — es un hallazgo nuevo
de esta sesión de documentación, no recogido en el PDF v9.1 ni en el capítulo de deuda técnica
existente. Se traslada al inventario de limitaciones del Cap. 19.
