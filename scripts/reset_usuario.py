# scripts/reset_usuario.py
"""
Borra TODOS los datos de un atleta para empezar de cero.

Por qué se reescribió (2026-07-31)
----------------------------------
La versión anterior llevaba una LISTA FIJA de archivos a borrar, y el sistema
fue añadiendo ficheros por atleta que nunca se agregaron a esa lista. Resultado:
el reset dejaba datos vivos y el atleta "reseteado" arrancaba con basura del
anterior. Se quedaban sin borrar, entre otros:

    historial_rm_{NUM}.json    <- todas sus marcas de 1RM
    overrides_{NUM}.json       <- sustituciones de ejercicio y descargas
    peso_corporal_{NUM}.json   <- su histórico de peso

Ahora los archivos se DESCUBREN por patrón (cualquier cosa en data/ que lleve su
número), así que un fichero nuevo por atleta se borra solo sin tocar este script.

Uso
---
    python3 scripts/reset_usuario.py                      (pregunta el número)
    python3 scripts/reset_usuario.py 527291395298
    python3 scripts/reset_usuario.py 527291395298 --si    (sin confirmar)
    python3 scripts/reset_usuario.py 527291395298 --con-videos
"""

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

RUTA_RAIZ = Path(__file__).resolve().parent.parent
CARPETA_DATA = RUTA_RAIZ / "data"
CARPETA_RUTINAS = RUTA_RAIZ / "resources" / "rutinas_generadas"
CARPETA_DATASET = CARPETA_DATA / "dataset_recoleccion"

# Colección compartida entre todos los atletas: NUNCA se toca en un reset.
COLECCIONES_COMPARTIDAS = {"biblioteca_cientifica"}


def _borrar(ruta: Path, etiqueta: str = "") -> bool:
    try:
        if ruta.is_dir():
            shutil.rmtree(ruta)
        else:
            ruta.unlink()
        print(f" [OK] {etiqueta or 'eliminado'}: {ruta.name}")
        return True
    except OSError as e:
        print(f" [ERROR] no se pudo borrar {ruta.name}: {e}")
        return False


def _archivos_del_usuario(numero: str) -> list[Path]:
    """Todo lo que hay en data/ con el número del atleta en el nombre.

    Se descubre por patrón a propósito: la lista fija de la versión anterior es
    justo lo que hacía que el reset se quedara corto. Incluye los residuos
    (.tmp, .corrupto-*, .migrado, .bak) que dejan las escrituras atómicas.
    """
    encontrados = {p for p in CARPETA_DATA.glob(f"*{numero}*") if p.is_file()}
    return sorted(encontrados)


def _videos_dataset(numero: str) -> list[Path]:
    """Vídeos del dataset de visión. Se nombran con los ÚLTIMOS 4 dígitos del
    número (…_atleta_5298_…), no con el número entero."""
    if not CARPETA_DATASET.exists():
        return []
    sufijo = numero[-4:]
    return sorted(CARPETA_DATASET.rglob(f"*atleta_{sufijo}_*"))


