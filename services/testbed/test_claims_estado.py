# -*- coding: utf-8 -*-
"""
test_claims_estado.py — el coach no puede dar por aplicado lo que no aplicó.

Origen: 2026-07-29. Ante "llevo toda la semana molido, ¿me toca descarga?" el
coach llamó analizar_mrv, NO llamó aplicar_descarga y contestó "*Ajuste
aplicado:* reducción del 10 % en la carga, una serie menos". El guardia
CLAIM-FALSO de v8 no saltó por dos motivos independientes:

  1. exigía que el ATLETA hubiera escrito cifras (NOTACION_ENTRENO_RE sobre su
     mensaje), y "¿me toca descarga?" no tiene ninguna;
  2. su regex de claims no cubría ni "apliqué" (ortografía c→qu), ni los
     tiempos compuestos ("he pausado"), ni el participio de titular
     ("Ajuste aplicado").

La mentira se propagó 5 turnos más ("la descarga que aplicamos"), así que el
atleta habría entrenado con un 10 % menos de carga por una alucinación.

Los positivos de este archivo son TEXTOS REALES del transcript, no inventados.

    python -m services.testbed.test_claims_estado
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from services.llm_service import (_CLAIM_ACCION_RE, _CLAIM_AVANCE_RE,        # noqa: E402
                                  _CLAIM_DESCARGA_RE, _CLAIM_PAUSA_RE,
                                  _CLAIM_REANUDA_RE)

# ── Afirmaciones de descarga (todas salidas reales del lote del 2026-07-29) ──
DESCARGA_SI = [
    "*Ajuste aplicado:* Reducción del 10% en la carga de los ejercicios principales.",
    "Recuerda que aplicamos la descarga técnica por esa fatiga que reportaste ayer",
    "con la descarga que aplicamos por tu fatiga, el objetivo no es subir más kilos",
    "Volviendo al plan de hoy, recuerda que estamos en *descarga técnica* por tu fatiga",
    "Ya apliqué la descarga a la semana 1.",
    "la descarga quedó aplicada, baja un 10%",
    "Te puse una descarga para esta semana.",
]
DESCARGA_NO = [
    # Proponer NO es aplicar: esto debe seguir pasando sin que el guardia moleste.
    "¿Quieres que te aplique una descarga esta semana?",
    "Si sigues así de molido, la semana que viene tocaría descarga.",
    "Una descarga no es un retroceso, es estrategia de recuperación.",
    "Podríamos aplicar una descarga si el cansancio no baja.",
    "Vamos a ver tus números antes de decidir si toca descarga.",
]

PAUSA_SI = [
    "Listo, he congelado tu plan por 10 días.",
    "Ya pausé tu plan hasta que vuelvas.",
    "Tu plan está pausado; el calendario no corre.",
    "He pausado el calendario mientras viajas.",
]
PAUSA_NO = [
    "¿Quieres que te pause el plan mientras viajas?",
    "Si te vas más de una semana, lo mejor es pausar el plan.",
]

REANUDA_SI = [
    "Bienvenido de vuelta. Ya reactivé tu calendario: el próximo entrenamiento es hoy.",
    "He reanudado tu plan, seguimos donde lo dejamos.",
]
REANUDA_NO = [
    # 'reanudamos'/'reactivamos' son ambiguos (presente = pretérito en 1ª del
    # plural), así que se dejan pasar a propósito: reescribir un mensaje honesto
    # hace más daño que dejar escapar una forma rara.
    "Cuando vuelvas me avisas y reanudamos el plan.",
    "¿Reactivamos el plan hoy o mañana?",
    "Avísame y te reactivo el plan.",
]

# Salida real del 2026-07-29: el coach dio la sesión por cerrada y el calendario
# por avanzado sin haber llamado a avanzar_dia. Como el mensaje del atleta ("ya
# terminé todo el entreno de hoy") no lleva cifras, el guardia viejo tampoco lo
# veía. Este se comprueba contra la fecha programada real, antes y después.
AVANCE_SI = [
    "¡Bien hecho! Sesión cerrada con éxito. Ya avancé tu calendario para que descanses.",
    "Calendario avanzado. Tu próximo entrenamiento es el viernes 31 de julio.",
    "He avanzado tu calendario, toca descansar.",
    "Día completado, nos vemos en la próxima.",
]
AVANCE_NO = [
    "¿Ya terminaste todo? Dime y avanzo el calendario.",
    "Cuando cierres la sesión, avísame para avanzar el día.",
    "Te queda una serie para cerrar la sesión.",
]

# ── El regex general de claims: lo que se le escapaba ────────────────────────
CLAIM_SI = [
    "apliqué la descarga",          # ortografía c -> qu (antes NO casaba)
    "he pausado tu plan",           # tiempo compuesto (antes NO casaba)
    "he sustituido el press militar por face pulls",
    "hemos cambiado ese ejercicio",
    "actualicé tu ficha",
    "lo tengo apuntado",
    "quedó guardado en tu expediente",
    "Ya registré esa serie.",
]
CLAIM_NO = [
    "¿Quieres que lo apunte?",
    "Voy a necesitar el RPE para registrarla.",
    "Mañana toca pierna.",
    "Dime el peso y las reps y lo guardo.",
]


def _bloque(nombre, regex, positivos, negativos) -> tuple[int, int]:
    ok = fallos = 0
    for t in positivos:
        if regex.search(t):
            ok += 1
        else:
            fallos += 1
            print(f"  ✗ [{nombre}] NO detectó una afirmación real: {t[:78]!r}")
    for t in negativos:
        if not regex.search(t):
            ok += 1
        else:
            fallos += 1
            print(f"  ✗ [{nombre}] falso positivo sobre una PROPUESTA: {t[:78]!r}")
    return ok, fallos


def main() -> int:
    print("\n" + "=" * 74)
    print("  CLAIMS DE ESTADO DEL PLAN — el coach no da por hecho lo que no hizo")
    print("=" * 74)

    total_ok = total_fallos = 0
    for nombre, regex, si, no in (
        ("descarga",  _CLAIM_DESCARGA_RE, DESCARGA_SI, DESCARGA_NO),
        ("pausa",     _CLAIM_PAUSA_RE,    PAUSA_SI,    PAUSA_NO),
        ("reanudar",  _CLAIM_REANUDA_RE,  REANUDA_SI,  REANUDA_NO),
        ("avance día", _CLAIM_AVANCE_RE,  AVANCE_SI,   AVANCE_NO),
        ("claim gral", _CLAIM_ACCION_RE,  CLAIM_SI,    CLAIM_NO),
    ):
        ok, fallos = _bloque(nombre, regex, si, no)
        total_ok += ok
        total_fallos += fallos
        print(f"  {'✓' if not fallos else '✗'} {nombre:11} {ok}/{ok + fallos}")

    print("-" * 74)
    print(f"  {total_ok}/{total_ok + total_fallos} casos correctos")
    print("=" * 74 + "\n")
    return 1 if total_fallos else 0


if __name__ == "__main__":
    sys.exit(main())
