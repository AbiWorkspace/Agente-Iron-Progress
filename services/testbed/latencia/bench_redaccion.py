"""Turno de SOLO REDACCION tal y como lo construye el proyecto: Python ya
registro la serie e inyecto el bloque [SISTEMA]. Mide ahorro Y calidad del texto."""
import json, time, urllib.request
URL="http://127.0.0.1:11434/api/chat"
BASE="/home/abimael/PycharmProjects/Agente-Iron-Progress"
SCR="/home/abimael/PycharmProjects/Agente-Iron-Progress/services/testbed/latencia"
SYS=json.load(open(f"{BASE}/core/prompt.json"))["promt"]
T=json.load(open(f"{BASE}/core/tools.json"))
T=[t if t.get("type")=="function" else {"type":"function","function":t} for t in T]
H=json.load(open(f"{SCR}/hist_congelado.json"))

MSG=("anote banca 100x8 rpe 8 hoy"
     "\n\n[SISTEMA — SERIE YA REGISTRADA]: guardé su serie de Press Banco Plano "
     "100 kg x 8 reps @RPE 8. NO llames herramientas para esto; solo confírmaselo brevemente."
     "\n\n--- TELEMETRÍA ---\nFECHA: 2026-07-28\nSEMANA 1 DÍA 2 (hipertrofia)\n--- FIN TELEMETRÍA ---")

def call(label, think, npred):
    msgs=[{"role":"system","content":SYS}]+H[-10:]+[{"role":"user","content":MSG}]
    o={"num_ctx":32768,"num_predict":npred,"temperature":0.6 if think else 0.3,
       "top_p":0.95 if think else 0.85,"top_k":20,"repeat_penalty":1.1,
       "presence_penalty":0.0,"min_p":0.0,"num_gpu":99}
    b={"model":"qwen3.6:27b","messages":msgs,"stream":False,"keep_alive":-1,
       "think":think,"options":o,"tools":T}
    t0=time.monotonic()
    r=json.loads(urllib.request.urlopen(urllib.request.Request(URL,data=json.dumps(b).encode(),
        headers={"Content-Type":"application/json"}),timeout=600).read())
    w=time.monotonic()-t0
    m=r.get("message",{}); th=m.get("thinking") or ""; c=m.get("content") or ""
    tc=[(x.get("function") or {}).get("name") for x in (m.get("tool_calls") or [])]
    print(f"\n[{label}] wall={w:.1f}s | think={len(th)//4} tok | tools={tc or '(ninguna, correcto)'}")
    print(f"  RESPUESTA: {c.strip()[:340]}")
    return w

print("=== TURNO 'SOLO REDACCION' (Python ya registro la serie) ===")
a=call("ANTES  (v9: think=True) ", True, 3072)
b=call("DESPUES(v10: think=False)", False, 768)
print(f"\n  >>> AHORRO: {a-b:.1f}s  ({(1-b/a)*100:.0f}% mas rapido)")
