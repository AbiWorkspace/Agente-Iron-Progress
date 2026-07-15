import sqlite3
import os
from datetime import datetime

# Rutas dinámicas (igual que en tu orquestador)
ruta_script_actual = os.path.dirname(os.path.abspath(__file__))
ruta_raiz          = os.path.dirname(ruta_script_actual)
carpeta_data       = os.path.join(ruta_raiz, "data")
os.makedirs(carpeta_data, exist_ok=True)

ruta_db = os.path.join(carpeta_data, "iron_progress_telemetria.db")


def inicializar_db():
    """Crea la tabla, el índice y actualiza las columnas si no existen. Seguro llamarlo en cada operación."""
    conexion = sqlite3.connect(ruta_db)
    cursor = conexion.cursor()

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

    # 2. ESCUDO DE MIGRACIÓN: Garantiza que las bases de datos viejas reciban las columnas nuevas de VBT
    nuevas_columnas = [
        "rpe_reportado REAL",
        "vbt_velocidad_media REAL",
        "vbt_velocidad_pico REAL",
        "vbt_perdida_velocidad REAL",
        "rom_cm REAL",
        "dolor_articular TEXT",
        "calidad_tecnica TEXT"
    ]

    for columna in nuevas_columnas:
        try:
            cursor.execute(f"ALTER TABLE series_log ADD COLUMN {columna}")
        except sqlite3.OperationalError:
            pass  # Si la columna ya existe, SQLite lanza un error que ignoramos silenciosamente

    # 3. Índice para búsquedas rápidas del historial del atleta
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_usuario_fecha
        ON series_log(numero_usuario, fecha_hora)
    ''')

    # 4. NUEVA TABLA: Control Anti-Spam (Para el agente proactivo)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alertas_enviadas (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_usuario TEXT    NOT NULL,
            tipo           TEXT    NOT NULL,
            enviada_en     TEXT    DEFAULT (datetime('now')),
            semana_iso     TEXT
        )
    ''')

    # 5. Índice para búsquedas ultra rápidas del escudo anti-spam
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_alertas_user_semana
        ON alertas_enviadas(numero_usuario, semana_iso)
    ''')

    conexion.commit()
    conexion.close()
    print("[SQL SERVICE]: Base de datos de telemetría lista y conectada.")

def guardar_serie_sql(numero_usuario: str, datos: dict) -> bool:
    """Guarda una serie completada. Siempre filtra por numero_usuario."""
    try:
        inicializar_db()  # No-op si ya existe, pero garantiza que la tabla esté lista
        conexion = sqlite3.connect(ruta_db)
        cursor = conexion.cursor()
        fecha_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute('''
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

        conexion.commit()
        conexion.close()
        print(f"[SQL] Serie guardada: {numero_usuario} | {datos.get('ejercicio')}")
        return True

    except Exception as e:
        print(f"[ERROR SQL] Fallo al guardar serie: {e}")
        return False


def obtener_dashboard_analitico(numero_usuario: str) -> str:
    """Extrae las últimas 5 series de ESTE usuario para inyectarlas como contexto al LLM."""
    try:
        inicializar_db()
        conexion = sqlite3.connect(ruta_db)
        cursor = conexion.cursor()

        cursor.execute('''
            SELECT fecha_hora, ejercicio, peso_kg, repeticiones,
                   rpe_reportado, vbt_velocidad_media
            FROM series_log
            WHERE numero_usuario = ?
            ORDER BY fecha_hora DESC
            LIMIT 5
        ''', (str(numero_usuario),))  # La coma es obligatoria: es una tupla

        resultados = cursor.fetchall()
        conexion.close()

        if not resultados:
            return "Sin datos históricos recientes en la base de datos SQL."

        dashboard = "--- ÚLTIMAS 5 SERIES REGISTRADAS (TELEMETRÍA SQL) ---\n"
        for fila in resultados:
            fecha, ej, peso, reps, rpe, vel = fila

            fecha_corta = fecha[:10] if fecha else "N/A"
            rpe_str = f"RPE {rpe}" if rpe is not None else "RPE N/A"
            vel_str = f"({vel} m/s)" if vel is not None else ""

            dashboard += f"📉 {fecha_corta} | {ej}: {peso}kg x {reps} {vel_str} | {rpe_str}\n"

        return dashboard

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
        conexion = sqlite3.connect(ruta_db)
        cursor = conexion.cursor()

        cursor.execute('''
            SELECT numero_usuario, COUNT(*) as total, MAX(fecha_hora) as ultima
            FROM series_log
            GROUP BY numero_usuario
            ORDER BY ultima DESC
        ''')

        filas = cursor.fetchall()
        conexion.close()

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