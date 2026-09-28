import hashlib
import os
from pathlib import Path

import chromadb
from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFDirectoryLoader
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

RUTA_SCRIPT   = Path(__file__).resolve()
RUTA_RAIZ     = RUTA_SCRIPT.parent.parent
CARPETA_LIBROS = RUTA_RAIZ / "scripts" / "resources" / "libros_cientificos"
CARPETA_DB    = RUTA_RAIZ / "data" / "chroma_db"
NOMBRE_COLECCION = "biblioteca_cientifica"


def _md5(ruta: Path) -> str:
    h = hashlib.md5()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _filtrar_duplicados_por_contenido(documentos: list) -> list:
    """PyPDFDirectoryLoader indexa cada PDF del directorio tal cual está.
    Si dos ficheros son el mismo contenido con nombre distinto (ocurrió con
    practical-programming / toaz.info-practical-programming, MD5 idéntico),
    el fragmento se indexaba dos veces y sesgaba la recuperación hacia ese
    libro. Aquí se descarta por hash, no por nombre, antes de fragmentar."""
    hash_por_ruta: dict[str, str] = {}
    rutas_vistas: dict[str, str] = {}   # hash -> primera ruta que lo usó
    conservados = []
    descartados = []

    for doc in documentos:
        ruta = doc.metadata["source"]
        if ruta not in hash_por_ruta:
            hash_por_ruta[ruta] = _md5(Path(ruta))
        h = hash_por_ruta[ruta]

        if h in rutas_vistas and rutas_vistas[h] != ruta:
            descartados.append(ruta)
            continue

        rutas_vistas.setdefault(h, ruta)
        conservados.append(doc)

    for ruta in sorted(set(descartados)):
        print(f"    [DUPLICADO] {Path(ruta).name} — mismo contenido que "
              f"{Path(rutas_vistas[hash_por_ruta[ruta]]).name}, se omite")

    return conservados


def _vaciar_coleccion_si_existe() -> None:
    """Re-ejecutar el script no debe ir sumando copias sobre lo que ya había:
    Chroma.from_documents() AÑADE, no reemplaza. Se limpia la colección
    propia (biblioteca_cientifica) antes de reinsertar; las otras dos
    colecciones del mismo chroma_db (perfil_dinamico_atleta,
    memoria_historica_atletas) son datos de atletas y no se tocan."""
    cliente = chromadb.PersistentClient(path=str(CARPETA_DB))
    nombres = {c.name for c in cliente.list_collections()}
    if NOMBRE_COLECCION in nombres:
        n_previos = cliente.get_collection(NOMBRE_COLECCION).count()
        cliente.delete_collection(NOMBRE_COLECCION)
        print(f"[0] Colección '{NOMBRE_COLECCION}' existente con {n_previos} "
              f"fragmentos — eliminada para reconstruirla desde cero.")


def ingestar_libros():
    print(f"[1] Buscando PDFs en '{CARPETA_LIBROS}'...")
    os.makedirs(CARPETA_LIBROS, exist_ok=True)
    os.makedirs(CARPETA_DB, exist_ok=True)

    loader    = PyPDFDirectoryLoader(str(CARPETA_LIBROS))
    documentos = loader.load()

    if not documentos:
        print(f"[ADVERTENCIA] No encontré ningún PDF en:\n{CARPETA_LIBROS}")
        return

    documentos = _filtrar_duplicados_por_contenido(documentos)
    print(f"[2] {len(documentos)} páginas únicas cargadas. Fragmentando...")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=700,
        chunk_overlap=100,
        separators=["\n\n", "\n", ".", " ", ""]
    )
    chunks = splitter.split_documents(documentos)
    print(f"[3] {len(chunks)} fragmentos generados.")

    _vaciar_coleccion_si_existe()

    print("[4] Vectorizando y guardando en ChromaDB...")
    embeddings = OllamaEmbeddings(model="nomic-embed-text")

    # ── CRÍTICO: especificar collection_name igual al que usa el sistema ──
    db = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=str(CARPETA_DB),
        collection_name=NOMBRE_COLECCION
    )

    print(f"[ÉXITO] {len(chunks)} fragmentos indexados en '{NOMBRE_COLECCION}'")
    print(f"[RUTA] {CARPETA_DB}")

if __name__ == "__main__":
    ingestar_libros()