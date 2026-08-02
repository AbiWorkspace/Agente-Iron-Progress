# -*- coding: utf-8 -*-
"""
test_intencion_borrado.py — contar lo que pasó no es pedir que se borre.

Sesión real del 2026-07-30:

    IN : "ya me hiciste mi plan de entrenamiento y ya borraste 3 veces, ok?
          solo quiero ahorita que me muestres mi plan de entrenamiento"
    LOG: [BORRAR] intención detectada -> pide confirmación
    OUT: "...el sistema me pide confirmar si quieres *borrar* tu plan"

El atleta pedía VER su plan y en la misma frase se le preguntó si quería
eliminarlo. Se le fueron tres turnos en desmentirlo, y acabó respondiendo
"No quiero borrarlo, no quiero, ya lo borraste una vez, no quiero".

La causa era el comodín `borra\\w*`, que caza igual el imperativo ("borra") que
el PRETÉRITO ("borraste", "borró", "borrado"). Narrar lo que uno cree que pasó
valía como orden. Ahora solo cuentan las formas con las que de verdad se pide
algo —imperativo, infinitivo y subjuntivo— y se descartan las negadas.

    python -m services.testbed.test_intencion_borrado
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import llm_service as L  # noqa: E402

ok = fallos = 0


def chk(desc: str, cond: bool, detalle: str = "") -> None:
    global ok, fallos
    ok += bool(cond)
    fallos += not cond
    print(f"  {'✓' if cond else '✗ FALLA'} {desc}" + (f"  ({detalle})" if detalle else ""))


# (mensaje, ¿es una petición de borrado?)
CASOS: list[tuple[str, str, bool]] = [
    # ── imperativo: la forma más directa ─────────────────────────────────────
    ("imperativo", "borra mi plan", True),
    ("imperativo", "borra mi plan por favor", True),
    ("imperativo", "bórralo todo", True),
    ("imperativo", "elimina mi rutina", True),
    ("imperativo", "elimíname el plan", True),
    ("imperativo", "mejor bórrame el plan", True),
    ("imperativo", "borra todo y empezamos de cero", True),

    # ── infinitivo con intención ─────────────────────────────────────────────
    ("infinitivo", "quiero borrar el plan", True),
    ("infinitivo", "quiero eliminar mi entrenamiento", True),
    ("infinitivo", "puedes borrar mi rutina?", True),
    ("infinitivo", "hay que borrar la rutina", True),
    ("infinitivo", "borrar el mesociclo", True),

    # ── subjuntivo de petición ───────────────────────────────────────────────
    ("subjuntivo", "quiero que borres mi plan", True),

    # ── verbos que faltaban en el catálogo ───────────────────────────────────
    ("nuevos", "resetea mi plan", True),
    ("nuevos", "reinicia mi rutina", True),

    # ── EL BUG: pretérito, o sea NARRAR lo que cree que pasó ─────────────────
    ("pretérito", "ya me hiciste mi plan de entrenamiento y ya borraste 3 veces, ok? "
                  "solo quiero ahorita que me muestres mi plan de entrenamiento", False),
    ("pretérito", "pero ya borraste el anterior o lo vas a volver a borrar", False),
    ("pretérito", "¿ya borraste mi plan?", False),
    ("pretérito", "el plan que borraste era el bueno", False),
    ("pretérito", "¿el plan se borró solo?", False),
    ("pretérito", "ya lo habías borrado el mesociclo", False),

    # ── negado: pide justo lo contrario ──────────────────────────────────────
    ("negado", "no quiero borrar mi plan", False),
    ("negado", "no me borres el plan", False),
    ("negado", "nunca borres mi rutina", False),
    ("negado", "sin borrar el plan, cámbiame un ejercicio", False),
    ("negado", "no quiero borrarlo, no quiero, ya lo borraste 1 vez, no quiero", False),
    ("negado", "menos mal que no borraste nada de mi rutina", False),

    # ── sin relación con borrar ──────────────────────────────────────────────
    ("ajeno", "muéstrame el plan de entrenamiento", False),
    ("ajeno", "¿qué me toca hoy?", False),
    ("ajeno", "borra ese mensaje", False),          # no menciona el plan
]


def main() -> int:
    cat = None
    for categoria, mensaje, esperado in CASOS:
        if categoria != cat:
            print(f"\n  ── {categoria} ──")
            cat = categoria
        got = L._pide_borrar_plan(mensaje)
        chk(f"{mensaje[:60]!r}", got == esperado,
            f"dio {got}, esperado {esperado}")

    print("\n  ── el orquestador usa el detector, no el regex pelado ──")
    fuente = Path(L.__file__).read_text(encoding="utf-8")
    chk("la rama de borrado llama a _pide_borrar_plan",
        "and _pide_borrar_plan(texto_puro)" in fuente)
    chk("y ya no decide con BORRAR_PLAN_RE.search directamente",
        "BORRAR_PLAN_RE.search(texto_puro)" not in fuente)

    print(f"\n{'='*74}\n  {ok} ok, {fallos} fallos\n{'='*74}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
