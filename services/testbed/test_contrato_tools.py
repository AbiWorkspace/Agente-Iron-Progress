# -*- coding: utf-8 -*-
"""
test_contrato_tools.py — el guardián del contrato entre core/tools.json y el código.

Por qué existe
--------------
core/tools.json es lo ÚNICO que el modelo ve: rellena los argumentos con los
nombres que ahí se declaran. Si un handler lee otro nombre, no hay error, no hay
excepción y no hay log: el argumento llega vacío y la tool hace algo distinto de
lo que promete. Así estuvo `guardar_en_expediente_personal` — el schema pedía
`dato_a_guardar`, el handler leía `dato`, y el expediente del atleta llevaba sin
escribirse una sola vez mientras el coach le confirmaba que lo tenía apuntado.

Lo que verifica
---------------
1. COBERTURA        cada tool declarada tiene despachador en el código.
2. GUÍA DE DECISIÓN cada tool aparece en el system prompt (si el modelo no tiene
                    una regla que se la mande, no la elige NUNCA).
3. ARGUMENTOS       cada parámetro `required` del schema se lee literalmente en
                    el handler (`args.get("x")`, `args["x"]` o `k in req`).
4. ALIAS SANOS      ningún alias de _ALIAS_ARGS apunta a un nombre que el schema
                    no declara, ni pisa un parámetro real de esa misma tool.
5. SECRETO          el nombre de cada tool está en _NOMBRES_TOOLS_RE, que es lo
                    que lo borra del texto visible. Una tool que falte ahí puede
                    acabar impresa en WhatsApp y rompe <secreto_profesional>.

Uso:  python -m services.testbed.test_contrato_tools     (0 = ok, 1 = roto)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
TOOLS_JSON = RAIZ / "core" / "tools.json"
PROMPT_JSON = RAIZ / "core" / "prompt.json"
FUENTES = [RAIZ / "services" / "llm_service.py",
           RAIZ / "services" / "handlers_v9.py"]

# Parámetros que el orquestador resuelve por su cuenta (perfil, estado, contexto
# del turno) y que por diseño el handler NO lee de args. Cada exención necesita
# su motivo escrito: si no sabes por qué está aquí, es un bug, no una exención.
# Tools que a propósito NO tienen regla en el prompt: el sistema las dispara
# por su cuenta y <creacion_de_plan> le prohíbe al modelo invocarlas.
SIN_REGLA_A_PROPOSITO = {"generar_rutina"}

EXENTOS: dict[str, dict[str, str]] = {
    "generar_rutina": {
        "nivel": "lo impone evaluar_nivel_fuerza en el pipeline, no el modelo",
        "porcentaje_grasa": "cae al perfil guardado si el modelo no lo manda",
        "rm_dominadas_kg": "cae al historial de RMs si el modelo no lo manda",
    },
}


def _solo_codigo(fuente: str) -> str:
    """Quita comentarios y docstrings. Un parámetro NOMBRADO en un comentario no
    es un parámetro leído: sin esto, el comentario que explica el bug bastaría
    para que el test lo diera por arreglado."""
    import io
    import tokenize
    piezas = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(fuente).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and tok.string.lstrip("rbfuRBFU")[:3] in ('"""', "'''"):
                continue                      # docstring o bloque explicativo
            piezas.append(tok.string)
    except (tokenize.TokenError, IndentationError):
        return fuente                          # ante la duda, analiza de más
    return "\n".join(piezas)


def _fuentes_crudas() -> str:
    """El código tal cual, con su layout intacto. Lo usa la comprobación de
    COBERTURA, que busca expresiones completas ('nombre == "x"') y necesita las
    líneas sin trocear."""
    return "\n".join(f.read_text(encoding="utf-8") for f in FUENTES)


def _cargar_fuentes() -> str:
    """Los fuentes SIN la tabla _ALIAS_ARGS y sin comentarios. Esa tabla contiene
    los nombres canónicos como strings, así que dejarla dentro haría que un
    parámetro contara como 'leído' solo por tener alias — justo el falso positivo
    que dejaría pasar el bug que este test existe para cazar."""
    partes = []
    for f in FUENTES:
        # El recorte va ANTES de tokenizar: después, cada token queda en su propia
        # línea y la regex ya no puede delimitar el bloque.
        crudo = re.sub(r"^_ALIAS_ARGS.*?^\}$", "", f.read_text(encoding="utf-8"),
                       flags=re.DOTALL | re.MULTILINE)
        partes.append(_solo_codigo(crudo))
    return "\n".join(partes)


