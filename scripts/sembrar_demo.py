# -*- coding: utf-8 -*-
"""
sembrar_demo.py — crea un atleta con MESES de historial creíble, en segundos.

Para qué existe
---------------
Media docena de funciones del sistema solo tienen algo que decir cuando hay
historial detrás, y ese historial tarda meses en construirse entrenando de
verdad:

    predecir_carga_optima   BayesianRidge necesita >= 2 sesiones con tríada
                            completa; con menos devuelve 'datos_insuficientes'
    analizar_mrv            compara ventanas de 7 y 28 días
    consultar_progreso      necesita una serie temporal de e1RM
    enviar_grafica_progreso sin puntos no hay gráfica
    evaluar_nivel_fuerza    necesita marcas en los tres básicos
    ACWR / readiness        carga aguda vs crónica: 28 días mínimo

En una demostración no se puede esperar cuatro meses. Este script genera esos
cuatro meses con una progresión REALISTA, no una recta: mesociclos de cuatro
semanas con su descarga, ruido en el RPE, un estancamiento deliberado en banca
(para que el análisis de MRV tenga algo que decir) y molestias puntuales.

Todo lo que escribe es exactamente lo que escribiría el sistema en producción:
las mismas tablas, los mismos archivos y las mismas claves canónicas.

Uso
---
    python3 scripts/sembrar_demo.py                      # número de demo por defecto
    python3 scripts/sembrar_demo.py 5215500000123
    python3 scripts/sembrar_demo.py --semanas 20
    python3 scripts/sembrar_demo.py --limpiar            # borra y vuelve a sembrar

Después:
    python3 scripts/verificar_demo.py                    # comprueba que todo responde
"""
from __future__ import annotations

import argparse
import json
import random
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

# Semilla fija: dos ejecuciones dan el MISMO atleta. En una demo no puede salir
# un número distinto cada vez que se reinicia.
SEMILLA = 20260731

# ==============================================================================
# EL ATLETA
# ==============================================================================
PERFIL = {
    "peso_kg": 84.0,
    "altura_m": 1.75,
    "edad": 26,
    "genero": "hombre",
    "porcentaje_grasa": 17.0,
    "dias_semana": 5,
    "meta": "powerbuilding",
}

# Marcas de partida (hace `semanas` semanas) y su progresión semanal esperada.
# 'freq' = veces por semana que aparece. 'estanca' corta la progresión a mitad
# del ciclo: es lo que hace que analizar_mrv y consultar_progreso tengan algo
# interesante que decir en la demo.
EJERCICIOS = [
    # nombre del plan            e1RM inicial  reps    freq  estanca
    ("Press Banco Plano",        112.0,        (3, 8),  2,   True),
    ("Sentadillas",              145.0,        (3, 8),  2,   False),
    ("Peso Muerto",              180.0,        (3, 6),  1,   False),
    ("Dominadas Lastradas",       22.0,        (5, 10), 2,   False),
    ("Remo en Maquina",           78.0,        (8, 12), 2,   False),
    ("Press Inclinado con Mancuernas", 34.0,   (8, 12), 1,   False),
    ("Prensa de Piernas",        210.0,        (8, 12), 1,   False),
    ("Curl Femoral",              52.0,        (10, 12), 1,  False),
    ("Extension de Cuadriceps",   60.0,        (10, 12), 1,  False),
    ("Curl de Biceps con Barra",  42.0,        (8, 12), 2,   False),
    ("Extension de Triceps Katana", 32.0,      (10, 12), 2,  False),
    ("Vuelos Laterales",          14.0,        (12, 15), 2,  False),
]

# Notas para el expediente vectorial: lo que un coach recuerda de su atleta.
EXPEDIENTE = [
    "El atleta tuvo molestia en el hombro derecho durante el press de banca en "
    "mayo de 2026; se resolvió bajando el agarre y ya no le molesta.",
    "Entrena en un gimnasio con discos de 2.5 kg como incremento mínimo y "
    "dispone de rack, mancuernas hasta 40 kg, prensa y poleas.",
    "Prefiere entrenar por la mañana temprano, entre las 6 y las 8.",
    "No le gustan las zancadas: prefiere prensa o hack para el trabajo de "
    "cuádriceps accesorio.",
    "Su objetivo declarado es powerbuilding: ganar músculo sin perder fuerza en "
    "los tres básicos.",
    "Reportó dolor lumbar leve tras una sesión de peso muerto pesado en junio de "
    "2026; se resolvió con trabajo de movilidad y ya no aparece.",
]

