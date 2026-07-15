def calcular_ffmi(peso_kg, porcentaje_grasa, altura_m):
    """Calcula el FFMI Normalizado."""
    if altura_m <= 0 or porcentaje_grasa < 0:
        return 0
    masa_libre_grasa = peso_kg * (1 - (porcentaje_grasa / 100))
    ffmi = masa_libre_grasa / (altura_m ** 2)
    ffmi_normalizado = ffmi + 6.1 * (1.8 - altura_m)
    return round(ffmi_normalizado, 2)


def clasificar_ffmi(ffmi_norm, genero):
    """Clasifica el nivel basado estrictamente en el FFMI Normalizado."""
    g = genero.lower()
    if g in ["mujer", "femenino"]:
        if ffmi_norm < 16.0:
            return "principiante"
        elif ffmi_norm < 18.0:
            return "intermedio"
        else:
            return "avanzado"
    else:  # Hombre por defecto
        if ffmi_norm < 20.0:
            return "principiante"
        elif ffmi_norm < 22.0:
            return "intermedio"
        else:
            return "avanzado"


def evaluar_perfil_completo(datos_usuario: dict) -> dict:
    """
    Recibe los datos físicos y marcas, calcula el FFMI y devuelve un diagnóstico.
    """
    peso = datos_usuario.get("peso_kg", 80.0)
    altura = datos_usuario.get("altura_m", 1.75)
    grasa = datos_usuario.get("porcentaje_grasa", 15.0)
    genero = datos_usuario.get("genero", "hombre")

    # 1. Calculamos FFMI
    ffmi_norm = calcular_ffmi(peso, grasa, altura)
    nivel_ffmi = clasificar_ffmi(ffmi_norm, genero)

    # 2. Construimos el reporte para Qwen
    diagnostico = (
        f"--- RESULTADOS DEL LABORATORIO BIOMÉTRICO ---\n"
        f"FFMI Normalizado: {ffmi_norm}\n"
        f"Nivel Fisiológico (según masa muscular): {nivel_ffmi.upper()}\n"
        f"-------------------------------------------\n"
        f"INSTRUCCIÓN PARA EL COACH: El algoritmo determinó que el nivel real de masa libre de grasa de este atleta corresponde a un {nivel_ffmi.upper()}. "
        f"Usa este nivel ('{nivel_ffmi}') en el parámetro 'nivel' cuando ejecutes la herramienta 'generar_rutina'."
    )

    return {
        "ffmi": ffmi_norm,
        "nivel_sugerido": nivel_ffmi,
        "reporte_texto": diagnostico
    }