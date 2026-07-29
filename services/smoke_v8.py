# -*- coding: utf-8 -*-
"""SMOKE TESTS v8 — importa el llm_service.py REAL (con stubs de sus dependencias)
y verifica los 6 mandatos contra los regex y funciones verdaderos del módulo."""
import asyncio, importlib.util, json, os, re, sys, types
from unittest.mock import MagicMock

BASE = os.path.dirname(os.path.abspath(__file__))
APP  = BASE if os.path.exists(os.path.join(BASE, "llm_service.py")) \
       else os.path.join(BASE, "testbed", "app")

# core/ejercicios.py NO se stubea: es lógica pura (re + unicodedata, sin ollama
# ni chroma ni disco), así que el smoke la ejercita de verdad. Solo hace falta
# que la raíz del proyecto esté en el path para poder importarla.
RAIZ = os.path.dirname(BASE)
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

# ---------- 1) STUBS de dependencias externas ----------
class _FakeAsyncClient:
    def __init__(self, *a, **k): pass
    async def chat(self, *a, **k):
        raise RuntimeError("ollama no disponible en smoke")

ollama_stub = types.ModuleType("ollama"); ollama_stub.AsyncClient = _FakeAsyncClient
aiofiles_stub = types.ModuleType("aiofiles")
lo = types.ModuleType("langchain_ollama"); lo.OllamaEmbeddings = MagicMock()
lc = types.ModuleType("langchain_chroma"); lc.Chroma = MagicMock()

services = types.ModuleType("services")
db = types.ModuleType("services.db_service")
db.guardar_serie_sql = MagicMock(); db.inicializar_db = MagicMock()
db.obtener_dashboard_analitico = MagicMock(return_value="")
cx = types.ModuleType("services.conexion_entrenamiento_llm_orquestacion")
cx.recibir_peticion = MagicMock()
cr = types.ModuleType("services.calculadora_rm")
cr.calcular_1rm = MagicMock(return_value=100.0); cr.TipoEjercicio = MagicMock()
ma = types.ModuleType("services.metricas_atleta"); ma.evaluar_perfil_completo = MagicMock()
ra = types.ModuleType("services.repositorio_atleta"); ra.repositorio = MagicMock()
# v9: llm_service pasó a importar el dispatcher de tools. Sin este stub, el smoke
# moría en el import con "'services' is not a package" y no llegaba a probar nada.
h9 = types.ModuleType("services.handlers_v9"); h9.DISPATCHER_V9 = {}

sys.modules.update({
    "ollama": ollama_stub, "aiofiles": aiofiles_stub,
    "langchain_ollama": lo, "langchain_chroma": lc,
    "services": services, "services.db_service": db,
    "services.conexion_entrenamiento_llm_orquestacion": cx,
    "services.calculadora_rm": cr, "services.metricas_atleta": ma,
    "services.repositorio_atleta": ra, "services.handlers_v9": h9,
})

# ---------- 2) IMPORTAR el módulo real ----------
spec = importlib.util.spec_from_file_location("llm_service", os.path.join(APP, "llm_service.py"))
llm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(llm)
FUENTE = open(os.path.join(APP, "llm_service.py"), encoding="utf-8").read()

OK = FALLA = 0
def chk(nombre, cond, extra=""):
    global OK, FALLA
    if cond: OK += 1;    print(f"  ✔ {nombre}")
    else:    FALLA += 1; print(f"  ✘ {nombre}  {extra}")

# ---------- MANDATO 4 — notación de entreno mata el modo casual ----------
print("\n[M4] NOTACION_ENTRENO_RE + router casual")
POS = ["100 x 10 r @8", "80kg x 5", "3x10 rpe 8", "subí a 0.45 hoy",
       "135 lbs x 3", "@ 8.5 la última", "hice 10 reps", "220 lbs", "60 kilos x 8"]