_GRUPO_ACCESORIO = {"Curl de Biceps con Barra", "Extension de Triceps Katana",
                    "Vuelos Laterales", "Curl Femoral", "Extension de Cuadriceps"}

# Accesorios que el motor de planificación mete en el plan pero que no están en
# EJERCICIOS. Sin una marca guardada, el orquestador no puede convertir el
# "70 % de 1RM" del plan en kilos y los imprime como *peso por calibrar*: en el
# plan generado para el atleta de demo eran 8 de 18 ejercicios, casi la mitad de
# la rutina saliendo sin número. Se les guarda una marca creíble, pero NO se les
# generan series: así aparecen con kilos sin inflar el volumen semanal ni
# falsear lo que ve `analizar_mrv`.
MARCAS_ACCESORIAS = {
    "Press Pecho en Maquina": 95.0,
    "Curl de Biceps en Polea": 38.0,
    "Crunch Abdominal": 45.0,
    "Sentadilla en Maquina Hack": 160.0,
    "Peso Muerto Rumano con Mancuernas": 40.0,
    "Abductor de Piernas": 70.0,
    "Aductor de Piernas": 65.0,
    "Extension de Tobillo": 110.0,
}


def _e1rm(peso: float, reps: int, rpe: float) -> float:
    """Epley ajustado por RIR — la misma fórmula que usa el motor."""
    rir = max(0.0, 10.0 - rpe)
    return round(peso * (1 + (reps + rir) / 30.0), 1)


def _peso_para(e1rm_objetivo: float, reps: int, rpe: float, incremento: float = 2.5) -> float:
    """Invierte Epley: con qué carga toca ese e1RM a esas reps y ese RPE."""
    rir = max(0.0, 10.0 - rpe)
    bruto = e1rm_objetivo / (1 + (reps + rir) / 30.0)
    return max(incremento, round(bruto / incremento) * incremento)


