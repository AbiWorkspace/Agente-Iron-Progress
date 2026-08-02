# -*- coding: utf-8 -*-
"""
test_promesa_generacion.py — el guardia de estado no puede perder una carrera.

Sesión real del 2026-07-30:

    16:27:37 [GEN] pipeline lanzado en background
    16:27:55 [GEN-BG] terminó en 17.7s
    16:27:57 [CLAIM-ESTADO] "...pero NO hay ninguna generación en marcha"
    16:28:00 [OUT] "¿Te parece bien si genero la rutina ahora mismo con estos datos?"

El plan YA existía. El guardia solo aceptaba `estado_generacion == 'generando'`
como prueba de que la promesa era cierta, y eso es una carrera perdida de
antemano: la generación tarda 10-45 s y la inferencia del modelo 20-130 s, así
que lo NORMAL es que el plan esté listo antes de que el coach acabe de escribir.

El daño no fue el mensaje. Al desmentir una promesa cumplida, el coach ofreció
generar un plan que ya estaba generado; el atleta dijo que sí, y de ahí salió el
bucle de tres regeneraciones seguidas y su "ya borraste 3 veces".

    python -m services.testbed.test_promesa_generacion
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import llm_service as L  # noqa: E402

NUM = "99900000066"

# Una promesa de las que el guardia vigila.
PROMESA = "Estoy armando tu plan, estará listo en unos minutos y te aviso."

ok = fallos = 0
_estado: dict = {}


def chk(desc: str, cond: bool, detalle: str = "") -> None:
    global ok, fallos
    ok += bool(cond)
    fallos += not cond
    print(f"  {'✓' if cond else '✗ FALLA'} {desc}" + (f"  ({detalle})" if detalle else ""))


def desmiente(gen: str, hace_seg: float | None, texto: str = PROMESA) -> list[str]:
    _estado.clear()
    _estado.update({
        "estado_generacion": gen, "semana": 1,
        "generacion_ts": (time.time() - hace_seg) if hace_seg is not None else 0,
    })
    return L._claims_plan_desmentidos(NUM, texto, 1, "")


def main() -> int:
    L.obtener_estado_usuario = lambda n: dict(_estado)
    L._cargar_overrides = lambda n: {}

    print("\n[0] La promesa de prueba es de las que el guardia vigila")
    chk("el regex la reconoce", bool(L._CLAIM_REGENERANDO_RE.search(PROMESA)))

    print("\n[1] EL BUG: la generación terminó DENTRO del turno")
    chk("terminada hace 2 s -> la promesa se sostiene", not desmiente("lista", 2))
    chk("terminada hace 156 s (2.º caso del log, 16:30) -> se sostiene",
        not desmiente("lista", 156))
    chk("en marcha -> se sostiene (esto ya funcionaba)", not desmiente("generando", 5))
    chk("justo en el límite de la ventana -> se sostiene",
        not desmiente("lista", L.VENTANA_GENERACION_RECIENTE - 5))

    print("\n[2] El guardia SIGUE cazando lo que debe")
    chk("nunca se disparó nada (ts=0) -> DESMIENTE", bool(desmiente("", None)))
    chk("estado vacío aunque el ts sea reciente -> DESMIENTE", bool(desmiente("", 5)))
    chk("plan listo pero de hace 2 horas -> DESMIENTE", bool(desmiente("lista", 7200)))
    chk("pasada la ventana -> DESMIENTE",
        bool(desmiente("lista", L.VENTANA_GENERACION_RECIENTE + 60)))

    print("\n[3] Una generación que FALLÓ se cuenta con su causa")
    d = desmiente("error", 10)
    chk("el error reciente se desmiente", bool(d))
    chk("y dice que falló, no que 'no hay nada en marcha'",
        bool(d) and "FALLÓ" in d[0], d[0][:80] if d else "")
    chk("un error viejo cae en el mensaje genérico",
        bool(desmiente("error", 7200)))

    print("\n[4] Sin promesa en el texto no se toca nada")
    for t in ("Aquí tienes tu rutina de hoy bro.",
              "Anotado: 90 kg x 11 @RPE 7.5.",
              ""):
        chk(f"{t[:32]!r} no dispara nada", not desmiente("", None, t))

    print("\n[5] Los demás desmentidos siguen vivos (no se rompió el guardia)")
    _estado.clear()
    _estado.update({"estado_generacion": "lista", "semana": 1,
                    "generacion_ts": time.time()})
    d_pausa = L._claims_plan_desmentidos(
        NUM, "Listo, tu plan queda pausado hasta que vuelvas.", 1, "")
    chk("una pausa inexistente se sigue desmintiendo", bool(d_pausa),
        d_pausa[0][:60] if d_pausa else "")
    d_desc = L._claims_plan_desmentidos(
        NUM, "Ya apliqué una descarga esta semana.", 1, "")
    chk("una descarga inexistente se sigue desmintiendo", bool(d_desc))

    print(f"\n{'='*74}\n  {ok} ok, {fallos} fallos\n{'='*74}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