NEG = ["hola bro", "siii", "gracias!", "vale, mañana te digo", "okey espero"]
for t in POS: chk(f"detecta {t!r}", bool(llm.NOTACION_ENTRENO_RE.search(t)))
for t in NEG: chk(f"ignora {t!r}", not llm.NOTACION_ENTRENO_RE.search(t))
# v11 — Antes esto buscaba la expresión literal en el fuente. El clasificador
# pasó a ser una función (_es_charla_casual), así que se comprueba lo que
# importa de verdad: que ninguna notación de entrenamiento acabe en modo casual,
# porque ahí se apagan tools, thinking y RAG.
for t in POS: chk(f"router: {t!r} nunca es casual", not llm._es_charla_casual(t))
chk("cinturón serie->no casual (fuente)", "serie detectada -> NO casual" in FUENTE)

# ---------- MANDATO 5 — señal fuerte vs débil + escape del jail ----------
print("\n[M5] CREACION_FUERTE_RE / clasificador / consulta de rutina")
FUERTE = ["hazme una rutina nueva", "crea un plan desde cero", "rehaz mi programa",
          "quiero otra rutina", "arma un mesociclo de fuerza", "genera mi plan de entrenamiento"]
DEBIL  = ["¿qué peso uso para cumplir mi rutina?", "quiero mejorar en mi rutina",
          "mi rutina de hoy estuvo dura", "amo mi plan"]
for t in FUERTE: chk(f"fuerte {t!r}", bool(llm.CREACION_FUERTE_RE.search(t)))
for t in DEBIL:  chk(f"NO fuerte {t!r}", not llm.CREACION_FUERTE_RE.search(t))

CONSULTA = ["¿qué peso uso para cumplir mi rutina?", "cuanto peso le pongo hoy",
            "cuánto peso debo cargar", "cómo cumplo mi rutina", "que pesos manejo"]
for t in CONSULTA:
    r = llm.detectar_consulta_rutina(t)
    chk(f"consulta {t!r} -> ver_rutina", bool(r) and r.get("tipo") == "ver_rutina", f"got {r}")
chk("'hola' no es consulta", llm.detectar_consulta_rutina("hola") is None)

veredicto = asyncio.run(llm.clasificar_intencion_plan("lo que sea", "smoke"))
chk("clasificador fail-safe -> 'otro' sin Ollama", veredicto == "otro", f"got {veredicto!r}")
chk("escape del jail por consulta/pregunta (fuente)",
    bool(re.search(r"detectar_consulta_rutina\(texto_puro\)[\s\S]{0,80}or[\s\S]{0,40}\"\?\"", FUENTE)))
chk("confirm regen exige señal FUERTE (fuente)",
    "CREACION_FUERTE_RE" in FUENTE and FUENTE.count("CREACION_FUERTE_RE") >= 3)

# ---------- MANDATO 1 — whitelist dinámica de kg ----------
print("\n[M1] whitelist dinámica + tolerancia ±0.5 + bypass acotado")
chk("números libres 145/30, sin años ni %",
    llm._extraer_numeros_libres("mi PR fue 145 y cargué 30 de lastre en 2026 al 80%") == {145.0, 30.0})

tmp = os.path.join(BASE, "testbed", "data_smoke"); os.makedirs(tmp, exist_ok=True)
p_hist = os.path.join(tmp, "hist_rm.json"); p_peso = os.path.join(tmp, "peso.json")
json.dump([{"rm": 147.5, "peso_origen": 135.0}], open(p_hist, "w"))
json.dump([{"peso": 104.5, "fecha": "2026-07-01"}], open(p_peso, "w"))
llm.cargar_perfil = lambda n: {"nombre": "Bro", "peso": 104.5, "lastre_kg": 30, "edad": 27}
llm.cargar_rms   = lambda n: {"press militar": 60.0, "sentadilla": 180.0}
llm._ruta_historial_rm = lambda n: p_hist
llm._ruta_peso         = lambda n: p_peso

wl = llm._construir_whitelist_kgs(
    "smoke",
    "hoy toca 100kg en banca",                                     # fuentes del turno
    [{"role": "user", "content": "mi PR de peso muerto fue 145"},  # bug 1.1 (historial)
     {"role": "assistant", "content": "tu último top fue 152.5 kg"}],
    "[TOOL-RESULT] consultar_rutina_futura: semana 2 -> 135 kg",   # bug 1.3 (tools)
)
for v, tag in [(100.0, "turno"), (145.0, "bug1.1 historial"), (152.5, "assistant historial"),
               (135.0, "bug1.3 tool-result"), (30.0, "bug1.2 lastre perfil"),
               (60.0, "RM canónico"), (147.5, "historial RM"), (104.5, "peso corporal")]:
    chk(f"whitelist incluye {v} ({tag})", v in wl)

