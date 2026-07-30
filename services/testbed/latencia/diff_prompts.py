#!/usr/bin/env python3
"""Compara los volcados de prompt de dos turnos consecutivos y señala el punto
exacto donde el prefijo deja de coincidir.

Contexto (2026-07-28): Ollama descarta el checkpoint por ~60 tokens
("checking checkpoint with [19847,19847] against 19787") y reprocesa los ~20.600
tokens enteros, ~26 s por turno. Hipótesis a confirmar: la poda de telemetría
reescribe la cola del prompt entre turnos.

Uso:
    IRON_DEBUG_PROMPT=1 python main.py 2>&1 | tee -a /tmp/iron.log
    # mandar 2-3 mensajes al bot, luego:
    python3 services/testbed/latencia/diff_prompts.py
"""
import difflib
import glob
import json
import os
import sys

DIR = "/tmp/iron_prompts"


def cargar(ruta):
    with open(ruta, encoding="utf-8") as fh:
        return json.load(fh)


def comparar(a, b):
    print(f"\n{'=' * 78}\n{os.path.basename(a['_ruta'])}  ->  {os.path.basename(b['_ruta'])}"
          f"\n{'=' * 78}")
    print(f"mensajes: {a['n_msgs']} -> {b['n_msgs']}   "
          f"chars: {a['chars_total']} -> {b['chars_total']}")

    ma, mb = a["mensajes"], b["mensajes"]

    # Primer índice donde las huellas dejan de coincidir: ahí muere el prefijo.
    comun = 0
    for x, y in zip(ma, mb):
        if x["sha1"] != y["sha1"]:
            break
        comun += 1

    chars_estables = sum(m["chars"] for m in ma[:comun])
    pct = 100 * chars_estables / max(a["chars_total"], 1)
    print(f"\nPrefijo idéntico: {comun}/{len(ma)} mensajes "
          f"= {chars_estables} chars ({pct:.1f}% del prompt)")

    if comun == len(ma):
        print("  -> El prefijo entero sobrevive; los mensajes viejos NO se reescriben.")
        return

    x = ma[comun]
    y = mb[comun] if comun < len(mb) else None
    print(f"\n*** PRIMER MENSAJE QUE CAMBIA: índice {comun}, rol '{x['role']}' ***")
    print(f"    chars {x['chars']} -> {y['chars'] if y else '(no existe)'}")
    if not y:
        return

    print(f"\n--- cabeza ANTES ---\n{x['cabeza']}")
    print(f"\n--- cabeza DESPUÉS ---\n{y['cabeza']}")
    if x["cola"] != y["cola"]:
        print(f"\n--- cola ANTES ---\n{x['cola']}")
        print(f"\n--- cola DESPUÉS ---\n{y['cola']}")

    # Cuántos mensajes posteriores también cambian: distingue "solo se reescribió
    # este" de "se reescribió todo lo que va detrás".
    posteriores = sum(1 for p, q in zip(ma[comun + 1:], mb[comun + 1:])
                      if p["sha1"] != q["sha1"])
    print(f"\nMensajes posteriores que también cambian: {posteriores}")


def main():
    # Argumento opcional: directorio a comparar. Sirve para aislar los turnos de
    # una versión del código (p. ej. antes/después de la Fase 2.3) sin que los
    # volcados viejos ensucien la comparación.
    directorio = sys.argv[1] if len(sys.argv) > 1 else DIR
    rutas = sorted(glob.glob(os.path.join(directorio, "*_principal.json")))
    if len(rutas) < 2:
        print(f"Hacen falta al menos 2 volcados de 'principal' en {directorio}; "
              f"hay {len(rutas)}.")
        print("¿Arrancaste el bot con IRON_DEBUG_PROMPT=1?")
        sys.exit(1)

    print(f"{len(rutas)} volcados de 'principal' encontrados. "
          f"Comparando cada turno con el siguiente.")
    dumps = []
    for r in rutas:
        d = cargar(r)
        d["_ruta"] = r
        dumps.append(d)

    for a, b in zip(dumps, dumps[1:]):
        comparar(a, b)

    # Diff textual de las colas de los dos últimos: si la telemetría es la
    # culpable, se ve aquí en crudo.
    print(f"\n{'=' * 78}\nDIFF DE LA COLA DEL PROMPT (últimos 3000 chars, "
          f"2 turnos finales)\n{'=' * 78}")
    d = difflib.unified_diff(
        dumps[-2]["cola_global"].splitlines(),
        dumps[-1]["cola_global"].splitlines(),
        fromfile=os.path.basename(dumps[-2]["_ruta"]),
        tofile=os.path.basename(dumps[-1]["_ruta"]),
        lineterm="", n=1)
    salida = [l for l in d]
    print("\n".join(salida[:120]) if salida else "(colas idénticas)")


if __name__ == "__main__":
    main()
