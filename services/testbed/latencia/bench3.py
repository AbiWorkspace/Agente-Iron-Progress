"""A/B de la Fase 1: mide el REPREFILL, que es lo que los checkpoints deben mover.
Simula el patron real del proyecto: turno principal -> post-tools -> turno siguiente."""
import json, time, urllib.request, sys

URL="http://127.0.0.1:11434/api/chat"
BASE="/home/abimael/PycharmProjects/Agente-Iron-Progress"
SYS=json.load(open(f"{BASE}/core/prompt.json"))["promt"]
T=json.load(open(f"{BASE}/core/tools.json"))
T=[t if t.get("type")=="function" else {"type":"function","function":t} for t in T]
H=json.load(open(f"{BASE}/data/historial_527291395298.json"))

def call(msgs,tools,npred,think,label):
    o={"num_ctx":16384,"num_predict":npred,"temperature":0.3,"top_p":0.85,"top_k":20,
       "repeat_penalty":1.1,"presence_penalty":0.0,"min_p":0.0,"num_gpu":99}
    b={"model":"qwen3.6:27b","messages":msgs,"stream":False,"keep_alive":-1,"think":think,"options":o}
    if tools: b["tools"]=tools
    t0=time.monotonic()
    r=json.loads(urllib.request.urlopen(urllib.request.Request(URL,data=json.dumps(b).encode(),
        headers={"Content-Type":"application/json"}),timeout=600).read())
    w=time.monotonic()-t0
    p,pn=r.get("prompt_eval_count",0),r.get("prompt_eval_duration",0)
    e,en=r.get("eval_count",0),r.get("eval_duration",0)
    print(f"  [{label:22s}] wall={w:6.1f}s | prompt={p:6d} tok | prefill={pn/1e9:5.2f}s"
          f" | gen={e:4d} tok en {en/1e9:5.1f}s ({e/(en/1e9) if en else 0:4.1f} t/s) | {r.get('done_reason')}")
    return r

# Turno 1: principal con tools (patron real: historial largo + tools + telemetria al final)
m1=[{"role":"system","content":SYS}]+H[-14:]+[
    {"role":"user","content":"anote banca 100x8 rpe 8 hoy\n\n--- TELEMETRIA ---\nFECHA: 2026-07-28 01:15\nSEMANA 1 DIA 2\n--- FIN TELEMETRIA ---"}]
# Turno 2: post-tools (mismo prefijo + resultado de herramienta al final) -> AQUI se ve el reprefill
m2=m1+[{"role":"assistant","content":"","tool_calls":[{"function":{"name":"registrar_serie_completada","arguments":{"peso_kg":100,"reps":8}}}]},
       {"role":"user","content":"[TOOL-RESULT]: serie registrada OK, volumen semanal 12400 kg"}]
# Turno 3: siguiente mensaje del atleta (prefijo comun con turno 1 salvo el final)
m3=[{"role":"system","content":SYS}]+H[-14:]+[
    {"role":"user","content":"y manana que me toca?\n\n--- TELEMETRIA ---\nFECHA: 2026-07-28 01:16\nSEMANA 1 DIA 2\n--- FIN TELEMETRIA ---"}]

etiqueta=sys.argv[1] if len(sys.argv)>1 else "run"
print(f"=== {etiqueta} ===")
call(m1,T,256,False,"1-principal")
call(m2,T,256,False,"2-post-tools")
call(m3,T,256,False,"3-turno siguiente")
