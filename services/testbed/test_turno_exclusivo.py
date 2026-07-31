# -*- coding: utf-8 -*-
"""
test_turno_exclusivo.py — el turno de un atleta no se intercala con el de otro.

Por qué existe
--------------
SEM_LLM se tomaba y se soltaba en CADA llamada al modelo, y un turno hace varias
(clasificador de intención, llamada principal, síntesis post-tools, algún
_forzar_respuesta_texto). Con dos atletas hablando a la vez esas llamadas se
intercalaban, y la caché de prompts de Ollama —topada al num_ctx de 32k, o sea
~dos prompts de 15k— desalojaba el prefijo de uno para meter el del otro. Ambos
pagaban el reprefill completo de ~20.600 tokens: 17-26 s por turno, justo la
regresión que costó eliminar dejando de podar la telemetría.

Nada de eso produce un error. El bot responde igual, solo tarda el doble, así
que sin un test que lo fije se vuelve a romper sin que nadie se entere.

Lo que verifica
---------------
1. ATOMICIDAD    dos turnos concurrentes NO intercalan sus inferencias.
2. SIN TURNO     fuera de un turno se sigue tomando y soltando por llamada
                 (readiness, extractor de tríada del audio, generador de rutina).
3. EXCEPCIÓN     si el turno revienta entre dos inferencias, SEM_LLM se libera.
4. HERENCIA      una tarea de fondo lanzada dentro de un turno no puede provocar
                 un release() de más (el fallo silencioso más grave: dejaría
                 SEM_LLM abierto y volverían las inferencias solapadas que
                 congelan el equipo por thrashing de VRAM).
5. PEREZA        el semáforo NO se toma hasta la primera inferencia: la fase de
                 preparación (RAG, SQLite, disco) de varios atletas debe solaparse.

Uso:  python -m services.testbed.test_turno_exclusivo     (0 = ok, 1 = roto)
"""
from __future__ import annotations

import ast
import asyncio
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from services import llm_service as llm   # noqa: E402

FALLOS: list[str] = []


def check(condicion: bool, titulo: str, detalle: str = "") -> None:
    if condicion:
        print(f"  ✓ {titulo}")
    else:
        print(f"  ✗ {titulo}" + (f"\n      {detalle}" if detalle else ""))
        FALLOS.append(titulo)


# ==============================================================================
# DOBLE DEL MODELO
# ==============================================================================
# Registra el orden REAL de entrada y salida de cada inferencia. El sleep no es
# decorativo: sin ceder el control al event loop, las corrutinas se ejecutarían
# enteras una detrás de otra y el test pasaría incluso con el semáforo roto.
TRAZA: list[tuple[str, str]] = []


async def _chat_falso(**kwargs):
    quien = kwargs.get("_quien", "?")
    TRAZA.append((quien, "entra"))
    await asyncio.sleep(0.01)
    TRAZA.append((quien, "sale"))
    return {"message": {"content": "ok", "thinking": ""}, "done_reason": "stop"}


class _ClienteFalso:
    def chat(self, **kwargs):
        # chat_llm no pasa 'quien' al cliente, pero el test necesita saber quién
        # llama: se recupera del primer mensaje, que aquí lo lleva a propósito.
        quien = ""
        for m in kwargs.get("messages") or []:
            quien = str(m.get("content") or "")
            break
        return _chat_falso(_quien=quien, **kwargs)


async def _llamar(quien: str) -> None:
    await llm.chat_llm([{"role": "user", "content": quien}], quien=quien)


def _bloques(traza: list[tuple[str, str]]) -> list[str]:
    """Secuencia de dueños consecutivos: [('A','entra'),('A','sale'),('B',...)] -> ['A','B']."""
    salida = []
    for quien, _evento in traza:
        base = quien.split("-")[0]
        if not salida or salida[-1] != base:
            salida.append(base)
    return salida


# ==============================================================================
# PRUEBAS
# ==============================================================================
async def prueba_atomicidad() -> None:
    """Dos turnos de 3 inferencias cada uno, lanzados a la vez."""
    TRAZA.clear()

    async def turno(nombre: str):
        async with llm.turno_exclusivo_llm():
            for i in range(3):
                await _llamar(f"{nombre}-{i}")

    await asyncio.gather(turno("A"), turno("B"))

    bloques = _bloques(TRAZA)
    check(len(TRAZA) == 12, "las 6 inferencias entraron y salieron",
          f"traza de {len(TRAZA)} eventos: {TRAZA}")
    check(bloques in (["A", "B"], ["B", "A"]),
          "un turno completo antes del otro (sin intercalar)",
          f"orden observado: {' -> '.join(bloques)}")


