# -*- coding: utf-8 -*-
"""
core/ejercicios.py — IDENTIDAD CANÓNICA DEL EJERCICIO
=====================================================
Un único lugar donde se decide qué nombre identifica a un ejercicio.

POR QUÉ EXISTE
--------------
El nombre del ejercicio nacía en tres sitios distintos y con tres formas
distintas, y todos escribían en la misma columna de `series_log`:

  * `corregir_ejercicio_usuario` (api/rutas_whatsapp.py) devolvía claves con
    guion bajo: "press_banca", "peso_muerto".
  * El clasificador de visión devolvía el nombre de la carpeta del dataset:
    "Press banco".
  * El LLM escribía lo que le sonara del contexto: "Press Banco Plano".

`motor_predictivo._norm` solo baja a minúsculas y colapsa espacios, así que las
tres formas sobrevivían como ejercicios DISTINTOS. Medido en la base real:
'Press Banco Plano' (2 filas) y 'press banco plano' (1) conviviendo — tres
series del mismo movimiento repartidas en dos identidades. Consecuencias:

  1. `leer_series(numero, ejercicio)` no encontraba el histórico, así que la
     detección de PR de e1RM comparaba contra un conjunto vacío y cualquier
     serie podía anunciarse como récord (o ninguna).
  2. `_GRUPO_DE` mapea "press banca" -> pecho. "press banco plano" no está en
     ese mapa, de modo que el análisis de MRV por grupo muscular ignoraba esas
     series al calcular el volumen de pecho.

REGLA DE DISEÑO: conservador. Solo se unifican variantes del MISMO movimiento.
Un press inclinado NO es un press banca y un peso muerto rumano NO es un peso
muerto convencional: son estímulos distintos y sus e1RM no son comparables, así
que conservan identidad propia. Lo que no se reconoce se deja pasar normalizado
(minúsculas, sin acentos, espacios colapsados) en lugar de forzarlo a un cajón
equivocado: perder la variante es mejor que mentir sobre ella.

Las claves canónicas coinciden con las que ya usa `_GRUPO_DE`, para que el
mapeo a grupo muscular funcione sin traducciones intermedias.
"""
from __future__ import annotations

import re
import unicodedata

# Reglas ORDENADAS de específico -> general. El orden es parte del contrato:
# "press inclinado" debe resolverse antes que "press", y "peso muerto rumano"
# antes que "peso muerto", o la regla general se traga a la específica.
_REGLAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("peso muerto rumano", ("peso muerto rumano", "rumano", "rdl")),
    ("peso muerto",        ("peso muerto", "deadlift", "despegue", "sumo")),
    ("press inclinado",    ("press inclinado", "inclinado", "incline")),
    ("press militar",      ("press militar", "militar", "overhead", "ohp",
                            "hombro")),
    # Dominadas y jalón van ANTES que press banca: "jalón al pecho" contenía el
    # alias "pecho" y se resolvía como press banca. "pecho" queda fuera de la
    # lista por eso — es un grupo muscular, no un ejercicio, y aparece en el
    # nombre de movimientos de espalda ("jalón al pecho") y de pectoral por
    # igual. Para mapear grupo muscular ya está `_GRUPO_DE`.
    ("dominadas",          ("dominada", "pull up", "pull-up", "pullup",
                            "chin up")),
    ("jalon",              ("jalon", "pulldown", "polea alta")),
    ("press banca",        ("press banca", "press banco", "banca", "banco",
                            "bench", "plano")),
    ("sentadilla",         ("sentadilla", "squat", "hack")),
    ("prensa",             ("prensa", "leg press")),
    ("hip thrust",         ("hip thrust", "empuje de cadera")),
    ("remo",               ("remo", "row")),
    ("zancadas",           ("zancada", "lunge", "desplante")),
    ("fondos",             ("fondo", "dips", "dip")),
    ("extension triceps",  ("extension de triceps", "extension triceps",
                            "triceps", "copa", "frances")),
    ("curl",               ("curl", "biceps")),
)


def _sin_acentos(txt: str) -> str:
    """'jalón' y 'jalon' son el mismo ejercicio; el atleta escribe las dos."""
    nfkd = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalizar(texto: str | None) -> str:
    """Minúsculas, sin acentos, sin guiones bajos, espacios colapsados.

    No decide identidad: solo deja el texto en una forma comparable. Los guiones
    bajos se convierten en espacios porque las claves internas ("press_banca")
    y los nombres de carpeta del dataset ("Peso_muerto_") los usan como
    separador.
    """
    t = _sin_acentos(str(texto or "")).lower()
    t = t.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", t).strip()


def canonizar(texto: str | None) -> str:
    """Devuelve la clave canónica del ejercicio, o el texto normalizado si no
    se reconoce (nunca cadena vacía si venía algo)."""
    t = normalizar(texto)
    if not t:
        return ""
    for canonico, alias in _REGLAS:
        if any(a in t for a in alias):
            return canonico
    return t


def detectar(texto: str | None) -> str | None:
    """Busca un ejercicio CONOCIDO dentro de texto libre; None si no hay ninguno.

    Se distingue de `canonizar` en que no inventa identidad: sobre "Sí, 120kg"
    `canonizar` devolvería "si, 120kg" (texto normalizado), mientras que aquí la
    respuesta correcta es None — el atleta confirmó, no corrigió.
    """
    t = normalizar(texto)
    if not t:
        return None
    for canonico, alias in _REGLAS:
        if any(a in t for a in alias):
            return canonico
    return None


def mismo_ejercicio(a: str | None, b: str | None) -> bool:
    """¿Dos nombres designan el mismo movimiento? Si alguno viene vacío no se
    puede afirmar que coincidan, pero tampoco que difieran: se devuelve True
    para que quien llama no trate la ausencia de dato como un conflicto."""
    if not normalizar(a) or not normalizar(b):
        return True
    return canonizar(a) == canonizar(b)


def para_mostrar(texto: str | None) -> str:
    """Forma legible para mensajes al atleta ('press banca' -> 'Press Banca')."""
    c = canonizar(texto)
    return c.title() if c else "Movimiento libre"
