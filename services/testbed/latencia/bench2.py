import json, time, urllib.request
URL="http://127.0.0.1:11434/api/chat"
SYS=json.load(open("/home/abimael/PycharmProjects/Agente-Iron-Progress/core/prompt.json"))["promt"]
T=json.load(open("/home/abimael/PycharmProjects/Agente-Iron-Progress/core/tools.json"))
T=[t if t.get("type")=="function" else {"type":"function","function":t} for t in T]
H=json.load(open("/home/abimael/PycharmProjects/Agente-Iron-Progress/data/historial_527291395298.json"))

def call(msgs,tools,npred,think,label,opts=None):
    o={"num_ctx":32768,"num_predict":npred,"temperature":0.3,"top_p":0.85,"top_k":20,
       "repeat_penalty":1.1,"presence_penalty":0.0,"min_p":0.0,"num_gpu":99}
    if opts: o.update(opts)
    b={"model":"qwen3.6:27b","messages":msgs,"stream":False,"keep_alive":-1,"think":think,"options":o}
    if tools: b["tools"]=tools
    t0=time.monotonic()
    r=json.loads(urllib.request.urlopen(urllib.request.Request(URL,data=json.dumps(b).encode(),
        headers={"Content-Type":"application/json"}),timeout=300).read())
    w=time.monotonic()-t0
    m=r.get("message",{}); th=m.get("thinking") or ""; c=m.get("content") or ""
    p,pn=r.get("prompt_eval_count",0),r.get("prompt_eval_duration",0)
    e,en=r.get("eval_count",0),r.get("eval_duration",0)
    print(f"[{label}] wall={w:5.1f}s | prompt={p} tok, prefill_real={pn/1e9:.1f}s | GENERADOS={e} tok en {en/1e9:.1f}s ({e/(en/1e9) if en else 0:.1f} t/s)")
    print(f"          thinking={len(th)} chars (~{len(th)//3.5:.0f} tok) | contenido={len(c)} chars | tool_calls={len(m.get('tool_calls') or [])} | done={r.get('done_reason')}")
    return r

msgs=[{"role":"system","content":SYS}]+H[-14:]+[{"role":"user","content":"anote banca 100x8 rpe 8 hoy, como voy?"}]
print("=== turno REAL simulado (historial 14 msgs + 22 tools) ===")
call(msgs,T,3072,True ,"CON thinking")
call(msgs,T,768 ,False,"SIN thinking")