# ==============================================================================
# GENERACIÓN
# ==============================================================================
def generar_series(semanas: int, rnd: random.Random) -> list[dict]:
    """Historial completo, sesión a sesión, hacia atrás desde AYER.

    El anclaje es el final, no el principio. Anclarlo al principio
    (`inicio = hoy - semanas` y sumar hacia delante) dejaba la última semana a
    medias: las sesiones se acababan tres o cuatro días antes de hoy y la
    ventana aguda de siete días quedaba medio vacía. Con eso, `analizar_mrv`
    comparaba 38 sets recientes contra 66 previos y `acwr_usuario` devolvía 0.39
    — el retrato de un atleta que ha dejado de entrenar, justo lo contrario de
    lo que se quiere enseñar en una demostración.

    Se ancla a AYER y no a hoy porque la sesión de hoy es la que el atleta va a
    reportar EN VIVO durante la demostración: si ya estuviera en el diario, el
    guardia de series duplicadas rechazaría el registro.
    """
    series: list[dict] = []
    ayer = (datetime.now() - timedelta(days=1)).replace(hour=7, minute=30, second=0, microsecond=0)
    # Última sesión = ayer; se retrocede semana a semana desde ahí.
    ultima_semana_inicio = ayer - timedelta(days=4)

    for semana in range(semanas):
        # Mesociclos de 4 semanas: la cuarta es DESCARGA (menos volumen y RPE).
        #
        # La fase se cuenta desde el FINAL, no desde el principio. Contándola
        # desde el principio con 16 semanas, la última caía en descarga
        # (15 % 4 == 3) y el atleta llegaba al día de la demostración recién
        # descargado: `analizar_mrv` veía 38 sets duros contra 66 y dictaminaba
        # "estable", así que cuando el atleta dijera "estoy reventado, ¿descargo?"
        # el sistema le llevaría la contraria. Anclando la fase al final, la
        # última semana es la 3.ª de acumulación —el pico de carga, justo antes
        # de tocar descarga—, que es el escenario donde el análisis de volumen
        # recuperable tiene algo que decir.
        desde_el_final = semanas - 1 - semana
        semana_del_ciclo = (2 - desde_el_final) % 4
        descarga = semana_del_ciclo == 3
        progreso = semana / max(1, semanas - 1)          # 0 -> 1
        # `semana` va de más antigua (0) a más reciente (semanas-1).
        base_semana = ultima_semana_inicio - timedelta(weeks=(semanas - 1 - semana))

        for idx_ej, (nombre, e1_inicial, rango_reps, freq, estanca) in enumerate(EJERCICIOS):
            # Progresión: +12 % en el periodo para básicos, +18 % en accesorios
            # (que suben más rápido porque parten más lejos de su techo).
            techo = 0.18 if nombre in _GRUPO_ACCESORIO else 0.12
            avance = progreso
            if estanca and progreso > 0.55:
                # Estancamiento deliberado: deja de subir a mitad de ciclo.
                avance = 0.55
            e1_semana = e1_inicial * (1 + techo * avance)

            for repeticion in range(freq):
                # Día de la semana: se reparten sin solaparse.
                # `idx_ej` y no `hash(nombre)`: hash() de str se aleatoriza por
                # proceso (PYTHONHASHSEED), así que el reparto cambiaba en cada
                # ejecución y el atleta "de semilla fija" salía distinto cada
                # vez — con otras marcas y otro estado de fatiga. En una demo
                # eso significa que los números del ensayo no son los del día.
                dia = (repeticion * 2 + (idx_ej % 2)) % 5
                fecha = base_semana + timedelta(days=dia, minutes=rnd.randint(0, 90))
                if fecha > ayer:
                    continue

                # El volumen sube dentro del mesociclo y se desploma en la
                # descarga. Sin esta rampa todas las semanas de carga eran
                # iguales y la ventana de 7 días nunca se distinguía de la de
                # 28: `analizar_mrv` no tenía contraste que medir.
                if descarga:
                    series_del_dia = 2
                else:
                    series_del_dia = rnd.choice([[3, 3], [3, 4], [4, 5]][semana_del_ciclo])
                reps_obj = rnd.randint(*rango_reps)
                if descarga:
                    rpe_base = 7.0
                else:
                    # La 3.ª semana se entrena más cerca del fallo: es la que
                    # justifica la descarga que viene después.
                    rpe_base = rnd.choice([[7.5, 7.5, 8.0], [7.5, 8.0, 8.0],
                                           [8.0, 8.5, 8.5]][semana_del_ciclo])

                for n_serie in range(series_del_dia):
                    # El RPE sube dentro de la sesión: la cuarta serie cuesta más.
                    rpe = min(10.0, rpe_base + 0.5 * n_serie + rnd.choice([0, 0, 0.5]))
                    reps = max(1, reps_obj - (1 if n_serie >= 2 and rpe >= 9 else 0))
                    peso = _peso_para(e1_semana, reps, rpe)
                    # Ruido de gimnasio: el disco que había, el día flojo.
                    peso = max(2.5, peso + rnd.choice([0, 0, 0, -2.5, 2.5]))

                    dolor = "ninguno"
                    if nombre == "Press Banco Plano" and 0.30 < progreso < 0.42 and rnd.random() < 0.25:
                        dolor = "hombro derecho leve"
                    elif nombre == "Peso Muerto" and 0.55 < progreso < 0.62 and rnd.random() < 0.3:
                        dolor = "lumbar leve"

                    series.append({
                        "fecha": (fecha + timedelta(minutes=n_serie * 4)).isoformat(" ", "seconds"),
                        "ejercicio": nombre,
                        "peso": float(peso),
                        "reps": int(reps),
                        "rpe": float(rpe),
                        "dolor": dolor,
                        # VBT solo en los básicos: es donde el atleta graba vídeo.
                        "vbt": (round(rnd.uniform(0.32, 0.55), 3)
                                if nombre in ("Press Banco Plano", "Sentadillas") and rnd.random() < 0.18
                                else None),
                    })

    series.sort(key=lambda s: s["fecha"])
    return series


