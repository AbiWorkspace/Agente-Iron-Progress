# -*- coding: utf-8 -*-
"""
bench_truncamiento.py — ¿siguen truncándose los turnos tras arreglar el nudo A?

El 2026-07-30, 6 turnos murieron con `done_reason=length`: el razonamiento se
comió los 3.072 tokens de num_predict y el contenido llegó vacío, forzando un
segundo intento. Coste medido: 97-129 s el primero + otros tantos el reintento.

Al auditarlo, 4 de los 6 razonaban sobre lo MISMO:

    "Telemetry shows RUTINA DEL DÍA with percentages but *without* explicit kg
     values in the list provided in the prompt text (it says 70% RM)."

O sea: el modelo daba vueltas porque le pedían kilos, la fuente decía "70% RM" y
el prompt le prohíbe multiplicar. Un conflicto sin salida. El nudo A arregló la
fuente (la rutina ya lleva los kg, y sin marca dice "peso por calibrar").

Este bench contrasta las dos condiciones con el MISMO modelo, las MISMAS opciones
del orquestador y los mensajes REALES que truncaron:

    A · rutina SIN kilos  ("— 70% RM")            <- lo que había
    B · rutina CON kilos  ("— *110 kg* (70% RM)") <- lo que hay ahora

Si B no trunca, el #17 estaba causado por el nudo A y no hay que tocar ningún
presupuesto de tokens. Si trunca igual, es un problema propio.

    python -m services.testbed.latencia.bench_truncamiento [repeticiones]
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from services import llm_service as L  # noqa: E402

# Los mensajes que de verdad truncaron ese día, con su hora en el log.
MENSAJES = [
    ("16:38", "Quiero que me pongas los pesos que voy a utilizar hoy."),
    ("17:19", "Con que peso le doy entonces?"),
    ("18:05", "Te odio con toda mi alma"),
]

RUTINA_SIN_KG = """*HOY — Semana 1 · Día 1 · Torso*
  • Press Pecho con Mancuernas: 4x10 — 70% RM @RPE 7.5
  • Remo en Maquina: 4x10 — 70% RM @RPE 7.5
  • Press Inclinado con Mancuernas: 2x11 — 70% RM @RPE 8.0
  • Extension de Triceps Katana: 3x11 — 70% RM @RPE 8.5
  • Curl de Biceps con Barra: 6x11 — 70% RM @RPE 8.5
  • Vuelos Laterales: 3x12 — 70% RM @RPE 8.5
  • Crunch Abdominal: 3x12 — 70% RM @RPE 8.5"""

RUTINA_CON_KG = """*HOY — Semana 1 · Día 1 · Torso*
  • Press Pecho con Mancuernas: 4x10 — *110 kg* (70% RM) @RPE 7.5
  • Remo en Maquina: 4x10 — *peso por calibrar* (70% RM) @RPE 7.5
  • Press Inclinado con Mancuernas: 2x11 — *95 kg* (70% RM) @RPE 8.0
  • Extension de Triceps Katana: 3x11 — *peso por calibrar* (70% RM) @RPE 8.5
  • Curl de Biceps con Barra: 6x11 — *37.5 kg* (70% RM) @RPE 8.5
  • Vuelos Laterales: 3x12 — *peso por calibrar* (70% RM) @RPE 8.5
  • Crunch Abdominal: 3x12 — *peso por calibrar* (70% RM) @RPE 8.5

[SISTEMA — SIN MARCA REGISTRADA]: no hay 1RM guardado de Remo en Maquina, \
Extension de Triceps Katana, Vuelos Laterales, Crunch Abdominal. Esos van SIN \
kilos a propósito. Dile 'peso por calibrar' tal cual y, al final del mensaje, \
pídele su marca (peso + repeticiones) en UNO de ellos o proponle calibrarlo con \
la primera serie usando el RPE indicado. PROHIBIDO calcular el porcentaje tú."""

FICHA = ("84.5 kg, 1.73 m, 24 años, hombre, 20% grasa, 6 días/semana, meta "
         "powerlifting, 1RM press banco plano: 157.1 kg, 1RM sentadilla: 216.7 kg, "
         "1RM peso muerto: 258.1 kg, 1RM dominadas: 36.9 kg")


def _telemetria(rutina: str) -> str:
    return f"""

