"""A/B con prompt de TAMANO FIJO (~15.4k tok), comparable al baseline.
El historial en disco cambia mientras el bot corre, asi que aqui se usa una
copia congelada + relleno determinista para fijar el tamano del prompt."""
import json, time, urllib.request, sys
URL="http://127.0.0.1:11434/api/chat"
BASE="/home/abimael/PycharmProjects/Agente-Iron-Progress"
SCR="/home/abimael/PycharmProjects/Agente-Iron-Progress/services/testbed/latencia"
SYS=json.load(open(f"{BASE}/core/prompt.json"))["promt"]
T=json.load(open(f"{BASE}/core/tools.json"))
T=[t if t.get("type")=="function" else {"type":"function","function":t} for t in T]
H=json.load(open(f"{SCR}/hist_congelado.json"))

# Relleno determinista: notas de sesion realistas hasta igualar el prompt del baseline
RELLENO=[]
for i in range(24):
    RELLENO.append({"role":"user","content":f"sesion {i+1}: hice sentadilla 140x5 @8, banca 100x6 @7, remo 80x10. me sentí solido, dormí 7h, sin molestias."})
    RELLENO.append({"role":"assistant","content":f"Buen trabajo en la sesion {i+1}. Esa sentadilla a RPE 8 indica que vas bien encaminado; mantenemos la progresion para la proxima semana y subimos 2.5 kg si el RPE se mantiene. La banca sigue estable, y el remo esta en el rango objetivo de volumen."})

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
    return w

TEL="\n\n--- TELEMETRIA ---\nFECHA: 2026-07-28 01:30\nSEMANA 1 DIA 2\n--- FIN TELEMETRIA ---"
base=[{"role":"system","content":SYS}]+RELLENO+H[-10:]
m1=base+[{"role":"user","content":"anote banca 100x8 rpe 8 hoy"+TEL}]
m2=m1+[{"role":"assistant","content":"","tool_calls":[{"function":{"name":"registrar_serie_completada","arguments":{"peso_kg":100,"reps":8}}}]},
       {"role":"user","content":"[TOOL-RESULT]: serie registrada OK, volumen semanal 12400 kg"}]
m3=base+[{"role":"user","content":"y manana que me toca?"+TEL}]

print(f"=== {sys.argv[1] if len(sys.argv)>1 else 'run'} ===")
t=0
t+=call(m1,T,256,False,"1-principal")
t+=call(m2,T,256,False,"2-post-tools")
t+=call(m3,T,256,False,"3-turno siguiente")
print(f"  {'TOTAL 3 llamadas':24s} {t:.1f}s")
