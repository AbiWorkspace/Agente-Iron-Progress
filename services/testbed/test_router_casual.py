# -*- coding: utf-8 -*-
"""
test_router_casual.py — quién se queda sin herramientas, y quién no.

`es_casual` apaga CUATRO cosas del turno: las 22 tools, el thinking, el RAG del
expediente y la telemetría completa. Decidía por ausencia: sin palabra de una
lista de gimnasio y con <=60 caracteres, se apagaba todo.

Medido sobre frases que un atleta dice de verdad, 9 de 19 se quedaban mudas —
"tengo hernia discal", "me operaron del menisco", "tomo creatina", "soy
vegetariano". No es que el modelo no usara la herramienta: no la tenía.

Los dos errores NO cuestan lo mismo:
  · falso CASUAL  -> el dato del atleta se pierde y el modelo responde de
                     memoria. Es el bug.
  · falso NORMAL  -> unos segundos de más en un saludo. Es el precio.
Por eso el corpus exige acierto total en GUARDABLE y ENTRENA, y tolera que
algún TRIVIAL se pase de listo.

    python -m services.testbed.test_router_casual
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.llm_service import _es_charla_casual  # noqa: E402

# ── TRIVIAL: charla pura. Puede quedarse sin herramientas sin perder nada ────
TRIVIAL = [
    "hola", "hola!", "buenas", "buenos días", "buenas noches", "hey",
    "qué tal", "cómo estás", "qué onda", "saludos",
    "gracias", "muchas gracias", "mil gracias", "de nada",
    "genial", "perfecto", "excelente", "listo", "entendido", "vale",
    "ok", "okey", "sale", "dale", "claro", "obvio",
    "jaja", "jajaja", "jeje", "xd", "lol", "😂", "👍", "💪",
    "adiós", "hasta luego", "nos vemos", "chao", "me voy",
    # saludos con signos de interrogación: siguen siendo saludos
    "¿qué tal?", "¿cómo estás?", "¿qué onda?",
]

# ── GUARDABLE: datos del atleta. NUNCA pueden quedarse sin herramientas ──────
GUARDABLE = [
    # salud y biomecánica
    "tengo hernia discal", "me operaron del menisco", "tengo tendinitis",
    "me diagnosticaron condromalacia", "sufro de ciática",
    "vengo de una fractura", "estoy en rehabilitación", "voy al fisio",
    "tengo el manguito rotador tocado", "ya me curé", "me dieron el alta",
    # nutrición
    "soy vegetariano", "soy vegano", "tomo creatina", "tomo proteína",
    "ayuno hasta las 2", "estoy a dieta", "soy celiaco",
    "soy intolerante a la lactosa", "estoy en superávit", "cuento macros",
    "no tomo café", "empecé keto",
    # estilo de vida y entorno
    "trabajo sentado 10 horas", "hago turnos de noche", "tengo insomnio",
    "ando con mucha ansiedad", "tengo un bebé y duermo poco",
    "me mudé de ciudad", "viajo mucho por trabajo", "madrugo a las 5",
    "dejé de fumar", "juego fútbol los domingos",
    # metas y contexto
    "quiero competir en septiembre", "me caso el mes que viene",
    "voy a la playa en julio", "mi objetivo es powerlifting",
    # equipo y preferencias
    "no tengo rack en casa", "solo tengo mancuernas",
    "entreno en un parque", "mi gym no tiene jaula",
    "consegui bandas elásticas",
    # declaración personal sin vocabulario conocido
    "soy zurdo", "mido menos que la media", "no puedo arrodillarme",
    "tengo una placa en el brazo",
]

# ── ENTRENA: entrenamiento explícito. Ya funcionaba, no debe romperse ────────
ENTRENA = [
    "que me toca hoy", "cual es mi rutina", "100x5", "100 kg x 5",
    "rpe 8", "ya terminé", "me duele el hombro", "cambia el peso muerto",
    "cuál es mi 1rm", "mándame la gráfica", "cual es mi nivel",
    "pausa mi plan", "ya volví del viaje", "dormí fatal",
    "cuántas series por grupo muscular", "@8.5 la última",
    "subí a 0.45 m/s", "220 lbs", "hice 10 reps",
    # preguntas cortas de teoría: la respuesta está en la biblioteca, no en la
    # memoria del modelo. "desequilibrios??" salió del historial real.
    "desequilibrios??", "y eso por qué?", "sirve de algo?", "cuál es mejor?",
]


def _evaluar(nombre: str, casos: list[str], esperado_casual: bool,
             exigir_total: bool) -> tuple[int, int, list[str]]:
    aciertos, fallos, malos = 0, 0, []
    for frase in casos:
        got = _es_charla_casual(frase)
        if got == esperado_casual:
            aciertos += 1
        else:
            fallos += 1
            malos.append(frase)
    etiqueta = "casual (sin tools)" if esperado_casual else "NORMAL (con tools)"
    marca = "" if (not exigir_total or not fallos) else "   <-- OBLIGATORIO 100 %"
    print(f"\n  {nombre}: espera {etiqueta}")
    print(f"    {aciertos}/{len(casos)} correctos{marca}")
    for f in malos:
        signo = "se queda MUDO" if got is None else (
            "se queda MUDO" if esperado_casual is False else "gasta tools de más")
        print(f"      x {f!r}  ({signo})")
    return aciertos, fallos, malos


def main() -> int:
    print(f"\nCorpus: {len(TRIVIAL)} triviales, {len(GUARDABLE)} guardables, "
          f"{len(ENTRENA)} de entrenamiento")

    a1, f1, _ = _evaluar("TRIVIAL", TRIVIAL, True, exigir_total=False)
    a2, f2, m2 = _evaluar("GUARDABLE", GUARDABLE, False, exigir_total=True)
    a3, f3, m3 = _evaluar("ENTRENA", ENTRENA, False, exigir_total=True)

    total = len(TRIVIAL) + len(GUARDABLE) + len(ENTRENA)
    print(f"\n{'='*74}")
    print(f"  aciertos totales: {a1+a2+a3}/{total}")
    print(f"  TRIVIAL   {a1}/{len(TRIVIAL)}   (tolerante: pasarse solo cuesta segundos)")
    print(f"  GUARDABLE {a2}/{len(GUARDABLE)}   (estricto: fallar aquí PIERDE el dato)")
    print(f"  ENTRENA   {a3}/{len(ENTRENA)}   (estricto: no romper lo que ya iba)")

    critico = f2 + f3
    if critico:
        print(f"\n  {critico} fallo(s) CRÍTICO(s): el atleta habla y el sistema no puede guardarlo.")
        return 1
    if f1 > len(TRIVIAL) * 0.35:
        print(f"\n  {f1} triviales activan herramientas: demasiados, revisa el coste.")
        return 1
    print("\n  Sin fallos críticos.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
