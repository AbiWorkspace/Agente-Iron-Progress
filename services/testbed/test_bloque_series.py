"""test_bloque_series.py — "4x8 con 60 kg" son 4 series de 8, no 8 kg por 4 reps.

Cubre `detectar_bloque_de_series` (v13) y las dos funciones que la consumen,
`detectar_serie_en_mensaje` y `detectar_series_multiples`.

POR QUÉ IMPORTA: la notación de PRESCRIPCIÓN (series x reps) y la de MARCA
(kilos x reps) usan la misma "x". El parser viejo solo entendía la segunda y
además ordenaba los números por tamaño, así que "hice 4x8 con 60 kg" entraba al
diario como 8 kg x 4 reps. Eso no es un dato incompleto: es un dato FALSO que
contamina el 1RM estimado y todas las cargas que se predicen a partir de él.

La pista que desambigua es el peso dicho CON UNIDAD: si el atleta ya declaró los
kilos aparte, la "x" no puede ser de kilos. Sin esa pista no se adivina.

Uso:  python3 -u -m services.testbed.test_bloque_series
"""
import sys

from services import llm_service

bloque = llm_service.detectar_bloque_de_series
una = llm_service.detectar_serie_en_mensaje
multiples = llm_service.detectar_series_multiples

fallos: list[str] = []


def chk(titulo: str, condicion: bool, detalle: str = "") -> None:
    print(f"  {'OK ' if condicion else 'FALLA'}  {titulo}" + (f"  ({detalle})" if detalle else ""))
    if not condicion:
        fallos.append(titulo)


def _resumen(series: list[dict]) -> str:
    if not series:
        return "[]"
    s = series[0]
    return f"{len(series)}x -> {s['peso']:g}kg x {s['reps']} rpe={s['rpe']} ej={s['ejercicio']}"


def main() -> int:
    print("\n1. Notación de PRESCRIPCIÓN: N series de R reps, peso aparte")
    casos_ok = [
        ("hice 4x8 con 60 kg",                 4, 8, 60.0),
        ("3 series de 12 con 40 kilos",        3, 12, 40.0),
        ("hoy hice 5 sets de 5 con 100 kg",    5, 5, 100.0),
        ("4 series x 10 con 80 kg",            4, 10, 80.0),
        ("hice 4×8 con 60 kg",                 4, 8, 60.0),          # × unicode
        ("3 series de 10 con 62.5 kg",         3, 10, 62.5),         # decimal con punto
        ("3 series de 10 con 62,5 kilos",      3, 10, 62.5),         # decimal con coma
    ]
    for texto, n, reps, peso in casos_ok:
        r = bloque(texto)
        chk(f"{texto!r}", len(r) == n and r and r[0]["reps"] == reps and r[0]["peso"] == peso,
            _resumen(r))

    print("\n2. Lo que NO debe tocar (sin la pista del peso, no se adivina)")
    for texto in ["hice 4x8", "hoy hice 3 series de 12", "me siento fatal", ""]:
        chk(f"{texto!r} -> []", bloque(texto) == [], _resumen(bloque(texto)))

    print("\n3. Notación de MARCA: no debe leerse como bloque")
    # "100 kg x 5" es UNA serie de 100 kg por 5 reps. Si esto se colara como
    # bloque saldrían 100 series (o se recortaría por el tope de 12).
    for texto in ["hice 100 kg x 5", "hice una serie de 100 kg x 5", "levanté 120 kg por 3"]:
        chk(f"{texto!r} -> []", bloque(texto) == [], _resumen(bloque(texto)))

    print("\n4. El ejercicio se detecta si se nombra")
    r = bloque("hice 4x8 de sentadilla con 60 kg")
    chk("sentadilla", bool(r) and r[0]["ejercicio"] == "sentadilla", _resumen(r))
    r = bloque("4 series de 10 en banca con 70 kg")
    chk("press banco plano", bool(r) and r[0]["ejercicio"] == "press banco plano", _resumen(r))
    r = bloque("hice 4x8 con 60 kg")
    chk("sin nombrarlo queda en None", bool(r) and r[0]["ejercicio"] is None, _resumen(r))

    print("\n5. El RPE viaja a todas las series del bloque")
    r = bloque("hice 4x8 con 60 kg rpe 8")
    chk("rpe 8 en las 4", len(r) == 4 and all(s["rpe"] == 8.0 for s in r), _resumen(r))

    print("\n6. Rangos: un bloque imposible no se inventa")
    chk("20 series -> []", bloque("hice 20x8 con 60 kg") == [], _resumen(bloque("hice 20x8 con 60 kg")))
    chk("40 reps -> []", bloque("3 series de 40 con 60 kg") == [])

    print("\n7. detectar_serie_en_mensaje NO invierte los números")
    r = una("hice 4x8 con 60 kg")
    chk("peso 60, reps 8", bool(r) and r["peso"] == 60.0 and r["reps"] == 8,
        f"{r}")
    # El caso que motivó todo: sin el arreglo esto daba peso=8 (o 4) y reps=4.
    chk("el peso NO es 8 ni 4", bool(r) and r["peso"] not in (8.0, 4.0), f"{r}")
    # La notación de marca de siempre debe seguir funcionando igual.
    r = una("hice 100x5")
    chk("marca clásica '100x5' intacta", bool(r) and r["peso"] == 100.0 and r["reps"] == 5, f"{r}")

    print("\n8. detectar_series_multiples expande el bloque")
    r = multiples("hice 4x8 con 60 kg")
    chk("devuelve 4 series", len(r) == 4, _resumen(r))
    chk("todas iguales", len({(s["peso"], s["reps"]) for s in r}) == 1, _resumen(r))
    # Un volcado de marcas sueltas NO es un bloque: debe seguir enumerando.
    r = multiples("hice 100x5, 105x3 y 110x1")
    chk("volcado de marcas sigue enumerando", len(r) == 3,
        str([(s["peso"], s["reps"]) for s in r]))

    print("\n9. PREGUNTA vs. REPORTE (es_pregunta_no_reporte)")
    # La notación por sí sola no distingue: '4x8 con 60 kg' es idéntica en
    # "hice 4x8" y en "¿hago 4x8?". El gate vive en el orquestador.
    pregunta = llm_service.es_pregunta_no_reporte

    print("   9a. Preguntas: NO deben registrarse")
    for texto in ["¿hago 4x8 con 60 kg?",
                  "hago 4x8 con 60 kg?",                  # sin signo de apertura
                  "que peso uso para 4x8?",
                  "¿me conviene 3 series de 12 con 40 kilos",
                  "cuantas series hago con 60 kg",
                  "debo hacer 4x8 con 60 kg?"]:
        chk(f"{texto!r} -> pregunta", pregunta(texto) is True)

    print("   9b. Reportes: DEBEN registrarse (falso negativo = serie perdida)")
    for texto in ["hice 4x8 con 60 kg",
                  "hice 4x8 con 60 kg, está bien?",        # reporte CON pregunta detrás
                  "hoy hice 4x8 con 60 kg",
                  "4x8 con 60 kg",                         # seco, sin verbo
                  "termine 4x8 con 60 kg",
                  "acabo de meter 3 series de 12 con 40 kilos",
                  "ya llevo 3 series de 10 con 50 kg",
                  "saqué 100x5"]:
        chk(f"{texto!r} -> reporte", pregunta(texto) is False)

    print("   9c. El gate solo se aplica cuando hay notación de serie")
    chk("'como estas?' no es asunto suyo (no hay serie)", una("como estas?") is None)

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
