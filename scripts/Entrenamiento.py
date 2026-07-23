"""
Motor de Programación de Entrenamiento — Distribucion_Torso_Pierna
==================================================================
Soporta 3, 4, 5, 6 y 7 días de entrenamiento por semana mediante
splits respaldados por la ciencia (mín. 48 h / máx. 72 h de descanso
entre sesiones del mismo grupo muscular).

Esta versión implementa las directrices del SISTEMA PRINCIPAL (prompt):

  · Punto 2 — Regla 1.0 / 0.5 de estiramiento maximal:
        cada ejercicio analítico puede declarar "factor_estiramiento"
        (1.0 o 0.5) en la base de datos. Las series físicas se calculan
        para cubrir el volumen EFECTIVO (físicas = efectivas / factor)
        y la poda contabiliza series efectivas, no físicas.
  · Punto 3 — Asimetría MEV-MAV por patrón (VOLUMEN_SEMANAL_POR_PATRON).
  · Punto 4 — Complementarios: bypass de la whitelist, inyectados AL FINAL
        de la sesión con el 75 % del volumen promedio por ejercicio
        principal, siempre a RPE 9.0.
  · Punto 5 — Fusión Lineal-Ondulante: el modo "DUP" ahora aplica la
        MATRIZ_FUSION_DUP (proporción de días DUP según el bloque:
        Acumulamiento 2H:1F · Fuerza 2F:1H · Realización 2F:1P).
        El modo "tradicional" (bloques lineales puros) se conserva.
  · Punto 6 — Guardia SNC: PROHIBIDO programar dos días de Fuerza Pesada
        en días de calendario consecutivos. Si la cola de fases lo
        pediría, la Fuerza se pospone y se adelanta la siguiente fase
        no-Fuerza del bloque (el conteo del bloque se conserva).
  · Punto 8 — Distribución matemática de esfuerzo por día DUP:
        - Reparto de series semanales:   Hipertrofia 50-60 % · Fuerza
          20-25 % · Potencia 15-20 %  (PESO_VOLUMEN_SEMANAL_DUP,
          renormalizado entre las fases presentes en la semana).
        - Bandas de intensidad:          Fuerza 80-95 % · Hipertrofia
          60-75 % · Potencia 50-70 %    (RANGO_INTENSIDAD_DUP; el %
          Epley del ejercicio brújula se recorta a la banda del día;
          en Potencia la carga se prescribe directa, no por RIR).
        - Olas de RPE por fase:          Fuerza 8.0-9.5 · Hipertrofia
          7.5-9.0 · Potencia ≤ 6 (fatiga nula, también en accesorios).

Periodizaciones disponibles:
  · "DUP" (alias "fusion") → Fusión Lineal-Ondulante: bloques macro
                             + días DUP en la proporción de la Matriz
                             de Fusión, con guardia SNC.
  · "tradicional"          → Por Bloques (lineal): Acumulamiento=
                             Hipertrofia, Fuerza=Fuerza,
                             Realizacion=Potencia.

Uso rápido:
    programa = Distribucion_Torso_Pierna(
        sesiones_por_semana = 4,          # 3 | 4 | 5 | 6 | 7
        nivel_atleta        = "intermedio",
        meta                = "powerbuilding",
        contexto_del_usuario= "...",
        tipo_periodizacion  = "DUP",      # "DUP"/"fusion" | "tradicional"
    )
    plan = programa.generar_programa()
"""

import math
import json
from pathlib import Path
from collections import defaultdict, deque
from types import MappingProxyType
from scripts.coherencia_entrenamiento import corregir_ejercicio

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from scripts.resources.Entrenamiento_llm_peticion import ServicioEntrenamiento


# ==============================================================================
#  CONSTANTES ORIGINALES DEL MOTOR
# ==============================================================================

MATRIZ_VOLUME = MappingProxyType({
    "Acumulamiento": {"Hipertrofia": 0.45, "Potencia": 0.50, "Fuerza": 0.55},
    "Fuerza":        {"Hipertrofia": 0.65, "Potencia": 0.70, "Fuerza": 0.75},
    "Realizacion":   {"Hipertrofia": 0.75, "Potencia": 0.85, "Fuerza": 0.80},
})

MATRIZ_REPS_RPE = MappingProxyType({
    "Acumulamiento": {"Hipertrofia": [12, 10, 8], "Fuerza": [9, 7, 5]},
    "Fuerza":        {"Hipertrofia": [10,  8, 6], "Fuerza": [7, 5, 3]},
    "Realizacion":   {"Hipertrofia": [ 6,  5, 3], "Fuerza": [5, 3, 1]},
})

VOLUMEN_ENTRENAMIENTO = MappingProxyType({
    "principiante": (10, 12),
    "intermedio":   (12, 15),
    "avanzado":     (15, 20),
})

CONFIGURACION_VOLUMEN_SESION = MappingProxyType({
    "powerlifting": {
        "Acumulamiento": {
            "Potencia":    {"compuestos": 2, "aislamiento": 1},
            "Hipertrofia": {"compuestos": 2, "aislamiento": 2},
            "Fuerza":      {"compuestos": 2, "aislamiento": 1},
        },
        "Fuerza": {
            "Potencia":    {"compuestos": 1, "aislamiento": 0},
            "Hipertrofia": {"compuestos": 2, "aislamiento": 1},
            "Fuerza":      {"compuestos": 2, "aislamiento": 0},
        },
        "Realizacion": {
            "Potencia":    {"compuestos": 1, "aislamiento": 0},
            "Hipertrofia": {"compuestos": 1, "aislamiento": 1},
            "Fuerza":      {"compuestos": 1, "aislamiento": 0},
        },
    },
    "powerbuilding": {
        "Acumulamiento": {
            "Potencia":    {"compuestos": 2, "aislamiento": 2},
            "Hipertrofia": {"compuestos": 2, "aislamiento": 4},
            "Fuerza":      {"compuestos": 2, "aislamiento": 2},
        },
        "Fuerza": {
            "Potencia":    {"compuestos": 1, "aislamiento": 1},
            "Hipertrofia": {"compuestos": 2, "aislamiento": 3},
            "Fuerza":      {"compuestos": 2, "aislamiento": 1},
        },
        "Realizacion": {
            "Potencia":    {"compuestos": 1, "aislamiento": 1},
            "Hipertrofia": {"compuestos": 1, "aislamiento": 2},
            "Fuerza":      {"compuestos": 1, "aislamiento": 0},
        },
    },
})


