"""test_aritmetica_coach.py — propiedades que los números del coach deben cumplir.

No comprueba valores concretos (esos son discutibles y dependen de la fórmula),
sino PROPIEDADES que cualquier estimador de 1RM y cualquier prescripción de carga
tienen que cumplir sí o sí. Cuando una falla, el atleta recibe un número
imposible y todo el plan que se deriva de él queda contaminado.

Los cuatro bugs que motivaron cada bloque, hallados el 2026-07-30:

  · 1 dominada con 20 kg de lastre devolvía un 1RM de lastre de 18,95 kg, y
    1-2 dominadas a peso corporal guardaban un 1RM de 0,00 kg.
  · Un back-off ligero al cierre de la sesión (105 kg x8 y luego 70 kg x10)
    anclaba la siguiente prescripción en 72,5 kg.
  · Una progresión lineal limpia (100 → 107,5 kg x5 @RPE8) recibía una
    recomendación de 100 kg: sobrecarga REGRESIVA.
  · El piso de "barra vacía" mandaba 20 kg de curl a quien mueve 12.

Uso:  python3 -u -m services.testbed.test_aritmetica_coach
"""
import os
import sys
import tempfile
from datetime import date, timedelta

from services import db_service
from services.calculadora_rm import (TipoEjercicio, calcular_1rm,
                                     estimacion_hibrida)

_TMP = tempfile.mkdtemp(prefix="aritmetica_")
# _asegurar_esquema() crea las tablas vía db_service, que tiene SU PROPIA ruta:
# hay que redirigir las dos o el esquema se crea en un sitio y se lee en otro.
db_service.ruta_db = os.path.join(_TMP, "tele.db")

from services import motor_predictivo as mp                       # noqa: E402

mp.RUTA_DB = os.path.join(_TMP, "tele.db")
mp.RUTA_DB_ATLETAS = os.path.join(_TMP, "atletas.db")   # ausente -> incremento 2.5

PL = TipoEjercicio.PESO_LIBRE
U = "5215500000QA"
fallos: list[str] = []


def chk(titulo: str, condicion: bool, detalle: str = "") -> None:
    print(f"  {'OK ' if condicion else 'FALLA'}  {titulo}" + (f"  ({detalle})" if detalle else ""))
    if not condicion:
        fallos.append(titulo)


def sembrar(filas):
    """filas: (dias_atras, ejercicio, peso, reps, rpe)"""
    with mp._con() as con:
        con.execute("DELETE FROM series_log WHERE numero_usuario=?", (U,))
        for d, ej, w, r, rpe in filas:
            f = (date.today() - timedelta(days=d)).isoformat() + " 18:00:00"
            con.execute(
                "INSERT INTO series_log (numero_usuario, fecha_hora, ejercicio, "
                "peso_kg, repeticiones, rpe_reportado) VALUES (?,?,?,?,?,?)",
                (U, f, ej, w, r, rpe))


