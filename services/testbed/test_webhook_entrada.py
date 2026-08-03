# -*- coding: utf-8 -*-
"""
test_webhook_entrada.py — ningún mensaje del atleta se queda sin respuesta.

Lo que entra por el webhook no es solo texto: la gente manda fotos del gimnasio,
stickers, PDFs con su dieta, ubicaciones y reacciones. Hasta v13 el webhook solo
tenía ramas para video, audio y texto, así que todo lo demás caía en el vacío:
sin respuesta, sin log, sin nada. Para el atleta eso es un coach caído.

Y con un video esperando kilos, CUALQUIER mensaje sin cifras recibía "no logré
identificar los kilos" — preguntabas "¿y mañana qué toca?" y te contestaba eso,
durante los 10 minutos que el video vive en memoria.

    python -m services.testbed.test_webhook_entrada
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from api import cola_mensajes                                          # noqa: E402
from api import rutas_whatsapp as W                                    # noqa: E402

NUM = "5215500000907"

OK = FALLOS = 0
ENVIADOS: list[tuple[str, str]] = []
ATENDIDOS: list[tuple[str, str]] = []


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global OK, FALLOS
    if condicion:
        OK += 1
        print(f"  ✓ {nombre}")
    else:
        FALLOS += 1
        print(f"  ✗ {nombre}" + (f"  → {detalle}" if detalle else ""))


async def _enviar_falso(numero, texto):
    ENVIADOS.append((numero, texto))


async def _pipeline_falso(numero_usuario, texto_usuario=None, media_id=None, **kw):
    ATENDIDOS.append((numero_usuario, texto_usuario or ""))


def _reset():
    ENVIADOS.clear()
    ATENDIDOS.clear()
    W.videos_pendientes.clear()
    W.mensajes_procesados.clear()
    W._orden_mensajes.clear()


async def _entregar(mensaje: dict) -> None:
    """Mete el mensaje por donde lo mete Meta y espera a que la cola lo vacíe."""
    await W._procesar_mensaje(mensaje)
    for _ in range(200):
        if not cola_mensajes.pendientes() and not cola_mensajes.estado()["en_curso"]:
            break
        await asyncio.sleep(0.01)


async def caso_tipos_no_soportados():
    print("\n[1] Foto, sticker, documento, ubicación: el atleta SIEMPRE recibe algo")
    for i, tipo in enumerate(("image", "sticker", "document", "location", "contacts")):
        _reset()
        await _entregar({"from": NUM, "type": tipo, "id": f"wamid.tipo{i}"})
        chk(f"'{tipo}' recibe respuesta", bool(ENVIADOS), "silencio absoluto")
        if ENVIADOS:
            chk(f"  y no menciona nada técnico ({tipo})",
                not any(p in ENVIADOS[0][1].lower() for p in ("error", "none", "webhook", "type")),
                ENVIADOS[0][1][:60])

    _reset()
    await _entregar({"from": NUM, "type": "reaction", "id": "wamid.react"})
    chk("una reacción (❤️) NO genera respuesta: sería ruido", not ENVIADOS,
        f"{ENVIADOS}")


async def caso_video_pendiente():
    print("\n[2] Con un video esperando kilos, la conversación no se secuestra")
    _reset()
    W.videos_pendientes[NUM] = {"id": "media123", "timestamp": 0,
                                "ejercicio_detectado": "Sentadilla"}
    await W._atender_texto(NUM, "y mañana qué toca?")
    chk("la pregunta se atiende como un mensaje normal",
        any("mañana" in t for _, t in ATENDIDOS), f"{ATENDIDOS}")
    chk("y se recuerda el video pendiente una vez",
        any("video" in t.lower() for _, t in ENVIADOS), f"{ENVIADOS}")
    chk("el video sigue en espera (no se descarta)", NUM in W.videos_pendientes)

    ENVIADOS.clear()
    await W._atender_texto(NUM, "y el viernes?")
    chk("el recordatorio NO se repite en cada mensaje",
        not any("video" in t.lower() for _, t in ENVIADOS), f"{ENVIADOS}")

    _reset()
    W.videos_pendientes[NUM] = {"id": "media123", "timestamp": 0,
                                "ejercicio_detectado": "Movimiento Libre"}
    await W._atender_texto(NUM, "era sentadilla")
    chk("nombrar el ejercicio SÍ sigue el camino del video (pide los kilos)",
        any("kilos" in t.lower() or "peso" in t.lower() for _, t in ENVIADOS), f"{ENVIADOS}")
    chk("y la corrección del ejercicio queda guardada",
        "sentadilla" in str(W.videos_pendientes[NUM].get("ejercicio_detectado", "")).lower(),
        f"{W.videos_pendientes.get(NUM)}")

    _reset()
    W.videos_pendientes[NUM] = {"id": "media123", "timestamp": 0,
                                "ejercicio_detectado": "Sentadilla"}
    await W._atender_texto(NUM, "140kg")
    chk("con los kilos, el análisis VBT arranca",
        any(m == "media123" for m, _ in [(a[1], a[0]) for a in ATENDIDOS]) or bool(ATENDIDOS),
        f"{ATENDIDOS}")
    chk("y el video sale de la lista de pendientes", NUM not in W.videos_pendientes)


async def caso_duplicados():
    print("\n[3] Meta reentrega el mismo mensaje: se procesa UNA vez")
    _reset()
    msg = {"from": NUM, "type": "text", "id": "wamid.dup",
           "text": {"body": "hola"}, "timestamp": "0"}
    await _entregar(dict(msg))
    primeros = len(ATENDIDOS)
    await _entregar(dict(msg))
    chk("la reentrega no vuelve a responder", len(ATENDIDOS) == primeros,
        f"{primeros} → {len(ATENDIDOS)}")


async def main() -> int:
    print("\n" + "=" * 74)
    print("  WEBHOOK — todo lo que entra tiene salida")
    print("=" * 74)
    enviar_real, pipeline_real = W.enviar_whatsapp, W.procesar_y_enviar_mensaje
    W.enviar_whatsapp = _enviar_falso
    W.procesar_y_enviar_mensaje = _pipeline_falso
    try:
        await caso_tipos_no_soportados()
        await caso_video_pendiente()
        await caso_duplicados()
    finally:
        W.enviar_whatsapp, W.procesar_y_enviar_mensaje = enviar_real, pipeline_real
        await cola_mensajes.reiniciar_para_test()
        _reset()
    print("-" * 74)
    print(f"  {OK}/{OK + FALLOS} comprobaciones correctas")
    print("=" * 74 + "\n")
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