# ==============================================================================
#  SPLITS CIENTÍFICOS (con mapa de días de calendario para la guardia SNC)
# ==============================================================================
# Garantías de todos los splits:
#   · Mínimo 48 h de recuperación entre sesiones del mismo grupo muscular.
#   · Máximo 72 h antes de volver a estimular el mismo grupo.
#
# "dias_calendario": día de la semana (0=Lunes … 6=Domingo) de cada sesión.
# Permite detectar días de calendario CONSECUTIVOS (guardia SNC, punto 6),
# incluso a través del cambio de semana (p. ej. Domingo → Lunes).
#
SPLITS_CIENTIFICOS = MappingProxyType({
    3: {
        "nombre": "Torso-Pierna Alternado · 3 días/semana",
        "descripcion": (
            "Semanas impares T/P/T, pares P/T/P. "
            "48 h mín. entre sesiones del mismo grupo. Frec. ~1.5×/semana."
        ),
        "tipo": "alternado",
        "patron_impar": ["Torso", "Pierna", "Torso"],
        "patron_par":   ["Pierna", "Torso", "Pierna"],
        "dias_calendario": (0, 2, 4),          # L - Mi - V
        "dias_dup_semanales": 2,
    },
    4: {
        "nombre": "Upper/Lower 2x · 4 días/semana",
        "descripcion": (
            "L-M-J-V: Torso y Pierna 2×/semana. "
            "72 h de descanso entre sesiones del mismo grupo (L→J, M→V)."
        ),
        "tipo": "fijo",
        "patron": ["Torso", "Pierna", "Torso", "Pierna"],
        "dias_calendario": (0, 1, 3, 4),       # L - M - J - V
        "dias_dup_semanales": 2,
    },
    5: {
        "nombre": "Upper/Lower 3+2 · 5 días/semana",
        "descripcion": (
            "L-M-Mi-J-V: 3 Torso + 2 Pierna. "
            "48 h mín. entre sesiones del mismo grupo muscular."
        ),
        "tipo": "fijo",
        "patron": ["Torso", "Pierna", "Torso", "Pierna", "Torso"],
        "dias_calendario": (0, 1, 2, 3, 4),    # L a V
        "dias_dup_semanales": 3,
    },
    6: {
        "nombre": "Upper/Lower 3x · 6 días/semana",
        "descripcion": (
            "L a S: Torso y Pierna 3×/semana. "
            "48 h de recuperación entre sesiones del mismo grupo muscular."
        ),
        "tipo": "fijo",
        "patron": ["Torso", "Pierna", "Torso", "Pierna", "Torso", "Pierna"],
        "dias_calendario": (0, 1, 2, 3, 4, 5),  # L a S
        "dias_dup_semanales": 3,
    },
    7: {
        "nombre": "Upper/Lower 4+3 · 7 días/semana",
        "descripcion": (
            "L a D: 4 Torso + 3 Pierna. "
            "Solo para atletas avanzados. 48 h mín. entre sesiones del mismo grupo."
        ),
        "tipo": "fijo",
        "patron": ["Torso", "Pierna", "Torso", "Pierna", "Torso", "Pierna", "Torso"],
        "dias_calendario": (0, 1, 2, 3, 4, 5, 6),  # L a D
        "dias_dup_semanales": 4,
    },
})

# Mapeo mesociclo → fase fija para Periodización Tradicional (Bloques Lineales).
FASE_POR_MESO_TRADICIONAL = MappingProxyType({
    "Acumulamiento": "Hipertrofia",   # Bloque de volumen
    "Fuerza":        "Fuerza",        # Bloque de intensidad
    "Realizacion":   "Potencia",      # Bloque de pico (peaking)
})

# Calidades secundarias de mantenimiento por bloque en la periodización
# TRADICIONAL. La fase dominante sigue siendo la de FASE_POR_MESO_TRADICIONAL
# (carácter lineal), pero cada bloque conserva un estímulo mínimo de las otras
# dos calidades para que Potencia, Fuerza e Hipertrofia SIEMPRE tengan días
# (y línea de progresión) desde el primer bloque. Cada secuencia incluye
# obligatoriamente Potencia salvo Realizacion, donde ya es la dominante.
_SECUNDARIAS_LINEALES = MappingProxyType({
    "Acumulamiento": ("Fuerza", "Potencia"),
    "Fuerza":        ("Hipertrofia", "Potencia"),
    "Realizacion":   ("Fuerza", "Hipertrofia"),
})
# Secuencia cíclica por bloque: [dominante, secundaria, dominante, terciaria].
# El doble de la dominante mantiene el sesgo lineal del bloque; la secundaria
# y la terciaria (que incluye Potencia) garantizan las 3 calidades por bloque.
MATRIZ_LINEAL_TRADICIONAL = MappingProxyType({
    meso: (
        FASE_POR_MESO_TRADICIONAL[meso],   # día dominante del bloque
        _SECUNDARIAS_LINEALES[meso][0],    # calidad secundaria (mantenimiento)
        FASE_POR_MESO_TRADICIONAL[meso],   # refuerzo de la dominante
        _SECUNDARIAS_LINEALES[meso][1],    # calidad terciaria (incluye Potencia)
    )
    for meso in FASE_POR_MESO_TRADICIONAL
})

# ==============================================================================
#  PUNTO 5 · MATRIZ DE FUSIÓN LINEAL-ONDULANTE
# ==============================================================================
# Proporción de días DUP según el bloque macro. Cada grupo muscular (Torso /
# Pierna) consume su propia cola cíclica de fases, por lo que la proporción
# se cumple por grupo aunque el split sea asimétrico (3+2, 4+3, alternado).
#
#   · Acumulamiento → 2 días de Hipertrofia : 1 día de Fuerza   (según prompt)
#   · Fuerza        → 2 días de Fuerza : 1 día de Hipertrofia   (interpolado)
#   · Realizacion   → 2 días de Fuerza : 1 día de Potencia      (según prompt)
#
# El orden interno de cada secuencia intercala las fases pesadas para
# minimizar intervenciones de la guardia SNC.
MATRIZ_FUSION_DUP = MappingProxyType({
    "Acumulamiento": ("Hipertrofia", "Fuerza", "Hipertrofia"),
    "Fuerza":        ("Fuerza", "Hipertrofia", "Fuerza"),
    "Realizacion":   ("Fuerza", "Potencia", "Fuerza"),
})

