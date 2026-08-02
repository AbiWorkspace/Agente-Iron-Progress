"""test_claims_generacion.py — promesas de plan que nadie está construyendo.

Extiende el guardia de estado del plan (v12) a los dos claims que se le
escapaban, hallados en el QA de interacción del 2026-07-30:

  ATLETA: "sí, confírmalo, regenera el plan"
  COACH : "Listo, confirmado. Cambio de frecuencia anotado: pasamos a *3 días
           semanales*. El sistema está reconstruyendo tu mesociclo ahora mismo.
           Dame unos minutos y te paso el nuevo plan cuando esté listo."
  REAL  : estado_generacion='lista', CERO herramientas, ningún cambio en disco.

Es la mentira más cara del catálogo porque no se descubre en el turno: se
descubre al día siguiente, cuando el plan prometido no existe. El guardia v8
no la veía (exige cifras en el mensaje del atleta) y el v12 tampoco (solo
cubría descarga, pausa, reanudación y avance de día).

Aquí también se fija la plantilla de honestidad y el filtro de jerga.

Uso:  python3 -u -m services.testbed.test_claims_generacion
"""
import sys

from services import llm_service as llm

fallos: list[str] = []


def chk(titulo: str, condicion: bool, detalle: str = "") -> None:
    print(f"  {'OK ' if condicion else 'FALLA'}  {titulo}" + (f"  ({detalle})" if detalle else ""))
    if not condicion:
        fallos.append(titulo)


# Texto literal que salió en el QA, y variantes que un modelo produce igual.
PROMESAS = [
    "El sistema está reconstruyendo tu mesociclo ahora mismo para adaptarlo.",
    "Estoy regenerando tu plan con la nueva frecuencia.",
    "Dame unos minutos y te paso el nuevo plan cuando esté listo.",
    "Ya estamos armando tu rutina nueva.",
    "Tu mesociclo estará listo en unos minutos.",
]
# Cosas que un coach honesto SÍ puede decir sin que salte el guardia.
INOCENTES = [
    "¿Quieres que te arme un plan nuevo con esa frecuencia?",
    "Si me confirmas, lo rehacemos desde cero.",
    "Hoy toca *Día 2: Pierna*. Empieza con sentadilla.",
    "Tu plan sigue igual, no he tocado nada.",
    "Cuando vuelvas del viaje retomamos donde lo dejaste.",
]


def main() -> int:
    print("\n1. PROMESA DE GENERACIÓN sin generación en marcha -> desmentida")
    for t in PROMESAS:
        d = llm._CLAIM_REGENERANDO_RE.search(t)
        chk(f"detecta: {t[:58]!r}", bool(d))

    print("\n2. Lo que un coach honesto puede decir -> NO se toca")
    for t in INOCENTES:
        d = llm._CLAIM_REGENERANDO_RE.search(t)
        chk(f"deja pasar: {t[:58]!r}", not d, d.group(0) if d else "")

    print("\n3. CAMBIO DE FRECUENCIA dado por hecho")
    for t in ["Cambio de frecuencia anotado: pasamos a *3 días semanales*.",
              "Listo, pasamos a 3 días.",
              "Ya actualicé tu frecuencia."]:
        chk(f"detecta: {t[:58]!r}", bool(llm._CLAIM_CAMBIO_DIAS_RE.search(t)))
    for t in ["¿Quieres pasar a 3 días semanales?",
              "Entrenas 4 días por semana."]:
        m = llm._CLAIM_CAMBIO_DIAS_RE.search(t)
        chk(f"deja pasar: {t[:58]!r}", not m, m.group(0) if m else "")

    print("\n3b. BORRADO dado por hecho con el plan intacto")
    # Literal del QA: "Hecho, plan eliminado. Vamos a armar uno nuevo para 3 días."
    # El plan seguía en disco y confirmar_borrado valía 0.
    for t in ["Hecho, plan eliminado. Vamos a armar uno nuevo.",
              "Ya borré tu plan, empezamos de cero.",
              "Tu rutina quedó eliminada."]:
        chk(f"detecta: {t[:58]!r}", bool(llm._CLAIM_BORRADO_RE.search(t)))
    for t in ["¿Seguro que quieres que borre tu plan?",
              "Si lo confirmas, elimino el plan y empezamos de cero."]:
        m = llm._CLAIM_BORRADO_RE.search(t)
        chk(f"deja pasar: {t[:58]!r}", not m, m.group(0) if m else "")

    print("\n4. FILTRO DE JERGA — 'el sistema' no puede llegar al atleta")
    casos = [
        ("Confírmame el número para actualizar tu récord en el sistema.", "sistema"),
        ("El sistema está reconstruyendo tu mesociclo.", "sistema"),
        ("Eso ya quedó guardado en el sistema.", "sistema"),
    ]
    for texto, prohibida in casos:
        limpio = llm.limpiar_texto_para_whatsapp(texto)
        chk(f"limpia: {texto[:52]!r}", prohibida not in limpio.lower(), f"-> {limpio}")

    print("\n5. Términos legítimos de entrenamiento que NO se deben tocar")
    for t in ["Subirlo al doble te fríe el sistema nervioso central.",
              "El descanso protege tu sistema inmune."]:
        limpio = llm.limpiar_texto_para_whatsapp(t)
        chk(f"respeta: {t[:52]!r}", limpio == t, f"-> {limpio}")

    print("\n6. La gramática sobrevive a la sustitución")
    limpio = llm.limpiar_texto_para_whatsapp("El sistema está reconstruyendo tu mesociclo.")
    chk("queda en castellano correcto", limpio == "Se está reconstruyendo tu mesociclo.", limpio)

    print("\n" + "=" * 60)
    if fallos:
        print(f"{len(fallos)} FALLOS:")
        for f in fallos:
            print(f"  - {f}")
    else:
        print("Todos los checks pasan.")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
