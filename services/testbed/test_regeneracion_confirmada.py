# -*- coding: utf-8 -*-
"""
test_regeneracion_confirmada.py — el modelo no puede rehacer el plan por su cuenta.

Sesión real del 2026-07-30:

    19:04 IN: 'Pero yo te dije powerbuilding'
    19:07 [TOOL] generar_rutina({"meta": "powerbuilding", "rm_banca_kg": 59.3, ...})
    19:07 [GEN] pipeline lanzado en background

Una queja sobre la meta le costó al atleta su plan entero, sin que nadie le
preguntara. Python protege la regeneración con un flujo de dos pasos
(confirmar_regeneracion -> confirmar_datos_regen) porque reemplazar un plan es
irreversible, pero ese flujo solo cubría la intención detectada por regex: el
modelo podía llamar a la herramienta y saltárselo entero.

Encima se rehízo con el 1RM de banca corrupto (59,3 kg), así que el motor lo bajó
de 'avanzado' a 'intermedio' y le dio MENOS volumen — justo lo contrario de lo
que estaba pidiendo.

Este test levanta un turno REAL del orquestador con el modelo simulado emitiendo
la tool call, y comprueba que la herramienta se intercepta.

    python -m services.testbed.test_regeneracion_confirmada
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from services import llm_service as L  # noqa: E402

NUM = "99900000088"

PLAN = [{"Semana_Abs": 1, "Enfoque": "Torso", "Ejercicios": [
    {"Ejercicio": "Press Banco Plano", "Series": 4, "Reps": 10, "RPE": 7.5, "1RM": "70.0%"}]}]

ok = fallos = 0
_estado: dict = {}
_generaciones: list[dict] = []      # espía: ¿se disparó el pipeline?
_perfil_escrito: dict = {}          # espía: ¿qué se guardó en la ficha?
_tool_emitida = True                # el modelo emite generar_rutina en la 1.ª vuelta


def chk(desc: str, cond: bool, detalle: str = "") -> None:
    global ok, fallos
    ok += bool(cond)
    fallos += not cond
    print(f"  {'✓' if cond else '✗ FALLA'} {desc}" + (f"  ({detalle})" if detalle else ""))


class _ClienteFalso:
    """Primera llamada: emite generar_rutina. Siguientes: redacta texto."""
    def __init__(self):
        self.llamadas = 0

    async def chat(self, **kw):
        self.llamadas += 1
        if self.llamadas == 1 and _tool_emitida and kw.get("tools"):
            return {"message": {"content": "", "thinking": "", "tool_calls": [
                {"function": {"name": "generar_rutina",
                              "arguments": {"meta": "powerbuilding", "dias": 6}}}]},
                    "done_reason": "stop"}
        return {"message": {"content": "¿Quieres que te lo rehaga desde cero?",
                            "thinking": ""}, "done_reason": "stop"}


async def _turno(mensaje: str) -> str:
    return await L._generar_respuesta_coach_interno(NUM, mensaje)


def _montar(tmp: str, con_plan: bool) -> None:
    """Deja el orquestador con lo mínimo para correr un turno sin tocar nada real."""
    _estado.clear()
    _estado.update({"semana": 1, "dia": 1, "estado_generacion": "lista" if con_plan else "",
                    "fecha_programada": "2026-07-30", "ultima_fecha_completada": ""})
    _generaciones.clear()
    _perfil_escrito.clear()

    L.historial_usuario = lambda n, _t=tmp: str(Path(_t) / f"h_{n}.json")
    L.obtener_estado_usuario = lambda n: dict(_estado)
    L._actualizar_estado_json = lambda n, **kw: _estado.update(kw)
    L._plan_files_existen = lambda n: con_plan
    L.obtener_rutina_del_json = lambda n, s, d, solo_hoy=False: (
        "*HOY — Semana 1 · Día 1 · Torso*\n  • Press Banco Plano: 4x10 — *110 kg* (70% RM)"
        if con_plan else "ESTADO DE RUTINA: Vacío (no hay plan generado para esa semana).")
    L.obtener_dashboard_analitico = lambda n: "SERIES REGISTRADAS HOY: ninguna todavía."
    L._sesiones_semana_cache = lambda n, s: (PLAN if con_plan else [])
    L.cargar_perfil = lambda n: {"peso_kg": 84.5, "altura_m": 1.73, "edad": 24,
                                 "genero": "hombre", "porcentaje_grasa": 20.0,
                                 "dias_semana": 6, "meta": "powerlifting"}
    L.cargar_rms = lambda n: {"press banco plano": 157.1, "sentadilla": 216.7,
                              "peso muerto": 258.1, "dominadas": 36.9}
    L.calcular_faltantes = lambda n: []
    L._cargar_overrides = lambda n: {}

    async def _sin_rag(n, m): return ""
    L.construir_contexto_enriquecido = _sin_rag
    L.consultar_expediente_atleta = lambda n, c, vector=None: "Sin datos"
    L._rectificar_ultima_serie = lambda n, m: None
    L._registrar_equipamiento = lambda n, m: None
    L.actualizar_perfil = lambda n, **kw: _perfil_escrito.update(kw)
    L._lanzar_compresion_diferida = lambda n, r: None

    async def _espia(args, n, texto):
        _generaciones.append(dict(args))
        return "Generación DISPARADA en segundo plano (tarda 2-3 minutos)."
    L._handle_generar_rutina = _espia

    L.historial_memoria.pop(NUM, None)
    L._firmas_historial.pop(NUM, None)
    L.cliente_ollama = _ClienteFalso()


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="regen_test_")

    print("\n[1] EL BUG: el modelo llama generar_rutina con un plan ACTIVO")
    _montar(tmp, con_plan=True)
    salida = asyncio.run(_turno("Pero yo te dije powerbuilding"))
    chk("el plan NO se regeneró", not _generaciones,
        f"se disparó con {_generaciones[0] if _generaciones else ''}")
    chk("queda pendiente la confirmación del atleta",
        bool(_estado.get("confirmar_regeneracion")),
        f"confirmar_regeneracion={_estado.get('confirmar_regeneracion')}")
    chk("y el atleta recibe una respuesta", bool(salida and salida.strip()),
        repr(salida[:70]) if salida else "")
    # Lo que pidió de verdad —el cambio de enfoque— no se pierde por el candado:
    # se guarda ya, y lo único que espera es el sí para rehacer el plan.
    chk("el cambio de meta SÍ se guarda en su ficha",
        _perfil_escrito.get("meta") == "powerbuilding", str(_perfil_escrito))

    print("\n[2] SIN plan que destruir, generar sigue funcionando")
    _montar(tmp, con_plan=False)
    asyncio.run(_turno("armame una rutina de powerbuilding"))
    chk("la generación SÍ se dispara", bool(_generaciones),
        "no se disparó" if not _generaciones else str(_generaciones[0])[:60])

    print("\n[3] Con la verificación ya confirmada, tampoco se bloquea")
    _montar(tmp, con_plan=True)
    _estado["confirmar_datos_regen"] = 1
    asyncio.run(_turno("dale, genera el plan"))
    chk("el atleta ya había dicho que sí: se genera", bool(_generaciones))

    print("\n[4] La regla está en el system prompt, no solo en el código")
    prompt = L.cargar_system_prompt()
    chk("prohíbe generar_rutina por nombre", "NUNCA generar_rutina" in prompt)
    chk("y dice que rehacer es irreversible", "IRREVERSIBLE" in prompt)
    chk("sin romper el resto del prompt",
        "<fuente_de_verdad>" in prompt and "<creacion_de_plan>" in prompt)

    print(f"\n{'='*74}\n  {ok} ok, {fallos} fallos\n{'='*74}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
