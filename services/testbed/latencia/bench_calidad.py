"""Decide si el thinking compra calidad: mide ACIERTO de tool call por modo.
Sin esto, quitar el thinking es apostar a ciegas."""
import json, time, urllib.request
URL="http://127.0.0.1:11434/api/chat"
BASE="/home/abimael/PycharmProjects/Agente-Iron-Progress"
SCR="/home/abimael/PycharmProjects/Agente-Iron-Progress/services/testbed/latencia"
SYS=json.load(open(f"{BASE}/core/prompt.json"))["promt"]
T=json.load(open(f"{BASE}/core/tools.json"))
T=[t if t.get("type")=="function" else {"type":"function","function":t} for t in T]
H=json.load(open(f"{SCR}/hist_congelado.json"))

# AVISO (2026-07-28): el último caso está MAL ESPECIFICADO — espera `generar_rutina`,
# pero el system prompt PROHIBE al modelo invocar herramientas de generación (el flujo
# lo controla Python). El modelo acierta al no llamarla, así que ese caso cuenta como
# fallo siempre y el máximo real de este script es 7/8, no 8/8. No se corrige aquí para
# no romper la comparabilidad con `resultado_calidad.txt`; la versión corregida está en
# `bench_calidad_hist.py`.
CASOS=[
 ("ya termine mi sesion de hoy, todo completo",        {"avanzar_dia"}),
 ("que me toca entrenar manana?",                      {"consultar_rutina_futura"}),
 ("cambia el peso muerto por rumano en todo el plan",  {"sustituir_ejercicio"}),
 ("hice 140x3 en sentadilla, cual seria mi 1rm?",      {"calcular_rm_exacto"}),
 ("me siento fatal, rpe 10 en la primera serie",       {"autorregular_dia","aplicar_descarga"}),
 ("pausa mi plan que me voy de viaje dos semanas",     {"pausar_plan"}),
 ("mandame la grafica de mi progreso",                 {"enviar_grafica_progreso"}),
 ("hazme una rutina nueva desde cero",                 {"generar_rutina"}),
]
MODOS=[("think=True",True,3072),("think=low","low",3072),("think=False",False,768)]

res={m[0]:{"ok":0,"t":0.0,"think":0} for m in MODOS}
for texto,esperadas in CASOS:
    print(f"\n--- {texto!r}")
    print(f"    esperado: {'|'.join(sorted(esperadas))}")
    for nombre,th,npred in MODOS:
        msgs=[{"role":"system","content":SYS}]+H[-10:]+[{"role":"user","content":texto}]
        o={"num_ctx":32768,"num_predict":npred,"temperature":0.6,"top_p":0.95,"top_k":20,
           "repeat_penalty":1.1,"presence_penalty":0.0,"min_p":0.0,"num_gpu":99}
        b={"model":"qwen3.6:27b","messages":msgs,"stream":False,"keep_alive":-1,
           "think":th,"options":o,"tools":T}
        t0=time.monotonic()
        try:
            r=json.loads(urllib.request.urlopen(urllib.request.Request(URL,
                data=json.dumps(b).encode(),headers={"Content-Type":"application/json"}),timeout=600).read())
        except Exception as e:
            print(f"    {nombre:12s} ERROR {str(e)[:60]}"); continue
        w=time.monotonic()-t0
        m=r.get("message",{}); th_txt=m.get("thinking") or ""
        tc={(x.get("function") or {}).get("name") for x in (m.get("tool_calls") or [])}
        acierto = bool(tc & esperadas)
        res[nombre]["ok"]+=int(acierto); res[nombre]["t"]+=w; res[nombre]["think"]+=len(th_txt)//4
        print(f"    {nombre:12s} {'OK ' if acierto else 'NO '} {w:5.1f}s think={len(th_txt)//4:4d} tok -> {sorted(tc) if tc else '(ninguna)'}")

print("\n\n================ RESUMEN ================")
n=len(CASOS)
for nombre,_,_ in MODOS:
    r=res[nombre]
    print(f"{nombre:12s} aciertos {r['ok']}/{n} | tiempo total {r['t']:6.1f}s "
          f"(media {r['t']/n:5.1f}s) | thinking total {r['think']} tok")
