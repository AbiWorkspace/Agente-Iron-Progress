import os
from pathlib import Path
from langchain_community.document_loaders import PyPDFDirectoryLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma

RUTA_SCRIPT   = Path(__file__).resolve()
RUTA_RAIZ     = RUTA_SCRIPT.parent.parent
CARPETA_LIBROS = RUTA_RAIZ / "scripts" / "resources" / "libros_cientificos"
CARPETA_DB    = RUTA_RAIZ / "data" / "chroma_db"

def ingestar_libros():
    print(f"[1] Buscando PDFs en '{CARPETA_LIBROS}'...")
    os.makedirs(CARPETA_LIBROS, exist_ok=True)
    os.makedirs(CARPETA_DB, exist_ok=True)

    loader    = PyPDFDirectoryLoader(str(CARPETA_LIBROS))
    documentos = loader.load()

    if not documentos:
        print(f"[ADVERTENCIA] No encontré ningún PDF en:\n{CARPETA_LIBROS}")
        return

    print(f"[2] {len(documentos)} páginas cargadas. Fragmentando...")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=700,
        chunk_overlap=100,
        separators=["\n\n", "\n", ".", " ", ""]
    )
    chunks = splitter.split_documents(documentos)
    print(f"[3] {len(chunks)} fragmentos generados.")

    print("[4] Vectorizando y guardando en ChromaDB...")
    embeddings = OllamaEmbeddings(model="nomic-embed-text")

    # ── CRÍTICO: especificar collection_name igual al que usa el sistema ──
    db = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=str(CARPETA_DB),
        collection_name="biblioteca_cientifica"  # <-- Este era el problema
    )

    print(f"[ÉXITO] {len(chunks)} fragmentos indexados en 'biblioteca_cientifica'")
    print(f"[RUTA] {CARPETA_DB}")

if __name__ == "__main__":
    ingestar_libros()