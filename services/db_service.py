import os
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
                datos.get("ejercicio"),
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


def obtener_dashboard_analitico(numero_usuario: str) -> str:
    """Extrae las últimas 5 series de ESTE usuario para inyectarlas como contexto al LLM."""
    try:
        inicializar_db()

        with conexion_db(solo_lectura=True) as conexion:
            resultados = conexion.execute('''
                SELECT fecha_hora, ejercicio, peso_kg, repeticiones,
                       rpe_reportado, vbt_velocidad_media
                FROM series_log
                WHERE numero_usuario = ?
                ORDER BY fecha_hora DESC
                LIMIT 5
            ''', (str(numero_usuario),)).fetchall()  # La coma es obligatoria: es una tupla

        if not resultados:
            return "Sin datos históricos recientes en la base de datos SQL."

        partes = ["--- ÚLTIMAS 5 SERIES REGISTRADAS (TELEMETRÍA SQL) ---"]
        for fecha, ej, peso, reps, rpe, vel in resultados:
            fecha_corta = fecha[:10] if fecha else "N/A"
            rpe_str = f"RPE {rpe}" if rpe is not None else "RPE N/A"
            vel_str = f"({vel} m/s)" if vel is not None else ""
            partes.append(f"📉 {fecha_corta} | {ej}: {peso}kg x {reps} {vel_str} | {rpe_str}")

        return "\n".join(partes) + "\n"

    except Exception as e:
        print(f"[ERROR SQL ANALÍTICO]: Fallo al generar dashboard -> {e}")
        return "Error al leer la telemetría analítica."


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
