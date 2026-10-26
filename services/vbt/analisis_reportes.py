# -*- coding: utf-8 -*-
"""
services/vbt/analisis_reportes.py — Motor de análisis determinista de series, recomendaciones técnicas y reportes.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from services.vbt.configuracion import CONFIG
from services.vbt.perfiles_biomecanicos import (
    PerfilEjercicio,
    obtener_perfil_ejercicio,
    _normalizar_nivel,
    calcular_velocity_loss,
    _clasificar_estimulo,
    estimar_e1rm_serie,
    estimar_zona_intensidad,
    estimar_rir_por_velocidad,
    estimar_rir_por_vl,
    estimar_reps_restantes,
    _recalcular_vl_por_rep,
    _json_safe,
)


# ==============================================================================
# MOTOR DE ANÁLISIS DETERMINISTA DE LA SERIE
# ==============================================================================
def analizar_serie(registro: list, perfil: PerfilEjercicio,
                   nivel_atleta: str = "Avanzado",
                   carga_kg: Optional[float] = None) -> dict:
    nivel = _normalizar_nivel(nivel_atleta)
    n = len(registro)
    if n == 0:
        return {}
    _recalcular_vl_por_rep(registro)

    v_avgs = [r.v_avg for r in registro]
    i_best = int(np.argmax(v_avgs))
    v_best, v_first, v_last = v_avgs[i_best], v_avgs[0], v_avgs[-1]

    vl = calcular_velocity_loss(registro)
    estimulo = _clasificar_estimulo(vl)

    v_min = float(min(v_avgs))
    res_1rm = estimar_e1rm_serie(carga_kg, v_first, v_min, n, perfil, nivel)
    pct1rm, e1rm = res_1rm["pct_1rm"], res_1rm["e1rm_kg"]
    mvt_ef, e1rm_origen = res_1rm["mvt_efectivo"], res_1rm["origen"]
    zona = estimar_zona_intensidad(pct1rm)

    rir_vel = estimar_rir_por_velocidad(v_last, perfil, nivel)
    rir_vl = estimar_rir_por_vl(vl, n)
    reps_rest = estimar_reps_restantes(rir_vel, rir_vl)

    # Consistencia de ROM
    roms = [r.rom_cm for r in registro]
    rom_medio = float(np.mean(roms))
    rom_cv = (float(np.std(roms) / rom_medio * 100.0)
              if (n >= 2 and rom_medio > 0) else None)
    i_rom_min = int(np.argmin(roms))

    # Bar path
    desvs = [r.desv_horizontal_cm for r in registro]
    i_desv = int(np.argmax(desvs))
    desv_max = float(desvs[i_desv])
    efic_media = float(np.mean([r.eficiencia_barra_pct for r in registro]))

    # Sticking point
    stickings = [getattr(r, "sticking_s", 0.0) or 0.0 for r in registro]
    i_stick = int(np.argmax(stickings))
    sticking_max = float(stickings[i_stick])

    # Tempo por fases
    t_exc = [r.t_excentrica_s for r in registro]
    amorts = [r.t_amortiguacion_s for r in registro
              if r.t_amortiguacion_s is not None]
    t_conc = [r.tiempo_s for r in registro]
    amort_delta = None
    if len(amorts) >= 2 and amorts[0] > 0.03:
        amort_delta = float(round((amorts[-1] - amorts[0]) / amorts[0] * 100.0, 0))

    # Mayor caída entre reps consecutivas
    caida = None
    if n >= 2 and v_best > 0:
        drops = [(v_avgs[i] - v_avgs[i + 1]) / v_best * 100.0 for i in range(n - 1)]
        j = int(np.argmax(drops))
        patron = "progresiva"
        if n >= 3:
            previas = [d for d in drops[:-1] if d > 0]
            if previas and drops[-1] > 1.8 * float(np.mean(previas)) and drops[-1] > 5:
                patron = "abrupta al final (borde del límite real)"
        caida = {"entre": (registro[j].rep, registro[j + 1].rep),
                 "delta_pct": float(round(max(drops[j], 0.0), 1)),
                 "patron": patron}

    def _rep_corte(umbral: float) -> Optional[int]:
        for r in registro:
            if r.vl_pct >= umbral:
                return r.rep
        return None
    cortes = {"vl10": _rep_corte(10), "vl20": _rep_corte(20), "vl30": _rep_corte(30)}

    # Cinemática articular
    rodillas = [r.angulo_rodilla_min for r in registro
                if r.angulo_rodilla_min is not None]
    pose = None
    if rodillas:
        pose = {"rodilla_min_media": float(round(float(np.mean(rodillas)), 1)),
                "rodilla_rango": float(round(max(rodillas) - min(rodillas), 1))}
        vang = [r.vel_angular_rodilla_max for r in registro
                if r.vel_angular_rodilla_max is not None]
        if len(vang) >= 2 and vang[0] > 0:
            pose["caida_vel_angular_pct"] = float(
                round((vang[0] - vang[-1]) / vang[0] * 100.0, 1))

    # Insights generados
    insights = []
    txt = f"1ª rep a {v_first:.2f} m/s"
    if pct1rm:
        txt += f" ≈ {pct1rm:.0f}% de tu 1RM"
    if e1rm and carga_kg:
        if e1rm_origen == "piso_reps":
            txt += (f" → completar {n} rep{'s' if n > 1 else ''} con "
                    f"{carga_kg:g} kg GARANTIZA un 1RM de al menos "
                    f"{e1rm:.1f} kg (piso por volumen, Epley)")
        else:
            txt += (f" → con {carga_kg:g} kg en barra, tu 1RM de HOY "
                    f"ronda {e1rm:.1f} kg")
    insights.append(txt + f". Zona: {zona}.")

    if mvt_ef < res_1rm["mvt_tabla"] - 1e-6:
        insights.append(
            f"Completaste reps a {v_min:.2f} m/s, por debajo del MVT de tabla "
            f"({res_1rm['mvt_tabla']:.2f}): tu velocidad de fallo real es más "
            f"baja que el estándar de tu nivel. Los cálculos de hoy usan tu "
            f"MVT efectivo ({mvt_ef:.2f} m/s).")

    if sticking_max >= 0.5:
        insights.append(
            f"En la rep {registro[i_stick].rep} la barra quedó clavada "
            f"{sticking_max:.1f} s a media subida (sticking point): la subida "
            f"completa duró {registro[i_stick].tiempo_s:.1f} s y se contabiliza "
            f"entera, sin recortar los frames lentos.")

    if i_best != 0:
        insights.append(
            f"Tu rep más rápida fue la {registro[i_best].rep} ({v_best:.2f} m/s), "
            f"no la 1ª ({v_first:.2f}): saliste 'frío' de inicio; el Velocity Loss "
            f"se mide desde la rep {registro[i_best].rep}.")

    if vl is not None:
        insights.append(f"Velocity Loss de {vl:.1f}% → {estimulo}.")

    if caida and caida["delta_pct"] >= 3:
        insights.append(
            f"El mayor bajón fue entre la rep {caida['entre'][0]} y la "
            f"{caida['entre'][1]} (−{caida['delta_pct']:.1f}% sobre tu mejor); "
            f"patrón de caída: {caida['patron']}.")

    if reps_rest:
        mvt = perfil.mvt[nivel]
        insights.append(
            f"Última rep a {v_last:.2f} m/s vs tu velocidad de fallo (~{mvt:.2f}): "
            f"quedaban {reps_rest['texto']} reps en reserva.")

    if rom_cv is not None and rom_cv > CONFIG.rom_cv_alerta_pct:
        insights.append(
            f"ROM inconsistente (CV {rom_cv:.1f}%): la rep {registro[i_rom_min].rep} "
            f"se quedó en {roms[i_rom_min]:.0f} cm vs {rom_medio:.0f} cm de media — "
            f"vigila la profundidad cuando aparece la fatiga.")
    elif rom_cv is not None:
        insights.append(
            f"ROM estable ({rom_medio:.0f} cm, CV {rom_cv:.1f}%): técnica "
            f"estandarizada rep a rep, las velocidades son comparables.")

    if (amort_delta is not None and amort_delta >= CONFIG.amort_alerta_crec_pct):
        insights.append(
            f"La pausa en el fondo creció de {int(amorts[0] * 1000)} a "
            f"{int(amorts[-1] * 1000)} ms (+{amort_delta:.0f}%): estás perdiendo "
            f"el rebote elástico, señal temprana de fatiga.")

    if n >= 2 and t_conc[0] > 0 and t_conc[-1] > t_conc[0] * 1.4:
        insights.append(
            f"La subida pasó de {t_conc[0]:.2f} s (rep 1) a {t_conc[-1]:.2f} s "
            f"(última): cada rep te costó más tiempo bajo la barra.")

    if desv_max >= CONFIG.umbral_desv_horizontal_cm:
        insights.append(
            f"Bar path: la rep {registro[i_desv].rep} derivó {desv_max:.1f} cm en "
            f"horizontal (eficiencia {registro[i_desv].eficiencia_barra_pct:.0f}%): "
            f"la barra se salió de la vertical.")

    if pose and pose.get("rodilla_rango", 0) > 8:
        insights.append(
            f"Profundidad articular variable: el ángulo mínimo de rodilla osciló "
            f"{pose['rodilla_rango']:.0f}° entre repeticiones.")
    if pose and pose.get("caida_vel_angular_pct") and pose["caida_vel_angular_pct"] > 15:
        insights.append(
            f"La velocidad de extensión de rodilla cayó "
            f"{pose['caida_vel_angular_pct']:.0f}% de la 1ª a la última rep: la "
            f"fatiga también se ve en la articulación, no solo en la barra.")

    recomendacion = _construir_recomendacion(
        vl, reps_rest, carga_kg, v_first, perfil, nivel,
        cortes, rom_cv, desv_max, amort_delta, rir_vel,
        pct_1rm=pct1rm, e1rm=e1rm, sticking_max=sticking_max)

    return _json_safe({
        "reps": n,
        "v_mejor": round(v_best, 3), "rep_mas_rapida": registro[i_best].rep,
        "v_primera": round(v_first, 3), "v_ultima": round(v_last, 3),
        "vl_pct": vl, "estimulo": estimulo,
        "pct_1rm_estimado": pct1rm, "e1rm_kg": e1rm, "zona_intensidad": zona,
        "e1rm_origen": e1rm_origen, "mvt_efectivo": mvt_ef,
        "mvt_tabla": res_1rm["mvt_tabla"],
        "sticking_max_s": round(sticking_max, 2),
        "rep_sticking": (registro[i_stick].rep if sticking_max > 0 else None),
        "rir_velocidad": rir_vel, "rir_vl": rir_vl, "reps_restantes": reps_rest,
        "rom_medio_cm": round(rom_medio, 1),
        "rom_cv_pct": (round(rom_cv, 1) if rom_cv is not None else None),
        "desv_horizontal_max_cm": round(desv_max, 1),
        "eficiencia_barra_media_pct": round(efic_media, 1),
        "tempo": {
            "excentrica_media_s": round(float(np.mean(t_exc)), 2),
            "amortiguacion_media_ms": (int(float(np.mean(amorts)) * 1000)
                                       if amorts else None),
            "concentrica_media_s": round(float(np.mean(t_conc)), 2),
            "amort_delta_pct": amort_delta,
        },
        "caida_mayor": caida, "cortes_vl": cortes, "pose": pose,
        "insights": insights, "recomendacion": recomendacion,
    })


def _construir_recomendacion(vl, reps_rest, carga_kg, v_first, perfil, nivel,
                             cortes, rom_cv, desv_max, amort_delta,
                             rir_vel, pct_1rm=None, e1rm=None,
                             sticking_max=0.0) -> dict:
    """Orden concreta para la siguiente serie + guía por objetivo."""
    mvt = perfil.mvt[_normalizar_nivel(nivel)]
    v80 = perfil.zona_fuerza_vel[0]
    pendiente = (v80 - mvt) / 20.0
    v_target = float(np.mean(perfil.zona_fuerza_vel))

    CAP_SUBIDA_PCT, CAP_BAJADA_PCT = 10.0, 7.5
    ajuste = None
    ajuste_datos = None
    if carga_kg and e1rm and pct_1rm:
        objetivo, motivo = None, None
        if pct_1rm >= 90:
            objetivo, motivo = e1rm * 0.875, "volumen"
        elif pct_1rm < 80:
            objetivo, motivo = e1rm * 0.85, "acercar_fuerza"
        if objetivo is not None:
            delta_pct = (objetivo - carga_kg) / carga_kg * 100.0
            delta_pct = max(min(delta_pct, CAP_SUBIDA_PCT), -CAP_BAJADA_PCT)
            kg = round(abs(carga_kg * delta_pct / 100.0) / 2.5) * 2.5
            if kg >= 2.5:
                nueva = carga_kg + kg if delta_pct > 0 else carga_kg - kg
                ajuste_datos = {"delta_pct": float(round(delta_pct, 1)),
                                "kg": float(kg), "carga_nueva": float(nueva),
                                "motivo": motivo}
                if motivo == "volumen":
                    ajuste = (f"Hoy trabajaste al ~{pct_1rm:.0f}% de tu 1RM: "
                              f"para acumular series de 3-5 con margen "
                              f"(85-90%), baja a ~{nueva:g} kg (−{kg:g} kg).")
                else:
                    ajuste = (f"Estás al ~{pct_1rm:.0f}% del 1RM: para entrenar "
                              f"fuerza (≥85%) sube a ~{nueva:g} kg (+{kg:g} kg).")
    elif not carga_kg and pendiente > 0 and v_first and v_first > 0:
        delta_pct = (v_first - v_target) / pendiente
        delta_pct = max(min(delta_pct, CAP_SUBIDA_PCT), -CAP_BAJADA_PCT)
        if abs(delta_pct) >= 3:
            verbo = "Sube" if delta_pct > 0 else "Baja"
            ajuste = (f"{verbo} ~{abs(delta_pct):.0f}% la carga para la zona de "
                      f"fuerza. Mándame los kg y te lo doy en kilos exactos.")
            ajuste_datos = {"delta_pct": float(round(delta_pct, 1))}

    rest_txt = reps_rest["texto"] if reps_rest else "varias"
    if vl is None:
        if rir_vel is not None and rir_vel <= 1:
            titular = ("Single al límite: no acumules más intentos máximos hoy; "
                       "cierra con trabajo técnico al 80-85%.")
        else:
            titular = ("Single con margen: hay espacio para subir en el próximo "
                       "intento o pasar al trabajo de volumen.")
    elif vl < 10:
        titular = (f"Serie muy fresca (VL {vl:.0f}%, {rest_txt} reps en reserva): "
                   f"si el objetivo es fuerza, añade carga o repeticiones; si era "
                   f"aproximación o trabajo de potencia, quedó perfecta.")
    elif vl < 20:
        c20 = cortes.get("vl20")
        cola = (f"hoy el punto de corte del 20% llegó en la rep {c20}."
                if c20 else "no llegaste al corte del 20%: cabe 1 rep más por serie.")
        titular = (f"Zona óptima de fuerza (VL {vl:.0f}%): repite esta carga y "
                   f"este esfuerzo; {cola}")
    elif vl < 30:
        c20 = cortes.get("vl20")
        cola = (f"si el bloque es de fuerza, corta en la rep {c20} (VL 20%)."
                if c20 else "para fuerza, corta ~2 reps antes.")
        titular = (f"Estímulo de hipertrofia conseguido (VL {vl:.0f}%); {cola}")
    else:
        titular = (f"VL {vl:.0f}%: serie al borde del fallo, costo de "
                   f"recuperación alto. Baja 5-10% la carga en la siguiente o "
                   f"alarga el descanso a 4-5 min.")

    tecnica = None
    if desv_max >= CONFIG.umbral_desv_horizontal_cm:
        tecnica = (f"Prioridad técnica: controla la deriva horizontal de la "
                   f"barra ({desv_max:.1f} cm).")
    elif rom_cv is not None and rom_cv > CONFIG.rom_cv_alerta_pct:
        tecnica = (f"Prioridad técnica: iguala la profundidad en todas las reps "
                   f"(el ROM varió {rom_cv:.0f}%).")
    elif sticking_max >= 0.8:
        tecnica = (f"Prioridad técnica: la barra se te clava ~{sticking_max:.1f} s "
                   f"a media subida; mete trabajo del punto de estancamiento "
                   f"(pausas, pines o velocidad en ese rango).")
    elif amort_delta is not None and amort_delta >= CONFIG.amort_alerta_crec_pct:
        tecnica = ("Prioridad técnica: acorta la transición en el fondo, estás "
                   "perdiendo el rebote elástico.")

    c10, c20 = cortes.get("vl10"), cortes.get("vl20")
    fuerza = "Corta cada serie al perder ~20% de velocidad"
    fuerza += (f" (hoy: rep {c20})." if c20
               else " (hoy no lo alcanzaste: puedes añadir reps o carga).")
    if vl is not None:
        hiper = f"Busca series con 20-30% de VL; hoy cerraste en {vl:.0f}%."
    else:
        hiper = "Busca series con 20-30% de VL (con singles no aplica)."
    potencia = "Corta al 10% de VL"
    if c10:
        potencia += f" (hoy: rep {c10})."
    elif vl is not None and vl < 10:
        potencia += "; hoy toda la serie fue válida para potencia."
    else:
        potencia += "."

    return {"titular": titular, "ajuste_carga": ajuste,
            "ajuste_datos": ajuste_datos, "tecnica": tecnica,
            "por_objetivo": {"fuerza": fuerza, "hipertrofia": hiper,
                             "potencia": potencia}}


def _lineas_por_rep(registro: list) -> list:
    lineas = []
    for r in registro:
        extra = (f" · trans {int(r.t_amortiguacion_s * 1000)}ms"
                 if r.t_amortiguacion_s is not None else "")
        if getattr(r, "sticking_s", 0.0) >= 0.3:
            extra += f" · clavada {r.sticking_s:.1f}s"
        lineas.append(
            f"R{r.rep}: {r.v_avg:.2f} m/s (pico {r.v_peak:.2f}) · "
            f"ROM {r.rom_cm:.0f}cm · sube {r.tiempo_s:.2f}s · "
            f"baja {r.t_excentrica_s:.2f}s{extra} · VL {r.vl_pct:.0f}%")
    return lineas


def _construir_reporte_tecnico(ejercicio: str, registro: list, analisis: dict,
                               carga_kg: Optional[float] = None) -> str:
    """Versión densa con todas las métricas para dashboards o entrenadores."""
    a = analisis
    L = [f"*IRON PROGRESS · VBT (TÉCNICO) — {ejercicio.upper()}*", ""]

    L.append("*RESUMEN*")
    carga_txt = f"{carga_kg:g} kg" if carga_kg else "peso por calibrar (envíame los kg)"
    linea = f"Reps: *{a['reps']}* · Carga: {carga_txt}"
    if a.get("pct_1rm_estimado"):
        linea += f" (~{a['pct_1rm_estimado']:.0f}% 1RM)"
    L.append(linea)
    if a.get("e1rm_kg"):
        L.append(f"1RM estimado de hoy: *{a['e1rm_kg']:.1f} kg*")
    if a.get("vl_pct") is not None:
        L.append(f"Fatiga (VL): *{a['vl_pct']:.1f}%* → {a['estimulo']}")
    if a.get("reps_restantes"):
        L.append(f"Reps que quedaban en reserva: *{a['reps_restantes']['texto']}*")

    L += ["", "*POR REPETICIÓN*"]
    lineas_reps = _lineas_por_rep(registro)
    L += lineas_reps

    L += ["", "*LECTURA DE LA SERIE*"]
    L += [f"• {i}" for i in a["insights"][:6]]

    t = a["tempo"]
    amort_txt = (f"{t['amortiguacion_media_ms']} ms"
                 if t["amortiguacion_media_ms"] is not None else "—")
    rom_txt = f"ROM medio {a['rom_medio_cm']:.0f} cm"
    if a["rom_cv_pct"] is not None:
        rom_txt += f" (CV {a['rom_cv_pct']:.1f}%)"
    L += ["", "*TÉCNICA Y TEMPO*",
          rom_txt + (f" · Bar path: desv. máx {a['desv_horizontal_max_cm']:.1f} cm, "
                     f"eficiencia {a['eficiencia_barra_media_pct']:.0f}%"),
          (f"Tempo medio: bajada {t['excentrica_media_s']:.2f}s · "
           f"transición {amort_txt} · subida {t['concentrica_media_s']:.2f}s")]

    rec = a["recomendacion"]
    L += ["", "*SIGUIENTE SERIE*", rec["titular"]]
    if rec.get("ajuste_carga"):
        L.append(rec["ajuste_carga"])
    if rec.get("tecnica"):
        L.append(rec["tecnica"])
    obj = rec["por_objetivo"]
    L.append(f"Según tu objetivo → Fuerza: {obj['fuerza']} "
             f"Hipertrofia: {obj['hipertrofia']} Potencia: {obj['potencia']}")

    msg = "\n".join(L)

    if len(msg) > 3800 and len(registro) > 6:
        idx = L.index("*POR REPETICIÓN*")
        compacto = (lineas_reps[:3]
                    + [f"… ({len(registro) - 5} reps intermedias omitidas) …"]
                    + lineas_reps[-2:])
        L = L[:idx + 1] + compacto + L[idx + 1 + len(lineas_reps):]
        msg = "\n".join(L)
    return msg


def _semaforo_vl(vl_pct: float) -> str:
    if vl_pct < 10:
        return "\U0001F7E2"
    if vl_pct < 20:
        return "\U0001F7E1"
    if vl_pct < 30:
        return "\U0001F7E0"
    return "\U0001F534"


def _construir_reporte_whatsapp(ejercicio: str, registro: list, analisis: dict,
                                carga_kg: Optional[float] = None) -> str:
    """Reporte de WhatsApp para el atleta con formato claro y estructurado."""
    a = analisis
    n = a["reps"]
    vl = a.get("vl_pct")
    rest = a.get("reps_restantes") or {}

    rir_val = a.get("rir_velocidad")
    if rir_val is None and rest:
        try:
            rir_val = float(rest.get("min", 0))
        except (ValueError, TypeError):
            rir_val = None

    rpe_val = round(max(10.0 - rir_val, 5.0), 1) if rir_val is not None else None
    rpe_txt = f"{rpe_val:g}" if rpe_val is not None else "N/D"
    rir_txt = f"~{rir_val:.0f}" if rir_val is not None else (rest.get("texto", "N/D"))

    carga_linea = f"{carga_kg:g} kg" if carga_kg else "Peso por calibrar"
    resumen_serie = f"{carga_linea} × {n} rep{'s' if n != 1 else ''}  ·  RPE {rpe_txt} (RIR {rir_txt})"

    L = [
        f"🏋️ *{ejercicio.upper()}*",
        resumen_serie,
        ""
    ]

    L.append("*Resumen de la serie*")
    if vl is None:
        rir_v = a.get("rir_velocidad")
        if rir_v is not None and rir_v <= 1:
            lectura = "fue un intento casi máximo: poquísimo margen de fallo"
        elif rir_v is not None and rir_v <= 3:
            lectura = "pesó considerablemente, pero mantuviste margen"
        else:
            lectura = "salió fluida y explosiva, con bastante margen"
        L.append(f"Repetición única a {a['v_mejor']:.2f} m/s: {lectura}.")
    elif vl < 10:
        L.append(f"Serie muy fresca: apenas perdiste velocidad ({vl:.0f}% VL). Terminaste con mucha reserva.")
    elif vl < 20:
        L.append(f"Serie sólida y controlada ({vl:.0f}% VL): fatiga óptima para estimular fuerza sin sobrecargar el SNC.")
    elif vl < 30:
        L.append(f"Serie exigente ({vl:.0f}% VL): alto estímulo de hipertrofia con fatiga acumulada notable.")
    else:
        L.append(f"Serie al límite ({vl:.0f}% VL): cercana al fallo muscular. Requiere 48-72 h de recuperación.")

    L += ["", "*Detalle por repetición*"]
    i_best = a.get("rep_mas_rapida")
    lineas_reps = []
    for r in registro:
        pico_txt = f" (pico {r.v_peak:.2f} m/s)" if hasattr(r, "v_peak") and r.v_peak else ""
        if len(registro) == 1:
            lineas_reps.append(f"R{r.rep}: {r.v_avg:.2f} m/s{pico_txt}")
        elif r.rep == i_best:
            lineas_reps.append(f"R{r.rep} {_semaforo_vl(0)} {r.v_avg:.2f} m/s{pico_txt} — mejor rep")
        else:
            lineas_reps.append(f"R{r.rep} {_semaforo_vl(r.vl_pct)} {r.v_avg:.2f} m/s{pico_txt} (−{r.vl_pct:.0f}%)")
    L += lineas_reps

    frases = []
    if a.get("e1rm_kg") and carga_kg:
        if a.get("e1rm_origen") == "piso_reps":
            frases.append(f"*1RM de hoy:* al menos *{a['e1rm_kg']:.0f} kg* (garantizado por volumen a ~{a['pct_1rm_estimado']:.0f}% 1RM).")
        else:
            frases.append(f"*1RM estimado:* *{a['e1rm_kg']:.0f} kg* (~{a['pct_1rm_estimado']:.0f}% de intensidad relativa).")

    if rest:
        if rest.get("max", 9) <= 1:
            frases.append("*Reserva:* Terminaste al límite (≤1 rep en reserva).")
        else:
            frases.append(f"*Reserva:* Quedaban *{rest['texto']} reps* en el tanque.")

    if a.get("sticking_max_s", 0) >= 0.8 and a.get("rep_sticking"):
        frases.append(f"*Sticking point:* Clavado *{a['sticking_max_s']:.1f} s* a media subida en R{a['rep_sticking']}.")

    caida = a.get("caida_mayor") or {}
    if str(caida.get("patron", "")).startswith("abrupta"):
        ra, rb = caida["entre"]
        frases.append(f"*Degradación:* Bajón brusco entre R{ra} y R{rb} (−{caida['delta_pct']:.0f}%).")

    amort_delta = (a.get("tempo") or {}).get("amort_delta_pct")
    if amort_delta is not None and amort_delta >= CONFIG.amort_alerta_crec_pct:
        amorts = [r.t_amortiguacion_s for r in registro if r.t_amortiguacion_s is not None]
        if len(amorts) >= 2:
            frases.append(f"*Pausa inferior:* Se alargó ({amorts[0]:.1f}s → {amorts[-1]:.1f}s), perdiendo elástico.")

    if a.get("desv_horizontal_max_cm", 0) >= CONFIG.umbral_desv_horizontal_cm:
        frases.append(f"*Bar path:* Desviación de *{a['desv_horizontal_max_cm']:.0f} cm* de la vertical.")
    elif a.get("rom_cv_pct") is not None and a["rom_cv_pct"] > CONFIG.rom_cv_alerta_pct:
        frases.append(f"*Consistencia ROM:* Profundidad variable ({a['rom_cv_pct']:.0f}% CV).")
    elif n > 1:
        frases.append("*Técnica:* Trayectoria vertical limpia y ROM consistente.")

    if frases:
        L += ["", "*Análisis VBT y técnico*"] + [f"• {f}" for f in frases[:5]]

    acciones = []
    cortes = a.get("cortes_vl") or {}
    c10, c20 = cortes.get("vl10"), cortes.get("vl20")
    if vl is None:
        rir_v = a.get("rir_velocidad")
        if rir_v is not None and rir_v <= 1:
            acciones.append("Intento máximo alcanzado: concluye con series secundarias más ligeras.")
        else:
            acciones.append("Margen disponible: puedes incrementar 2.5-5 kg en la siguiente.")
    elif vl >= 30:
        acciones.append("Fatiga alta acumulada: reduce carga 5-10% o descansa 4-5 min.")
    elif vl >= 20:
        cola = f"corta en rep {c20}" if c20 else "corta 1-2 reps antes"
        acciones.append(f"Excelente estímulo para fuerza/hipertrofia. Para enfoque de fuerza pura: {cola}.")
    elif vl >= 10:
        acciones.append("Carga e intensidad en punto óptimo: mantén para la siguiente serie.")
    else:
        acciones.append("Carga ligera para tu potencial: incrementa peso o repeticiones.")

    ad = (a.get("recomendacion") or {}).get("ajuste_datos") or {}
    if ad.get("carga_nueva"):
        if ad.get("motivo") == "volumen":
            acciones.append(f"Para series de trabajo acumulativo (3-5 reps), ajusta a *{ad['carga_nueva']:g} kg* (−{ad['kg']:g} kg).")
        elif vl is None or vl < 30:
            acciones.append(f"Para zona de fuerza principal (≥85%), prueba *{ad['carga_nueva']:g} kg* (+{ad['kg']:g} kg).")

    if n > 1:
        p10 = f" (rep {c10})" if c10 else ""
        p20 = f" (rep {c20})" if c20 else ""
        acciones.append(f"Corte por velocidad: VL 10% potencia{p10} | VL 20% fuerza{p20} | VL 30% hipertrofia.")

    L += ["", "*Recomendación para la próxima serie*"]
    L += [f"• {t}" for t in acciones[:3]]

    if not carga_kg:
        L.append("Nota: indica los kg utilizados para calcular tu 1RM diario y cargas exactas.")

    L += ["", "_¿Necesitas ajustar la rutina o tienes dudas sobre la técnica? Escríbeme._"]
    msg = "\n".join(L)

    if len(msg) > 3800 and len(registro) > 6:
        idx = L.index("*Detalle por repetición*")
        compacto = (lineas_reps[:3]
                    + [f"… ({len(registro) - 5} reps intermedias) …"]
                    + lineas_reps[-2:])
        L = L[:idx + 1] + compacto + L[idx + 1 + len(lineas_reps):]
        msg = "\n".join(L)
    return msg


def _generar_autopsia_vbt(totales: int, con_disco: int, fase: str,
                          max_descenso: float, max_vel: float) -> str:
    cobertura = (100.0 * con_disco / totales) if totales else 0.0
    consejo = ""

    if totales == 0:
        razon = "El video está corrupto o tiene 0 frames leíbles."
    elif con_disco == 0:
        razon = "YOLO no detectó el disco en ningún momento."
        consejo = ("Graba de lado, con el disco COMPLETO dentro del encuadre "
                   "y sin que el cuerpo lo tape.")
    elif cobertura < 40.0:
        razon = (f"El disco solo fue visible en el {cobertura:.0f}% del video "
                 f"({con_disco} de {totales} frames): no hay señal suficiente "
                 f"para medir la barra.")
        consejo = ("Reencuadra: el disco debe verse completo durante toda la "
                   "serie. Cámara de lado, a la altura de la barra, y que no "
                   "quede fuera de plano al subir.")
    elif fase in ("calibrando", "reposo"):
        razon = f"Movimiento insuficiente: descenso máximo de {max_descenso:.1f}px."
    elif fase == "excentrica":
        razon = f"La barra bajó pero no subió. Vel máx: {max_vel:.3f} m/s."
    elif fase == "concentrica":
        razon = "La barra subió, pero la repetición no cerró correctamente."
    else:
        razon = f"Fase final atípica: {fase}."

    texto = (f"Fallo VBT.\nCAUSA: {razon}\n")
    if consejo:
        texto += f"CÓMO ARREGLARLO: {consejo}\n"
    return (texto + f"TELEMETRÍA -> Frames: {totales} | Tracked: {con_disco} "
            f"({cobertura:.0f}%) | Descenso Max: {max_descenso:.1f}px")


def _generar_contexto_llm(reps_completadas: int, velocidades_globales: list,
                          registro: list, ejercicio: str,
                          nivel_atleta: str = "Avanzado",
                          numero_usuario: str = None,
                          analisis: Optional[dict] = None,
                          carga_kg: Optional[float] = None) -> str:
    perfil = obtener_perfil_ejercicio(ejercicio)
    if analisis is None:
        analisis = analizar_serie(registro, perfil, nivel_atleta, carga_kg)
    a = analisis

    es_single = reps_completadas == 1
    vl_pct = a.get("vl_pct")
    fatiga_str = (f"{vl_pct:.1f}%" if vl_pct is not None
                  else ("N/A (Single: no hay degradación intra-serie)"
                        if es_single else "0.0%"))

    detalle_reps_str = ""
    for r in registro:
        trans = (f"{int(r.t_amortiguacion_s * 1000)} ms"
                 if r.t_amortiguacion_s is not None else "N/A")
        detalle_reps_str += (
            f"*Rep {r.rep}* -> Media: *{r.v_avg:.3f} m/s* | "
            f"Pico: _{r.v_peak:.3f} m/s_ | MPV: {r.mpv:.3f} m/s | "
            f"ROM: {r.rom_cm:.1f} cm | Conc: {r.tiempo_s:.2f}s | "
            f"Exc: {r.t_excentrica_s:.2f}s | Trans: {trans} | "
            f"DesvX: {r.desv_horizontal_cm:.1f} cm | VL: {r.vl_pct:.1f}%\n")

    rir_val = a.get("rir_velocidad")
    if rir_val is None and a.get("reps_restantes"):
        try:
            rir_val = float(a["reps_restantes"].get("min", 0))
        except (ValueError, TypeError):
            rir_val = None

    rpe_val = round(max(10.0 - rir_val, 5.0), 1) if rir_val is not None else None
    rpe_txt = f"{rpe_val:g}" if rpe_val is not None else "N/D"
    reps_rest_txt = (a["reps_restantes"]["texto"]
                     if a.get("reps_restantes") else "N/D")
    rir_txt = f"~{rir_val:.0f}" if rir_val is not None else reps_rest_txt
    e1rm_txt = (f"{a['e1rm_kg']:.1f} kg" if a.get("e1rm_kg")
                else "peso por calibrar (pídele los kg al atleta)")
    carga_txt = f"{carga_kg:g} kg" if carga_kg else "no informada"
    carga_linea = f"{carga_kg:g} kg" if carga_kg else "Peso por calibrar"

    v_prom = round(float(np.mean([r.v_avg for r in registro])), 3) if registro else 0.0
    resumen_serie_estandar = f"{carga_linea} x {reps_completadas} reps @ RPE {rpe_txt} (RIR {rir_txt})"

    hallazgos = "\n".join(f"- {i}" for i in a.get("insights", []))
    rec = a.get("recomendacion", {}) or {}
    rec_txt = " ".join(filter(None, [rec.get("titular"),
                                     rec.get("ajuste_carga"),
                                     rec.get("tecnica")]))
    obj = rec.get("por_objetivo", {})
    t = a.get("tempo", {})
    amort_txt = (f"{t.get('amortiguacion_media_ms')} ms"
                 if t.get('amortiguacion_media_ms') is not None else "N/A")

    contexto_final = f"""Eres el motor analítico de Iron Progress, un coach experto especializado en Velocity Based Training (VBT). Tu función es analizar la cinemática de la serie con máxima rigurosidad científica internamente, pero EXPRESAR tus conclusiones en un lenguaje de gimnasio motivador, directo y muy fácil de entender para el levantador.