chk("±0.5: 147 pasa con PR 147.5", llm._verificar_kgs_salida("sube a 147 kg", {147.5}) == [])
chk("inventado 122.5 detectado", llm._verificar_kgs_salida("te toca 122.5 kg", {100.0, 145.0}) == [122.5])
chk("triviales exentos (2.5 kg discos)", llm._verificar_kgs_salida("pon 2.5 kg por lado y 20 kg de barra", set()) == [])
chk("bug1.4 'siii': cita del historial pasa", llm._verificar_kgs_salida("tu PR sigue en 145 kg", wl) == [])
chk("bypass acotado presente (fuente)", "rutina_vacia" in FUENTE and "sin digitos" in FUENTE.lower()
    or "salida_con_forma_de_rutina" in FUENTE or "forma de rutina" in FUENTE)

# ---------- MANDATO 3 — fallbacks honestos ----------
print("\n[M3] _instruccion_fallback con estado real")
i1 = llm._instruccion_fallback("Hubo un problema técnico.", [], ["registrar_serie_completada (timeout)"])
chk("sin éxitos -> PROHIBIDO afirmar guardado", "PROHIBIDO" in i1)
chk("fallidas viajan al modelo", "registrar_serie_completada" in i1)
i2 = llm._instruccion_fallback("Cierra el turno.", ["serie de banca guardada"], [])
chk("éxitos confirmables viajan", "SÍ SE COMPLETARON" in i2 and "banca" in i2)
chk("guardia CLAIM re-verifica (fuente)", FUENTE.count("_CLAIM_ACCION_RE") >= 2)
chk("plantilla honesta determinista (fuente)", "Seré honesto contigo bro" in FUENTE)

# ---------- MANDATO 2 — dedupe + to_thread ----------
print("\n[M2] dedupe de tools + escrituras sin bloquear el loop")
chk("firmas_ejecutadas en el loop (fuente)", "firmas_ejecutadas" in FUENTE)
chk("TOOLS_ESCRITURA definido con escrituras clave",
    {"generar_rutina", "registrar_serie_completada", "avanzar_dia"} <= set(llm.TOOLS_ESCRITURA))
chk("to_thread usado en escrituras (fuente)", FUENTE.count("asyncio.to_thread") >= 8,
    f"count={FUENTE.count('asyncio.to_thread')}")

# ---------- MANDATO 6 — retry anti-truncamiento ----------
print("\n[M6] chat_llm: done_reason='length' -> retry sin thinking")
chk("log de done_reason (fuente)", "done_reason" in FUENTE)
chk("condición exacta del retry (fuente)",
    re.search(r"done_reason\)\s*==\s*\"length\"[\s\S]{0,120}len\(contenido\.strip\(\)\)\s*<\s*60", FUENTE) is not None)
chk("retry apaga thinking (fuente)", 'kwargs["think"] = False' in FUENTE)
chk("PENSAR_POST_TOOLS=False (síntesis fría)", llm.PENSAR_POST_TOOLS is False)

# ---------- FIX ESTRUCTURAL — flujos destructivos vivos ----------
print("\n[ESTRUCTURAL] bloque v6 reubicado")
chk("marcador del fix presente", "v8 FIX ESTRUCTURAL" in FUENTE)
chk("BORRAR_PLAN_RE detecta", bool(llm.BORRAR_PLAN_RE.search("borra mi plan por favor")))
chk("CAMBIAR_DIAS detecta", bool(llm.CAMBIAR_DIAS_INTENT_RE.search("ahora solo puedo entrenar 2 días")))

print(f"\n{'='*60}\nRESULTADO: {OK} OK, {FALLA} FALLAS\n{'='*60}")
sys.exit(1 if FALLA else 0)