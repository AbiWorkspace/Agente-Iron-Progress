# -*- coding: utf-8 -*-
"""
test_rpe_hablado.py — cómo la gente describe de verdad el esfuerzo de una serie.

La tabla de conversión vivía solo en la description del parámetro 'rpe' de
core/tools.json, o sea únicamente para el modelo. El interceptor determinista
entendía '@8' y 'rpe 8', y nada más:

    "100x5 y me quedaron 2 en el tanque"  ->  rpe=None

Ese None apagaba tres cosas: el cálculo de e1RM por RIR, la autorregulación
obligatoria a RPE >= 9.5, y el registro vía tool (que exige la tríada).

    python -m services.testbed.test_rpe_hablado
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.llm_service import (  # noqa: E402
    detectar_serie_en_mensaje, detectar_series_multiples, rpe_desde_texto,
)

# (categoría, frase, rpe esperado)
CASOS: list[tuple[str, str, float | None]] = [
    # ── notación técnica: seguía y sigue funcionando ─────────────────────────
    ("técnico",  "100x5 rpe 8",                             8.0),
    ("técnico",  "100x5 @8",                                8.0),
    ("técnico",  "100x5 @9.5",                              9.5),
    ("técnico",  "100x5 @ 7,5",                             7.5),

    # ── RIR explícito: RPE = 10 - RIR. EL BUG ───────────────────────────────
    ("RIR",      "me quedaron 2 en el tanque",              8.0),
    ("RIR",      "me quedaban 2",                           8.0),
    ("RIR",      "me quedaba 1",                            9.0),
    ("RIR",      "me quedaron como 3",                      7.0),
    ("RIR",      "rir 2",                                   8.0),
    ("RIR",      "2 rir",                                   8.0),
    ("RIR",      "rir de 3",                                7.0),
    ("RIR",      "3 en reserva",                            7.0),
    ("RIR",      "2 en la recámara",                        8.0),
    ("RIR",      "podría haber sacado 2 más",               8.0),
    ("RIR",      "me sobraron 4",                           6.0),
    ("RIR",      "me quedaron 0",                           10.0),

    # ── frases sin cifra ─────────────────────────────────────────────────────
    ("verbal",   "casi no sube la última",                  9.5),
    ("verbal",   "a punto de fallar",                       9.5),
    ("verbal",   "me costó horrores",                       9.5),
    ("verbal",   "apenas la subí",                          9.5),
    ("verbal",   "fui al fallo",                            10.0),
    ("verbal",   "fallé la última",                         10.0),
    ("verbal",   "fallo técnico en la tercera",             10.0),
    ("verbal",   "no me salió la última",                   10.0),
    ("verbal",   "me quedaba una",                          9.0),
    ("verbal",   "salió fácil",                             6.0),
    ("verbal",   "muy suave, sobrado",                      6.0),
    ("verbal",   "ni me enteré",                            6.0),

    # ── el orden importa: 'casi no subió' NO es fallo ───────────────────────
    ("orden",    "casi no subía",                           9.5),
    ("orden",    "no subía",                               10.0),

    # ── no hay señal: NO se inventa un esfuerzo ─────────────────────────────
    ("nada",     "100x5",                                   None),
    ("nada",     "acabo de hacer la primera serie",         None),
    ("nada",     "hoy me siento bien",                      None),
    ("nada",     "hice press banca",                        None),

    # ── falsos positivos que hay que evitar ─────────────────────────────────
    ("no-rir",   "me quedaron 2 series por hacer",          None),
    ("no-rir",   "me quedan 3 días de descanso",            None),
    ("no-rir",   "me quedaron 2 semanas de plan",           None),
]

# Frase completa -> (peso, reps, rpe) por el interceptor real
CASOS_SERIE: list[tuple[str, tuple[float, int, float | None]]] = [
    ("acabo de hacer la primera serie, 100x5 y me quedaron 2 en el tanque", (100, 5, 8.0)),
    ("hice 100x5 y casi no sube la última",                                 (100, 5, 9.5)),
    ("80 por 10, salió fácil",                                              (80, 10, 6.0)),
    ("120x3 al fallo",                                                      (120, 3, 10.0)),
    ("100x5 rpe 8",                                                         (100, 5, 8.0)),
    ("100x5",                                                               (100, 5, None)),
]


def main() -> int:
    ok = fallos = 0
    ancho = max(len(f) for _c, f, _e in CASOS)
    cat = None
    print(f"\n1) Traducción del esfuerzo — {len(CASOS)} formas de decirlo\n")
    for categoria, frase, esperado in CASOS:
        if categoria != cat:
            print(f"\n  ── {categoria} ──")
            cat = categoria
        got = rpe_desde_texto(frase)
        bien = got == esperado
        ok += bien
        fallos += not bien
        linea = f"  {'ok   ' if bien else 'FALLA'} {frase:<{ancho}}  ->  {got}"
        if not bien:
            linea += f"   ESPERADO: {esperado}"
        print(linea)

    print("\n\n2) El interceptor completo (peso, reps y esfuerzo juntos)\n")
    for frase, esperado in CASOS_SERIE:
        s = detectar_serie_en_mensaje(frase)
        got = (s["peso"], s["reps"], s["rpe"]) if s else None
        esp = (float(esperado[0]), int(esperado[1]), esperado[2])
        bien = got == esp
        ok += bien
        fallos += not bien
        print(f"  {'ok   ' if bien else 'FALLA'} {frase}")
        print(f"        -> {got}" + ("" if bien else f"   ESPERADO: {esp}"))

    print("\n\n3) Series múltiples: el split no debe huerfanar el esfuerzo\n")
    multi: list[tuple[str, list[float | None]]] = [
        ("banca 100x5 y me quedaron 2",                       [8.0]),
        ("banca 100x5, sentadilla 140x3",                     [None, None]),
        ("banca 100x5 y sentadilla 140x3, las dos al fallo",  [None, None]),
    ]
    for frase, esperado in multi:
        got = [e["rpe"] for e in detectar_series_multiples(frase)]
        bien = got == esperado
        ok += bien
        fallos += not bien
        print(f"  {'ok   ' if bien else 'FALLA'} {frase}")
        print(f"        -> rpe por serie: {got}" + ("" if bien else f"   ESPERADO: {esperado}"))

    print(f"\n{'='*72}\n{ok} ok, {fallos} fallos\n")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
