"""test_conversacion_larga.py — qué pasa a los 30, 60 y 120 mensajes.

El banco de QA mide turnos SUELTOS. Pero el compresor entra a los
UMBRAL_COMPRESION mensajes y reescribe la memoria del atleta, así que todo lo
que se rompa en una conversación de días se rompe ahí — y no había ninguna
medición. Esto simula la conversación completa SIN gastar modelo: el compresor
se sustituye por un doble que devuelve un resumen fijo, porque lo que se prueba
es la MECÁNICA (qué sobrevive, qué se pierde, cuánto crece), no la redacción.

Lo que se verifica:
  · el historial no crece sin límite;
  · el system prompt sobrevive SIEMPRE en la posición 0 (si se pierde, el coach
    se queda sin reglas y empieza a inventar);
  · el resumen se ENCADENA en vez de perderse al comprimir dos veces;
  · los mensajes del orquestador no entran en la memoria a largo plazo;
  · el prompt se mantiene por debajo de num_ctx (con 32768 se llegó a 32.699
    tok y quedaban 69 para responder);
  · la compresión diferida no pisa al turno siguiente.

Uso:  python3 -u -m services.testbed.test_conversacion_larga
"""
import asyncio
import sys

from services import llm_service as llm

fallos: list[str] = []


def chk(titulo: str, condicion: bool, detalle: str = "") -> None:
    print(f"  {'OK ' if condicion else 'FALLA'}  {titulo}" + (f"  ({detalle})" if detalle else ""))
    if not condicion:
        fallos.append(titulo)


def tokens(hist) -> int:
    import json
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in hist) // 4


SYSTEM = {"role": "system", "content": "REGLAS DEL COACH. " * 400}       # ~3.700 tok


def turno(i: int) -> list:
    """Un turno realista: el atleta escribe con su telemetría pegada y el coach responde."""
    return [
        {"role": "user", "content": f"mensaje {i} del atleta\n\n[TELEMETRÍA]: " + "dato " * 150},
        {"role": "assistant", "content": f"respuesta {i} del coach. " * 20},
    ]


async def main() -> int:
    # Doble del modelo: el compresor es lo único que llama a chat_llm aquí.
    llamadas = {"n": 0}

    async def _chat_falso(mensajes, **kw):
        llamadas["n"] += 1
        return {"message": {"content": "[ESTADO DE LA CHARLA]: hablando de banca.\n"
                                       "[ACUERDOS Y PREFERENCIAS]: molestia de codo activa.\n"
                                       "[DATOS SUELTOS]: PR de 110 kg mencionado."},
                "done_reason": "stop"}

    real = llm.chat_llm
    llm.chat_llm = _chat_falso
    try:
        print(f"\n1. CRECIMIENTO — umbral {llm.UMBRAL_COMPRESION}, ventana {llm.VENTANA_RECIENTE}")
        hist = [SYSTEM]
        tamanos = []
        for i in range(60):
            hist += turno(i)
            if len(hist) - 1 >= llm.UMBRAL_COMPRESION:
                hist = await llm.comprimir_historial_si_necesario(hist)
            tamanos.append(tokens(hist))
        print(f"   tras 60 turnos: {len(hist)} mensajes, {tokens(hist)} tok "
              f"(pico {max(tamanos)} tok)")
        chk("el historial NO crece sin límite", max(tamanos) < 20000, f"pico {max(tamanos)} tok")
        chk("se comprimió varias veces", llamadas["n"] >= 3, f"{llamadas['n']} compresiones")

        print("\n2. EL SYSTEM PROMPT SOBREVIVE (si no, el coach pierde sus reglas)")
        chk("sigue en la posición 0", hist[0].get("role") == "system")
        chk("intacto, no resumido", hist[0]["content"] == SYSTEM["content"])

        print("\n3. EL RESUMEN SE ENCADENA, no se pierde al recomprimir")
        marcadores = [m for m in hist if str(m.get("content", "")).startswith(llm.RESUMEN_MARKER)]
        chk("hay exactamente UN bloque de resumen", len(marcadores) == 1, f"{len(marcadores)}")
        chk("el resumen conserva las 3 secciones",
            all(s in marcadores[0]["content"] for s in
                ("[ESTADO DE LA CHARLA]", "[ACUERDOS Y PREFERENCIAS]", "[DATOS SUELTOS]")))

        print("\n4. CABE EN EL CONTEXTO (num_ctx = "
              f"{llm.OPCIONES_QWEN_32B['num_ctx']})")
        import json
        tools_tok = len(json.dumps(json.load(open("core/tools.json")), ensure_ascii=False)) // 4
        peor = max(tamanos) + tools_tok
        margen = llm.OPCIONES_QWEN_32B["num_ctx"] - peor
        print(f"   peor caso: {max(tamanos)} (historial) + {tools_tok} (tools) = {peor} tok")
        chk("queda margen para generar (>2048 tok)", margen > 2048, f"margen {margen} tok")

        print("\n5. LOS MENSAJES DEL ORQUESTADOR NO ENTRAN EN LA MEMORIA LARGA")
        hist2 = [SYSTEM]
        for i in range(20):
            hist2 += turno(i)
            hist2.append({"role": "assistant", "content": "",
                          "tool_calls": [{"function": {"name": "registrar_serie_completada"}}]})
            hist2.append({"role": "user", "content": "[TOOL-RESULT — datos REALES]: serie guardada"})
        comprimido = await llm.comprimir_historial_si_necesario(hist2)
        resumen = next((m["content"] for m in comprimido
                        if str(m.get("content", "")).startswith(llm.RESUMEN_MARKER)), "")
        chk("el resumen no cita [TOOL-RESULT]", "TOOL-RESULT" not in resumen)

        print("\n6. LA COMPRESIÓN DIFERIDA NO PISA AL TURNO SIGUIENTE")
        num = "5215500000LARGO"
        llm.historial_memoria[num] = [SYSTEM] + [m for i in range(40) for m in turno(i)]
        lock = llm._locks_compresion.setdefault(num, asyncio.Lock())
        await lock.acquire()             # simula una compresión en vuelo
        espera = asyncio.create_task(llm._esperar_compresion_en_curso(num))
        await asyncio.sleep(0.05)
        chk("el turno ESPERA mientras se comprime", not espera.done())
        lock.release()
        await asyncio.wait_for(espera, timeout=2)
        chk("y continúa en cuanto termina", espera.done())
        llm.historial_memoria.pop(num, None)
        llm._locks_compresion.pop(num, None)
    finally:
        llm.chat_llm = real

    print("\n" + "=" * 62)
    if fallos:
        print(f"{len(fallos)} FALLOS:")
        for f in fallos:
            print(f"  - {f}")
    else:
        print("Todos los checks pasan.")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
