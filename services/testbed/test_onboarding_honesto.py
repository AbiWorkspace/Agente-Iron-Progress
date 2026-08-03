# -*- coding: utf-8 -*-
"""
test_onboarding_honesto.py — preguntar no es responder, y corregir sí cambia la ficha.

Tres fallos del onboarding hallados en conversación real, todos de la misma
familia: el sistema da por bueno un dato que el atleta nunca dio.

  #19 FALSOS POSITIVOS DEL PARSER
      "tengo una duda: ¿en qué afecta que sea hombre o mujer?"
          -> [ONB-PARSE] campo='genero' exito=True   (guardó "hombre")
      "No sé ni qué es Powerbuilding ni qué es Powerlifting"
          -> guardó meta=powerbuilding
      En el primero los DOS regex matchean y ganaba el que se evaluaba antes. La
      meta, además, decide la selección de ejercicios y el volumen del mesociclo
      entero.

  #20 EL ONBOARDING LINEAL NO CORRIGE HACIA ATRÁS
      Con campo_pendiente='porcentaje_grasa' el atleta dijo "pero es que soy
      mujer", el coach respondió "entendido, cambio a mujer en la ficha", y el
      plan se generó con 'genero': 'hombre'. El coach confirmando un cambio que
      nadie hizo.

  #21 DOMINADAS ASISTIDAS
      "hago 6 dominadas con liga" -> [RM-GUARDADO] dominadas: 6.0kg x 6 -> 1RM 7.15
      El 6 eran repeticiones, no kilos. Y un 1RM absoluto de 7 kg no existe para
      un hombre de 84 kg: si necesita asistencia, su dominada máxima está POR
      DEBAJO de su peso corporal. La liga es lastre negativo y la calculadora no
      tiene ese tipo (solo lastrado y calistenia pura).

    python -m services.testbed.test_onboarding_honesto
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import llm_service as L  # noqa: E402

NUM = "99900000200"

ok = fallos = 0
_perfil: dict = {}
_rms: dict = {}


def chk(desc: str, cond: bool, detalle: str = "") -> None:
    global ok, fallos
    ok += bool(cond)
    fallos += not cond
    print(f"  {'✓' if cond else '✗ FALLA'} {desc}" + (f"  ({detalle})" if detalle else ""))


def onb(campo: str, msg: str) -> tuple[bool, dict]:
    """Corre el parser y devuelve (exito, lo que CAMBIÓ en la ficha)."""
    antes = dict(_perfil)
    exito, _fb = L.procesar_respuesta_onboarding(NUM, campo, msg.lower())
    return exito, {k: v for k, v in _perfil.items() if antes.get(k) != v}


def main() -> int:
    L.actualizar_perfil = lambda n, **kw: _perfil.update(kw)
    L.cargar_perfil = lambda n: dict(_perfil)
    L.guardar_rm = lambda n, ej, rm, **kw: (_rms.update({ej: rm}), "")[1]
    L.guardar_dato_en_expediente = lambda n, t: None
    L.parsear_batch_rms = lambda n, m, solo_campos=None: []
    L.obtener_estado_usuario = lambda n: {}
    L._actualizar_estado_json = lambda n, **kw: None

    print("\n[19a] GÉNERO: nombrarlos no es elegir")
    for msg in ("Oye, tengo una duda en qué afecta que sea hombre o mujer",
                "¿en qué cambia si soy hombre o mujer?",
                "no sé si eso de ser hombre importa",
                "¿qué diferencia hay entre hombre y mujer para esto?"):
        exito, cambio = onb("genero", msg)
        chk(f"{msg[:44]!r}", not exito and not cambio, f"guardó {cambio}")

    print("\n[19a-bis] Y cuando SÍ elige, se guarda")
    for msg, esp in (("soy hombre", "hombre"), ("mujer", "mujer"),
                     ("soy varón", "hombre"), ("femenino", "mujer")):
        _perfil.clear()
        exito, cambio = onb("genero", msg)
        chk(f"{msg!r} -> {esp}", exito and cambio.get("genero") == esp, str(cambio))

    print("\n[19b] META: preguntar por ellas no es escoger una")
    for msg in ("No sé ni qué es Powerbuilding ni qué es Powerlifting",
                "¿qué diferencia hay entre powerlifting y powerbuilding?",
                "cuál me conviene, hipertrofia o powerlifting",
                "explícame qué es el powerbuilding"):
        _perfil.clear()
        exito, cambio = onb("meta", msg)
        chk(f"{msg[:44]!r}", not exito and not cambio, f"guardó {cambio}")

    print("\n[19b-bis] Las elecciones reales siguen entrando")
    for msg, esp in (("quiero powerbuilding", "powerbuilding"),
                     ("powerlifting", "powerlifting"),
                     ("quiero ganar músculo", "hipertrofia"),
                     ("busco fuerza y músculo", "powerbuilding"),
                     ("prefiero powerbuilding, no powerlifting", "powerbuilding")):
        _perfil.clear()
        exito, cambio = onb("meta", msg)
        chk(f"{msg[:38]!r} -> {esp}", exito and cambio.get("meta") == esp, str(cambio))

    print("\n[20] Corregir el género con OTRO campo pendiente")
    _perfil.clear()
    _perfil["genero"] = "hombre"
    corr = L._corregir_genero_declarado(NUM, "pero es que soy mujer")
    chk("la corrección se aplica de verdad", _perfil.get("genero") == "mujer",
        f"quedó {_perfil.get('genero')!r}")
    chk("y devuelve feedback para el coach", bool(corr), str(corr))
    chk("una mención de paso NO cambia nada",
        L._corregir_genero_declarado(NUM, "mi novia es mujer") is None)
    chk("repetir lo mismo no genera ruido",
        L._corregir_genero_declarado(NUM, "soy mujer") is None)
    chk("una duda tampoco corrige",
        L._corregir_genero_declarado(NUM, "¿y si fuera hombre o mujer?") is None)
    # La meta ya tenía este mecanismo desde v11: se comprueba que sigue vivo.
    _perfil["meta"] = "powerlifting"
    chk("el equivalente de la meta sigue funcionando",
        bool(L._corregir_meta_declarada(NUM, "en realidad quiero powerbuilding"))
        and _perfil.get("meta") == "powerbuilding", str(_perfil.get("meta")))

    print("\n[21] DOMINADAS ASISTIDAS: ni 6 kg ni 1RM inventado")
    for msg in ("hago 6 repeticiones de dominadas con liga",
                "6 dominadas asistidas con banda",
                "las hago en la máquina de asistencia",
                "hago 5 pero con goma"):
        _rms.clear()
        exito, _c = onb("rm_dominadas", msg)
        chk(f"{msg[:42]!r} -> sin lastre",
            exito and _rms.get("dominadas") == 0, f"guardó {_rms}")

    print("\n[21-bis] El lastre de verdad no se confunde con asistencia")
    _rms.clear()
    exito, _c = onb("rm_dominadas", "dominadas con 20 kilos de lastre saqué 6")
    chk("20 kg de lastre x 6 sí calcula 1RM",
        exito and (_rms.get("dominadas") or 0) > 20, f"guardó {_rms}")
    _rms.clear()
    exito, _c = onb("rm_dominadas", "hago 8 dominadas a peso corporal")
    chk("a peso corporal sigue siendo lastre 0",
        exito and _rms.get("dominadas") == 0, f"guardó {_rms}")

    print(f"\n{'='*74}\n  {ok} ok, {fallos} fallos\n{'='*74}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
