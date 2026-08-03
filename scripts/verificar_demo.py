# -*- coding: utf-8 -*-
"""
verificar_demo.py — pasa lista a todo lo que la demostración va a enseñar.

Para qué existe
---------------
Sembrar datos no garantiza que las herramientas respondan: `predecir_carga_optima`
puede seguir diciendo 'datos_insuficientes' porque las series no traen la tríada
completa, la biblioteca científica puede estar indexada con otro nombre de
colección, y `consultar_historial_narrativo` puede contestar en vacío porque
nadie escribe nunca esa colección.

Este script comprueba, una por una y contra el sistema de verdad, que cada pieza
tiene algo que decir ANTES de que haya público delante. Ninguna comprobación
usa el LLM: todas leen disco, SQLite o ChromaDB, así que el chequeo entero tarda
segundos y se puede repetir justo antes de empezar.

    python3 scripts/verificar_demo.py
    python3 scripts/verificar_demo.py 5215500000123

Códigos de salida: 0 si todo lo crítico está listo, 1 si algo crítico falla.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

CARPETA_DATA = RAIZ / "data"
DB_ATLETAS = CARPETA_DATA / "iron_progress.db"
DB_TELEMETRIA = CARPETA_DATA / "iron_progress_telemetria.db"
NUMERO_DEMO = "5217291395298"

VERDE, ROJO, AMARILLO, GRIS, FIN = "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[0m"

_resultados: list[tuple[str, bool, bool, str]] = []   # (nombre, ok, critico, detalle)


def comprobar(nombre: str, critico: bool = True):
    """Decorador: ejecuta la comprobación y captura el fallo sin tumbar el resto."""
    def envoltura(fn):
        try:
            ok, detalle = fn()
        except Exception as e:
            ok, detalle = False, f"{type(e).__name__}: {e}"
        _resultados.append((nombre, ok, critico, detalle))
        icono = f"{VERDE}✓{FIN}" if ok else (f"{ROJO}✗{FIN}" if critico else f"{AMARILLO}!{FIN}")
        print(f"  {icono} {nombre:<38} {GRIS}{detalle}{FIN}")
        return fn
    return envoltura


def main() -> int:
    numero = (sys.argv[1] if len(sys.argv) > 1 else NUMERO_DEMO).strip()

    print(f"\n{'=' * 74}")
    print(f"  VERIFICACIÓN DE LA DEMOSTRACIÓN — atleta {numero}")
    print(f"  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'=' * 74}\n")

    # ── 1. Cimientos ────────────────────────────────────────────────────────
    print(f"{GRIS}  CIMIENTOS{FIN}")

    @comprobar("Perfil del atleta")
    def _perfil():
        with sqlite3.connect(DB_ATLETAS) as con:
            f = con.execute("SELECT peso_kg, altura_m, edad, genero, meta, dias_semana "
                            "FROM atletas WHERE numero_usuario = ?", (numero,)).fetchone()
        if not f:
            return False, "no existe en la tabla atletas — corre sembrar_demo.py"
        faltan = [c for c, v in zip(("peso", "altura", "edad", "género", "meta", "días"), f) if v in (None, "")]
        if faltan:
            return False, f"faltan campos: {', '.join(faltan)}"
        return True, f"{f[0]} kg · {f[1]} m · {f[2]} años · {f[4]} · {f[5]} días"

    @comprobar("Plan generado (3 mesociclos)")
    def _plan():
        carpeta = RAIZ / "resources" / "rutinas_generadas" / numero
        archivos = sorted(p.name for p in carpeta.glob("*.json")) if carpeta.exists() else []
        if len(archivos) < 3:
            return False, f"solo {len(archivos)} ficheros — el plan no está completo"
        sesiones = sum(len(json.loads((carpeta / a).read_text(encoding="utf-8"))) for a in archivos)
        return True, f"{len(archivos)} mesociclos, {sesiones} sesiones"

    @comprobar("Estado del calendario")
    def _estado():
        estados = json.loads((CARPETA_DATA / "estado_usuarios.json").read_text(encoding="utf-8"))
        e = estados.get(numero)
        if not e:
            return False, "el atleta no está en estado_usuarios.json"
        if e.get("onboarding_activo"):
            return False, "onboarding ACTIVO: el coach pedirá datos en vez de entrenar"
        if e.get("plan_pausado"):
            return False, "el plan está PAUSADO: hoy no habrá rutina"
        return True, f"semana {e.get('semana')}, día {e.get('dia')}, programado {e.get('fecha_programada')}"

    # ── 2. Diario y analítica ───────────────────────────────────────────────
    print(f"\n{GRIS}  DIARIO Y ANALÍTICA{FIN}")

    @comprobar("Series con tríada completa")
    def _series():
        with sqlite3.connect(DB_TELEMETRIA) as con:
            total, con_triada, ultima = con.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN peso_kg IS NOT NULL AND repeticiones IS NOT NULL "
                "         AND rpe_reportado IS NOT NULL THEN 1 ELSE 0 END), "
                "MAX(date(fecha_hora)) FROM series_log WHERE numero_usuario = ?",
                (numero,)).fetchone()
        if not total:
            return False, "diario vacío"
        # motor_predictivo.leer_series descarta lo que no trae la tríada.
        if (con_triada or 0) < 20:
            return False, f"{con_triada} series con tríada (hacen falta bastantes más)"
        dias = (datetime.now().date() - datetime.fromisoformat(ultima).date()).days
        aviso = "" if dias <= 2 else f" — ¡la última es de hace {dias} días!"
        return True, f"{con_triada}/{total} con tríada, última {ultima}{aviso}"

    @comprobar("Marcas de 1RM")
    def _marcas():
        with sqlite3.connect(DB_ATLETAS) as con:
            n, ejercicios = con.execute(
                "SELECT COUNT(*), COUNT(DISTINCT ejercicio) FROM marcas_rm "
                "WHERE numero_usuario = ?", (numero,)).fetchone()
        if (ejercicios or 0) < 3:
            return False, f"solo {ejercicios} ejercicios con marca"
        return True, f"{n} marcas en {ejercicios} ejercicios"

    @comprobar("Cobertura de kg del plan")
    def _cobertura():
        """Cada ejercicio del plan sin marca sale como *peso por calibrar*."""
        import unicodedata

        def norm(x):
            return unicodedata.normalize("NFD", str(x)).encode("ascii", "ignore").decode().lower().strip()

        with sqlite3.connect(DB_ATLETAS) as con:
            marcas = {norm(r[0]) for r in con.execute(
                "SELECT DISTINCT ejercicio FROM marcas_rm WHERE numero_usuario = ?", (numero,))}
        carpeta = RAIZ / "resources" / "rutinas_generadas" / numero
        ficheros = sorted(carpeta.glob("*.json")) if carpeta.exists() else []
        if not ficheros:
            return False, "sin plan que revisar"
        nombres = {e["Ejercicio"] for f in ficheros
                   for s in json.loads(f.read_text(encoding="utf-8")) for e in s.get("Ejercicios", [])}
        sin = sorted(n for n in nombres if norm(n) not in marcas)
        if sin:
            return False, f"{len(sin)}/{len(nombres)} saldrán como 'peso por calibrar': {', '.join(sin[:3])}…"
        return True, f"{len(nombres)}/{len(nombres)} ejercicios con kg reales"

    @comprobar("predecir_carga_optima")
    def _predecir():
        from services import motor_predictivo as mp
        r = mp.predecir_carga_optima(numero, "Press Banco Plano", 5)
        if not r.get("ok"):
            return False, f"{r.get('motivo') or r}"
        s = r.get("senales", {})
        return True, f"{r['carga_recomendada_kg']} kg · {s.get('sesiones_usadas')} sesiones · ACWR {s.get('acwr')}"

    @comprobar("analizar_mrv")
    def _mrv():
        from services import motor_predictivo as mp
        r = mp.analizar_mrv(numero)
        if not r.get("ok"):
            return False, f"{r.get('motivo') or r}"
        m = r.get("metricas", {})
        return True, (f"veredicto '{r.get('veredicto')}' · {m.get('sets_duros_recientes')} sets recientes "
                      f"vs {m.get('sets_duros_previos')} previos")

    @comprobar("ACWR (carga aguda/crónica)")
    def _acwr():
        from services import motor_predictivo as mp
        v = mp.acwr_usuario(numero)
        if v is None:
            return False, "sin datos suficientes"
        if not (0.7 <= v <= 1.4):
            return False, f"{v} — fuera de la zona creíble (0.7-1.4); el relato de la demo chirriará"
        return True, f"{v} (zona óptima)"

    @comprobar("evaluar_nivel_fuerza")
    def _nivel():
        """Lee las marcas por clave canónica exacta, sin pasar por los alias."""
        from services.llm_service import evaluar_nivel_fuerza, cargar_rms, cargar_perfil
        r, p = cargar_rms(numero), cargar_perfil(numero)
        res = evaluar_nivel_fuerza({
            "peso_kg": p.get("peso_kg", 0),
            "rm_banca_kg": r.get("press banco plano", 0),
            "rm_sentadilla_kg": r.get("sentadilla", 0),
            "rm_peso_muerto_kg": r.get("peso muerto", 0),
            "genero": p.get("genero", "hombre"),
        })
        if res.get("datos_insuficientes"):
            return False, ("devuelve 'principiante' por falta de marcas: la clave canónica "
                           "no coincide con la guardada (¿'sentadillas' vs 'sentadilla'?)")
        return True, f"nivel '{res.get('nivel_sugerido')}'"

    @comprobar("Gráfica de progreso")
    def _grafica():
        from services.proactividad import generar_grafica_e1rm
        r = generar_grafica_e1rm(numero, "Press Banco Plano", dias=120)
        if not r:
            return False, "no hay puntos suficientes para dibujarla"
        ruta, resumen = r
        return True, f"{Path(ruta).name} · {resumen.get('sesiones')} sesiones, Δ {resumen.get('delta_kg')} kg"

    @comprobar("Readiness (check matutino)", critico=False)
    def _readiness():
        with sqlite3.connect(DB_TELEMETRIA) as con:
            n, ultimo = con.execute(
                "SELECT COUNT(*), MAX(fecha) FROM readiness WHERE numero_usuario = ?",
                (numero,)).fetchone()
        if not n:
            return False, "sin check-ins: el coach no podrá hablar de tu descanso"
        return True, f"{n} días registrados, último {ultimo}"

    @comprobar("Peso corporal")
    def _peso():
        ruta = CARPETA_DATA / f"peso_corporal_{numero}.json"
        if not ruta.exists():
            return False, "sin registro de peso corporal (FFMI y progreso quedarán cojos)"
        filas = json.loads(ruta.read_text(encoding="utf-8"))
        return True, f"{len(filas)} registros, actual {filas[-1].get('peso')} kg"

    # ── 3. Las tres memorias ────────────────────────────────────────────────
    print(f"\n{GRIS}  MEMORIA{FIN}")

    @comprobar("Biblioteca científica")
    def _biblioteca():
        from services.llm_service import consultar_literatura_cientifica
        r = consultar_literatura_cientifica("volumen semanal por grupo muscular para hipertrofia")
        if not r or "No se encontró" in r or r.startswith("Error"):
            return False, "vacía — corre scripts/crear_base_conocimiento.py (~1 min)"
        return True, f"responde ({len(r)} caracteres)"

    @comprobar("Expediente personal")
    def _expediente():
        from services.llm_service import consultar_expediente_atleta
        r = consultar_expediente_atleta(numero, "molestias y material de gimnasio")
        if not r or "Sin datos" in r or r.startswith("Error"):
            return False, "vacío — el coach no recordará nada personal"
        return True, f"{r.count('- ')} notas recuperadas"

    @comprobar("Historial narrativo")
    def _narrativo():
        from services.llm_service import consultar_memoria_historica_atleta
        r = consultar_memoria_historica_atleta(numero, "mesociclo anterior")
        if not r or "No hay registros" in r or r.startswith("Error"):
            return False, "vacío — corre scripts/sembrar_conversacion.py"
        return True, f"{r.count('- ')} bitácoras recuperadas"

    @comprobar("Hilo de conversación")
    def _hilo():
        from services.llm_service import UMBRAL_COMPRESION
        ruta = CARPETA_DATA / f"historial_{numero}.json"
        if not ruta.exists():
            return False, "sin pasado: el coach saludará como si fuera el primer día"
        msgs = json.loads(ruta.read_text(encoding="utf-8"))
        if msgs and msgs[0].get("role") == "system":
            return False, ("empieza por un mensaje 'system' congelado: el orquestador NO lo "
                           "sustituirá y la demo correrá con un prompt viejo")
        if len(msgs) >= UMBRAL_COMPRESION:
            return False, (f"{len(msgs)} mensajes ≥ UMBRAL_COMPRESION ({UMBRAL_COMPRESION}): "
                           f"el primer turno pagará una compresión de ~8 s")
        return True, f"{len(msgs)} mensajes de pasado"

    # ── 4. Multimodal e infraestructura ─────────────────────────────────────
    print(f"\n{GRIS}  MULTIMODAL E INFRAESTRUCTURA{FIN}")

    @comprobar("Modelos de visión en disco")
    def _vision():
        faltan = [str(p.relative_to(RAIZ)) for p in
                  (RAIZ / "Entrenamiento_modelo_media" / "clasificador_ejercicios.pkl",
                   RAIZ / "Entrenamiento_modelo_media" / "columnas_modelo.pkl",
                   RAIZ / "models" / "best.pt") if not p.exists()]
        if faltan:
            return False, f"faltan: {', '.join(faltan)}"
        return True, "clasificador + columnas + pesos YOLO"

    @comprobar("Vídeos de muestra", critico=False)
    def _videos():
        base = CARPETA_DATA / "dataset_recoleccion"
        if not base.exists():
            return False, "no hay carpeta dataset_recoleccion"
        por_ej = {d.name: len(list(d.glob("*.mp4"))) for d in base.iterdir() if d.is_dir()}
        if not any(por_ej.values()):
            return False, "sin vídeos que enseñar"
        return True, " · ".join(f"{k}: {v}" for k, v in sorted(por_ej.items()))

    @comprobar("Ollama y modelos")
    def _ollama():
        import ollama
        nombres = [m.get("model") or m.get("name") for m in ollama.list().get("models", [])]
        faltan = [m for m in ("qwen3.6:27b", "nomic-embed-text")
                  if not any(str(n).startswith(m.split(":")[0]) for n in nombres)]
        if faltan:
            return False, f"faltan modelos: {', '.join(faltan)}"
        return True, f"{len(nombres)} modelos disponibles"

    @comprobar("Credenciales de WhatsApp", critico=False)
    def _credenciales():
        from core.config import settings
        faltan = settings.faltantes()
        if faltan:
            return False, f"ausentes: {', '.join(faltan)} — la demo tendrá que ir por simulador"
        return True, "webhook listo para mensajes reales"

    # ── Veredicto ───────────────────────────────────────────────────────────
    criticos_mal = [n for n, ok, crit, _ in _resultados if not ok and crit]
    avisos = [n for n, ok, crit, _ in _resultados if not ok and not crit]

    print(f"\n{'=' * 74}")
    if criticos_mal:
        print(f"  {ROJO}NO LISTA{FIN} — {len(criticos_mal)} comprobación(es) crítica(s) fallan:")
        for n in criticos_mal:
            print(f"     · {n}")
    else:
        print(f"  {VERDE}LISTA PARA DEMOSTRAR{FIN} — {len(_resultados) - len(avisos)}/{len(_resultados)} comprobaciones en verde")
    if avisos:
        print(f"  {AMARILLO}Avisos no bloqueantes:{FIN} {', '.join(avisos)}")
    print(f"{'=' * 74}\n")
    return 1 if criticos_mal else 0


if __name__ == "__main__":
    sys.exit(main())