def _lee_argumento(codigo: str, param: str) -> bool:
    """¿El código lee ese nombre de argumento en alguna de sus formas?"""
    p = re.escape(param)
    patrones = (
        rf'args\.get\(\s*["\']{p}["\']',      # args.get("x")
        rf'args\[\s*["\']{p}["\']\s*\]',      # args["x"]
        rf'["\']{p}["\']\s*,?\s*\)?\s*$',     # dentro de una tupla de requeridos
        rf'\(\s*["\']{p}["\']',
        rf'["\']{p}["\']\s*,',
    )
    return any(re.search(pat, codigo, re.MULTILINE) for pat in patrones)


def main() -> int:
    tools = json.loads(TOOLS_JSON.read_text(encoding="utf-8"))
    prompt = json.loads(PROMPT_JSON.read_text(encoding="utf-8"))["promt"]
    codigo = _cargar_fuentes()      # tokenizado: para leer ARGUMENTOS
    crudo = _fuentes_crudas()       # con layout: para detectar DESPACHADORES

    # _ALIAS_ARGS se lee del fuente sin importar llm_service (que arrastra Ollama,
    # Chroma y el resto del mundo); este test tiene que poder correr en seco.
    from services import llm_service  # noqa: F401  (import perezoso opcional)
    alias_args = llm_service._ALIAS_ARGS

    fallos: list[str] = []
    avisos: list[str] = []

    for t in tools:
        fn = t["function"]
        nombre = fn["name"]
        props = (fn.get("parameters") or {}).get("properties") or {}
        requeridos = (fn.get("parameters") or {}).get("required") or []

        # 1. COBERTURA
        if not re.search(rf'nombre == ["\']{re.escape(nombre)}["\']', crudo) \
                and f'"{nombre}":' not in crudo:
            fallos.append(f"[COBERTURA] {nombre}: declarada en tools.json pero sin despachador")

        # 2. GUÍA DE DECISIÓN
        if nombre not in prompt and nombre not in SIN_REGLA_A_PROPOSITO:
            avisos.append(f"[PROMPT] {nombre}: sin regla en el system prompt; "
                          f"el modelo no la elegirá nunca")

        # 3. ARGUMENTOS
        for param in requeridos:
            if param in EXENTOS.get(nombre, {}):
                continue
            if not _lee_argumento(codigo, param):
                fallos.append(f"[ARGUMENTO] {nombre}.{param}: es required en el schema "
                              f"y NINGÚN handler lo lee")

        # 5. SECRETO PROFESIONAL
        if not llm_service._NOMBRES_TOOLS_RE.fullmatch(nombre):
            fallos.append(f"[SECRETO] {nombre}: no está en _NOMBRES_TOOLS_RE; si el "
                          f"modelo la nombra, el atleta lo lee en WhatsApp")

        # 4. ALIAS SANOS
        for alias, canonico in (alias_args.get(nombre) or {}).items():
            if canonico not in props:
                fallos.append(f"[ALIAS] {nombre}: '{alias}' -> '{canonico}', "
                              f"que no existe en el schema")
            if alias in props:
                fallos.append(f"[ALIAS] {nombre}: '{alias}' es un parámetro REAL "
                              f"del schema y el alias lo estaría renombrando")

    # Al revés: un alias para una tool que ya no existe es basura acumulada.
    declaradas = {t["function"]["name"] for t in tools}
    for nombre in alias_args:
        if nombre not in declaradas:
            avisos.append(f"[ALIAS] tabla para '{nombre}', que no está en tools.json")

    print(f"Contrato de tools — {len(tools)} herramientas declaradas\n")
    for a in avisos:
        print(f"  AVISO  {a}")
    for f in fallos:
        print(f"  FALLO  {f}")
    if not fallos and not avisos:
        print("  todo cuadra: cobertura, prompt, argumentos y alias.")
    print()
    if fallos:
        print(f"RESULTADO: {len(fallos)} fallo(s) de contrato.")
        return 1
    print(f"RESULTADO: contrato OK{f' ({len(avisos)} aviso(s))' if avisos else ''}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
