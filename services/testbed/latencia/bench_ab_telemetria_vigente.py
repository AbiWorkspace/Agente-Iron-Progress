"""A/B del bloque <telemetria_vigente> añadido al system prompt el 2026-07-28.

En `bench_calidad.py` think=True bajó de 6/8 a 5/8, y el ÚNICO caso que se degradó
fue "que me toca entrenar manana?" (de OK con 421 tok de razonamiento a NO con
2853) — justo el que trata de calendario y día de entreno, el tema del bloque.
Con temperature 0.6 y una sola corrida eso puede ser ruido, así que aquí se repite
el caso N veces CON y SIN el bloque para separar las dos explicaciones.

    python3 bench_ab_telemetria_vigente.py [repeticiones]
"""
import json, re, sys, time, urllib.request

URL = "http://127.0.0.1:11434/api/chat"
BASE = "/home/abimael/PycharmProjects/Agente-Iron-Progress"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5

SYS_CON = json.load(open(f"{BASE}/core/prompt.json"))["promt"]
SYS_SIN = re.sub(r"\n<telemetria_vigente>.*?</telemetria_vigente>\n", "\n",
                 SYS_CON, flags=re.S)
assert SYS_SIN != SYS_CON, "no se pudo quitar el bloque del prompt"

T = json.load(open(f"{BASE}/core/tools.json"))
T = [t if t.get("type") == "function" else {"type": "function", "function": t} for t in T]
H = json.load(open(f"{BASE}/services/testbed/latencia/hist_congelado.json"))

TEXTO = "que me toca entrenar manana?"
ESPERADA = {"consultar_rutina_futura"}
# Caso de control: no habla de calendario, así que el bloque no debería tocarlo.
CONTROL = ("pausa mi plan que me voy de viaje dos semanas", {"pausar_plan"})


def tirada(sys_prompt, texto, esperadas):
    body = {
        "model": "qwen3.6:27b", "stream": False, "keep_alive": -1, "think": True, "tools": T,
        "messages": [{"role": "system", "content": sys_prompt}] + H[-10:]
                    + [{"role": "user", "content": texto}],
        "options": {"num_ctx": 32768, "num_predict": 3072, "temperature": 0.6,
                    "top_p": 0.95, "top_k": 20, "repeat_penalty": 1.1,
                    "presence_penalty": 0.0, "min_p": 0.0, "num_gpu": 99},
    }
    t0 = time.monotonic()
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=600).read())
    m = r.get("message", {})
    tc = {(x.get("function") or {}).get("name") for x in (m.get("tool_calls") or [])}
    return bool(tc & esperadas), len(m.get("thinking") or "") // 4, time.monotonic() - t0


for etiqueta, texto, esperadas in (("CASO   ", TEXTO, ESPERADA),
                                   ("CONTROL", CONTROL[0], CONTROL[1])):
    print(f"\n===== {etiqueta}: {texto!r}")
    for nombre, sp in (("CON bloque", SYS_CON), ("SIN bloque", SYS_SIN)):
        ok = 0
        tks = []
        for i in range(N):
            try:
                acierto, tk, w = tirada(sp, texto, esperadas)
            except Exception as e:
                print(f"  {nombre} #{i+1}: ERROR {str(e)[:60]}")
                continue
            ok += int(acierto)
            tks.append(tk)
            print(f"  {nombre} #{i+1}: {'OK ' if acierto else 'NO '} "
                  f"{w:5.1f}s think={tk:4d} tok")
        media = sum(tks) / len(tks) if tks else 0
        print(f"  -> {nombre}: {ok}/{N} aciertos | thinking medio {media:.0f} tok")
