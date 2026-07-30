"""Compara estrategias para acotar el thinking SIN perder el criterio de tool calls.
Mide: tokens de razonamiento, tool calls emitidas y tiempo."""
import json, time, urllib.request, sys
URL="http://127.0.0.1:11434/api/chat"
BASE="/home/abimael/PycharmProjects/Agente-Iron-Progress"
SCR="/home/abimael/PycharmProjects/Agente-Iron-Progress/services/testbed/latencia"
SYS=json.load(open(f"{BASE}/core/prompt.json"))["promt"]
T=json.load(open(f"{BASE}/core/tools.json"))
T=[t if t.get("type")=="function" else {"type":"function","function":t} for t in T]
H=json.load(open(f"{SCR}/hist_congelado.json"))

def call(label, think, extra_sys=None, npred=3072, msg=None):
    sysmsg = SYS + (("\n\n"+extra_sys) if extra_sys else "")
    msgs=[{"role":"system","content":sysmsg}]+H[-10:]+[{"role":"user","content":msg}]
    o={"num_ctx":32768,"num_predict":npred,"temperature":0.6,"top_p":0.95,"top_k":20,
       "repeat_penalty":1.1,"presence_penalty":0.0,"min_p":0.0,"num_gpu":99}
    b={"model":"qwen3.6:27b","messages":msgs,"stream":False,"keep_alive":-1,"think":think,"options":o,"tools":T}
    t0=time.monotonic()
    try:
        r=json.loads(urllib.request.urlopen(urllib.request.Request(URL,data=json.dumps(b).encode(),
            headers={"Content-Type":"application/json"}),timeout=600).read())
    except Exception as e:
        print(f"  [{label:34s}] ERROR: {str(e)[:90]}"); return
    w=time.monotonic()-t0
    m=r.get("message",{}); th=m.get("thinking") or ""; c=m.get("content") or ""
    e,en=r.get("eval_count",0),r.get("eval_duration",0)
    tc=[ (x.get("function") or {}).get("name") for x in (m.get("tool_calls") or []) ]
    print(f"  [{label:34s}] gen={e:4d} tok / {en/1e9:5.1f}s | think={len(th)//4:4d} tok"
          f" | tools={tc if tc else '(ninguna)'} | {r.get('done_reason')}")

MSG="anote banca 100x8 rpe 8 hoy, como voy?"
print("=== ESTRATEGIAS PARA ACOTAR EL THINKING ===")
print(f"caso: {MSG!r}\n")
call("A. baseline think=True",           True)
call("B. think='low'",                   "low")
call("C. think='medium'",                "medium")
call("D. True + instruccion breve",      True,
     "[PRESUPUESTO DE RAZONAMIENTO]: piensa en 60 palabras COMO MAXIMO antes de responder. "
     "Decide la herramienta y actua; no repases el historial ni enumeres alternativas.")
call("E. think=False (referencia)",      False, npred=768)