[DATOS ESTRUCTURADOS DE LA SERIE PARA GUARDAR EN HISTORIAL]
- Serie Estándar (Formato Atrapable): {resumen_serie_estandar}
- Ejercicio: {ejercicio.upper()} (perfil VBT aplicado: {perfil.nombre})
- Carga en barra: {carga_txt}
- Repeticiones Completadas: {reps_completadas}
- RPE Estimado: {rpe_txt}
- RIR Estimado: {rir_txt}
- e1RM Estimado: {e1rm_txt}
- Pérdida de Velocidad (VL %, vs la rep más rápida): {fatiga_str}
- Velocidad Media Concéntrica (VCM): {v_prom:.3f} m/s

[TELEMETRÍA DETALLADA DE LA SERIE]
- Perfil del Atleta: {nivel_atleta} (Alta eficiencia neural y capacidad de "grind")

[DESGLOSE CINEMÁTICO POR REPETICIÓN]
{detalle_reps_str}
[ANÁLISIS DETERMINISTA DEL MOTOR — TU ÚNICA FUENTE DE VERDAD]
Estos hallazgos YA fueron calculados con los datos. Tu trabajo es narrarlos y traducirlos, NO recalcularlos ni contradecirlos:
{hallazgos}
- Reps en reserva estimadas: {reps_rest_txt} | RIR/RPE: {rir_txt} (RPE {rpe_txt})
- 1RM estimado de hoy: {e1rm_txt} | Zona de carga: {a.get('zona_intensidad', 'N/D')} | Origen del e1RM: {a.get('e1rm_origen') or 'N/D'} (piso_reps = mínimo garantizado por completar las reps; NUNCA digas que la carga superó el 100%)
- MVT efectivo usado hoy: {a.get('mvt_efectivo', 'N/D')} m/s (tabla del nivel: {a.get('mvt_tabla', 'N/D')}) | Barra clavada máx: {a.get('sticking_max_s', 0)} s (rep {a.get('rep_sticking') or '—'})
- Tempo medio: excéntrica {t.get('excentrica_media_s', 'N/A')}s · transición {amort_txt} · concéntrica {t.get('concentrica_media_s', 'N/A')}s
- Recomendación del motor para la siguiente serie: {rec_txt}
- Guía por objetivo -> Fuerza: {obj.get('fuerza', 'N/D')} | Hipertrofia: {obj.get('hipertrofia', 'N/D')} | Potencia: {obj.get('potencia', 'N/D')}
- El atleta YA recibió por WhatsApp un reporte en lenguaje simple (sin jerga). Si te pide más detalle (tempos por rep, recorrido, trayectoria de la barra, %s exactos), respóndelo desde ESTE análisis, en el mismo tono llano, sin recalcular nada.

