# -*- coding: utf-8 -*-
"""
test_claim_falso_honesto.py — el guardia anti-mentira no puede producir la mentira.

Sesión real del 2026-07-30. Tres veces seguidas:

    [RM-GUARDADO] press banco plano: 145.0kg x 3 -> 1RM 157.13
    [ONB-PARSE]   campo='rm_banca' exito=True
    [CLAIM-FALSO] afirma una acción NO ejecutada; reescritura honesta
    [OUT] -> "Seré honesto contigo bro: esa serie NO me quedó apuntada."

El dato SÍ estaba guardado. Dos agujeros distintos, con el mismo síntoma:

  a) `acciones_ok` nacía a mitad del orquestador, DESPUÉS del onboarding, así que
     todo lo que el onboarding escribía (marcas, peso, días) quedaba fuera de la
     contabilidad y el guardia lo leía vacío.
  b) El guardia solo mira las escrituras de ESTE turno. Cuando el atleta repite
     algo que ya mandó —lo más normal del mundo— no se escribe nada nuevo, y el
     coach acababa negando un dato que llevaba minutos guardado.

El daño no fue solo el mensaje: el atleta reenvió su marca de banca creyendo que
se había perdido, para entonces el onboarding ya iba por la sentadilla, y 145x3
se guardaron como sentadilla. De ahí salió la cadena de datos corruptos.

    python -m services.testbed.test_claim_falso_honesto
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import llm_service as L  # noqa: E402
from services import motor_predictivo as mp  # noqa: E402

NUM = "99900000055"
HOY = dt.datetime.now().strftime("%Y-%m-%d")

ok = fallos = 0


def chk(desc: str, cond: bool, detalle: str = "") -> None:
    global ok, fallos
    ok += bool(cond)
    fallos += not cond
    print(f"  {'✓' if cond else '✗ FALLA'} {desc}" + (f"  ({detalle})" if detalle else ""))


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="claim_test_")
    L._ruta_historial_rm = lambda n, _d=tmp: os.path.join(_d, f"rm_{n}.json")
    sin_series = lambda n, e=None, dias=180: []            # noqa: E731
    mp.leer_series = sin_series

    print("\n[1] El orquestador declara la contabilidad ANTES del onboarding")
    fuente = Path(L.__file__).read_text(encoding="utf-8")
    i_decl = fuente.find("acciones_ok: list[str] = []")
    i_onb = fuente.find("exito, feedback = procesar_respuesta_onboarding")
    # Ancla al inicio REAL del bloque: la cadena "REGISTRO DETERMINISTA" también
    # aparece arriba, en el comentario de las constantes.
    i_reg = fuente.find("accion_ejecutada = False")
    chk("acciones_ok existe antes de que el onboarding escriba", 0 < i_decl < i_onb,
        f"decl={i_decl} onboarding={i_onb}")
    chk("y antes del registro determinista", 0 < i_decl < i_reg)
    chk("no se redeclara a mitad (borraría lo del onboarding)",
        fuente.count("acciones_ok: list[str] = []") == 1,
        f"{fuente.count('acciones_ok: list[str] = []')} declaraciones")
    chk("el onboarding alimenta acciones_ok",
        "acciones_ok.append(f\"datos del atleta guardados en su ficha" in fuente)

    print("\n[2] CASO 16:26 — la marca del onboarding ya guardada")
    marca = "Pres de banca, 145 kilos a 3 repeticiones."
    chk("antes de guardarla NO consta (el guardia debe poder saltar)",
        L._accion_ya_constaba(NUM, marca) == "")
    json.dump([{"fecha": HOY + " 16:26", "ejercicio": "press banco plano",
                "rm": 157.1, "peso_origen": 145.0}],
              open(L._ruta_historial_rm(NUM), "w", encoding="utf-8"))
    L._invalidar_json(L._ruta_historial_rm(NUM))
    consta = L._accion_ya_constaba(NUM, marca)
    chk("después de guardarla, la afirmación es CIERTA", bool(consta), consta)
    chk("y el mensaje nombra el ejercicio y la marca",
        "press banco plano" in consta and "145" in consta)

    print("\n[3] CASO 16:55 — la serie repetida ya está en el diario")
    mp.leer_series = lambda n, e=None, dias=180: [
        {"f": HOY + " 16:51:04", "e": "press banca", "w": 45.0, "r": 10, "rpe": 10.0}]
    consta_serie = L._accion_ya_constaba(NUM, "Si, fue 45 kilos por 10 reps en press, banco, comancuarnas.")
    chk("la serie ya apuntada se reconoce", bool(consta_serie), consta_serie)
    chk("una serie NUNCA reportada no se reconoce (el guardia sí debe saltar)",
        L._accion_ya_constaba(NUM, "Hice con 90kg 11 a rpe @7.5") == "")

    print("\n[4] Lo de OTRO día no cuenta como apuntado hoy")
    mp.leer_series = lambda n, e=None, dias=180: [
        {"f": "2026-01-01 10:00:00", "e": "press banca", "w": 45.0, "r": 10, "rpe": 10.0}]
    chk("una serie idéntica de enero no vale como hoy",
        L._accion_ya_constaba(NUM, "45 kilos por 10 reps") == "")
    json.dump([{"fecha": "2026-01-01 10:00", "ejercicio": "sentadilla",
                "rm": 200.0, "peso_origen": 180.0}],
              open(L._ruta_historial_rm(NUM), "w", encoding="utf-8"))
    L._invalidar_json(L._ruta_historial_rm(NUM))
    chk("una marca vieja tampoco",
        L._accion_ya_constaba(NUM, "180 kilos por 3") == "")

    print("\n[5] Sin marca en el mensaje no se toca nada")
    mp.leer_series = sin_series
    for m in ("hola bro", "muéstrame el plan", "te odio con toda mi alma",
              "", "me duele el hombro"):
        chk(f"{m[:28]!r} no dispara nada", L._accion_ya_constaba(NUM, m) == "")

    print("\n[6] Un fallo de lectura no puede tumbar el turno")
    def revienta(*a, **k): raise RuntimeError("base caída")   # noqa: E306,E704
    mp.leer_series = revienta
    chk("con el diario caído devuelve '' en vez de propagar",
        L._accion_ya_constaba(NUM, "45 kilos por 10 reps") == "")
    mp.leer_series = sin_series

    print(f"\n{'='*74}\n  {ok} ok, {fallos} fallos\n{'='*74}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
