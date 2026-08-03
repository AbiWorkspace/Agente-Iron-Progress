# -*- coding: utf-8 -*-
"""
test_validador_kilajes.py — ningún kilo llega al atleta sin existir en una fuente.

Sesión real del 2026-07-30. El validador tenía el criterio invertido: bloqueaba
cifras legítimas y dejaba pasar las inventadas.

    16:40:29 IN : "¿Cómo no vas a tener mis datos si te los di al inicio?"
    16:40:54 LOG: [KG-CHECK] bypass: usuario sin cifras y salida sin forma de rutina
    16:40:54 OUT: "Aquí tienes los pesos calculados... ~55 kg ... ~60 kg ... ~95 kg"

Tres kilajes inventados directos al atleta, en el formato más peligroso que hay:
una lista de pesos que se va a cargar a la espalda. El bypass decidía con dos
señales que no dicen nada sobre si la SALIDA trae kilos falsos —si el mensaje del
atleta traía cifras, y un contador de líneas con series o reps— y una tabla de
kilos puros puntuaba 1 contra un umbral de 2.

Mientras tanto, ese mismo turno el validador bloqueaba los 110 kg que SÍ venían
de una herramienta.

Lo que se fija aquí: el chequeo corre siempre que haya kilos, y la whitelist
dinámica es lo bastante ancha para no producir falsos positivos.

    python -m services.testbed.test_validador_kilajes
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import llm_service as L  # noqa: E402

NUM = "99900000099"

# La rutina del día tal y como sale ahora: con los kg ya calculados y el aviso de
# los que no tienen marca (ver test_carga_y_nombres).
RUTINA = """*HOY — Semana 1 · Día 1 · Torso*
  • Press Pecho con Mancuernas: 3x10 — *110 kg* (70% RM) @RPE 7.5
  • Dominadas Lastradas: 4x10 — *25 kg* (70% RM) @RPE 7.5
  • Vuelos Laterales: 2x12 — *peso por calibrar* (70% RM) @RPE 8.5
[SISTEMA — SIN MARCA REGISTRADA]: no hay 1RM guardado de Vuelos Laterales, Remo en Maquina."""

ok = fallos = 0


def chk(desc: str, cond: bool, detalle: str = "") -> None:
    global ok, fallos
    ok += bool(cond)
    fallos += not cond
    print(f"  {'✓' if cond else '✗ FALLA'} {desc}" + (f"  ({detalle})" if detalle else ""))


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="kg_test_")
    L._ruta_historial_rm = lambda n, _d=tmp: os.path.join(_d, f"rm_{n}.json")
    L._ruta_peso = lambda n, _d=tmp: os.path.join(_d, f"peso_{n}.json")
    L.cargar_perfil = lambda n: {"peso_kg": 84.5, "altura_m": 1.73, "edad": 24,
                                 "porcentaje_grasa": 20.0, "dias_semana": 6}
    L.cargar_rms = lambda n: {"press banco plano": 157.1, "sentadilla": 216.7,
                              "peso muerto": 258.1, "dominadas": 36.9}
    L._incremento_barra = lambda n: 2.5

    # El mensaje del atleta NO trae cifras: es justo el caso que activaba el bypass.
    fuentes = "\n".join(["¿Cómo no vas a tener mis datos si te los di al inicio?",
                         RUTINA, "SERIES REGISTRADAS HOY: ninguna todavía.", ""])
    wl = L._construir_whitelist_kgs(NUM, fuentes, [], "")

    def inventados(salida: str) -> list:
        return L._verificar_kgs_salida(salida, wl)

    print("\n[1] EL BUG: la tabla de pesos que se coló")
    tabla = ("Aquí tienes los pesos calculados para tu sesión de hoy:\n"
             "*Press Pecho con Mancuernas:* ~55 kg por 10 reps\n"
             "*Remo en Maquina:* ~60 kg\n"
             "*Press Inclinado con Barra:* ~95 kg")
    chk("los tres kilajes se detectan", inventados(tabla) == [55.0, 60.0, 95.0],
        str(inventados(tabla)))
    chk("y la señal vieja NO la habría visto (puntúa 1, umbral era 2)",
        L._parece_rutina_inventada(tabla) < 2, f"puntúa {L._parece_rutina_inventada(tabla)}")

    print("\n[2] Un peso inventado suelto tampoco pasa")
    chk("en mitad de una charla sin cifras", inventados("Métele 72 kg y me cuentas.") == [72.0])

    print("\n[3] Lo legítimo NO se bloquea (falsos positivos)")
    legitimos = [
        ("transcribe la rutina con sus kg", "Hoy: Press Pecho con Mancuernas 3x10 con *110 kg* @RPE 7.5"),
        ("las dominadas lastradas del plan", "Dominadas Lastradas 4x10 con *25 kg* de lastre."),
        ("cita su 1RM guardado", "Tu máximo en banca sigue en 157.1 kg, bestia."),
        ("cita otro RM del repositorio", "Con 216.7 kg de sentadilla estás en nivel avanzado."),
        ("discos y barra vacía (triviales)", "Pon 2.5 kg por lado sobre la barra de 20 kg."),
        ("redondeo conversacional", "Vas con 110 kg, ni uno más."),
        ("su peso corporal", "A 84.5 kg de peso corporal vas bien."),
        ("sin ningún kilaje", "¿Cómo te fue la serie bro?"),
        ("habla de calibrar sin dar número", "Los vuelos van por calibrar: dime con cuánto los mueves."),
    ]
    for desc, texto in legitimos:
        chk(desc, inventados(texto) == [], str(inventados(texto)))

    print("\n[4] Lo que el atleta escribe entra a la whitelist")
    wl2 = L._construir_whitelist_kgs(
        NUM, "hice 90kg 11 reps a rpe 7.5\n" + RUTINA, [], "")
    chk("el coach puede confirmar los 90 kg que él reportó",
        L._verificar_kgs_salida("Anotado: 90 kg x 11 @RPE 7.5", wl2) == [])

    print("\n[5] Los payloads de las herramientas también")
    wl3 = L._construir_whitelist_kgs(
        NUM, RUTINA, [], '[predecir_carga_optima]: {"carga_recomendada_kg": 97.5}')
    chk("el kilaje que devolvió la tool NO se marca inventado",
        L._verificar_kgs_salida("Entra con 97.5 kg en la top", wl3) == [])

    print("\n[6] El bypass ya no existe en el código")
    fuente = Path(L.__file__).read_text(encoding="utf-8")
    chk("no queda la variable del criterio viejo",
        "usuario_trae_cifras = " not in fuente)
    chk("ni la condición que lo saltaba",
        "aplicar_chequeo = " not in fuente)
    chk("la plantilla de último recurso ya no manda a 'ver mi rutina'",
        "prefiero no aventarte cifras al aire" not in fuente)

    print(f"\n{'='*74}\n  {ok} ok, {fallos} fallos\n{'='*74}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
