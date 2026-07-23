# -*- coding: utf-8 -*-
"""
migracion_v9.py — Iron Progress v9
Migración ADITIVA e IDEMPOTENTE de SQLite.
Ajustado específicamente para el esquema de 'series_log' en 'iron_progress_telemetria.db'.
"""
import os
import shutil
import sqlite3
import sys
from datetime import datetime

# Rutas exactas basadas en tu db_service.py
ruta_script_actual = os.path.dirname(os.path.abspath(__file__))
ruta_raiz = os.path.dirname(ruta_script_actual)  # Asumiendo que el script corre desde /scripts o similar
# Si vas a poner este script en la raíz del proyecto, usa:
# ruta_raiz = ruta_script_actual

RUTA_DB = os.environ.get(
    "IRON_DB",
    os.path.join(ruta_raiz, "data", "iron_progress_telemetria.db")
)

# NOMBRES REALES DE TU ESQUEMA
TABLA_SERIES = "series_log"
COL_USUARIO = "numero_usuario"
COL_FECHA = "fecha_hora"


def _columnas(cur: sqlite3.Cursor, tabla: str) -> set:
    return {fila[1] for fila in cur.execute(f"PRAGMA table_info({tabla})")}


def migrar(ruta_db: str = RUTA_DB) -> None:
    if not os.path.exists(ruta_db):
        raise SystemExit(f"[MIGRACION] No existe la BD: {ruta_db} (¿La ruta es correcta?)")

    respaldo = f"{ruta_db}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(ruta_db, respaldo)
    print(f"[MIGRACION] Backup de seguridad creado exitosamente: {respaldo}")

    con = sqlite3.connect(ruta_db)
    cur = con.cursor()
    try:
        # ------------------------------------------------------------------
        # 1) Verificación de columnas en la tabla principal (series_log)
        # ------------------------------------------------------------------
        cols = _columnas(cur, TABLA_SERIES)
        if not cols:
            raise SystemExit(f"[MIGRACION] La tabla '{TABLA_SERIES}' no existe.")

        # Como ya las tienes, esto lo saltará, pero queda por seguridad
        if "repeticiones" not in cols:
            cur.execute(f"ALTER TABLE {TABLA_SERIES} ADD COLUMN repeticiones INTEGER")
            print("[MIGRACION] + columna repeticiones (INTEGER, nullable)")
        else:
            print("[MIGRACION] ✓ Columna 'repeticiones' ya existe.")

        if "rpe_reportado" not in cols:
            cur.execute(f"ALTER TABLE {TABLA_SERIES} ADD COLUMN rpe_reportado REAL")
            print("[MIGRACION] + columna rpe_reportado (REAL, nullable)")
        else:
            print("[MIGRACION] ✓ Columna 'rpe_reportado' ya existe.")

        # Índice que alimenta TODAS las consultas del motor bayesiano (usuario+ejercicio+fecha)
        cur.execute(f"""CREATE INDEX IF NOT EXISTS idx_series_u_e_f 
                        ON {TABLA_SERIES}({COL_USUARIO}, ejercicio, {COL_FECHA})""")
        print("[MIGRACION] ✓ Índice compuesto idx_series_u_e_f asegurado.")

        # ------------------------------------------------------------------
        # 2) Readiness matutino (cuestionario proactivo).
        # ------------------------------------------------------------------
        cur.execute("""CREATE TABLE IF NOT EXISTS readiness
        (
            id
            INTEGER
            PRIMARY
            KEY
            AUTOINCREMENT,
            numero_usuario
            TEXT
            NOT
            NULL,
            fecha
            TEXT
            NOT
            NULL,      -- YYYY-MM-DD
            sueno_horas
            REAL,
            sueno_calidad
            INTEGER,   -- 1-5
            estres
            INTEGER,   -- 1-5 (5 = frito)
            dolor
            TEXT,      -- zona o 'ninguno'
            score
            REAL
            NOT
            NULL,      -- 0.0-1.0 consolidado
            fuente
            TEXT
            DEFAULT
            'botones', -- botones | texto | inferido
            UNIQUE
                       (
            numero_usuario,
            fecha
                       )
            )""")
        print("[MIGRACION] ✓ Tabla 'readiness' asegurada.")

        # ------------------------------------------------------------------
        # 3) Eventos de autorregulación (DUP, descargas disparadas, MRV)
        # ------------------------------------------------------------------
        cur.execute("""CREATE TABLE IF NOT EXISTS eventos_autorregulacion
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           numero_usuario
                           TEXT
                           NOT
                           NULL,
                           fecha
                           TEXT
                           NOT
                           NULL, -- ISO datetime
                           tipo
                           TEXT
                           NOT
                           NULL, -- dup_ajuste | dup_descarga | mrv_alerta
                           ejercicio
                           TEXT,
                           detalle_json
                           TEXT  -- payload completo del evento
                       )""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_eventos_u_f
            ON eventos_autorregulacion(numero_usuario, fecha)""")
        print("[MIGRACION] ✓ Tabla 'eventos_autorregulacion' y su índice asegurados.")

        con.commit()
        print("[MIGRACION] OK — ESQUEMA V9 LISTO Y ACTUALIZADO 💪")
    except Exception as e:
        print(f"[ERROR FATAL DE MIGRACIÓN] {e}")
        con.rollback()
    finally:
        con.close()


if __name__ == "__main__":
    # Ajustar para que tome el arg o la ruta por defecto
    migrar(sys.argv[1] if len(sys.argv) > 1 else RUTA_DB)