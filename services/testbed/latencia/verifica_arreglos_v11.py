"""Verifica los arreglos de calidad del 2026-07-28 (v11) repitiendo cada caso.

Una sola tirada no distingue señal de ruido (temperature 0.6), así que cada caso
se repite N veces. Línea base a batir, de `resultado_calidad.txt` + las corridas
de esa misma tarde:

  - "me siento fatal, rpe 10"  -> autorregular_dia : 0 aciertos SIEMPRE
    (la herramienta no aparecía ni una vez en el system prompt; se le añadió la
    regla 10 a la guía de decisión)
  - "que me toca entrenar manana?" -> consultar_rutina_futura : ~2/5 (inestable)

    python3 verifica_arreglos_v11.py [repeticiones]
"""
import json, sys, time, urllib.request

URL = "http://127.0.0.1:11434/api/chat"
BASE = "/home/abimael/PycharmProjects/Agente-Iron-Progress"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5

SYS = json.load(open(f"{BASE}/core/prompt.json"))["promt"]
T = json.load(open(f"{BASE}/core/tools.json"))
T = [t if t.get("type") == "function" else {"type": "function", "function": t} for t in T]
H = json.load(open(f"{BASE}/services/testbed/latencia/hist_congelado.json"))

CASOS = [
    ("me siento fatal, rpe 10 en la primera serie", {"autorregular_dia", "aplicar_descarga"},
     "0/N antes — autorregular_dia no estaba en el prompt"),
    ("que me toca entrenar manana?", {"consultar_rutina_futura"},
     "~2/5 antes — caso inestable, se mide para vigilarlo"),
    ("hazme una rutina nueva desde cero", set(),
     "acierto = NO llamar generar_rutina (Python controla ese flujo)"),
]

for texto, esperadas, nota in CASOS:
    print(f"\n===== {texto!r}\n      ({nota})")
    ok = 0
    for i in range(N):
        body = {
            "model": "qwen3.6:27b", "stream": False, "keep_alive": -1, "think": True, "tools": T,
            "messages": [{"role": "system", "content": SYS}] + H[-10:]
                        + [{"role": "user", "content": texto}],
            "options": {"num_ctx": 32768, "num_predict": 3072, "temperature": 0.6,
                        "top_p": 0.95, "top_k": 20, "repeat_penalty": 1.1,
                        "presence_penalty": 0.0, "min_p": 0.0, "num_gpu": 99},
        }
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=600).read())
        except Exception as e:
            print(f"  #{i+1}: ERROR {str(e)[:60]}")
            continue
        m = r.get("message", {})
        tc = {(x.get("function") or {}).get("name") for x in (m.get("tool_calls") or [])}
        acierto = ("generar_rutina" not in tc) if not esperadas else bool(tc & esperadas)
        ok += int(acierto)
        print(f"  #{i+1}: {'OK ' if acierto else 'NO '} {time.monotonic()-t0:5.1f}s "
              f"think={len(m.get('thinking') or '')//4:4d} tok -> {sorted(tc) if tc else '(ninguna)'}")
    print(f"  ==> {ok}/{N}")
