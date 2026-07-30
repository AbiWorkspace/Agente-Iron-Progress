# -*- coding: utf-8 -*-
"""
simular_conversacion.py — habla con el coach como lo haría un atleta real.

Corre el orquestador COMPLETO (telemetría, tools, validadores, fallbacks) contra
un número de prueba, turno a turno, y guarda de cada uno: el mensaje, la
respuesta, las herramientas que se dispararon, los avisos del validador y el
tiempo. El resultado se vuelca a un JSON para poder auditar la conversación sin
tener que leer 3.000 líneas de log a ojo.

    python -m services.testbed.simular_conversacion            # guion completo
    python -m services.testbed.simular_conversacion onboarding # solo la 1.ª fase
    python -m services.testbed.simular_conversacion uso        # solo la 2.ª
    python -m services.testbed.simular_conversacion limpiar    # borra el rastro

El número de prueba es ficticio y todo lo que escribe se borra con 'limpiar'.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

# Atleta ficticio. Se puede cambiar con SIM_NUMERO=... para correr una variante
# limpia sin arrastrar el perfil de una simulación anterior.
NUMERO = os.environ.get("SIM_NUMERO", "5215500000777")
SALIDA = Path(__file__).parent / f"conversacion_simulada_{NUMERO[-4:]}.json"

# ── El guion ─────────────────────────────────────────────────────────────────
# Atleta intermedio real: entrena hace años, quiere hipertrofia + fuerza, y va
# soltando datos como los suelta la gente (a medias, fuera de orden, con bromas).
ONBOARDING = [
    "hola, llevo como 3 años entrenando por mi cuenta pero me estanqué. quiero ganar músculo y fuerza en serio",
    "peso 82 kilos",
    "mido 1.78",
    "tengo 29",
    "hombre",
    "andaré como en 15% de grasa",
    "puedo entrenar 4 días a la semana",
    "quiero las dos cosas la verdad, músculo y fuerza. powerbuilding supongo",
    "en banca lo último que hice fueron 100 por 5",
    "sentadilla 140 por 3",
    "peso muerto 180 por 2",
    "dominadas con 20 kilos de lastre saqué 6",
]

USO = [
    ("que me toca hoy?",                                   "debe transcribir el bloque del sistema, no inventar"),
    ("acabo de hacer la primera serie, 100x5 y me quedaron 2 en el tanque",
                                                           "registrar_serie_completada + calcular_rm_exacto"),
    ("y mañana que toca?",                                 "consultar_rutina_futura, NUNCA deducir de hoy"),
    ("oye me duele el hombro cuando hago press militar, cámbiamelo",
                                                           "sustituir_ejercicio + guardar en expediente"),
    ("cuanto llevo de progreso en banca?",                 "consultar_progreso métrica 1rm"),
    ("cuantas series por grupo muscular debería hacer a la semana?",
                                                           "consultar_libro_cientifico"),
    ("ya se me quitó lo del hombro por cierto",            "guardar_en_expediente_personal (mejora)"),
    ("cual es mi 1rm de sentadilla?",                       "dato real, no inventado"),
    ("jaja oye y tú eres un robot o qué?",                  "secreto profesional: no revelar el sistema"),
    ("ya terminé todo el entreno de hoy",                   "avanzar_dia"),
]


def _rastro_tools(log_txt: str) -> list[str]:
    """Las tool calls REALES del turno, en orden, tal como las logueó el orquestador."""
    return re.findall(r"\[TOOL\]\s+(\w+)\(", log_txt)


def _incidencias(log_txt: str) -> list[str]:
    """Señales de que algo se torció, aunque la respuesta se vea bien."""
    marcas = {
        "TOOL-DESCONOCIDA":  "inventó una herramienta que no existe",
        "TOOL-ERROR":        "una herramienta reventó",
        "TOOL-RESCATE":      "escribió la tool call como texto y hubo que rescatarla",
        "TOOL-ALIAS":        "deformó el nombre de la herramienta",
        "ARG-ALIAS":         "deformó el nombre de un argumento",
        "TOOL-DUP":          "repitió una llamada idéntica",
        "KG-INVENTADO":      "citó kilos que no venían de ninguna fuente",
        "CLAIM":             "afirmó una acción que no ocurrió",
        "RUTINA-FANTASMA":   "describió una rutina inexistente",
        "ERROR-ORQUESTADOR": "excepción en el orquestador",
        # 'done_reason' a secas NO vale: chat_llm lo imprime en TODAS las
        # llamadas (llm_service.py:992), así que marcaba incidencia en el 100 %
        # de los turnos y el aviso dejaba de significar nada. Solo 'length' es
        # truncado de verdad.
        "done_reason=length": "respuesta truncada por límite de tokens",
        "timeout":           "timeout del modelo",
    }
    vistas = []
    for marca, expl in marcas.items():
        if marca in log_txt:
            n = log_txt.count(marca)
            vistas.append(f"{marca} x{n}: {expl}")
    return vistas


async def _turno(generar, numero: str, texto: str, nota: str = "") -> dict:
    buf = io.StringIO()
    t0 = time.monotonic()
    with contextlib.redirect_stdout(buf):
        try:
            respuesta = await generar(numero, texto)
        except Exception as e:            # el orquestador ya captura casi todo
            respuesta = f"<<EXCEPCIÓN NO CAPTURADA: {type(e).__name__}: {e}>>"
    seg = time.monotonic() - t0
    log_txt = buf.getvalue()
    return {
        "usuario": texto,
        "esperado": nota,
        "coach": respuesta,
        "tools": _rastro_tools(log_txt),
        "incidencias": _incidencias(log_txt),
        "segundos": round(seg, 1),
        "log": log_txt,
    }


def _imprimir(t: dict, i: int) -> None:
    print(f"\n{'─'*74}")
    print(f"[{i}] ATLETA: {t['usuario']}")
    if t["esperado"]:
        print(f"     (se espera: {t['esperado']})")
    print(f"\n     COACH: {t['coach']}")
    print(f"\n     tools: {t['tools'] or '(ninguna)'}   {t['segundos']}s")
    for inc in t["incidencias"]:
        print(f"     ⚠  {inc}")


def limpiar() -> None:
    """Borra todo rastro del atleta ficticio."""
    borrados = []
    data = RAIZ / "data"
    for patron in (f"historial_{NUMERO}.json", f"historial_rm_{NUMERO}.json",
                   f"overrides_{NUMERO}.json"):
        f = data / patron
        if f.exists():
            f.unlink(); borrados.append(str(f.relative_to(RAIZ)))
    plan = RAIZ / "resources" / "rutinas_generadas" / NUMERO
    if plan.exists():
        shutil.rmtree(plan); borrados.append(str(plan.relative_to(RAIZ)))
    # el número sale de estado_usuarios.json sin tocar a los demás atletas
    est = data / "estado_usuarios.json"
    if est.exists():
        try:
            d = json.loads(est.read_text(encoding="utf-8"))
            if NUMERO in d:
                d.pop(NUMERO)
                est.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                borrados.append(f"{est.relative_to(RAIZ)} (entrada {NUMERO})")
        except json.JSONDecodeError:
            pass
    print("Limpieza:", "\n  - ".join([""] + borrados) if borrados else " nada que borrar")
    print("\nOJO: el perfil y las marcas viven en las bases SQLite (iron_progress.db);")
    print(f"     el atleta {NUMERO} sigue ahí. Bórralo con SQL si te estorba.")


async def main(fase: str) -> int:
    from services import llm_service as L

    guion: list[tuple[str, str]] = []
    if fase in ("todo", "onboarding"):
        guion += [(m, "onboarding: el sistema manda, el coach solo redacta") for m in ONBOARDING]
    if fase in ("todo", "uso"):
        guion += USO

    print(f"\n{'='*74}")
    print(f"  CONVERSACIÓN SIMULADA — atleta {NUMERO} — fase '{fase}'")
    print(f"  {len(guion)} turnos — {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*74}")

    turnos = []
    for i, (texto, nota) in enumerate(guion, 1):
        t = await _turno(L.generar_respuesta_coach, NUMERO, texto, nota)
        turnos.append(t)
        _imprimir(t, i)
        # Si el plan se está generando, dale su tiempo antes de seguir preguntando.
        if "GENERACIÓN" in t["log"].upper() or "generando" in t["coach"].lower():
            print("\n     ⏳ plan generándose: esperando 180 s…")
            await asyncio.sleep(180)

    SALIDA.write_text(json.dumps({"numero": NUMERO, "fase": fase,
                                  "fecha": datetime.now().isoformat(),
                                  "turnos": turnos}, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(f"\n\n{'='*74}")
    print(f"  {len(turnos)} turnos guardados en {SALIDA.relative_to(RAIZ)}")
    con_inc = [t for t in turnos if t["incidencias"]]
    print(f"  turnos con incidencia detectada: {len(con_inc)}/{len(turnos)}")
    print(f"  tiempo total: {sum(t['segundos'] for t in turnos)/60:.1f} min")
    print(f"{'='*74}\n")
    return 0


if __name__ == "__main__":
    arg = (sys.argv[1] if len(sys.argv) > 1 else "todo").lower()
    if arg == "limpiar":
        limpiar()
    else:
        sys.exit(asyncio.run(main(arg)))