# ==============================================================================
#  PUNTO 8 · DISTRIBUCIÓN MATEMÁTICA DE ESFUERZO POR DÍA DUP
# ==============================================================================
# Reparto de las series semanales entre los tipos de día presentes en la
# semana (puntos medios de los rangos del prompt, renormalizados entre las
# fases que realmente ocurren):
#   Hipertrofia 50-60 % → 0.55 · Fuerza 20-25 % → 0.225 · Potencia 15-20 % → 0.175
PESO_VOLUMEN_SEMANAL_DUP = MappingProxyType({
    "Hipertrofia": 0.550,
    "Fuerza":      0.225,
    "Potencia":    0.175,
})

# Bandas de intensidad por tipo de día (fracción del 1RM).
#   · El % del ejercicio BRÚJULA se recorta a la banda de su día.
#   · En días de Potencia la carga se prescribe DIRECTA dentro de 50-70 %
#     (una carga de velocidad no debe derivarse de RIR/Epley).
RANGO_INTENSIDAD_DUP = MappingProxyType({
    "Fuerza":      (0.80, 0.95),
    "Hipertrofia": (0.60, 0.75),
    "Potencia":    (0.50, 0.70),
})

# Olas de RPE por fase (orden: baja → alta → media), dentro de los rangos
# del prompt:  Fuerza 8.0-9.5 · Hipertrofia 7.5-9.0+ · Potencia ≤ 6.
RPE_POR_FASE = MappingProxyType({
    "Fuerza":      (8.0, 9.5, 8.5),
    "Hipertrofia": (7.5, 9.0, 8.0),
    "Potencia":    (5.0, 6.0, 5.5),
})

# ==============================================================================
#  PUNTO 3 · TARGET DE SERIES SEMANALES POR PATRÓN DE MOVIMIENTO (MEV→MAV)
# ==============================================================================
# Torso:  tracción ~ +20 % vs empuje (equilibrio escapular/postural).
# Pierna: empuje ≥ tracción (cuádriceps admite más volumen; se protege
#         la zona lumbar en los días de alto volumen vía selección LLM).
# Formato: (series_min_semanal, series_max_semanal). El motor escala de
# max→min a lo largo del macrociclo (semana 1 = max, última = min/deload).
VOLUMEN_SEMANAL_POR_PATRON = MappingProxyType({
    "principiante": {
        "Torso": {
            "empuje":   (8, 12),
            "traccion": (10, 14),
        },
        "Pierna": {
            "empuje":   (8, 12),
            "traccion": (6, 10),
        },
    },
    "intermedio": {
        "Torso": {
            "empuje":   (10, 16),
            "traccion": (12, 20),
        },
        "Pierna": {
            "empuje":   (10, 16),
            "traccion": (8, 14),
        },
    },
    "avanzado": {
        "Torso": {
            "empuje":   (14, 20),
            "traccion": (14, 22),
        },
        "Pierna": {
            "empuje":   (12, 20),
            "traccion": (10, 18),
        },
    },
})


# ==============================================================================
#  CLASE PRINCIPAL
# ==============================================================================

