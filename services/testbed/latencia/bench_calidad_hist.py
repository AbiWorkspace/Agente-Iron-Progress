"""Mide si la telemetría acumulada en el historial (opción B, 2026-07-28) degrada
el acierto de tool call.

`bench_calidad.py` usa `hist_congelado.json`, que NO tiene telemetría persistida:
mide la condición VIEJA. Este script corre los mismos 8 casos contra un historial
elegible, para comparar las dos condiciones con el mismo system prompt:

    python3 bench_calidad_hist.py services/testbed/latencia/hist_congelado.json
    python3 bench_calidad_hist.py data/historial_527291395298.json

Por defecto solo think=True, que es el modo con el que producción decide
herramientas (los otros dos están medidos y descartados en ESTADO_PLAN_LATENCIA.md).
"""
import json, sys, time, urllib.request

URL = "http://127.0.0.1:11434/api/chat"
BASE = "/home/abimael/PycharmProjects/Agente-Iron-Progress"

SYS = json.load(open(f"{BASE}/core/prompt.json"))["promt"]
T = json.load(open(f"{BASE}/core/tools.json"))
T = [t if t.get("type") == "function" else {"type": "function", "function": t} for t in T]

# Mismos casos que bench_calidad.py: no cambiarlos o se pierde la comparabilidad.
CASOS = [
    ("ya termine mi sesion de hoy, todo completo",       {"avanzar_dia"}),
    ("que me toca entrenar manana?",                     {"consultar_rutina_futura"}),
    ("cambia el peso muerto por rumano en todo el plan", {"sustituir_ejercicio"}),
    ("hice 140x3 en sentadilla, cual seria mi 1rm?",     {"calcular_rm_exacto"}),
    ("me siento fatal, rpe 10 en la primera serie",      {"autorregular_dia", "aplicar_descarga"}),
    ("pausa mi plan que me voy de viaje dos semanas",    {"pausar_plan"}),
    ("mandame la grafica de mi progreso",                {"enviar_grafica_progreso"}),
    # OJO: este caso está MAL ESPECIFICADO en bench_calidad.py, que espera
    # `generar_rutina`. El system prompt ordena lo contrario ("Tú NO gestionas el
    # orden... NO invocas herramientas de generación") porque el flujo de creación
    # lo controla Python (`clasificar_intencion_plan`, `CREACION_FUERTE_RE`). El
    # modelo acierta al NO llamarla; era el test el que fallaba. Aquí el acierto es
    # no emitir ninguna herramienta de generación.
    ("hazme una rutina nueva desde cero",                 set()),
]

ruta_hist = sys.argv[1] if len(sys.argv) > 1 else f"{BASE}/services/testbed/latencia/hist_congelado.json"
think = sys.argv[2] if len(sys.argv) > 2 else "True"
think = {"true": True, "false": False, "low": "low"}[think.lower()]

H = json.load(open(ruta_hist, encoding="utf-8"))
con_tele = sum(1 for m in H
               if m.get("role") == "user" and "--- TELEMETRÍA ---" in (m.get("content") or ""))
ventana = H[-10:]
tele_ventana = sum(1 for m in ventana
                   if m.get("role") == "user" and "--- TELEMETRÍA ---" in (m.get("content") or ""))
chars = sum(len(m.get("content") or "") for m in ventana)

print(f"historial : {ruta_hist.split('/')[-1]}")
print(f"            {len(H)} msgs, {con_tele} con telemetría persistida")
print(f"ventana   : últimos 10 msgs, {chars} chars, {tele_ventana} con telemetría")
print(f"think     : {think}\n")

ok = 0
t_total = 0.0
for texto, esperadas in CASOS:
    msgs = [{"role": "system", "content": SYS}] + ventana + [{"role": "user", "content": texto}]
    body = {
        "model": "qwen3.6:27b", "messages": msgs, "stream": False, "keep_alive": -1,
        "think": think, "tools": T,
        "options": {"num_ctx": 32768, "num_predict": 3072 if think else 768,
                    "temperature": 0.6, "top_p": 0.95, "top_k": 20,
                    "repeat_penalty": 1.1, "presence_penalty": 0.0,
                    "min_p": 0.0, "num_gpu": 99},
    }
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=600).read())
    except Exception as e:
        print(f"  ERROR {str(e)[:70]} <- {texto!r}")
        continue
    w = time.monotonic() - t0
    t_total += w
    m = r.get("message", {})
    tc = {(x.get("function") or {}).get("name") for x in (m.get("tool_calls") or [])}
    # esperadas vacío = el acierto es NO llamar a generación (ver nota en CASOS).
    acierto = ("generar_rutina" not in tc) if not esperadas else bool(tc & esperadas)
    ok += int(acierto)
    n_think = len(m.get("thinking") or "") // 4
    print(f"  {'OK ' if acierto else 'NO '} {w:5.1f}s think={n_think:4d} tok | "
          f"{texto[:42]:42s} -> {sorted(tc) if tc else '(ninguna)'}")

print(f"\n==> aciertos {ok}/{len(CASOS)} | tiempo total {t_total:.1f}s "
      f"(media {t_total/len(CASOS):.1f}s)")
