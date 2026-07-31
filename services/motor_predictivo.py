# -*- coding: utf-8 -*-
"""
motor_predictivo.py — Iron Progress v9
Núcleo matemático del coach predictivo. CERO dependencias del orquestador:
llm_service (o FastAPI) lo importa y lo expone como tools. Todo retorna dicts
JSON-serializables listos para viajar como [TOOL-RESULT] — lo que además los
mete automáticamente a la whitelist de kg del validador v8 (Mandato 1).

Ciencia detrás (referencias conceptuales en ARQUITECTURA_V9.md):
  * e1RM = Epley ajustado por RIR (RIR = 10 − RPE, escala Zourdos/Helms).
  * %prescripción = inversa de Epley para (reps objetivo + RIR objetivo).
  * Fatiga = ACWR con EWMA 7d/28d (Williams/Gabbett; heurística, no oráculo).
  * Predicción = motor determinista e1RM-EWMA como PISO + BayesianRidge como
    corrección, con GATING por incertidumbre: si σ es grande o hay pocos
    datos, manda el determinista. El modelo jamás prescribe fuera de los
    guardrails (clamp por sesión previa, techo vs e1RM, redondeo a discos).
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import numpy as np

from core.ejercicios import canonizar

# ==============================================================================
# AJUSTA AQUÍ — único punto de acople con tu esquema real (ver migracion_v9.py)
# ==============================================================================
# OJO: la telemetría vive en iron_progress_telemetria.db (tabla series_log),
# NO en iron_progress.db (esa es la de perfiles/RMs de repositorio_atleta).
# Estos valores deben coincidir con scripts/migracion_v9.py, que es quien crea
# las tablas readiness y eventos_autorregulacion.
RUTA_DB = os.environ.get(
    "IRON_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "data", "iron_progress_telemetria.db"))
TABLA_SERIES = "series_log"
C = dict(usuario="numero_usuario", ejercicio="ejercicio", peso="peso_kg",
         reps="repeticiones", rpe="rpe_reportado", dolor="dolor_articular",
         fecha="fecha_hora")

# ---- constantes fisiológicas (todas discutibles y por eso configurables) ----
K_RPE          = 0.03    # ~3 % de carga por punto de RPE (rango típico RTS 2-4 %)
HALFLIFE_E1RM  = 4       # sesiones: una marca pesa la mitad tras 4 sesiones
RPE_SET_DURO   = 6.5     # umbral de "serie efectiva" para conteo de volumen
MIN_SESIONES_ML = 8      # debajo de esto, solo motor determinista
SIGMA_MAX_REL  = 0.04    # gating: σ > 4 % del e1RM => el modelo no opina
CLAMP_BAJO, CLAMP_ALTO = 0.93, 1.05   # salto máx. vs última carga top del ejercicio


# ==============================================================================
# ACCESO A DATOS
# ==============================================================================
_esquema_verificado = False


def _asegurar_esquema() -> None:
    """Garantiza que series_log / readiness / eventos_autorregulacion existan.

    Este módulo se puede importar solo (proactividad, scripts de análisis) sin
    que el orquestador haya arrancado. Sin esto, la primera pulsación del
    readiness moría con 'no such table: readiness'. Tras la primera llamada es
    una comparación booleana: coste cero en el camino caliente.
    """
    global _esquema_verificado
    if _esquema_verificado:
        return
    try:
        from services.db_service import inicializar_db
        inicializar_db()
    except Exception as e:            # nunca tumbar la lectura por el esquema
        print(f"[MOTOR] no se pudo verificar el esquema: {e}")
    _esquema_verificado = True


@contextmanager
def _con():
    """Conexión de vida corta con cierre GARANTIZADO.

    Antes era `with sqlite3.connect(...) as con:`, que hace commit/rollback pero
    NO cierra: cada lectura de series dejaba un descriptor abierto hasta que
    pasara el recolector. En un proceso de servidor eso acaba en fuga de
    descriptores y en 'database is locked' intermitentes.
    """
    _asegurar_esquema()
    con = sqlite3.connect(RUTA_DB, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _fecha(v) -> date:
    s = str(v)[:10]
    return datetime.strptime(s, "%Y-%m-%d").date()


def _norm(txt: str) -> str:
    """Normalización de TEXTO GENÉRICO (dolor, grupo muscular, etiquetas).

    Deliberadamente NO canoniza ejercicios: para eso está `canonizar`, que se
    aplica de forma explícita en cada punto donde el texto es un ejercicio. La
    distinción importa — las reglas de ejercicio convierten "hombro" en "press
    militar", que es correcto para un ejercicio y desastroso para un reporte de
    dolor articular o para un grupo muscular.
    """
    return re.sub(r"\s+", " ", str(txt or "").strip().lower())


def leer_series(numero: str, ejercicio: str | None = None, dias: int = 180) -> list[dict]:
    """Series con tríada completa (las históricas sin reps/RPE se excluyen: son
    pre-v9 y no sirven para e1RM ni para el modelo)."""
    desde = (date.today() - timedelta(days=dias)).isoformat()
    q = (f"SELECT {C['fecha']} AS f, {C['ejercicio']} AS e, {C['peso']} AS w, "
         f"{C['reps']} AS r, {C['rpe']} AS rpe, {C['dolor']} AS dolor "
         f"FROM {TABLA_SERIES} WHERE {C['usuario']}=? AND {C['fecha']}>=? "
         f"AND {C['peso']} IS NOT NULL AND {C['reps']} IS NOT NULL "
         f"AND {C['rpe']} IS NOT NULL")
    args = [str(numero), desde]
    q += f" ORDER BY {C['fecha']} ASC"
    with _con() as con:
        filas = [dict(r) for r in con.execute(q, args)]

    # El filtro por ejercicio se hace en Python, no en SQL. Las filas ya
    # escritas conservan el nombre con el que se guardaron ('Press Banco
    # Plano'), y un `WHERE LOWER(ejercicio)=?` contra la clave canónica no las
    # encontraría: el histórico quedaría invisible y la detección de PR
    # compararía contra un conjunto vacío. Canonizando en lectura, las series
    # antiguas se agrupan con las nuevas SIN reescribir la base.
    if ejercicio:
        objetivo = canonizar(ejercicio)
        filas = [f for f in filas if canonizar(f["e"]) == objetivo]
    return filas


def guardar_serie_v9(numero: str, ejercicio: str, peso_kg: float,
                     repeticiones: int, rpe: float, dolor: str = "ninguno") -> None:
    """Inserción directa con la tríada completa. Si prefieres centralizar en tu
    db_service.guardar_serie_sql, amplíale la firma con (repeticiones, rpe) y
    redirige esta función — el resto de v9 no cambia."""
    with _con() as con:
        con.execute(
            f"INSERT INTO {TABLA_SERIES} ({C['usuario']}, {C['ejercicio']}, "
            f"{C['peso']}, repeticiones, rpe_reportado, {C['dolor']}, {C['fecha']}) "
            "VALUES (?,?,?,?,?,?,?)",
            (str(numero), canonizar(ejercicio), float(peso_kg), int(repeticiones),
             float(rpe), str(dolor or "ninguno"), datetime.now().isoformat(" ", "seconds")))
        con.commit()


def guardar_readiness(numero: str, score: float, sueno_horas=None,
                      sueno_calidad=None, estres=None, dolor=None,
                      fuente: str = "botones") -> None:
    with _con() as con:
        con.execute(
            """INSERT INTO readiness (numero_usuario, fecha, sueno_horas,
                 sueno_calidad, estres, dolor, score, fuente)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(numero_usuario, fecha) DO UPDATE SET
                 sueno_horas=excluded.sueno_horas, sueno_calidad=excluded.sueno_calidad,
                 estres=excluded.estres, dolor=excluded.dolor,
                 score=excluded.score, fuente=excluded.fuente""",
            (str(numero), date.today().isoformat(), sueno_horas, sueno_calidad,
             estres, dolor, round(float(score), 2), fuente))
        con.commit()


def leer_readiness_hoy(numero: str) -> float | None:
    with _con() as con:
        fila = con.execute(
            "SELECT score FROM readiness WHERE numero_usuario=? AND fecha=?",
            (str(numero), date.today().isoformat())).fetchone()
    return float(fila["score"]) if fila else None


def registrar_evento(numero: str, tipo: str, ejercicio: str | None, detalle: dict) -> None:
    with _con() as con:
        con.execute(
            "INSERT INTO eventos_autorregulacion (numero_usuario, fecha, tipo, "
            "ejercicio, detalle_json) VALUES (?,?,?,?,?)",
            (str(numero), datetime.now().isoformat(" ", "seconds"), tipo,
             canonizar(ejercicio) if ejercicio else None, json.dumps(detalle, ensure_ascii=False)))
        con.commit()


def _eventos_hoy(numero: str, tipo_prefijo: str, ejercicio: str | None = None) -> int:
    hoy = date.today().isoformat()
    q = ("SELECT COUNT(*) FROM eventos_autorregulacion WHERE numero_usuario=? "
         "AND fecha LIKE ? AND tipo LIKE ?")
    args = [str(numero), hoy + "%", tipo_prefijo + "%"]
    if ejercicio:
        q += " AND ejercicio=?"
        args.append(canonizar(ejercicio))
    with _con() as con:
        return int(con.execute(q, args).fetchone()[0])


# ==============================================================================
# MATEMÁTICA BASE: e1RM, prescripción, EWMA, ACWR
# ==============================================================================
def e1rm_serie(peso: float, reps: int, rpe: float) -> tuple[float, float]:
    """Epley ajustado por RIR: e1RM = peso × (1 + reps_efectivas/30), con
    reps_efectivas = reps + (10 − RPE). Devuelve (e1rm, reps_efectivas);
    con reps_efectivas > 12 la fórmula pierde precisión (nota de confianza).

    Anclaje en 1: una repetición a RPE 10 ES el 1RM, por definición. Epley sin
    anclar devolvía peso × 31/30, así que un single máximo real de 100 kg se
    guardaba como 103.3 kg de 1RM — un 3,3 % de más justo en el dato más
    importante del atleta, y de ahí a todos los porcentajes del plan.
    (calculadora_rm ya lo anclaba: `if reps == 1: return peso`.)
    """
    rir = max(0.0, 10.0 - float(rpe))
    reps_ef = float(reps) + rir
    if reps_ef <= 1.0:
        return round(float(peso), 1), reps_ef
    return round(float(peso) * (1.0 + reps_ef / 30.0), 1), reps_ef


def pct_prescripcion(reps_obj: int, rpe_obj: float) -> float:
    """Inversa de Epley: qué % del e1RM corresponde a (reps @ RPE) objetivo.
    Ej.: 5 reps @8 → 7 reps efectivas → 81.1 % (coincide con tablas RTS).

    Mismo anclaje que e1rm_serie: un single a RPE 10 es el 100 %, no el 96.8 %.
    Sin él, prescribir un intento máximo de 150 kg mandaba montar 145.2."""
    reps_ef = float(reps_obj) + max(0.0, 10.0 - float(rpe_obj))
    if reps_ef <= 1.0:
        return 1.0
    return 1.0 / (1.0 + reps_ef / 30.0)


def redondear_carga(kg: float, incremento: float = 2.5) -> float:
    """Redondea al salto que el atleta puede MONTAR. Por defecto 2.5 (discos de
    1.25 por lado); con solo discos de 2.5 el incremento real es 5."""
    if not incremento or incremento <= 0:
        incremento = 2.5
    return round(round(float(kg) / incremento) * incremento, 1)


# El incremento vive en el perfil (iron_progress.db, repositorio_atleta). Se lee
# de forma tolerante para no romper el principio de este módulo: sin el
# orquestador delante, sigue funcionando con el valor por defecto.
RUTA_DB_ATLETAS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "iron_progress.db")


def incremento_barra(numero: str) -> float:
    """Salto mínimo montable por este atleta, o 2.5 si no lo ha declarado."""
    try:
        con = sqlite3.connect(RUTA_DB_ATLETAS, timeout=5)
        try:
            fila = con.execute(
                "SELECT incremento_kg FROM atletas WHERE numero_usuario = ?",
                (str(numero),)).fetchone()
        finally:
            con.close()
        if fila and fila[0] and float(fila[0]) > 0:
            return float(fila[0])
    except Exception:
        pass          # BD ausente o sin la columna: el default es correcto
    return 2.5


def _ewma(valores: list[float], halflife: float) -> float | None:
    if not valores:
        return None
    a = 1.0 - 0.5 ** (1.0 / halflife)
    acc = valores[0]
    for v in valores[1:]:
        acc = a * v + (1 - a) * acc
    return acc


def _sesiones(series: list[dict]) -> list[dict]:
    """Agrega series → sesiones por fecha: e1RM del día (mejor serie), tonelaje,
    sets duros y RPE medio. Ordenadas ascendente."""
    por_dia: dict[date, dict] = {}
    for s in series:
        d = _fecha(s["f"])
        e1, reps_ef = e1rm_serie(s["w"], s["r"], s["rpe"])
        b = por_dia.setdefault(d, {"fecha": d, "e1rm": 0.0, "e1rm_largo": 0.0,
                                   "tonelaje": 0.0, "sets_duros": 0,
                                   "rpes": [], "cargas": []})
        if reps_ef <= 12:
            b["e1rm"] = max(b["e1rm"], e1)
        else:
            # Series largas: Epley pierde precisión, pero su estimación sigue
            # siendo MUCHO mejor que el apaño anterior (tratar la carga top como
            # si fuera un single). En un plan DUP, con días de 12 reps, aquel
            # fallback hundía el e1RM del atleta: 95 kg x12 entraban como un 1RM
            # de 95 y el EWMA arrastraba a la baja toda la predicción de cargas.
            b["e1rm_largo"] = max(b["e1rm_largo"], e1)
        b["tonelaje"] += float(s["w"]) * int(s["r"])
        b["rpes"].append(float(s["rpe"]))
        b["cargas"].append(float(s["w"]))
        if float(s["rpe"]) >= RPE_SET_DURO:
            b["sets_duros"] += 1
    out = []
    for d in sorted(por_dia):
        b = por_dia[d]
        b["rpe_medio"] = round(sum(b["rpes"]) / len(b["rpes"]), 2)
        b["carga_top"] = max(b["cargas"])
        if b["e1rm"] == 0.0:  # todas las series fueron >12 reps efectivas
            b["e1rm"] = b["e1rm_largo"] or max(b["cargas"])
        out.append(b)
    return out


def acwr_usuario(numero: str, hoy: date | None = None) -> float | None:
    """Acute:Chronic Workload Ratio sobre tonelaje diario TOTAL del atleta,
    EWMA λ=2/(N+1) con N=7 (agudo) y N=28 (crónico), incluyendo días en cero.
    Zona dulce ≈ 0.8-1.3; >1.5 = pico de carga. Heurística con crítica
    metodológica en la literatura: se usa como SEMÁFORO, nunca como veredicto."""
    hoy = hoy or date.today()
    series = leer_series(numero, None, dias=60)
    if len(series) < 6:
        return None
    carga_dia: dict[date, float] = {}
    for s in series:
        d = _fecha(s["f"])
        carga_dia[d] = carga_dia.get(d, 0.0) + float(s["w"]) * int(s["r"])
    lam_a, lam_c = 2.0 / 8.0, 2.0 / 29.0
    agudo = cronico = 0.0
    for i in range(55, -1, -1):
        d = hoy - timedelta(days=i)
        x = carga_dia.get(d, 0.0)
        agudo = lam_a * x + (1 - lam_a) * agudo
        cronico = lam_c * x + (1 - lam_c) * cronico
    if cronico < 1e-6:
        return None
    return round(agudo / cronico, 2)


# ==============================================================================
# SOLICITUD 2 — PREDICCIÓN DE CARGA ÓPTIMA
# ==============================================================================
def _features_en(sesiones: list[dict], idx: int, readiness: float, acwr: float | None) -> list[float]:
    """Vector de features usando SOLO información anterior a la sesión idx
    (en predicción, idx = len(sesiones)). Orden fijo — documentado en el doc."""
    prev = sesiones[:idx]
    e1s = [s["e1rm"] for s in prev]
    ewma = _ewma(e1s, HALFLIFE_E1RM) or 0.0
    # pendiente kg/día de los últimos 21 días (≥3 puntos; si no, 0)
    corte = prev[-1]["fecha"] - timedelta(days=21) if prev else date.today()
    pts = [(float((s["fecha"] - corte).days), s["e1rm"]) for s in prev if s["fecha"] >= corte]
    pend = float(np.polyfit(*zip(*pts), 1)[0]) if len(pts) >= 3 else 0.0
    dias_desc = float((sesiones[idx]["fecha"] - prev[-1]["fecha"]).days) if idx < len(sesiones) and prev \
        else (float((date.today() - prev[-1]["fecha"]).days) if prev else 7.0)
    rpe_ult = prev[-1]["rpe_medio"] if prev else 8.0
    return [ewma, pend, min(dias_desc, 14.0), rpe_ult,
            acwr if acwr is not None else 1.0, readiness]


def _modelo_bayesiano(sesiones: list[dict], readiness: float,
                      acwr: float | None) -> tuple[float, float] | None:
    """Entrena BayesianRidge al vuelo (dataset diminuto: milisegundos) y devuelve
    (μ, σ) del e1RM esperado HOY. None si no hay datos suficientes: el llamador
    cae al motor determinista. Sin persistencia: fit-on-request siempre fresco."""
    if len(sesiones) < MIN_SESIONES_ML:
        return None
    from sklearn.linear_model import BayesianRidge   # import perezoso
    X, y = [], []
    for i in range(3, len(sesiones)):
        X.append(_features_en(sesiones, i, readiness=0.75, acwr=acwr))
        y.append(sesiones[i]["e1rm"])
    if len(X) < 5:
        return None
    m = BayesianRidge().fit(np.array(X), np.array(y))
    mu, sd = m.predict(np.array([_features_en(sesiones, len(sesiones), readiness, acwr)]),
                       return_std=True)
    return float(mu[0]), float(sd[0])


def predecir_carga_optima(numero: str, ejercicio: str, repeticiones_objetivo: int,
                          rpe_objetivo: float = 8.0) -> dict:
    """Carga recomendada para la próxima serie top de `ejercicio`, con rango de
    incertidumbre y las señales que la explican. Diseño en dos capas:

      PISO determinista : e1RM_EWMA × %prescripción × factor_readiness
      CORRECCIÓN ML     : BayesianRidge sobre la trayectoria del e1RM, mezclada
                          50/50 SOLO si σ ≤ 4 % del e1RM (gating). Con datos
                          escasos o ruidosos, manda el piso — el modelo nunca
                          puede empeorar la recomendación más de lo que el
                          clamp permite.

    GUARDRAILS finales: salto acotado a [−7 %, +5 %] de la última carga top,
    techo en el propio e1RM, redondeo a discos de 2.5."""
    ejercicio = canonizar(ejercicio)
    reps = int(repeticiones_objetivo)
    rpe_obj = float(rpe_objetivo)
    if not (1 <= reps <= 30) or not (5.0 <= rpe_obj <= 10.0):
        return {"ok": False, "error": "objetivo fuera de rango (reps 1-30, RPE 5-10)"}

    series = leer_series(numero, ejercicio)
    ses = _sesiones(series)
    if len(ses) < 2:
        return {"ok": False, "error": "datos_insuficientes",
                "detalle": f"solo {len(ses)} sesión(es) con tríada completa de "
                           f"'{ejercicio}'; usa el %RM del plan y registra series "
                           "completas (peso+reps+RPE) para activar la predicción."}

    readiness = leer_readiness_hoy(numero)
    readiness_val = readiness if readiness is not None else 0.75
    acwr = acwr_usuario(numero)

    e1_ewma = _ewma([s["e1rm"] for s in ses], HALFLIFE_E1RM)
    fuente, sigma = "motor_e1rm", None
    e1_hat = e1_ewma
    pred = _modelo_bayesiano(ses, readiness_val, acwr)
    if pred:
        mu, sd = pred
        if sd <= SIGMA_MAX_REL * e1_ewma:
            e1_hat = 0.5 * e1_ewma + 0.5 * mu
            sigma = round(sd, 1)
            fuente = "bayes+e1rm"

    # factor readiness: 0→0.90 (molido), 0.75→0.99 (normal), 1→1.02 (al 100)
    factor = 0.90 + 0.12 * max(0.0, min(1.0, readiness_val))
    carga = e1_hat * pct_prescripcion(reps, rpe_obj) * factor

    ultima_top = ses[-1]["carga_top"]
    # El clamp existe para que la barra no dé saltos bruscos, pero comparaba
    # contra la última carga top FUERA CUAL FUERA su objetivo. En un plan DUP
    # eso mezclaba peras con manzanas: tras un día de 3x3 pesadas, la
    # recomendación para 12 reps se veía forzada al 93 % de aquella carga y, con
    # el techo del e1RM encima, la función devolvía el MISMO kilaje para 3, 8 o
    # 12 repeticiones. Se compara con las cargas usadas en un objetivo de reps
    # parecido (±3); si no hay ninguna, no se clampa y manda el e1RM.
    #
    # Y se toma la MÁS PESADA de ese último día, no la última registrada: un
    # back-off ligero al cierre de la sesión (105 kg x8 y luego 70 kg x10, que es
    # práctica estándar) se convertía en la referencia y el clamp ataba la
    # prescripción a él — 72.5 kg para un atleta que mueve 105.
    comparables = [s for s in series if abs(int(s["r"]) - reps) <= 3]
    ref_top = rpe_ref = None
    if comparables:
        dia_ref = _fecha(comparables[-1]["f"])
        del_dia = [s for s in comparables if _fecha(s["f"]) == dia_ref]
        ref_top = max(float(s["w"]) for s in del_dia)
        rpe_ref = min(float(s["rpe"]) for s in del_dia if float(s["w"]) == ref_top)
    if ref_top:
        carga = max(ref_top * CLAMP_BAJO, min(carga, ref_top * CLAMP_ALTO))
    carga = min(carga, e1_hat)                      # nadie prescribe > e1RM

    # PISO DE PROGRESIÓN. El e1RM va por EWMA de halflife 4 sesiones, o sea que
    # persigue al atleta con retraso: con una progresión lineal limpia
    # (100 → 102.5 → 105 → 107.5 kg x5 @RPE8) el EWMA se quedaba en 126 kg de
    # e1RM y la recomendación salía 100 kg — mandarle MENOS de lo que acababa de
    # levantar. Si ya completó esa carga a un RPE igual o menor que el objetivo,
    # está demostrada y repetirla es el suelo, no el techo. Se desactiva si viene
    # molido: ahí bajar la carga es la decisión correcta.
    if ref_top and rpe_ref is not None and rpe_ref <= rpe_obj and readiness_val >= 0.6:
        carga = max(carga, ref_top)

    inc = incremento_barra(numero)                  # discos reales del atleta
    # 'Barra vacía' como piso SOLO si el ejercicio se hace con barra. Aplicado a
    # todo, prescribía 20 kg de curl a quien mueve 12 (y 20 kg de lastre a quien
    # hace dominadas a peso corporal).
    tope_historico = max((float(s["w"]) for s in series), default=0.0)
    piso = 20.0 if tope_historico >= 20.0 else inc
    carga_bruta = carga
    carga = max(piso, redondear_carga(carga, inc))
    # El redondeo se aplica DESPUÉS del clamp y puede sacarlo de él: con un
    # accesorio de 12 kg, el suelo del clamp (11.16) redondeaba a 10.0, un 17 %
    # por debajo de lo que el atleta mueve. Cuando eso pasa se redondea hacia
    # arriba, que es el lado seguro (el RPE objetivo lo corrige en la serie).
    if ref_top and carga < ref_top * CLAMP_BAJO:
        carga = max(carga, redondear_carga(carga_bruta + inc / 2.0, inc))

    pend_sem = 0.0
    if len(ses) >= 3:
        corte = ses[-1]["fecha"] - timedelta(days=21)
        pts = [(float((s["fecha"] - corte).days), s["e1rm"]) for s in ses if s["fecha"] >= corte]
        if len(pts) >= 3:
            pend_sem = round(float(np.polyfit(*zip(*pts), 1)[0]) * 7, 2)

    return {
        "ok": True,
        "ejercicio": ejercicio,
        "carga_recomendada_kg": carga,
        "objetivo": f"{reps} reps @ RPE {rpe_obj:g}",
        "rango_kg": ([redondear_carga(carga - 1.96 * sigma * pct_prescripcion(reps, rpe_obj), inc),
                      redondear_carga(carga + 1.96 * sigma * pct_prescripcion(reps, rpe_obj), inc)]
                     if sigma else None),
        "fuente": fuente,
        "senales": {
            "e1rm_estimado_kg": round(e1_hat, 1),
            "tendencia_e1rm_kg_por_semana": pend_sem,
            "readiness_hoy": readiness,          # None si no contestó el cuestionario
            "acwr": acwr,
            "sesiones_usadas": len(ses),
            "ultima_carga_top_kg": ultima_top,
        },
        "nota_coach": ("Explica la recomendación SOLO con estos números; si "
                       "readiness es None, sugiere contestar el check matutino."),
    }


# ==============================================================================
# SOLICITUD 3.1 — MRV: ¿EL VOLUMEN EXCEDIÓ LA RECUPERACIÓN?
# ==============================================================================
# Nombre canónico (el que devuelve core.ejercicios.canonizar) → grupo muscular.
#
# CUIDADO con "curl": canonizar() colapsa "Curl Femoral" a "curl", igual que
# "Curl de Biceps". Con la tabla anterior, el femoral contaba como volumen de
# BRAZO y analizar_mrv(grupo_muscular='brazo') mezclaba isquios con bíceps —
# medido el 2026-07-29 contra un plan real: 7 de 18 ejercicios se quedaban sin
# grupo y el femoral caía en el grupo equivocado. Por eso el desempate por
# nombre completo va ANTES que el canónico (ver grupo_muscular_de()).
_GRUPO_DE = {
    "press banca": "pecho", "press inclinado": "pecho", "fondos": "pecho",
    "press pecho en maquina": "pecho", "aperturas": "pecho",
    "sentadilla": "pierna", "prensa": "pierna", "zancadas": "pierna",
    "extension de cuadriceps": "pierna", "sentadilla bulgara": "pierna",
    "peso muerto": "cadena posterior", "peso muerto rumano": "cadena posterior",
    "hip thrust": "cadena posterior", "curl femoral": "cadena posterior",
    "press militar": "hombro", "vuelos laterales": "hombro",
    "elevaciones laterales": "hombro", "face pull": "hombro",
    "remo": "espalda", "remo con barra": "espalda", "jalon": "espalda",
    "dominadas": "espalda", "encogimientos": "espalda",
    "curl": "brazo", "extension triceps": "brazo", "curl martillo": "brazo",
    "extension de tobillo": "gemelo", "elevacion de talones": "gemelo",
    "crunch abdominal": "core", "plancha": "core", "rueda abdominal": "core",
    "abductor de piernas": "gluteo", "aductor de piernas": "aductor",
}

# Desempates por el NOMBRE COMPLETO del ejercicio, antes de canonizar. Existen
# porque canonizar() pierde información: "Curl Femoral" y "Curl de Biceps" acaban
# los dos en "curl", y son grupos musculares opuestos.
_GRUPO_POR_NOMBRE = (
    ("femoral", "cadena posterior"),
    ("isquio", "cadena posterior"),
    ("gluteo", "gluteo"),
    ("abductor", "gluteo"),
    ("aductor", "aductor"),
    ("triceps", "brazo"),
    ("biceps", "brazo"),
    ("pantorrilla", "gemelo"),
    ("gemelo", "gemelo"),
    ("tobillo", "gemelo"),
    ("abdominal", "core"),
    ("abdomen", "core"),
    ("lateral", "hombro"),
    ("pecho", "pecho"),
)


def grupo_muscular_de(nombre_ejercicio: str) -> str:
    """Grupo muscular de un ejercicio, o '' si no se reconoce.

    Mira primero el nombre completo (donde 'femoral' desambigua un 'curl') y solo
    después la clave canónica.
    """
    crudo = _norm(nombre_ejercicio)
    for aguja, grupo in _GRUPO_POR_NOMBRE:
        if aguja in crudo:
            return grupo
    return _GRUPO_DE.get(canonizar(nombre_ejercicio), "")


def analizar_mrv(numero: str, grupo_muscular: str | None = None,
                 ventana_dias: int = 7) -> dict:
    """Compara la ventana reciente vs la anterior con las TRES señales clásicas
    de haber rebasado el Máximo Volumen Recuperable (marco MEV/MAV/MRV):
      1. Rendimiento: Δ% del e1RM por ejercicio (promedio ponderado por sets).
      2. Esfuerzo:    ΔRPE a cargas emparejadas (misma carga ±2.5 kg).
      3. Síntomas:    reportes de dolor articular.
    Tabla de decisión (explicable, sin ML): ver ARQUITECTURA_V9.md §4."""
    grupo = _norm(grupo_muscular) if grupo_muscular else None
    hoy = date.today()
    ini_rec = hoy - timedelta(days=ventana_dias)
    ini_prev = ini_rec - timedelta(days=ventana_dias)

    series = leer_series(numero, None, dias=ventana_dias * 2 + 3)
    if grupo:
        series = [s for s in series
                  if grupo_muscular_de(s["e"]) == grupo or grupo in canonizar(s["e"])]
    rec  = [s for s in series if _fecha(s["f"]) >= ini_rec]
    prev = [s for s in series if ini_prev <= _fecha(s["f"]) < ini_rec]
    if len(rec) < 4 or len(prev) < 4:
        return {"ok": False, "veredicto": "datos_insuficientes",
                "detalle": f"se necesitan ≥4 series con tríada en cada ventana "
                           f"({len(prev)} previas, {len(rec)} recientes)."}

    def _por_ej(bloque):
        out: dict[str, dict] = {}
        for s in bloque:
            e = canonizar(s["e"])
            e1, ef = e1rm_serie(s["w"], s["r"], s["rpe"])
            b = out.setdefault(e, {"e1rms": [], "sets": 0, "duros": 0, "pares": []})
            if ef <= 12:
                b["e1rms"].append(e1)
            b["sets"] += 1
            b["duros"] += 1 if float(s["rpe"]) >= RPE_SET_DURO else 0
            b["pares"].append((float(s["w"]), float(s["rpe"])))
        return out

    a, b = _por_ej(prev), _por_ej(rec)
    deltas_e1, pesos_w = [], []
    deltas_rpe = []
    for e in set(a) & set(b):
        if a[e]["e1rms"] and b[e]["e1rms"]:
            d = (max(b[e]["e1rms"]) - max(a[e]["e1rms"])) / max(a[e]["e1rms"]) * 100
            deltas_e1.append(d)
            pesos_w.append(b[e]["sets"])
        for w_rec, rpe_rec in b[e]["pares"]:          # cargas emparejadas ±2.5
            match = [rpe_a for w_a, rpe_a in a[e]["pares"] if abs(w_a - w_rec) <= 2.5]
            if match:
                deltas_rpe.append(rpe_rec - (sum(match) / len(match)))

    delta_e1 = round(float(np.average(deltas_e1, weights=pesos_w)), 2) if deltas_e1 else None
    delta_rpe = round(sum(deltas_rpe) / len(deltas_rpe), 2) if deltas_rpe else None
    dolores = [str(s["dolor"]) for s in rec
               if s["dolor"] and _norm(s["dolor"]) not in ("", "ninguno", "no", "none")]
    sets_rec = sum(x["duros"] for x in b.values())
    sets_prev = sum(x["duros"] for x in a.values())

    if delta_e1 is not None and delta_e1 <= -2.5 and ((delta_rpe or 0) >= 0.5 or dolores):
        ver, rec_txt = "mrv_excedido", (
            "Semana de descarga YA: recorta 40-50 % de las series duras, mantén "
            "cargas ≤ RPE 6 y prioriza sueño. El rendimiento cayó con el esfuerzo "
            "subiendo: recuperación en números rojos.")
    elif (delta_e1 is not None and -2.5 < delta_e1 <= 1.0) and (delta_rpe or 0) >= 0.75:
        ver, rec_txt = "en_el_limite", (
            "Estás rozando tu MRV: congela el volumen (0 series nuevas) esta "
            "semana y vigila el RPE; si vuelve a subir a cargas iguales, descarga.")
    elif delta_e1 is not None and delta_e1 >= 1.0 and (delta_rpe or 0) <= 0.25:
        ver, rec_txt = "margen_disponible", (
            "Sigues bajo tu MAV: hay margen para +1-2 series duras en el grupo "
            "si la meta lo pide. El e1RM sube sin que el esfuerzo se dispare.")
    else:
        ver, rec_txt = "estable", ("Señales mixtas o planas: mantén el volumen "
                                   "actual una semana más y reevalúa.")

    salida = {"ok": True, "veredicto": ver, "grupo": grupo or "global",
              "ventana_dias": ventana_dias,
              "metricas": {"delta_e1rm_pct": delta_e1, "delta_rpe_cargas_iguales": delta_rpe,
                           "sets_duros_recientes": sets_rec, "sets_duros_previos": sets_prev,
                           "reportes_dolor": dolores[:5]},
              "recomendacion": rec_txt}
    if ver == "mrv_excedido":
        registrar_evento(numero, "mrv_alerta", grupo, salida["metricas"])
    return salida


# ==============================================================================
# SOLICITUD 3.2 — AUTORREGULACIÓN DUP INTRA-SESIÓN
# ==============================================================================
def autorregular_dia(numero: str, ejercicio: str, rpe_reportado: float,
                     rpe_objetivo: float, series_restantes: int,
                     carga_actual_kg: float) -> dict:
    """El atleta reporta la 1.ª serie del día muy por encima del objetivo
    (p. ej. RPE 9.5-10 en día de hipertrofia a RPE 7-8). Recalcula el bloque:

      overshoot = RPE_reportado − RPE_objetivo
      ajuste    = min(10 %, 3 % × overshoot)      # misma K del motor
      series    = se recorta 1 (o 2 si overshoot ≥ 2), nunca < 2
      escalada  = 2.ª alarma en el MISMO día ⇒ el día completo pasa a
                  descarga: 60 % de la carga, mitad de series, tope RPE 7.

    Cada llamada se persiste en eventos_autorregulacion: auditoría de por qué
    se bajó una carga + feature futura del modelo."""
    ejercicio = canonizar(ejercicio)
    rpe_rep, rpe_obj = float(rpe_reportado), float(rpe_objetivo)
    restantes = max(0, int(series_restantes))
    carga = float(carga_actual_kg)
    overshoot = round(rpe_rep - rpe_obj, 1)
    inc = incremento_barra(numero)          # todo ajuste debe ser montable

    if overshoot <= 0.5:
        return {"ok": True, "accion": "mantener", "overshoot": overshoot,
                "carga_kg": redondear_carga(carga, inc), "series_restantes": restantes,
                "mensaje_coach": "Dentro de lo esperado: mismo peso, misma chamba."}

    alarmas_previas = _eventos_hoy(numero, "dup_", None)
    if rpe_rep >= 9.5 and alarmas_previas >= 1:
        detalle = {"overshoot": overshoot, "carga_original": carga,
                   "alarmas_previas_hoy": alarmas_previas}
        registrar_evento(numero, "dup_descarga", ejercicio, detalle)
        return {"ok": True, "accion": "descarga_del_dia", "overshoot": overshoot,
                "carga_kg": max(20.0, redondear_carga(carga * 0.60, inc)),
                "series_restantes": max(1, restantes // 2), "rpe_tope": 7.0,
                "razones": ["segunda alarma de RPE ≥9.5 en el mismo día",
                            "el SNC no está para sobrecarga hoy"],
                "mensaje_coach": ("Hoy el cuerpo mandó dos avisos: el día se "
                                  "convierte en técnica. 60 % del peso, la mitad "
                                  "de series, nada arriba de RPE 7 — la sobrecarga "
                                  "vuelve cuando duermas.")}

    ajuste = min(0.10, K_RPE * overshoot)
    recorte = 2 if overshoot >= 2.0 else 1
    nuevas_series = max(2, restantes - recorte) if restantes > 2 else restantes
    carga_nueva = max(20.0, redondear_carga(carga * (1.0 - ajuste), inc))
    detalle = {"overshoot": overshoot, "ajuste_pct": round(ajuste * 100, 1),
               "carga_original": carga, "carga_nueva": carga_nueva,
               "series": [restantes, nuevas_series]}
    registrar_evento(numero, "dup_ajuste", ejercicio, detalle)
    return {"ok": True, "accion": "ajustar_bloque", "overshoot": overshoot,
            "carga_kg": carga_nueva, "series_restantes": nuevas_series,
            "rpe_tope": rpe_obj,
            "razones": [f"overshoot de {overshoot:g} puntos de RPE en la 1.ª serie",
                        f"carga −{round(ajuste*100):d} % (≈{K_RPE*100:g} %/punto)",
                        f"series {restantes} → {nuevas_series}"],
            "mensaje_coach": ("Primera serie demasiado cara: bajamos a "
                              f"{carga_nueva:g} kg y hacemos {nuevas_series} series "
                              f"con tope RPE {rpe_obj:g}. Estímulo sí, hoyo de "
                              "fatiga no.")}