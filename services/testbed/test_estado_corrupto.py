# -*- coding: utf-8 -*-
"""
test_estado_corrupto.py — el fichero de estado no puede perder atletas en silencio.

Origen: 2026-07-29. `data/estado_usuarios.json` quedó con marcadores de conflicto
de git sin resolver (el fichero está versionado Y lo reescribe la app varias veces
por turno, así que los conflictos son inevitables). El resultado fue silencioso y
total:

    json.load  -> JSONDecodeError
    el except  -> lo confundía con "el fichero no existe"
    _leer_estados -> {}
    el siguiente _escribir_estados -> dejaba en disco UN solo atleta

Es decir: el calendario, la semana y el cursor de todos los demás atletas
desaparecían para siempre, sin un solo mensaje de error.

    python -m services.testbed.test_estado_corrupto
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from services import llm_service as L                                  # noqa: E402

ATLETA_A = {"semana": 2, "dia": 3, "fecha_programada": "2026-08-04",
            "generacion_ts": 2000.0, "cursor_semana": 2, "cursor_dia": 2}
ATLETA_B = {"semana": 1, "dia": 1, "fecha_programada": "2026-08-01",
            "generacion_ts": 1000.0, "cursor_semana": 1, "cursor_dia": 0}

OK = FALLOS = 0


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global OK, FALLOS
    if condicion:
        OK += 1
        print(f"  ✓ {nombre}")
    else:
        FALLOS += 1
        print(f"  ✗ {nombre}" + (f"  → {detalle}" if detalle else ""))


def _entorno(contenido: str | None):
    """Apunta el módulo a un fichero de estado temporal y limpia su caché."""
    tmp = Path(tempfile.mkdtemp()) / "estado_usuarios.json"
    if contenido is not None:
        tmp.write_text(contenido, encoding="utf-8")
    L.ruta_estado = str(tmp)
    L._estados_cache = None
    L._estados_firma = None
    L._estados_bloqueado = False
    return tmp


def caso_conflicto_git():
    print("\n[1] Marcadores de conflicto de git → se resuelven fusionando los dos lados")
    lado_a = json.dumps({"111": ATLETA_A}, indent=4)[1:-1]
    lado_b = json.dumps({"111": ATLETA_B, "222": ATLETA_B}, indent=4)[1:-1]
    crudo = "{\n<<<<<<< HEAD\n" + lado_a + "\n=======\n" + lado_b + "\n>>>>>>> origin/Pruebas\n}"
    tmp = _entorno(crudo)

    estados = L._leer_estados()
    chk("no devuelve un mapa vacío", bool(estados), f"devolvió {estados!r}")
    chk("recupera a los DOS atletas", set(estados) == {"111", "222"}, f"{sorted(estados)}")
    chk("por atleta gana la generacion_ts más alta",
        estados.get("111", {}).get("semana") == 2,
        f"semana={estados.get('111', {}).get('semana')}")
    chk("reescribe el fichero ya resuelto (sin marcadores)",
        "<<<<<<<" not in tmp.read_text(encoding="utf-8"))
    chk("el fichero reescrito vuelve a ser JSON válido",
        isinstance(json.loads(tmp.read_text(encoding="utf-8")), dict))
    chk("no queda bloqueado para escribir", L._estados_bloqueado is False)


def caso_corrupcion_irrecuperable():
    print("\n[2] Corrupción que no se puede adivinar → se preserva y se bloquea la escritura")
    tmp = _entorno(json.dumps({"111": ATLETA_A}, indent=4))
    L._leer_estados()                                  # deja caché buena en memoria

    tmp.write_text('{"111": {"semana": 2, "dia":', encoding="utf-8")   # truncado
    L._estados_firma = None                            # fuerza la relectura
    estados = L._leer_estados()

    chk("sigue sirviendo la última copia buena en memoria",
        estados.get("111", {}).get("semana") == 2, f"devolvió {estados!r}")
    chk("queda bloqueado para escribir", L._estados_bloqueado is True)
    chk("deja una copia íntegra del fichero corrupto",
        any(p.name.startswith("estado_usuarios.json.corrupto-") for p in tmp.parent.iterdir()))

    L._escribir_estados({"333": ATLETA_B})
    en_disco = tmp.read_text(encoding="utf-8")
    chk("la escritura NO pisa el fichero corrupto", "333" not in en_disco, en_disco[:60])


def caso_vaciado_accidental():
    print("\n[3] Intento de dejar el fichero vacío habiendo atletas → se rechaza")
    tmp = _entorno(json.dumps({"111": ATLETA_A, "222": ATLETA_B}, indent=4))
    L._leer_estados()

    L._escribir_estados({})
    datos = json.loads(tmp.read_text(encoding="utf-8"))
    chk("conserva a los dos atletas", set(datos) == {"111", "222"}, f"{sorted(datos)}")


def caso_primer_arranque():
    print("\n[4] Fichero inexistente → vacío es la respuesta correcta, sin bloquear")
    _entorno(None)
    chk("devuelve un mapa vacío", L._leer_estados() == {})
    chk("NO queda bloqueado (no hay nada que perder)", L._estados_bloqueado is False)


def caso_escritura_atomica():
    print("\n[5] Escritura y relectura normales")
    tmp = _entorno(json.dumps({"111": ATLETA_A}, indent=4))
    L._leer_estados()
    L._escribir_estados({"111": ATLETA_A, "222": ATLETA_B})
    chk("persiste los dos atletas",
        set(json.loads(tmp.read_text(encoding="utf-8"))) == {"111", "222"})
    chk("no deja el fichero temporal a medias", not (tmp.parent / "estado_usuarios.json.tmp").exists())
    L._estados_firma = None
    chk("la relectura ve lo escrito", set(L._leer_estados()) == {"111", "222"})


def main() -> int:
    print("\n" + "=" * 74)
    print("  ESTADO DE USUARIOS — ningún atleta se pierde en silencio")
    print("=" * 74)
    ruta_original = L.ruta_estado
    try:
        caso_conflicto_git()
        caso_corrupcion_irrecuperable()
        caso_vaciado_accidental()
        caso_primer_arranque()
        caso_escritura_atomica()
    finally:
        L.ruta_estado = ruta_original
        L._estados_cache = None
        L._estados_firma = None
        L._estados_bloqueado = False
    print("-" * 74)
    print(f"  {OK}/{OK + FALLOS} comprobaciones correctas")
    print("=" * 74 + "\n")
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
