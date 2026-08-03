"""bench_techo_thinking.py — ¿se puede acotar el razonamiento sin perder tool calls?

EL DATO QUE MOTIVA ESTO (resultado_calidad.txt, think=True):

    aciertos  ->  73, 122, 421, 532, 667, 1653 tok de thinking
    fallos    ->  2573, 2755 tok            (96,3 s y 82,6 s tirados)

Todos los aciertos caen por debajo de 1.653 tok; los dos que pasan de 2.500 fallan.
O sea que razonar de más no es que ayude poco: es el PREDICTOR del fallo. Un techo
por debajo de ~1.800 no debería costar ningún acierto y se ahorra ~60 s en los
casos que se desbocan.

Ollama no tiene un parámetro de "máximo de tokens de razonamiento", así que el
techo se implementa leyendo el stream y cortándolo a mano (verificado: `thinking`
llega token a token y cerrar la conexión aborta la generación).

ESTRATEGIAS
  A. baseline           think=True, num_predict 3072            <- lo que hay hoy
  B. techo + reintento  corta a N tok y relanza sin thinking
  C. techo + rescate    corta a N tok y relanza sin thinking PERO reinyectando el
                        razonamiento ya hecho (no tirar 1.800 tok de análisis)
  D. presupuesto        think=True + instrucción de brevedad en el prompt (empujón
                        blando, sin techo duro: el modelo puede ignorarlo)

Uso:  python3 bench_techo_thinking.py [techo_tokens] [repeticiones]
"""
import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:11434/api/chat"
BASE = "/home/abimael/PycharmProjects/Agente-Iron-Progress"
SCR = f"{BASE}/services/testbed/latencia"
SYS = json.load(open(f"{BASE}/core/prompt.json"))["promt"]
T = json.load(open(f"{BASE}/core/tools.json"))
T = [t if t.get("type") == "function" else {"type": "function", "function": t} for t in T]
H = json.load(open(f"{SCR}/hist_congelado.json"))

TECHO = int(sys.argv[1]) if len(sys.argv) > 1 else 1800
REPS = int(sys.argv[2]) if len(sys.argv) > 2 else 1
MODELO = "qwen3.6:27b"

# Los dos primeros son los que se desbocan (2.755 y 2.573 tok). Los dos últimos son
# CONTROL: si el techo los rompe, la idea no sirve. "ya termine mi sesion" es el más
# informativo de los dos porque acierta con 1.653 tok, justo por debajo del techo.
CASOS = [
    ("me siento fatal, rpe 10 en la primera serie", {"autorregular_dia", "aplicar_descarga"}, "SE DESBOCA"),
    ("hazme una rutina nueva desde cero",           set(),                                    "SE DESBOCA (no debe llamar nada)"),
    ("ya termine mi sesion de hoy, todo completo",  {"avanzar_dia"},                          "control, 1653 tok"),
    ("pausa mi plan que me voy de viaje dos semanas", {"pausar_plan"},                        "control, 73 tok"),
]

INSTRUCCION_PRESUPUESTO = (
    "[PRESUPUESTO DE RAZONAMIENTO]: piensa en 80 palabras COMO MÁXIMO antes de actuar. "
    "Decide la herramienta y emítela; no repases el historial entero ni enumeres "
    "alternativas que ya descartaste.")

OPCIONES = {"num_ctx": 32768, "temperature": 0.6, "top_p": 0.95, "top_k": 20,
            "repeat_penalty": 1.1, "presence_penalty": 0.0, "min_p": 0.0, "num_gpu": 99}


def _peticion(msgs, think, npred, stream, tools=True):
    cuerpo = {"model": MODELO, "messages": msgs, "stream": stream, "keep_alive": -1,
              "think": think, "options": {**OPCIONES, "num_predict": npred}}
    if tools:
        cuerpo["tools"] = T
    return urllib.request.Request(URL, data=json.dumps(cuerpo).encode(),
                                  headers={"Content-Type": "application/json"})


def _mensajes(texto, extra_sys=None):
    sistema = SYS + (("\n\n" + extra_sys) if extra_sys else "")
    return [{"role": "system", "content": sistema}] + H[-10:] + [{"role": "user", "content": texto}]


def _tools_de(msg):
    return {(x.get("function") or {}).get("name") for x in (msg.get("tool_calls") or [])}


def sin_techo(texto, think=True, extra_sys=None, npred=3072):
    """Una llamada normal, sin streaming. A y D."""
    r = json.loads(urllib.request.urlopen(
        _peticion(_mensajes(texto, extra_sys), think, npred, False), timeout=600).read())
    m = r.get("message", {}) or {}
    return {"think_tok": len((m.get("thinking") or "")) // 4,
            "contenido": len((m.get("content") or "")),
            "tools": _tools_de(m),
            "done": r.get("done_reason"),
            "cortado": False}


