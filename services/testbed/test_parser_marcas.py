# -*- coding: utf-8 -*-
"""
test_parser_marcas.py — cómo la gente dice de verdad sus marcas.

El parser viejo entendía 'N x M' y 'N por M'. Para todo lo demás cogía el primer
número y asumía 1 repetición, en silencio y devolviendo éxito:

    "dominadas con 20 kilos de lastre saqué 6"  ->  (20, 1)

Cada caso de aquí es una forma real de responder a "¿cuál es tu mejor marca?".
La regla que se verifica es doble: (1) si el dato está en la frase, hay que
sacarlo; (2) si NO está o es ambiguo, hay que devolver None con un motivo —
nunca inventar un número.

    python -m services.testbed.test_parser_marcas
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.llm_service import _parsear_marca_dx  # noqa: E402

# (frase, esperado) — esperado None significa "no adivines, pregunta"
CASOS: list[tuple[str, str, tuple[float, int] | None]] = [
    # ── forma canónica: lo único que funcionaba antes ────────────────────────
    ("canónico", "100x5",                                   (100, 5)),
    ("canónico", "100 x 5",                                 (100, 5)),
    ("canónico", "100 por 5",                               (100, 5)),
    ("canónico", "sentadilla 140 por 3",                    (140, 3)),
    ("canónico", "peso muerto 180 por 2",                   (180, 2)),
    ("canónico", "100kg x 5",                               (100, 5)),
    ("canónico", "en banca lo último que hice fueron 100 por 5", (100, 5)),

    # ── EL BUG: verbo de ejecución en vez de 'x' ─────────────────────────────
    ("verbo",    "dominadas con 20 kilos de lastre saqué 6", (20, 6)),
    ("verbo",    "con 100 kilos hice 5",                    (100, 5)),
    ("verbo",    "saqué 6 con 20 kilos",                    (20, 6)),
    ("verbo",    "con 140 kg logré 3",                      (140, 3)),
    ("verbo",    "me salieron 8 con 90 kilos",              (90, 8)),
    ("verbo",    "con 20 kg de lastre me salen 6",          (20, 6)),
    ("verbo",    "levanté 180 kilos, saqué 2",              (180, 2)),
    ("verbo",    "pude hacer 4 con 150 kg",                 (150, 4)),

    # ── unidad de repeticiones explícita ─────────────────────────────────────
    ("unidad",   "6 reps con 20 kg",                        (20, 6)),
    ("unidad",   "100 kilos, 5 repeticiones",               (100, 5)),
    ("unidad",   "100 kilos 5 veces",                       (100, 5)),
    ("unidad",   "hice 3 series de 10 reps con 60 kg",      (60, 10)),
    ("unidad",   "20 kg de lastre por 6 repes",             (20, 6)),

    # ── el peso ligero: la heurística 'el mayor es el peso' se rompe aquí ────
    ("ligero",   "elevaciones con 8 kg saqué 12",           (8, 12)),
    ("ligero",   "12 reps con 8 kilos",                     (8, 12)),

    # ── palabra numérica ─────────────────────────────────────────────────────
    ("palabra",  "180 kilos dos veces",                     (180, 2)),
    ("palabra",  "100 kg tres repeticiones",                (100, 3)),

    # ── un solo número: es su 1RM, y eso sigue siendo válido ─────────────────
    ("1rm",      "120",                                     (120, 1)),
    ("1rm",      "mi 1rm es 120",                           (120, 1)),
    ("1rm",      "160 kilos",                               (160, 1)),

    # ── AMBIGUO: antes inventaba, ahora pregunta ────────────────────────────
    ("pregunta", "100 kilos al fallo",                      None),
    ("pregunta", "saqué las máximas con 100 kg",            None),
    ("pregunta", "como 6 o 7",                              None),
    ("pregunta", "no sé mi marca",                          None),
    ("pregunta", "nunca lo he medido",                      None),
    ("pregunta", "unas 3 o 4 series de 8 a 10 con 80",      None),

    # ── decimales y separadores ──────────────────────────────────────────────
    ("decimal",  "102.5 x 3",                               (102.5, 3)),
    ("decimal",  "102,5 kg por 3",                          (102.5, 3)),
    ("decimal",  "con 62.5 kilos saqué 8",                  (62.5, 8)),

    # ── reps fuera del rango estimable: se leen bien, el flujo las rechaza ───
    ("extremo",  "100 kg x 20",                             (100, 20)),
    # El typo clásico. Se LEE bien (30 kg, 78 reps) para que el flujo de arriba
    # lo rechace con su mensaje bueno: "¿fueron 7-8 reps o de verdad 78?".
    ("extremo",  "30 kg por 78",                            (30, 78)),
    ("extremo",  "con 60 kilos saqué 25",                   (60, 25)),

    # ── ruido que NO debe convertirse en una marca ──────────────────────────
    ("ruido",    "no me acuerdo la verdad",                 None),
    ("ruido",    "creo que nunca lo he probado",            None),
    ("ruido",    "eso depende del día",                     None),
]

# El batch parsea VARIAS marcas de un mensaje. Aquí solo se comprueba el reparto
# ejercicio -> (peso, reps); el guardado se prueba en el onboarding end-to-end.
CASOS_BATCH: list[tuple[str, dict[str, tuple[float, int]]]] = [
    ("banca 100x5, sentadilla 140x3 y peso muerto 180x2",
     {"press banco plano": (100, 5), "sentadilla": (140, 3), "peso muerto": (180, 2)}),
    ("en banca saqué 5 con 100 kilos, en sentadilla 3 con 140 kilos",
     {"press banco plano": (100, 5), "sentadilla": (140, 3)}),
    ("dominadas con 20 kg de lastre saqué 6",
     {"dominadas": (20, 6)}),
    ("banca 100 kilos al fallo",
     {}),                       # ambiguo: no se guarda nada, ni se inventa
]


def main() -> int:
    anchos = max(len(f) for _c, f, _e in CASOS)
    ok = fallos = 0
    cat_actual = None
    print(f"\n{len(CASOS)} formas reales de reportar una marca\n")
    for cat, frase, esperado in CASOS:
        if cat != cat_actual:
            print(f"\n  ── {cat} ──")
            cat_actual = cat
        marca, motivo = _parsear_marca_dx(frase)
        bien = marca == (esperado if esperado is None else
                         (float(esperado[0]), int(esperado[1])))
        ok += bien
        fallos += not bien
        sello = "ok   " if bien else "FALLA"
        got = f"{marca[0]:g} kg x {marca[1]}" if marca else "(pregunta)"
        linea = f"  {sello} {frase:<{anchos}}  ->  {got}"
        if not bien:
            esp = f"{esperado[0]:g} kg x {esperado[1]}" if esperado else "(pregunta)"
            linea += f"   ESPERADO: {esp}"
        print(linea)
        if marca is None and motivo:
            print(f"        motivo: {motivo}")

    # ── batch: varias marcas en un mismo mensaje ─────────────────────────────
    print("\n  ── batch (varias marcas de golpe) ──")
    from services.llm_service import _KW_EJERCICIO, RM_CANONICO
    import re as _re
    for mensaje, esperado in CASOS_BATCH:
        obtenido: dict[str, tuple[float, int]] = {}
        for parte in _re.split(r"[,;\n]|\s+y\s+", mensaje.lower()):
            campo = next((c for kws, c in _KW_EJERCICIO if any(k in parte for k in kws)), None)
            if not campo:
                continue
            marca, _m = _parsear_marca_dx(parte)
            if marca:
                obtenido[RM_CANONICO[campo]] = (marca[0], marca[1])
        esp = {k: (float(v[0]), int(v[1])) for k, v in esperado.items()}
        bien = obtenido == esp
        ok += bien
        fallos += not bien
        print(f"  {'ok   ' if bien else 'FALLA'} {mensaje}")
        print(f"        -> {obtenido or '(nada, como debe ser)'}")
        if not bien:
            print(f"        ESPERADO: {esp}")

    print(f"\n{'='*70}\n{ok} ok, {fallos} fallos\n")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
