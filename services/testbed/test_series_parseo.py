# -*- coding: utf-8 -*-
"""
test_series_parseo.py — cómo entra al diario lo que el atleta escribe.

Dos fallos reales del 2026-07-30, los dos silenciosos:

1. NOTACIÓN DE PRESCRIPCIÓN vs DE MARCA. "hice 4x8 con 60 kg" son 4 series de 8
   repeticiones a 60 kg. El parser lo leía como kilos x reps y, al ordenar los
   números por tamaño, lo guardaba como 8 kg x 4 reps. No es un dato incompleto:
   es un dato FALSO que además contamina el 1RM estimado y todas las cargas que
   se predicen a partir de él.

2. RECTIFICACIONES IMPOSIBLES. "perdón, eran 90 no 100" y "olvida esa serie, no
   la hice" no tenían ninguna vía: el coach contestaba "lo corrijo en tu
   expediente" / "la ignoro por completo" y no ocurría nada.

    python -m services.testbed.test_series_parseo
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from services import db_service as D                                  # noqa: E402
from services import llm_service as L                                 # noqa: E402

NUM = "5215500000901"          # atleta de laboratorio, se limpia al terminar

OK = FALLOS = 0


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global OK, FALLOS
    if condicion:
        OK += 1
        print(f"  ✓ {nombre}")
    else:
        FALLOS += 1
        print(f"  ✗ {nombre}" + (f"  → {detalle}" if detalle else ""))


def _sembrar(peso=100.0, reps=5, rpe=8.0, ejercicio="press banco plano"):
    while D.borrar_ultima_serie(NUM):
        pass
    D.guardar_serie_sql(NUM, {"ejercicio": ejercicio, "peso_kg": peso,
                              "repeticiones": reps, "rpe_reportado": rpe})


def bloque_notacion():
    print("\n[1] 'N series de R con P kg' y 'NxR con P kg' → N series idénticas")
    casos = [
        ("hice 3 series de 12 con 40 kilos en remo, rpe 8", 3, 40.0, 12),
        ("hice 4x8 con 60 kg en remo rpe 8",                4, 60.0, 8),
        ("5 series de 5 a 100 kg en banca, me quedaron 2",  5, 100.0, 5),
    ]
    for texto, n, peso, reps in casos:
        s = L.detectar_series_multiples(texto)
        chk(f"{texto[:42]!r} → {n} series de {peso:g} kg x {reps}",
            len(s) == n and s and s[0]["peso"] == peso and s[0]["reps"] == reps,
            f"{s}")

    print("\n[2] La notación de MARCA sigue intacta (no se rompió lo que funcionaba)")
    for texto, peso, reps in [("hice 100x5 en banca rpe 8", 100.0, 5),
                              ("sentadilla 140 por 3", 140.0, 3),
                              ("peso muerto 180x2", 180.0, 2),
                              ("hice 12 reps con 40 kg", 40.0, 12)]:
        s = L.detectar_serie_en_mensaje(texto)
        chk(f"{texto!r} → {peso:g} kg x {reps}",
            bool(s) and s["peso"] == peso and s["reps"] == reps, f"{s}")

    s = L.detectar_series_multiples("100x5 rpe8, 100x4 rpe9 y 95x5 rpe9")
    chk("volcado de 3 marcas distintas sigue dando 3 series", len(s) == 3, f"{s}")
    chk("un peso corporal no es una serie",
        L.detectar_serie_en_mensaje("pesé 84 kilos hoy") is None)


def bloque_correccion():
    print("\n[3] Corrección de la última serie: se escribe en el diario de verdad")
    _sembrar(peso=100.0, reps=5)
    r = L._rectificar_ultima_serie(NUM, "perdón, eran 90 no 100")
    u = D.obtener_ultima_serie(NUM) or {}
    chk("'eran 90 no 100' corrige el PESO", u.get("peso_kg") == 90.0, f"{u}")
    chk("y no crea una serie nueva", bool(r) and r["etapa"] == "SERIE-CORRECCION")
    chk("las reps no se tocan", u.get("repeticiones") == 5, f"{u}")

    _sembrar(peso=100.0, reps=5)
    L._rectificar_ultima_serie(NUM, "perdón, eran 6 no 5 reps")
    u = D.obtener_ultima_serie(NUM) or {}
    chk("el campo se deduce del valor erróneo, no del texto (reps)",
        u.get("repeticiones") == 6 and u.get("peso_kg") == 100.0, f"{u}")

    _sembrar(peso=100.0, reps=5)
    L._rectificar_ultima_serie(NUM, "no eran 100 sino 95 kilos")
    u = D.obtener_ultima_serie(NUM) or {}
    chk("'no eran X sino Y' toma el SEGUNDO valor", u.get("peso_kg") == 95.0, f"{u}")

    print("\n[4] Lo que NO es una corrección de serie se deja en paz")
    _sembrar(peso=100.0, reps=5)
    for texto in ("qué me toca hoy?", "ahora peso 84 kilos", "mi PR de banca es 110 por 3"):
        antes = D.obtener_ultima_serie(NUM)
        L._rectificar_ultima_serie(NUM, texto)
        chk(f"{texto!r} no toca el diario", D.obtener_ultima_serie(NUM) == antes)


def bloque_retiro():
    print("\n[5] Retirar una serie que no se hizo")
    _sembrar(peso=100.0, reps=5)
    r = L._rectificar_ultima_serie(NUM, "mejor olvida esa serie, no la hice")
    chk("la borra del diario", D.obtener_ultima_serie(NUM) is None)
    chk("y avisa de que el 1RM ya calculado no se revierte",
        bool(r) and "1RM" in r["bloque"], f"{r}")

    r = L._rectificar_ultima_serie(NUM, "borra esa serie")
    chk("sin series, dice que no hay nada que retirar y NO finge",
        bool(r) and "NO HAY SERIE" in r["bloque"], f"{r}")


def bloque_calendario():
    print("\n[6] El microciclo dura 7 días: los saltos entre sesiones suman una semana")
    import services.llm_service as LL
    original = LL.cargar_perfil
    try:
        for dias in range(1, 8):
            LL.cargar_perfil = lambda u, _d=dias: {"dias_semana": _d}
            saltos = [LL._salto_hasta_siguiente_sesion("nadie", {"semana": 99999, "dia": i + 1})
                      for i in range(dias)]
            chk(f"{dias} día(s)/semana → {saltos} = 7", sum(saltos) == 7, f"suma {sum(saltos)}")
            chk(f"  y ningún salto de 0 días con {dias}", all(x >= 1 for x in saltos), f"{saltos}")
    finally:
        LL.cargar_perfil = original


def bloque_peso_corporal():
    print("\n[7] Peso corporal: se reconoce como habla la gente, sin tragarse las cargas")
    for texto, esperado in [("ahora peso 84 kilos", 84.0), ("bajé a 82 kg", 82.0),
                            ("hoy me pesé: 83.5 kg", 83.5), ("subí a 85 kilos", 85.0),
                            ("mi peso es 84", 84.0), ("amanecí en 83,2 kg", 83.2),
                            ("peso corporal 84", 84.0), ("estoy en 84 kg", 84.0)]:
        chk(f"{texto!r} → {esperado} kg", L.detectar_peso_corporal(texto) == esperado,
            f"{L.detectar_peso_corporal(texto)}")
    for texto in ("peso muerto 180x2", "subí a 85 kilos en sentadilla", "hice 100x5 en banca",
                  "la barra pesa 20 kg", "cuánto peso debo usar?"):
        chk(f"NO es peso corporal: {texto!r}", L.detectar_peso_corporal(texto) is None,
            f"{L.detectar_peso_corporal(texto)}")


def bloque_sustitucion():
    print("\n[8] Pedir un cambio de ejercicio, dicho de las cinco formas normales")
    casos = [("cámbiame el press militar por press con mancuernas", "press militar", "press con mancuernas"),
             ("sustituye las dominadas por jalón al pecho", "dominadas", "jalón al pecho"),
             ("reemplaza el peso muerto por rumano", "peso muerto", "rumano"),
             ("me cambias la banca por mancuernas?", "banca", "mancuernas"),
             ("cámbiame las sentadillas por prensa porque me duele la rodilla", "sentadillas", "prensa")]
    for texto, orig, nuevo in casos:
        m = L.SUSTITUIR_RE.search(texto)
        chk(f"{texto[:38]!r} → {orig} ▸ {nuevo}",
            bool(m) and m.group(1).strip() == orig and m.group(2).strip() == nuevo,
            f"{(m.group(1), m.group(2)) if m else None}")


def bloque_claims():
    print("\n[9] El guardia de claims cubre el presente ('lo corrijo', 'la ignoro')")
    for texto in ("Lo corrijo en tu expediente.", "Entendido, la ignoro por completo.",
                  "Listo, la borro del diario."):
        chk(f"detecta claim: {texto!r}", bool(L._CLAIM_ACCION_RE.search(texto)))
    for texto in ("Dime el peso y las reps y lo guardo.",
                  "¿Quieres que la quite del diario?"):
        chk(f"NO marca una oferta: {texto!r}", not L._CLAIM_ACCION_RE.search(texto))


def main() -> int:
    print("\n" + "=" * 74)
    print("  PARSEO Y RECTIFICACIÓN DE SERIES")
    print("=" * 74)
    try:
        bloque_notacion()
        bloque_correccion()
        bloque_retiro()
        bloque_calendario()
        bloque_peso_corporal()
        bloque_sustitucion()
        bloque_claims()
    finally:
        while D.borrar_ultima_serie(NUM):
            pass
    print("-" * 74)
    print(f"  {OK}/{OK + FALLOS} comprobaciones correctas")
    print("=" * 74 + "\n")
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