def con_techo(texto, techo=TECHO, rescatar=False):
    """Lee el stream y CORTA si el razonamiento se pasa del techo. B y C.

    Al cortar se relanza sin thinking. Con `rescatar`, el razonamiento parcial
    viaja en el prompt del reintento: son ~1.800 tokens de análisis del caso y
    tirarlos es justo lo que hace que el reintento actual (`chat_llm`, auto-rescate
    por truncamiento) responda a ciegas.
    """
    limite_chars = techo * 4
    pensado, contenido, cortado = [], [], False
    resp_final = None
    # CUIDADO: en streaming las tool_calls NO vienen en el chunk final, llegan en
    # uno intermedio. Leerlas solo del último (el que trae done=True) daba
    # "(ninguna)" incluso en casos que el baseline resolvía bien: los controles
    # fallaban con el techo SIN HABERSE ACTIVADO, que es lo que delató el bug.
    tools_vistas: set = set()

    stream = urllib.request.urlopen(_peticion(_mensajes(texto), True, 3072, True), timeout=600)
    try:
        for linea in stream:
            if not linea.strip():
                continue
            d = json.loads(linea)
            m = d.get("message", {}) or {}
            pensado.append(m.get("thinking") or "")
            contenido.append(m.get("content") or "")
            tools_vistas |= _tools_de(m)
            if d.get("done"):
                # Terminó por su cuenta sin pasarse: no hay nada que cortar.
                resp_final = {"think_tok": len("".join(pensado)) // 4,
                              "contenido": len("".join(contenido)),
                              "tools": tools_vistas,
                              "done": d.get("done_reason"),
                              "cortado": False}
                break
            # Solo se corta MIENTRAS razona: si ya empezó a escribir contenido o a
            # emitir la tool call, cortar ahí destruiría una respuesta buena.
            if (not tools_vistas and len("".join(contenido)) == 0
                    and len("".join(pensado)) > limite_chars):
                cortado = True
                break
    finally:
        stream.close()

    if not cortado:
        return resp_final or {"think_tok": len("".join(pensado)) // 4,
                              "contenido": len("".join(contenido)),
                              "tools": tools_vistas, "done": "?", "cortado": False}

    razonamiento = "".join(pensado)
    msgs = _mensajes(texto)
    if rescatar:
        msgs.append({"role": "user", "content":
                     "[RAZONAMIENTO YA HECHO — no lo repitas, concluye desde aquí]:\n"
                     + razonamiento.strip()
                     + "\n\n[SISTEMA]: se te acabó el tiempo de análisis. DECIDE YA: "
                       "emite la herramienta que corresponda o responde en corto. "
                       "No sigas razonando."})
    r = json.loads(urllib.request.urlopen(
        _peticion(msgs, False, 1536, False), timeout=600).read())
    m = r.get("message", {}) or {}
    return {"think_tok": len(razonamiento) // 4,
            "contenido": len((m.get("content") or "")),
            "tools": _tools_de(m),
            "done": r.get("done_reason"),
            "cortado": True}


ESTRATEGIAS = [
    ("A. baseline (hoy)",   lambda t: sin_techo(t)),
    (f"B. techo {TECHO}",   lambda t: con_techo(t, rescatar=False)),
    (f"C. techo+rescate",   lambda t: con_techo(t, rescatar=True)),
    ("D. presupuesto prompt", lambda t: sin_techo(t, extra_sys=INSTRUCCION_PRESUPUESTO)),
]


def main() -> int:
    print(f"=== TECHO DEL THINKING (techo={TECHO} tok, {REPS} rep) ===\n")
    acumulado = {n: {"ok": 0, "t": 0.0, "think": 0, "cortes": 0, "n": 0}
                 for n, _ in ESTRATEGIAS}

    for texto, esperadas, nota in CASOS:
        print(f"--- {texto!r}  [{nota}]")
        print(f"    esperado: {'|'.join(sorted(esperadas)) or '(ninguna herramienta)'}")
        for nombre, fn in ESTRATEGIAS:
            for rep in range(REPS):
                t0 = time.monotonic()
                try:
                    r = fn(texto)
                except Exception as e:
                    print(f"    {nombre:22s} ERROR {type(e).__name__}: {str(e)[:70]}")
                    continue
                w = time.monotonic() - t0
                # Sin herramientas esperadas, acertar = NO llamar ninguna.
                acierto = bool(r["tools"] & esperadas) if esperadas else not r["tools"]
                a = acumulado[nombre]
                a["ok"] += int(acierto); a["t"] += w; a["think"] += r["think_tok"]
                a["cortes"] += int(r["cortado"]); a["n"] += 1
                print(f"    {nombre:22s} {'OK ' if acierto else 'NO '} {w:5.1f}s "
                      f"think={r['think_tok']:4d} tok{' CORTADO' if r['cortado'] else '        '} "
                      f"cont={r['contenido']:4d}ch done={r['done']:8s} "
                      f"-> {sorted(r['tools']) or '(ninguna)'}")
        print()

    print("\n================ RESUMEN ================")
    for nombre, _ in ESTRATEGIAS:
        a = acumulado[nombre]
        if not a["n"]:
            continue
        print(f"{nombre:22s} aciertos {a['ok']}/{a['n']} | "
              f"media {a['t'] / a['n']:5.1f}s | think medio {a['think'] // a['n']:4d} tok | "
              f"cortes {a['cortes']}")
    print("\nOJO: ±1 acierto es RUIDO con temperature 0.6 (ver bench-calidad-tiene-ruido).")
    print("La latencia es la señal fiable con pocas repeticiones; para calidad, repetir.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
