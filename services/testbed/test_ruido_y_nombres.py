# -*- coding: utf-8 -*-
"""
test_ruido_y_nombres.py — los seis errores medios y bajos del 2026-07-30.

  #12 EL CATÁLOGO MANDABA LAS MANCUERNAS AL EJERCICIO CON BARRA
      'press banco con mancuernas 45x10' -> 'press banco plano'
      Ninguna variante matcheaba "banco con mancuernas" y caía al fallback
      legacy, que solo conoce los cuatro básicos, todos de barra. Es la misma
      confusión que hundió un 1RM de 157 a 59,3 kg, por otro camino.

  #11 EL 1RM NO LLEGABA A LA VARIANTE DEL MOVIMIENTO
      El atleta dio "press inclinado 110x7" (1RM 135,7) y su plan tiene "Press
      Inclinado con Mancuernas": el ejercicio salía "peso por calibrar" teniendo
      el dato guardado.

  #9  SERIES DUPLICADAS
      'Press Pecho Mancuernas: 45x10' + 'Press Banca: 45x10' eran UNA serie. Los
      duplicados falsean volumen, MRV y detección de PR.

  #10 MENSAJES DOBLES
      Un sticker mientras el turno trabajaba colaba "😄 Te leo" por delante de la
      respuesta; y una ráfaga de tres mensajes producía dos turnos.

  #16 ARGUMENTOS DE OTRA HERRAMIENTA, IGNORADOS EN SILENCIO
      predecir_carga_optima({..., 'carga_actual_kg': '0'}) — ese argumento es de
      autorregular_dia y se perdía sin una línea de log.

  #—  PROMESAS DE PLAN SIN VIGILAR
      "Se está recalculando todo... Tardará unos 2-3 minutos en armarse" no lo
      veía _CLAIM_REGENERANDO_RE.

    python -m services.testbed.test_ruido_y_nombres
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import llm_service as L  # noqa: E402
from services import motor_predictivo as mp  # noqa: E402

ok = fallos = 0


def chk(desc: str, cond: bool, detalle: str = "") -> None:
    global ok, fallos
    ok += bool(cond)
    fallos += not cond
    print(f"  {'✓' if cond else '✗ FALLA'} {desc}" + (f"  ({detalle})" if detalle else ""))


def main() -> int:
    print("\n[12] El equipo que nombra el atleta manda sobre el músculo")
    for msg, esperado in [
        ("press banco con mancuernas 45x10", "Press Banco Plano con Mancuernas"),
        ("press banco mancuernas 45x10", "Press Banco Plano con Mancuernas"),
        ("press plano con mancuernas", "Press Banco Plano con Mancuernas"),
        ("curl de biceps con mancuernas 20x12", "Curl de Biceps con Mancuernas"),
        ("curl de biceps con barra 40x12", "Curl de Biceps con Barra"),
        ("sentadilla en maquina smith", "Sentadilla en Maquina Smith"),
        ("sentadilla en maquina hack", "Sentadilla en Maquina Hack"),
        ("press pecho en maquina", "Press Pecho en Maquina"),
        ("peso muerto con mancuernas", "Peso Muerto con Mancuernas"),
        # sin equipo nombrado, el básico de barra es lo correcto
        ("press banca 100x5", "Press Banco Plano"),
        ("hice sentadilla 140x3", "Sentadillas"),
        ("peso muerto 200x3", "Peso Muerto"),
        # Whisper escribe "con mancuernas" como "comancuarnas": sin variante que
        # encaje, None es mejor que mandarlo a la barra
        ("press, banco, comancuarnas", None),
    ]:
        got = L._inferir_ejercicio_serie(msg)
        chk(f"{msg[:38]!r} -> {esperado}", got == esperado, f"dio {got!r}")

    print("\n[11] La marca del movimiento base alcanza a sus variantes")
    rms = {"press inclinado": 135.7, "press banco plano": 157.1, "dominadas": 0}
    for base, esperado in [
        ("press inclinado con mancuernas", (135.7, True)),   # aproximada: avisa
        ("press inclinado con barra", (135.7, True)),
        ("press inclinado", (135.7, False)),                 # exacta
        ("press banco plano", (157.1, False)),
        ("dominadas lastradas", (0, False)),                 # 0 es un valor, no ausencia
        ("vuelos laterales", (None, False)),
    ]:
        got = L._rm_guardado_de(rms, base)
        chk(f"{base:<32} -> {esperado}", got == esperado, f"dio {got}")

    print("\n[9] La misma serie no entra dos veces")
    guardadas: list[tuple] = []
    ya_hay = {"activo": False}
    mp.guardar_serie_v9 = lambda n, e, p, r, rpe, d="ninguno": guardadas.append((e, p, r))
    mp.leer_series = lambda n, e=None, dias=365: []
    mp.serie_duplicada_reciente = (
        lambda n, e, p, r, rpe, minutos=20: ya_hay["activo"])

    from services import handlers_v9 as H
    args = {"ejercicio": "Press Pecho con Mancuernas", "peso_kg": 45,
            "repeticiones": 10, "rpe": 10}
    res1 = asyncio.run(H.tool_registrar_serie_completada("99900000400", dict(args)))
    chk("la primera se guarda", res1.get("ok") and len(guardadas) == 1,
        f"{len(guardadas)} guardadas")
    ya_hay["activo"] = True
    res2 = asyncio.run(H.tool_registrar_serie_completada("99900000400", dict(args)))
    chk("la repetida NO se guarda", len(guardadas) == 1, f"{len(guardadas)} guardadas")
    chk("y no se reporta como fallo", res2.get("ok") is True, str(res2.get("ok")))
    chk("se marca como duplicada", res2.get("duplicada") is True)
    chk("el coach recibe qué decir",
        "YA constaba" in str(res2.get("nota_coach")), str(res2.get("nota_coach"))[:60])

    print("\n[10] Un aviso suelto no se cuela delante de la respuesta")
    from api import cola_mensajes as cola
    asyncio.run(cola.reiniciar_para_test())
    chk("sin trabajo suyo, el aviso puede salir", not cola.tiene_trabajo("99900000401"))
    cola._en_curso["99900000401"] = "texto[hola]"
    chk("con un turno EN CURSO, se calla", cola.tiene_trabajo("99900000401"))
    cola._en_curso.pop("99900000401", None)
    cola._colas["99900000401"] = __import__("collections").deque([object()])
    chk("con mensajes suyos EN COLA, también", cola.tiene_trabajo("99900000401"))
    asyncio.run(cola.reiniciar_para_test())
    chk("la ventana de ráfaga está activa", cola.VENTANA_RAFAGA_S > 0,
        f"{cola.VENTANA_RAFAGA_S}s")
    chk("y es corta frente a un turno (20-130 s)", cola.VENTANA_RAFAGA_S <= 5)

    print("\n[16] Un argumento de otra herramienta se descarta y se registra")
    fuera = L._normalizar_args_tool(
        "predecir_carga_optima",
        {"ejercicio": "press banca", "repeticiones_objetivo": 10, "carga_actual_kg": "0"},
        "99900000402")
    chk("'carga_actual_kg' (de autorregular_dia) se descarta",
        "carga_actual_kg" not in fuera, str(fuera))
    chk("lo declarado sobrevive intacto",
        fuera == {"ejercicio": "press banca", "repeticiones_objetivo": 10}, str(fuera))
    alias = L._normalizar_args_tool(
        "predecir_carga_optima", {"nombre_ejercicio": "press banca", "reps_objetivo": 10},
        "99900000402")
    chk("y los alias legítimos se siguen traduciendo",
        alias == {"ejercicio": "press banca", "repeticiones_objetivo": 10}, str(alias))
    completo = {"ejercicio": "sentadilla", "peso_kg": 100, "repeticiones": 5, "rpe": 8}
    chk("una llamada correcta no se toca",
        L._normalizar_args_tool("registrar_serie_completada", dict(completo)) == completo)

    print("\n[—] Promesas de plan que antes no se vigilaban")
    for t in ("Se está recalculando todo para darte ese volumen extra.",
              "Tardará unos 2-3 minutos en armarse.",
              "Estará listo en unos minutos y te aviso.",
              "Se está reajustando tu mesociclo."):
        chk(f"detecta: {t[:48]!r}", bool(L._CLAIM_REGENERANDO_RE.search(t)))
    for t in ("¿Quieres que te arme un plan nuevo?",
              "Si me confirmas, lo rehacemos desde cero.",
              "Tu plan sigue igual, no he tocado nada.",
              "Descansa 2 minutos entre series.",
              "Hoy toca Día 2: Pierna. Empieza con sentadilla."):
        m = L._CLAIM_REGENERANDO_RE.search(t)
        chk(f"deja pasar: {t[:44]!r}", not m, m.group(0) if m else "")

    print(f"\n{'='*74}\n  {ok} ok, {fallos} fallos\n{'='*74}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
