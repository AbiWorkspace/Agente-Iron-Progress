#calculadora_rm.py
from enum import Enum
from typing import Union, Dict


class TipoEjercicio(Enum):
    PESO_LIBRE = "peso_libre"
    MAQUINA = "maquina"
    LASTRADO = "lastrado"
    CALISTENIA_PURA = "calistenia_pura"


# =========================
# FORMULAS BASE
# =========================

def brzycki(peso: float, reps: int) -> float:
    # El denominador se anula justo en reps=37 (ZeroDivisionError) y se vuelve
    # negativo por encima. Se acota para que la fórmula nunca reviente: en el rango
    # donde SÍ se usa (1-15 reps, ver estimacion_hibrida) el resultado no cambia,
    # porque ahí 37-reps ya vale 22 o más.
    return peso * (36 / max(1.0, 37 - reps))


def epley(peso: float, reps: int) -> float:
    return peso * (1 + reps / 30)


def lombardi(peso: float, reps: int) -> float:
    return peso * (reps ** 0.10)


def mayhew(peso: float, reps: int) -> float:
    return (100 * peso) / (52.2 + 41.9 * (2.71828 ** (-0.055 * reps)))


def oconner(peso: float, reps: int) -> float:
    return peso * (1 + reps / 40)


def wathan(peso: float, reps: int) -> float:
    return (100 * peso) / (48.8 + 53.8 * (2.71828 ** (-0.075 * reps)))


# =========================
# MOTOR HIBRIDO AVANZADO
# =========================

def estimacion_hibrida(peso: float, reps: int) -> float:
    """Ensamble de las seis fórmulas, garantizado MONÓTONO en repeticiones.

    Los tramos de ponderación cambian de fórmulas en sus fronteras (en 11 entra
    Lombardi y sale Mayhew; en 16 desaparece Wathan), y esos saltos hacían que
    con el MISMO peso 11 reps estimaran menos 1RM que 10, y 16 reps hasta 2,8 kg
    menos que 15. Para el atleta eso es mejorar y que el coach le diga que ha
    retrocedido, así que el resultado se acota por el del tramo anterior: un
    estimador de 1RM puede ser impreciso, pero no puede contradecirse a sí mismo.
    """
    reps = int(reps)
    if reps <= 1:
        return peso
    mejor = _hibrida_cruda(peso, 1)
    for r in range(2, reps + 1):
        mejor = max(mejor, _hibrida_cruda(peso, r))
    return mejor


def _hibrida_cruda(peso: float, reps: int) -> float:

    if reps == 1:
        return peso

    # FORMULAS
    brz = brzycki(peso, reps)
    epl = epley(peso, reps)
    lom = lombardi(peso, reps)
    may = mayhew(peso, reps)
    oco = oconner(peso, reps)
    wat = wathan(peso, reps)

    # =========================
    # PONDERACIONES DINAMICAS
    # =========================

    # 2-3 reps -> Brzycki domina
    if reps <= 3:
        return (
            brz * 0.45 +
            wat * 0.25 +
            may * 0.15 +
            epl * 0.10 +
            oco * 0.05
        )

    # 4-6 reps -> mezcla fuerza/hipertrofia
    elif reps <= 6:
        return (
            brz * 0.30 +
            epl * 0.25 +
            wat * 0.20 +
            may * 0.15 +
            lom * 0.10
        )

    # 7-10 reps -> Epley y Wathan
    elif reps <= 10:
        return (
            epl * 0.35 +
            wat * 0.30 +
            may * 0.15 +
            lom * 0.10 +
            oco * 0.10
        )

    # 11-15 reps -> Lombardi mejora bastante
    elif reps <= 15:
        return (
            lom * 0.35 +
            epl * 0.25 +
            wat * 0.20 +
            oco * 0.20
        )

    # 15+ reps -> precisión baja inevitable
    else:
        return (
            lom * 0.45 +
            oco * 0.35 +
            epl * 0.20
        )


# =========================
# AJUSTE POR TIPO DE EJERCICIO
# =========================

def factor_tipo_ejercicio(tipo: TipoEjercicio) -> float:

    factores = {
        TipoEjercicio.PESO_LIBRE: 1.00,
        TipoEjercicio.MAQUINA: 0.97,
        TipoEjercicio.LASTRADO: 0.99,
        TipoEjercicio.CALISTENIA_PURA: 0.95,
    }

    return factores[tipo]


def _aplicar_factor(estimacion: float, peso_movido: float, factor: float) -> float:
    """Corrige la EXTRAPOLACIÓN, nunca el dato duro.

    El factor multiplicaba el 1RM entero, y eso empujaba el resultado por DEBAJO
    del peso que el atleta acababa de levantar: una dominada con 20 kg de lastre
    devolvía un 1RM de lastre de 18,95 kg, y una sola dominada a peso corporal
    (85 kg) devolvía 80,75 kg de fuerza total. Un 1RM menor que una repetición ya
    ejecutada es físicamente imposible, y de ahí salen todos los porcentajes del
    plan.

    Lo que el factor corrige es la parte ESTIMADA (cuánto más de lo levantado se
    le supone al atleta), no lo que ya demostró. Con peso libre (factor 1.00) el
    resultado es idéntico al de antes.
    """
    peso_movido = max(0.0, float(peso_movido))
    ganancia = max(0.0, float(estimacion) - peso_movido)
    return peso_movido + ganancia * float(factor)


# =========================
# API PRINCIPAL
# =========================

def calcular_1rm(
        tipo_ejercicio: TipoEjercicio,
        repeticiones: int,
        peso_levantado: float = 0.0,
        peso_corporal: float = 85.0
) -> Union[float, Dict[str, float]]:

    if repeticiones < 1:
        raise ValueError("Las repeticiones deben ser al menos 1.")

    # =========================================
    # PESO LIBRE / MAQUINA
    # =========================================

    if tipo_ejercicio in (
        TipoEjercicio.PESO_LIBRE,
        TipoEjercicio.MAQUINA
    ):

        rm = estimacion_hibrida(
            peso_levantado,
            repeticiones
        )

        rm = _aplicar_factor(rm, peso_levantado,
                             factor_tipo_ejercicio(tipo_ejercicio))

        return round(rm, 2)

    # =========================================
    # LASTRADO
    # =========================================

    elif tipo_ejercicio == TipoEjercicio.LASTRADO:

        peso_total = peso_corporal + peso_levantado

        rm_total = estimacion_hibrida(
            peso_total,
            repeticiones
        )

        rm_total = _aplicar_factor(rm_total, peso_total,
                                   factor_tipo_ejercicio(tipo_ejercicio))

        return {
            "1rm_absoluto_kg": round(rm_total, 2),
            "1rm_solo_lastre_kg": round(
                rm_total - peso_corporal,
                2
            )
        }

    # =========================================
    # CALISTENIA PURA
    # =========================================

    elif tipo_ejercicio == TipoEjercicio.CALISTENIA_PURA:

        rm_total = estimacion_hibrida(
            peso_corporal,
            repeticiones
        )

        rm_total = _aplicar_factor(rm_total, peso_corporal,
                                   factor_tipo_ejercicio(tipo_ejercicio))

        lastre = max(0.0, rm_total - peso_corporal)

        return {
            "1rm_fuerza_total_kg": round(rm_total, 2),
            "lastre_sugerido_para_1rm_kg": round(lastre, 2)
        }

    else:
        raise ValueError("Tipo de ejercicio no soportado.")