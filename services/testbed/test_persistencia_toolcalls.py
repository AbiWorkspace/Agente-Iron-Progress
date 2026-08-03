# -*- coding: utf-8 -*-
"""
test_persistencia_toolcalls.py — el turno con herramientas TIENE que llegar a disco.

Sesión real del 2026-07-30. En el log aparece 20+ veces, siempre pegado a su
contrario:

    [HIST-ORQ] 2 mensajes del orquestador persistidos (prefijo de caché intacto)
    [HIST-ERROR] no se pudo persistir: Object of type ToolCall is not JSON serializable

Ollama devuelve los `tool_calls` como modelos pydantic y `json.dumps` no sabe
serializarlos, así que TODO turno que llamara una herramienta moría al escribir.
Tres consecuencias encadenadas:

  1. El turno entero no llegaba a disco: el historial del atleta acaba a las
     19:16 y el turno de las 19:29 sencillamente no existe.
  2. El `except` hacía `_firmas_historial.pop()`, así que el turno siguiente veía
     la firma cambiada y recargaba la versión vieja: se borraba de la memoria
     también.
  3. Con el historial saltando, el prompt del turno N dejaba de ser prefijo del
     N+1 y la caché de Ollama fallaba: 28,7 s de prefill (790 tok/s) frente a los
     4,1 s (6.376 tok/s) de un turno con caché acertada.

Lo que se verifica aquí es lo que hace falta para que las tres no vuelvan: que se
escriba, que sobreviva la llamada, y que el prompt enviado NO cambie ni un byte.

    python -m services.testbed.test_persistencia_toolcalls
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ollama._types import ChatRequest, Message  # noqa: E402

from services import llm_service as L  # noqa: E402

NUM = "99900000031"

# El tool call exacto del turno de las 18:19:44.
TC = Message.ToolCall(function=Message.ToolCall.Function(
    name="registrar_serie_completada",
    arguments={"ejercicio": "Curl Biceps Barra", "peso_kg": 40,
               "repeticiones": 12, "rpe": 10}))

ok = fallos = 0


def chk(desc: str, cond: bool, detalle: str = "") -> None:
    global ok, fallos
    ok += bool(cond)
    fallos += not cond
    print(f"  {'✓' if cond else '✗ FALLA'} {desc}" + (f"  ({detalle})" if detalle else ""))


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="hist_test_")
    ruta = os.path.join(tmp, f"historial_{NUM}.json")

    print("\n[1] Aplanado: objeto de Ollama y dict dan lo MISMO")
    plano = L._tool_calls_planos([TC])
    chk("el objeto ToolCall se convierte", plano == [{"function": {
        "name": "registrar_serie_completada",
        "arguments": {"ejercicio": "Curl Biceps Barra", "peso_kg": 40,
                      "repeticiones": 12, "rpe": 10}}}], str(plano))
    chk("es serializable", bool(json.dumps(plano)))
    chk("idempotente: aplanar un dict no lo cambia", L._tool_calls_planos(plano) == plano)
    chk("una call rota se descarta en vez de reventar",
        L._tool_calls_planos([{"no": "es una call"}, None]) == [])
    chk("lista vacía y None son inofensivos",
        L._tool_calls_planos([]) == [] and L._tool_calls_planos(None) == [])

    print("\n[2] EL BUG: el turno con herramientas llega a disco")
    historial = [
        {"role": "user", "content": "Curl de biceps con barra con 40kg hice 12 reps @ fallo"},
        {"role": "assistant", "content": "", "tool_calls": [TC]},   # el que reventaba
        {"role": "user", "content": "[TOOL-RESULT — datos REALES ya calculados]"},
        {"role": "assistant", "content": "¡Brutal! 40 kg x 12 al fallo."},
    ]
    L._firmas_historial.pop(NUM, None)
    asyncio.run(L._persistir_historial(NUM, historial, ruta))

    chk("el archivo existe y no está vacío",
        os.path.exists(ruta) and os.path.getsize(ruta) > 0)
    chk("NO queda un .tmp huérfano de 0 bytes", not os.path.exists(ruta + ".tmp"))
    chk("la firma queda registrada (si no, el turno siguiente recarga y pierde esto)",
        L._firmas_historial.get(NUM) is not None)
    en_disco = json.load(open(ruta, encoding="utf-8"))
    chk("los 4 mensajes del turno están guardados", len(en_disco) == 4,
        f"hay {len(en_disco)}")
    chk("el tool_call sobrevivió al viaje",
        en_disco[1].get("tool_calls") == plano)

    print("\n[3] Lo recargado se puede reenviar a Ollama")
    msg = Message.model_validate(en_disco[1])
    chk("pydantic lo revalida a ToolCall",
        msg.tool_calls[0].function.name == "registrar_serie_completada")

    print("\n[4] LA CACHÉ: el prompt enviado no cambia ni un byte")
    como_antes = ChatRequest(model="m", messages=[
        {"role": "assistant", "content": "", "tool_calls": [TC]}]).model_dump_json(exclude_none=True)
    como_ahora = ChatRequest(model="m", messages=[
        {"role": "assistant", "content": "", "tool_calls": plano}]).model_dump_json(exclude_none=True)
    tras_disco = ChatRequest(model="m", messages=[en_disco[1]]).model_dump_json(exclude_none=True)
    chk("aplanar no altera la petición", como_antes == como_ahora)
    chk("ir y volver del disco tampoco", como_antes == tras_disco)

    print("\n[5] Un objeto imposible ya no cuesta el turno entero")
    class Raro:                                              # noqa: E306
        def __repr__(self): return "<raro>"
    ruta2 = os.path.join(tmp, "h2.json")
    asyncio.run(L._persistir_historial(NUM, [
        {"role": "assistant", "content": "x", "tool_calls": [TC]},
        {"role": "user", "content": Raro()}], ruta2))
    chk("se escribe igual gracias al default=str", os.path.exists(ruta2))
    chk("y tampoco deja .tmp", not os.path.exists(ruta2 + ".tmp"))

    print("\n[6] Los mensajes normales NO ganan una clave tool_calls")
    ruta3 = os.path.join(tmp, "h3.json")
    asyncio.run(L._persistir_historial(NUM, [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "qué tal"}], ruta3))
    limpio = json.load(open(ruta3, encoding="utf-8"))
    chk("sin 'tool_calls': null (cambiaría el prefijo de caché)",
        all("tool_calls" not in m for m in limpio), str(limpio))

    print(f"\n{'='*74}\n  {ok} ok, {fallos} fallos\n{'='*74}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
