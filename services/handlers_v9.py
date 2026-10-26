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

import time

from core.ejercicios import canonizar, mismo_ejercicio, para_mostrar
from services import motor_predictivo as mp

# ==============================================================================
# EJERCICIO CONFIRMADO POR EL ATLETA (guarda contra la deriva del LLM)
# ------------------------------------------------------------------------------
# Cuando el atleta corrige la detección de visión ("No, sentadilla 140kg"), ese
# dato queda resuelto en el orquestador... pero `registrar_serie_completada`
# guardaba `args["ejercicio"]` tal cual, o sea lo que el modelo hubiera decidido
# escribir. Con la rutina del día en contexto diciendo "Press Banco Plano", el
# reporte VBT salía correcto (SENTADILLA) y la serie se registraba como press:
# mismos kilos, mismas reps, mismo e1RM, ejercicio equivocado.
#
# Reforzar el prompt reduce la probabilidad; esta guarda la elimina. El dato
# confirmado se registra aquí antes de invocar al LLM y el handler lo impone.
#
# v14 — DOS USOS, DOS VENTANAS. El sello se usaba para dos cosas opuestas con un
# único TTL de 30 minutos:
#
#   HEREDAR  — el atleta manda "otra igual, 100x4" sin decir el ejercicio. Aquí
#              una ventana larga es justo lo que se quiere: la sesión sigue.
#   IMPONER  — el modelo trae OTRO nombre y el sello lo pisa. Esto solo tiene
#              sentido durante los minutos que siguen a la confirmación, porque
#              corrige una deriva puntual del modelo.
#
# Con 30 minutos para ambos, imponer se comía la sesión entera: en un entreno se
# cambia de ejercicio cada 5-10 minutos. Medido el 2026-07-30 — el atleta reporta
# "Curl de biceps con barra con 40kg hice 12 reps", el modelo emite correctamente
# `{"ejercicio": "Curl Biceps Barra"}`, y el sello de "Press Inclinado" puesto 7
# minutos antes lo sobrescribe: el curl quedó en su diario como press inclinado.
_TTL_CONFIRMADO_S = 30 * 60      # ventana de HERENCIA
_TTL_IMPONER_S = 3 * 60          # ventana de IMPOSICIÓN sobre lo que trae el modelo
_ejercicio_confirmado: dict[str, tuple[str, float]] = {}


def fijar_ejercicio_confirmado(numero: str, ejercicio: str | None) -> None:
    """El orquestador declara qué ejercicio confirmó el atleta para esta serie."""
    if not ejercicio:
        return
    _ejercicio_confirmado[str(numero)] = (str(ejercicio), time.time())


def leer_ejercicio_confirmado(numero: str, max_edad_s: float | None = None) -> str | None:
    """Devuelve el ejercicio confirmado vigente, o None si no hay o ya caducó.

    `max_edad_s` acota la ventana para quien necesita una garantía más fuerte que
    la herencia (ver la nota de DOS USOS, DOS VENTANAS). No lo consume: el LLM
    puede reinvocar la tool dentro del mismo turno.
    """
    dato = _ejercicio_confirmado.get(str(numero))
    if not dato:
        return None
    ejercicio, sellado = dato
    edad = time.time() - sellado
    if edad > _TTL_CONFIRMADO_S:
        _ejercicio_confirmado.pop(str(numero), None)
        return None
    if max_edad_s is not None and edad > max_edad_s:
        return None
    return ejercicio


