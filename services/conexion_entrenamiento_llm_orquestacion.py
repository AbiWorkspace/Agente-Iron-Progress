from scripts.Entrenamiento import Distribucion_Torso_Pierna
from scripts.Entrenamiento import imprimir_bitacora_agrupada
from pathlib import Path

ruta_script_actual = Path(__file__).resolve().parent  # .../services
ruta_raiz_proyecto = ruta_script_actual.parent         # .../raiz del proyecto

def recibir_peticion(datos_usuario: dict, numero_usuario: str):
    print("=" * 50)
    print(f"[PUENTE]: Conectando el Orquestador con el Motor de Reglas...")
    print(f"[PUENTE]: Atleta -> {numero_usuario}")
    print(f"[PUENTE]: Datos recibidos -> {datos_usuario}")
    print("=" * 50)

    sesiones_por_semana = datos_usuario.get("dias", 6)
    nivel_atleta_llm    = datos_usuario.get("nivel", "avanzado")
    meta_usuario        = datos_usuario.get("meta", "powerbuilding")
    contexto_usuario    = datos_usuario.get(
        "contexto_usuario",
        f"Atleta {nivel_atleta_llm} enfocado en {meta_usuario}."
    )

    print(f"[PUENTE]: Contexto profundo extraído -> {contexto_usuario}")

    distribucion = Distribucion_Torso_Pierna(
        sesiones_por_semana=sesiones_por_semana,
        nivel_atleta=nivel_atleta_llm,
        meta=meta_usuario,
        contexto_del_usuario=contexto_usuario
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