async def prueba_sin_turno() -> None:
    """Fuera de un turno el comportamiento es el de siempre: por llamada."""
    TRAZA.clear()

    async def suelto(nombre: str):
        for i in range(3):
            await _llamar(f"{nombre}-{i}")

    await asyncio.gather(suelto("C"), suelto("D"))

    bloques = _bloques(TRAZA)
    check(len(TRAZA) == 12, "las 6 inferencias sueltas se ejecutaron",
          f"traza de {len(TRAZA)} eventos")
    # Se intercalan a propósito: el readiness o el extractor de tríada del audio
    # no deben quedar detrás de un turno entero.
    check(len(bloques) > 2, "sin turno SÍ se alternan (no se retiene el semáforo)",
          f"orden observado: {' -> '.join(bloques)}")


async def prueba_solapamiento_serializado() -> None:
    """Con tres turnos a la vez, jamás hay dos inferencias abiertas."""
    TRAZA.clear()

    async def turno(nombre: str):
        async with llm.turno_exclusivo_llm():
            for i in range(2):
                await _llamar(f"{nombre}-{i}")

    await asyncio.gather(turno("E"), turno("F"), turno("G"))

    # Recorre la traza llevando la cuenta de inferencias abiertas: si en algún
    # momento pasa de 1, dos peticiones se solaparon contra Ollama (el thrashing
    # de VRAM que SEM_LLM existe para evitar).
    abiertas = maximo = 0
    for _quien, evento in TRAZA:
        abiertas += 1 if evento == "entra" else -1
        maximo = max(maximo, abiertas)

    check(maximo == 1, "jamás dos inferencias abiertas al mismo tiempo",
          f"llegó a {maximo} simultáneas")
    # Exactamente 3 bloques = ningún turno se partió por la mitad. Si alguno se
    # intercalara, aparecerían 4 o más (E -> F -> E -> ...).
    bloques = _bloques(TRAZA)
    check(len(bloques) == 3, "los tres turnos salen enteros, uno detrás de otro",
          f"orden observado: {' -> '.join(bloques)}")


async def prueba_excepcion_libera() -> None:
    """Un fallo entre inferencias no puede dejar el semáforo tomado."""
    TRAZA.clear()

    async def turno_que_revienta():
        async with llm.turno_exclusivo_llm():
            await _llamar("H-0")
            raise RuntimeError("fallo simulado a mitad de turno")

    try:
        await turno_que_revienta()
    except RuntimeError:
        pass

    check(not llm.SEM_LLM.locked(), "SEM_LLM libre tras la excepción",
          "quedó tomado: el bot estaría mudo para TODOS los atletas")
    # Y el turno siguiente debe poder trabajar.
    async with llm.turno_exclusivo_llm():
        await _llamar("I-0")
    check(not llm.SEM_LLM.locked(), "un turno posterior funciona y libera")


async def prueba_herencia_de_contexto() -> None:
    """Una tarea de fondo nacida dentro de un turno no corrompe el semáforo.

    `asyncio.create_task` copia los ContextVar, así que la tarea hija ve el
    guardia del padre. Si lo marcara, el padre haría un release() de un semáforo
    que en realidad tomó la hija, y SEM_LLM acabaría con hueco para DOS
    inferencias simultáneas: el thrashing de VRAM, sin ningún error a la vista.
    """
    valor_inicial = llm.SEM_LLM._value   # introspección deliberada: es lo que se corrompe

    # (a) El patrón REAL de _generar_rutina_background: toma SEM_LLM por su
    #     cuenta y trabaja sin pasar por chat_llm (recibir_peticion va a un hilo).
    async def fondo_como_el_generador():
        llm._soltar_guardia_heredado()
        async with llm.SEM_LLM:
            await asyncio.sleep(0.01)

    async with llm.turno_exclusivo_llm():
        await _llamar("J-0")
        tarea = asyncio.create_task(fondo_como_el_generador())
        await asyncio.sleep(0)           # la deja arrancar dentro del turno
    await tarea

    check(llm.SEM_LLM._value == valor_inicial,
          "tras el generador de rutina el contador vuelve a su valor",
          f"esperado {valor_inicial}, quedó {llm.SEM_LLM._value}: "
          "hay release() de más y caben dos inferencias simultáneas")

    # (b) Una tarea hija que sí usa chat_llm: desvinculada, tiene que ESPERAR su
    #     propio turno del semáforo en vez de colarse entre dos inferencias del
    #     turno padre.
    TRAZA.clear()

    async def fondo_con_chat():
        llm._soltar_guardia_heredado()
        await _llamar("BG-0")

    async with llm.turno_exclusivo_llm():
        await _llamar("K-0")
        tarea = asyncio.create_task(fondo_con_chat())
        await asyncio.sleep(0)
        await _llamar("K-1")
    await tarea

    check(_bloques(TRAZA) == ["K", "BG"],
          "la tarea de fondo desvinculada no se cuela dentro del turno",
          f"orden observado: {' -> '.join(_bloques(TRAZA))}")
    check(llm.SEM_LLM._value == valor_inicial,
          "el contador sigue sano tras la tarea de fondo con chat_llm",
          f"esperado {valor_inicial}, quedó {llm.SEM_LLM._value}")
    check(not llm.SEM_LLM.locked(), "SEM_LLM libre al final")


