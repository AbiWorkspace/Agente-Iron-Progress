"""
coherencia_entrenamiento.py — Motor de coherencia entre variables de entrenamiento.

Problema que resuelve (caso real): el generador prescribió 12 reps @ 72.5% 1RM
con RPE 7.5. A 72.5% el máximo absoluto son ~11 reps AL FALLO; pedir 12 con 2-3
en reserva es físicamente imposible. Las variables se asignaban por separado sin
la cadena que las une:

    %1RM  ->  reps máximas posibles (Epley inverso)
          ->  RIR = reps_max - reps prescritas
          ->  RPE = 10 - RIR
          ->  reps efectivas = RPE - 5   (las últimas 5 antes del fallo)
          ->  series/semana = objetivo de efectivas semanales / efectivas por serie
          ->  series/sesión = series semanales repartidas en la frecuencia

Uso mínimo dentro de Entrenamiento.py (clase distribucion_torso_pierna), justo
antes de escribir cada ejercicio al JSON:

    from coherencia_entrenamiento import corregir_ejercicio, series_recomendadas

    pct, reps, rpe, nota = corregir_ejercicio(pct, reps, rpe, tipo_dia="hipertrofia")
    series_sesion, series_semana = series_recomendadas(meta, rpe, frecuencia_patron_semana)

O para post-procesar sesiones completas ya armadas (tu esquema JSON):

    from coherencia_entrenamiento import auditar_y_corregir_sesiones
    sesiones, reporte = auditar_y_corregir_sesiones(sesiones)
"""

import math
import re

# ==============================================================================
# PARÁMETRO FISIOLÓGICO
# ==============================================================================
# Coeficiente de Epley. 30 es el estándar poblacional. "Depende de cada persona":
# atletas muy eficientes a reps altas (fibra lenta) toleran 33-35; muy explosivos
# (fibra rápida) rinden como 25-28. Ajustable por atleta cuando tengas datos.
COEF_EPLEY = 30.0

# Perfiles DUP: rangos válidos de %1RM y RPE por tipo de día.
# Potencia según tu especificación: RPE 5-6, 77.5%-85% conservando el RIR.
PERFIL_DIA = {
    "potencia":    {"pct": (77.5, 85.0), "rpe": (5.0, 6.0)},
    "fuerza":      {"pct": (80.0, 90.0), "rpe": (7.0, 8.5)},
    "hipertrofia": {"pct": (62.5, 77.5), "rpe": (7.0, 8.5)},
}

# Objetivo de REPS EFECTIVAS por semana y por patrón/grupo muscular según meta.
# (hipertrofia se construye con volumen efectivo; powerlifting con calidad neural,
# por eso su objetivo de efectivas es menor: su estímulo principal es la carga.)
EFECTIVAS_SEMANA_META = {
    "hipertrofia":   25,
    "culturismo":    28,
    "powerbuilding": 20,
    "powerlifting":  12,
}

TOPE_SERIES_SESION = 6      # techo de series duras por patrón en una sesión
MINIMO_SERIES_SESION = 2


# ==============================================================================
# CADENA DE COHERENCIA (todas las funciones derivan una variable de las otras)
# ==============================================================================
def redondear_pct(pct: float) -> float:
    """Los %1RM se trabajan en pasos de 2.5."""
    return round(float(pct) / 2.5) * 2.5


def reps_maximas(pct: float, coef: float = COEF_EPLEY) -> float:
    """Reps máximas posibles AL FALLO a un %1RM dado (Epley inverso).
    72.5% -> ~11.4 reps. 85% -> ~5.3. 100% -> 0... clamp a 1."""
    pct = max(30.0, min(100.0, float(pct)))
    return max(1.0, coef * (100.0 / pct - 1.0))


def rir_de(pct: float, reps: int, coef: float = COEF_EPLEY) -> float:
    """Reps en reserva que quedan al hacer 'reps' con ese %1RM.
    Negativo = prescripción imposible (más reps de las que existen)."""
    return reps_maximas(pct, coef) - float(reps)


def rpe_de(pct: float, reps: int, coef: float = COEF_EPLEY) -> float:
    """RPE real que produce esa combinación %1RM + reps."""
    return max(1.0, min(10.0, 10.0 - rir_de(pct, reps, coef)))


def reps_para(pct: float, rpe_objetivo: float, coef: float = COEF_EPLEY) -> int:
    """Reps que debes prescribir a un %1RM para aterrizar en el RPE objetivo.
    Es LA función que faltaba: reps = reps_max - RIR, con RIR = 10 - RPE."""
    rir_objetivo = 10.0 - float(rpe_objetivo)
    reps = reps_maximas(pct, coef) - rir_objetivo
    return max(1, int(round(reps)))


