# -*- coding: utf-8 -*-
"""
test_ejercicio_confirmado.py — el sello de un ejercicio no puede secuestrar los demás.

Sesión real del 2026-07-30:

    18:12:57 [TOOL] registrar_serie_completada({"ejercicio": "Press Inclinado con Barra", ...})
    18:19:44 [TOOL] registrar_serie_completada({"ejercicio": "Curl Biceps Barra", "peso_kg": 40, ...})
    18:56    telemetría -> · Press Inclinado: 40.0kg x 12 | RPE 10.0

El curl de bíceps quedó apuntado como press inclinado. Dos fallos independientes,
los dos capaces de estropear el diario por su cuenta:

  1. EL SELLO. `_ejercicio_confirmado` se usaba para dos cosas opuestas con un
     único TTL de 30 minutos: HEREDAR el ejercicio cuando el atleta no lo dice
     ("otra igual, 100x4") e IMPONERLO sobre lo que trae el modelo. Lo primero
     quiere una ventana larga; lo segundo, cortísima. Con 30 min para ambos, toda
     serie de los siguientes 30 minutos acababa en el primer ejercicio — y en un
     entreno real se cambia de ejercicio cada 5-10 minutos.

  2. EL CATÁLOGO. "Dominadas" tenía "barra" entre sus sinónimos, y como la
     búsqueda es de subcadena y esa entrada va la última, CUALQUIER ejercicio
     nombrado "con barra" que no matcheara antes caía en dominadas. "Curl de
     biceps con barra" -> Dominadas. "Remo con barra" -> Dominadas.

    python -m services.testbed.test_ejercicio_confirmado
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import handlers_v9 as H  # noqa: E402
from services import llm_service as L  # noqa: E402
from services import motor_predictivo as mp  # noqa: E402

NUM = "99900000077"

ok = fallos = 0
_guardadas: list[tuple] = []


def chk(desc: str, cond: bool, detalle: str = "") -> None:
    global ok, fallos
    ok += bool(cond)
    fallos += not cond
    print(f"  {'✓' if cond else '✗ FALLA'} {desc}" + (f"  ({detalle})" if detalle else ""))


def registrar(ejercicio: str, peso: float, reps: int) -> tuple[dict, str | None]:
    _guardadas.clear()
    res = asyncio.run(H.tool_registrar_serie_completada(NUM, {
        "ejercicio": ejercicio, "peso_kg": peso, "repeticiones": reps, "rpe": 8}))
    return res, (_guardadas[0][0] if _guardadas else None)


def sellar_hace(ejercicio: str, segundos: float) -> None:
    H._ejercicio_confirmado[NUM] = (ejercicio, time.time() - segundos)


def main() -> int:
    mp.guardar_serie_v9 = lambda n, e, p, r, rpe, d="ninguno": _guardadas.append((e, p, r))
    mp.leer_series = lambda n, e=None, dias=365: []

    print("\n[1] EL CATÁLOGO: 'con barra' ya no significa dominadas")
    casos = [
        ("Curl de biceps con barra con 40kg hice 12 reps @ fallo", "Curl de Biceps con Barra"),
        ("curl de biceps con mancuernas 20x12", "Curl de Biceps con Mancuernas"),
        ("press inclinado con barra 90x11", "Press Inclinado"),
        ("sentadilla con barra 140x3", "Sentadillas"),
        ("hice 8 dominadas", "Dominadas"),
        ("dominadas lastradas 30x7", "Dominadas Lastradas"),
        ("me colgué de la barra fija", "Dominadas"),
    ]
    for mensaje, esperado in casos:
        got = L._inferir_ejercicio_serie(mensaje)
        chk(f"{mensaje[:38]!r} -> {esperado}", got == esperado, f"dio {got!r}")
    # Sin entrada en el catálogo, None es la respuesta honesta: la resuelve el
    # plan del día o la pregunta el coach. Devolver 'Dominadas' era el desastre.
    chk("un ejercicio que no está en el catálogo devuelve None, no una adivinanza",
        L._inferir_ejercicio_serie("remo con barra 80x10") is None)

    print("\n[2] EL BUG: sello de hace 7 minutos, ejercicio nuevo")
    sellar_hace("Press Inclinado", 7 * 60)
    _res, guardado = registrar("Curl Biceps Barra", 40, 12)
    chk("el curl se guarda como curl, no como el press de antes",
        "curl" in str(guardado).lower(), f"quedó como {guardado!r}")

    print("\n[3] La guarda ORIGINAL sigue viva (sello reciente)")
    sellar_hace("Sentadillas", 20)
    res, guardado = registrar("Press Banco Plano", 140, 3)
    chk("el atleta confirmó sentadillas y el modelo derivó: manda el atleta",
        "sentadill" in str(guardado).lower(), f"quedó como {guardado!r}")
    chk("y el coach recibe el aviso para nombrarlo bien",
        bool(res.get("correccion_ejercicio")))

    print("\n[4] Herencia: el atleta NO dice el ejercicio")
    sellar_hace("Press Inclinado", 7 * 60)
    _res, guardado = registrar("", 90, 11)
    chk("una serie sin nombre hereda el sello aunque no pueda imponerse",
        "inclinado" in str(guardado).lower(), f"quedó como {guardado!r}")

    print("\n[5] Un sello caducado no asigna nada")
    sellar_hace("Press Inclinado", 45 * 60)
    res, _g = registrar("", 90, 11)
    chk("pasado el TTL se pide el ejercicio en vez de adivinar",
        not res.get("ok") and "ejercicio" in (res.get("faltan") or []),
        str(res.get("faltan")))

    print("\n[6] Las dos ventanas están separadas y ordenadas")
    chk("imponer es más corto que heredar", H._TTL_IMPONER_S < H._TTL_CONFIRMADO_S,
        f"imponer={H._TTL_IMPONER_S}s heredar={H._TTL_CONFIRMADO_S}s")
    sellar_hace("Sentadillas", 30)
    chk("dentro de la ventana corta el sello se lee para imponer",
        H.leer_ejercicio_confirmado(NUM, max_edad_s=H._TTL_IMPONER_S) == "Sentadillas")
    sellar_hace("Sentadillas", H._TTL_IMPONER_S + 60)
    chk("fuera de ella ya no",
        H.leer_ejercicio_confirmado(NUM, max_edad_s=H._TTL_IMPONER_S) is None)
    chk("pero sigue disponible para heredar",
        H.leer_ejercicio_confirmado(NUM) == "Sentadillas")

    print(f"\n{'='*74}\n  {ok} ok, {fallos} fallos\n{'='*74}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
