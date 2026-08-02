# -*- coding: utf-8 -*-
"""
sembrar_conversacion.py — le da al coach un pasado con el atleta, en segundos.

Para qué existe
---------------
`sembrar_demo.py` deja meses de ENTRENAMIENTO en disco: series, marcas, peso
corporal, readiness. Pero el coach sigue conociendo a su atleta desde cero,
porque su memoria conversacional vive en otros dos sitios que aquel script no
toca:

    data/historial_<numero>.json    el hilo de WhatsApp — de lo que "se acuerdan"
                                    los dos. Sin él, el primer mensaje de la
                                    demostración es literalmente el primero:
                                    ningún "como te decía la semana pasada".

    memoria_historica_atletas       la bitácora de largo plazo (mesociclos ya
    (colección de ChromaDB)         cerrados, lesiones viejas). Es lo que lee
                                    consultar_historial_narrativo.

OJO — sobre la colección narrativa
----------------------------------
Ninguna parte del sistema ESCRIBE hoy en `memoria_historica_atletas`: en todo el
repositorio solo hay un `add_texts` (el del expediente) y un `from_documents`
(el de la biblioteca científica). La colección existe y se lee, pero nace y
muere vacía, así que `consultar_historial_narrativo` siempre contesta "No hay
registros narrativos previos". Este script es, de momento, el único productor de
esa colección; hasta que el sistema cierre mesociclos por su cuenta, la
herramienta solo tiene algo que decir si se siembra aquí.

Por qué 26 mensajes y no 60
---------------------------
`llm_service.UMBRAL_COMPRESION` vale 30: en cuanto el historial pasa de ahí, el
turno siguiente dispara el compresor asíncrono (~8 s). Sembrar 60 mensajes hace
que el PRIMER mensaje de la demostración pague esa compresión. Se siembra por
debajo del umbral a propósito.

Tampoco se siembra el mensaje `system`: el orquestador inserta el prompt vigente
si el historial no empieza por uno (llm_service ~5859). Sembrarlo congelaría una
versión vieja del prompt — que es justo lo que le pasa a los historiales
antiguos que hay en data/.

Uso
---
    python3 scripts/sembrar_conversacion.py                  # número de demo
    python3 scripts/sembrar_conversacion.py 5215500000123
    python3 scripts/sembrar_conversacion.py --solo-narrativa
    python3 scripts/sembrar_conversacion.py --limpiar        # borra antes
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

CARPETA_DATA = RAIZ / "data"
NUMERO_DEMO = "5217291395298"


# ==============================================================================
# EL HILO DE WHATSAPP
# ==============================================================================
# Cada entrada: (quién, texto, [herramientas que el coach usó en ese turno]).
# Los turnos del coach llevan sus tool_calls porque así es como se guardan en
# producción (`_tool_calls_planos`): un assistant que llamó herramientas y se
# recarga como texto pelado se renderiza distinto y rompe el prefijo de caché.
#
# El contenido es coherente a propósito con lo que siembra sembrar_demo.py:
# las mismas marcas, el mismo material de gimnasio y las mismas dos molestias
# (hombro derecho y lumbar) que están en el expediente vectorial. Si cambias uno
# cambia el otro, o el coach se contradirá en mitad de la demostración.
GUION: list[tuple[str, str, list[dict] | None]] = [
    ("user", "oye una cosa, el finde me fui de boda y no pude entrenar el sábado. lo recupero o lo dejo pasar?", None),
    ("assistant",
     "Déjalo pasar, no pasa nada. Una sesión suelta no te mueve la aguja; lo que la mueve es no romper el patrón dos "
     "semanas seguidas. Retomamos donde estabas y seguimos.", None),

    ("user", "vale. por cierto ya no me molesta nada el hombro en banca desde que bajé el agarre", None),
    ("assistant",
     "Buenísima señal, lo doy por cerrado entonces. Lo dejo apuntado para no volver a sacártelo cada vez que toque "
     "empuje. Si en algún momento vuelve a asomar, me lo dices y revisamos el agarre otra vez.",
     [{"function": {"name": "guardar_en_expediente_personal",
                    "arguments": {"dato": "El atleta ya no tiene molestia en el hombro derecho durante el press de "
                                          "banca desde que bajó el agarre; la molestia de mayo de 2026 está resuelta."}}}]),

    ("user", "cómo voy de banca? siento que llevo semanas atascado ahí", None),
    ("assistant",
     "No es una sensación tuya, los números dicen lo mismo: la banca lleva un mes plana mientras sentadilla y peso "
     "muerto siguen subiendo. No es falta de ganas, es que ese patrón te está pidiendo otra cosa. Lo miramos en el "
     "próximo bloque.",
     [{"function": {"name": "consultar_progreso",
                    "arguments": {"metrica": "1rm", "ejercicio": "Press Banco Plano"}}}]),

    ("user", "y eso por qué pasa? el resto sube sin problema", None),
    ("assistant",
     "Suele ser volumen mal repartido más que falta de esfuerzo: el pecho aguanta bastante menos series efectivas por "
     "semana que la pierna antes de dejar de responder. Estás en la zona alta de lo que tolera tu empuje, así que más "
     "series no te va a dar más; toca calidad, no cantidad.",
     [{"function": {"name": "consultar_libro_cientifico",
                    "arguments": {"tema": "volumen semanal por grupo muscular estancamiento hipertrofia"}}}]),

    ("user", "el lunes acabé reventado, dormí fatal toda la semana pasada", None),
    ("assistant",
     "Se nota en tus respuestas del check matutino: vienes de una semana con el sueño por los suelos. Eso no se "
     "compensa apretando más. Bajamos una marcha esta semana y volvemos a empujar cuando duermas bien dos noches "
     "seguidas.", None),

    ("user", "en el gym solo hay discos de 2.5 como mínimo, no puedo hacer saltos más finos", None),
    ("assistant",
     "Perfecto, con eso trabajo. Te redondeo siempre a múltiplos de 2.5 para que no tengas que inventar nada en la "
     "barra. Si algún día ves un salto raro, dímelo y lo ajusto.",
     [{"function": {"name": "guardar_en_expediente_personal",
                    "arguments": {"dato": "El incremento mínimo disponible en el gimnasio del atleta es de 2.5 kg; "
                                          "todas las cargas deben redondearse a múltiplos de 2.5."}}}]),

    ("user", "las zancadas las odio, en serio. hay algo que las sustituya?", None),
    ("assistant",
     "Anotado, fuera zancadas. Prensa y hack te dan el mismo trabajo de cuádriceps sin la parte que no soportas, y de "
     "hecho te dejan cargar más. No pierdes nada por el cambio.",
     [{"function": {"name": "guardar_en_expediente_personal",
                    "arguments": {"dato": "Al atleta no le gustan las zancadas: prefiere prensa o hack para el "
                                          "trabajo accesorio de cuádriceps."}}}]),

    ("user", "acabo de sacar 140x3 en sentadilla y me quedó una en el tanque", None),
    ("assistant",
     "Ahí está. Esa serie te deja la sentadilla en su mejor estimación hasta hoy, y encima con una en recámara: hay "
     "más ahí dentro. Queda apuntada.",
     [{"function": {"name": "registrar_serie_completada",
                    "arguments": {"nombre_ejercicio": "Sentadillas", "peso_kg": 140, "repeticiones": 3, "rpe": 9}}},
      {"function": {"name": "calcular_rm_exacto",
                    "arguments": {"nombre_ejercicio": "Sentadillas", "peso_kg": 140, "repeticiones": 3}}}]),

    ("user", "me pesé esta mañana, 84 kilos", None),
    ("assistant",
     "Apuntado. Vas subiendo despacio y la fuerza sube contigo, que es exactamente lo que buscamos en powerbuilding. "
     "Ritmo correcto, sigue así.",
     [{"function": {"name": "registrar_peso_corporal", "arguments": {"peso_kg": 84.0}}}]),

    ("user", "entreno mejor temprano, sobre las 6 o 7 de la mañana. el resto del día no rindo igual", None),
    ("assistant",
     "Tomo nota. Entrenar siempre a la misma hora te da lecturas más limpias: cuando comparo tus series entre semanas, "
     "no tengo que descontar si era de mañana o de noche.",
     [{"function": {"name": "guardar_en_expediente_personal",
                    "arguments": {"dato": "El atleta prefiere entrenar temprano, entre las 6 y las 8 de la mañana; "
                                          "rinde peor por la tarde."}}}]),

    ("user", "el peso muerto de la semana pasada me dejó el lumbar tocado, pero ya está bien", None),
    ("assistant",
     "Me alegro. Lo dejo como resuelto, no como una lesión abierta. Si vuelve a aparecer con la misma carga hablamos "
     "de técnica antes que de bajar kilos.",
     [{"function": {"name": "guardar_en_expediente_personal",
                    "arguments": {"dato": "El atleta reportó dolor lumbar leve tras peso muerto pesado en junio de "
                                          "2026; ya está resuelto y no requiere restricción."}}}]),

    ("user", "gracias por lo de ayer, me vino bien bajar el volumen", None),
    ("assistant",
     "Para eso estoy. Bajar a tiempo es parte del plan, no un premio de consolación: la semana que descargas es la que "
     "hace que la siguiente sirva de algo.", None),
]


# ==============================================================================
# LA BITÁCORA DE LARGO PLAZO
# ==============================================================================
# Lo que consultar_historial_narrativo debería encontrar cuando el atleta dice
# "la vez pasada" o "el mesociclo anterior". Se escribe en tercera persona y con
# fechas absolutas, igual que el expediente.
NARRATIVA = [
    "Mesociclo de acumulación cerrado en mayo de 2026: cinco días semanales, enfoque powerbuilding. El atleta subió "
    "la sentadilla de 145 a 152 kg estimados y el peso muerto de 180 a 188 kg. La banca se movió poco. Terminó el "
    "bloque con buena adherencia: no faltó a ninguna sesión.",

    "Durante mayo de 2026 apareció una molestia en el hombro derecho en el press de banca. Se resolvió bajando el "
    "agarre unos centímetros, sin necesidad de retirar el ejercicio ni de parar el bloque. No volvió a aparecer.",

    "Mesociclo de fuerza cerrado en junio de 2026: se bajaron las repeticiones y se subió la intensidad. El atleta "
    "respondió bien en tren inferior, pero el press de banca empezó a estancarse en este bloque y ya no recuperó "
    "la progresión.",

    "En junio de 2026 el atleta reportó dolor lumbar leve tras una sesión de peso muerto pesado. Se trabajó movilidad "
    "durante dos semanas y el dolor desapareció. No hubo que modificar el plan ni retirar el peso muerto.",

    "Semana de descarga de finales de junio de 2026: se redujo el volumen a la mitad manteniendo la intensidad. El "
    "atleta llegó a esa semana con varios RPE por encima de lo prescrito y salió de ella con las cargas recuperadas.",

    "El atleta lleva entrenando de forma continuada desde abril de 2026 con una adherencia alta: prácticamente no "
    "falla sesiones y responde el cuestionario matutino casi todos los días. Su punto débil histórico es el empuje "
    "horizontal; su punto fuerte, la cadena posterior.",
]


# ==============================================================================
# ESCRITURA
# ==============================================================================
def escribir_historial(numero: str, dias_atras: int = 21) -> int:
    """Vuelca el guion a data/historial_<numero>.json en el formato del sistema."""
    mensajes = []
    for quien, texto, tools in GUION:
        msg: dict = {"role": quien, "content": texto}
        if tools:
            msg["tool_calls"] = tools
        mensajes.append(msg)

    ruta = CARPETA_DATA / f"historial_{numero}.json"
    ruta.write_text(json.dumps(mensajes, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(mensajes)


def escribir_narrativa(numero: str) -> str:
    """Siembra la colección `memoria_historica_atletas` de ChromaDB.

    Se escribe con la misma forma de metadatos que usa el expediente
    (`numero_usuario` + `fecha`), porque `consultar_memoria_historica_atleta`
    filtra por `{"numero_usuario": numero}` y sin ese campo no recupera nada.
    """
    try:
        from services.llm_service import db_memoria_atleta
    except Exception as e:
        return f"ERROR importando el orquestador: {type(e).__name__}: {e}"

    if db_memoria_atleta is None:
        return "ERROR: ChromaDB no está disponible (¿falta data/chroma_db o Ollama?)"

    # Sin desduplicar, cada ejecución vuelve a insertar las mismas seis notas y
    # el RAG acaba devolviendo la misma frase tres veces.
    try:
        existentes = db_memoria_atleta.get(where={"numero_usuario": str(numero)})
        ids_previos = existentes.get("ids") or []
        if ids_previos:
            db_memoria_atleta.delete(ids=ids_previos)
    except Exception:
        pass

    hoy = datetime.now()
    try:
        db_memoria_atleta.add_texts(
            texts=NARRATIVA,
            metadatas=[{"numero_usuario": str(numero),
                        "fecha": (hoy - timedelta(days=30 + i * 10)).strftime("%Y-%m-%d")}
                       for i in range(len(NARRATIVA))],
        )
    except Exception as e:
        return f"ERROR escribiendo: {type(e).__name__}: {e}"
    return f"{len(NARRATIVA)} bitácoras"


def limpiar(numero: str) -> None:
    ruta = CARPETA_DATA / f"historial_{numero}.json"
    if ruta.exists():
        ruta.unlink()
        print(f"  · borrado {ruta.name}")
    try:
        from services.llm_service import db_memoria_atleta
        if db_memoria_atleta is not None:
            previos = db_memoria_atleta.get(where={"numero_usuario": str(numero)})
            if previos.get("ids"):
                db_memoria_atleta.delete(ids=previos["ids"])
                print(f"  · borradas {len(previos['ids'])} bitácoras narrativas")
    except Exception as e:
        print(f"  · no se pudo limpiar la narrativa: {e}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Siembra el pasado conversacional del atleta.")
    ap.add_argument("numero", nargs="?", default=NUMERO_DEMO)
    ap.add_argument("--solo-narrativa", action="store_true",
                    help="no toca el hilo de WhatsApp, solo la bitácora vectorial")
    ap.add_argument("--solo-hilo", action="store_true",
                    help="no toca ChromaDB, solo el historial de conversación")
    ap.add_argument("--limpiar", action="store_true", help="borra antes de sembrar")
    args = ap.parse_args()

    numero = args.numero.strip()
    print(f"\n{'=' * 70}")
    print(f"  SEMBRANDO EL PASADO CONVERSACIONAL — {numero}")
    print(f"{'=' * 70}\n")

    if args.limpiar:
        limpiar(numero)

    if not args.solo_narrativa:
        n = escribir_historial(numero)
        umbral = 30
        aviso = "" if n < umbral else f"  ¡OJO! supera UMBRAL_COMPRESION={umbral}"
        print(f"  · hilo de WhatsApp: {n} mensajes{aviso}")

    if not args.solo_hilo:
        print("  · bitácora narrativa: ", end="", flush=True)
        print(escribir_narrativa(numero))

    print(f"\n{'=' * 70}")
    print("  El coach ya tiene pasado con este atleta.")
    print(f"{'=' * 70}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