def pct_para(reps: int, rpe_objetivo: float, coef: float = COEF_EPLEY) -> float:
    """%1RM necesario para que 'reps' salgan al RPE objetivo (inversa de Epley
    con reserva): pct = 100 / (1 + (reps + RIR)/coef), redondeado a 2.5."""
    rir_objetivo = 10.0 - float(rpe_objetivo)
    reps_al_fallo = float(reps) + rir_objetivo
    pct = 100.0 / (1.0 + reps_al_fallo / coef)
    return redondear_pct(pct)


def reps_efectivas_por_serie(rpe: float) -> float:
    """Tu modelo de reps efectivas: solo cuentan las últimas 5 antes del fallo.
    RPE 6 -> 1 | RPE 7 -> 2 | RPE 8 -> 3 | RPE 9 -> 4 | RPE 10 -> 5.
    (Equivale a: efectivas = RPE - 5, acotado a [0, 5]; admite medios puntos.)"""
    return max(0.0, min(5.0, float(rpe) - 5.0))


# ==============================================================================
# VALIDACIÓN Y CORRECCIÓN DE PRESCRIPCIONES
# ==============================================================================
def validar_ejercicio(pct: float, reps: int, rpe: float,
                      tolerancia_rpe: float = 0.75) -> tuple[bool, str]:
    """¿La tripleta (%1RM, reps, RPE) es físicamente coherente?"""
    rmax = reps_maximas(pct)
    if reps > rmax:
        return False, (f"IMPOSIBLE: {reps} reps @ {pct:g}% excede el máximo al fallo "
                       f"(~{rmax:.0f} reps). RIR negativo.")
    rpe_real = rpe_de(pct, reps)
    if abs(rpe_real - rpe) > tolerancia_rpe:
        return False, (f"INCOHERENTE: {reps} reps @ {pct:g}% produce RPE ~{rpe_real:.1f}, "
                       f"no el RPE {rpe:g} prescrito (desvío > {tolerancia_rpe}).")
    return True, "OK"


def corregir_ejercicio(pct: float, reps: int, rpe: float,
                       tipo_dia: str = "hipertrofia") -> tuple[float, int, float, str]:
    """
    Devuelve (pct, reps, rpe, nota) coherentes, preservando el CARÁCTER del día:
    1) El %1RM y el RPE se acotan al rango del tipo de día DUP (el %1RM manda
       sobre el carácter del estímulo; el RPE define la reserva).
    2) Las reps se DERIVAN de esos dos — nunca se fijan por separado.
    Ejemplo del bug real: (72.5%, 12 reps, RPE 7.5) en día hipertrofia
    -> reps_max(72.5%)=11.4, RIR objetivo 2.5 -> 9 reps. Sale (72.5, 9, 7.5).
    """
    perfil = PERFIL_DIA.get(str(tipo_dia).lower().strip(), PERFIL_DIA["hipertrofia"])
    notas = []

    pct_orig, rpe_orig, reps_orig = float(pct), float(rpe), int(reps)

    pct = redondear_pct(min(max(float(pct), perfil["pct"][0]), perfil["pct"][1]))
    if pct != redondear_pct(pct_orig):
        notas.append(f"%1RM {pct_orig:g}->{pct:g} (rango {tipo_dia})")

    rpe = min(max(float(rpe), perfil["rpe"][0]), perfil["rpe"][1])
    if rpe != rpe_orig:
        notas.append(f"RPE {rpe_orig:g}->{rpe:g} (rango {tipo_dia})")

    reps_coherentes = reps_para(pct, rpe)
    if reps_coherentes != reps_orig:
        notas.append(f"reps {reps_orig}->{reps_coherentes} "
                     f"(a {pct:g}% el fallo llega a ~{reps_maximas(pct):.0f}; "
                     f"RIR {10 - rpe:g} exige {reps_coherentes})")
    reps = reps_coherentes

    # Guardas de sensatez por tipo de día (si las reps derivadas quedan fuera
    # del rango razonable, se ajusta el %1RM, no las reps a la fuerza):
    rangos_reps = {"potencia": (2, 5), "fuerza": (3, 6), "hipertrofia": (6, 12)}
    lo, hi = rangos_reps.get(str(tipo_dia).lower().strip(), (3, 12))
    if reps < lo or reps > hi:
        reps = max(lo, min(hi, reps))
        pct_ajustado = pct_para(reps, rpe)
        pct_ajustado = min(max(pct_ajustado, perfil["pct"][0]), perfil["pct"][1])
        if pct_ajustado != pct:
            notas.append(f"reps fuera de rango del día -> reps={reps}, %1RM reajustado a {pct_ajustado:g}")
            pct = pct_ajustado

    return pct, int(reps), round(rpe * 2) / 2, ("; ".join(notas) if notas else "sin cambios")


