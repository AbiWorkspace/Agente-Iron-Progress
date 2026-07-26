# -*- coding: utf-8 -*-
"""
handlers_v9.py — Iron Progress v9
Capa de tools del orquestador: valida, ejecuta (via to_thread: NADA síncrono
toca el event loop — lección del Mandato 2 v8) y devuelve dicts JSON-listos.

Integración en llm_service.py (loop de tools, junto a los elif existentes):

    from handlers_v9 import DISPATCHER_V9
    ...
    elif nombre_tool in DISPATCHER_V9:
        resultado = await DISPATCHER_V9[nombre_tool](numero_usuario, argumentos)
        if resultado.get("ok"):
            if nombre_tool in ("registrar_serie_completada", "autorregular_dia"):
                acciones_ok.append(f"{nombre_tool}: {resultado.get('resumen', 'ok')}")
        elif resultado.get("error") not in ("datos_insuficientes",):
            acciones_fallidas.append(f"{nombre_tool} ({resultado.get('error')})")

Los payloads viajan como [TOOL-RESULT] ⇒ cada kg que contienen entra SOLO a la
whitelist dinámica del validador v8: el coach puede citar la predicción sin que
el guardián lo bloquee, y NO puede citar kilos que no vengan de aquí.
"""
from __future__ import annotations

import asyncio
import json

from services import motor_predictivo as mp

# ==============================================================================
# SOLICITUD 1 — registrar_serie_completada con TRÍADA OBLIGATORIA
# ==============================================================================
def _f(x, defecto=None):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return defecto


async def tool_registrar_serie_completada(numero: str, args: dict) -> dict:
    """Exige Peso + Repeticiones + RPE (+ dolor opcional). Si falta algo, NO
    guarda a medias ni inventa: devuelve `faltan` para que el coach pregunte
    exactamente ese dato — engrana con la maquinaria de honestidad v8
    (acciones_fallidas / _instruccion_fallback)."""
    ejercicio = str(args.get("ejercicio") or "").strip()
    peso = _f(args.get("peso_kg"))
    reps = args.get("repeticiones")
    rpe = _f(args.get("rpe"))
    dolor = str(args.get("dolor_articular") or "ninguno").strip() or "ninguno"

    faltan = []
    if not ejercicio:
        faltan.append("ejercicio")
    if peso is None or not (1.0 <= peso <= 500.0):
        faltan.append("peso_kg (1-500)")
    try:
        reps = int(reps)
        if not (1 <= reps <= 50):
            faltan.append("repeticiones (1-50)")
    except (TypeError, ValueError):
        faltan.append("repeticiones")
    if rpe is None or not (5.0 <= rpe <= 10.0):
        faltan.append("rpe (5-10)")
    else:
        rpe = round(rpe * 2) / 2.0            # pasos de 0.5
    if faltan:
        return {"ok": False, "error": "faltan_datos", "faltan": faltan,
                "instruccion": ("NO afirmes que la serie quedó registrada. "
                                "Pide al atleta SOLO estos datos: " + ", ".join(faltan))}

    # PR: comparar contra el mejor e1RM histórico ANTES de insertar
    e1_nuevo, reps_ef = mp.e1rm_serie(peso, reps, rpe)
    historicas = await asyncio.to_thread(mp.leer_series, numero, ejercicio, 365)
    e1_prev = 0.0
    for s in historicas:
        e1, ef = mp.e1rm_serie(s["w"], s["r"], s["rpe"])
        if ef <= 12:
            e1_prev = max(e1_prev, e1)
    es_pr = bool(e1_prev) and e1_nuevo > e1_prev + 0.5

    await asyncio.to_thread(mp.guardar_serie_v9, numero, ejercicio, peso, reps, rpe, dolor)

    return {"ok": True,
            "resumen": f"{ejercicio} {peso:g}×{reps} @RPE {rpe:g}",
            "e1rm_serie_kg": e1_nuevo,
            "e1rm_previo_kg": round(e1_prev, 1) or None,
            "es_record": es_pr,
            "confianza_e1rm": "baja (>12 reps efectivas)" if reps_ef > 12 else "normal",
            "dolor_articular": dolor,
            "nota_coach": ("¡Es PR de e1RM! Celébralo y menciona el número." if es_pr
                           else "Confirma en corto; no inventes e1RM fuera de este payload.")}


# ==============================================================================
# SOLICITUDES 2 y 3 — envoltorios async del motor
# ==============================================================================
async def tool_predecir_carga_optima(numero: str, args: dict) -> dict:
    return await asyncio.to_thread(
        mp.predecir_carga_optima, numero,
        str(args.get("ejercicio") or ""),
        int(_f(args.get("repeticiones_objetivo"), 5)),
        _f(args.get("rpe_objetivo"), 8.0))


async def tool_analizar_mrv(numero: str, args: dict) -> dict:
    return await asyncio.to_thread(
        mp.analizar_mrv, numero,
        args.get("grupo_muscular") or None,
        int(_f(args.get("ventana_dias"), 7)))


async def tool_autorregular_dia(numero: str, args: dict) -> dict:
    req = ("ejercicio", "rpe_reportado", "rpe_objetivo",
           "series_restantes", "carga_actual_kg")
    faltan = [k for k in req if args.get(k) in (None, "")]
    if faltan:
        return {"ok": False, "error": "faltan_datos", "faltan": faltan,
                "instruccion": "Pide al atleta los datos faltantes antes de recalcular."}
    res = await asyncio.to_thread(
        mp.autorregular_dia, numero,
        str(args["ejercicio"]), _f(args["rpe_reportado"]),
        _f(args["rpe_objetivo"]), int(_f(args["series_restantes"], 0)),
        _f(args["carga_actual_kg"]))
    res["resumen"] = f"{res.get('accion')}: {res.get('carga_kg')} kg × {res.get('series_restantes')} series"
    return res


async def tool_enviar_grafica_progreso(numero: str, args: dict) -> dict:
    """Genera la gráfica de e1RM y la envía por WhatsApp (upload → media_id →
    message). Import perezoso para que este módulo no exija credenciales de
    Meta al arrancar en desarrollo."""
    from services import proactividad
    ejercicio = str(args.get("ejercicio") or "").strip()
    dias = int(_f(args.get("periodo_dias"), 90))
    if not ejercicio:
        return {"ok": False, "error": "faltan_datos", "faltan": ["ejercicio"]}
    return await proactividad.generar_y_enviar_grafica(numero, ejercicio, dias,
                                                       caption=args.get("caption"))


DISPATCHER_V9 = {
    "registrar_serie_completada": tool_registrar_serie_completada,
    "predecir_carga_optima":      tool_predecir_carga_optima,
    "analizar_mrv":               tool_analizar_mrv,
    "autorregular_dia":           tool_autorregular_dia,
    "enviar_grafica_progreso":    tool_enviar_grafica_progreso,
}