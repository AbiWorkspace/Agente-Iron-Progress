import re


# ======================================================================
# 1. TUS FUNCIONES EXACTAS EXTRAÍDAS DE llm_service.py
# ======================================================================
def _detectar_enfoque(msg: str) -> str | None:
    if re.search(r"\bpiernas?\b|\bpierna\b|\btren\s+inferior\b|\bpiern", msg):
        return "Pierna"
    if re.search(r"\btorsos?\b|\btren\s+superior\b|\bupper\b", msg):
        return "Torso"
    return None


def detectar_consulta_rutina(mensaje: str) -> dict | None:
    msg = mensaje.lower()
    m_dia = re.search(r'\bd[ií]a\s*(\d+)\b', msg)
    if m_dia:
        guard = re.search(r'rutina|plan|entren|toca|ver|dime|mu[eé]str|mostrar|dame|'
                          r'ense[ñn]|ejercicio|semana|saber|c[oó]mo', msg)
        if guard or len(msg.strip()) <= 45:
            return {"tipo": "dia_especifico", "dia_objetivo": int(m_dia.group(1)),
                    "enfoque": _detectar_enfoque(msg)}

    if re.search(r'(siguiente|pr[óo]xim[oa])\s+(d[ií]a|entrenamiento|sesi[oó]n)|'
                 r'd[ií]a\s+(siguiente|que\s+sigue)|\bqu[eé]\s+sigue\b|'
                 r'\b(?:y\s+)?(?:el|la)\s+(?:que\s+)?sigue\b|\bel\s+siguiente\b', msg):
        return {"tipo": "dia_siguiente", "enfoque": _detectar_enfoque(msg)}
    return None


def _avanzar_cursor(semana: int, dia: int, enfoque: str | None = None, max_saltos: int = 21):
    s, d = int(semana), int(dia)
    for _ in range(max_saltos):
        total = _contar_sesiones_semana(s)
        if not total: return None
        if d >= total:
            s, d = s + 1, 1
        else:
            d += 1
        if not _contar_sesiones_semana(s): return None
        if enfoque is None: return s, d

        sesiones = _sesiones_de_semana(s)
        if 1 <= d <= len(sesiones) and enfoque.lower() in str(sesiones[d - 1].get("Enfoque", "")).lower():
            return s, d
    return None


# ======================================================================
# 2. MOCK DE TU BASE DE DATOS (Plan Simulado)
# ======================================================================
PLAN_FALSO = {
    1: [{"Enfoque": "Torso"}, {"Enfoque": "Pierna"}, {"Enfoque": "Torso"}, {"Enfoque": "Pierna"}],
    2: [{"Enfoque": "Torso"}, {"Enfoque": "Pierna"}]
}


def _contar_sesiones_semana(sem): return len(PLAN_FALSO.get(sem, []))


def _sesiones_de_semana(sem): return PLAN_FALSO.get(sem, [])


# ======================================================================
# 3. SIMULADOR DE CHAT
# ======================================================================
estado = {"semana": 1, "dia": 1, "cursor_semana": 1, "cursor_dia": 1}

mensajes_prueba = [
    "quiero ver el día 2",
    "dame el siguiente dia",
    "y el siguiente dia de pierna estupido",
    "que sigue",
    "el proximo de torso"
]

print("=== INICIANDO SIMULADOR DE ORQUESTADOR ===\n")
print(
    f"ESTADO INICIAL -> Hoy es S{estado['semana']}D{estado['dia']}. Cursor en S{estado['cursor_semana']}D{estado['cursor_dia']}\n")

for msg in mensajes_prueba:
    print(f"TÚ: '{msg}'")
    consulta = detectar_consulta_rutina(msg)

    if not consulta:
        print("🤖 SISTEMA: No detecté consulta de rutina.\n")
        continue

    tipo = consulta["tipo"]
    enfoque = consulta.get("enfoque")

    if tipo == "dia_especifico":
        obj = consulta["dia_objetivo"]
        estado["cursor_dia"] = obj
        print(f"🤖 SISTEMA: Detecté día explícito. Reanclando cursor a Día {obj}.")
        print(f"   => MUESTRO: S{estado['cursor_semana']}D{obj} (Enfoque detectado: {enfoque})\n")

    elif tipo == "dia_siguiente":
        siguiente = _avanzar_cursor(estado["cursor_semana"], estado["cursor_dia"], enfoque)
        if siguiente:
            estado["cursor_semana"], estado["cursor_dia"] = siguiente
            print(f"🤖 SISTEMA: Avanzando cursor iterativamente (Filtro: {enfoque or 'Ninguno'})")
            print(f"   => MUESTRO: S{estado['cursor_semana']}D{estado['cursor_dia']}\n")
        else:
            print(f"🤖 SISTEMA: Llegamos al fin del plan buscando '{enfoque}'.\n")