def generar_readiness(semanas: int, rnd: random.Random) -> list[dict]:
    """Un check-in matutino por día, con una semana mala a propósito."""
    filas = []
    hoy = datetime.now().date()
    for atras in range(semanas * 7):
        dia = hoy - timedelta(days=atras)
        mala = 30 < atras < 38          # semana de mal sueño: baja el readiness
        sueno = round(rnd.uniform(5.0, 6.2) if mala else rnd.uniform(6.5, 8.5), 1)
        calidad = rnd.randint(2, 3) if mala else rnd.randint(3, 5)
        estres = rnd.randint(4, 5) if mala else rnd.randint(1, 3)
        dolor = rnd.randint(2, 4) if mala else rnd.randint(1, 2)
        score = max(0.0, min(1.0, round(
            (sueno / 8.0) * 0.4 + (calidad / 5.0) * 0.3
            + ((6 - estres) / 5.0) * 0.15 + ((5 - dolor) / 4.0) * 0.15, 2)))
        filas.append({"fecha": dia.isoformat(), "sueno_horas": sueno,
                      "sueno_calidad": calidad, "estres": estres,
                      "dolor": dolor, "score": score})
    return filas


def generar_peso_corporal(semanas: int, rnd: random.Random) -> list[dict]:
    """Tendencia lenta al alza, como un powerbuilding en superávit ligero."""
    filas = []
    hoy = datetime.now().date()
    inicial = PERFIL["peso_kg"] - 3.2
    for semana in range(semanas + 1):
        dia = hoy - timedelta(weeks=(semanas - semana))
        peso = round(inicial + (3.2 * semana / semanas) + rnd.uniform(-0.4, 0.4), 1)
        filas.append({"fecha": dia.isoformat(), "peso": peso})
    return filas


