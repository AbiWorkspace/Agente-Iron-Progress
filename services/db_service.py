import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

# Rutas dinámicas (igual que en tu orquestador)
ruta_script_actual = os.path.dirname(os.path.abspath(__file__))
ruta_raiz          = os.path.dirname(ruta_script_actual)
carpeta_data       = os.path.join(ruta_raiz, "data")
os.makedirs(carpeta_data, exist_ok=True)

ruta_db = os.path.join(carpeta_data, "iron_progress_telemetria.db")

# ==============================================================================
# ESQUEMA: SE APLICA UNA SOLA VEZ POR PROCESO
# ==============================================================================
# Antes `inicializar_db()` se llamaba al principio de CADA operación: abría
# conexión, lanzaba 4 CREATE TABLE, 7 ALTER TABLE que fallaban a propósito
# (siete excepciones capturadas por llamada), 4 CREATE INDEX y un print. Eso
# corría dos veces por turno de chat y en cada guardado de serie. El esquema no
# cambia en caliente: se aplica una vez y se recuerda con este flag.
_esquema_listo = False
_lock_esquema = threading.Lock()


@contextmanager
def conexion_db(solo_lectura: bool = False):
    """Conexión de vida corta con cierre garantizado.

    `with sqlite3.connect(...) as con:` NO cierra la conexión (solo hace commit
    o rollback): el descriptor queda abierto hasta que pase el recolector. En un
    servidor de larga vida eso termina agotando descriptores de archivo.
    """
    con = sqlite3.connect(ruta_db, timeout=15)
    try:
        yield con
        if not solo_lectura:
            con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def inicializar_db(forzar: bool = False):
    """Crea tablas e índices. Idempotente y, desde v9, de coste cero a partir de
    la segunda llamada (el esquema ya está aplicado en este proceso)."""
    global _esquema_listo
    if _esquema_listo and not forzar:
        return

    with _lock_esquema:
        if _esquema_listo and not forzar:
            return

        conexion = sqlite3.connect(ruta_db, timeout=15)
        try:
            cursor = conexion.cursor()

            # WAL: lectores y escritor concurrentes sin bloquearse entre sí. El
            # webhook escribe series mientras el orquestador lee el dashboard, y
            # en el modo por defecto (journal DELETE) eso provoca 'database is
            # locked' bajo carga.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")

            # 1. Creación de la tabla principal
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS series_log (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    numero_usuario        TEXT    NOT NULL,
                    fecha_hora            TEXT    NOT NULL,
                    ejercicio             TEXT    NOT NULL,
                    peso_kg               REAL    NOT NULL,
                    repeticiones          INTEGER NOT NULL,
                    rpe_reportado         REAL,
                    vbt_velocidad_media   REAL,
                    vbt_velocidad_pico    REAL,
                    vbt_perdida_velocidad REAL,
                    rom_cm                REAL,
                    dolor_articular       TEXT,
                    calidad_tecnica       TEXT
                )
            ''')

            # 2. ESCUDO DE MIGRACIÓN: bases viejas reciben las columnas nuevas de VBT.
            #    Se consulta PRAGMA table_info en vez de lanzar ALTER a ciegas y
            #    capturar la excepción: una comprobación en lugar de siete errores.
            columnas_existentes = {fila[1] for fila in cursor.execute("PRAGMA table_info(series_log)")}
            nuevas_columnas = {
                "rpe_reportado": "REAL",
                "vbt_velocidad_media": "REAL",
                "vbt_velocidad_pico": "REAL",
                "vbt_perdida_velocidad": "REAL",
                "rom_cm": "REAL",
                "dolor_articular": "TEXT",
                "calidad_tecnica": "TEXT",
            }
            for columna, tipo in nuevas_columnas.items():
                if columna not in columnas_existentes:
                    cursor.execute(f"ALTER TABLE series_log ADD COLUMN {columna} {tipo}")
                    print(f"[SQL SERVICE]: + columna series_log.{columna}")

            # 3. Índice para búsquedas rápidas del historial del atleta
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_usuario_fecha
                ON series_log(numero_usuario, fecha_hora)
            ''')

            # 4. Control Anti-Spam (para el agente proactivo)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS alertas_enviadas (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    numero_usuario TEXT    NOT NULL,
                    tipo           TEXT    NOT NULL,
                    enviada_en     TEXT    DEFAULT (datetime('now')),
                    semana_iso     TEXT
                )
            ''')

            # 5. Índice para el escudo anti-spam
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_alertas_user_semana
                ON alertas_enviadas(numero_usuario, semana_iso)
            ''')

            # 6. TABLAS V9: readiness matutino y eventos de autorregulación.
            #    Antes solo las creaba scripts/migracion_v9.py, que se ejecuta A MANO: en una
            #    instalación nueva, el primer botón del readiness moría con
            #    "no such table: readiness" (motor_predictivo.guardar_readiness) y el atleta
            #    se quedaba sin respuesta. Mismo DDL que la migración, e igual de idempotente.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS readiness (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    numero_usuario TEXT NOT NULL,
                    fecha          TEXT NOT NULL,              -- YYYY-MM-DD
                    sueno_horas    REAL,
                    sueno_calidad  INTEGER,                    -- 1-5
                    estres         INTEGER,                    -- 1-5 (5 = frito)
                    dolor          TEXT,                       -- zona o 'ninguno'
                    score          REAL NOT NULL,              -- 0.0-1.0 consolidado
                    fuente         TEXT DEFAULT 'botones',     -- botones | texto | inferido
                    UNIQUE (numero_usuario, fecha)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS eventos_autorregulacion (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    numero_usuario TEXT NOT NULL,
                    fecha          TEXT NOT NULL,   -- ISO datetime
                    tipo           TEXT NOT NULL,   -- dup_ajuste | dup_descarga | mrv_alerta
                    ejercicio      TEXT,
                    detalle_json   TEXT             -- payload completo del evento
                )
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_eventos_u_f
                ON eventos_autorregulacion(numero_usuario, fecha)
            ''')

            # 7. Índice compuesto que alimenta las consultas del motor predictivo
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_series_u_e_f
                ON series_log(numero_usuario, ejercicio, fecha_hora)
            ''')

            conexion.commit()
            _esquema_listo = True
            print("[SQL SERVICE]: Base de datos de telemetría lista y conectada.")
        finally:
            conexion.close()