def reset_usuario(numero_usuario: str, borrar_videos: bool = False) -> None:
    numero = str(numero_usuario).strip()
    print(f"\n[RESET] Limpieza profunda del atleta: {numero}")
    print("=" * 68)

    # ── 1. Archivos por atleta en data/ (descubiertos, no listados) ──────────
    archivos = _archivos_del_usuario(numero)
    if archivos:
        for arch in archivos:
            _borrar(arch, "archivo de datos")
    else:
        print(" [SKIP] Sin archivos suyos en data/.")

    # ── 2. Estado del calendario y del onboarding ────────────────────────────
    ruta_estado = CARPETA_DATA / "estado_usuarios.json"
    if ruta_estado.exists():
        try:
            estados = json.loads(ruta_estado.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            estados = None
            print(f" [ERROR] estado_usuarios.json ilegible ({e}); no se tocó.")
        if isinstance(estados, dict):
            if numero in estados:
                del estados[numero]
                ruta_estado.write_text(
                    json.dumps(estados, indent=4, ensure_ascii=False), encoding="utf-8")
                print(" [OK] Estado del calendario y onboarding eliminado.")
            else:
                print(" [SKIP] No estaba en estado_usuarios.json.")
    else:
        print(" [SKIP] No existe estado_usuarios.json.")

    # ── 3. SQLite: cualquier tabla con columna numero_usuario ────────────────
    bases = list(CARPETA_DATA.glob("*.db"))
    if not bases:
        print(" [SKIP] Sin bases de datos SQL en data/.")
    for db_file in bases:
        try:
            with sqlite3.connect(db_file) as conn:
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
                borrados = 0
                for (tabla,) in cur.fetchall():
                    cur.execute(f"PRAGMA table_info({tabla});")
                    if "numero_usuario" not in [c[1] for c in cur.fetchall()]:
                        continue
                    cur.execute(f"DELETE FROM {tabla} WHERE numero_usuario = ?", (numero,))
                    if cur.rowcount > 0:
                        print(f" [OK] {cur.rowcount} filas borradas de '{tabla}' ({db_file.name}).")
                        borrados += cur.rowcount
                if borrados == 0:
                    print(f" [SKIP] Sin filas suyas en {db_file.name}.")
        except sqlite3.Error as e:
            print(f" [ERROR] {db_file.name}: {e}")

    # ── 4. Planes generados ──────────────────────────────────────────────────
    carpeta_plan = CARPETA_RUTINAS / numero
    if carpeta_plan.exists():
        _borrar(carpeta_plan, "carpeta de rutinas")
    else:
        print(" [SKIP] Sin rutinas generadas.")

    # ── 5. ChromaDB: TODAS sus colecciones, no dos fijas ─────────────────────
    # Se usa el cliente de chromadb directamente en vez de langchain: borrar por
    # metadato no necesita el modelo de embeddings, así que esto no depende de
    # que Ollama esté levantado ni tarda en cargar nada.
    try:
        import chromadb
        cliente = chromadb.PersistentClient(path=str(CARPETA_DATA / "chroma_db"))
        total = 0
        for col in cliente.list_collections():
            nombre = col if isinstance(col, str) else col.name
            if nombre in COLECCIONES_COMPARTIDAS:
                continue
            coleccion = cliente.get_collection(nombre)
            ids = (coleccion.get(where={"numero_usuario": numero}) or {}).get("ids", [])
            if ids:
                coleccion.delete(ids=ids)
                print(f" [OK] {len(ids)} documentos borrados de ChromaDB/'{nombre}'.")
                total += len(ids)
        if total == 0:
            print(" [SKIP] Sin documentos suyos en ChromaDB.")
    except Exception as e:
        print(f" [ERROR] ChromaDB: {type(e).__name__}: {e}")

    # ── 6. Vídeos del dataset de visión (opcional) ───────────────────────────
    videos = _videos_dataset(numero)
    if not videos:
        print(" [SKIP] Sin vídeos suyos en el dataset.")
    elif borrar_videos:
        for v in videos:
            _borrar(v, "vídeo del dataset")
    else:
        print(f" [AVISO] {len(videos)} vídeos suyos en dataset_recoleccion/ NO se han "
              f"borrado.\n         Son material de entrenamiento del modelo de visión. "
              f"Usa --con-videos si quieres eliminarlos.")

    # ── 7. VERIFICACIÓN: ¿de verdad no queda nada? ───────────────────────────
    print("-" * 68)
    restos = _archivos_del_usuario(numero)
    if carpeta_plan.exists():
        restos.append(carpeta_plan)
    if restos:
        print(" [AVISO] Todavía quedan estos rastros suyos:")
        for r in restos:
            print(f"          - {r}")
    else:
        print(" [VERIFICADO] No queda ningún archivo suyo en disco.")

    print("=" * 68)
    print(f"[RESET FINALIZADO] Atleta {numero} borrado.")
    print("La biblioteca científica y la base de conocimiento NO se tocaron.")
    print("\n⚠  Si el servidor está ENCENDIDO, reinícialo antes de volver a escribirle.")
    print("   El orquestador guarda en memoria su historial, su estado y el ejercicio")
    print("   confirmado; sin reiniciar, el primer mensaje reescribe parte de lo")
    print("   que acabas de borrar.\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Borra todos los datos de un atleta.")
    ap.add_argument("numero", nargs="?", help="número de WhatsApp (ej: 527291395298)")
    ap.add_argument("--si", action="store_true", help="no pedir confirmación")
    ap.add_argument("--con-videos", action="store_true",
                    help="borrar también sus vídeos del dataset de visión")
    args = ap.parse_args()

    numero = (args.numero or input("Número de atleta a resetear (ej: 527291395298): ")).strip()
    if not numero.isdigit() or len(numero) < 8:
        print(f"[ABORTADO] '{numero}' no parece un número de WhatsApp válido.")
        return 1

    # Se enseña lo que se va a destruir ANTES de preguntar: un reset es
    # irreversible y el atleta puede llevar meses de historial encima.
    archivos = _archivos_del_usuario(numero)
    videos = _videos_dataset(numero)
    tiene_plan = (CARPETA_RUTINAS / numero).exists()
    print(f"\nSe va a borrar de {numero}:")
    print(f"  · {len(archivos)} archivos en data/")
    print(f"  · plan generado: {'sí' if tiene_plan else 'no'}")
    print(f"  · filas en SQLite y documentos en ChromaDB")
    if videos:
        print(f"  · {len(videos)} vídeos del dataset "
              f"({'SÍ se borran' if args.con_videos else 'se CONSERVAN'})")
    if not archivos and not tiene_plan:
        print("  (no parece haber datos suyos; se ejecuta igual por si hay filas en SQL)")

    if not args.si:
        if input(f"\n¿Confirmas borrar TODO de '{numero}'? (si/no): ").strip().lower() != "si":
            print("Operación cancelada.")
            return 1

    reset_usuario(numero, borrar_videos=args.con_videos)
    return 0


if __name__ == "__main__":
    sys.exit(main())
