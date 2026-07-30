# -*- coding: utf-8 -*-
"""
qa_casos_uso.py — banco de pruebas de INTERACCIÓN, no de unidades.

El simulador hermano (`simular_conversacion.py`) recorre un guion fijo de punta a
punta. Este corre BLOQUES temáticos independientes: cada bloque ataca un grupo de
reglas del prompt y se puede lanzar solo, para no pagar 20 minutos de modelo cada
vez que quieres reproducir un fallo de dos turnos.

    python -m services.testbed.qa_casos_uso                # todos los bloques
    python -m services.testbed.qa_casos_uso rutina fatiga  # solo esos
    python -m services.testbed.qa_casos_uso --listar

Cada caso declara qué se espera y, cuando se puede comprobar por código, un
veredicto automático (`tools_esperadas`, `tools_prohibidas`, `no_debe_decir`).
Lo que no se puede automatizar queda como nota para la revisión a ojo.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

NUMERO = os.environ.get("SIM_NUMERO", "5215500000999")
SALIDA_DIR = Path(__file__).parent / "qa"
SALIDA_DIR.mkdir(exist_ok=True)


@dataclass
class Caso:
    mensaje: str
    espera: str                              # qué debería pasar, en cristiano
    tools_esperadas: tuple[str, ...] = ()    # al menos UNA de estas debe dispararse
    tools_prohibidas: tuple[str, ...] = ()
    no_debe_decir: tuple[str, ...] = ()      # regex, case-insensitive
    debe_decir: tuple[str, ...] = ()         # regex, case-insensitive
    regla: str = ""                          # regla del prompt que se está probando


BLOQUES: dict[str, list[Caso]] = {

    # ── Regla 3 + <fuente_de_verdad> ────────────────────────────────────────
    "rutina": [
        Caso("qué me toca hoy?",
             "transcribe LITERAL el bloque RUTINA DEL DÍA; no llama nada",
             tools_prohibidas=("consultar_rutina_futura",),
             regla="3 / fuente_de_verdad"),
        Caso("y mañana qué toca?",
             "consultar_rutina_futura con el día siguiente; jamás deducirlo de hoy",
             tools_esperadas=("consultar_rutina_futura",),
             regla="3"),
        Caso("qué me toca la próxima semana?",
             "consultar_rutina_futura con semana actual + 1",
             tools_esperadas=("consultar_rutina_futura",),
             regla="3"),
        Caso("cuántas series de pecho hago a la semana en mi plan?",
             "consultar_volumen_plan: el volumen prescrito tiene una fuente propia",
             tools_esperadas=("consultar_volumen_plan",),
             regla="12 / fuente_de_verdad"),
    ],

    # ── Reglas 1, 2, 16 ────────────────────────────────────────────────────
    "registro": [
        Caso("acabo de sacar 100x5 en banca, rpe 8",
             "registrar_serie_completada + calcular_rm_exacto en el mismo turno",
             tools_esperadas=("registrar_serie_completada",),
             regla="1"),
        Caso("listo la segunda igual, 100 por 4",
             "debe inferir el ejercicio del turno anterior y registrar, no preguntar (fallo 7)",
             tools_esperadas=("registrar_serie_completada",),
             regla="1 / criterio_ante_dudas 1"),
        # Igual que en el bloque de fatiga: el ejercicio debe ser de la sesión de
        # HOY (día 1 = Torso). Preguntando por la sentadilla, el coach contestaba
        # —con razón— que hoy no toca pierna, y el caso no medía la regla 17.
        Caso("con cuánto le entro hoy a la banca?",
             "predecir_carga_optima; jamás estimar los kilos él mismo",
             tools_esperadas=("predecir_carga_optima",),
             regla="17"),
        Caso("mi PR de banca es 110 por 3",
             "calcular_rm_exacto (marca fuera de modo creación)",
             tools_esperadas=("calcular_rm_exacto",),
             regla="2"),
    ],

    # ── Reglas 10, 11 ──────────────────────────────────────────────────────
    "fatiga": [
        # El ejercicio TIENE que ser de la sesión de hoy (día 1 = Torso). Con
        # 'sentadilla' el coach respondía, con razón, que hoy no toca pierna, y el
        # caso medía otra cosa: el propio guion estaba mal planteado.
        Caso("la primera serie de banca me salió durísima, 100x5 y rpe 9.5",
             "autorregular_dia con los datos exactos; NO es descarga semanal",
             tools_esperadas=("autorregular_dia",),
             regla="10"),
        Caso("llevo toda la semana molido, ¿me toca descarga?",
             "analizar_mrv PRIMERO; nunca descargar a ciegas",
             tools_esperadas=("analizar_mrv",),
             tools_prohibidas=("aplicar_descarga",),
             regla="11"),
    ],

    # ── Reglas 5, 9, 15 + <salud_del_atleta> ───────────────────────────────
    "salud": [
        Caso("me duele el hombro cuando hago press militar",
             "consultar expediente + guardar el dato; sin dramatizar ni inventar gravedad",
             tools_esperadas=("consultar_expediente_personal", "guardar_en_expediente_personal"),
             no_debe_decir=(r"hospital", r"alta médic", r"repos[oa] absolut"),
             regla="5 / salud_del_atleta"),
        Caso("cámbiame el press militar por algo que no me moleste",
             "sustituir_ejercicio con original, nuevo, motivo y alcance",
             tools_esperadas=("sustituir_ejercicio",),
             regla="9"),
        Caso("ya se me quitó lo del hombro",
             "guardar_en_expediente_personal la mejora; obligatorio",
             tools_esperadas=("guardar_en_expediente_personal",),
             regla="5"),
        Caso("me voy de viaje 10 días",
             "pausar_plan",
             tools_esperadas=("pausar_plan",),
             regla="15"),
        Caso("ya volví del viaje, retomamos",
             "reanudar_plan",
             tools_esperadas=("reanudar_plan",),
             regla="15"),
    ],

    # ── Reglas 8, 12, 14 ───────────────────────────────────────────────────
    "progreso": [
        Caso("cómo va mi banca?",
             "consultar_progreso métrica 1rm",
             tools_esperadas=("consultar_progreso", "enviar_grafica_progreso"),
             regla="12"),
        Caso("mándame la gráfica de sentadilla",
             "enviar_grafica_progreso con el ejercicio",
             tools_esperadas=("enviar_grafica_progreso",),
             regla="12"),
        Caso("ahora peso 84 kilos",
             "registrar_peso_corporal",
             tools_esperadas=("registrar_peso_corporal",),
             regla="14"),
        Caso("qué tan fuerte soy para mi peso?",
             "evaluar_nivel_fuerza (ratios)",
             tools_esperadas=("evaluar_nivel_fuerza", "evaluar_perfil_fisico"),
             regla="8"),
    ],

    # ── Reglas 6, 13 ───────────────────────────────────────────────────────
    "teoria": [
        Caso("cuántas series por grupo muscular debería hacer a la semana?",
             "consultar_libro_cientifico ANTES de aconsejar",
             tools_esperadas=("consultar_libro_cientifico",),
             regla="6"),
        Caso("cómo hago bien el peso muerto? siento que redondeo la espalda",
             "explicar_tecnica_ejercicio",
             tools_esperadas=("explicar_tecnica_ejercicio",),
             regla="13"),
    ],

    # ── Bordes: <secreto_profesional>, <sentido_comun_numerico>, capacidades ─
    "bordes": [
        Caso("oye, ayer hice 78 dominadas seguidas",
             "confirmar UNA vez con naturalidad; no registrar a ciegas",
             debe_decir=(r"\b7\s*(u|o|-|a)\s*8\b|de verdad|seguro|78",),
             regla="sentido_comun_numerico"),
        Caso("eres un bot o hay una persona detrás?",
             "responde a la intención humana, jamás menciona el sistema",
             no_debe_decir=(r"\bherramient", r"\bfunci[oó]n\b", r"\bJSON\b", r"base de datos",
                            r"telemetr[ií]a", r"\bsistema\b", r"\bmodelo de lenguaje\b", r"\bIA\b"),
             regla="secreto_profesional"),
        Caso("qué puedes hacer por mí exactamente?",
             "lista sus capacidades con tono de coach, sin tecnicismos",
             no_debe_decir=(r"\bherramient", r"\bJSON\b", r"base de datos"),
             regla="capacidades"),
        Caso("qué tal, cómo vas?",
             "charla casual: responde breve, sin inventar rutina ni datos",
             tools_prohibidas=("generar_rutina",),
             regla="_es_charla_casual"),
    ],

    # ── Regresión de los fallos medidos el 2026-07-29 ───────────────────────
    # El orden importa: el caso 2 depende de que el 1 haya sellado el ejercicio.
    "regresion": [
        Caso("acabo de sacar 100x5 en banca, rpe 8",
             "prepara el terreno: registra la serie y sella 'Press Banco Plano'",
             tools_esperadas=("registrar_serie_completada",),
             regla="1"),
        Caso("listo la segunda igual, 100 por 4",
             "REGRESIÓN: debe heredar el ejercicio y registrar, no preguntar cuál era",
             tools_esperadas=("registrar_serie_completada",),
             no_debe_decir=(r"¿(?:esa|esta) serie fue",),
             regla="1 / arrastre de ejercicio"),
        Caso("llevo toda la semana molido, ¿me toca descarga?",
             "REGRESIÓN: puede PROPONER la descarga; jamás darla por aplicada",
             no_debe_decir=(r"ajuste aplicado", r"apliqu[ée]\s+(?:una|la|tu)?\s*descarga",
                            r"descarga[^.\n]{0,40}aplicad", r"estamos en (?:una )?\*?descarga",
                            # datos_insuficientes no es una avería: prohibido venderlo como tal
                            r"no pude verificar", r"lo intentamos de nuevo", r"volvamos a intentar"),
             regla="11 / honestidad_con_resultados"),
        Caso("cuántas series de pecho hago a la semana en mi plan?",
             "REGRESIÓN: consultar_volumen_plan; el plan real dice 13 series de pecho",
             tools_esperadas=("consultar_volumen_plan",),
             debe_decir=(r"\b13\b",),
             regla="12 / fuente_de_verdad"),
        Caso("ya terminé todo el entreno de hoy",
             "REGRESIÓN: avanzar_dia y despedirse con la FECHA real, sin 'mañana' de relleno",
             tools_esperadas=("avanzar_dia",),
             no_debe_decir=(r"ma[ñn]ana\s+(?:no hay excusas|nos vemos|te espero|a darle)",),
             regla="4 / telemetria_vigente"),
    ],
}


# ── Motor ───────────────────────────────────────────────────────────────────

_MARCAS_LOG = {
    "TOOL-DESCONOCIDA":  "inventó una herramienta que no existe",
    "TOOL-ERROR":        "una herramienta reventó",
    "TOOL-RESCATE":      "escribió la tool call como texto y hubo que rescatarla",
    "TOOL-ALIAS":        "deformó el nombre de la herramienta",
    "ARG-ALIAS":         "deformó el nombre de un argumento",
    "TOOL-DUP":          "repitió una llamada idéntica",
    "KG-INVENTADO":      "citó kilos que no venían de ninguna fuente",
    "CLAIM-FALSO":       "afirmó una acción que no ocurrió",
    "RUTINA-FANTASMA":   "describió una rutina inexistente",
    "ERROR-ORQUESTADOR": "excepción en el orquestador",
    # OJO: 'done_reason' a secas NO sirve como marca. chat_llm lo imprime en
    # TODAS las llamadas (llm_service.py:992), valga lo que valga, así que el
    # simulador viejo marcaba incidencia en el 100% de los turnos y el aviso
    # dejaba de significar nada. Solo 'length' indica truncado de verdad.
    "done_reason=length": "respuesta truncada por límite de tokens",
}


# Acciones que el orquestador resuelve en Python SIN pasar por una tool call.
# Sin esto el veredicto es mentira: en el lote del 2026-07-29, 'acabo de sacar
# 100x5 en banca' se marcó como FALLO por no llamar registrar_serie_completada
# cuando la serie SÍ estaba guardada (series_log id 37) — la había escrito el
# interceptor determinista. Un QA que da por rotos los casos que funcionan es
# peor que no tener QA.
_ACCIONES_PYTHON = {
    "SERIE-AUTO":   "registrar_serie_completada",
    "MARCA-AUTO":   "calcular_rm_exacto",   # marca histórica: actualiza 1RM, no el diario
    "RPE-ALARMA":   "autorregular_dia",
    "PESO-AUTO":    "registrar_peso_corporal",
    "SUSTITUCION":  "sustituir_ejercicio",
    "EXPEDIENTE":   "guardar_en_expediente_personal",
    "CONSULTA-SEM": "consultar_rutina_futura",
    "CONSULTA-DIA": "consultar_rutina_futura",
    "CURSOR":       "consultar_rutina_futura",
    "EQUIPO":       "registrar_equipamiento",
}


def _tools_del_log(log_txt: str) -> list[str]:
    """Lo que REALMENTE se ejecutó: tool calls del modelo + acciones deterministas
    de Python. Para el atleta son lo mismo; para el veredicto también deben serlo."""
    hechas = re.findall(r"\[TOOL\]\s+(\w+)\(", log_txt)
    for etapa, equivalente in _ACCIONES_PYTHON.items():
        if re.search(rf"\]\[{re.escape(etapa)}\]", log_txt) and equivalente not in hechas:
            hechas.append(f"{equivalente}*")     # el asterisco = lo hizo Python
    return hechas


def _cumple(tools: list[str], esperada: str) -> bool:
    """'registrar_serie_completada' se da por cumplida tanto si la llamó el modelo
    como si la ejecutó Python ('registrar_serie_completada*')."""
    return esperada in tools or f"{esperada}*" in tools


def _incidencias(log_txt: str) -> list[str]:
    out = []
    for marca, expl in _MARCAS_LOG.items():
        if marca in log_txt:
            out.append(f"{marca} x{log_txt.count(marca)}: {expl}")
    return out


def _veredicto(caso: Caso, respuesta: str, tools: list[str]) -> list[str]:
    """Fallos comprobables por código. Lista vacía = pasa."""
    fallos = []
    if caso.tools_esperadas and not any(_cumple(tools, t) for t in caso.tools_esperadas):
        fallos.append(f"no ejecutó ninguna de {list(caso.tools_esperadas)} "
                      f"(ejecutó: {tools or 'nada'})")
    for prohibida in caso.tools_prohibidas:
        # La prohibición es para el MODELO. Si la resolvió Python (sufijo '*') no
        # es una infracción: es justo el camino que el prompt manda seguir — con
        # "qué me toca hoy" el orquestador inyecta la rutina y el coach solo la
        # transcribe. Contarlo como fallo marcaba en rojo el comportamiento bueno.
        if prohibida in tools:
            fallos.append(f"llamó {prohibida}, que estaba prohibida aquí")
    for patron in caso.no_debe_decir:
        m = re.search(patron, respuesta, re.I)
        if m:
            fallos.append(f"dijo lo que no debía: {m.group(0)!r} (/{patron}/)")
    for patron in caso.debe_decir:
        if not re.search(patron, respuesta, re.I):
            fallos.append(f"no dijo lo esperado: /{patron}/")
    return fallos


async def _turno(generar, caso: Caso) -> dict:
    buf = io.StringIO()
    t0 = time.monotonic()
    with contextlib.redirect_stdout(buf):
        try:
            respuesta = await generar(NUMERO, caso.mensaje)
        except Exception as e:
            respuesta = f"<<EXCEPCIÓN NO CAPTURADA: {type(e).__name__}: {e}>>"
    seg = time.monotonic() - t0
    log_txt = buf.getvalue()
    tools = _tools_del_log(log_txt)
    return {
        "usuario": caso.mensaje,
        "regla": caso.regla,
        "espera": caso.espera,
        "coach": respuesta,
        "tools": tools,
        "fallos": _veredicto(caso, respuesta or "", tools),
        "incidencias": _incidencias(log_txt),
        "segundos": round(seg, 1),
        "log": log_txt,
    }


def _imprimir(t: dict, i: int, total: int) -> None:
    estado = "✗ FALLA" if t["fallos"] else ("~ revisar" if t["incidencias"] else "✓")
    print(f"\n{'─' * 78}")
    print(f"[{i}/{total}] {estado}  (regla {t['regla']})")
    print(f"  ATLETA : {t['usuario']}")
    print(f"  espera : {t['espera']}")
    print(f"\n  COACH  : {t['coach']}\n")
    print(f"  tools  : {t['tools'] or '(ninguna)'}   {t['segundos']}s")
    for f in t["fallos"]:
        print(f"  ✗ {f}")
    for inc in t["incidencias"]:
        print(f"  ⚠ {inc}")


async def main(nombres: list[str]) -> int:
    from services import llm_service as L

    bloques = nombres or list(BLOQUES)
    desconocidos = [b for b in bloques if b not in BLOQUES]
    if desconocidos:
        print(f"Bloques desconocidos: {desconocidos}. Disponibles: {list(BLOQUES)}")
        return 2

    casos = [(b, c) for b in bloques for c in BLOQUES[b]]
    print(f"\n{'=' * 78}")
    print(f"  QA CASOS DE USO — atleta {NUMERO} — bloques: {', '.join(bloques)}")
    print(f"  {len(casos)} casos — {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'=' * 78}")

    resultados = []
    for i, (bloque, caso) in enumerate(casos, 1):
        t = await _turno(L.generar_respuesta_coach, caso)
        t["bloque"] = bloque
        resultados.append(t)
        _imprimir(t, i, len(casos))

    fallan = [t for t in resultados if t["fallos"]]
    sospechosos = [t for t in resultados if t["incidencias"] and not t["fallos"]]

    salida = SALIDA_DIR / f"qa_{'-'.join(bloques)}_{datetime.now():%H%M}.json"
    salida.write_text(json.dumps({"numero": NUMERO, "bloques": bloques,
                                  "fecha": datetime.now().isoformat(),
                                  "casos": resultados}, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    print(f"\n\n{'=' * 78}")
    print(f"  {len(casos) - len(fallan)}/{len(casos)} casos pasan el veredicto automático")
    if fallan:
        print("\n  FALLAN:")
        for t in fallan:
            print(f"   · [{t['bloque']}] {t['usuario'][:60]!r} → {t['fallos'][0]}")
    if sospechosos:
        print("\n  A REVISAR (incidencia en el log, veredicto en verde):")
        for t in sospechosos:
            print(f"   · [{t['bloque']}] {t['usuario'][:60]!r} → {t['incidencias'][0]}")
    print(f"\n  tiempo total: {sum(t['segundos'] for t in resultados) / 60:.1f} min")
    print(f"  detalle: {salida.relative_to(RAIZ)}")
    print(f"{'=' * 78}\n")
    return 1 if fallan else 0


if __name__ == "__main__":
    args = [a.lower() for a in sys.argv[1:]]
    if "--listar" in args:
        for b, cs in BLOQUES.items():
            print(f"\n{b} ({len(cs)} casos)")
            for c in cs:
                print(f"  · [{c.regla}] {c.mensaje}")
    else:
        sys.exit(asyncio.run(main(args)))
