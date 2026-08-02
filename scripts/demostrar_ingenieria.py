# -*- coding: utf-8 -*-
"""
demostrar_ingenieria.py — Escena 9 del guion: "esto es ingeniería, no un prompt".

Para qué existe
----------------
docs/PLAN_DEMOSTRACION.md pide mostrar, EN PANTALLA DE CÓDIGO (no WhatsApp), tres
cosas: las seis redes de validación de salida, el banco de QA (74 casos, 22
bloques) + 32 suites deterministas + 72 aserciones de humo, y que ningún nombre
de función o tabla llega nunca al chat ("secreto profesional").

El comando que trae la documentación,

    SIM_NUMERO=5215500000777 python -m services.testbed.qa_casos_uso fatiga salud

apunta a un atleta FANTASMA: 5215500000777 no tiene perfil, marcas ni plan en
este repo, así que el banco de QA se estrellaría contra el onboarding en vez de
ejercitar las reglas de fatiga y salud que se quieren enseñar. Este script:

  1. señala en el código dónde vive cada una de las seis redes (grep en vivo,
     no números fijos que se puedan desactualizar),
  2. corre el smoke real (services/smoke_v8.py) y cuenta las suites deterministas,
  3. corre el banco de QA de verdad, con un atleta que SÍ está sembrado
     (por defecto el mismo que preparaste con sembrar_demo.py /
     sembrar_conversacion.py — ver scripts/verificar_demo.py),
  4. revisa las respuestas del coach en busca de fugas de jerga técnica y
     reporta si el "secreto profesional" se sostuvo.

Uso
---
    python3 scripts/demostrar_ingenieria.py                       # bloques fatiga salud
    python3 scripts/demostrar_ingenieria.py --numero 527291395298 rutina progreso
    python3 scripts/demostrar_ingenieria.py --solo-redes           # solo el paso 1, sin LLM
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
LLM_SERVICE = RAIZ / "services" / "llm_service.py"
TESTBED = RAIZ / "services" / "testbed"
QA_DIR = TESTBED / "qa"

NUMERO_POR_DEFECTO = "527291395298"   # el atleta que ya está sembrado y verificado

VERDE, ROJO, AMARILLO, GRIS, NEGRITA, FIN = (
    "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[1m", "\033[0m")

# Las seis redes, en el orden en que corren dentro del turno.
REDES = [
    ("KG-INVENTADOS", "Validador de kilajes",
     "todo peso que aparece en la respuesta debe existir en alguna fuente real "
     "(mensaje, rutina, dashboard, tools, historial, marcas); si no, reescribe honesto."),
    ("RUTINA-FANTASMA", "Anti-rutina-fantasma",
     "si el plan real aún no existe, prohíbe que la respuesta tenga forma de rutina inventada."),
    ("ONB-REPARADO", "Reparador de onboarding",
     "durante el alta de datos, la respuesta no se puede salir del guion de la ficha."),
    ("CLAIM-FALSO", "Guardia claim-falso",
     "si el borrador afirma haber guardado/registrado algo que NO se ejecutó, se reescribe "
     "o cae a una plantilla honesta determinista (sin LLM, sin bucle)."),
    ("CLAIM-ESTADO", "Guardia de estado del plan",
     "contrasta contra disco lo que el coach dice sobre descargas/pausas/reanudaciones: "
     "nunca puede dar por aplicado un cambio que no ocurrió."),
    ("DUPLICADO", "Anti-eco",
     "si la respuesta sale idéntica a la anterior, se le añade variación antes de enviarla."),
]

# Jerga que <secreto_profesional> prohíbe que llegue al chat.
JERGA_PROHIBIDA = re.compile(
    r"\bherramient|\bfunci[oó]n\b|\bJSON\b|\bbase de datos\b|\btabla\b|\bSQL\b|"
    r"\btelemetr[ií]a\b|\bsistema\b|\bmodelo de lenguaje\b|\b(?<!yo )IA\b|\bChroma\b|"
    r"\bdispatcher\b|\bhandler\b|\borquestador\b",
    re.IGNORECASE)


def _titulo(txt: str) -> None:
    print(f"\n{NEGRITA}{'═' * 78}{FIN}")
    print(f"{NEGRITA}  {txt}{FIN}")
    print(f"{NEGRITA}{'═' * 78}{FIN}")


def paso_1_redes() -> None:
    _titulo("1/4 — Las seis redes de validación (en el código, no en WhatsApp)")
    fuente = LLM_SERVICE.read_text(encoding="utf-8")
    lineas = fuente.splitlines()
    for tag, nombre, descripcion in REDES:
        patron = f'"{tag}"'
        num = next((i + 1 for i, l in enumerate(lineas) if patron in l), None)
        ubic = f"{LLM_SERVICE.relative_to(RAIZ)}:{num}" if num else "(no encontrada — ¿se movió?)"
        print(f"\n  {VERDE}●{FIN} {NEGRITA}{nombre}{FIN}  {GRIS}[{tag}]{FIN}")
        print(f"    {descripcion}")
        print(f"    {GRIS}{ubic}{FIN}")
    print(f"\n  {GRIS}Ábrelas en el editor en ese orden mientras hablas: son secuenciales,"
          f" cada una puede reescribir lo que dejó la anterior.{FIN}")


def paso_2_smoke_y_suites() -> tuple[int, int, int]:
    _titulo("2/4 — Smoke determinista + suites")
    r = subprocess.run([sys.executable, str(TESTBED.parent / "smoke_v8.py")],
                       capture_output=True, text=True, cwd=RAIZ)
    m = re.search(r"RESULTADO:\s*(\d+)\s*OK,\s*(\d+)\s*FALLAS", r.stdout)
    ok, fallas = (int(m.group(1)), int(m.group(2))) if m else (0, -1)
    color = VERDE if fallas == 0 else ROJO
    print(f"  {color}smoke_v8.py: {ok} OK, {fallas} FALLAS{FIN}")

    suites = sorted(TESTBED.glob("test_*.py"))
    print(f"  {VERDE}{len(suites)} suites deterministas{FIN} en services/testbed/ "
          f"({GRIS}pytest services/testbed/{FIN})")
    return ok, fallas, len(suites)


def paso_3_qa(numero: str, bloques: list[str]) -> Path | None:
    _titulo(f"3/4 — Banco de QA en vivo — atleta {numero} — bloques: {', '.join(bloques)}")
    print(f"  {GRIS}Habla con el orquestador COMPLETO (modelo real, 27B local). "
          f"Cada caso tarda 10-60 s.{FIN}\n")

    antes = set(QA_DIR.glob("*.json")) if QA_DIR.exists() else set()
    env = {"SIM_NUMERO": numero}
    import os
    proceso = subprocess.run(
        [sys.executable, "-m", "services.testbed.qa_casos_uso", *bloques],
        cwd=RAIZ, env={**os.environ, **env})
    if proceso.returncode not in (0, 1):
        print(f"  {ROJO}el banco de QA no pudo correr (código {proceso.returncode}){FIN}")
        return None

    despues = set(QA_DIR.glob("*.json")) if QA_DIR.exists() else set()
    nuevos = despues - antes
    if not nuevos:
        print(f"  {AMARILLO}no se encontró el JSON de salida recién escrito{FIN}")
        return None
    return max(nuevos, key=lambda p: p.stat().st_mtime)


def paso_4_secreto_profesional(ruta_json: Path | None) -> None:
    _titulo("4/4 — El secreto profesional: ¿se le escapó jerga técnica al atleta?")
    if not ruta_json:
        print(f"  {AMARILLO}sin resultados del paso 3 que revisar{FIN}")
        return
    datos = json.loads(ruta_json.read_text(encoding="utf-8"))
    fugas = []
    for caso in datos.get("casos", []):
        coincidencias = JERGA_PROHIBIDA.findall(caso.get("coach", "") or "")
        if coincidencias:
            fugas.append((caso["usuario"], sorted(set(coincidencias))))
    if not fugas:
        print(f"  {VERDE}✓ ninguna respuesta del coach mencionó herramientas, tablas, "
              f"JSON, el sistema ni el modelo{FIN}")
    else:
        print(f"  {ROJO}✗ {len(fugas)} respuesta(s) dejaron escapar jerga:{FIN}")
        for msg, terms in fugas:
            print(f"    · {msg!r} → {terms}")
    print(f"\n  {GRIS}detalle completo: {ruta_json.relative_to(RAIZ)}{FIN}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("bloques", nargs="*", default=["fatiga", "salud"],
                    help="bloques del banco de QA a correr (default: fatiga salud)")
    ap.add_argument("--numero", default=NUMERO_POR_DEFECTO,
                    help=f"atleta YA sembrado a usar (default: {NUMERO_POR_DEFECTO})")
    ap.add_argument("--solo-redes", action="store_true",
                    help="solo el paso 1 (código), sin tocar el modelo")
    args = ap.parse_args()

    print(f"\n{NEGRITA}ESCENA 9 — Lo que hace que esto sea ingeniería y no un prompt{FIN}")

    paso_1_redes()
    if args.solo_redes:
        return 0

    paso_2_smoke_y_suites()
    ruta = paso_3_qa(args.numero, args.bloques)
    paso_4_secreto_profesional(ruta)

    print(f"\n{NEGRITA}{'═' * 78}{FIN}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