# ==============================================================================
# SERIES POR SEMANA Y POR SESIÓN (derivadas de las reps efectivas)
# ==============================================================================
def series_recomendadas(meta: str, rpe_dia: float,
                        frecuencia_patron_semana: int) -> tuple[int, int]:
    """
    (series_por_sesion, series_por_semana) para un patrón/grupo muscular.

    Lógica: el objetivo semanal de la meta se paga en REPS EFECTIVAS.
    Cada serie al RPE del día aporta 'reps_efectivas_por_serie(rpe)'.
        series_semana = ceil(objetivo_efectivas / efectivas_por_serie)
        series_sesion = series_semana / frecuencia, acotado [2, 6].

    Consecuencia natural (y correcta): los días de POTENCIA (RPE 5-6, 0.5-1
    efectiva/serie) NO se compensan inflando series hasta 20 — su estímulo es
    neural, no de volumen; por eso el tope por sesión los mantiene en 4-6 series
    de calidad y el volumen efectivo se cobra en los días de hipertrofia.
    """
    objetivo = EFECTIVAS_SEMANA_META.get(str(meta).lower().strip(), 20)
    efectivas = max(0.5, reps_efectivas_por_serie(rpe_dia))
    frecuencia = max(1, int(frecuencia_patron_semana))

    series_semana = math.ceil(objetivo / efectivas)
    series_sesion = math.ceil(series_semana / frecuencia)
    series_sesion = max(MINIMO_SERIES_SESION, min(TOPE_SERIES_SESION, series_sesion))
    series_semana = series_sesion * frecuencia
    return series_sesion, series_semana


# ==============================================================================
# AUDITORÍA DE SESIONES COMPLETAS (post-proceso drop-in para tu esquema JSON)
# ==============================================================================
_PCT_RE = re.compile(r"([\d.]+)\s*%")

def _leer_reps(valor) -> int | None:
    nums = [int(x) for x in re.findall(r"\d+", str(valor))]
    if not nums:
        return None
    return round(sum(nums) / len(nums))   # "8-10" -> 9

def _tipo_dia_de(sesion: dict) -> str:
    texto = (str(sesion.get("Enfoque", "")) + " " + str(sesion.get("Fase", ""))).lower()
    if "potencia" in texto:
        return "potencia"
    if "fuerza" in texto:
        return "fuerza"
    return "hipertrofia"

def auditar_y_corregir_sesiones(sesiones: list, meta: str = "",
                                frecuencia_patron: int = 2) -> tuple[list, list]:
    """
    Recorre sesiones con tu esquema ({Enfoque, Fase, Ejercicios:[{Ejercicio,
    Series, Reps, 1RM:'72.5%', RPE, tipo}]}), corrige cada tripleta incoherente
    y (si se pasa 'meta') recalcula las Series desde las reps efectivas.
    Devuelve (sesiones_corregidas, reporte). Los accesorios sin %1RM se ignoran.
    """
    reporte = []
    for sesion in sesiones:
        tipo_dia = _tipo_dia_de(sesion)
        for ej in sesion.get("Ejercicios", []):
            m = _PCT_RE.search(str(ej.get("1RM", "")))
            reps = _leer_reps(ej.get("Reps"))
            if not m or reps is None:
                continue   # peso por calibrar / accesorio: fuera del contrato %1RM
            pct = float(m.group(1))
            try:
                rpe = float(ej.get("RPE"))
            except (TypeError, ValueError):
                rpe = sum(PERFIL_DIA[tipo_dia]["rpe"]) / 2

            ok, diag = validar_ejercicio(pct, reps, rpe)
            if not ok:
                npct, nreps, nrpe, nota = corregir_ejercicio(pct, reps, rpe, tipo_dia)
                ej["1RM"] = f"{npct:g}%"
                ej["Reps"] = nreps
                ej["RPE"] = nrpe
                reporte.append(f"[{sesion.get('Enfoque','?')}] {ej.get('Ejercicio','?')}: "
                               f"{diag} -> corregido: {nota}")
                pct, reps, rpe = npct, nreps, nrpe

            if meta:
                s_sesion, s_semana = series_recomendadas(meta, rpe, frecuencia_patron)
                if ej.get("Series") != s_sesion:
                    reporte.append(f"[{sesion.get('Enfoque','?')}] {ej.get('Ejercicio','?')}: "
                                   f"Series {ej.get('Series')}->{s_sesion} "
                                   f"({reps_efectivas_por_serie(rpe):g} efectivas/serie, "
                                   f"objetivo {EFECTIVAS_SEMANA_META.get(meta.lower(), 20)}/sem, "
                                   f"{s_semana} series/sem en {frecuencia_patron} sesiones)")
                    ej["Series"] = s_sesion
    return sesiones, reporte