def main() -> int:
    print("\n1. COTA FÍSICA — el 1RM nunca puede ser menor que lo ya levantado")
    for reps in range(1, 16):
        v = calcular_1rm(PL, reps, 100.0)
        chk(f"peso libre {reps:2d} reps", v >= 100.0, f"{v}") if reps in (1, 8, 15) else None
        if v < 100.0:
            fallos.append(f"peso libre {reps} reps < 100")

    d = calcular_1rm(TipoEjercicio.LASTRADO, 1, 20.0, peso_corporal=85.0)
    chk("1 dominada con +20 kg -> 1RM de lastre >= 20",
        d["1rm_solo_lastre_kg"] >= 20.0, f"{d['1rm_solo_lastre_kg']}")

    d = calcular_1rm(TipoEjercicio.CALISTENIA_PURA, 1, 0.0, peso_corporal=85.0)
    chk("1 dominada a peso corporal -> fuerza total >= 85",
        d["1rm_fuerza_total_kg"] >= 85.0, f"{d['1rm_fuerza_total_kg']}")

    d = calcular_1rm(TipoEjercicio.MAQUINA, 1, 100.0)
    chk("1 rep en máquina con 100 kg -> 1RM >= 100", d >= 100.0, f"{d}")

    print("\n2. MONOTONÍA — más repeticiones con el mismo peso ⇒ 1RM mayor")
    print("   (si baja, el atleta mejora y el coach le dice que empeoró)")
    for peso in (60.0, 100.0):
        prev, malos = None, []
        for reps in range(1, 16):          # 1-15 es el rango que el sistema acepta
            v = estimacion_hibrida(peso, reps)
            if prev is not None and v < prev - 1e-9:
                malos.append(f"{reps}({v:.2f}<{prev:.2f})")
            prev = v
        chk(f"peso {peso:g} kg, reps 1-15", not malos, ", ".join(malos))

    print("\n3. IDA Y VUELTA — prescribir y volver a estimar cierra el círculo")
    peor = 0.0
    for e1rm in (100.0, 150.0):
        for reps in (1, 3, 5, 8, 10, 12):
            for rpe in (7.0, 8.0, 9.0, 10.0):
                carga = e1rm * mp.pct_prescripcion(reps, rpe)
                vuelta, _ = mp.e1rm_serie(carga, reps, rpe)
                peor = max(peor, abs(vuelta - e1rm))
    chk("el error de ida y vuelta es despreciable", peor <= 0.15, f"peor {peor:.3f} kg")

    print("\n4. SOBRECARGA PROGRESIVA — no mandar menos de lo ya demostrado")
    sembrar([(21, "sentadilla", 100.0, 5, 8.0),
             (14, "sentadilla", 102.5, 5, 8.0),
             (7,  "sentadilla", 105.0, 5, 8.0),
             (2,  "sentadilla", 107.5, 5, 8.0)])
    r = mp.predecir_carga_optima(U, "sentadilla", 5, 8.0)
    chk("progresión limpia 100->107.5: no retrocede",
        r.get("ok") and r["carga_recomendada_kg"] >= 107.5,
        f"recomienda {r.get('carga_recomendada_kg')} kg tras hacer 107.5")

    print("\n5. BACK-OFF — una serie ligera de cierre no puede anclar la carga")
    sembrar([(14, "press banca", 100.0, 8, 8.0),
             (7,  "press banca", 102.5, 8, 8.0),
             (2,  "press banca", 105.0, 8, 8.5),     # top
             (2,  "press banca", 70.0, 10, 6.0)])    # back-off, mismo día
    r = mp.predecir_carga_optima(U, "press banca", 8, 8.0)
    chk("mueve 105, no se le manda un peso de back-off",
        r.get("ok") and r["carga_recomendada_kg"] >= 95.0,
        f"recomienda {r.get('carga_recomendada_kg')} kg")

    print("\n6. ACCESORIOS LIGEROS — el piso de 'barra vacía' no aplica a todo")
    sembrar([(14, "curl", 10.0, 12, 8.0),
             (7,  "curl", 11.0, 12, 8.0),
             (2,  "curl", 12.0, 12, 8.5)])
    r = mp.predecir_carga_optima(U, "curl", 12, 8.0)
    c = r.get("carga_recomendada_kg")
    chk("no le manda 20 kg de curl a quien mueve 12", r.get("ok") and c < 20.0, f"{c} kg")
    chk("ni le hace retroceder más de un escalón", r.get("ok") and c >= 11.0, f"{c} kg")

    print("\n7. PLAN DUP — objetivos de reps distintos dan cargas distintas")
    sembrar([(21, "sentadilla", 120.0, 3, 8.0), (18, "sentadilla", 100.0, 10, 8.0),
             (14, "sentadilla", 122.5, 3, 8.0), (11, "sentadilla", 102.5, 10, 8.0),
             (7,  "sentadilla", 125.0, 3, 8.0), (3,  "sentadilla", 105.0, 10, 8.0)])
    c3 = mp.predecir_carga_optima(U, "sentadilla", 3, 8.0).get("carga_recomendada_kg")
    c10 = mp.predecir_carga_optima(U, "sentadilla", 10, 8.0).get("carga_recomendada_kg")
    chk("3 reps pesa más que 10 reps", c3 > c10, f"3 reps={c3}, 10 reps={c10}")
    chk("ninguno retrocede", c3 >= 125.0 and c10 >= 105.0, f"{c3} / {c10}")

    print("\n8. NUNCA por encima del 1RM estimado")
    sembrar([(14, "peso muerto", 140.0, 5, 8.0),
             (7,  "peso muerto", 142.5, 5, 8.0),
             (2,  "peso muerto", 145.0, 5, 8.0)])
    r = mp.predecir_carga_optima(U, "peso muerto", 5, 8.0)
    chk("carga <= e1RM estimado",
        r["carga_recomendada_kg"] <= r["senales"]["e1rm_estimado_kg"],
        f"{r['carga_recomendada_kg']} vs e1RM {r['senales']['e1rm_estimado_kg']}")

    print("\n" + "=" * 60)
    if fallos:
        print(f"{len(fallos)} FALLOS:")
        for f in fallos:
            print(f"  - {f}")
    else:
        print("Todos los checks pasan.")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