# ==============================================================================
# ESCRITURA
# ==============================================================================
def escribir(numero: str, series: list[dict], readiness: list[dict],
             pesos: list[dict], semanas: int) -> dict:
    from services.repositorio_atleta import repositorio

    resumen: dict = {}

    # --- perfil (SQLite, vía el repositorio real) ---------------------------
    repositorio.actualizar_perfil(numero, **PERFIL)
    resumen["perfil"] = PERFIL

    # --- diario de series ---------------------------------------------------
    with sqlite3.connect(DB_TELEMETRIA) as con:
        con.execute("DELETE FROM series_log WHERE numero_usuario = ?", (numero,))
        con.executemany(
            "INSERT INTO series_log (numero_usuario, fecha_hora, ejercicio, peso_kg, "
            "repeticiones, rpe_reportado, vbt_velocidad_media, dolor_articular) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(numero, s["fecha"], s["ejercicio"], s["peso"], s["reps"],
              s["rpe"], s["vbt"], s["dolor"]) for s in series])
        con.execute("DELETE FROM readiness WHERE numero_usuario = ?", (numero,))
        con.executemany(
            "INSERT INTO readiness (numero_usuario, fecha, sueno_horas, sueno_calidad, "
            "estres, dolor, score, fuente) VALUES (?,?,?,?,?,?,?,?)",
            [(numero, r["fecha"], r["sueno_horas"], r["sueno_calidad"], r["estres"],
              r["dolor"], r["score"], "demo") for r in readiness])
        con.commit()
    resumen["series"] = len(series)
    resumen["readiness"] = len(readiness)

    # --- marcas de 1RM: la MEJOR de cada ejercicio + su historial fechado ----
    mejores: dict[str, tuple[float, dict]] = {}
    for s in series:
        if s["reps"] > 12:
            continue
        e1 = _e1rm(s["peso"], s["reps"], s["rpe"])
        if e1 > mejores.get(s["ejercicio"], (0, None))[0]:
            mejores[s["ejercicio"]] = (e1, s)

    historial_rm = []
    for nombre, (e1, s) in mejores.items():
        repositorio.guardar_rm(numero, nombre, e1, peso_origen=s["peso"],
                               reps_origen=s["reps"], fuente="demo")
        historial_rm.append({"fecha": s["fecha"][:16], "ejercicio": nombre.lower(),
                             "rm": e1, "peso_origen": s["peso"]})
    # Puntos intermedios: sin ellos la gráfica de progreso es un solo punto.
    for nombre, (_e1, _s) in mejores.items():
        del_ej = [s for s in series if s["ejercicio"] == nombre and s["reps"] <= 12]
        for s in del_ej[::max(1, len(del_ej) // 12)]:
            historial_rm.append({"fecha": s["fecha"][:16], "ejercicio": nombre.lower(),
                                 "rm": _e1rm(s["peso"], s["reps"], s["rpe"]),
                                 "peso_origen": s["peso"]})
    historial_rm.sort(key=lambda h: h["fecha"])
    (CARPETA_DATA / f"historial_rm_{numero}.json").write_text(
        json.dumps(historial_rm, indent=2, ensure_ascii=False), encoding="utf-8")
    resumen["marcas"] = {k: v[0] for k, v in mejores.items()}
    resumen["puntos_progreso"] = len(historial_rm)

    # --- marcas de los accesorios que el plan usa pero no se entrenan aquí ---
    # Solo la marca, sin series ni puntos de progreso: existen para que el
    # orquestador pueda resolver el "% de 1RM" del plan a kilos de verdad.
    for nombre, e1 in MARCAS_ACCESORIAS.items():
        repositorio.guardar_rm(numero, nombre, e1, fuente="demo")
    resumen["marcas_accesorias"] = len(MARCAS_ACCESORIAS)

    # --- alias canónicos: 'Sentadillas' -> 'sentadilla' ---------------------
    # `repositorio.guardar_rm` normaliza el nombre (minúsculas y espacios) pero
    # NO lo canoniza, así que la marca queda bajo 'sentadillas'. Al leer la
    # rutina eso da igual, porque `_rm_guardado_de` conoce los alias; pero
    # `evaluar_nivel_fuerza` hace `rms.get("sentadilla")` a pelo
    # (llm_service.py:7052) y se encuentra un 0. El resultado era que este
    # atleta —204 kg de peso muerto y 164 de sentadilla— salía diagnosticado
    # como PRINCIPIANTE con "datos insuficientes" en mitad de la demostración.
    # Se guarda la marca también bajo su clave canónica cuando difiere.
    try:
        from core.ejercicios import canonizar
    except Exception:
        canonizar = None
    alias = 0
    if canonizar is not None:
        for nombre, (e1, s) in mejores.items():
            canon = canonizar(nombre)
            if canon and canon != nombre.strip().lower():
                repositorio.guardar_rm(numero, canon, e1, peso_origen=s["peso"],
                                       reps_origen=s["reps"], fuente="demo")
                alias += 1
    resumen["alias_canonicos"] = alias

    # --- peso corporal ------------------------------------------------------
    (CARPETA_DATA / f"peso_corporal_{numero}.json").write_text(
        json.dumps(pesos, indent=2, ensure_ascii=False), encoding="utf-8")
    resumen["peso_corporal"] = len(pesos)

    # --- estado del calendario ---------------------------------------------
    ruta_estado = CARPETA_DATA / "estado_usuarios.json"
    try:
        estados = json.loads(ruta_estado.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        estados = {}
    estados[numero] = {
        "semana": 1, "dia": 1,
        "fecha_programada": datetime.now().strftime("%Y-%m-%d"),
        "ultima_fecha_completada": "",
        "estado_generacion": "lista",
        "onboarding_activo": False, "campo_pendiente": "",
        "cursor_semana": 1, "cursor_dia": 0,
        "bienvenida_enviada": datetime.now().timestamp(),
    }
    ruta_estado.write_text(json.dumps(estados, indent=4, ensure_ascii=False), encoding="utf-8")

    # --- expediente vectorial ----------------------------------------------
    try:
        from services.llm_service import guardar_dato_en_expediente
        for nota in EXPEDIENTE:
            guardar_dato_en_expediente(numero, nota)
        resumen["expediente"] = len(EXPEDIENTE)
    except Exception as e:
        resumen["expediente"] = f"ERROR: {type(e).__name__}: {e}"

    return resumen


def generar_plan(numero: str) -> str:
    """Lanza el motor de planificación REAL con el perfil sembrado."""
    try:
        from services.conexion_entrenamiento_llm_orquestacion import recibir_peticion
        from services.repositorio_atleta import repositorio
        rms = repositorio.obtener_rms(numero)
        datos = {
            "meta": PERFIL["meta"], "nivel": "avanzado",
            "dias": PERFIL["dias_semana"], "peso_kg": PERFIL["peso_kg"],
            "porcentaje_grasa": PERFIL["porcentaje_grasa"],
            "rm_banca_kg": rms.get("press banco plano") or rms.get("press banca"),
            "rm_sentadilla_kg": rms.get("sentadilla"),
            "rm_peso_muerto_kg": rms.get("peso muerto"),
            "rm_dominadas_kg": rms.get("dominadas"),
            "contexto_usuario": ("Atleta de powerbuilding con 4 meses de historial "
                                 "registrado. Molestia previa en hombro derecho, ya resuelta."),
            "tipo_periodizacion": "DUP",
        }
        recibir_peticion(datos, numero)
        carpeta = RAIZ / "resources" / "rutinas_generadas" / numero
        archivos = sorted(p.name for p in carpeta.glob("*.json")) if carpeta.exists() else []
        return f"{len(archivos)} mesociclos: {', '.join(archivos)}" if archivos else "sin archivos"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Siembra un atleta de demostración.")
    ap.add_argument("numero", nargs="?", default=NUMERO_DEMO)
    ap.add_argument("--semanas", type=int, default=16,
                    help="semanas de historial (por defecto 16 = 4 mesociclos)")
    ap.add_argument("--limpiar", action="store_true",
                    help="borra al atleta antes de sembrarlo")
    ap.add_argument("--sin-plan", action="store_true",
                    help="no ejecutar el motor de planificación (más rápido)")
    args = ap.parse_args()

    numero = args.numero.strip()
    rnd = random.Random(SEMILLA)

    print(f"\n{'=' * 70}")
    print(f"  SEMBRANDO ATLETA DE DEMOSTRACIÓN — {numero}")
    print(f"  {args.semanas} semanas de historial ({args.semanas // 4} mesociclos)")
    print(f"{'=' * 70}\n")

    if args.limpiar:
        from scripts.reset_usuario import reset_usuario
        reset_usuario(numero)
        print()

    print("  · generando series...", end=" ", flush=True)
    series = generar_series(args.semanas, rnd)
    print(f"{len(series)} series")

    print("  · generando readiness...", end=" ", flush=True)
    readiness = generar_readiness(args.semanas, rnd)
    print(f"{len(readiness)} días")

    print("  · generando peso corporal...", end=" ", flush=True)
    pesos = generar_peso_corporal(args.semanas, rnd)
    print(f"{len(pesos)} registros")

    print("  · escribiendo en base de datos y expediente...", flush=True)
    resumen = escribir(numero, series, readiness, pesos, args.semanas)

    if not args.sin_plan:
        print("  · ejecutando el motor de planificación real...", flush=True)
        resumen["plan"] = generar_plan(numero)

    print(f"\n{'=' * 70}")
    print("  LISTO. El atleta tiene:")
    print(f"{'=' * 70}")
    print(f"   {resumen['series']:>5} series en el diario")
    print(f"   {resumen['readiness']:>5} check-ins de readiness")
    print(f"   {resumen['peso_corporal']:>5} registros de peso corporal")
    print(f"   {resumen['puntos_progreso']:>5} puntos de progreso de 1RM")
    print(f"   {resumen.get('expediente', 0):>5} notas en el expediente vectorial")
    if "plan" in resumen:
        print(f"         plan: {resumen['plan']}")
    print("\n   Marcas actuales:")
    for ej, e1 in sorted(resumen["marcas"].items(), key=lambda kv: -kv[1]):
        print(f"     · {ej:<34} 1RM {e1:>6.1f} kg")
    print(f"\n{'=' * 70}")
    print("  Siguiente paso:  python3 scripts/verificar_demo.py " +
          (numero if numero != NUMERO_DEMO else ""))
    print(f"{'=' * 70}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
