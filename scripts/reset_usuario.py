# scripts/reset_usuario.py

import json
import os
import shutil
import sqlite3
from pathlib import Path

RUTA_RAIZ = Path(__file__).resolve().parent.parent
CARPETA_DATA = RUTA_RAIZ / "data"
CARPETA_RUTINAS = RUTA_RAIZ / "resources" / "rutinas_generadas"


def reset_usuario(numero_usuario: str):
    """
    Borra TODOS los datos de un usuario específico para empezar desde cero.
    Limpia JSONs, historiales, estados, y cualquier tabla SQL que lo contenga.
    """
    numero = str(numero_usuario).strip()
    print(f"\n[RESET] Iniciando limpieza profunda para usuario: {numero}")
    print("=" * 60)

    # 1. Historial de conversación
    historial = CARPETA_DATA / f"historial_{numero}.json"
    if historial.exists():
        historial.unlink()
        print(f" [OK] Historial de chat eliminado: {historial.name}")
    else:
        print(f" [SKIP] Sin historial de chat que borrar.")

    # 2. Archivos heredados (v3 o anteriores)
    archivos_legacy = [
        CARPETA_DATA / f"perfil_{numero}.json",
        CARPETA_DATA / f"rms_{numero}.json",
        CARPETA_DATA / f"perfil_{numero}.json.migrado",
        CARPETA_DATA / f"rms_{numero}.json.migrado"
    ]
    for arch in archivos_legacy:
        if arch.exists():
            arch.unlink()
            print(f" [OK] Archivo legacy eliminado: {arch.name}")

    # 3. Estado del calendario y onboarding
    ruta_estado = CARPETA_DATA / "estado_usuarios.json"
    if ruta_estado.exists():
        with open(ruta_estado, "r", encoding="utf-8") as f:
            estados = json.load(f)
        if numero in estados:
            del estados[numero]
            with open(ruta_estado, "w", encoding="utf-8") as f:
                json.dump(estados, f, indent=4)
            print(f" [OK] Estado del calendario y onboarding reseteados.")
        else:
            print(f" [SKIP] El usuario no estaba en estado_usuarios.json.")
    else:
        print(f" [SKIP] Archivo estado_usuarios.json no existe.")

    # 4. Bases de datos SQLite (Limpieza dinámica v4)
    # Escanea TODOS los .db y borra de cualquier tabla que tenga la columna 'numero_usuario'
    bases_sql = list(CARPETA_DATA.glob("*.db"))
    if bases_sql:
        for db_file in bases_sql:
            conn = sqlite3.connect(db_file)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tablas = [row[0] for row in cursor.fetchall()]

            total_borrados_db = 0
            for tabla in tablas:
                cursor.execute(f"PRAGMA table_info({tabla});")
                columnas = [col[1] for col in cursor.fetchall()]

                if "numero_usuario" in columnas:
                    cursor.execute(f"DELETE FROM {tabla} WHERE numero_usuario = ?", (numero,))
                    if cursor.rowcount > 0:
                        print(f" [OK] {cursor.rowcount} registros eliminados de la tabla '{tabla}' en {db_file.name}.")
                        total_borrados_db += cursor.rowcount

            if total_borrados_db == 0:
                print(f" [SKIP] Sin registros para este usuario en {db_file.name}.")

            conn.commit()
            conn.close()
    else:
        print(f" [SKIP] No se encontraron bases de datos SQL en la carpeta data.")

    # 5. Rutinas generadas
    carpeta_rutinas_usuario = CARPETA_RUTINAS / numero
    if carpeta_rutinas_usuario.exists():
        shutil.rmtree(carpeta_rutinas_usuario)
        print(f" [OK] Carpeta de rutinas generadas eliminada por completo.")
    else:
        print(f" [SKIP] Sin carpeta de rutinas que borrar.")

    # 6. ChromaDB — Memoria a largo plazo
    try:
        from langchain_ollama import OllamaEmbeddings
        from langchain_chroma import Chroma

        embeddings = OllamaEmbeddings(model="nomic-embed-text")
        carpeta_chroma = str(CARPETA_DATA / "chroma_db")

        # Expediente Dinámico
        db_perfil = Chroma(
            persist_directory=carpeta_chroma,
            embedding_function=embeddings,
            collection_name="perfil_dinamico_atleta"
        )
        resultados_perfil = db_perfil.get(where={"numero_usuario": numero})
        ids_perfil = resultados_perfil.get("ids", [])

        if ids_perfil:
            db_perfil.delete(ids=ids_perfil)
            print(f" [OK] {len(ids_perfil)} notas del expediente ChromaDB eliminadas.")
        else:
            print(f" [SKIP] Sin notas en expediente ChromaDB.")

        # Memoria Histórica
        db_historia = Chroma(
            persist_directory=carpeta_chroma,
            embedding_function=embeddings,
            collection_name="memoria_historica_atletas"
        )
        resultados_hist = db_historia.get(where={"numero_usuario": numero})
        ids_hist = resultados_hist.get("ids", [])

        if ids_hist:
            db_historia.delete(ids=ids_hist)
            print(f" [OK] {len(ids_hist)} registros de memoria histórica ChromaDB eliminados.")
        else:
            print(f" [SKIP] Sin memoria histórica en ChromaDB.")

    except Exception as e:
        print(f" [ERROR] Hubo un problema limpiando ChromaDB: {e}")

    print("=" * 60)
    print(f"[RESET FINALIZADO] El usuario {numero} ha sido borrado de la existencia.")
    print(f"La base de conocimientos y biblioteca científica NO fueron tocadas.\n")


if __name__ == "__main__":
    numero = input("Número de usuario a resetear (ej: 527291395298): ").strip()
    confirmar = input(f"¿Confirmas borrar TODOS los datos de '{numero}'? (si/no): ").strip().lower()

    if confirmar == "si":
        reset_usuario(numero)
    else:
        print("Operación cancelada.")