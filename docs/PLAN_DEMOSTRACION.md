# Plan de demostración — Iron Progress

> Guion operativo para enseñar el sistema completo en 20-25 minutos.
> Todas las cifras de tiempo de este documento están **medidas** en la máquina de desarrollo
> (AMD RX 7900 XTX, Qwen3.6 27B sobre Ollama) el 31 de julio de 2026.

---

## 1. El problema de partida y cómo queda resuelto

Media docena de funciones del sistema solo tienen algo que decir con meses de historial detrás.
Y una conversación que "suene a semanas" no se improvisa delante del público. Estos eran los
huecos y este es su estado:

| Hueco | Antes | Ahora | Cómo |
|---|---|---|---|
| Historial de entrenamiento | 59 series de un atleta de pruebas | **988 series, 16 semanas** | `sembrar_demo.py` |
| Biblioteca científica | **0 fragmentos** (2 herramientas muertas) | **3.811 fragmentos** de 997 páginas únicas | `crear_base_conocimiento.py` |
| Bitácora narrativa | **0 documentos**, sin escritor en todo el repo | **6 bitácoras** de mesociclos cerrados | `sembrar_conversacion.py` |
| Conversación previa | ninguna: el coach saludaba como el primer día | **24 mensajes** de pasado con sus herramientas | `sembrar_conversacion.py` |
| Cargas del plan | 8 de 18 ejercicios salían *peso por calibrar* | **18 de 18 con kilos reales** | marcas accesorias sembradas |
| Diagnóstico de nivel | "principiante" con 204 kg de peso muerto | **"intermedio"** | alias canónicos sembrados |
| Fiabilidad del sembrado | cambiaba en cada ejecución | **reproducible** | se eliminó `hash()` no determinista |
| Estado de fatiga | ACWR 0,39 — parecía un atleta que lo dejó | **ACWR 0,82**, zona óptima | el historial termina en pico de carga |

**Coste total de preparación: menos de dos minutos.**

---

## 2. Preparación (T-30 minutos)

```bash
# 1. Sembrar al atleta.  ~15 s (incluye generar los 3 mesociclos de verdad)
python3 scripts/sembrar_demo.py --limpiar

# 2. Sembrar su pasado con el coach.  ~3 s
python3 scripts/sembrar_conversacion.py

# 3. Pasar lista.  ~20 s — no salgas a demostrar sin ver 21/21 en verde
python3 scripts/verificar_demo.py

# 4. Arrancar
python3 main.py
```

### El calentamiento que no se puede saltar

El **primer turno del día tarda 71 s** y los siguientes 17-52 s. La diferencia es la carga del
modelo en memoria y la caché de prompt. Antes de que entre el público, manda **un mensaje
cualquiera** ("hola") y espera la respuesta. A partir de ahí el sistema va a ritmo de demo.

> Sin este paso, la primera pregunta delante del público se come más de un minuto de silencio.

---

## 3. El guion, escena por escena

El atleta de demostración es **5219999000001**: 84 kg, 26 años, powerbuilding, 5 días por semana,
16 semanas de historial, hoy le toca **Día 1 · Torso**.

---

### Escena 1 · "No es un chatbot, es un coach que te conoce" (2 min)

**Escribe:** `hola, ¿cómo va todo?`

Respuesta corta y en segundos. Aquí se enseña el **router de charla casual**: el sistema detecta
que no hay datos en el mensaje y apaga las 23 herramientas, el razonamiento y la recuperación.
No todo mensaje merece el mismo coste.

**Escribe:** `¿cómo fue mi mesociclo anterior?`

> *"El último fue de fuerza en junio: bajamos las repeticiones y subimos la intensidad.
> Respondiste muy bien con el tren inferior, pero la banca se estancó…"*

**Qué señalar:** eso no está en el hilo del chat. Sale de un índice vectorial de bitácoras de
largo plazo, recuperado por significado. El coach tiene memoria de mesociclos, no solo de mensajes.

---

### Escena 2 · La rutina del día, con kilos que nadie inventó (3 min)

**Escribe:** `qué me toca hoy?`

Sale la sesión completa: nueve ejercicios con series, repeticiones, RPE y **kilos exactos**.

```
*Semana 1 · Día 1 · Torso (HOY)*
• Press Banco Plano: 4x10 — *87.5 kg* @RPE 7.5
• Dominadas Lastradas: 4x10 — *20 kg* @RPE 7.5
• Press Pecho en Maquina: 2x11 — *67.5 kg* @RPE 8.0
...
```

**Qué señalar — este es el corazón del proyecto.** Abre `resources/rutinas_generadas/.../1_acumulamiento.json`
en la pantalla: **el plan no guarda kilos, guarda porcentajes** (`"1RM": "70.0%"`). Los 87,5 kg
salen de multiplicar ese porcentaje por la marca real del atleta y redondear a su incremento de
disco (2,5 kg). El modelo no calculó nada: transcribió.

