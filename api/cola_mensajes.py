# -*- coding: utf-8 -*-
"""
cola_mensajes.py — Iron Progress v13: una cola por atleta, en orden, sin perder nada.

Por qué existe
--------------
El webhook usaba `BackgroundTasks` de FastAPI, que NO es una cola:

  1. Las tareas de una MISMA entrega se ejecutan en serie (Starlette hace
     `for task in tasks: await task()`). Meta agrupa mensajes de varios atletas en
     una sola entrega, así que un video de 2 minutos de A dejaba esperando el
     "hola" de B detrás.
  2. Cuando un atleta ya tenía trabajo en curso, su mensaje nuevo se DESCARTABA en
     silencio (`usuarios_procesando`). El que reportaba "sentadilla 140x5" y luego
     "@8" perdía el segundo sin enterarse.
  3. Ese filtro además tenía una carrera: el chequeo estaba en el handler del
     webhook, pero el `add` ocurría dentro de la tarea de fondo, que corre DESPUÉS
     de responder. Dos entregas seguidas del mismo atleta pasaban ambas.

Qué hace este módulo
--------------------
Una cola FIFO **por atleta** con un worker propio. Los mensajes de un atleta se
atienden en el orden que llegaron y ninguno se tira; los de atletas distintos
avanzan en paralelo (hasta donde deje el semáforo del modelo, que sigue mandando
en `llm_service`).

Efecto lateral que arregla un bug real: el video y el texto con los kilos ya no
pueden solaparse. Antes, si "140kg" llegaba antes de que terminara la predicción
rápida del video, el `pop` de `videos_pendientes` sacaba una entrada todavía sin
`ejercicio_detectado` y el análisis corría como "Movimiento Libre".

Lo que este módulo NO hace (es la Fase 3 del plan)
--------------------------------------------------
No hay durabilidad: la cola vive en memoria. Un reinicio pierde lo pendiente, y
como Meta ya recibió el 200 no lo reintenta. `drenar()` cubre el apagado
ordenado; una caída seca, no. Para eso hace falta persistir la cola en SQLite.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

# Tope de mensajes en espera por atleta. Con más que esto no hay conversación que
# salvar: es alguien pegando texto o un bucle. Se le avisa y se descarta el resto.
MAX_EN_COLA = 20

# Cuántos mensajes de texto CONTIGUOS se fusionan en un solo turno. Si el atleta
# escribe "sentadilla 140x5" y luego "@8" mientras el coach piensa, tratarlos como
# dos turnos es peor que leerlos juntos: es lo que haría un coach humano al volver
# al teléfono. Tope para que una ráfaga larga no monte un prompt gigante.
MAX_FUSION_TEXTO = 5

# v14 — VENTANA DE RÁFAGA. En WhatsApp la gente parte una idea en 2-3 mensajes
# seguidos ("Ayyy ai cierto" / "Perdón" / "En press banco con mancuernas"), y el
# worker arrancaba con el primero antes de que llegaran los demás: el atleta
# recibía DOS respuestas del coach a lo que era un solo pensamiento. La fusión ya
# existía, pero solo alcanzaba a lo que estuviera en cola cuando el turno acababa.
#
# Este margen se paga UNA vez y solo cuando hay un único texto esperando; frente a
# los 20-130 s que dura un turno es ruido, y evita el turno duplicado entero.
# A 0 se desactiva.
VENTANA_RAFAGA_S = float(os.environ.get("IRON_VENTANA_RAFAGA", "2.0"))

# Aviso al atleta cuando su mensaje entra detrás de otro trabajo suyo. Interruptor
# para A/B: ponlo en False y la cola trabaja en silencio.
AVISAR_EN_COLA = True
TEXTO_AVISO_EN_COLA = "Voy con lo tuyo bro, dame un momento 🙏"
TEXTO_COLA_LLENA = ("Tengo demasiados mensajes tuyos en fila bro 😅 Dame un rato "
                    "para ponerme al día y me escribes de nuevo.")


@dataclass(slots=True)
class Trabajo:
    """Una unidad de trabajo ya resuelta: el webhook decide QUÉ hacer, la cola
    decide CUÁNDO.

    `ejecutar` es una closure sin argumentos para que este módulo no tenga que
    conocer las firmas de los pipelines del webhook (ni importarlos: eso crearía
    un ciclo, porque el webhook importa esta cola).

    `rehacer` solo lo rellenan los trabajos de texto fusionables: recibe el texto
    combinado y devuelve un Trabajo equivalente. Sin él no habría forma de
    coalescer sin que la cola supiera cómo se construye cada pipeline.
    """
    numero: str
    tipo: str                                   # "texto" | "audio" | "video" | "otro"
    etiqueta: str
    ejecutar: Callable[[], Awaitable[None]]
    texto: str = ""
    rehacer: Callable[[str], "Trabajo"] | None = None
    encolado_en: float = field(default_factory=time.monotonic)

    @property
    def fusionable(self) -> bool:
        return self.tipo == "texto" and self.rehacer is not None and bool(self.texto)


@dataclass(slots=True)
class Recibo:
    """Qué pasó al encolar, para que el webhook sepa si avisar al atleta."""
    aceptado: bool
    en_espera: bool = False      # había trabajo suyo por delante
    avisar: bool = False         # conviene mandarle el acuse de recibo


# ==============================================================================
# ESTADO DE LA COLA
# ==============================================================================
_colas: dict[str, deque[Trabajo]] = {}
_workers: dict[str, asyncio.Task] = {}
_en_curso: dict[str, str] = {}          # numero -> etiqueta de lo que se atiende
_avisados: set[str] = set()             # ya recibió el acuse en esta ráfaga

# Un solo lock para TODO el registro (colas, workers, en_curso). Es lo que cierra
# la carrera que tenía `usuarios_procesando`: crear la cola, meter el trabajo y
# decidir si hace falta arrancar un worker ocurre de forma atómica.
_lock = asyncio.Lock()


async def encolar(trabajo: Trabajo) -> Recibo:
    """Mete el trabajo en la cola del atleta y arranca su worker si no lo tiene."""
    numero = trabajo.numero
    async with _lock:
        cola = _colas.setdefault(numero, deque())
        if len(cola) >= MAX_EN_COLA:
            print(f"[COLA] {numero}: cola llena ({len(cola)}); se descarta "
                  f"{trabajo.etiqueta}")
            return Recibo(aceptado=False)

        # En espera = ya tiene algo suyo delante (en la cola o ejecutándose).
        en_espera = bool(cola) or numero in _en_curso
        cola.append(trabajo)

        avisar = False
        if en_espera and AVISAR_EN_COLA and numero not in _avisados:
            _avisados.add(numero)
            avisar = True

        worker = _workers.get(numero)
        if worker is None or worker.done():
            # Referencia fuerte en _workers: asyncio solo guarda referencias
            # débiles a las tareas y el recolector puede llevarse una a medias.
            _workers[numero] = asyncio.create_task(_atender(numero))

        print(f"[COLA] {numero}: + {trabajo.etiqueta} "
              f"(en cola: {len(cola)}, en curso: {_en_curso.get(numero, '-')})")

    return Recibo(aceptado=True, en_espera=en_espera, avisar=avisar)


def _extraer(cola: deque[Trabajo]) -> Trabajo:
    """Saca el siguiente trabajo, fusionando los textos contiguos que lo sigan.

    Solo fusiona un prefijo CONTIGUO de textos: si detrás hay un audio o un video,
    se corta ahí y se respeta el orden real de la conversación.
    """
    trabajo = cola.popleft()
    if not trabajo.fusionable:
        return trabajo

    piezas = [trabajo.texto]
    while len(piezas) < MAX_FUSION_TEXTO and cola and cola[0].fusionable:
        piezas.append(cola.popleft().texto)

    if len(piezas) == 1:
        return trabajo

    print(f"[COLA] {trabajo.numero}: {len(piezas)} mensajes de texto seguidos "
          f"se atienden como un solo turno")
    return trabajo.rehacer("\n".join(piezas))    # type: ignore[misc]


async def _esperar_rafaga(numero: str) -> None:
    """Da un margen a que lleguen los mensajes que el atleta sigue escribiendo.

    Solo espera cuando lo único pendiente es UN texto: con dos ya se fusionan
    solos, y con un audio o un vídeo delante no hay ráfaga que agrupar.
    """
    if VENTANA_RAFAGA_S <= 0:
        return
    async with _lock:
        cola = _colas.get(numero)
        if not cola or len(cola) != 1 or not cola[0].fusionable:
            return
    await asyncio.sleep(VENTANA_RAFAGA_S)


async def _atender(numero: str) -> None:
    """Worker de UN atleta: vacía su cola en orden y muere cuando no queda nada."""
    while True:
        await _esperar_rafaga(numero)
        async with _lock:
            cola = _colas.get(numero)
            if not cola:
                # Comprobado BAJO EL LOCK: nadie puede encolar entre esta
                # comprobación y el borrado, así que no se queda trabajo huérfano
                # sin worker que lo atienda.
                _colas.pop(numero, None)
                _workers.pop(numero, None)
                _avisados.discard(numero)
                return
            trabajo = _extraer(cola)
            _en_curso[numero] = trabajo.etiqueta

        espera = time.monotonic() - trabajo.encolado_en
        if espera > 1.0:
            print(f"[COLA] {numero}: {trabajo.etiqueta} esperó {espera:.1f}s en cola")

        try:
            await trabajo.ejecutar()
        except asyncio.CancelledError:
            # Apagado: se devuelve el trabajo al frente para que `drenar` o el
            # siguiente arranque lo vean, y se propaga (nunca se traga).
            async with _lock:
                _colas.setdefault(numero, deque()).appendleft(trabajo)
                _en_curso.pop(numero, None)
            raise
        except Exception as e:
            # Un fallo procesando UN mensaje no puede matar el worker: los
            # siguientes del mismo atleta tienen que seguir atendiéndose.
            print(f"[COLA] {numero}: error en {trabajo.etiqueta} -> "
                  f"{type(e).__name__}: {e}")
        finally:
            _en_curso.pop(numero, None)


# ==============================================================================
# OPERACIÓN
# ==============================================================================
def pendientes(numero: str | None = None) -> int:
    """Trabajos en espera (sin contar el que se esté ejecutando)."""
    if numero is not None:
        return len(_colas.get(numero) or ())
    return sum(len(c) for c in _colas.values())


def tiene_trabajo(numero: str) -> bool:
    """¿Este atleta tiene algo suyo en marcha o esperando?

    Lo usan los avisos que NO pasan por la cola (el acuse de un sticker, una
    foto o un PDF): salen al instante y, con un turno en vuelo, se cuelan por
    delante de la respuesta que el atleta está esperando.
    """
    return bool(_colas.get(numero)) or numero in _en_curso


def estado() -> dict:
    """Foto para el health check: quién está esperando y a qué."""
    return {
        "atletas_en_cola": len(_colas),
        "mensajes_en_espera": pendientes(),
        "en_curso": dict(_en_curso),
        "detalle": {n: len(c) for n, c in _colas.items() if c},
    }


async def drenar(timeout: float = 30.0) -> int:
    """Espera a que las colas se vacíen antes de apagar. Devuelve lo que quedó.

    Sin esto, un reinicio ordenado (systemd restart, Ctrl-C) tiraba en silencio
    todos los mensajes ya aceptados con un 200. No cubre una caída seca: para eso
    hace falta persistir la cola (Fase 3).
    """
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if not pendientes() and not _en_curso:
            return 0
        await asyncio.sleep(0.2)

    quedan = pendientes() + len(_en_curso)
    if quedan:
        print(f"[COLA] apagando con {quedan} trabajo(s) sin terminar: "
              f"{estado()}")
    return quedan


async def cancelar_todo() -> None:
    """Cancela los workers vivos. Solo para el apagado, después de `drenar`."""
    async with _lock:
        tareas = [t for t in _workers.values() if not t.done()]
    for t in tareas:
        t.cancel()
    for t in tareas:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass


async def reiniciar_para_test() -> None:
    """Deja el módulo limpio entre pruebas. NO usar en producción."""
    await cancelar_todo()
    async with _lock:
        _colas.clear()
        _workers.clear()
        _en_curso.clear()
        _avisados.clear()
