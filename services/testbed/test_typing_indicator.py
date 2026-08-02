"""test_typing_indicator.py — 'escribiendo…' antes de los 48 s de silencio.

El turno del coach tarda ~48 s de mediana (QA del 2026-07-30) y hasta ahora el
atleta no veía NADA en ese tiempo, ni la doble palomita azul. En WhatsApp eso se
interpreta como "no me va a contestar", y era lo que más pesaba en la experiencia
sin ser un fallo de contenido.

Lo que se verifica aquí:
  · el payload es EXACTAMENTE el que pide la Cloud API (un campo mal puesto no
    da error visible: Meta responde 200 y no se muestra nada);
  · el webhook no se bloquea esperando a Meta (tiene que devolver 200 rápido o
    Meta reintenta la entrega);
  · un fallo del indicador JAMÁS impide atender el mensaje del atleta;
  · la tarea de fondo suelta el guardia heredado (ver EXCLUSIÓN POR TURNO).

Uso:  python3 -u -m services.testbed.test_typing_indicator
"""
import asyncio
import inspect
import sys

from services import proactividad

fallos: list[str] = []


def chk(titulo: str, condicion: bool, detalle: str = "") -> None:
    print(f"  {'OK ' if condicion else 'FALLA'}  {titulo}" + (f"  ({detalle})" if detalle else ""))
    if not condicion:
        fallos.append(titulo)


def main() -> int:
    print("\n1. PAYLOAD — el contrato exacto de la Cloud API")
    capturado = {}

    async def _fake_post(ruta, **kw):
        capturado["ruta"] = ruta
        capturado["json"] = kw.get("json")
        return {"success": True}

    real_post, real_token, real_id = (proactividad._post_graph,
                                      proactividad.TOKEN, proactividad.PHONE_ID)
    proactividad._post_graph = _fake_post
    proactividad.TOKEN = proactividad.TOKEN or "TOKEN_DE_PRUEBA"
    proactividad.PHONE_ID = proactividad.PHONE_ID or "123456"
    try:
        ok = asyncio.run(proactividad.marcar_leido_y_escribiendo("wamid.TEST123"))
        chk("devuelve True cuando Meta acepta", ok is True)
        chk("va a /messages", capturado.get("ruta") == "messages", str(capturado.get("ruta")))
        p = capturado.get("json") or {}
        chk("messaging_product = whatsapp", p.get("messaging_product") == "whatsapp")
        chk("status = read (doble palomita azul)", p.get("status") == "read")
        chk("lleva el message_id entrante", p.get("message_id") == "wamid.TEST123")
        chk("typing_indicator.type = text",
            (p.get("typing_indicator") or {}).get("type") == "text", str(p.get("typing_indicator")))

        print("\n2. ROBUSTEZ — un fallo de Meta no puede tumbar el turno")

        async def _post_que_revienta(ruta, **kw):
            raise RuntimeError("400 Bad Request: message already read")

        proactividad._post_graph = _post_que_revienta
        ok = asyncio.run(proactividad.marcar_leido_y_escribiendo("wamid.X"))
        chk("devuelve False en vez de propagar", ok is False)

        print("\n3. Sin credenciales o sin id -> no intenta nada")
        proactividad._post_graph = _fake_post
        capturado.clear()
        chk("sin message_id no llama a Meta",
            asyncio.run(proactividad.marcar_leido_y_escribiendo("")) is False and not capturado)
        proactividad.TOKEN = ""
        chk("sin token no llama a Meta",
            asyncio.run(proactividad.marcar_leido_y_escribiendo("wamid.Y")) is False and not capturado)
    finally:
        proactividad._post_graph = real_post
        proactividad.TOKEN, proactividad.PHONE_ID = real_token, real_id

    print("\n4. EL WEBHOOK NO ESPERA — fire and forget")
    from api import rutas_whatsapp as rw
    fuente = inspect.getsource(rw._lanzar_escribiendo)
    chk("usa create_task (no await bloqueante)", "create_task" in fuente)
    chk("NO hace await de la llamada a Meta desde el webhook",
        "await proactividad.marcar_leido_y_escribiendo" not in
        inspect.getsource(rw._procesar_mensaje))
    chk("suelta el guardia heredado del semáforo", "_soltar_guardia_heredado" in fuente,
        "sin esto, la tarea de fondo provoca un release() de más en SEM_LLM")
    chk("tolera no tener event loop", "RuntimeError" in fuente)

    print("\n5. Se dispara al entrar el mensaje, antes de procesarlo")
    src = inspect.getsource(rw._procesar_mensaje)
    pos_typing = src.find("_lanzar_escribiendo")
    pos_encolar = src.find("_encolar_y_avisar")
    chk("se llama en _procesar_mensaje", pos_typing > 0)
    chk("ANTES de encolar el trabajo", 0 < pos_typing < pos_encolar,
        f"typing en {pos_typing}, encolado en {pos_encolar}")
    print("\n6. Mantiene el indicador vivo y se cancela al enviar el mensaje")
    chk("existe detener_escribiendo", hasattr(rw, "detener_escribiendo"))

    async def _test_loop():
        llamadas = []
        async def _fake_marcar(mid):
            llamadas.append(mid)
            return True
        orig_marcar = proactividad.marcar_leido_y_escribiendo
        proactividad.marcar_leido_y_escribiendo = _fake_marcar
        try:
            rw._lanzar_escribiendo("wamid.LOOP", "+5215500000000")
            await asyncio.sleep(0.05)
            chk("lanzó la primera llamada inmediatamente", len(llamadas) == 1 and llamadas[0] == "wamid.LOOP")
            chk("registró la tarea en _TAREAS_TYPING", "+5215500000000" in rw._TAREAS_TYPING)
            rw.detener_escribiendo("+5215500000000")
            await asyncio.sleep(0.05)
            chk("cancela y remueve la tarea de _TAREAS_TYPING", "+5215500000000" not in rw._TAREAS_TYPING)
        finally:
            proactividad.marcar_leido_y_escribiendo = orig_marcar

    asyncio.run(_test_loop())

    print("\n" + "=" * 60)
    if fallos:
        print(f"{len(fallos)} FALLOS:")
        for f in fallos:
            print(f"  - {f}")
    else:
        print("Todos los checks pasan.")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