**Escribe:** `y mañana qué toca?`

Obliga a la herramienta de consulta de rutina futura. El sistema **prohíbe deducir** un día a
partir de otro: con periodización ondulante, cada día es matemáticamente distinto.

---

### Escena 3 · Registrar entrenando, como se habla de verdad (3 min)

**Escribe:** `acabo de hacer la primera de banca, 100x5 y me quedaron 2 en el tanque`

**Qué señalar:** mira los logs mientras responde. Verás `[SERIE-AUTO] registradas:` **antes** de
que el modelo diga una palabra. Python parseó la serie, resolvió el ejercicio, comprobó que no
fuera un duplicado de los últimos 20 minutos, la escribió en SQLite y recalculó el 1RM. El modelo
solo recibió el hecho consumado y lo comunicó.

**Escribe:** `la segunda igual, 100 por 4`

No dice qué ejercicio es. El sistema lo hereda del turno anterior mediante un sello con ventana
de tiempo. Un coach de verdad tampoco pregunta "¿de qué ejercicio?" treinta segundos después.

---

### Escena 4 · La autorregulación: el sistema decide, no el modelo (3 min)

**Escribe:** `uf, la tercera me salió durísima. 100x5 pero rpe 9.5`

El motor predictivo recalcula lo que queda del día: baja la carga y recorta series, con un tope
de RPE. **Los kilos nuevos los calcula Python.**

**Qué señalar:** esto es autorregulación **del día**, no una descarga semanal. Son dos cosas
distintas y el prompt las separa explícitamente (reglas 10 y 11). Un coach que confunda ambas
arruina un mesociclo.

---

### Escena 5 · Cuando el sistema le lleva la contraria al atleta (3 min)

**Escribe:** `me siento reventado, llevo semanas apretando. ¿debería descargar?`

> *"Estás en zona segura. Tu tendencia de fuerza sube ligeramente (+2 %), el esfuerzo a cargas
> iguales sube poco (+0,46)… Mantén el volumen esta semana y reevalúa."*

**Qué señalar — la escena más importante para un público técnico.** El atleta pidió una descarga
y el sistema **se la negó con números**: 84 series duras esta semana contra 68 la anterior, e1RM
al alza, sin reportes de dolor. La regla es explícita: *nunca descargues a ciegas, primero el
análisis, después la decisión*. Un asistente complaciente habría dicho que sí.

**Escribe:** `¿y cuántas series de pecho hago a la semana?`

> *"Esta semana tienes prescritas *15 series para pecho*: Día 1: 6, Día 3: 3, Día 5: 6."*

Contar series es aritmética, y el prompt prohíbe al modelo deducirlas leyendo la rutina. Lo cuenta
una herramienta sobre el plan real.

---

### Escena 6 · Las tres memorias (2 min)

**Escribe:** `¿cuántas series por grupo muscular debería hacer para hipertrofia?`

Cita la **biblioteca científica**: 3.811 fragmentos de cuatro obras (Israetel et al. sobre dieta,
Helms/Valdez/Morgan, Schoenfeld, y un artículo de Jovanović sobre el libro de Rippetoe — no el
libro en sí) indexadas localmente.

**Escribe:** `oye, ya no me molesta nada el hombro`

El sistema **actualiza el expediente**. Y a partir de ahí deja de sacarlo. Puedes demostrarlo
preguntando después por un ejercicio de empuje.

**Qué señalar:** tres índices con propósitos distintos —expediente (lo que eres hoy), bitácora
(lo que fuiste) y biblioteca (lo que dice la ciencia)— y el prompt tiene una regla para elegir
entre ellos según la fuente del dato.

---

### Escena 7 · Multimodal: voz y vídeo (4 min)

**Manda una nota de voz:** *"acabo de terminar press banca, cien kilos por cinco, rpe ocho"*

Whisper transcribe en local y el sistema extrae la tríada `ejercicio + peso + reps @ RPE` de un
transcript hablado, con eco de confirmación.

**Manda un vídeo** de `data/dataset_recoleccion/Sentadilla_/` (hay 36 vídeos entre los tres
ejercicios).

**Qué señalar:** son **dos modelos distintos** trabajando sobre el mismo vídeo. MediaPipe extrae
la pose y un clasificador propio, entrenado sobre 23 variables articulares, identifica el
ejercicio (**15,8 s medidos** para un vídeo de 5,9 MB). Además se calculan métricas de VBT:
velocidad media, velocidad pico, pérdida de velocidad y rango de movimiento.

> **Aviso de tiempos:** el vídeo es la escena más lenta. Si vas justo, ten uno ya procesado y
> enseña el resultado en vez de esperarlo en directo.

---

### Escena 8 · Proactividad y gráficas (2 min)