async def prueba_adquisicion_perezosa() -> None:
    """Abrir un turno no debe tomar el semáforo: solo la primera inferencia."""
    TRAZA.clear()
    async with llm.turno_exclusivo_llm():
        check(not llm.SEM_LLM.locked(),
              "abrir el turno NO toma el semáforo todavía",
              "se toma al entrar: la preparación (RAG, disco) de otros atletas "
              "quedaría bloqueada sin motivo")
        await _llamar("K-0")
        check(llm.SEM_LLM.locked(), "tras la primera inferencia sí está tomado")
    check(not llm.SEM_LLM.locked(), "al cerrar el turno se libera")


async def prueba_toda_tarea_de_fondo_se_desvincula() -> None:
    """Comprobación ESTÁTICA: toda tarea lanzada con create_task desde un turno
    tiene que llamar a `_soltar_guardia_heredado`.

    Va sobre el fuente y no sobre el comportamiento porque el fallo es invisible
    en ejecución: no lanza excepción, solo deja el semáforo abierto. Si mañana se
    añade otra tarea de fondo al orquestador y se olvida la desvinculación, este
    test lo dice; en producción solo se vería como el equipo congelándose.
    """
    fuente = (RAIZ / "services" / "llm_service.py").read_text(encoding="utf-8")

    lanzadas = re.findall(r"asyncio\.create_task\(\s*(\w+)\s*\(", fuente)
    check(bool(lanzadas), "se encontraron tareas de fondo que revisar",
          "el patrón create_task(nombre(...)) ya no casa: revisa este test")

    # El cuerpo se localiza con `ast`, no con regex: la corrutina de fondo suele
    # definirse ANIDADA dentro del lanzador (`_lanzar_compresion_diferida` tiene
    # su `async def _tarea()` dentro), y buscando `^async def` a nivel 0 no
    # aparecía — el test la marcaba como "no encontrada" y daba un fallo que no
    # era tal. ast las encuentra a cualquier profundidad.
    arbol = ast.parse(fuente)
    definiciones = {n.name: n for n in ast.walk(arbol)
                    if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))}

    sin_desvincular = []
    for nombre in set(lanzadas):
        nodo = definiciones.get(nombre)
        if nodo is None:
            sin_desvincular.append(f"{nombre} (no se encontró su definición)")
            continue
        if "_soltar_guardia_heredado()" not in (ast.get_source_segment(fuente, nodo) or ""):
            sin_desvincular.append(nombre)

    check(not sin_desvincular,
          "toda tarea de fondo se desvincula del guardia del turno",
          f"les falta _soltar_guardia_heredado(): {', '.join(sin_desvincular)}")


async def main() -> int:
    llm.cliente_ollama = _ClienteFalso()
    print("\n=== test_turno_exclusivo ===\n")
    for nombre, prueba in (
            ("1. ATOMICIDAD", prueba_atomicidad),
            ("2. SIN TURNO", prueba_sin_turno),
            ("3. SERIALIZACIÓN", prueba_solapamiento_serializado),
            ("4. EXCEPCIÓN", prueba_excepcion_libera),
            ("5. HERENCIA", prueba_herencia_de_contexto),
            ("6. PEREZA", prueba_adquisicion_perezosa),
            ("7. DESVINCULACIÓN", prueba_toda_tarea_de_fondo_se_desvincula),
    ):
        print(nombre)
        await prueba()
        print()

    if FALLOS:
        print(f"ROTO: {len(FALLOS)} comprobación(es) fallaron -> {'; '.join(FALLOS)}")
        return 1
    print("OK: el turno del atleta es atómico frente al modelo.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
