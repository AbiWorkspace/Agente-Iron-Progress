# -*- coding: utf-8 -*-
"""
metricas_documentacion.py — recuenta en vivo las cifras que la documentación
técnica y la tesina citan como hechos del sistema, para que la tabla "Cifras
del sistema" se regenere sola en vez de escribirse (y desactualizarse) a mano.

Tarea F1.2 del plan de documentación (docs/PLAN_DOCUMENTACION_TESINA.md).
Ninguna cifra de este script se estima: todas se derivan de leer el propio
repositorio en el momento de ejecutarlo.

    python3 scripts/metricas_documentacion.py
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Componentes de ruta a excluir. Se comparan por COMPONENTE completo
# (Path.parts), no por subcadena: un filtro ingenuo tipo `"data" in ruta`
# descarta por error services/Creardataset.py y 1_crear_dataset.py, que
# contienen la subcadena "data" sin estar bajo el directorio data/. Ese
# error se cometió y se detectó al reconciliar contra un segundo método de
# conteo (`find | xargs wc -l`) — la propia regla de este plan ("ninguna
# cifra entra sin verificar") se aplicó también a este script.
COMPONENTES_EXCLUIDOS = {"__pycache__", ".git", "torch"}


def _lineas_py(ruta: Path) -> int:
    """Cuenta saltos de línea, igual que `wc -l` (no líneas lógicas): así el
    resultado es comparable con el que usa el resto de la documentación."""
    try:
        return ruta.read_text(encoding="utf-8", errors="ignore").count("\n")
    except OSError:
        return 0


def contar_lineas_python() -> dict[str, int]:
    """Líneas de Python por fichero relevante y total del proyecto.

    Separa el CÓDIGO DEL PRODUCTO (lo que corre en producción: api/, core/,
    services/, scripts/ salvo scripts de documentación, Entrenamiento_modelo_media/)
    de las HERRAMIENTAS DE DOCUMENTACIÓN (docs/fuentes/*.py: generar_docx.py,
    generar_reporte_aplicacion.py, diagramas.py, y el propio
    metricas_documentacion.py). Esta separación NO existía en las primeras
    mediciones de este proyecto: la cifra "≈28.500 líneas" del PDF v9.1 y los
    "29.106"/"29.286" de sesiones de documentación previas ya incluían
    generar_docx.py y diagramas.py (1.779 líneas) sin decirlo, y en esta misma
    sesión generar_reporte_aplicacion.py (1.489 líneas más) habría inflado el
    total un 5% adicional si no se separa. Es el mismo tipo de error que
    describe el comentario de arriba sobre el filtro por subcadena: medir el
    instrumento de medición junto con lo medido."""
    total_producto = 0
    total_docs = 0
    por_fichero: dict[str, int] = {}
    for ruta in RAIZ.rglob("*.py"):
        rel = ruta.relative_to(RAIZ)
        if COMPONENTES_EXCLUIDOS & set(rel.parts):
            continue
        n = _lineas_py(ruta)
        por_fichero[str(rel)] = n
        if rel.parts[0] == "docs":
            total_docs += n
        else:
            total_producto += n
    return {"total_producto": total_producto, "total_docs": total_docs,
            "total": total_producto + total_docs, "por_fichero": por_fichero}


def contar_herramientas() -> int:
    with open(RAIZ / "core" / "tools.json", encoding="utf-8") as f:
        return len(json.load(f))


def contar_secciones_prompt() -> list[str]:
    with open(RAIZ / "core" / "prompt.json", encoding="utf-8") as f:
        p = json.load(f)["promt"]
    return re.findall(r"<([a-z_]+)>", p)


def contar_reglas_herramientas() -> int:
    """Cuenta las reglas numeradas dentro de la sección <herramientas> del prompt."""
    with open(RAIZ / "core" / "prompt.json", encoding="utf-8") as f:
        p = json.load(f)["promt"]
    m = re.search(r"<herramientas>(.*?)</herramientas>", p, re.S)
    if not m:
        return 0
    # Reglas enumeradas "1.", "2." ... al inicio de línea dentro del bloque.
    return len(re.findall(r"(?:^|\n)\s*(\d{1,2})\.\s", m.group(1)))


def contar_suites_deterministas() -> int:
    testbed = RAIZ / "services" / "testbed"
    return len(list(testbed.glob("test_*.py")))


def contar_casos_qa() -> dict[str, int]:
    """Importa qa_casos_uso.py y cuenta bloques/casos reales (no por grep)."""
    import importlib.util
    import sys as _sys
    ruta = RAIZ / "services" / "testbed" / "qa_casos_uso.py"
    spec = importlib.util.spec_from_file_location("qa_casos_uso_metrica", ruta)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules["qa_casos_uso_metrica"] = mod
    spec.loader.exec_module(mod)
    bloques = mod.BLOQUES
    total_casos = sum(len(v) for v in bloques.values())
    return {"bloques": len(bloques), "casos": total_casos}


def contar_smoke(ejecutar: bool = False) -> str:
    """services/smoke_v8.py usa un helper `chk(nombre, cond)` invocado desde
    dentro de bucles, así que contar líneas con 'chk(' en el fichero (33)
    NO es el número de aserciones reales que corren (72) — hay que
    ejecutarlo y leer su propio resumen "RESULTADO: N OK, M FALLAS"."""
    ruta = RAIZ / "services" / "smoke_v8.py"
    if not ruta.exists():
        return "fichero no encontrado"
    if not ejecutar:
        return "no ejecutado (pasa ejecutar=True o --smoke); NO contar 'chk(' en el fichero, subestima"
    try:
        r = subprocess.run(["python3", "-m", "services.smoke_v8"], cwd=RAIZ,
                            capture_output=True, text=True, timeout=120)
        m = re.search(r"RESULTADO:\s*(\d+)\s*OK,\s*(\d+)\s*FALLAS", r.stdout)
        return f"{m.group(1)} OK, {m.group(2)} FALLAS" if m else "sin resumen reconocible"
    except Exception as e:  # pragma: no cover
        return f"error al ejecutar: {e}"


def contar_colecciones_chroma() -> dict[str, int] | str:
    try:
        import chromadb
        c = chromadb.PersistentClient(path=str(RAIZ / "data" / "chroma_db"))
        return {col.name: c.get_collection(col.name).count() for col in c.list_collections()}
    except Exception as e:  # pragma: no cover — chroma_db puede no existir aún
        return f"no disponible: {e}"


def contar_tablas_sqlite() -> dict[str, list[str]]:
    import sqlite3
    salida = {}
    for nombre in ("iron_progress.db", "iron_progress_telemetria.db"):
        ruta = RAIZ / "data" / nombre
        if not ruta.exists():
            salida[nombre] = []
            continue
        con = sqlite3.connect(str(ruta))
        tablas = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        con.close()
        salida[nombre] = tablas
    return salida


def modelo_declarado() -> str:
    txt = (RAIZ / "services" / "llm_service.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'MODELO_LLM\s*=\s*"([^"]+)"', txt)
    return m.group(1) if m else "no encontrado"


def num_ctx_declarado() -> dict[str, str | None]:
    llm = (RAIZ / "services" / "llm_service.py").read_text(encoding="utf-8", errors="ignore")
    modelfile = (RAIZ / "Modelfile").read_text(encoding="utf-8", errors="ignore")
    m1 = re.search(r'"num_ctx":\s*(\d+)', llm)
    m2 = re.search(r'PARAMETER\s+num_ctx\s+(\d+)', modelfile)
    return {"llm_service.py": m1.group(1) if m1 else None,
            "Modelfile": m2.group(1) if m2 else None}


def commit_actual() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ, text=True).strip()
    except Exception:
        return "sin git"


def main() -> None:
    import sys
    ejecutar_smoke = "--smoke" in sys.argv
    lineas = contar_lineas_python()
    secciones = contar_secciones_prompt()
    suites = contar_suites_deterministas()
    qa = contar_casos_qa()
    smoke = contar_smoke(ejecutar=ejecutar_smoke)
    chroma = contar_colecciones_chroma()
    tablas = contar_tablas_sqlite()

    print("=" * 70)
    print(f"MÉTRICAS DE IRON PROGRESS — commit {commit_actual()}")
    print("=" * 70)
    print(f"Líneas de Python — código del PRODUCTO...... {lineas['total_producto']}")
    print(f"Líneas de Python — herramientas de docs..... {lineas['total_docs']}  "
          f"(docs/fuentes/*.py: NO es parte del sistema)")
    print(f"Líneas de Python (ambos sumados)............ {lineas['total']}")
    print(f"  services/llm_service.py.......... "
          f"{lineas['por_fichero'].get('services/llm_service.py', '?')}")
    print(f"Modelo declarado (MODELO_LLM)....... {modelo_declarado()}")
    print(f"num_ctx (llm_service.py / Modelfile) {num_ctx_declarado()}")
    print(f"Herramientas en core/tools.json..... {contar_herramientas()}")
    print(f"Secciones del prompt................ {len(secciones)} -> {secciones}")
    print(f"Reglas dentro de <herramientas>..... {contar_reglas_herramientas()}")
    print(f"Suites deterministas (test_*.py).... {suites}")
    print(f"Banco de QA: bloques / casos........ {qa['bloques']} / {qa['casos']}")
    print(f"smoke_v8.py.......................... {smoke}")
    print(f"Colecciones ChromaDB................ {chroma}")
    for db, tbls in tablas.items():
        print(f"Tablas en {db} ({len(tbls)}): {tbls}")
    print("=" * 70)


if __name__ == "__main__":
    main()
