"""Reproduce el patron EXACTO de un turno del proyecto, incluida la llamada
intercalada del clasificador que antes desalojaba el prompt principal."""
import json, time, urllib.request, sys
URL="http://127.0.0.1:11434/api/chat"
BASE="/home/abimael/PycharmProjects/Agente-Iron-Progress"
SCR="/home/abimael/PycharmProjects/Agente-Iron-Progress/services/testbed/latencia"
NCTX=int(sys.argv[2]) if len(sys.argv)>2 else 32768
SYS=json.load(open(f"{BASE}/core/prompt.json"))["promt"]
T=json.load(open(f"{BASE}/core/tools.json"))
T=[t if t.get("type")=="function" else {"type":"function","function":t} for t in T]
H=json.load(open(f"{SCR}/hist_congelado.json"))
R=[]
for i in range(24):
    R.append({"role":"user","content":f"sesion {i+1}: hice sentadilla 140x5 @8, banca 100x6 @7, remo 80x10. me senti solido, dormi 7h, sin molestias."})
    R.append({"role":"assistant","content":f"Buen trabajo en la sesion {i+1}. Esa sentadilla a RPE 8 indica que vas bien encaminado; mantenemos la progresion y subimos 2.5 kg si el RPE se mantiene."})

def call(msgs,label,tools=True,npred=120):
    o={"num_ctx":NCTX,"num_predict":npred,"temperature":0.3,"top_p":0.85,"top_k":20,
       "repeat_penalty":1.1,"presence_penalty":0.0,"min_p":0.0,"num_gpu":99}
    b={"model":"qwen3.6:27b","messages":msgs,"stream":False,"keep_alive":-1,"think":False,"options":o}
    if tools: b["tools"]=T
    t0=time.monotonic()
    r=json.loads(urllib.request.urlopen(urllib.request.Request(URL,data=json.dumps(b).encode(),
        headers={"Content-Type":"application/json"}),timeout=600).read())
    w=time.monotonic()-t0
    p,pn=r.get("prompt_eval_count",0),r.get("prompt_eval_duration",0)
    e,en=r.get("eval_count",0),r.get("eval_duration",0)
    print(f"  [{label:32s}] wall={w:6.1f}s | prompt={p:6d} | prefill={pn/1e9:5.2f}s | gen={e:3d} tok")
    return (r.get("message",{}) or {}).get("content","") or "ok", w

TEL="\n\n--- TELEMETRIA ---\nFECHA: 2026-07-28\nSEMANA 1 DIA 2\n--- FIN TELEMETRIA ---"
base=[{"role":"system","content":SYS}]+R+H[-10:]
print(f"=== {sys.argv[1]} (num_ctx={NCTX}) ===")
tot=0
# 1) llamada principal del turno
m1=base+[{"role":"user","content":"anote banca 100x8 rpe 8 hoy"+TEL}]
_,w=call(m1,"1. principal (con tools)"); tot+=w
# 2) clasificador de intencion: prompt AISLADO -> el que rompia la cache
_,w=call([{"role":"user","content":"Clasifica: crear/consultar/otro. Mensaje: 'anote banca 100x8'"}],
         "2. clasificador (prompt aislado)",tools=False,npred=8); tot+=w
# 3) post-tools: EXTIENDE el prompt principal -> aqui se ve si sobrevivio la cache
m2=m1+[{"role":"assistant","content":"","tool_calls":[{"function":{"name":"registrar_serie_completada","arguments":{"peso_kg":100,"reps":8}}}]},
       {"role":"user","content":"[TOOL-RESULT]: serie registrada OK, volumen semanal 12400 kg"}]
_,w=call(m2,"3. post-tools (tras intercalada)"); tot+=w
# 4) turno siguiente del atleta
m3=m2+[{"role":"assistant","content":"Anotado bro"},{"role":"user","content":"y manana que me toca?"+TEL}]
_,w=call(m3,"4. turno siguiente"); tot+=w
print(f"  {'TOTAL TURNO':34s} {tot:.1f}s")
