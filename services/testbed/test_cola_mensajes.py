# -*- coding: utf-8 -*-
"""
test_cola_mensajes.py — la cola por atleta: en orden, en paralelo y sin perder nada.

Por qué existe
--------------
Lo que sustituye (`BackgroundTasks` + el set `usuarios_procesando`) fallaba en
silencio: el segundo mensaje de un atleta se DESCARTABA sin avisarle, y los
mensajes de una misma entrega de Meta corrían en serie, así que un video de dos
minutos de un atleta bloqueaba el "hola" de otro. Ninguno de esos fallos lanza
una excepción ni deja rastro en el log, así que sin test se vuelven a colar.

Lo que verifica
---------------
1. ORDEN         los mensajes de UN atleta se atienden en el orden que llegaron.
2. NADA SE PIERDE un mensaje que llega con trabajo en curso se atiende después,
                  no se descarta (era el bug de `usuarios_procesando`).
3. PARALELISMO   atletas distintos avanzan a la vez (no se bloquean entre sí).
4. FUSIÓN        varios textos seguidos se atienden como UN turno, y la fusión se
                 corta al llegar a otro tipo (un audio detrás no se absorbe).
5. AISLAMIENTO   un fallo procesando un mensaje no mata el worker del atleta: los
                 siguientes se siguen atendiendo.
6. TOPE          pasado MAX_EN_COLA se rechaza explícitamente (para poder avisar)
                 en vez de crecer sin límite.
7. AVISO         el acuse de recibo se manda UNA vez por ráfaga, no por mensaje.
8. DRENAR        el apagado espera a que se vacíe la cola.

Uso:  python -u -m services.testbed.test_cola_mensajes     (0 = ok, 1 = roto)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from api import cola_mensajes as cm   # noqa: E402

FALLOS: list[str] = []
TRAZA: list[str] = []


def check(condicion: bool, titulo: str, detalle: str = "") -> None:
    if condicion:
        print(f"  ✓ {titulo}")
    else:
        print(f"  ✗ {titulo}" + (f"\n      {detalle}" if detalle else ""))
        FALLOS.append(titulo)


def _trabajo(numero: str, etiqueta: str, tarda: float = 0.01,
             revienta: bool = False) -> cm.Trabajo:
    async def ejecutar():
        TRAZA.append(f"{numero}:{etiqueta}:inicio")
        await asyncio.sleep(tarda)
        if revienta:
            TRAZA.append(f"{numero}:{etiqueta}:revienta")
            raise RuntimeError("fallo simulado")
        TRAZA.append(f"{numero}:{etiqueta}:fin")

    return cm.Trabajo(numero=numero, tipo="otro", etiqueta=etiqueta,
                      ejecutar=ejecutar)


def _trabajo_texto(numero: str, texto: str) -> cm.Trabajo:
    """Réplica del de rutas_whatsapp: fusionable, con `rehacer`."""
    async def ejecutar():
        TRAZA.append(f"{numero}:TURNO({texto})")

    return cm.Trabajo(numero=numero, tipo="texto", etiqueta=f"texto[{texto}]",
                      texto=texto, ejecutar=ejecutar,
                      rehacer=lambda t: _trabajo_texto(numero, t))


async def _vaciar() -> None:
    """Espera a que la cola termine, con tope para que un cuelgue no pare el test."""
    await cm.drenar(timeout=10.0)


def _antes_que(primero: str, segundo: str) -> bool:
    """¿`primero` ocurrió antes de `segundo` en la traza? False si falta alguno.

    Evita que un check reviente con ValueError cuando el fallo real ya lo señaló
    un check anterior: un test que explota no dice qué se rompió.
    """
    if primero not in TRAZA or segundo not in TRAZA:
        return False
    return TRAZA.index(primero) < TRAZA.index(segundo)


# ==============================================================================
# PRUEBAS
# ==============================================================================
async def prueba_orden_y_nada_se_pierde() -> None:
    TRAZA.clear()
    await cm.reiniciar_para_test()

    # Los tres llegan de golpe, el primero mientras no hay nada en curso.
    for i in (1, 2, 3):
        await cm.encolar(_trabajo("A", f"m{i}", tarda=0.02))
    await _vaciar()

    inicios = [t for t in TRAZA if t.endswith(":inicio")]
    check(len(inicios) == 3, "los 3 mensajes se atendieron (ninguno descartado)",
          f"solo se atendieron {len(inicios)}: {TRAZA}")
    check(inicios == ["A:m1:inicio", "A:m2:inicio", "A:m3:inicio"],
          "se atienden en el orden que llegaron",
          f"orden observado: {inicios}")
    # Y en serie: m2 no arranca hasta que m1 termina. `_antes_que` en vez de
    # index() directo para que este check falle limpio si el anterior ya reveló
    # que faltan eventos, en vez de romper el test con un ValueError.
    check(_antes_que("A:m1:fin", "A:m2:inicio"),
          "no se solapan entre sí (un turno por atleta a la vez)",
          f"traza: {TRAZA}")


async def prueba_paralelismo_entre_atletas() -> None:
    TRAZA.clear()
    await cm.reiniciar_para_test()

    # B tarda mucho; C debe adelantarlo, no esperar detrás.
    await cm.encolar(_trabajo("B", "lento", tarda=0.30))
    await cm.encolar(_trabajo("C", "rapido", tarda=0.01))
    await _vaciar()

    check("C:rapido:fin" in TRAZA and "B:lento:fin" in TRAZA,
          "los dos atletas terminan")
    check(_antes_que("C:rapido:fin", "B:lento:fin"),
          "un atleta lento NO bloquea a otro",
          f"traza: {TRAZA}")


async def prueba_fusion_de_textos() -> None:
    TRAZA.clear()
    await cm.reiniciar_para_test()

    # Ocupa el worker para que los textos se acumulen detrás.
    await cm.encolar(_trabajo("D", "ocupado", tarda=0.15))
    for t in ("sentadilla 140x5", "@8", "me quedaron 2"):
        await cm.encolar(_trabajo_texto("D", t))
    await _vaciar()

    turnos = [t for t in TRAZA if ":TURNO(" in t]
    check(len(turnos) == 1, "los 3 textos seguidos se atienden como UN turno",
          f"se generaron {len(turnos)} turnos: {turnos}")
    if turnos:
        check("sentadilla 140x5" in turnos[0] and "@8" in turnos[0]
              and "me quedaron 2" in turnos[0],
              "el turno fusionado lleva los tres mensajes",
              f"contenido: {turnos[0]}")


async def prueba_fusion_se_corta_en_otro_tipo() -> None:
    TRAZA.clear()
    await cm.reiniciar_para_test()

    await cm.encolar(_trabajo("E", "ocupado", tarda=0.15))
    await cm.encolar(_trabajo_texto("E", "primero"))
    await cm.encolar(_trabajo("E", "audio", tarda=0.01))      # corta la fusión
    await cm.encolar(_trabajo_texto("E", "despues"))
    await _vaciar()

    turnos = [t for t in TRAZA if ":TURNO(" in t]
    check(len(turnos) == 2, "un audio en medio corta la fusión (no se absorbe)",
          f"turnos: {turnos}")
    if len(turnos) == 2:
        check("primero" in turnos[0] and "despues" in turnos[1],
              "se respeta el orden real de la conversación",
              f"turnos: {turnos}")
    check(_antes_que("E:TURNO(primero)", "E:audio:inicio")
          and _antes_que("E:audio:inicio", "E:TURNO(despues)"),
          "el audio se atiende entre los dos textos",
          f"traza: {TRAZA}")


async def prueba_fallo_no_mata_el_worker() -> None:
    TRAZA.clear()
    await cm.reiniciar_para_test()

    await cm.encolar(_trabajo("F", "malo", tarda=0.01, revienta=True))
    await cm.encolar(_trabajo("F", "bueno", tarda=0.01))
    await _vaciar()

    check("F:malo:revienta" in TRAZA, "el trabajo que falla efectivamente falló")
    check("F:bueno:fin" in TRAZA,
          "el mensaje siguiente del atleta se atiende igual",
          f"el worker murió con el fallo; traza: {TRAZA}")


async def prueba_tope_de_cola() -> None:
    TRAZA.clear()
    await cm.reiniciar_para_test()

    # El primero entra en ejecución; los siguientes llenan la cola.
    await cm.encolar(_trabajo("G", "ocupado", tarda=0.5))
    aceptados = rechazados = 0
    for i in range(cm.MAX_EN_COLA + 5):
        recibo = await cm.encolar(_trabajo("G", f"m{i}", tarda=0.001))
        aceptados += recibo.aceptado
        rechazados += not recibo.aceptado

    check(rechazados > 0, "pasado el tope se rechaza explícitamente",
          "la cola creció sin límite: no habría forma de avisar al atleta")
    check(aceptados <= cm.MAX_EN_COLA,
          f"nunca más de MAX_EN_COLA ({cm.MAX_EN_COLA}) en espera",
          f"se aceptaron {aceptados}")
    await cm.reiniciar_para_test()


async def prueba_aviso_una_vez_por_rafaga() -> None:
    TRAZA.clear()
    await cm.reiniciar_para_test()

    await cm.encolar(_trabajo("H", "ocupado", tarda=0.20))
    avisos = 0
    for i in range(4):
        recibo = await cm.encolar(_trabajo("H", f"m{i}", tarda=0.01))
        avisos += recibo.avisar

    check(avisos == 1, "el acuse de recibo se manda UNA vez, no por mensaje",
          f"se habrían mandado {avisos} avisos al atleta")

    # El primero de una ráfaga nueva (cola ya vacía) no lleva aviso: no espera.
    await _vaciar()
    recibo = await cm.encolar(_trabajo("H", "solo", tarda=0.01))
    check(not recibo.avisar and not recibo.en_espera,
          "sin nada por delante no se avisa (no se molesta al atleta)")
    await _vaciar()


async def prueba_drenar() -> None:
    TRAZA.clear()
    await cm.reiniciar_para_test()

    await cm.encolar(_trabajo("I", "m1", tarda=0.05))
    await cm.encolar(_trabajo("I", "m2", tarda=0.05))
    quedan = await cm.drenar(timeout=10.0)

    check(quedan == 0, "drenar espera a que se vacíe la cola",
          f"quedaron {quedan} sin atender")
    check(len([t for t in TRAZA if t.endswith(":fin")]) == 2,
          "los dos trabajos terminaron antes de apagar", f"traza: {TRAZA}")
    check(cm.pendientes() == 0 and not cm.estado()["en_curso"],
          "el registro queda limpio tras vaciarse",
          f"estado: {cm.estado()}")


async def main() -> int:
    print("\n=== test_cola_mensajes ===\n")
    for nombre, prueba in (
            ("1. ORDEN / NADA SE PIERDE", prueba_orden_y_nada_se_pierde),
            ("2. PARALELISMO", prueba_paralelismo_entre_atletas),
            ("3. FUSIÓN DE TEXTOS", prueba_fusion_de_textos),
            ("4. FUSIÓN ACOTADA", prueba_fusion_se_corta_en_otro_tipo),
            ("5. AISLAMIENTO DE FALLOS", prueba_fallo_no_mata_el_worker),
            ("6. TOPE DE COLA", prueba_tope_de_cola),
            ("7. AVISO", prueba_aviso_una_vez_por_rafaga),
            ("8. DRENAR", prueba_drenar),
    ):
        print(nombre)
        await prueba()
        print()

    await cm.reiniciar_para_test()
    if FALLOS:
        print(f"ROTO: {len(FALLOS)} comprobación(es) fallaron -> {'; '.join(FALLOS)}")
        return 1
    print("OK: la cola respeta el orden, no pierde mensajes y no bloquea atletas.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
