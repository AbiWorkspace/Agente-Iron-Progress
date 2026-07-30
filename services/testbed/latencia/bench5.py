"""Patron REAL del bot: la conversacion CRECE (cada turno extiende al anterior),
no alterna entre prompts distintos. Asi es como se usa el prefix cache de verdad."""
import json, time, urllib.request, sys
URL="http://127.0.0.1:11434/api/chat"
BASE="/home/abimael/PycharmProjects/Agente-Iron-Progress"
SCR="/home/abimael/PycharmProjects/Agente-Iron-Progress/services/testbed/latencia"
SYS=json.load(open(f"{BASE}/core/prompt.json"))["promt"]
T=json.load(open(f"{BASE}/core/tools.json"))
T=[t if t.get("type")=="function" else {"type":"function","function":t} for t in T]
H=json.load(open(f"{SCR}/hist_congelado.json"))
R=[]
for i in range(24):
    R.append({"role":"user","content":f"sesion {i+1}: hice sentadilla 140x5 @8, banca 100x6 @7, remo 80x10. me senti solido, dormi 7h, sin molestias."})
    R.append({"role":"assistant","content":f"Buen trabajo en la sesion {i+1}. Esa sentadilla a RPE 8 indica que vas bien encaminado; mantenemos la progresion para la proxima semana y subimos 2.5 kg si el RPE se mantiene."})

def call(msgs,label):
    o={"num_ctx":16384,"num_predict":120,"temperature":0.3,"top_p":0.85,"top_k":20,
       "repeat_penalty":1.1,"presence_penalty":0.0,"min_p":0.0,"num_gpu":99}
    b={"model":"qwen3.6:27b","messages":msgs,"stream":False,"keep_alive":-1,"think":False,"options":o,"tools":T}
    t0=time.monotonic()
    r=json.loads(urllib.request.urlopen(urllib.request.Request(URL,data=json.dumps(b).encode(),
        headers={"Content-Type":"application/json"}),timeout=600).read())
    w=time.monotonic()-t0
    p,pn=r.get("prompt_eval_count",0),r.get("prompt_eval_duration",0)
    e,en=r.get("eval_count",0),r.get("eval_duration",0)
    print(f"  [{label:20s}] wall={w:6.1f}s | prompt={p:6d} | prefill={pn/1e9:5.2f}s | gen={e:3d} tok ({e/(en/1e9) if en else 0:4.1f} t/s)")
    return (r.get("message",{}) or {}).get("content","") or "ok"

print(f"=== {sys.argv[1] if len(sys.argv)>1 else 'run'} ===")
msgs=[{"role":"system","content":SYS}]+R+H[-10:]
for i,txt in enumerate(["anote banca 100x8 rpe 8 hoy","y manana que me toca?","como voy de volumen esta semana?"],1):
    msgs=msgs+[{"role":"user","content":txt+"\n\n--- TELEMETRIA ---\nFECHA: 2026-07-28\nSEMANA 1 DIA 2\n--- FIN TELEMETRIA ---"}]
    out=call(msgs,f"turno {i} (creciente)")
    msgs=msgs+[{"role":"assistant","content":out}]
