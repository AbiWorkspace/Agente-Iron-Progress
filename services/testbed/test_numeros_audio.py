# -*- coding: utf-8 -*-
"""
test_numeros_audio.py — "un" es un artículo, no el número 1.

Sesión real del 2026-07-30, con el atleta mandando audios:

    [WHISPER ORIGINAL]: ...así de vista, chance en un 20%.
    [WHISPER FILTRADO]: ...así de vista, chance en 1 20%.
    [ONB-PARSE] campo='porcentaje_grasa' exito=False
    [OUT] "Volvemos al tema: ¿cuál es tu porcentaje de grasa estimado?"
    ATLETA: "ya te dije, 20%"

`normalizar_numeros_es` convierte los números escritos con palabras ("ciento
cuarenta y cinco kilos por tres" -> "145 kilos por 3"), que es imprescindible
porque Whisper los escribe así la mitad de las veces. Pero su tabla incluía
{"un": 1, "uno": 1, "una": 1} y los sustituía a ciegas, y en español esas tres
palabras son ARTÍCULO mucho más a menudo que número.

El dígito falso entra en el mensaje y de ahí a todos los parsers. En el caso de
arriba, `_primer_numero` cogió el 1, quedó fuera del rango 3-60 y el atleta tuvo
que repetir su dato. Otros del mismo log: "tienes un error" -> "tienes 1 error",
"es un press de pecho" -> "es 1 press de pecho".

    python -m services.testbed.test_numeros_audio
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import llm_service as L  # noqa: E402
from services.proactividad import normalizar_numeros_es as N  # noqa: E402

ok = fallos = 0


def chk(desc: str, cond: bool, detalle: str = "") -> None:
    global ok, fallos
    ok += bool(cond)
    fallos += not cond
    print(f"  {'✓' if cond else '✗ FALLA'} {desc}" + (f"  ({detalle})" if detalle else ""))


def main() -> int:
    print("\n[1] ARTÍCULO: el texto no se toca")
    articulos = [
        "Bueno, yo pienso que maybe, valoración, así de vista, chance en un 20%.",
        "Pero oye, tienes un error, no me registraste las sentadillas.",
        "entiendo que es un press de pecho con mancuernas",
        "dame un momento",
        "quiero un plan nuevo",
        "me duele un poco el hombro",
        "hazme una rutina de powerbuilding",
        "es una barbaridad de volumen",
    ]
    for t in articulos:
        chk(f"{t[:52]!r}", N(t) == t, f"quedó {N(t)[:60]!r}")

    print("\n[2] NÚMERO: se sigue convirtiendo")
    numeros = [
        ("hice ciento cuarenta y cinco kilos por tres", "145 kilos por 3"),
        ("ciento dos y medio kilos por ocho repeticiones", "102.5 kilos por 8 repeticiones"),
        ("doscientos kilos por tres", "200 kilos por 3"),
        ("a rpe nueve", "a rpe 9"),
        ("metí un kilo más", "1 kilo más"),
        ("saqué una repetición", "1 repetición"),
        ("lo hice una vez", "1 vez"),
        ("una serie de diez", "1 serie de 10"),
        ("quince reps", "15 reps"),
    ]
    for t, esperado in numeros:
        chk(f"{t[:44]!r} -> …{esperado}", esperado in N(t), f"dio {N(t)!r}")

    print("\n[3] Los compuestos no se rompen (el 'un' interno sigue sumando)")
    chk("'ciento un kilos' -> 101", "101 kilos" in N("ciento un kilos"), N("ciento un kilos"))
    chk("'doscientos un kilos' -> 201",
        "201 kilos" in N("doscientos un kilos"), N("doscientos un kilos"))

    print("\n[4] EL TURNO PERDIDO: el 20% se captura al primer intento")
    guardado: dict = {}
    L.actualizar_perfil = lambda n, **kw: guardado.update(kw)
    L.guardar_dato_en_expediente = lambda n, t: None
    L.parsear_batch_rms = lambda n, m, solo_campos=None: []
    crudo = "Bueno, yo pienso que maybe, valoración, así de vista, chance en un 20%."
    exito, _fb = L.procesar_respuesta_onboarding("99900000111", "porcentaje_grasa", N(crudo))
    chk("el onboarding acepta la respuesta", exito)
    chk("y guarda el 20, no el 1", guardado.get("porcentaje_grasa") == 20.0, str(guardado))

    print("\n[5] Casos límite: nada revienta")
    for t in ("", "   ", "un", "una", "uno"):
        try:
            N(t)
            chk(f"{t!r} no revienta", True)
        except Exception as e:                                   # noqa: BLE001
            chk(f"{t!r} no revienta", False, f"{type(e).__name__}: {e}")

    print(f"\n{'='*74}\n  {ok} ok, {fallos} fallos\n{'='*74}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
