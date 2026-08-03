# -*- coding: utf-8 -*-
"""
test_bienvenida.py — la carta de presentación al atleta nuevo.

Un atleta que escribe por primera vez recibía como respuesta "¿cuánto pesas?",
sin saber con quién hablaba ni qué podía pedirle. Esta funcionalidad manda una
presentación con las capacidades reales del sistema ANTES de que el orquestador
empiece a pensar, sin interferir con la respuesta a su mensaje.

Lo que hay que fijar, porque son las tres formas de que esto salga mal:

  1. Que se envíe DOS veces (el atleta manda dos mensajes seguidos).
  2. Que la reciban los atletas que YA existen al desplegar el cambio, a mitad
     de su mesociclo.
  3. Que prometa capacidades que el sistema no tiene: el texto es fijo y escrito
     a mano, así que se contrasta contra las herramientas realmente declaradas.

    python -m services.testbed.test_bienvenida
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import llm_service as L  # noqa: E402

NUM = "99900000123"

ok = fallos = 0
_estado: dict = {}
_enviados: list[str] = []


def chk(desc: str, cond: bool, detalle: str = "") -> None:
    global ok, fallos
    ok += bool(cond)
    fallos += not cond
    print(f"  {'✓' if cond else '✗ FALLA'} {desc}" + (f"  ({detalle})" if detalle else ""))


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="bienv_test_")
    L.historial_usuario = lambda n, _d=tmp: os.path.join(_d, f"h_{n}.json")
    L.obtener_estado_usuario = lambda n: dict(_estado)
    L._actualizar_estado_json = lambda n, **kw: _estado.update(kw)
    L._plan_files_existen = lambda n: False
    L.cargar_perfil = lambda n: {}

    import api.rutas_whatsapp as R
    R.enviar_mensaje_whatsapp = lambda tel, txt=None, **kw: _enviados.append(txt or kw.get("texto"))

    def reset(**kw):
        _estado.clear()
        _enviados.clear()
        L.cargar_perfil = lambda n: kw.get("perfil") or {}
        L._plan_files_existen = lambda n: bool(kw.get("plan"))
        h = L.historial_usuario(NUM)
        if kw.get("historial"):
            Path(h).write_text("[]", encoding="utf-8")
        elif os.path.exists(h):
            os.unlink(h)

    print("\n[1] Atleta NUEVO: recibe la presentación")
    reset()
    chk("se detecta como sin estrenar", L._atleta_sin_estrenar(NUM))
    chk("se envía", asyncio.run(L.enviar_bienvenida_si_procede(NUM)))
    chk("y sale un solo mensaje", len(_enviados) == 1, f"{len(_enviados)} mensajes")
    chk("queda la marca en su estado", bool(_estado.get("bienvenida_enviada")))

    print("\n[2] NO se repite en el segundo mensaje")
    chk("ya no cuenta como nuevo", not L._atleta_sin_estrenar(NUM))
    chk("no se vuelve a enviar", not asyncio.run(L.enviar_bienvenida_si_procede(NUM)))
    chk("sigue habiendo un solo mensaje", len(_enviados) == 1, f"{len(_enviados)} mensajes")

    print("\n[3] Los atletas que YA existen no la reciben al desplegar")
    reset(perfil={"peso_kg": 84.5, "meta": "powerlifting"})
    chk("con perfil guardado no es nuevo", not L._atleta_sin_estrenar(NUM))
    reset(historial=True)
    chk("con historial en disco tampoco", not L._atleta_sin_estrenar(NUM))
    reset(plan=True)
    chk("con un plan generado tampoco", not L._atleta_sin_estrenar(NUM))

    print("\n[4] Un fallo comprobando no puede tumbar el turno")
    reset()
    _falla = L.cargar_perfil
    L.cargar_perfil = lambda n: (_ for _ in ()).throw(RuntimeError("base caída"))
    chk("ante un error devuelve False en vez de propagar",
        L._atleta_sin_estrenar(NUM) is False)
    L.cargar_perfil = _falla

    print("\n[5] El mensaje es enviable por WhatsApp")
    m = L.MENSAJE_BIENVENIDA
    chk("no está vacío", bool(m.strip()))
    chk("usa negrita simple de WhatsApp, no markdown", "**" not in m)
    chk("sin encabezados markdown", not any(l.startswith("#") for l in m.splitlines()))
    chk("longitud razonable para trocear", 500 < len(m) < 4000, f"{len(m)} chars")

    print("\n[6] No promete nada que el sistema no sepa hacer")
    nombres = {t.get("function", {}).get("name", "") for t in L.cargar_herramientas()}
    # Cada afirmación de la carta, con la herramienta (o el pipeline) que la sostiene.
    respaldos = {
        "1RM": "calcular_rm_exacto",
        "mesociclo": "generar_rutina",
        "con cuánto entrarle hoy": "predecir_carga_optima",
        "descarga técnica": "aplicar_descarga",
        "series por músculo": "consultar_volumen_plan",
        "volumen recuperable": "analizar_mrv",
        "cambio de ejercicio": "sustituir_ejercicio",
        "pausar el plan": "pausar_plan",
        "gráfica": "enviar_grafica_progreso",
        "evolución": "consultar_progreso",
        "técnica": "explicar_tecnica_ejercicio",
        "composición corporal": "evaluar_perfil_fisico",
        "fuerza relativa": "evaluar_nivel_fuerza",
        "reajuste por RPE": "autorregular_dia",
        "memoria de lesiones": "guardar_en_expediente_personal",
        "literatura científica": "consultar_libro_cientifico",
        "bitácora de series": "registrar_serie_completada",
    }
    for afirmacion, tool in respaldos.items():
        chk(f"'{afirmacion}' la sostiene {tool}", tool in nombres)
    # El vídeo no es una tool: es el pipeline VBT del webhook.
    chk("el análisis de vídeo (VBT) existe como pipeline",
        (Path(L.__file__).parent / "vbt_service.py").exists())

    print(f"\n{'='*74}\n  {ok} ok, {fallos} fallos\n{'='*74}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
