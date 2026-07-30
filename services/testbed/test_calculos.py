# -*- coding: utf-8 -*-
"""
test_calculos.py — la aritmética de la que cuelga todo lo demás.

El 1RM estimado decide los porcentajes del plan, y esos porcentajes son los kilos
que el atleta monta en la barra. Un error del 3 % aquí no se ve en ninguna
respuesta del coach: se ve seis semanas después, en una lesión o en un
estancamiento. Los cuatro fallos que motivaron este fichero (2026-07-30):

1. DOS e1RM CONVIVIENDO. El interceptor determinista usaba el híbrido de
   calculadora_rm SIN pasarle el RPE; el handler v9 usaba Epley+RIR. La misma
   serie daba hasta un 12 % de diferencia según quién la registrara, y el
   interceptor devolvía el mismo número tanto si al atleta le sobraban cuatro
   repeticiones como si había ido al fallo.

2. UN SINGLE MÁXIMO NO VALÍA EL 100 %. Epley sin anclar da peso × 31/30: un 1RM
   real de 100 kg se guardaba como 103.3, y prescribir "un single a RPE 10"
   mandaba montar el 96.8 % del 1RM.

3. LA PREDICCIÓN IGNORABA LAS REPS OBJETIVO. El clamp se comparaba con la última
   carga top fuera cual fuera su objetivo, así que tras un día de triples
   pesados la función devolvía el MISMO kilaje para 3, 8 o 12 repeticiones.

4. EL NIVEL DE FUERZA SE MEDÍA CON EL BAREMO EQUIVOCADO. `genero == "hombre"`
   exacto: cualquier otra cosa ("masculino", "varón", "") caía en el baremo
   femenino y el atleta salía sobrevalorado — y de ahí, con una rutina de nivel
   superior al suyo.

    python -m services.testbed.test_calculos
"""
from __future__ import annotations

import datetime
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from services import llm_service as L                                  # noqa: E402
from services import motor_predictivo as mp                            # noqa: E402
from services.calculadora_rm import TipoEjercicio, calcular_1rm        # noqa: E402

NUM = "5215500000910"

OK = FALLOS = 0


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global OK, FALLOS
    if condicion:
        OK += 1
        print(f"  ✓ {nombre}")
    else:
        FALLOS += 1
        print(f"  ✗ {nombre}" + (f"  → {detalle}" if detalle else ""))


def bloque_un_solo_e1rm():
    print("\n[1] Un solo e1RM en todo el sistema")
    for peso, reps, rpe in [(100, 5, 6), (100, 5, 8), (100, 5, 10), (140, 3, 8), (60, 12, 7)]:
        interceptor = L._e1rm_de_serie(peso, reps, rpe)
        handler = mp.e1rm_serie(peso, reps, rpe)[0]
        chk(f"{peso}x{reps}@RPE{rpe}: interceptor y handler coinciden ({interceptor} kg)",
            abs(interceptor - handler) < 0.05, f"{interceptor} vs {handler}")

    suave = L._e1rm_de_serie(100, 5, 6)      # le sobraban 4 reps
    duro = L._e1rm_de_serie(100, 5, 10)      # al fallo
    chk("una serie con 4 reps en reserva estima MÁS 1RM que la misma al fallo",
        suave > duro + 5, f"suave {suave} vs duro {duro}")

    sin_rpe = L._e1rm_de_serie(100, 5, None)
    chk("sin RPE (marca declarada) se usa el híbrido, no Epley",
        abs(sin_rpe - calcular_1rm(TipoEjercicio("peso_libre"), 5, 100)) < 0.05, f"{sin_rpe}")


def bloque_single_maximo():
    print("\n[2] Una repetición a RPE 10 ES el 1RM (por definición)")
    chk("e1rm_serie(100, 1, 10) == 100", mp.e1rm_serie(100, 1, 10)[0] == 100.0,
        f"{mp.e1rm_serie(100, 1, 10)[0]}")
    chk("pct_prescripcion(1, 10) == 100 %", mp.pct_prescripcion(1, 10) == 1.0,
        f"{mp.pct_prescripcion(1, 10)}")
    chk("y el híbrido dice lo mismo",
        calcular_1rm(TipoEjercicio("peso_libre"), 1, 100) == 100.0)
    # Lo de al lado no se toca: 2 reps o 1 rep a RPE 9 sí estiman por encima.
    chk("2 reps @10 sigue estimando por encima", mp.e1rm_serie(100, 2, 10)[0] > 100)
    chk("1 rep @9 sigue estimando por encima", mp.e1rm_serie(100, 1, 9)[0] > 100)