def guardar_serie_sql(numero_usuario: str, datos: dict) -> bool:
    """Guarda una serie completada. Siempre filtra por numero_usuario."""
    try:
        inicializar_db()
        fecha_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with conexion_db() as conexion:
            conexion.execute('''
                INSERT INTO series_log (
                    numero_usuario, fecha_hora, ejercicio, peso_kg, repeticiones,
                    rpe_reportado, vbt_velocidad_media, vbt_velocidad_pico,
                    vbt_perdida_velocidad, rom_cm, dolor_articular, calidad_tecnica
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                str(numero_usuario),  # Forzamos string: evita bugs si llega un int
                fecha_hora,
                # El MISMO ejercicio entraba con dos grafías según quién lo
                # escribiera: el interceptor determinista guardaba "press banco
                # plano" (clave canónica) y el handler del modelo "Press Banco
                # Plano" (catálogo). En el dashboard aparecían como si fueran dos
                # ejercicios distintos. Se normaliza al escribir, como ya hace
                # marcas_rm; para mostrarlo se recapitaliza.
                str(datos.get("ejercicio") or "").strip().lower() or None,
                datos.get("peso_kg", 0),
                datos.get("repeticiones", 0),
                datos.get("rpe_reportado"),
                datos.get("vbt_velocidad_media"),
                datos.get("vbt_velocidad_pico"),
                datos.get("vbt_perdida_velocidad"),
                datos.get("rom_cm"),
                datos.get("dolor_articular"),
                datos.get("calidad_tecnica"),
            ))

        print(f"[SQL] Serie guardada: {numero_usuario} | {datos.get('ejercicio')}")
        return True

    except Exception as e:
        print(f"[ERROR SQL] Fallo al guardar serie: {e}")
        return False


def _ultima_serie_id(conexion, numero_usuario: str, ejercicio: str | None = None):
    sql = "SELECT id, ejercicio, peso_kg, repeticiones, rpe_reportado FROM series_log WHERE numero_usuario = ?"
    params: list = [str(numero_usuario)]
    if ejercicio:
        sql += " AND lower(ejercicio) = lower(?)"
        params.append(ejercicio)
    sql += " ORDER BY id DESC LIMIT 1"
    return conexion.execute(sql, tuple(params)).fetchone()


def obtener_ultima_serie(numero_usuario: str, ejercicio: str | None = None) -> dict | None:
    """La última serie que este atleta apuntó, o None."""
    try:
        inicializar_db()
        with conexion_db(solo_lectura=True) as conexion:
            fila = _ultima_serie_id(conexion, numero_usuario, ejercicio)
        if not fila:
            return None
        sid, ej, peso, reps, rpe = fila
        return {"id": sid, "ejercicio": ej, "peso_kg": peso,
                "repeticiones": reps, "rpe_reportado": rpe}
    except Exception as e:
        print(f"[ERROR SQL] Fallo al leer la última serie: {e}")
        return None


def corregir_ultima_serie(numero_usuario: str, cambios: dict,
                          ejercicio: str | None = None) -> dict | None:
    """Rectifica la última serie registrada. Devuelve {antes, despues} o None.

    El atleta se corrige constantemente ("perdón, eran 90 no 100") y hasta v13 no
    había forma de hacerlo: el coach contestaba "lo corrijo en tu expediente" y
    el dato equivocado se quedaba en el diario contaminando el 1RM estimado.
    """
    campos = {"peso_kg": "peso_kg", "repeticiones": "repeticiones", "rpe_reportado": "rpe_reportado"}
    sets = {k: v for k, v in cambios.items() if k in campos and v is not None}
    if not sets:
        return None
    try:
        inicializar_db()
        with conexion_db() as conexion:
            fila = _ultima_serie_id(conexion, numero_usuario, ejercicio)
            if not fila:
                return None
            sid, ej, peso, reps, rpe = fila
            asignaciones = ", ".join(f"{c} = ?" for c in sets)
            conexion.execute(f"UPDATE series_log SET {asignaciones} WHERE id = ? AND numero_usuario = ?",
                             (*sets.values(), sid, str(numero_usuario)))
        antes = {"ejercicio": ej, "peso_kg": peso, "repeticiones": reps, "rpe_reportado": rpe}
        despues = {**antes, **sets}
        print(f"[SQL] Serie {sid} corregida: {antes} → {despues}")
        return {"id": sid, "antes": antes, "despues": despues}
    except Exception as e:
        print(f"[ERROR SQL] Fallo al corregir serie: {e}")
        return None


def borrar_ultima_serie(numero_usuario: str, ejercicio: str | None = None) -> dict | None:
    """Elimina la última serie registrada ('olvida esa, no la hice')."""
    try:
        inicializar_db()
        with conexion_db() as conexion:
            fila = _ultima_serie_id(conexion, numero_usuario, ejercicio)
            if not fila:
                return None
            sid, ej, peso, reps, rpe = fila
            conexion.execute("DELETE FROM series_log WHERE id = ? AND numero_usuario = ?",
                             (sid, str(numero_usuario)))
        borrada = {"ejercicio": ej, "peso_kg": peso, "repeticiones": reps, "rpe_reportado": rpe}
        print(f"[SQL] Serie {sid} borrada: {borrada}")
        return {"id": sid, "borrada": borrada}
    except Exception as e:
        print(f"[ERROR SQL] Fallo al borrar serie: {e}")
        return None


# Una sesión tiene 15-25 series. Con LIMIT 5, a la pregunta más natural del
# mundo —"¿qué llevo hoy?"— el coach solo veía las cinco últimas y respondía de
# menos con total seguridad. Además cada línea empezaba con 📉 (gráfico a la
# baja) sin que eso significara nada: un emoji que sugiere caída en TODAS las
# series, incluidas las que son un récord.
LIMITE_SERIES_DASHBOARD = 14


def obtener_dashboard_analitico(numero_usuario: str) -> str:
    """Últimas series de ESTE atleta con telemetría VBT completa, recuento de hoy y estado biofísico."""
    try:
        inicializar_db()

        with conexion_db(solo_lectura=True) as conexion:
            resultados = conexion.execute('''
                SELECT fecha_hora, ejercicio, peso_kg, repeticiones,
                       rpe_reportado, vbt_velocidad_media, vbt_velocidad_pico,
                       vbt_perdida_velocidad, rom_cm, dolor_articular, calidad_tecnica
                FROM series_log
                WHERE numero_usuario = ?
                ORDER BY fecha_hora DESC
                LIMIT ?
            ''', (str(numero_usuario), LIMITE_SERIES_DASHBOARD)).fetchall()
            hoy = datetime.now().strftime("%Y-%m-%d")
            resumen_hoy = conexion.execute('''
                SELECT lower(ejercicio) AS ej, COUNT(*) AS n
                FROM series_log
                WHERE numero_usuario = ? AND substr(fecha_hora, 1, 10) = ?
                GROUP BY lower(ejercicio)
                ORDER BY n DESC
            ''', (str(numero_usuario), hoy)).fetchall()

            # Consultar readiness reciente (hoy o último registrado)
            readiness_reciente = None
            try:
                readiness_reciente = conexion.execute('''
                    SELECT fecha, score, sueno_horas, sueno_calidad, estres, dolor, fuente
                    FROM readiness
                    WHERE numero_usuario = ?
                    ORDER BY fecha DESC
                    LIMIT 1
                ''', (str(numero_usuario),)).fetchone()
            except Exception:
                pass

            # Consultar eventos de autorregulación recientes (últimos 3 días)
            eventos_recientes = []
            try:
                eventos_recientes = conexion.execute('''
                    SELECT fecha, tipo, ejercicio, detalle_json
                    FROM eventos_autorregulacion
                    WHERE numero_usuario = ?
                    ORDER BY fecha DESC
                    LIMIT 3
                ''', (str(numero_usuario),)).fetchall()
            except Exception:
                pass

        if not resultados and not readiness_reciente:
            return "Sin datos históricos recientes en la base de datos SQL."

        partes = [f"--- ÚLTIMAS {len(resultados)} SERIES REGISTRADAS (TELEMETRÍA SQL) ---"]
        for fecha, ej, peso, reps, rpe, vm, vp, vl, rom, dolor, calidad in resultados:
            fecha_corta = fecha[:10] if fecha else "N/A"
            rpe_str = f"RPE {rpe:g}" if rpe is not None else "RPE N/A"

            telemetria_items = []
            if vm is not None:
                telemetria_items.append(f"Vmed: {vm:.3f} m/s")
            if vp is not None:
                telemetria_items.append(f"Vpico: {vp:.3f} m/s")
            if vl is not None:
                telemetria_items.append(f"VL: {vl:.1f}%")
            if rom is not None:
                telemetria_items.append(f"ROM: {rom:.1f}cm")
            if calidad and str(calidad).strip():
                telemetria_items.append(f"Técnica: {calidad}")
            if dolor and str(dolor).strip().lower() not in ("ninguno", ""):
                telemetria_items.append(f"Dolor: {dolor}")

            tel_str = f" ({', '.join(telemetria_items)})" if telemetria_items else ""
            partes.append(f"· {fecha_corta} | {str(ej).title()}: {peso:g}kg x {reps}{tel_str} | {rpe_str}")

        if resumen_hoy:
            total = sum(n for _, n in resumen_hoy)
            detalle = ", ".join(f"{str(ej).title()}: {n}" for ej, n in resumen_hoy)
            partes.append(f"SERIES REGISTRADAS HOY ({hoy}): {total} en total ({detalle}). "
                          "Esto es lo que consta APUNTADO, no lo que prescribe el plan.")
        else:
            partes.append("SERIES REGISTRADAS HOY: ninguna todavía.")

        if readiness_reciente:
            f_r, score_r, s_h, s_q, estres_r, dolor_r, fuente_r = readiness_reciente
            estado_r = "ÓPTIMO" if score_r >= 0.8 else ("MODERADO" if score_r >= 0.6 else "FATIGADO")
            detalles_r = [f"Score {score_r * 100:.0f}% ({estado_r})"]
            if s_h is not None:
                cal_txt = f", calidad {s_q}/5" if s_q is not None else ""
                detalles_r.append(f"Sueño {s_h:g}h{cal_txt}")
            if estres_r is not None:
                detalles_r.append(f"Estrés {estres_r}/5")
            if dolor_r and str(dolor_r).strip().lower() not in ("ninguno", ""):
                detalles_r.append(f"Molestia: {dolor_r}")
            partes.append(f"ESTADO DE READINESS ({f_r}): {', '.join(detalles_r)}")

        if eventos_recientes:
            ev_list = []
            for ev_f, ev_tipo, ev_ej, ev_det in eventos_recientes:
                f_corta = ev_f[:10] if ev_f else ""
                ej_str = f" [{str(ev_ej).title()}]" if ev_ej else ""
                ev_list.append(f"{f_corta}: {ev_tipo}{ej_str}")
            partes.append(f"AUTORREGULACIONES RECIENTES DEL SISTEMA: {'; '.join(ev_list)}")

        return "\n".join(partes) + "\n"

    except Exception as e:
        print(f"[ERROR SQL ANALÍTICO]: Fallo al generar dashboard -> {e}")
        return "Error al leer la telemetría analítica."


def consultar_series_atleta(numero_usuario: str, ejercicio: str | None = None, limite: int = 25) -> dict:
    """Consulta el historial detallado de series en SQLite para uno o varios ejercicios.

    Permite al LLM o al RAG consultar todas las series de un movimiento específico
    (ej. 'peso muerto' o 'sentadilla, press banca') con su cinemática VBT, repeticiones,
    RPE y estimación de 1RM sin perder información.
    """
    try:
        inicializar_db()
        from core.ejercicios import canonizar, para_mostrar

        limite = max(1, min(int(limite or 25), 100))

        # Parsear si vienen varios ejercicios (separados por coma o 'y')
        filtros_ej = []
        if ejercicio and str(ejercicio).strip():
            partes_raw = re.split(r"[,y]\s*", str(ejercicio).strip())
            filtros_ej = [canonizar(p) for p in partes_raw if canonizar(p)]

        with conexion_db(solo_lectura=True) as conexion:
            cursor = conexion.execute('''
                SELECT id, fecha_hora, ejercicio, peso_kg, repeticiones,
                       rpe_reportado, vbt_velocidad_media, vbt_velocidad_pico,
                       vbt_perdida_velocidad, rom_cm, dolor_articular, calidad_tecnica
                FROM series_log
                WHERE numero_usuario = ?
                ORDER BY fecha_hora DESC
            ''', (str(numero_usuario),))
            filas_raw = cursor.fetchall()

        if not filas_raw:
            return {
                "ok": True,
                "total_series": 0,
                "mensaje": f"No hay series registradas en la base de datos para el usuario {numero_usuario}.",
                "resumen_texto": "Sin series registradas en el historial.",
                "series": []
            }

        series_filtradas = []
        for r in filas_raw:
            sid, f_h, ej_raw, peso, reps, rpe, vm, vp, vl, rom, dolor, calidad = r
            c_ej = canonizar(ej_raw)
            if filtros_ej:
                coincide = any(f == c_ej or f in c_ej or c_ej in f for f in filtros_ej)
                if not coincide:
                    continue

            # Estimación de 1RM para contextualizar la serie
            e1rm = None
            if peso and reps:
                rpe_ef = rpe if rpe is not None else 10.0
                rir = max(0.0, 10.0 - rpe_ef)
                reps_ef = reps + rir
                e1rm = round(peso * (1.0 + reps_ef / 30.0), 1)

            series_filtradas.append({
                "id": sid,
                "fecha_hora": f_h,
                "ejercicio": para_mostrar(c_ej),
                "ejercicio_canonico": c_ej,
                "peso_kg": peso,
                "repeticiones": reps,
                "rpe": rpe,
                "vbt_velocidad_media": vm,
                "vbt_velocidad_pico": vp,
                "vbt_perdida_velocidad": vl,
                "rom_cm": rom,
                "dolor_articular": dolor,
                "calidad_tecnica": calidad,
                "e1rm_estimado_kg": e1rm
            })

            if len(series_filtradas) >= limite:
                break

        if not series_filtradas:
            ej_txt = ", ".join(filtros_ej) if filtros_ej else "especificado"
            return {
                "ok": True,
                "total_series": 0,
                "mensaje": f"No se encontraron series para el ejercicio: {ej_txt}.",
                "resumen_texto": f"No hay registros de series previas de {ej_txt}.",
                "series": []
            }

        lineas = []
        ejercicios_unicos = {s["ejercicio"] for s in series_filtradas}
        for ej_nombre in sorted(ejercicios_unicos):
            sub = [s for s in series_filtradas if s["ejercicio"] == ej_nombre]
            mejor_peso = max(s["peso_kg"] for s in sub)
            mejor_e1rm = max((s["e1rm_estimado_kg"] or 0) for s in sub)
            mejor_serie = max(sub, key=lambda s: (s["peso_kg"], s["repeticiones"]))

            lineas.append(f"HISTORIAL DE SERIES: {ej_nombre.upper()} ({len(sub)} series encontradas):")
            lineas.append(f"  · Carga máxima levantada: {mejor_peso:g} kg")
            lineas.append(f"  · Mejor serie: {mejor_serie['peso_kg']:g}kg x {mejor_serie['repeticiones']} reps "
                          f"@RPE {mejor_serie['rpe'] or 'N/A'}")
            if mejor_e1rm > 0:
                lineas.append(f"  · Mejor 1RM estimado: {mejor_e1rm:g} kg")

            lineas.append("  · Detalle de series (más recientes primero):")
            for s in sub:
                f_corta = s["fecha_hora"][:16] if s["fecha_hora"] else "N/A"
                rpe_str = f"@RPE {s['rpe']:g}" if s["rpe"] is not None else "RPE N/A"
                vbt_parts = []
                if s["vbt_velocidad_media"] is not None:
                    vbt_parts.append(f"Vmed: {s['vbt_velocidad_media']:.3f} m/s")
                if s["vbt_velocidad_pico"] is not None:
                    vbt_parts.append(f"Vpico: {s['vbt_velocidad_pico']:.3f} m/s")
                if s["vbt_perdida_velocidad"] is not None:
                    vbt_parts.append(f"VL: {s['vbt_perdida_velocidad']:.1f}%")
                if s["rom_cm"] is not None:
                    vbt_parts.append(f"ROM: {s['rom_cm']:.1f}cm")
                if s["calidad_tecnica"]:
                    vbt_parts.append(f"Técnica: {s['calidad_tecnica']}")
                if s["dolor_articular"] and str(s["dolor_articular"]).lower() not in ("ninguno", ""):
                    vbt_parts.append(f"Dolor: {s['dolor_articular']}")

                vbt_txt = f" ({', '.join(vbt_parts)})" if vbt_parts else ""
                e1rm_txt = f" [1RM est: {s['e1rm_estimado_kg']:g} kg]" if s["e1rm_estimado_kg"] else ""
                lineas.append(f"    - {f_corta} | {s['peso_kg']:g}kg x {s['repeticiones']} {rpe_str}{vbt_txt}{e1rm_txt}")
            lineas.append("")

        resumen_texto = "\n".join(lineas).strip()

        return {
            "ok": True,
            "total_series": len(series_filtradas),
            "ejercicios_encontrados": list(ejercicios_unicos),
            "resumen_texto": resumen_texto,
            "series": series_filtradas
        }

    except Exception as e:
        print(f"[ERROR SQL CONSULTA SERIES]: {e}")
        return {
            "ok": False,
            "error": str(e),
            "resumen_texto": f"Error consultando series: {e}",
            "series": []
        }


def diagnostico_multi_usuario():
    """
    Herramienta de verificación: muestra cuántas series tiene cada usuario.
    Llámala una vez desde el terminal para confirmar que el aislamiento funciona.

    Uso:
        from services.db_service import diagnostico_multi_usuario
        diagnostico_multi_usuario()
    """
    try:
        inicializar_db()

        with conexion_db(solo_lectura=True) as conexion:
            filas = conexion.execute('''
                SELECT numero_usuario, COUNT(*) as total, MAX(fecha_hora) as ultima
                FROM series_log
                GROUP BY numero_usuario
                ORDER BY ultima DESC
            ''').fetchall()

        if not filas:
            print("[DIAGNÓSTICO] Base de datos vacía, sin series registradas.")
            return

        print("[DIAGNÓSTICO] Estado multi-usuario actual:")
        print("-" * 55)
        for usuario, total, ultima in filas:
            print(f"  → {usuario} | {total} series | Última: {ultima}")
        print("-" * 55)

    except Exception as e:
        print(f"[ERROR DIAGNÓSTICO]: {e}")