def limpiar_ejercicio_confirmado(numero: str) -> None:
    _ejercicio_confirmado.pop(str(numero), None)


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

    # GUARDA: si el atleta confirmó el ejercicio de esta serie, manda ese dato.
    # El LLM puede desviarse hacia el nombre de la rutina del día o hacia la
    # detección de visión que el propio atleta ya corrigió.
    #
    # v14 — Pisar lo que trae el modelo solo se admite dentro de _TTL_IMPONER_S;
    # pasada esa ventana el sello sigue sirviendo para HEREDAR (el atleta no dijo
    # el ejercicio) pero ya no para sobrescribir un nombre explícito, que es como
    # una serie de curl acababa apuntada en el press inclinado de siete minutos
    # antes.
    corregido_desde = None
    if not ejercicio:
        ejercicio = leer_ejercicio_confirmado(numero) or ""
    else:
        confirmado = leer_ejercicio_confirmado(numero, max_edad_s=_TTL_IMPONER_S)
        if confirmado and not mismo_ejercicio(ejercicio, confirmado):
            corregido_desde, ejercicio = ejercicio, confirmado

    peso = _f(args.get("peso_kg"))
    reps = args.get("repeticiones")
    rpe = _f(args.get("rpe"))
    # El schema pide un número, pero el modelo a veces reenvía lo que dijo el
    # atleta tal cual ("me quedaron 2"). Antes eso valía None y la serie se
    # rechazaba entera por tríada incompleta; la frase lleva el dato dentro.
    if rpe is None and args.get("rpe") is not None:
        from services.llm_service import rpe_desde_texto
        rpe = rpe_desde_texto(str(args.get("rpe")))
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

    # v14 — DUPLICADO: la misma serie ya está en el diario de hace un momento.
    # Pasa cuando el atleta la reenvía (porque cree que no se guardó) o cuando
    # llega por las dos vías del turno. Un duplicado falsea el volumen semanal,
    # el MRV y la detección de PR, y el atleta no puede auditarlo.
    if await asyncio.to_thread(mp.serie_duplicada_reciente,
                               numero, ejercicio, peso, reps, rpe):
        etiqueta_dup = para_mostrar(ejercicio)
        return {"ok": True, "duplicada": True,
                "resumen": f"{etiqueta_dup} {peso:g}×{reps} @RPE {rpe:g} (ya estaba apuntada)",
                "nota_coach": (f"Esa serie de {etiqueta_dup} YA constaba en su diario, así que "
                               f"NO se apuntó otra vez. Díselo con naturalidad —ya la tienes— y "
                               f"sigue; no le pidas que la repita ni le digas que falló nada.")}

    # PR: comparar contra el mejor e1RM histórico ANTES de insertar
    e1_nuevo, reps_ef = mp.e1rm_serie(peso, reps, rpe)
    historicas = await asyncio.to_thread(mp.leer_series, numero, ejercicio, 365)
    e1_prev = 0.0
    for s in historicas:
        e1, ef = mp.e1rm_serie(s["w"], s["r"], s["rpe"])
        if ef <= 12:
            e1_prev = max(e1_prev, e1)
    es_pr = bool(e1_prev) and e1_nuevo > e1_prev + 0.5

    vbt_vm = _f(args.get("vbt_velocidad_media") or args.get("velocidad_media"))
    vbt_vp = _f(args.get("vbt_velocidad_pico") or args.get("velocidad_pico"))
    vbt_vl = _f(args.get("vbt_perdida_velocidad") or args.get("perdida_velocidad"))
    rom = _f(args.get("rom_cm") or args.get("rom"))
    calidad = str(args.get("calidad_tecnica") or args.get("calidad") or "").strip() or None

    kwargs_extra = {}
    if vbt_vm is not None:
        kwargs_extra["vbt_velocidad_media"] = vbt_vm
    if vbt_vp is not None:
        kwargs_extra["vbt_velocidad_pico"] = vbt_vp
    if vbt_vl is not None:
        kwargs_extra["vbt_perdida_velocidad"] = vbt_vl
    if rom is not None:
        kwargs_extra["rom_cm"] = rom
    if calidad:
        kwargs_extra["calidad_tecnica"] = calidad

    await asyncio.to_thread(mp.guardar_serie_v9, numero, ejercicio, peso, reps, rpe, dolor, **kwargs_extra)

    # El nombre que se devuelve al coach es el canónico, el mismo con el que
    # quedó guardado: si el modelo narra "Press Banco Plano" mientras la base
    # dice "press banca", el atleta no puede auditar su propio historial.
    etiqueta = para_mostrar(ejercicio)
    aviso = None
    if corregido_desde:
        aviso = (f"OJO: ibas a registrar '{corregido_desde}', pero el atleta "
                 f"confirmó '{etiqueta}' para esta serie. Se guardó como "
                 f"'{etiqueta}'. Nómbralo así al responder.")

    return {"ok": True,
            "resumen": f"{etiqueta} {peso:g}×{reps} @RPE {rpe:g}",
            **({"correccion_ejercicio": aviso} if aviso else {}),
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


async def tool_consultar_historial_series(numero: str, args: dict) -> dict:
    from services.db_service import consultar_series_atleta
    ejercicio = args.get("ejercicio")
    limite = int(_f(args.get("limite"), 20))
    return await asyncio.to_thread(consultar_series_atleta, numero, ejercicio, limite)


DISPATCHER_V9 = {
    "registrar_serie_completada": tool_registrar_serie_completada,
    "predecir_carga_optima":      tool_predecir_carga_optima,
    "analizar_mrv":               tool_analizar_mrv,
    "autorregular_dia":           tool_autorregular_dia,
    "enviar_grafica_progreso":    tool_enviar_grafica_progreso,
    "consultar_historial_series": tool_consultar_historial_series,
}