def bloque_prescripcion_rts():
    print("\n[3] El % del 1RM por (reps @ RPE) se parece a las tablas RTS")
    referencia = {(3, 8): 86.7, (5, 8): 81.1, (8, 8): 74.2, (5, 9): 83.7, (10, 7): 70.7}
    for (reps, rpe), esperado in referencia.items():
        got = mp.pct_prescripcion(reps, rpe) * 100
        chk(f"{reps} reps @RPE {rpe} → {got:.1f} % (RTS ≈{esperado} %)",
            abs(got - esperado) <= 1.5, f"desvío {got - esperado:+.1f} pts")


def _sembrar_dup(con, cuando_carga_reps_rpe):
    con.execute("DELETE FROM series_log WHERE numero_usuario=?", (NUM,))
    hoy = datetime.date.today()
    for dias, carga, reps, rpe in cuando_carga_reps_rpe:
        f = (hoy - datetime.timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
        con.execute("INSERT INTO series_log (numero_usuario, fecha_hora, ejercicio, "
                    "peso_kg, repeticiones, rpe_reportado) VALUES (?,?,?,?,?,?)",
                    (NUM, f, "sentadilla", carga, reps, rpe))
    con.commit()


def bloque_prediccion_dup():
    print("\n[4] En un plan DUP, la carga depende del objetivo de repeticiones")
    con = sqlite3.connect(str(RAIZ / "data" / "iron_progress_telemetria.db"))
    try:
        # Alterna día ligero (12 reps @7) y día pesado (3 reps @9), como el plan real.
        _sembrar_dup(con, [(10, 95, 12, 7), (7, 140, 3, 9), (4, 97.5, 12, 7), (1, 142.5, 3, 9)])
        cargas = {}
        for reps, rpe in [(3, 9), (8, 8), (12, 7)]:
            r = mp.predecir_carga_optima(NUM, "sentadilla", reps, rpe)
            cargas[reps] = r.get("carga_recomendada_kg")
        chk("tres objetivos, tres cargas distintas", len(set(cargas.values())) == 3, f"{cargas}")
        chk("el triple pesado pide más peso que las 12 repeticiones",
            (cargas[3] or 0) > (cargas[12] or 0) + 20, f"{cargas}")
        chk("y las 8 reps quedan en medio",
            (cargas[12] or 0) < (cargas[8] or 0) < (cargas[3] or 0), f"{cargas}")
        chk("ninguna prescripción supera el e1RM estimado",
            all((c or 0) < 160 for c in cargas.values()), f"{cargas}")

        # El e1RM de un día de 12 reps no puede leerse como si fuera un single.
        ses = mp._sesiones(mp.leer_series(NUM, "sentadilla"))
        dia_ligero = min(ses, key=lambda s: s["carga_top"])
        chk("el e1RM de un día ligero es mayor que su carga top (no es un single)",
            dia_ligero["e1rm"] > dia_ligero["carga_top"],
            f"e1rm {dia_ligero['e1rm']} vs top {dia_ligero['carga_top']}")
    finally:
        con.execute("DELETE FROM series_log WHERE numero_usuario=?", (NUM,))
        con.commit()
        con.close()


def bloque_autorregulacion():
    print("\n[5] Autorregulación por overshoot de RPE")
    # Las alarmas se PERSISTEN y se cuentan por día: sin limpiar, la segunda
    # ejecución del test ve la alarma de la primera y todo pasa a descarga.
    limpio = NUM[:-1] + "1"
    con = sqlite3.connect(str(RAIZ / "data" / "iron_progress_telemetria.db"))
    try:
        con.execute("DELETE FROM eventos_autorregulacion WHERE numero_usuario IN (?, ?)",
                    (NUM, limpio))
        con.commit()
    finally:
        con.close()
    r = mp.autorregular_dia(NUM, "banca", rpe_reportado=8.0, rpe_objetivo=7.5,
                            series_restantes=4, carga_actual_kg=100)
    chk("medio punto de overshoot no cambia nada", r["accion"] == "mantener", f"{r['accion']}")
    r = mp.autorregular_dia(NUM, "banca", rpe_reportado=9.5, rpe_objetivo=7.5,
                            series_restantes=4, carga_actual_kg=100)
    chk("2 puntos de overshoot → −6 % de carga, montable (95 kg)",
        r["carga_kg"] == 95.0, f"{r['carga_kg']}")
    chk("y se recortan 2 series (4 → 2)", r["series_restantes"] == 2, f"{r['series_restantes']}")
    # Con OTRO atleta: la segunda alarma del mismo día convierte la sesión en
    # descarga (60 %), que es el comportamiento correcto pero mide otra cosa.
    r = mp.autorregular_dia(limpio, "banca", rpe_reportado=10, rpe_objetivo=5,
                            series_restantes=5, carga_actual_kg=100)
    chk("el ajuste por overshoot nunca pasa del 10 % (5 puntos → −10 %, no −15 %)",
        r["carga_kg"] == 90.0, f"{r['carga_kg']} ({r['accion']})")
    r2 = mp.autorregular_dia(limpio, "banca", rpe_reportado=9.5, rpe_objetivo=7.5,
                             series_restantes=4, carga_actual_kg=100)
    chk("y la SEGUNDA alarma del día sí convierte la sesión en descarga",
        r2["accion"] == "descarga_del_dia", f"{r2['accion']}")


def bloque_nivel_fuerza():
    print("\n[6] El nivel de fuerza se mide con el baremo correcto")
    marcas = dict(peso_kg=84, rm_banca_kg=104, rm_sentadilla_kg=152, rm_peso_muerto_kg=189)
    for genero in ("hombre", "masculino", "varón", "Hombre", ""):
        nivel = L.evaluar_nivel_fuerza({**marcas, "genero": genero})["nivel_sugerido"]
        chk(f"género {genero!r} usa el baremo masculino → intermedio",
            nivel == "intermedio", f"{nivel}")
    for genero in ("mujer", "femenino"):
        nivel = L.evaluar_nivel_fuerza({**marcas, "genero": genero})["nivel_sugerido"]
        chk(f"género {genero!r} usa el baremo femenino", nivel == "avanzado", f"{nivel}")

    sin_marcas = L.evaluar_nivel_fuerza({"peso_kg": 84, "genero": "hombre"})
    chk("sin marcas NO se emite un veredicto de nivel",
        sin_marcas.get("datos_insuficientes") is True, f"{sin_marcas}")
    sin_peso = L.evaluar_nivel_fuerza({"peso_kg": 0, "rm_banca_kg": 100, "genero": "hombre"})
    chk("sin peso corporal tampoco", sin_peso.get("datos_insuficientes") is True, f"{sin_peso}")


def bloque_redondeo():
    print("\n[7] Los kilos prescritos se pueden montar de verdad")
    for kg, inc in [(102.4, 2.5), (101.3, 2.5), (103.75, 2.5), (102.5, 5), (61.9, 1.25)]:
        a, b = L._redondear_carga(kg, inc), mp.redondear_carga(kg, inc)
        chk(f"{kg} kg con saltos de {inc} → {a} (una sola implementación)",
            abs(a - b) < 1e-9, f"llm {a} vs motor {b}")
        chk(f"  y {a} es múltiplo de {inc}", abs(round(a / inc) * inc - a) < 1e-9)


def main() -> int:
    print("\n" + "=" * 74)
    print("  CÁLCULOS — 1RM, prescripción, autorregulación y nivel")
    print("=" * 74)
    bloque_un_solo_e1rm()
    bloque_single_maximo()
    bloque_prescripcion_rts()
    bloque_prediccion_dup()
    bloque_autorregulacion()
    bloque_nivel_fuerza()
    bloque_redondeo()
    print("-" * 74)
    print(f"  {OK}/{OK + FALLOS} comprobaciones correctas")
    print("=" * 74 + "\n")
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
