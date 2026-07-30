# -*- coding: utf-8 -*-
"""
test_sustitucion_fantasma.py — no se puede sustituir lo que no está en el plan.

`sustituir_ejercicio` aceptaba cualquier cadena. Medido en una conversación real:
el atleta pidió cambiar el "press militar" —que NO está en su plan— y el sistema
escribió el override igualmente:

    {"ejercicios": {"press militar": {"nuevo": "Face Pulls", ...}}}

El coach respondió "he sustituido el Press Militar por Face Pulls en todo tu
plan". El plan siguió intacto: no hay ningún press militar que sustituir.

Se verifica que el resolutor (1) encuentra el ejercicio aunque el atleta lo
nombre a su manera, (2) devuelve el nombre EXACTO del plan —porque esa es la
clave con la que se guarda y se aplica el override— y (3) dice que no cuando el
movimiento sencillamente no está.

    python -m services.testbed.test_sustitucion_fantasma
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import llm_service as L  # noqa: E402

# Plan de torso/pierna equivalente al que genera el motor para 4 días.
PLAN = [{
    "Semana_Abs": 1, "Enfoque": "Torso",
    "Ejercicios": [
        {"Ejercicio": "Press Banco Plano"}, {"Ejercicio": "Remo en Maquina"},
        {"Ejercicio": "Press Pecho en Maquina"}, {"Ejercicio": "Jalon en Maquina"},
        {"Ejercicio": "Extension de Triceps Katana"},
        {"Ejercicio": "Curl de Biceps en Polea"},
        {"Ejercicio": "Vuelos Laterales"}, {"Ejercicio": "Crunch Abdominal"},
    ]}, {
    "Semana_Abs": 1, "Enfoque": "Pierna",
    "Ejercicios": [
        {"Ejercicio": "Sentadillas"}, {"Ejercicio": "Peso Muerto"},
        {"Ejercicio": "Sentadilla en Maquina Hack"},
        {"Ejercicio": "Peso Muerto Rumano con Mancuernas"},
        {"Ejercicio": "Extension de Cuadriceps"}, {"Ejercicio": "Curl Femoral"},
    ]},
]

# (cómo lo dice el atleta, nombre exacto esperado del plan o None)
CASOS: list[tuple[str, str, str | None]] = [
    # ── nombre literal ───────────────────────────────────────────────────────
    ("literal",  "Press Banco Plano",             "Press Banco Plano"),
    ("literal",  "press banco plano",             "Press Banco Plano"),
    ("literal",  "Sentadillas",                   "Sentadillas"),

    # ── como lo dice la gente ────────────────────────────────────────────────
    ("coloquial", "press banca",                  "Press Banco Plano"),
    ("coloquial", "la banca",                     "Press Banco Plano"),
    ("coloquial", "el peso muerto",               "Peso Muerto"),
    ("coloquial", "sentadilla",                   "Sentadillas"),
    ("coloquial", "el jalon",                     "Jalon en Maquina"),
    ("coloquial", "jalón",                        "Jalon en Maquina"),
    ("coloquial", "curl femoral",                 "Curl Femoral"),
    ("coloquial", "los vuelos laterales",         "Vuelos Laterales"),

    # ── EL BUG: no está en su plan ──────────────────────────────────────────
    ("fantasma", "press militar",                 None),
    ("fantasma", "hip thrust",                    None),
    ("fantasma", "zancadas",                      None),
    ("fantasma", "face pulls",                    None),
    ("fantasma", "prensa",                        None),
    ("fantasma", "fondos en paralelas",           None),

    # ── variantes distintas NO se confunden entre sí ────────────────────────
    ("variante", "peso muerto rumano",            "Peso Muerto Rumano con Mancuernas"),
    ("variante", "press inclinado",               None),   # tiene plano, no inclinado

    # ── AMBIGUO: hay dos candidatos, elegir a ciegas cambiaría el equivocado ─
    ("ambiguo",  "el curl",                       None),   # bíceps o femoral
    ("ambiguo",  "la extension",                  None),   # tríceps o cuádriceps
    ("ambiguo",  "el press",                      None),   # banco plano o pecho máquina

    # ── basura ───────────────────────────────────────────────────────────────
    ("basura",   "",                              None),
    ("basura",   "x",                             None),
]


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="plan_test_")
    num = "99900000001"
    carpeta = Path(tmp) / num
    carpeta.mkdir(parents=True)
    (carpeta / "1_acumulamiento.json").write_text(
        json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")

    # Se apunta el lector de planes a la carpeta temporal: el test no toca ni
    # lee el plan de ningún atleta real.
    L._carpeta_plan = lambda n, _base=Path(tmp): _base / str(n).strip()
    L._cargar_overrides = lambda n: {}
    for f in getattr(L, "_ARCHIVOS_PLAN", []):
        L._invalidar_json(carpeta / f)

    catalogo = L._ejercicios_del_plan(num)
    print(f"\nPlan de prueba: {len(catalogo)} ejercicios")
    print(f"  {', '.join(catalogo)}\n")
    if len(catalogo) != 14:
        print(f"  AVISO: se esperaban 14 ejercicios, se leyeron {len(catalogo)}")

    ok = fallos = 0
    ancho = max(len(f) for _c, f, _e in CASOS)
    cat = None
    for categoria, dicho, esperado in CASOS:
        if categoria != cat:
            print(f"  ── {categoria} ──")
            cat = categoria
        got = L._resolver_ejercicio_del_plan(num, dicho)
        bien = got == esperado
        ok += bien
        fallos += not bien
        linea = f"  {'ok   ' if bien else 'FALLA'} {dicho!r:<{ancho + 2}} -> {got!r}"
        if not bien:
            linea += f"   ESPERADO: {esperado!r}"
        print(linea)

    print(f"\n{'='*74}\n{ok} ok, {fallos} fallos\n")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