--- TELEMETRÍA ---
FECHA: 2026-07-30 (jueves) 16:38:05

CALENDARIO: AL DÍA: Hoy toca entrenamiento. | SEMANA ACTUAL: 1

GENERACIÓN DE PLAN: ninguna en curso.

RUTINA DEL DÍA (los *kg* ya vienen calculados y redondeados: transcríbelos \
literales, no los recalcules):
{rutina}

ESTRUCTURA DE LA SEMANA 1: D1 Torso (el de hoy) · D2 Pierna · D3 Torso · D4 \
Pierna · D5 Torso · D6 Pierna.

ÚLTIMAS SERIES:
SERIES REGISTRADAS HOY: ninguna todavía.

FICHA DEL ATLETA: {FICHA}

--- FIN TELEMETRÍA ---

[SISTEMA]: Si decides usar una herramienta, EMÍTELA como llamada real."""


async def _una(mensaje: str, rutina: str) -> dict:
    """Una llamada con las MISMAS opciones que usa el orquestador al razonar."""
    mensajes = [
        {"role": "system", "content": L.cargar_system_prompt()},
        {"role": "user", "content": mensaje + _telemetria(rutina)},
    ]
    opciones = {**L.OPCIONES_QWEN_32B, **L.AJUSTES_PENSANDO}
    t0 = time.monotonic()
    try:
        r = await L.chat_llm(mensajes, tools=L.cargar_herramientas(),
                             opciones=opciones, pensar=True, quien="bench-trunc")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "segundos": time.monotonic() - t0}
    msg = r.get("message", {}) or {}
    return {
        "done": str(L._campo_resp(r, "done_reason", "?")),
        "thinking": len(msg.get("thinking") or ""),
        "contenido": len(msg.get("content") or ""),
        "tools": [t.function.name if hasattr(t, "function") else "?"
                  for t in (msg.get("tool_calls") or [])],
        "segundos": round(time.monotonic() - t0, 1),
    }


async def main(repeticiones: int = 1) -> int:
    print(f"\nMODELO: {L.MODELO_LLM} | num_predict={L.AJUSTES_PENSANDO['num_predict']} "
          f"| num_ctx={L.OPCIONES_QWEN_32B['num_ctx']}")
    print(f"{len(MENSAJES)} mensajes x 2 condiciones x {repeticiones} repetición(es) "
          f"= {len(MENSAJES) * 2 * repeticiones} llamadas\n")

    resultados: dict[str, list[dict]] = {"A_sin_kg": [], "B_con_kg": []}
    for rep in range(repeticiones):
        for hora, mensaje in MENSAJES:
            for etiqueta, rutina in (("A_sin_kg", RUTINA_SIN_KG),
                                     ("B_con_kg", RUTINA_CON_KG)):
                r = await _una(mensaje, rutina)
                r["mensaje"] = f"[{hora}] {mensaje[:38]}"
                resultados[etiqueta].append(r)
                trunc = "TRUNCA" if r.get("done") == "length" else "ok    "
                print(f"  {etiqueta}  {trunc}  think={r.get('thinking', 0):>6}  "
                      f"cont={r.get('contenido', 0):>5}  {r.get('segundos')}s   "
                      f"{r['mensaje']}"
                      + (f"  ERROR {r['error']}" if r.get("error") else ""))

    print("\n" + "=" * 74)
    for etiqueta, rs in resultados.items():
        validos = [r for r in rs if not r.get("error")]
        if not validos:
            print(f"  {etiqueta}: sin resultados válidos")
            continue
        truncados = sum(1 for r in validos if r["done"] == "length")
        media_t = sum(r["segundos"] for r in validos) / len(validos)
        media_think = sum(r["thinking"] for r in validos) / len(validos)
        vacios = sum(1 for r in validos if r["contenido"] < 60 and not r["tools"])
        print(f"  {etiqueta}: {truncados}/{len(validos)} truncados · "
              f"{vacios} sin salida útil · {media_t:.0f}s de media · "
              f"thinking medio {media_think:.0f} chars")
    print("=" * 74)

    salida = Path(__file__).parent / "resultado_truncamiento.json"
    salida.write_text(json.dumps(resultados, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDetalle en {salida}\n")
    return 0


if __name__ == "__main__":
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    sys.exit(asyncio.run(main(reps)))
