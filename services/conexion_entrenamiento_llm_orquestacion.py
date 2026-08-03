from scripts.Entrenamiento import Distribucion_Torso_Pierna
from scripts.Entrenamiento import imprimir_bitacora_agrupada
from scripts.Entrenamiento import (
    CONFIGURACION_VOLUMEN_SESION,
    VOLUMEN_ENTRENAMIENTO,
    SPLITS_CIENTIFICOS,
)
from pathlib import Path

ruta_script_actual = Path(__file__).resolve().parent  # .../services
ruta_raiz_proyecto = ruta_script_actual.parent         # .../raiz del proyecto

# Sinónimos que el LLM o el perfil del atleta pueden traer y que el motor de
# reglas no conoce. Sin esto, un valor inesperado lanza ValueError DENTRO del
# hilo de background: el atleta nunca recibe su plan y nadie se entera.
_ALIAS_META = {
    "powerbuilding": "powerbuilding", "power building": "powerbuilding",
    "powerlifting": "powerlifting", "power lifting": "powerlifting",
    "fuerza": "powerlifting", "fuerza pura": "powerlifting",
    "hipertrofia": "hipertrofia", "masa": "hipertrofia",
    "volumen": "hipertrofia", "musculo": "hipertrofia", "músculo": "hipertrofia",
}
_ALIAS_NIVEL = {
    "principiante": "principiante", "novato": "principiante", "basico": "principiante",
    "básico": "principiante", "iniciado": "principiante",
    "intermedio": "intermedio", "medio": "intermedio",
    "avanzado": "avanzado", "experto": "avanzado", "elite": "avanzado",
}


def _normalizar(valor, alias: dict, validos, defecto: str, etiqueta: str) -> str:
    crudo = str(valor or "").strip().lower()
    elegido = alias.get(crudo, crudo)
    if elegido not in validos:
        print(f"[PUENTE]: {etiqueta} {valor!r} no reconocida -> uso {defecto!r}")
        return defecto
    return elegido

def recibir_peticion(datos_usuario: dict, numero_usuario: str):
    print("=" * 50)
    print(f"[PUENTE]: Conectando el Orquestador con el Motor de Reglas...")
    print(f"[PUENTE]: Atleta -> {numero_usuario}")
    print(f"[PUENTE]: Datos recibidos -> {datos_usuario}")
    print("=" * 50)

    try:
        sesiones_por_semana = int(datos_usuario.get("dias") or 6)
    except (TypeError, ValueError):
        sesiones_por_semana = 6
    if sesiones_por_semana not in SPLITS_CIENTIFICOS:
        validos = sorted(SPLITS_CIENTIFICOS.keys())
        ajustado = min(validos, key=lambda d: abs(d - sesiones_por_semana))
        print(f"[PUENTE]: {sesiones_por_semana} días/semana no tiene split -> uso {ajustado}")
        sesiones_por_semana = ajustado

    nivel_atleta_llm = _normalizar(
        datos_usuario.get("nivel"), _ALIAS_NIVEL, VOLUMEN_ENTRENAMIENTO,
        "avanzado", "Nivel")   # se conserva el default original del puente
    meta_usuario = _normalizar(
        datos_usuario.get("meta"), _ALIAS_META, CONFIGURACION_VOLUMEN_SESION,
        "powerbuilding", "Meta")

    contexto_usuario    = datos_usuario.get(
        "contexto_usuario",
        f"Atleta {nivel_atleta_llm} enfocado en {meta_usuario}."
    )

    # El orquestador ya resuelve esto a 'DUP' o 'tradicional'; aquí solo se acota
    # por si la petición llega de otra vía. Antes ni se leía: el motor caía siempre
    # en su default DUP aunque el atleta hubiera pedido bloques lineales.
    tipo_periodizacion = str(datos_usuario.get("tipo_periodizacion") or "DUP").strip()
    if tipo_periodizacion not in ("DUP", "fusion", "tradicional"):
        print(f"[PUENTE]: periodización {tipo_periodizacion!r} no reconocida -> uso DUP")
        tipo_periodizacion = "DUP"

    print(f"[PUENTE]: Contexto profundo extraído -> {contexto_usuario}")
    print(f"[PUENTE]: Periodización -> {tipo_periodizacion}")

    distribucion = Distribucion_Torso_Pierna(
        sesiones_por_semana=sesiones_por_semana,
        nivel_atleta=nivel_atleta_llm,
        meta=meta_usuario,
        contexto_del_usuario=contexto_usuario,
        tipo_periodizacion=tipo_periodizacion
    )

    programa = distribucion.generar_programa()


    # Ruta absoluta: nunca falla sin importar desde dónde se ejecute el proceso
    carpeta_rutinas = ruta_raiz_proyecto / "resources" / "rutinas_generadas" / str(numero_usuario)
    carpeta_rutinas.mkdir(parents=True, exist_ok=True)

    distribucion.exportar_plan_por_mesociclos(
        plan_completo=programa,
        carpeta_destino=carpeta_rutinas
    )

    print(f"[PUENTE]: ¡Plan guardado en {carpeta_rutinas}!")
    return programa