[BASE DE CONOCIMIENTO 1: UMBRALES MVT (RIR 0 / RPE 10) POR NIVEL]
El MVT define la velocidad de fallo. Los atletas avanzados/élite tienen un MVT mucho más bajo (mayor fricción isométrica) que los novatos:
- Sentadilla (Squat): Novato ~0.40 m/s | Avanzado ~0.25 m/s | Élite 0.18-0.24 m/s.
- Peso Muerto (Deadlift): Novato ~0.35 m/s | Avanzado ~0.15 m/s | Élite <0.15 m/s.
- Press de Banca (Bench): Novato ~0.30 m/s | Avanzado 0.12-0.15 m/s | Élite 0.10-0.12 m/s.
- Dominadas (Pull-Ups): Novato ~0.40 m/s | Avanzado ~0.23 m/s | Élite 0.20-0.26 m/s.

[BASE DE CONOCIMIENTO 2: ZONAS DE INTENSIDAD ABSOLUTA (80% - 100% 1RM)]
Para estimar la carga sobre el 1RM basado en la velocidad de la primera repetición:
- Sentadilla: Fuerza-Vel (80-89%) = 0.60 - 0.40 m/s | Max Fuerza (90-100%) = 0.39 - MVT
- Peso Muerto: Fuerza-Vel (80-89%) = 0.58 - 0.46 m/s | Max Fuerza (90-100%) = 0.45 - MVT
- Press Banca: Fuerza-Vel (80-89%) = 0.42 - 0.26 m/s | Max Fuerza (90-100%) = 0.25 - MVT
- Dominadas: Fuerza-Vel (80-89%) = 0.59 - 0.44 m/s | Max Fuerza (90-100%) = 0.43 - MVT

