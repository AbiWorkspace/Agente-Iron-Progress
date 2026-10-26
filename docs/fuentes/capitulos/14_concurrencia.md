# Cap. 14 · Concurrencia y rendimiento

> Estado: **hecho (borrador)** · Tarea: F6.10 · Fecha: 2026-08-27
> Ficheros fuente: `api/cola_mensajes.py` (301 líneas), `services/testbed/test_cola_mensajes.py`
> Ver también: `docs/fuentes/capitulos/f1_1_modelo_y_contexto.md` (F1.1)

---

## 14.1 Herencia del PDF v9.1

El capítulo 14 del PDF v9.1 documenta con precisión el cuello de botella de latencia (17,6 s
→ 1,2-3,5 s de preprocesado al dejar de podar la telemetría), la exclusión por turno mediante
semáforo (`SEM_LLM`), y el techo de razonamiento del modelo. Esa información no se repite
aquí; esta sección añade la cola de mensajes por atleta, un componente que no aparece descrito
en el PDF v9.1 pese a ser la pieza que resuelve un problema real de mensajería.

## 14.2 La cola FIFO por atleta con fusión de ráfaga

`api/cola_mensajes.py` mantiene una cola de tipo FIFO independiente por número de atleta, cada
una con su propio *worker* asíncrono (`_atender`). El problema que resuelve, documentado en el
propio código con fecha y causa (v14, líneas 56-65): en WhatsApp la gente parte una idea en
varios mensajes seguidos —el ejemplo textual del código es *"Ayyy ai cierto" / "Perdón" / "En
press banco con mancuernas"*—, y sin este mecanismo el *worker* arrancaba a procesar el primer
mensaje antes de que llegaran los siguientes, y el atleta recibía **dos respuestas del coach a
lo que era un solo pensamiento**.

### El mecanismo tiene dos capas, no una

1. **Fusión de un prefijo contiguo de textos ya en cola** (`_extraer`, líneas 155-173): al
   sacar el siguiente trabajo de la cola, si es fusionable (texto, no audio/vídeo) se
   incorporan hasta `MAX_FUSION_TEXTO = 5` mensajes de texto consecutivos que ya estuvieran
   esperando, cortando la fusión en cuanto aparece un audio o un vídeo — para no alterar el
   orden real de una conversación multimodal.
2. **Ventana de ráfaga de 2 segundos** (`_esperar_rafaga`, líneas 177-190), añadida
   específicamente porque la fusión de la capa 1 solo alcanzaba a lo que **ya** estuviera en
   cola cuando el turno terminaba de procesar el mensaje anterior — si el atleta seguía
   escribiendo mientras el *worker* estaba libre y a punto de arrancar, esos mensajes
   llegaban tarde para fusionarse. La ventana solo se activa cuando hay exactamente un texto
   fusionable esperando (con dos ya se fusionan solos; con un audio o vídeo delante, no hay
   ráfaga que agrupar), y se paga una sola vez por turno. El propio código cuantifica el
   coste-beneficio: *"frente a los 20-130 s que dura un turno es ruido, y evita el turno
   duplicado entero"*.

### Salvaguardas adicionales

- `MAX_EN_COLA = 20`: tope de mensajes en espera por atleta; por encima de ese volumen el
  código asume que no hay conversación que salvar —"alguien pegando texto o un bucle"— y
  descarta el resto con aviso al atleta.
- Aviso de cola ocupada (`AVISAR_EN_COLA`, activable/desactivable para pruebas A/B): si un
  mensaje entra detrás de otro trabajo del mismo atleta, se le informa en vez de dejarlo sin
  respuesta hasta que le toque turno.

## 14.3 Relación con la exclusión por turno del PDF v9.1

La cola por atleta y el semáforo de exclusión por turno (`SEM_LLM`, PDF v9.1 Cap. 14) resuelven
problemas complementarios, no el mismo problema dos veces: el semáforo garantiza que las 2-4
inferencias que componen el turno de **un mismo atleta** no se intercalen con las de **otro**
atleta (evitando el desalojo mutuo de la caché de prefijo, Cap. 2 §2.4); la cola garantiza que
los mensajes de **un mismo atleta** lleguen al orquestador como una unidad conversacional
coherente, no como fragmentos que generan respuestas duplicadas. Uno opera a nivel de acceso al
modelo; el otro, a nivel de entrada del canal — ambos son necesarios porque resuelven fallas
observadas en capas distintas del sistema.

## 14.4 Coste no medido de esta sesión

A diferencia de la latencia de inferencia (instrumentada exhaustivamente, PDF v9.1 Cap. 14),
no se encontró en el código ni en los bancos de `services/testbed/latencia/` una medición
específica del coste o beneficio de la ventana de ráfaga de 2 segundos en producción —cuántos
turnos duplicados evitó frente a cuántos segundos de latencia percibida añadió agregados sobre
un volumen real de mensajes—. Se señala como dependencia pendiente para el Cap. 16 (F8.2), no
como una cifra ya disponible.
