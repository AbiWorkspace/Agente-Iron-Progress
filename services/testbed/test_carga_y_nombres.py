# -*- coding: utf-8 -*-
"""
test_carga_y_nombres.py — el nudo que dejó al atleta haciendo las cuentas a mano.

Sesión real del 2026-07-30. Tres fallos encadenados, uno detrás de otro:

  1. El atleta sustituye 'Press Banco Plano' por 'Press Pecho con Mancuernas'.
     `obtener_rutina_del_json` reasignaba `base` al nombre NUEVO y buscaba el 1RM
     con él. La marca vive bajo el nombre original, así que no la encontraba y la
     línea salía "— 70% RM", sin kilos. Medido: 7 de los 9 ejercicios del día.

  2. Sin kilos en la fuente, el modelo multiplicaba (cosa que el prompt le
     prohíbe), el validador lo cazaba ([KG-INVENTADOS] [110.0]) y respondía
     "prefiero no aventarte cifras al aire". El atleta acabó escribiendo
     "41kg es el 70%" él mismo.

  3. Al reportar 45 kg x 10 de ese ejercicio, el modelo emitió "press banca
     plano"; `_resolver_ejercicio_del_plan` lo resolvió por coincidencia parcial
     al nombre CRUDO del plan y la serie de MANCUERNAS entró como banca con
     barra: su 1RM cayó de 157,1 a 59,3 kg y con él todas las cargas del plan.

    python -m services.testbed.test_carga_y_nombres
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import llm_service as L  # noqa: E402

NUM = "99900000042"

PLAN = [{
    "Semana_Abs": 1, "Enfoque": "Torso",
    "Ejercicios": [
        {"Ejercicio": "Press Banco Plano", "Series": 3, "Reps": 10, "RPE": 7.5, "1RM": "70.0%"},
        {"Ejercicio": "Dominadas Lastradas", "Series": 4, "Reps": 10, "RPE": 7.5, "1RM": "70.0%"},
        {"Ejercicio": "Vuelos Laterales", "Series": 2, "Reps": 12, "RPE": 8.5, "1RM": "70.0%"},
    ]}, {
    "Semana_Abs": 1, "Enfoque": "Pierna",
    "Ejercicios": [
        {"Ejercicio": "Sentadillas", "Series": 3, "Reps": 10, "RPE": 7.5, "1RM": "70.0%"},
    ]},
]

# Lo que el sistema tiene guardado: la banca con BARRA a 157,1 kg (145x3).
RMS = {"press banco plano": 157.1, "sentadilla": 200.0, "dominadas": 30.0}

# El atleta cambió la banca por mancuernas: es lo que entrena de verdad.
OVERRIDES = {"ejercicios": {
    "press banco plano": {"nuevo": "Press Pecho con Mancuernas",
                          "alcance": "permanente", "fecha": "2026-07-29"}}}

ok = fallos = 0


def chk(desc: str, cond: bool, detalle: str = "") -> None:
    global ok, fallos
    ok += bool(cond)
    fallos += not cond
    print(f"  {'✓' if cond else '✗ FALLA'} {desc}" + (f"  ({detalle})" if detalle else ""))


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="carga_test_")
    carpeta = Path(tmp) / NUM
    carpeta.mkdir(parents=True)
    (carpeta / "1_acumulamiento.json").write_text(
        json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")

    L._carpeta_plan = lambda n, _b=Path(tmp): _b / str(n).strip()
    L._cargar_overrides = lambda n: json.loads(json.dumps(OVERRIDES))
    L.cargar_rms = lambda n: dict(RMS)
    L._incremento_barra = lambda n: 2.5
    for f in getattr(L, "_ARCHIVOS_PLAN", []):
        L._invalidar_json(carpeta / f)

    print("\n[1] La sustitución NO puede dejar el ejercicio sin kilos")
    dia = L.obtener_rutina_del_json(NUM, 1, 1, solo_hoy=True)
    print("\n" + "\n".join("      " + l for l in dia.splitlines()) + "\n")
    linea_press = next((l for l in dia.splitlines() if "Press Pecho con Mancuernas" in l), "")
    chk("el ejercicio sustituido aparece con su nombre nuevo", bool(linea_press))
    chk("y LLEVA kilos (antes: '— 70% RM' a secas)", " kg*" in linea_press,
        linea_press.strip())
    # 157.1 * 0.70 = 109.97 -> 110 con saltos de 2.5
    chk("los kilos salen del 1RM del ejercicio original", "*110 kg*" in linea_press)
    chk("se avisa de que la carga es ESTIMADA, no una marca suya",
        "CARGA ESTIMADA" in dia)

    print("\n[2] Sin ninguna marca: 'peso por calibrar', nunca un % suelto")
    chk("el accesorio dice 'peso por calibrar'",
        any("Vuelos Laterales" in l and "peso por calibrar" in l for l in dia.splitlines()))
    chk("ninguna línea deja un porcentaje sin kilos ni aviso",
        not any(l.strip().startswith("•") and "% RM)" not in l and "kg*" not in l
                and "calibrar" not in l and "peso corporal" not in l
                for l in dia.splitlines()))
    chk("el bloque instruye qué hacer con los 'por calibrar'",
        "SIN MARCA REGISTRADA" in dia and "PROHIBIDO calcular el porcentaje" in dia)

    print("\n[3] Lastre 0 = a peso corporal, no 'sin kilos'")
    RMS["dominadas"] = 0
    L._invalidar_json(carpeta / "1_acumulamiento.json")
    dia0 = L.obtener_rutina_del_json(NUM, 1, 1, solo_hoy=True)
    chk("dominadas con lastre 0 se leen como peso corporal",
        any("Dominadas" in l and "peso corporal" in l for l in dia0.splitlines()))
    RMS["dominadas"] = 30.0

    print("\n[4] EL BUG DEL 1RM: la serie va al ejercicio que ENTRENA")
    casos = [
        ("press banca plano",           "Press Pecho con Mancuernas"),
        ("Press Banco Plano",           "Press Pecho con Mancuernas"),
        ("press banca",                 "Press Pecho con Mancuernas"),
        ("press pecho con mancuernas",  "Press Pecho con Mancuernas"),
        ("sentadillas",                 "Sentadillas"),
        ("press militar",               None),
    ]
    for dicho, esperado in casos:
        got = L._resolver_ejercicio_del_plan(NUM, dicho)
        chk(f"{dicho!r} -> {esperado!r}", got == esperado, f"dio {got!r}")
    chk("y sigue disponible la clave CRUDA del plan para el override",
        L._resolver_ejercicio_del_plan(NUM, "press banca", vigente=False) == "Press Banco Plano")

    print("\n[5] Sustituir dos veces el mismo hueco escribe la clave del PLAN")
    escrito: dict = {}
    L._guardar_overrides = lambda n, d: escrito.update(d)
    L._guardar_override_ejercicio(NUM, "Press Pecho con Mancuernas",
                                  "Press Inclinado con Barra", "permanente")
    claves = list((escrito.get("ejercicios") or {}).keys())
    chk("la clave sigue siendo la del plan, no la del sustituto",
        claves == ["press banco plano"], f"claves={claves}")
    chk("y apunta al ejercicio nuevo",
        (escrito.get("ejercicios") or {}).get("press banco plano", {}).get("nuevo")
        == "Press Inclinado con Barra")

    print(f"\n{'='*74}\n  {ok} ok, {fallos} fallos\n{'='*74}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