class Distribucion_Torso_Pierna:

    def __init__(
        self,
        sesiones_por_semana: int,
        nivel_atleta: str = "principiante",
        meta: str = "powerlifting",
        contexto_del_usuario: str = None,
        tipo_periodizacion: str = "DUP",   # "DUP"/"fusion" | "tradicional"
    ):
        # ── Validaciones de entrada ──────────────────────────────────────────
        if sesiones_por_semana not in SPLITS_CIENTIFICOS:
            raise ValueError(
                f"[CONFIG]: 'sesiones_por_semana' debe ser 3, 4, 5, 6 o 7. "
                f"Recibido: {sesiones_por_semana}.\n"
                f"Splits disponibles: {sorted(SPLITS_CIENTIFICOS.keys())}"
            )
        if tipo_periodizacion == "fusion":       # alias explícito del prompt
            tipo_periodizacion = "DUP"
        if tipo_periodizacion not in ("DUP", "tradicional"):
            raise ValueError(
                f"[CONFIG]: 'tipo_periodizacion' debe ser 'DUP' (fusión "
                f"lineal-ondulante) o 'tradicional'. Recibido: {tipo_periodizacion!r}"
            )
        if meta not in CONFIGURACION_VOLUMEN_SESION:
            raise ValueError(
                f"[CONFIG]: 'meta' debe ser una de "
                f"{sorted(CONFIGURACION_VOLUMEN_SESION.keys())}. Recibido: {meta!r}"
            )
        if nivel_atleta not in VOLUMEN_ENTRENAMIENTO:
            raise ValueError(
                f"[CONFIG]: 'nivel_atleta' debe ser una de "
                f"{sorted(VOLUMEN_ENTRENAMIENTO.keys())}. Recibido: {nivel_atleta!r}"
            )

        # ── Atributos ────────────────────────────────────────────────────────
        self.sesiones_por_semana   = sesiones_por_semana
        self.meta_usuario          = meta
        self.rango_volumen_semanal = VOLUMEN_ENTRENAMIENTO[nivel_atleta]
        self.mesociclos            = ["Acumulamiento", "Fuerza", "Realizacion"]
        self.dias_dup              = ["Potencia", "Hipertrofia", "Fuerza"]
        self.contexto_usuario      = contexto_del_usuario

        self.tipo_periodizacion = tipo_periodizacion
        self.split_config       = SPLITS_CIENTIFICOS[sesiones_por_semana]
        self.nivel_atleta       = nivel_atleta
        self.volumen_por_patron = VOLUMEN_SEMANAL_POR_PATRON[nivel_atleta]

        # ── LLM: los rangos pedidos se derivan de la configuración real ─────
        # (antes estaban fijos en "2", dejando cupos de aislamiento sin llenar
        #  cuando la sesión admitía 3-4 analíticos por patrón).
        cfg_meta = CONFIGURACION_VOLUMEN_SESION[meta]
        max_compuestos  = max(v["compuestos"]  for f in cfg_meta.values() for v in f.values())
        max_aislamiento = max(v["aislamiento"] for f in cfg_meta.values() for v in f.values())

        generar_listas = ServicioEntrenamiento()
        self.lista_blanca = generar_listas.extraer_listas_por_llm(
            contexto_usuario=contexto_del_usuario,
            rango_compuestos=str(max_compuestos),
            rango_aislamiento=str(max(1, max_aislamiento)),
        )

        ruta_db_ejercicios = (
            Path(__file__).resolve().parent.parent
            / "resources"
            / "base_de_datos_ejercicios.json"
        )
        try:
            with open(ruta_db_ejercicios, "r", encoding="utf-8") as archivo:
                self.ejercicios_db = json.load(archivo)
            print(
                f"[MOTOR]: Base de datos cargada.\n"
                f"  · Nivel        : {nivel_atleta.upper()} "
                f"— Rango {self.rango_volumen_semanal[0]}-{self.rango_volumen_semanal[1]} series.\n"
                f"  · Split        : {self.split_config['nombre']}\n"
                f"  · Descripción  : {self.split_config['descripcion']}\n"
                f"  · Periódización: "
                + ("DUP (Fusión Lineal-Ondulante)" if tipo_periodizacion == "DUP"
                   else "TRADICIONAL (Bloques Lineales)")
            )
        except FileNotFoundError:
            raise SystemExit(
                f"\n[ERROR CRÍTICO]: No se encontró el archivo de ejercicios.\n"
                f"El sistema lo buscó en:\n-> {ruta_db_ejercicios}\n"
            )
        except json.JSONDecodeError:
            raise SystemExit(
                f"\n[ERROR CRÍTICO]: El archivo 'base_de_datos_ejercicios.json' está corrupto.\n"
            )

    # ==========================================================================
    #  MÉTODOS ORIGINALES (sin cambios)
    # ==========================================================================

    def devolver_semana_y_dia(self, n_meso, microciclos, dia_sem):
        return n_meso * microciclos, dia_sem

    def _calcular_porcentaje_epley(self, reps: int, rpe: float) -> float:
        reps_efectivas = reps + (10.0 - rpe)
        return (52.2 + 41.9 * math.exp(-0.055 * reps_efectivas)) / 100.0

    def _calcular_porcentaje_ejercicios_brujula(
        self, mesociclo: str, fase_dup: str, rpe: float
    ) -> float:
        modificador_rpe = max(0, (7.5 - rpe) * 0.025)
        return max(0.3, min(0.90, MATRIZ_VOLUME[mesociclo][fase_dup] + modificador_rpe))

    def asignar_cantidad_ejercicios(self, meta, mesociclos: str, dia_dup: str):
        principales = CONFIGURACION_VOLUMEN_SESION[meta][mesociclos][dia_dup]["compuestos"]
        secundarios = CONFIGURACION_VOLUMEN_SESION[meta][mesociclos][dia_dup]["aislamiento"]
        if meta == "powerlifting" and principales > 1:
            principales -= 1
        return principales, secundarios

    def graficar_progresion_basicos(self, plan: list):
        datos_grafica, datos_grafica_multiarticulares = [], []
        tipos_ejercicios = {
            ej["nombre"]: ej.get("tipo", "").lower()
            for enfoque in ["Torso", "Pierna"]
            for ej in self.ejercicios_db.get(enfoque, [])
        }

        for sesion in plan:
            semana, fase = sesion["Semana_Abs"], sesion["Fase"]
            for ej in sesion["Ejercicios"]:
                nombre = ej["Ejercicio"]
                tipo   = tipos_ejercicios.get(nombre, "")
                if tipo not in ["basico-multiarticular", "multiarticular"]:
                    continue
                try:
                    porcentaje_1rm = float(ej["1RM"].replace("%", "")) / 100.0
                except ValueError:
                    porcentaje_1rm = 0.0
                row = {
                    "Semana": semana, "Fase": fase, "Ejercicio": nombre,
                    "Series": ej["Series"], "Reps": ej["Reps"], "RPE": ej["RPE"],
                    "Intensidad_%": porcentaje_1rm * 100,
                    "Tonelaje_Relativo": ej["Series"] * ej["Reps"] * porcentaje_1rm,
                }
                if tipo == "basico-multiarticular":
                    datos_grafica.append(row)
                else:
                    datos_grafica_multiarticulares.append(row)

        df  = pd.DataFrame(datos_grafica)
        df2 = pd.DataFrame(datos_grafica_multiarticulares)
        if df.empty:
            print("[ADVERTENCIA]: No se encontraron ejercicios básicos-multiarticulares.")
            return

        sns.set_theme(style="darkgrid")
        fig, axes = plt.subplots(4, 1, figsize=(18, 9.5), sharex=True)
        fig.suptitle("Análisis de Progresión DUP - Ejercicios Brújula", fontsize=16, fontweight="bold")

        sns.lineplot(ax=axes[0], data=df, x="Semana", y="Tonelaje_Relativo",
                     hue="Ejercicio", style="Fase", markers=True, dashes=False,
                     linewidth=2, legend=False)
        axes[0].set_title("Tonelaje Relativo (Series x Reps x Intensidad)", fontweight="bold")
        axes[0].set_ylabel("Índice de Estrés")
        axes[0].legend(loc="upper right", bbox_to_anchor=(1.15, 1))

        sns.lineplot(ax=axes[1], data=df, x="Semana", y="Reps",
                     hue="Ejercicio", style="Fase", markers=True, dashes=False, legend=False)
        axes[1].set_title("Repeticiones por Sesión (Perfil de Ondulación)", fontweight="bold")
        axes[1].set_ylabel("Repeticiones")

        sns.barplot(ax=axes[2], data=df, x="Semana", y="Series", hue="Fase", alpha=0.8, legend=False)
        axes[2].set_title("Ejercicios principales semanas", fontweight="bold")
        axes[2].set_ylabel("Cantidad de Series")

        semanas_unicas = sorted(df["Semana"].unique())
        ser = sorted(df["Series"].unique())
        axes[2].set_xticks(semanas_unicas)
        axes[2].set_xlim(min(semanas_unicas) - 0.5, max(semanas_unicas) + 0.5)
        axes[2].set_ylim(min(ser) - 3.0, max(ser) + 0.5)

        sns.barplot(ax=axes[3], data=df2, x="Semana", y="Series", hue="Fase", alpha=0.8, legend=False)
        axes[3].set_title("Ejercicios Secundarios (Multiarticulares)", fontweight="bold")
        axes[3].set_ylabel("Cantidad de Series")
        axes[3].set_xlabel("Semana Absoluta")
        axes[3].legend(loc="upper right", bbox_to_anchor=(1.15, 1))

        axes[3].set_xticks(semanas_unicas)
        axes[3].set_xlim(min(semanas_unicas) - 0.5, max(semanas_unicas) + 0.5)
        if not df2.empty:
            axes[3].set_ylim(0, max(sorted(df2["Series"].unique())) + 1.0)

        plt.tight_layout(rect=[0, 0, 0.85, 0.96])
        plt.show()

    def exportar_plan_por_mesociclos(
        self, plan_completo: list[dict], carpeta_destino=None
    ) -> None:
        carpeta_destino = (
            Path(carpeta_destino) if carpeta_destino
            else Path(__file__).resolve().parent / "resources"
        )
        try:
            carpeta_destino.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"[ERROR]: No se pudo acceder a '{carpeta_destino}': {e}")
            return

        mesociclos_agrupados = defaultdict(list)
        for sesion in plan_completo:
            mesociclos_agrupados[sesion.get("Mesociclo", "mesociclo_desconocido")].append(sesion)

        print(f"\n[SISTEMA]: Iniciando exportación a: {carpeta_destino}")
        for nombre, sesiones in mesociclos_agrupados.items():
            nombre_archivo = nombre.lower().replace(" - ", "_").replace(" ", "_")
            try:
                with open(carpeta_destino / f"{nombre_archivo}.json", "w", encoding="utf-8") as f:
                    json.dump(sesiones, f, indent=4, ensure_ascii=False)
                print(f"[OK]: {nombre_archivo}.json guardado.")
            except OSError as e:
                print(f"[ERROR]: Falló el guardado de {nombre_archivo}.json: {e}")
        print("[INFO]: Proceso terminado.\n")

    # ==========================================================================
    #  MÉTODOS DE APOYO DE LAS DIRECTRICES DEL PROMPT
    # ==========================================================================

    def _obtener_config_fase(self, fase: str, nombre_meso: str) -> dict:
        """
        Punto 8: la ola de RPE de cada fase vive dentro de su rango oficial
        (Fuerza 8.0-9.5 · Hipertrofia 7.5-9.0 · Potencia ≤ 6). Las reps del
        día siguen dependiendo del bloque macro (MATRIZ_REPS_RPE), que es la
        componente "lineal" de la fusión.
        """
        rpes = list(RPE_POR_FASE[fase])
        if fase == "Potencia":
            return {"reps": [3, 2, 1], "rpes": rpes}
        diccionario_fases = MATRIZ_REPS_RPE.get(nombre_meso, MATRIZ_REPS_RPE["Realizacion"])
        return {"reps": diccionario_fases.get(fase, [6, 5, 3]), "rpes": rpes}

    @staticmethod
    def _orden_seleccion(datos: dict) -> int:
        """
        Ordena la sesión: básicos → compuestos → analíticos → complementarios.
        Garantiza que el ejercicio brújula de cada patrón sea el básico
        (obligatorio en powerlifting, punto 1) cuando exista, y que los
        complementarios queden AL FINAL de la sesión (punto 4).
        """
        t = datos.get("tipo", "").lower()
        if "complementario" in t:
            return 3
        if "basico" in t:
            return 0
        if "monoarticular" in t or "analitico" in t:
            return 2
        if "multiarticular" in t:
            return 1
        return 2

    @staticmethod
    def _factor_estiramiento(datos: dict) -> float:
        """
        Punto 2 — Métrica de estiramiento maximal. Lee "factor_estiramiento"
        de la base de datos y lo normaliza al sistema estricto 1.0 / 0.5:
          · 1.0 → estiramiento máximo bajo carga + motor principal.
          · 0.5 → sinergista secundario o sin estiramiento máximo.
        Si el campo no existe, se asume 1.0 (retro-compatibilidad).
        """
        try:
            bruto = float(datos.get("factor_estiramiento", 1.0))
        except (TypeError, ValueError):
            return 1.0
        return 0.5 if bruto <= 0.75 else 1.0

    def _intensidad_compuesto(self, fase: str, reps: int, rpe: float, es_brujula: bool) -> float:
        """
        Punto 8 — Bandas de intensidad del día DUP:
          · Potencia: carga DIRECTA 55-65 % (RPE 5→55 %, 5.5→60 %, 6→65 %),
            nunca derivada de RIR: la velocidad manda, dentro de 50-70 %.
          · Fuerza / Hipertrofia: % Epley; el ejercicio BRÚJULA se recorta
            a la banda del día (80-95 % / 60-75 %). Los secundarios usan
            Epley puro para no prescribir combinaciones reps×%1RM imposibles.
        """
        if fase == "Potencia":
            lo, hi = RANGO_INTENSIDAD_DUP["Potencia"]
            return max(lo, min(hi, 0.55 + (rpe - 5.0) * 0.10))
        porcentaje = self._calcular_porcentaje_epley(reps, rpe)
        if es_brujula:
            lo, hi = RANGO_INTENSIDAD_DUP[fase]
            porcentaje = max(lo, min(hi, porcentaje))
        return porcentaje

    @staticmethod
    def _config_accesorios(fase: str, reps_brujula: int, rpe_brujula: float) -> dict:
        """
        Reps/RPE de secundarios y analíticos según el tipo de día (punto 8):
          · Hipertrofia: cercanía al fallo priorizada (RPE hasta 9.5).
          · Fuerza: accesorios en rango medio-alto sin exceder RPE 9.
          · Potencia: FATIGA NULA también en los accesorios (RPE 6).
        """
        if fase == "Hipertrofia":
            return {
                "reps_sec":  reps_brujula + 2, "rpe_sec":  min(9.5, rpe_brujula + 0.5),
                "reps_aisl": reps_brujula + 2, "rpe_aisl": min(9.5, rpe_brujula + 1.0),
            }
        if fase == "Fuerza":
            return {
                "reps_sec":  reps_brujula + 3, "rpe_sec":  min(9.0, rpe_brujula),
                "reps_aisl": reps_brujula + 5, "rpe_aisl": 8.5,
            }
        return {  # Potencia
            "reps_sec":  reps_brujula + 3, "rpe_sec":  6.0,
            "reps_aisl": reps_brujula + 5, "rpe_aisl": 6.0,
        }

    def _asignar_fase_fusion(self, enfoque: str, secuencia: tuple, dia_absoluto: int) -> str:
        """
        Punto 5 + Punto 6 — Asigna la fase DUP del día consumiendo la cola
        cíclica del grupo (proporción de la Matriz de Fusión) y aplicando la
        GUARDIA SNC: si la fase propuesta es "Fuerza" y la sesión del día de
        calendario INMEDIATAMENTE anterior (de cualquier grupo) también fue
        "Fuerza", la Fuerza se pospone (vuelve al frente de la cola) y se
        adelanta la primera fase no-Fuerza disponible. Ninguna fase se pierde:
        solo se reordena, así la proporción del bloque se conserva.
        """
        cola = self._cola_fases[enfoque]
        if not cola:
            cola.extend(secuencia)
        fase = cola.popleft()

        es_dia_consecutivo = (
            self._ultimo_dia_absoluto is not None
            and (dia_absoluto - self._ultimo_dia_absoluto) == 1
        )
        if fase == "Fuerza" and self._ultima_fase_global == "Fuerza" and es_dia_consecutivo:
            indice = next((i for i, f in enumerate(cola) if f != "Fuerza"), None)
            if indice is None:
                cola.extend(secuencia)
                indice = next(i for i, f in enumerate(cola) if f != "Fuerza")
            fase_alterna = cola[indice]
            del cola[indice]
            cola.appendleft(fase)          # la Fuerza pendiente se reprograma
            fase = fase_alterna

        self._ultima_fase_global  = fase
        self._ultimo_dia_absoluto = dia_absoluto
        return fase

    def _calcular_volumen_patron_sesion(
        self,
        enfoque: str,
        patron: str,
        fase_dup: str,
        cantidad_total_semanas: int,
        semana_actual: int,
        peso_fase: float,
        suma_pesos_grupo: float,
    ) -> int:
        """
        Series objetivo (EFECTIVAS) por sesión para UN patrón de movimiento.

        1. Consulta VOLUMEN_SEMANAL_POR_PATRON (punto 3: asimetría MEV-MAV)
           e interpola max→min a lo largo del macrociclo.
        2. Punto 8: el volumen semanal del patrón NO se divide por igual entre
           las sesiones del grupo, sino según el peso del tipo de día:
           Hipertrofia 0.55 · Fuerza 0.225 · Potencia 0.175, renormalizados
           entre las fases presentes esa semana. Así los días de Hipertrofia
           absorben ~50-60 % de las series semanales, la Fuerza Pesada
           ~20-25 % y la Potencia ~15-20 %.
        """
        min_vol, max_vol = self.volumen_por_patron[enfoque][patron]
        arreglo = np.linspace(max_vol, min_vol, cantidad_total_semanas)
        volumen_semanal = np.ceil(arreglo[max(0, int(semana_actual - 1))])
        bruto = volumen_semanal * (peso_fase / max(suma_pesos_grupo, 1e-9))
        if fase_dup in ("Potencia", "Hipertrofia"):
            return int(max(1, np.ceil(bruto)))
        return int(max(1, np.round(bruto)))

    # ==========================================================================
    #  generar_programa — Fusión Lineal-Ondulante + reglas del prompt
    # ==========================================================================

    def generar_programa(self) -> list:
        plan = []
        sesion_global = 0  # Contador global mantenido por compatibilidad

        # ── Configuración del split científico ────────────────────────────────
        split_config       = self.split_config
        dias_dup_semanales = split_config["dias_dup_semanales"]
        dias_calendario    = split_config["dias_calendario"]

        # Fórmula de semanas adaptada al split (preserva la escala original).
        semanas = int(54 / dias_dup_semanales)
        cantidad_total_semanas_por_macrociclo = len(self.mesociclos) * semanas

        # Estado de la guardia SNC (punto 6): última fase asignada y su día
        # de calendario absoluto, compartidos entre AMBOS grupos musculares
        # porque la fatiga central es sistémica.
        self._ultima_fase_global  = None
        self._ultimo_dia_absoluto = None

        tracker_progresion = {
            "Torso": {"Potencia": 0, "Fuerza": 0, "Hipertrofia": 0},
            "Pierna": {"Potencia": 0, "Fuerza": 0, "Hipertrofia": 0},
        }

        for n_meso, nombre_meso in enumerate(self.mesociclos, 1):
            # Cola de fases por grupo: se reinicia al cambiar de bloque para
            # que la proporción de la Matriz de Fusión entre limpia (punto 5).
            secuencia_fusion = MATRIZ_FUSION_DUP[nombre_meso]
            self._cola_fases = {"Torso": deque(), "Pierna": deque()}

            for n_micro in range(1, semanas + 1):
                semana_absoluta = ((n_meso - 1) * semanas) + n_micro

                # ── Patrón Torso/Pierna de la semana ──────────────────────────
                if split_config["tipo"] == "alternado":
                    patron_semana = (
                        split_config["patron_impar"] if n_micro % 2 == 1
                        else split_config["patron_par"]
                    )
                else:
                    patron_semana = split_config["patron"]

                # ══ PASO 1 · Fases de toda la semana (Fusión + Guardia SNC) ══
                fases_semana = []
                for dia_idx, enfoque in enumerate(patron_semana):
                    dia_abs = (semana_absoluta - 1) * 7 + dias_calendario[dia_idx]
                    if self.tipo_periodizacion == "tradicional":
                        # Lineal con calidades de mantenimiento: la dominante del
                        # bloque manda, pero Potencia/Fuerza/Hipertrofia siempre
                        # tienen día. Reusa la cola cíclica + guardia SNC.
                        fase = self._asignar_fase_fusion(
                            enfoque, MATRIZ_LINEAL_TRADICIONAL[nombre_meso], dia_abs
                        )
                    else:
                        fase = self._asignar_fase_fusion(enfoque, secuencia_fusion, dia_abs)

                    fases_semana.append(fase)

                # ══ PASO 2 · Pesos de volumen por fase presentes esta semana ══
                # (punto 8: Hipertrofia 50-60 % · Fuerza 20-25 % · Potencia
                #  15-20 % de las series semanales, renormalizado por grupo).
                suma_pesos_grupo = {"Torso": 0.0, "Pierna": 0.0}
                for dia_idx, enfoque in enumerate(patron_semana):
                    suma_pesos_grupo[enfoque] += PESO_VOLUMEN_SEMANAL_DUP[fases_semana[dia_idx]]

                # ══ PASO 3 · Construcción de cada sesión ══════════════════════
                for dia_idx, enfoque in enumerate(patron_semana):
                    fase      = fases_semana[dia_idx]
                    peso_fase = PESO_VOLUMEN_SEMANAL_DUP[fase]

                    config      = self._obtener_config_fase(fase, nombre_meso)
                    paso_actual = tracker_progresion[enfoque][fase]

                    reps_brujula = config["reps"][(paso_actual // len(config["rpes"])) % len(config["reps"])]
                    rpe_brujula  = config["rpes"][paso_actual % len(config["rpes"])]
                    tracker_progresion[enfoque][fase] += 1

                    porc_brujula = self._calcular_porcentaje_ejercicios_brujula(
                        nombre_meso, fase, rpe_brujula
                    )

                    # ── Volumen efectivo objetivo por patrón (puntos 3 y 8) ──
                    series_por_patron: dict[str, int] = {
                        patron: self._calcular_volumen_patron_sesion(
                            enfoque=enfoque,
                            patron=patron,
                            fase_dup=fase,
                            cantidad_total_semanas=cantidad_total_semanas_por_macrociclo,
                            semana_actual=semana_absoluta,
                            peso_fase=peso_fase,
                            suma_pesos_grupo=suma_pesos_grupo[enfoque],
                        )
                        for patron in ("empuje", "traccion")
                    }
                    series_totales_sesion_hoy = round(
                        sum(series_por_patron.values()) / max(1, len(series_por_patron))
                    )

                    limite_compuestos, limite_aislamientos = self.asignar_cantidad_ejercicios(
                        self.meta_usuario, mesociclos=nombre_meso, dia_dup=fase
                    )

                    # ── Selección de ejercicios ───────────────────────────────
                    ejercicios_hoy      = []
                    conteo_compuestos   = {"empuje": 0, "traccion": 0}
                    conteo_aislamientos = {"empuje": 0, "traccion": 0}

                    for datos in self.ejercicios_db[enfoque]:
                        tipo_ejercicio = datos.get("tipo", "").lower()

                        # Punto 4 · BYPASS: los complementarios entran directo
                        # sin pasar por la whitelist del LLM.
                        if "complementario" in tipo_ejercicio:
                            ejercicios_hoy.append(datos)
                            continue

                        nombre_ejercicio = datos.get("nombre", "")
                        if nombre_ejercicio not in self.lista_blanca:
                            continue

                        patron_movimiento = datos.get("patron_movimiento", "").lower()

                        es_compuesto   = "multiarticular" in tipo_ejercicio
                        es_aislamiento = ("monoarticular" in tipo_ejercicio
                                          or "analitico" in tipo_ejercicio)
                        es_basico      = "basico-multiarticular" in tipo_ejercicio

                        if patron_movimiento in ("empuje", "traccion"):
                            if self.meta_usuario == "powerlifting" and es_basico:
                                ejercicios_hoy.append(datos)
                            elif es_compuesto and conteo_compuestos[patron_movimiento] < limite_compuestos:
                                ejercicios_hoy.append(datos)
                                conteo_compuestos[patron_movimiento] += 1
                            elif es_aislamiento and conteo_aislamientos[patron_movimiento] < limite_aislamientos:
                                ejercicios_hoy.append(datos)
                                conteo_aislamientos[patron_movimiento] += 1

                    # Orden estable: básicos → compuestos → analíticos → compl.
                    # Así la brújula de cada patrón es el básico si existe.
                    ejercicios_hoy.sort(key=self._orden_seleccion)

                    # ── Ranuras reales por patrón ─────────────────────────────
                    n_compuestos = {"empuje": 0, "traccion": 0}
                    n_analiticos = {"empuje": 0, "traccion": 0}
                    for datos in ejercicios_hoy:
                        t  = datos.get("tipo", "").lower()
                        pm = datos.get("patron_movimiento", "").lower()
                        if "complementario" in t or pm not in ("empuje", "traccion"):
                            continue
                        if "multiarticular" in t:
                            n_compuestos[pm] += 1
                        elif "monoarticular" in t or "analitico" in t:
                            n_analiticos[pm] += 1

                    # Reparto por patrón: brújula = porc_brujula del target;
                    # el RESTO efectivo se divide entre ranuras de secundarios
                    # y analíticos (los analíticos convierten efectivas →
                    # físicas con su factor de estiramiento, punto 2).
                    series_brujula_por_patron: dict[str, int] = {}
                    efectivas_por_ranura:      dict[str, float] = {}
                    for _p, _vol in series_por_patron.items():
                        if n_compuestos[_p] > 0:
                            _brujula = int(max(1, np.round(_vol * porc_brujula)))
                        else:
                            _brujula = 0
                        series_brujula_por_patron[_p] = _brujula
                        ranuras = max(0, n_compuestos[_p] - 1) + n_analiticos[_p]
                        resto   = max(0, _vol - _brujula)
                        efectivas_por_ranura[_p] = (resto / ranuras) if ranuras > 0 else 0.0

                    accesorios = self._config_accesorios(fase, reps_brujula, rpe_brujula)

                    # ── Asignación de series a ejercicios PRINCIPALES ─────────
                    ejercicios_sesion_temporal = []
                    complementarios_pendientes = []
                    brujulas_asignadas = {"empuje": False, "traccion": False}

                    for datos in ejercicios_hoy:
                        nombre_ejercicio = datos.get("nombre", "")
                        tipo_ejercicio = datos.get("tipo", "").lower()
                        patron_movimiento = datos.get("patron_movimiento", "").lower()
                        es_compuesto = "multiarticular" in tipo_ejercicio

                        if "complementario" in tipo_ejercicio or patron_movimiento not in ("empuje", "traccion"):
                            complementarios_pendientes.append(datos)
                            continue

                        factor = self._factor_estiramiento(datos)

                        # Regla A — Ejercicio Brújula (primario del patrón)
                        if es_compuesto and not brujulas_asignadas[patron_movimiento]:
                            s = series_brujula_por_patron[patron_movimiento]
                            r = reps_brujula
                            rpe = rpe_brujula
                            perc = self._intensidad_compuesto(fase, r, rpe, es_brujula=True)
                            prioridad, factor = 4, 1.0
                            brujulas_asignadas[patron_movimiento] = True

                        # Regla B — Compuestos secundarios del patrón
                        elif es_compuesto:
                            efectivas = efectivas_por_ranura[patron_movimiento]
                            s = max(1, int(np.round(efectivas / factor)))
                            r = accesorios["reps_sec"]
                            rpe = accesorios["rpe_sec"]
                            perc = self._intensidad_compuesto(fase, r, rpe, es_brujula=False)
                            prioridad = 3

                        # Regla C — Analíticos: series físicas = efectivas / factor
                        else:
                            efectivas = efectivas_por_ranura[patron_movimiento]
                            s = max(1, int(math.ceil(efectivas / factor - 1e-9))) if efectivas > 0 else 1
                            r = accesorios["reps_aisl"]
                            rpe = accesorios["rpe_aisl"]
                            perc = self._calcular_porcentaje_epley(r, rpe)
                            prioridad = 2

                        # === INTEGRACIÓN DEL MOTOR DE COHERENCIA ===
                        # Pasamos el % a escala 0-100 para que el motor trabaje
                        pct_bruto = perc * 100.0

                        pct_corr, r_corr, rpe_corr, _ = corregir_ejercicio(
                            pct=pct_bruto,
                            reps=r,
                            rpe=rpe,
                            tipo_dia=fase  # Acota las variables a la banda del día DUP
                        )

                        # Devolvemos a escala 0.0 - 1.0 para compatibilidad interna
                        perc = pct_corr / 100.0

                        fila = {
                            "Enfoque": enfoque,
                            "Categoria": patron_movimiento,
                            "Ejercicio": nombre_ejercicio,
                            "tipo": tipo_ejercicio,
                            "Series": int(s),
                            "Reps": int(r_corr),
                            "RPE": round(float(rpe_corr), 1),
                            "1RM": f"{round(perc * 100, 1)}%",
                            "_prioridad": prioridad,
                            "_factor": factor,
                        }
                        if prioridad == 2:
                            fila["Factor_Estiramiento"] = factor
                        ejercicios_sesion_temporal.append(fila)

                    # ── Poda dinámica por patrón (volumen EFECTIVO, punto 2) ──
                    # ... [El código de poda se mantiene exactamente igual] ...

                    # ── Punto 4 · Inyección de COMPLEMENTARIOS al final ───────
                    series_principales = [ej["Series"] for ej in ejercicios_sesion_temporal]
                    if series_principales:
                        promedio_sesion = sum(series_principales) / len(series_principales)
                    else:
                        promedio_sesion = float(series_totales_sesion_hoy)

                    for datos in complementarios_pendientes:
                        r_c = max(10, reps_brujula + 5)
                        rpe_c = 9.0
                        perc_bruto_c = self._calcular_porcentaje_epley(r_c, rpe_c) * 100.0

                        # Coherencia para complementarios (forzados a lógica metabólica/hipertrofia)
                        pct_corr_c, r_corr_c, rpe_corr_c, _ = corregir_ejercicio(
                            pct=perc_bruto_c, reps=r_c, rpe=rpe_c, tipo_dia="hipertrofia"
                        )

                        ejercicios_sesion_temporal.append({
                            "Enfoque": enfoque,
                            "Categoria": datos.get("patron_movimiento", "").lower(),
                            "Ejercicio": datos.get("nombre", ""),
                            "tipo": datos.get("tipo", "").lower(),
                            "Series": int(max(1, math.ceil(promedio_sesion * 0.75))),
                            "Reps": int(r_corr_c),
                            "RPE": round(float(rpe_corr_c), 1),
                            "1RM": f"{round(pct_corr_c, 1)}%",
                            "_prioridad": 1,
                            "_factor": 0.0,  # no computa al volumen principal
                        })

                    # ── Limpieza, orden final y guardado en el plan ───────────
                    ejercicios_sesion_temporal.sort(key=lambda ej: -ej["_prioridad"])
                    ejercicios_sesion = []
                    for ej in ejercicios_sesion_temporal:
                        if "Factor_Estiramiento" in ej:
                            ej["Series_Efectivas"] = round(ej["Series"] * ej["_factor"], 1)
                        ej.pop("_prioridad", None)
                        ej.pop("_factor", None)
                        ejercicios_sesion.append(ej)

                    plan.append({
                        "Mesociclo":            f"{n_meso} - {nombre_meso}",
                        "Semana_Abs":           semana_absoluta,
                        "Fase":                 fase,
                        "Enfoque":              enfoque,
                        "Vol_Sistemico_Target": series_totales_sesion_hoy,
                        "Vol_Por_Patron":       dict(series_por_patron),
                        "Ejercicios":           ejercicios_sesion,
                    })
                    sesion_global += 1

        return plan


# ==============================================================================
#  FUNCIÓN DE UTILIDAD (sin cambios)
# ==============================================================================

def imprimir_bitacora_agrupada(plan: list):
    print("\n" + "=" * 115)
    print("SISTEMA DE ENTRENAMIENTO - ANALISIS DE PROGRESION POR FASE DUP")
    print("=" * 115)

    for enfoque in ["Torso", "Pierna"]:
        print(f"\n" + "#" * 115)
        print(f"[{enfoque.upper()}] - HISTORICO AGRUPADO POR TIPO DE SESION")
        print("#" * 115)

        for fase_objetivo in ["Potencia", "Fuerza", "Hipertrofia"]:
            print(f"\n>>> LINEA DE PROGRESION: {fase_objetivo.upper()} <<<")
            print("-" * 115)
            print(
                f"{'SEM':<4} | {'MESOCICLO':<15} | {'V.SIS':<5} | "
                f"{'CATEGORIA':<25} | {'EJERCICIO':<25} | {'SRxREP':<7} | {'%1RM':<6} | {'RPE':<4}"
            )
            print("-" * 115)

            sesiones_fase = [
                s for s in plan
                if s["Enfoque"] == enfoque and s["Fase"] == fase_objetivo
            ]

            for s in sesiones_fase:
                for i, ej in enumerate(s["Ejercicios"]):
                    sem    = str(s["Semana_Abs"]) if i == 0 else ""
                    meso   = s["Mesociclo"][:15]  if i == 0 else ""
                    vsis   = str(s.get("Vol_Sistemico_Target", "")) if i == 0 else ""
                    cat    = ej["Categoria"][:23]
                    srxrep = f"{ej['Series']}x{ej['Reps']}"
                    print(
                        f"{sem:<4} | {meso:<15} | {vsis:<5} | {cat:<25} | "
                        f"{ej['Ejercicio']:<25} | {srxrep:<7} | {ej['1RM']:<6} | {ej['RPE']:<4}"
                    )
                print("-" * 115)