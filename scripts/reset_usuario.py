# scripts/reset_usuario.py

import json
import os
import shutil
from pathlib import Path

RUTA_RAIZ    = Path(__file__).resolve().parent.parent
CARPETA_DATA = RUTA_RAIZ / "data"
CARPETA_RUTINAS = RUTA_RAIZ / "resources" / "rutinas_generadas"


def reset_usuario(numero_usuario: str):
    """
    Borra TODOS los datos de un usuario específico para empezar desde cero.
    No toca los datos de otros usuarios ni la biblioteca científica.
    """
    numero = str(numero_usuario).strip()
    print(f"\n[RESET] Iniciando limpieza para usuario: {numero}")
    print("=" * 50)

    # 1. Historial de conversación
    historial = CARPETA_DATA / f"historial_{numero}.json"
    if historial.exists():
        historial.unlink()
        print(f"  ✅ Historial eliminado: {historial.name}")
    else:
        print(f"  ⏭️  Sin historial que borrar.")

    # 2. Estado del calendario
    ruta_estado = CARPETA_DATA / "estado_usuarios.json"
    if ruta_estado.exists():
        with open(ruta_estado, "r", encoding="utf-8") as f:
            estados = json.load(f)
        if numero in estados:
            del estados[numero]
            with open(ruta_estado, "w", encoding="utf-8") as f:
                json.dump(estados, f, indent=4)
            print(f"  ✅ Estado del calendario eliminado.")
        else:
            print(f"  ⏭️  Sin estado de calendario que borrar.")

    # 3. Series SQL
    ruta_db = CARPETA_DATA / "iron_progress_telemetria.db"
    if ruta_db.exists():
        import sqlite3
        conn   = sqlite3.connect(ruta_db)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM series_log WHERE numero_usuario = ?", (numero,))
        borradas = cursor.rowcount
        conn.commit()
        conn.close()
        print(f"  ✅ {borradas} series SQL eliminadas.")
    else:
        print(f"  ⏭️  Sin base de datos SQL que limpiar.")

    # 4. Rutinas generadas
    carpeta_rutinas_usuario = CARPETA_RUTINAS / numero
    if carpeta_rutinas_usuario.exists():
        shutil.rmtree(carpeta_rutinas_usuario)
        print(f"  ✅ Carpeta de rutinas eliminada: {carpeta_rutinas_usuario}")
    else:
        print(f"  ⏭️  Sin rutinas que borrar.")

    # 5. ChromaDB — perfil dinámico del atleta
    try:
        from langchain_ollama import OllamaEmbeddings
        from langchain_chroma import Chroma

        embeddings = OllamaEmbeddings(model="nomic-embed-text")
        carpeta_chroma = str(CARPETA_DATA / "chroma_db")

        db_perfil = Chroma(
            persist_directory=carpeta_chroma,
            embedding_function=embeddings,
            collection_name="perfil_dinamico_atleta"
        )
        # Buscar todos los documentos de este usuario
        resultados = db_perfil.get(where={"numero_usuario": numero})
        ids_a_borrar = resultados.get("ids", [])

        if ids_a_borrar:
            db_perfil.delete(ids=ids_a_borrar)
            print(f"  ✅ {len(ids_a_borrar)} documentos del expediente ChromaDB eliminados.")
        else:
            print(f"  ⏭️  Sin documentos en ChromaDB que borrar.")

        # Memoria histórica narrativa
        db_historia = Chroma(
            persist_directory=carpeta_chroma,
            embedding_function=embeddings,
            collection_name="memoria_historica_atletas"
        )
        resultados_hist = db_historia.get(where={"numero_usuario": numero})
        ids_hist = resultados_hist.get("ids", [])
        if ids_hist:
            db_historia.delete(ids=ids_hist)
            print(f" {len(ids_hist)} documentos de memoria histórica eliminados.")
        else:
            print(f"Sin memoria histórica que borrar.")

    except Exception as e:
        print(f"Error limpiando ChromaDB: {e}")

    print("=" * 50)
    print(f"[RESET]Usuario {numero} limpiado completamente.")
    print(f"[RESET] La biblioteca científica NO fue tocada.\n")


if __name__ == "__main__":
    numero = input("Número de usuario a resetear (ej: 527291395298): ").strip()
    confirmar = input(f"¿Confirmas borrar TODOS los datos de '{numero}'? (si/no): ").strip().lower()

    if confirmar == "si":
        reset_usuario(numero)
    else:
        print("Operación cancelada.")