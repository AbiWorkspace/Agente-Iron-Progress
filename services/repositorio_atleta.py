"""
repositorio_atleta.py — Capa de acceso a datos del atleta (SQLite).

Reemplaza a los archivos sueltos perfil_{numero}.json y rms_{numero}.json con
una base SQLite única, manteniendo la MISMA forma de datos que llm_service ya
consume (dicts), para que el orquestador no cambie su lógica.

Qué gana el sistema con esto:
  1. Una sola fuente de verdad consultable con SQL.
  2. HISTORIAL fechado de cada 1RM y de cada cambio de perfil (peso corporal,
     %grasa...). Ese historial es exactamente el dataset que necesitará el
     futuro modelo de predicción de rendimiento.
  3. Migración automática: la primera vez que corre, absorbe los JSON legados
     y los renombra a *.json.migrado (no borra nada).

Uso desde llm_service:
    from services.repositorio_atleta import repositorio
    repositorio.obtener_perfil(numero)          -> dict (misma forma que antes)
    repositorio.actualizar_perfil(numero, **c)  -> None
    repositorio.obtener_rms(numero)             -> {ejercicio: rm_actual}
    repositorio.guardar_rm(numero, ejercicio, rm, peso, reps, fuente) -> None
    repositorio.historial_rms(numero, ejercicio=None) -> list[dict]
    repositorio.dataset_progresion(numero)      -> list[dict]  (para el modelo)
"""

import json
import os
import re
import sqlite3
import glob
from datetime import datetime
from pathlib import Path

_RUTA_RAIZ = Path(__file__).resolve().parent.parent
RUTA_DB_ATLETAS = _RUTA_RAIZ / "data" / "iron_progress.db"

CAMPOS_PERFIL = ("peso_kg", "altura_m", "edad", "genero",
                 "porcentaje_grasa", "dias_semana", "meta")

_CAMPOS_NUMERICOS = {"peso_kg": float, "altura_m": float, "edad": int,
                     "porcentaje_grasa": float, "dias_semana": int}


def _norm_ej(nombre) -> str:
    return re.sub(r"\s+", " ", str(nombre).strip().lower())


