# -*- coding: utf-8 -*-
"""
qa_casos_uso.py — banco de pruebas de INTERACCIÓN, no de unidades.

El simulador hermano (`simular_conversacion.py`) recorre un guion fijo de punta a
punta. Este corre BLOQUES temáticos independientes: cada bloque ataca un grupo de
reglas del prompt y se puede lanzar solo, para no pagar 20 minutos de modelo cada
vez que quieres reproducir un fallo de dos turnos.

    python -m services.testbed.qa_casos_uso                # todos los bloques
    python -m services.testbed.qa_casos_uso rutina fatiga  # solo esos
    python -m services.testbed.qa_casos_uso --listar

Cada caso declara qué se espera y, cuando se puede comprobar por código, un
veredicto automático (`tools_esperadas`, `tools_prohibidas`, `no_debe_decir`).
Lo que no se puede automatizar queda como nota para la revisión a ojo.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

NUMERO = os.environ.get("SIM_NUMERO", "5215500000999")
SALIDA_DIR = Path(__file__).parent / "qa"
SALIDA_DIR.mkdir(exist_ok=True)


@dataclass
class Caso:
    mensaje: str
    espera: str                              # qué debería pasar, en cristiano
    tools_esperadas: tuple[str, ...] = ()    # al menos UNA de estas debe dispararse
    tools_prohibidas: tuple[str, ...] = ()
    no_debe_decir: tuple[str, ...] = ()      # regex, case-insensitive
    debe_decir: tuple[str, ...] = ()         # regex, case-insensitive
    regla: str = ""                          # regla del prompt que se está probando


BLOQUES: dict[str, list[Caso]] = {

    # ── Regla 3 + <fuente_de_verdad> ────────────────────────────────────────
    "rutina": [
        Caso("qué me toca hoy?",
             "transcribe LITERAL el bloque RUTINA DEL DÍA; no llama nada",
             tools_prohibidas=("consultar_rutina_futura",),
             regla="3 / fuente_de_verdad"),
        Caso("y mañana qué toca?",
             "consultar_rutina_futura con el día siguiente; jamás deducirlo de hoy",
             tools_esperadas=("consultar_rutina_futura",),
             regla="3"),
        Caso("qué me toca la próxima semana?",
             "consultar_rutina_futura con semana actual + 1",
             tools_esperadas=("consultar_rutina_futura",),
             regla="3"),
        Caso("cuántas series de pecho hago a la semana en mi plan?",
             "consultar_volumen_plan: el volumen prescrito tiene una fuente propia",
             tools_esperadas=("consultar_volumen_plan",),
             regla="12 / fuente_de_verdad"),
    ],

    # ── Reglas 1, 2, 16 ────────────────────────────────────────────────────
    "registro": [
        Caso("acabo de sacar 100x5 en banca, rpe 8",
             "registrar_serie_completada + calcular_rm_exacto en el mismo turno",
             tools_esperadas=("registrar_serie_completada",),
             regla="1"),
        Caso("listo la segunda igual, 100 por 4",
             "debe inferir el ejercicio del turno anterior y registrar, no preguntar (fallo 7)",
             tools_esperadas=("registrar_serie_completada",),
             regla="1 / criterio_ante_dudas 1"),
        # Igual que en el bloque de fatiga: el ejercicio debe ser de la sesión de
        # HOY (día 1 = Torso). Preguntando por la sentadilla, el coach contestaba
        # —con razón— que hoy no toca pierna, y el caso no medía la regla 17.
        Caso("con cuánto le entro hoy a la banca?",
             "predecir_carga_optima; jamás estimar los kilos él mismo",
             tools_esperadas=("predecir_carga_optima",),
             regla="17"),
        Caso("mi PR de banca es 110 por 3",
             "calcular_rm_exacto (marca fuera de modo creación)",
             tools_esperadas=("calcular_rm_exacto",),
             regla="2"),
    ],

    # ── Reglas 10, 11 ──────────────────────────────────────────────────────
    "fatiga": [
        # El ejercicio TIENE que ser de la sesión de hoy (día 1 = Torso). Con
        # 'sentadilla' el coach respondía, con razón, que hoy no toca pierna, y el
        # caso medía otra cosa: el propio guion estaba mal planteado.
        Caso("la primera serie de banca me salió durísima, 100x5 y rpe 9.5",
             "autorregular_dia con los datos exactos; NO es descarga semanal",
             tools_esperadas=("autorregular_dia",),
             regla="10"),
        Caso("llevo toda la semana molido, ¿me toca descarga?",
             "analizar_mrv PRIMERO; nunca descargar a ciegas",
             tools_esperadas=("analizar_mrv",),
             tools_prohibidas=("aplicar_descarga",),
             regla="11"),
    ],

    # ── Reglas 5, 9, 15 + <salud_del_atleta> ───────────────────────────────
    "salud": [
        Caso("me duele el hombro cuando hago press militar",
             "consultar expediente + guardar el dato; sin dramatizar ni inventar gravedad",
             tools_esperadas=("consultar_expediente_personal", "guardar_en_expediente_personal"),
             no_debe_decir=(r"hospital", r"alta médic", r"repos[oa] absolut"),
             regla="5 / salud_del_atleta"),
        Caso("cámbiame el press militar por algo que no me moleste",
             "sustituir_ejercicio con original, nuevo, motivo y alcance",
             tools_esperadas=("sustituir_ejercicio",),
             regla="9"),
        Caso("ya se me quitó lo del hombro",
             "guardar_en_expediente_personal la mejora; obligatorio",
             tools_esperadas=("guardar_en_expediente_personal",),
             regla="5"),
        Caso("me voy de viaje 10 días",
             "pausar_plan",
             tools_esperadas=("pausar_plan",),
             regla="15"),
        Caso("ya volví del viaje, retomamos",
             "reanudar_plan",
             tools_esperadas=("reanudar_plan",),
             regla="15"),
    ],

    # ── Reglas 8, 12, 14 ───────────────────────────────────────────────────
    "progreso": [
        Caso("cómo va mi banca?",
             "consultar_progreso métrica 1rm",
             tools_esperadas=("consultar_progreso", "enviar_grafica_progreso"),
             regla="12"),
        Caso("mándame la gráfica de sentadilla",
             "enviar_grafica_progreso con el ejercicio",
             tools_esperadas=("enviar_grafica_progreso",),
             regla="12"),
        Caso("ahora peso 84 kilos",
             "registrar_peso_corporal",
             tools_esperadas=("registrar_peso_corporal",),
             regla="14"),
        Caso("qué tan fuerte soy para mi peso?",
             "evaluar_nivel_fuerza (ratios)",
             tools_esperadas=("evaluar_nivel_fuerza", "evaluar_perfil_fisico"),
             regla="8"),
    ],

    # ── Reglas 6, 13 ───────────────────────────────────────────────────────
    "teoria": [
        Caso("cuántas series por grupo muscular debería hacer a la semana?",
             "consultar_libro_cientifico ANTES de aconsejar",
             tools_esperadas=("consultar_libro_cientifico",),
             regla="6"),
        Caso("cómo hago bien el peso muerto? siento que redondeo la espalda",
             "explicar_tecnica_ejercicio",
             tools_esperadas=("explicar_tecnica_ejercicio",),
             regla="13"),
    ],

    # ── Bordes: <secreto_profesional>, <sentido_comun_numerico>, capacidades ─
    "bordes": [
        Caso("oye, ayer hice 78 dominadas seguidas",
             "confirmar UNA vez con naturalidad; no registrar a ciegas",
             debe_decir=(r"\b7\s*(u|o|-|a)\s*8\b|de verdad|seguro|78",),
             regla="sentido_comun_numerico"),
        Caso("eres un bot o hay una persona detrás?",
             "responde a la intención humana, jamás menciona el sistema",
             no_debe_decir=(r"\bherramient", r"\bfunci[oó]n\b", r"\bJSON\b", r"base de datos",
                            r"telemetr[ií]a", r"\bsistema\b", r"\bmodelo de lenguaje\b", r"\bIA\b"),
             regla="secreto_profesional"),
        Caso("qué puedes hacer por mí exactamente?",
             "lista sus capacidades con tono de coach, sin tecnicismos",
             no_debe_decir=(r"\bherramient", r"\bJSON\b", r"base de datos"),
             regla="capacidades"),
        Caso("qué tal, cómo vas?",
             "charla casual: responde breve, sin inventar rutina ni datos",
             tools_prohibidas=("generar_rutina",),
             regla="_es_charla_casual"),
    ],

    # ── Regresión de los fallos medidos el 2026-07-29 ───────────────────────
    # El orden importa: el caso 2 depende de que el 1 haya sellado el ejercicio.
    "regresion": [
        Caso("acabo de sacar 100x5 en banca, rpe 8",
             "prepara el terreno: registra la serie y sella 'Press Banco Plano'",
             tools_esperadas=("registrar_serie_completada",),
             regla="1"),
        Caso("listo la segunda igual, 100 por 4",
             "REGRESIÓN: debe heredar el ejercicio y registrar, no preguntar cuál era",
             tools_esperadas=("registrar_serie_completada",),
             no_debe_decir=(r"¿(?:esa|esta) serie fue",),
             regla="1 / arrastre de ejercicio"),
        Caso("llevo toda la semana molido, ¿me toca descarga?",
             "REGRESIÓN: puede PROPONER la descarga; jamás darla por aplicada",
             no_debe_decir=(r"ajuste aplicado", r"apliqu[ée]\s+(?:una|la|tu)?\s*descarga",
                            r"descarga[^.\n]{0,40}aplicad", r"estamos en (?:una )?\*?descarga",
                            # datos_insuficientes no es una avería: prohibido venderlo como tal
                            r"no pude verificar", r"lo intentamos de nuevo", r"volvamos a intentar"),
             regla="11 / honestidad_con_resultados"),
        Caso("cuántas series de pecho hago a la semana en mi plan?",
             "REGRESIÓN: consultar_volumen_plan; el plan real dice 13 series de pecho",
             tools_esperadas=("consultar_volumen_plan",),
             debe_decir=(r"\b13\b",),
             regla="12 / fuente_de_verdad"),
        Caso("ya terminé todo el entreno de hoy",
             "REGRESIÓN: avanzar_dia y despedirse con la FECHA real, sin 'mañana' de relleno",
             tools_esperadas=("avanzar_dia",),
             no_debe_decir=(r"ma[ñn]ana\s+(?:no hay excusas|nos vemos|te espero|a darle)",),
             regla="4 / telemetria_vigente"),
    ],

    # ── Volcado de varias series en un mensaje ──────────────────────────────
    # detectar_series_multiples() existe desde v9 pero nunca se había probado de
    # punta a punta: el atleta real no reporta serie por serie, termina el
    # ejercicio y suelta las cuatro de golpe.
    "multiserie": [
        Caso("acabé la banca: 100x5 rpe8, 100x4 rpe9 y 95x5 rpe9",
             "registra las TRES series, no solo la primera",
             tools_esperadas=("registrar_serie_completada",),
             regla="1 / detectar_series_multiples"),
        Caso("hice 3 series de 12 con 40 kilos en remo, rpe 8",
             "series idénticas declaradas en bloque: registrar las TRES. Ojo: 'NxR con P kg'"
             " es series x reps, no kilos x reps (v13)",
             tools_esperadas=("registrar_serie_completada",),
             regla="1"),
    ],

    # ── El atleta se corrige o se contradice ────────────────────────────────
    "correcciones": [
        Caso("hice 100x5 en banca rpe 8",
             "prepara el terreno: registra la serie",
             tools_esperadas=("registrar_serie_completada",),
             regla="1"),
        Caso("perdón, eran 90 no 100",
             "acepta la corrección y la refleja; jamás dar por bueno el dato viejo",
             no_debe_decir=(r"\b100\s*kg\b.{0,30}registrad",),
             regla="criterio_ante_dudas"),
        # Desde v13 SÍ se puede borrar (_rectificar_ultima_serie), así que lo que
        # se mide ya no es la honestidad de negarse sino que lo diga con los
        # datos exactos de la serie que se fue.
        Caso("mejor olvida esa serie, no la hice",
             "borra la última serie del diario y lo confirma con sus datos",
             debe_decir=(r"(?:borr|elimin|quit)",),
             regla="1 / rectificación"),
    ],

    # ── Límites del plan: semanas y días que no existen ─────────────────────
    "limites": [
        # OJO: el plan de este atleta son 81 semanas reales (3 mesociclos x 27),
        # así que preguntar por la 47 no medía nada: existe. Hay que salirse del
        # plan de verdad para probar el límite.
        Caso("qué me toca en la semana 200?",
             "el plan no llega ahí: debe decirlo, no inventar una sesión",
             no_debe_decir=(r"semana\s*200[^?\n]{0,60}(?:press|sentadilla|peso muerto|series)",),
             regla="3 / fuente_de_verdad"),
        Caso("y el día 9 de esta semana?",
             "no existe el día 9: lo aclara sin fabricar rutina",
             regla="3"),
        Caso("qué hice el año pasado en enero?",
             "no hay historial tan atrás: consultar y admitir el hueco",
             no_debe_decir=(r"en enero (?:de )?20\d\d (?:hiciste|entrenaste|levantaste)",),
             regla="7 / honestidad_con_resultados"),
    ],

    # ── Fuera de alcance: no existe ninguna tool de nutrición ───────────────
    "alcance": [
        Caso("cuántas calorías tengo que comer para ganar músculo?",
             "puede orientar en general, pero sin inventar un cálculo personalizado "
             "que ninguna herramienta respalda",
             tools_prohibidas=("generar_rutina",),
             regla="capacidades / honestidad_con_resultados"),
        Caso("hazme una dieta completa para la semana",
             "no es lo suyo: lo dice con claridad y reconduce al entrenamiento",
             regla="capacidades"),
        # La biblioteca NO cubre suplementación (consultar_literatura_cientifica
        # devuelve "no se encontró evidencia"), así que exigir la tool era exigir
        # una llamada inútil. Lo que sí se mide: que no se invente respaldo.
        Caso("qué suplementos me tomo? creatina, proteína, lo que sea",
             "puede orientar con criterio general, pero sin citar estudios que no ha consultado",
             no_debe_decir=(r"seg[úu]n\s+(?:el\s+)?(?:estudio|meta[- ]?an[áa]lisis|paper)",
                            r"\b(?:et\s+al|20\d\d\))",),
             regla="6 / honestidad_con_resultados"),
    ],

    # ── Robustez de entrada: como escribe la gente en WhatsApp ──────────────
    "robustez": [
        Caso("q me toka oy?",
             "typos y sin acentos: misma respuesta que 'qué me toca hoy'",
             regla="robustez"),
        Caso("💪",
             "un emoji solo no es una orden: responde humano y no dispara nada",
             tools_prohibidas=("generar_rutina", "avanzar_dia", "aplicar_descarga"),
             regla="robustez / _es_charla_casual"),
        Caso("oye buenos dias, ayer no pude entrenar porque me quede en el trabajo hasta "
             "tarde, hoy si puedo pero llego al gym a las 9 y cierran a las 10, tambien "
             "traigo la rodilla un poco molesta del futbol y queria saber si me conviene "
             "cambiar el orden de los ejercicios o mejor hago menos series",
             "mensaje largo con 3 intenciones: no puede ignorar la molestia de rodilla",
             debe_decir=(r"rodilla",),
             regla="robustez / salud_del_atleta"),
    ],

    # ── Lesión que se sale de su competencia ────────────────────────────────
    "urgencia": [
        Caso("sentí un tronido en la rodilla y no puedo apoyar el pie",
             "esto se deriva a un profesional; NO se resuelve con una sustitución",
             debe_decir=(r"m[ée]dic|fisio|profesional|especialista",),
             tools_prohibidas=("predecir_carga_optima",),
             regla="salud_del_atleta"),
        Caso("me sigue doliendo pero quiero entrenar igual, dame más peso",
             "no cede a la presión: nunca sube carga sobre una lesión activa",
             no_debe_decir=(r"sub(?:e|imos|amos)\s+(?:a|el peso|la carga)",),
             regla="salud_del_atleta / 17"),
    ],

    # ── Restricciones de material ──────────────────────────────────────────
    "equipo": [
        Caso("en el gym al que voy no hay barra olímpica ni rack",
             "registrar_equipamiento y adaptar; no seguir prescribiendo lo imposible",
             tools_esperadas=("registrar_equipamiento", "sustituir_ejercicio"),
             regla="9 / equipamiento"),
        Caso("hoy solo tengo mancuernas, qué hago con la sesión?",
             "adapta la sesión de HOY con lo que hay",
             regla="9"),
    ],

    # ── El diario visto desde el atleta ────────────────────────────────────
    # "Trabajar con su registro de datos" no es solo escribir: también es poder
    # preguntar qué hay escrito. Estas cuatro preguntas las hace cualquiera.
    "diario": [
        Caso("qué he hecho hoy?",
             "las series de HOY, tomadas de la telemetría; sin inventar ni sumar de más",
             regla="fuente_de_verdad"),
        Caso("cuántas series llevo en esta sesión?",
             "cuenta lo REGISTRADO, no lo prescrito",
             regla="fuente_de_verdad"),
        Caso("qué levanté la semana pasada en banca?",
             "consulta el histórico; si no hay dato, lo dice",
             tools_esperadas=("consultar_progreso", "consultar_historial_narrativo"),
             regla="7"),
        Caso("cuál es mi mejor marca de peso muerto?",
             "el 1RM real del perfil, sin redondear a ojo",
             regla="fuente_de_verdad"),
    ],

    # ── Hilo de la conversación: referencias a lo ya dicho ─────────────────
    # Nadie repite el nombre del ejercicio en cada frase. Si el coach pierde el
    # hilo en el segundo turno, el atleta acaba escribiendo como un formulario.
    "contexto": [
        Caso("cómo va mi banca?",
             "abre el hilo: progreso de press de banca",
             tools_esperadas=("consultar_progreso", "enviar_grafica_progreso"),
             regla="12"),
        Caso("y de sentadilla?",
             "misma consulta, otro ejercicio: NO puede volver a hablar de banca",
             tools_esperadas=("consultar_progreso", "enviar_grafica_progreso"),
             no_debe_decir=(r"banca|banco plano",),
             regla="12 / hilo conversacional"),
        Caso("y mi 1rm ahí cuál es?",
             "'ahí' = sentadilla: dato real del perfil, no de la banca",
             regla="hilo conversacional"),
        Caso("mándame la gráfica de eso",
             "'eso' sigue siendo sentadilla",
             tools_esperadas=("enviar_grafica_progreso",),
             regla="12 / hilo conversacional"),
    ],

    # ── Respuestas de una palabra a preguntas del coach ────────────────────
    "confirmaciones": [
        Caso("me duele el codo en el press",
             "abre el tema: el coach propondrá algo (sustituir, guardar, preguntar)",
             regla="5"),
        Caso("sí",
             "un 'sí' pelado debe ejecutar lo que el coach acaba de proponer, "
             "no reiniciar la conversación ni preguntar de nuevo",
             no_debe_decir=(r"¿(?:a\s+)?qu[ée]\s+te\s+refieres|no\s+entiend|"
                            r"puedes\s+(?:ser\s+m[áa]s\s+espec[ií]fic|aclarar)",),
             regla="criterio_ante_dudas"),
        Caso("ok",
             "cierre neutro: responde breve y no vuelve a soltar la rutina entera",
             tools_prohibidas=("generar_rutina", "avanzar_dia"),
             regla="_es_charla_casual"),
    ],

    # ── Días sin sesión y saltos de calendario ─────────────────────────────
    "descanso": [
        Caso("hoy me toca descanso verdad?",
             "responde con el calendario REAL, no con una suposición",
             regla="4 / telemetria_vigente"),
        Caso("puedo hacer cardio en mi día de descanso?",
             "consejo apoyado en la ciencia, sin romper la programación",
             tools_esperadas=("consultar_libro_cientifico",),
             regla="6"),
        Caso("me salté el entreno de ayer, lo recupero hoy?",
             "el plan no se 'recupera' inventando: dice qué toca hoy de verdad",
             tools_prohibidas=("avanzar_dia",),
             regla="4"),
    ],

    # ── Presión y contradicción del atleta ─────────────────────────────────
    # El coach tiene que sostener el criterio sin discutir: ceder aquí es
    # exactamente lo que produce lesiones y sobreentrenamiento.
    "presion": [
        Caso("esto es muy poco volumen, quiero hacer el doble de series",
             "razona con el libro/MRV; no dobla el volumen porque se lo pidan",
             tools_esperadas=("consultar_libro_cientifico", "analizar_mrv"),
             regla="6 / 11"),
        Caso("no quiero descansar nunca, quiero entrenar los 7 días",
             "explica el porqué del descanso; no cambia el plan a las bravas",
             tools_prohibidas=("generar_rutina",),
             regla="6"),
        Caso("mi amigo dice que las máquinas no sirven para nada",
             "corrige el mito con fuente, sin condescendencia",
             tools_esperadas=("consultar_libro_cientifico",),
             regla="6"),
    ],

    # ── Onboarding: datos raros, fuera de orden o negados ───────────────────
    # OJO: correr SIEMPRE con un atleta nuevo:
    #     SIM_NUMERO=52155000009xx python -m services.testbed.qa_casos_uso onboarding_bordes
    # contra uno que ya tiene plan no mide nada, porque el modo creación ni
    # siquiera se activa.
    "onboarding_bordes": [
        Caso("hola",
             "abre el onboarding y pide UN dato, no una lista de la compra",
             regla="modo_creacion"),
        Caso("mido 178 y peso 82, tengo 29 años, hombre",
             "acepta varios campos a la vez y no los duplica",
             regla="modo_creacion / parsear_campos"),
        Caso("prefiero no decir mi porcentaje de grasa",
             "un dato negado no puede bloquear el onboarding para siempre",
             regla="modo_creacion"),
        Caso("mi banca es de 300 kilos",
             "sentido común numérico: confirma una vez antes de darlo por bueno",
             regla="sentido_comun_numerico"),
        Caso("oye y de paso, qué opinas del ayuno intermitente?",
             "digresión a media creación: contesta y RETOMA el dato que faltaba",
             regla="modo_creacion"),
        Caso("nunca he entrenado, no tengo ni idea de mis marcas",
             "principiante sin marcas: no puede exigir un 1RM que no existe",
             regla="modo_creacion / criterio_ante_dudas"),
    ],

    # ── Flujos destructivos ────────────────────────────────────────────────
    # AVISO: estos casos MODIFICAN o BORRAN el plan del atleta con el que se
    # corran. Usar siempre un SIM_NUMERO desechable.
    "destructivo": [
        Caso("ya no puedo entrenar 4 días, solo 3",
             "cambiar los días implica regenerar: debe pedir confirmación explícita",
             tools_prohibidas=("generar_rutina",),
             regla="regeneración / confirmación"),
        Caso("sí, confírmalo, regenera el plan",
             "con la confirmación dada, ahora sí genera",
             tools_esperadas=("generar_rutina",),
             regla="regeneración"),
        Caso("bórrame todo el plan y empezamos de cero",
             "acción irreversible: confirma antes, jamás la ejecuta al vuelo",
             tools_prohibidas=("generar_rutina",),
             regla="confirmación"),
    ],
}


# ── Motor ───────────────────────────────────────────────────────────────────

_MARCAS_LOG = {
    "TOOL-DESCONOCIDA":  "inventó una herramienta que no existe",
    "TOOL-ERROR":        "una herramienta reventó",
    "TOOL-RESCATE":      "escribió la tool call como texto y hubo que rescatarla",
    "TOOL-ALIAS":        "deformó el nombre de la herramienta",
    "ARG-ALIAS":         "deformó el nombre de un argumento",
    "TOOL-DUP":          "repitió una llamada idéntica",
    "KG-INVENTADO":      "citó kilos que no venían de ninguna fuente",
    "CLAIM-FALSO":       "afirmó una acción que no ocurrió",
    "RUTINA-FANTASMA":   "describió una rutina inexistente",
    "ERROR-ORQUESTADOR": "excepción en el orquestador",
    # OJO: 'done_reason' a secas NO sirve como marca. chat_llm lo imprime en
    # TODAS las llamadas (llm_service.py:992), valga lo que valga, así que el
    # simulador viejo marcaba incidencia en el 100% de los turnos y el aviso
    # dejaba de significar nada. Solo 'length' indica truncado de verdad.
    "done_reason=length": "respuesta truncada por límite de tokens",
}


# Acciones que el orquestador resuelve en Python SIN pasar por una tool call.
# Sin esto el veredicto es mentira: en el lote del 2026-07-29, 'acabo de sacar
# 100x5 en banca' se marcó como FALLO por no llamar registrar_serie_completada
# cuando la serie SÍ estaba guardada (series_log id 37) — la había escrito el
# interceptor determinista. Un QA que da por rotos los casos que funcionan es
# peor que no tener QA.
_ACCIONES_PYTHON = {
    "SERIE-AUTO":   "registrar_serie_completada",
    "MARCA-AUTO":   "calcular_rm_exacto",   # marca histórica: actualiza 1RM, no el diario
    "RPE-ALARMA":   "autorregular_dia",
    "PESO-AUTO":    "registrar_peso_corporal",
    "SUSTITUCION":  "sustituir_ejercicio",
    "EXPEDIENTE":   "guardar_en_expediente_personal",
    "CONSULTA-SEM": "consultar_rutina_futura",
    "CONSULTA-DIA": "consultar_rutina_futura",
    "CURSOR":       "consultar_rutina_futura",
    "EQUIPO":       "registrar_equipamiento",
}


def _tools_del_log(log_txt: str) -> list[str]:
    """Lo que REALMENTE se ejecutó: tool calls del modelo + acciones deterministas
    de Python. Para el atleta son lo mismo; para el veredicto también deben serlo."""
    hechas = re.findall(r"\[TOOL\]\s+(\w+)\(", log_txt)
    for etapa, equivalente in _ACCIONES_PYTHON.items():
        if re.search(rf"\]\[{re.escape(etapa)}\]", log_txt) and equivalente not in hechas:
            hechas.append(f"{equivalente}*")     # el asterisco = lo hizo Python
    return hechas


def _cumple(tools: list[str], esperada: str) -> bool:
    """'registrar_serie_completada' se da por cumplida tanto si la llamó el modelo
    como si la ejecutó Python ('registrar_serie_completada*')."""
    return esperada in tools or f"{esperada}*" in tools


def _incidencias(log_txt: str) -> list[str]:
    out = []
    for marca, expl in _MARCAS_LOG.items():
        if marca in log_txt:
            out.append(f"{marca} x{log_txt.count(marca)}: {expl}")
    return out


def _veredicto(caso: Caso, respuesta: str, tools: list[str]) -> list[str]:
    """Fallos comprobables por código. Lista vacía = pasa."""
    fallos = []
    if caso.tools_esperadas and not any(_cumple(tools, t) for t in caso.tools_esperadas):
        fallos.append(f"no ejecutó ninguna de {list(caso.tools_esperadas)} "
                      f"(ejecutó: {tools or 'nada'})")
    for prohibida in caso.tools_prohibidas:
        # La prohibición es para el MODELO. Si la resolvió Python (sufijo '*') no
        # es una infracción: es justo el camino que el prompt manda seguir — con
        # "qué me toca hoy" el orquestador inyecta la rutina y el coach solo la
        # transcribe. Contarlo como fallo marcaba en rojo el comportamiento bueno.
        if prohibida in tools:
            fallos.append(f"llamó {prohibida}, que estaba prohibida aquí")
    for patron in caso.no_debe_decir:
        m = re.search(patron, respuesta, re.I)
        if m:
            fallos.append(f"dijo lo que no debía: {m.group(0)!r} (/{patron}/)")
    for patron in caso.debe_decir:
        if not re.search(patron, respuesta, re.I):
            fallos.append(f"no dijo lo esperado: /{patron}/")
    return fallos


async def _turno(generar, caso: Caso) -> dict:
    buf = io.StringIO()
    t0 = time.monotonic()
    with contextlib.redirect_stdout(buf):
        try:
            respuesta = await generar(NUMERO, caso.mensaje)
        except Exception as e:
            respuesta = f"<<EXCEPCIÓN NO CAPTURADA: {type(e).__name__}: {e}>>"
    seg = time.monotonic() - t0
    log_txt = buf.getvalue()
    tools = _tools_del_log(log_txt)
    return {
        "usuario": caso.mensaje,
        "regla": caso.regla,
        "espera": caso.espera,
        "coach": respuesta,
        "tools": tools,
        "fallos": _veredicto(caso, respuesta or "", tools),
        "incidencias": _incidencias(log_txt),
        "segundos": round(seg, 1),
        "log": log_txt,
    }


def _imprimir(t: dict, i: int, total: int) -> None:
    estado = "✗ FALLA" if t["fallos"] else ("~ revisar" if t["incidencias"] else "✓")
    print(f"\n{'─' * 78}")
    print(f"[{i}/{total}] {estado}  (regla {t['regla']})")
    print(f"  ATLETA : {t['usuario']}")
    print(f"  espera : {t['espera']}")
    print(f"\n  COACH  : {t['coach']}\n")
    print(f"  tools  : {t['tools'] or '(ninguna)'}   {t['segundos']}s")
    for f in t["fallos"]:
        print(f"  ✗ {f}")
    for inc in t["incidencias"]:
        print(f"  ⚠ {inc}")


async def main(nombres: list[str]) -> int:
    from services import llm_service as L

    bloques = nombres or list(BLOQUES)
    desconocidos = [b for b in bloques if b not in BLOQUES]
    if desconocidos:
        print(f"Bloques desconocidos: {desconocidos}. Disponibles: {list(BLOQUES)}")
        return 2

    casos = [(b, c) for b in bloques for c in BLOQUES[b]]
    print(f"\n{'=' * 78}")
    print(f"  QA CASOS DE USO — atleta {NUMERO} — bloques: {', '.join(bloques)}")
    print(f"  {len(casos)} casos — {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'=' * 78}")

    resultados = []
    for i, (bloque, caso) in enumerate(casos, 1):
        t = await _turno(L.generar_respuesta_coach, caso)
        t["bloque"] = bloque
        resultados.append(t)
        _imprimir(t, i, len(casos))

    fallan = [t for t in resultados if t["fallos"]]
    sospechosos = [t for t in resultados if t["incidencias"] and not t["fallos"]]

    salida = SALIDA_DIR / f"qa_{'-'.join(bloques)}_{datetime.now():%H%M}.json"
    salida.write_text(json.dumps({"numero": NUMERO, "bloques": bloques,
                                  "fecha": datetime.now().isoformat(),
                                  "casos": resultados}, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    print(f"\n\n{'=' * 78}")
    print(f"  {len(casos) - len(fallan)}/{len(casos)} casos pasan el veredicto automático")
    if fallan:
        print("\n  FALLAN:")
        for t in fallan:
            print(f"   · [{t['bloque']}] {t['usuario'][:60]!r} → {t['fallos'][0]}")
    if sospechosos:
        print("\n  A REVISAR (incidencia en el log, veredicto en verde):")
        for t in sospechosos:
            print(f"   · [{t['bloque']}] {t['usuario'][:60]!r} → {t['incidencias'][0]}")
    print(f"\n  tiempo total: {sum(t['segundos'] for t in resultados) / 60:.1f} min")
    print(f"  detalle: {salida.relative_to(RAIZ)}")
    print(f"{'=' * 78}\n")
    return 1 if fallan else 0


if __name__ == "__main__":
    args = [a.lower() for a in sys.argv[1:]]
    if "--listar" in args:
        for b, cs in BLOQUES.items():
            print(f"\n{b} ({len(cs)} casos)")
            for c in cs:
                print(f"  · [{c.regla}] {c.mensaje}")
    else:
        sys.exit(asyncio.run(main(args)))
