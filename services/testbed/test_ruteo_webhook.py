# -*- coding: utf-8 -*-
"""
test_ruteo_webhook.py — el webhook encola el trabajo correcto para cada mensaje.

Por qué existe
--------------
`_procesar_mensaje` es el reparto de TODO lo que entra: texto, audio, video,
botones del readiness, reentregas de Meta, mensajes viejos. Al pasar de
`BackgroundTasks` a la cola por atleta se reescribió entero, y sus fallos son
silenciosos por diseño: cada `return` descarta un mensaje sin avisar a nadie
(está hecho así a propósito para que un payload raro no tumbe el lote). Si el
ruteo se equivoca, el síntoma es un atleta al que el coach no le contesta, sin
una sola línea de error en el log.

Lo que verifica
---------------
1. TEXTO         un mensaje normal encola un turno de chat.
2. AUDIO         encola el pipeline de Whisper, no un turno de texto.
3. VIDEO         sin kilos en el caption deja el video pendiente y encola la
                 predicción rápida; con kilos va directo al pipeline VBT.
4. READINESS     el botón NO se cuela al LLM, y se encola aparte.
5. DUPLICADOS    una reentrega de Meta (mismo id) no se encola dos veces.
6. VIEJOS        un mensaje de hace más de 5 min se descarta (Meta lo reintenta
                 tras haberlo dado por fallido; responderlo confunde al atleta).
7. VIDEO->TEXTO  el texto con los kilos se resuelve DENTRO del worker, cuando la
                 predicción del video ya escribió `ejercicio_detectado`. Es la
                 carrera que hacía correr el análisis VBT como "Movimiento Libre".

Uso:  python -u -m services.testbed.test_ruteo_webhook     (0 = ok, 1 = roto)
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from api import cola_mensajes as cm      # noqa: E402
from api import rutas_whatsapp as rw     # noqa: E402
from core.ejercicios import canonizar    # noqa: E402

FALLOS: list[str] = []
LLAMADAS: list[tuple] = []               # (pipeline, *args) de lo que se ejecutó
ENVIADOS: list[tuple[str, str]] = []     # (numero, texto) mandados por WhatsApp

# A propósito un número CON el 521 mexicano: el webhook lo normaliza a 52+10
# dígitos y todo lo de dentro (videos_pendientes, la cola, el historial) usa esa
# forma. Si el test usara un número ya normalizado no se vería si alguna rama se
# olvida de normalizar y acaba escribiendo el estado con dos claves distintas
# para el mismo atleta.
NUMERO = "5215500000777"
NUMERO_NORM = rw.normalizar_numero_mexicano(NUMERO)


def check(condicion: bool, titulo: str, detalle: str = "") -> None:
    if condicion:
        print(f"  ✓ {titulo}")
    else:
        print(f"  ✗ {titulo}" + (f"\n      {detalle}" if detalle else ""))
        FALLOS.append(titulo)


# ==============================================================================
# DOBLES DE LOS PIPELINES
# ==============================================================================
async def _falso_procesar_y_enviar(numero, texto=None, media_id=None,
                                   nombre_ejercicio=None, carga_kg=None):
    LLAMADAS.append(("mensaje", numero, texto, media_id, nombre_ejercicio, carga_kg))


async def _falso_audio(numero, audio_id):
    LLAMADAS.append(("audio", numero, audio_id))


async def _falso_prediccion(numero, media_id):
    LLAMADAS.append(("prediccion", numero, media_id))
    # Lo que hace la de verdad: escribe el ejercicio detectado. El test 7 depende
    # de que esto ocurra ANTES de que se atienda el texto con los kilos.
    rw.videos_pendientes.setdefault(numero, {})["ejercicio_detectado"] = "Sentadilla"


async def _falso_readiness(numero, texto_boton):
    LLAMADAS.append(("readiness", numero, texto_boton))


async def _falso_enviar(numero, texto):
    ENVIADOS.append((numero, texto))


def instalar_dobles() -> None:
    rw.procesar_y_enviar_mensaje = _falso_procesar_y_enviar
    rw.procesar_audio_en_segundo_plano = _falso_audio
    rw.procesar_prediccion_rapida = _falso_prediccion
    rw.responder_readiness_en_segundo_plano = _falso_readiness
    rw.enviar_whatsapp = _falso_enviar


# ==============================================================================
# PAYLOADS DE META
# ==============================================================================
_contador = [0]


def _msg(tipo: str, **extra) -> dict:
    _contador[0] += 1
    base = {"from": NUMERO, "type": tipo, "id": f"wamid.TEST{_contador[0]}",
            "timestamp": str(int(time.time()))}
    base.update(extra)
    return base


async def _limpiar() -> None:
    await cm.reiniciar_para_test()
    LLAMADAS.clear()
    ENVIADOS.clear()
    rw.videos_pendientes.clear()
    rw.mensajes_procesados.clear()
    rw._orden_mensajes.clear()


async def _procesar(*mensajes: dict) -> None:
    for m in mensajes:
        await rw._procesar_mensaje(m)
    await cm.drenar(timeout=10.0)


# ==============================================================================
# PRUEBAS
# ==============================================================================
async def prueba_texto() -> None:
    await _limpiar()
    await _procesar(_msg("text", text={"body": "¿qué me toca hoy?"}))

    check(len(LLAMADAS) == 1 and LLAMADAS[0][0] == "mensaje",
          "un texto normal encola un turno de chat", f"llamadas: {LLAMADAS}")
    if LLAMADAS:
        check(LLAMADAS[0][2] == "¿qué me toca hoy?" and LLAMADAS[0][3] is None,
              "llega el texto tal cual y sin media_id", f"{LLAMADAS[0]}")


async def prueba_audio() -> None:
    await _limpiar()
    await _procesar(_msg("audio", audio={"id": "AUDIO123"}))

    check(len(LLAMADAS) == 1 and LLAMADAS[0][0] == "audio",
          "un audio va al pipeline de Whisper", f"llamadas: {LLAMADAS}")


async def prueba_video_sin_kilos() -> None:
    await _limpiar()
    await _procesar(_msg("video", video={"id": "VID1", "caption": ""}))

    check(len(LLAMADAS) == 1 and LLAMADAS[0][0] == "prediccion",
          "video sin kilos -> predicción rápida", f"llamadas: {LLAMADAS}")
    check(NUMERO_NORM in rw.videos_pendientes,
          "queda registrado como video pendiente",
          "sin esto, el texto con los kilos no encuentra el video")
    check(NUMERO not in rw.videos_pendientes,
          "se registra con el número YA normalizado",
          "guardarlo sin normalizar parte el estado del atleta en dos claves")


async def prueba_video_con_kilos() -> None:
    await _limpiar()
    await _procesar(_msg("video", video={"id": "VID2",
                                         "caption": "sentadilla 140kg"}))

    check(len(LLAMADAS) == 1 and LLAMADAS[0][0] == "mensaje",
          "video con kilos -> pipeline VBT directo", f"llamadas: {LLAMADAS}")
    if LLAMADAS:
        check(LLAMADAS[0][3] == "VID2" and LLAMADAS[0][5] == 140.0,
              "viajan el media_id y la carga extraída del caption",
              f"{LLAMADAS[0]}")
    check(NUMERO_NORM not in rw.videos_pendientes,
          "no queda pendiente: ya tenía todo lo que hacía falta")


async def prueba_readiness() -> None:
    await _limpiar()
    await _procesar(_msg("button", button={"text": "Al 100"}))

    check(len(LLAMADAS) == 1 and LLAMADAS[0][0] == "readiness",
          "el botón del readiness NO se cuela al LLM", f"llamadas: {LLAMADAS}")
    if LLAMADAS:
        check(LLAMADAS[0][2] == "Al 100",
              "llega el texto del botón tal como lo manda Meta (sin emoji)")


async def prueba_duplicados() -> None:
    await _limpiar()
    mensaje = _msg("text", text={"body": "hola"})
    await _procesar(mensaje, dict(mensaje))     # Meta reentrega el mismo id

    check(len(LLAMADAS) == 1,
          "una reentrega de Meta no se procesa dos veces",
          f"se procesó {len(LLAMADAS)} veces: el atleta recibiría respuesta doble")


async def prueba_mensaje_viejo() -> None:
    await _limpiar()
    viejo = _msg("text", text={"body": "hola"})
    viejo["timestamp"] = str(int(time.time()) - rw.ANTIGUEDAD_MAXIMA_MENSAJE - 60)
    await _procesar(viejo)

    check(not LLAMADAS, "un mensaje demasiado viejo se descarta",
          f"llamadas: {LLAMADAS}")


async def prueba_video_luego_kilos() -> None:
    """La carrera que arregla la cola: video y kilos casi seguidos.

    Antes el webhook resolvía `videos_pendientes` AL RECIBIR, y la entrada
    todavía no tenía `ejercicio_detectado` (lo escribe la predicción, que tarda).
    El análisis VBT corría con el perfil de "Movimiento Libre": MVT y pendiente
    carga-velocidad de otro ejercicio.
    """
    await _limpiar()
    # Los dos en el MISMO lote, como los entrega Meta cuando llegan juntos.
    await _procesar(
        _msg("video", video={"id": "VID3", "caption": ""}),
        _msg("text", text={"body": "sí, 140kg"}),
    )

    tipos = [l[0] for l in LLAMADAS]
    check(tipos == ["prediccion", "mensaje"],
          "la predicción del video se atiende ANTES del texto con los kilos",
          f"orden observado: {tipos}")

    vbt = next((l for l in LLAMADAS if l[0] == "mensaje"), None)
    check(vbt is not None and vbt[3] == "VID3",
          "el texto arranca el análisis del video que estaba esperando",
          f"llamada: {vbt}")
    # Se compara contra `canonizar`, no contra el literal "Sentadilla": el
    # ejercicio viaja al motor VBT en su forma canónica (una sola fuente de
    # vocabulario, core/ejercicios). Lo que importa es que NO sea "Movimiento
    # Libre", que es lo que llegaba cuando el texto adelantaba a la predicción.
    esperado = canonizar("Sentadilla")
    check(vbt is not None and vbt[4] == esperado,
          "el ejercicio es el que detectó la visión, no 'Movimiento Libre'",
          f"esperaba {esperado!r}, llegó {vbt[4] if vbt else '?'!r} "
          "— es la carrera del bug")
    check(vbt is not None and vbt[5] == 140.0, "la carga llega al motor VBT",
          f"carga: {vbt[5] if vbt else '?'}")


async def main() -> int:
    instalar_dobles()
    print("\n=== test_ruteo_webhook ===\n")
    for nombre, prueba in (
            ("1. TEXTO", prueba_texto),
            ("2. AUDIO", prueba_audio),
            ("3. VIDEO SIN KILOS", prueba_video_sin_kilos),
            ("4. VIDEO CON KILOS", prueba_video_con_kilos),
            ("5. READINESS", prueba_readiness),
            ("6. DUPLICADOS", prueba_duplicados),
            ("7. MENSAJE VIEJO", prueba_mensaje_viejo),
            ("8. VIDEO -> KILOS", prueba_video_luego_kilos),
    ):
        print(nombre)
        await prueba()
        print()

    await _limpiar()
    if FALLOS:
        print(f"ROTO: {len(FALLOS)} comprobación(es) fallaron -> {'; '.join(FALLOS)}")
        return 1
    print("OK: el webhook encola el trabajo correcto para cada tipo de mensaje.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