def _ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class RepositorioAtleta:

    def __init__(self, ruta_db: str | Path = RUTA_DB_ATLETAS):
        self.ruta_db = str(ruta_db)
        os.makedirs(os.path.dirname(self.ruta_db), exist_ok=True)
        self._crear_tablas()
        self._migrar_json_legado()

    # ------------------------------------------------------------------ infra
    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.ruta_db, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _crear_tablas(self):
        with self._conn() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS atletas (
                numero_usuario   TEXT PRIMARY KEY,
                peso_kg          REAL,
                altura_m         REAL,
                edad             INTEGER,
                genero           TEXT,
                porcentaje_grasa REAL,
                dias_semana      INTEGER,
                meta             TEXT,
                creado_en        TEXT NOT NULL,
                actualizado_en   TEXT NOT NULL
            );

            -- Cada cambio de perfil queda fechado (peso corporal en el tiempo,
            -- %grasa en el tiempo...): features directas para el modelo.
            CREATE TABLE IF NOT EXISTS historial_perfil (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                numero_usuario TEXT NOT NULL,
                campo          TEXT NOT NULL,
                valor          TEXT,
                fecha          TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_hp_usr
                ON historial_perfil(numero_usuario, campo, fecha);

            -- HISTORIAL de marcas: cada 1RM estimado se INSERTA, nunca se pisa.
            -- 'El RM actual' = la fila más reciente por ejercicio.
            CREATE TABLE IF NOT EXISTS marcas_rm (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                numero_usuario TEXT NOT NULL,
                ejercicio      TEXT NOT NULL,
                rm_estimado    REAL NOT NULL,
                peso_origen    REAL,
                reps_origen    INTEGER,
                fuente         TEXT DEFAULT 'chat',
                fecha          TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rm_usr_ej
                ON marcas_rm(numero_usuario, ejercicio, fecha);
            """)

    # -------------------------------------------------------------- migración
    def _migrar_json_legado(self):
        """Absorbe data/perfil_*.json y data/rms_*.json una sola vez."""
        carpeta_data = _RUTA_RAIZ / "data"

        for ruta in glob.glob(str(carpeta_data / "perfil_*.json")):
            try:
                numero = Path(ruta).stem.replace("perfil_", "")
                with open(ruta, "r", encoding="utf-8") as f:
                    perfil = json.load(f)
                if isinstance(perfil, dict) and perfil:
                    self.actualizar_perfil(numero, _migracion=True, **{
                        k: v for k, v in perfil.items() if k in CAMPOS_PERFIL})
                os.replace(ruta, ruta + ".migrado")
                print(f"[REPO] Migrado perfil de {numero} a SQLite ({ruta} -> .migrado)")
            except Exception as e:
                print(f"[REPO] No se pudo migrar {ruta}: {e}")

        for ruta in glob.glob(str(carpeta_data / "rms_*.json")):
            try:
                numero = Path(ruta).stem.replace("rms_", "")
                with open(ruta, "r", encoding="utf-8") as f:
                    rms = json.load(f)
                if isinstance(rms, dict):
                    existentes = self.obtener_rms(numero)
                    for ejercicio, rm in rms.items():
                        if _norm_ej(ejercicio) not in existentes:
                            self.guardar_rm(numero, ejercicio, rm, fuente="migracion_json")
                os.replace(ruta, ruta + ".migrado")
                print(f"[REPO] Migrados RMs de {numero} a SQLite ({ruta} -> .migrado)")
            except Exception as e:
                print(f"[REPO] No se pudo migrar {ruta}: {e}")

    # ------------------------------------------------------------------ perfil
    def obtener_perfil(self, numero_usuario: str) -> dict:
        """Misma forma que el antiguo cargar_perfil(): dict solo con lo conocido."""
        with self._conn() as con:
            fila = con.execute("SELECT * FROM atletas WHERE numero_usuario = ?",
                               (str(numero_usuario),)).fetchone()
        if not fila:
            return {}
        perfil = {}
        for campo in CAMPOS_PERFIL:
            valor = fila[campo]
            if valor is not None and valor != "":
                perfil[campo] = valor
        return perfil

    def actualizar_perfil(self, numero_usuario: str, _migracion: bool = False, **campos):
        """Upsert de campos no-None + registro fechado de cada cambio real."""
        numero_usuario = str(numero_usuario)
        limpios = {}
        for k, v in campos.items():
            if k not in CAMPOS_PERFIL or v is None:
                continue
            try:
                limpios[k] = _CAMPOS_NUMERICOS[k](v) if k in _CAMPOS_NUMERICOS else str(v)
            except (TypeError, ValueError):
                continue
        if not limpios:
            return

        ahora = _ahora()
        previo = self.obtener_perfil(numero_usuario)
        with self._conn() as con:
            con.execute(
                "INSERT INTO atletas (numero_usuario, creado_en, actualizado_en) "
                "VALUES (?, ?, ?) ON CONFLICT(numero_usuario) DO NOTHING",
                (numero_usuario, ahora, ahora))
            sets = ", ".join(f"{k} = ?" for k in limpios)
            con.execute(
                f"UPDATE atletas SET {sets}, actualizado_en = ? WHERE numero_usuario = ?",
                (*limpios.values(), ahora, numero_usuario))
            for k, v in limpios.items():
                if previo.get(k) != v:   # solo cambios reales al historial
                    con.execute(
                        "INSERT INTO historial_perfil (numero_usuario, campo, valor, fecha) "
                        "VALUES (?, ?, ?, ?)",
                        (numero_usuario, k, str(v), ahora))

    def historial_perfil(self, numero_usuario: str, campo: str | None = None) -> list[dict]:
        q = "SELECT campo, valor, fecha FROM historial_perfil WHERE numero_usuario = ?"
        params: list = [str(numero_usuario)]
        if campo:
            q += " AND campo = ?"
            params.append(campo)
        q += " ORDER BY fecha, id"
        with self._conn() as con:
            return [dict(r) for r in con.execute(q, params).fetchall()]

    # --------------------------------------------------------------------- RM
    def guardar_rm(self, numero_usuario: str, ejercicio: str, rm,
                   peso_origen=None, reps_origen=None, fuente: str = "chat"):
        """INSERTA una marca fechada (historial completo, nunca se pisa)."""
        with self._conn() as con:
            con.execute(
                "INSERT INTO marcas_rm (numero_usuario, ejercicio, rm_estimado, "
                "peso_origen, reps_origen, fuente, fecha) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(numero_usuario), _norm_ej(ejercicio), round(float(rm), 1),
                 float(peso_origen) if peso_origen is not None else None,
                 int(reps_origen) if reps_origen is not None else None,
                 fuente, _ahora()))

    def obtener_rms(self, numero_usuario: str) -> dict:
        """Misma forma que el antiguo cargar_rms(): {ejercicio: rm_actual}."""
        q = """
            SELECT m.ejercicio, m.rm_estimado
            FROM marcas_rm m
            JOIN (SELECT ejercicio, MAX(id) AS max_id
                  FROM marcas_rm WHERE numero_usuario = ?
                  GROUP BY ejercicio) ult
              ON m.ejercicio = ult.ejercicio AND m.id = ult.max_id
        """
        with self._conn() as con:
            filas = con.execute(q, (str(numero_usuario),)).fetchall()
        return {f["ejercicio"]: f["rm_estimado"] for f in filas}

    def historial_rms(self, numero_usuario: str, ejercicio: str | None = None) -> list[dict]:
        q = ("SELECT ejercicio, rm_estimado, peso_origen, reps_origen, fuente, fecha "
             "FROM marcas_rm WHERE numero_usuario = ?")
        params: list = [str(numero_usuario)]
        if ejercicio:
            q += " AND ejercicio = ?"
            params.append(_norm_ej(ejercicio))
        q += " ORDER BY fecha, id"
        with self._conn() as con:
            return [dict(r) for r in con.execute(q, params).fetchall()]

    # ----------------------------------------------- dataset para el modelo ML
    def dataset_progresion(self, numero_usuario: str) -> list[dict]:
        """
        Filas listas para entrenar el futuro predictor de rendimiento:
        cada marca de RM enriquecida con el estado del atleta y el tiempo
        transcurrido. Exportable a CSV/pandas tal cual.
        """
        rms = self.historial_rms(numero_usuario)
        if not rms:
            return []
        perfil = self.obtener_perfil(numero_usuario)
        primera = rms[0]["fecha"]
        try:
            t0 = datetime.strptime(primera[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            t0 = datetime.now()
        filas = []
        for r in rms:
            try:
                t = datetime.strptime(r["fecha"][:19], "%Y-%m-%d %H:%M:%S")
                dias = (t - t0).days
            except ValueError:
                dias = 0
            filas.append({
                "numero_usuario": str(numero_usuario),
                "ejercicio": r["ejercicio"],
                "rm_estimado": r["rm_estimado"],
                "peso_origen": r["peso_origen"],
                "reps_origen": r["reps_origen"],
                "fuente": r["fuente"],
                "fecha": r["fecha"],
                "dias_desde_inicio": dias,
                "peso_corporal": perfil.get("peso_kg"),
                "porcentaje_grasa": perfil.get("porcentaje_grasa"),
                "edad": perfil.get("edad"),
                "genero": perfil.get("genero"),
                "dias_entreno_semana": perfil.get("dias_semana"),
                "meta": perfil.get("meta"),
            })
        return filas

    def exportar_dataset_csv(self, numero_usuario: str, ruta_salida: str) -> int:
        """Vuelca dataset_progresion a CSV. Devuelve el nº de filas."""
        import csv
        filas = self.dataset_progresion(numero_usuario)
        if not filas:
            return 0
        with open(ruta_salida, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(filas[0].keys()))
            w.writeheader()
            w.writerows(filas)
        return len(filas)


# Singleton compartido por todo el proceso
repositorio = RepositorioAtleta()