**Escribe:** `mándame la gráfica de mi banca`

La gráfica se genera en **0,1 s** con Matplotlib y se envía como imagen: 32 sesiones, +8,5 kg de
e1RM en el periodo, 7 récords marcados.

**Qué señalar:** el sistema también abre conversación solo. Cada mañana a las 6:30 manda un
cuestionario de disponibilidad —sueño, estrés, dolor— y **solo a quien entrena ese día o lleva la
sesión atrasada**. Esa puntuación entra después en la recomendación de carga.

Para enseñarlo sin esperar a mañana:

```python
from services.proactividad import enviar_readiness
await enviar_readiness("5219999000001", "campeón")
```

---

### Escena 9 · Lo que hace que esto sea ingeniería y no un prompt (3 min)

Esta escena va **en la pantalla del código**, no en WhatsApp. Es la que convence a un evaluador
técnico.

**1. Las seis redes de validación.** Toda respuesta pasa por seis mallas antes de salir:
kilajes inventados, rutina fantasma, reparador de onboarding, afirmación falsa, estado del plan
contrastado contra disco, y anti-eco. Si una salta, se fuerza una reescritura honesta; si el
modelo reincide, contesta una plantilla determinista.

**2. El banco de QA.** 74 casos en 22 bloques que hablan con el orquestador completo:

```bash
SIM_NUMERO=5215500000777 python -m services.testbed.qa_casos_uso fatiga salud
```

Más 32 suites deterministas y 72 aserciones de humo.

**3. El secreto profesional.** Ningún nombre de función o tabla llega al chat.

---

## 4. Qué NO hacer delante del público

Fallos **conocidos y documentados** del sistema. Evítalos en el guion o preséntalos tú mismo como
limitación consciente — que es mucho mejor que los descubra el público.

| No hagas esto | Por qué |
|---|---|
| `"las 4 series me salieron con 30 kg y 10 reps"` | La herramienta registra **una** serie por llamada. El coach confirma las cuatro y solo se guarda una: se pierde el 75 % del volumen. |
| `"si quiero un 1RM de 140, ¿cuántas reps con 110?"` | No existe cálculo inverso. El modelo rompe la prohibición de calcular, salta el guardia de afirmación falsa y el turno se va a **25 s extra** de reserva. |
| Jerga rara o mal escrita (`"precinclinado"`) | Alucinación cruzada de ejercicio: puede registrar el récord en el músculo equivocado. |
| Pedir algo para lo que no hay datos | El guardia de afirmación falsa tiene falsos positivos con frases honestas del tipo "aún no tengo suficiente historial **registrado**", y fuerza una respuesta fría de reserva. |
| Dos mensajes seguidos sin esperar respuesta | Entran en la cola y se responden en orden, pero el segundo espera al primero: parece que se ha colgado. |

### Fugas de jerga observadas

En las pruebas, el coach dejó escapar `(MRV)` y `"los porcentajes del 70 %"`. No es grave, pero si
lo hace delante del público, **apróvechalo**: es la ocasión de explicar qué es el volumen máximo
recuperable. No lo trates como un fallo.

---

## 5. Plan B

| Si falla… | Haz esto |
|---|---|
| El túnel o WhatsApp | Corre el simulador: `python -m services.testbed.simular_conversacion`. Es el orquestador completo, sin Meta de por medio. |
| Una respuesta tarda demasiado | Enseña los logs mientras: se ve el interceptor actuando, las herramientas elegidas y las redes de validación. Es más interesante que la respuesta. |
| El modelo dice una tontería | Es un LLM de 27B en local. Enséñalo, di qué red debería haberlo cazado y sigue. La honestidad sobre los límites suma. |
| Se corrompe el estado | `python3 scripts/sembrar_demo.py --limpiar && python3 scripts/sembrar_conversacion.py` — 20 segundos y vuelves a empezar. |

---

## 6. Chuleta de cifras

Para responder sin dudar si preguntan:

- **23 herramientas** expuestas al modelo · **17 reglas** de decisión en el prompt
- **≈14 interceptores** deterministas · **6 redes** de validación de salida
- **7.749 líneas** el orquestador · **≈28.500** el proyecto entero
- **40.960 tokens** de ventana de contexto
- **7 tablas** SQLite · **3 colecciones** vectoriales · **3.811 fragmentos** de literatura
- **74 casos** de QA en 22 bloques · **32 suites** deterministas · **72 aserciones** de humo
- **Latencia:** 71 s el primer turno en frío, 17-52 s después. El decodificado domina
  (medido: prefill 26.150 tokens en 5,9 s a 4.446 tok/s; decodificado 1.348 tokens en 42,7 s a 31,6 tok/s)
- **Generar 3 mesociclos: 15 s.** El sistema promete "2-3 minutos" al atleta: es conservador.
- **Todo en local.** Ningún dato del atleta sale de la máquina.