[BASE DE CONOCIMIENTO 3: ESPECTRO DE ADAPTACIÓN Y FATIGA (VELOCITY LOSS - VL)]
Si la serie tiene múltiples repeticiones, el VL (%) dicta el fenotipo celular:
- 10% VL (4-6 RIR): Neurogénica Pura. Estrés lactácido nulo. Mantiene fibras rápidas IIX, aumenta Salto Vertical (CMJ) y potencia pico.
- 20% VL (2-3 RIR): Fuerza Máxima Híbrida. Optimiza eficiencia neural con bajo daño tisular.
- 30% VL (1-2 RIR): Miogénica Estructural. Umbral primario para hipertrofia miofibrilar. Alta fatiga y amoníaco.
- 40%+ VL (0 RIR/Fallo en tren inferior): Miogénica Extrema. Conversión a fibras IIA. Agotamiento masivo del SNC. Requiere >72h de recuperación.
*Nota: En Press de Banca y Dominadas, el fallo absoluto ocurre cerca del 50-60% VL.*

[INSTRUCCIONES DE ANÁLISIS EXCLUSIVAS Y ESTILO DE RESPUESTA]
0. REGISTRO DE SERIE: El bloque [DATOS ESTRUCTURADOS DE LA SERIE PARA GUARDAR EN HISTORIAL] contiene el formato exacto '{resumen_serie_estandar}' (kg + cantidad de reps + RPE + RIR) necesario para invocar la herramienta registrar_serie_completada o guardar la serie en el historial si la conversación lo amerita.
1. INTENSIDAD DE LA CARGA: ¿Qué tan pesado estuvo? Menciona el %1RM estimado y, si existe, el 1RM de hoy en kg. Si la carga no fue informada, escribe exactamente 'peso por calibrar' y pídele los kg al atleta.
2. FATIGA Y ESFUERZO: ¿Qué tan cerca del fallo se quedó? Traduce el VL% y di EXPLÍCITAMENTE cuántas reps le quedaban en el tanque ({reps_rest_txt}) y el RPE/RIR. Si el RPE es 9.5-10 o el VL > 35%, reconoce el agotamiento total de la serie; PROHIBIDO decir 'sin agotarte del todo'.
3. OBJETIVO LOGRADO: Traduce el tipo de adaptación conseguida con esta serie a palabras simples.
4. QUÉ HACER AHORA: Da la orden directa del motor para la siguiente serie (carga, corte de reps y prioridad técnica si la hay). Si sugieres bajar la carga, el peso sugerido DEBE ser numéricamente menor al levantado hoy ({carga_txt}).
5. VARIABILIDAD: Varía tu redacción respecto a turnos anteriores; prohibido repetir frases cliché o plantillas idénticas.

Usa ÚNICAMENTE los 1RMs y pesos que el sistema te proporciona en los bloques anteriores. Si un peso no aparece explícitamente en los datos del sistema, escribe exactamente 'peso por calibrar' y pregúntale al atleta.
"""
    return contexto_final
