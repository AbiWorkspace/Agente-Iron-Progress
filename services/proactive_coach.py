"""
proactive_coach.py — Escaneo diario de fatiga (alertas proactivas).

OJO: este scheduler NO está enganchado en main.py. El readiness matutino que sí
corre en producción vive en services/proactividad.py. Este módulo queda listo
para activarse (llamando a `iniciar_scheduler()` desde el lifespan) sin las tres
minas que tenía:
  · llamaba a Ollama saltándose SEM_LLM -> dos contextos del modelo a la vez en
    24 GB de VRAM es exactamente el thrashing que congelaba el equipo;
  · fijaba el nombre del modelo a mano, así que un cambio en llm_service.MODELO_LLM
    dejaba aquí un modelo distinto rotando en memoria;
  · enviaba el WhatsApp de forma síncrona desde una corrutina, congelando el
    event loop durante todo el envío.
"""
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Importamos las herramientas de tu proyecto
from data.meta_service import enviar_mensaje_whatsapp
from services.llm_service import chat_llm, ruta_estado, OPCIONES_QWEN_32B
from services.db_service import conexion_db, inicializar_db


class Anomalia(Enum):
    BAJA_ADHERENCIA = "baja_adherencia"
    FATIGA_ACUMULADA = "fatiga_acumulada"
    DELOAD_REQUERIDO = "deload_requerido"


@dataclass
class AlertaDetectada:
    tipo: Anomalia
    severidad: int  # 1=info · 2=warning · 3=crítica
    datos: dict = field(default_factory=dict)


class ProactiveCoachService:
    UMBRALES = {
        "min_sesiones_semana": 3,  # Si entrenas menos de 3 días a la semana
        "rpe_alto": 8.5,  # RPE promedio crítico
        "velocity_loss_critico": 25.0,  # Pérdida de velocidad media semanal
    }

    def _get_atletas_activos(self):
        """Lee el estado_usuarios.json para saber a quién auditar."""
        try:
            with open(ruta_estado, "r", encoding="utf-8") as f:
                estados = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[PROACTIVIDAD] no se pudo leer el estado de atletas: {e}")
            return []
        if not isinstance(estados, dict):
            return []
        return [{"numero_usuario": num} for num in estados.keys()]

    def _alerta_enviada_esta_semana(self, numero_usuario: str, tipo_alerta: str) -> bool:
        """Verifica el escudo anti-spam con la semana ISO actual."""
        semana_iso = datetime.now().strftime('%G-W%V')
        with conexion_db(solo_lectura=True) as conn:
            resultado = conn.execute('''
                SELECT 1 FROM alertas_enviadas
                WHERE numero_usuario = ? AND semana_iso = ? AND tipo = ?
            ''', (numero_usuario, semana_iso, tipo_alerta)).fetchone()
        return bool(resultado)

    def _registrar_alerta(self, numero_usuario: str, tipo_alerta: str):
        semana_iso = datetime.now().strftime('%G-W%V')
        with conexion_db() as conn:
            conn.execute('''
                INSERT INTO alertas_enviadas (numero_usuario, tipo, semana_iso)
                VALUES (?, ?, ?)
            ''', (numero_usuario, tipo_alerta, semana_iso))

    def _detectar_anomalias(self, numero_usuario: str) -> list[AlertaDetectada]:
        alertas = []

        with conexion_db(solo_lectura=True) as conn:
            # 1. Conteo de sesiones (Adherencia)
            sesiones = conn.execute('''
                SELECT COUNT(DISTINCT date(fecha_hora)) FROM series_log
                WHERE numero_usuario = ? AND date(fecha_hora) >= date('now', '-7 days')
            ''', (numero_usuario,)).fetchone()[0] or 0

            # 2. RPE Promedio
            rpe_row = conn.execute('''
                SELECT AVG(rpe_reportado) FROM series_log
                WHERE numero_usuario = ? AND date(fecha_hora) >= date('now', '-5 days')
                AND rpe_reportado IS NOT NULL
            ''', (numero_usuario,)).fetchone()
            rpe_prom = rpe_row[0] if rpe_row and rpe_row[0] else 0

            # 3. Fatiga VBT Promedio
            vl_row = conn.execute('''
                SELECT AVG(vbt_perdida_velocidad) FROM series_log
                WHERE numero_usuario = ? AND date(fecha_hora) >= date('now', '-7 days')
                AND vbt_perdida_velocidad IS NOT NULL
            ''', (numero_usuario,)).fetchone()
            vl_prom = vl_row[0] if vl_row and vl_row[0] else 0

        # Evaluamos los umbrales
        if sesiones < self.UMBRALES["min_sesiones_semana"]:
            alertas.append(AlertaDetectada(
                tipo=Anomalia.BAJA_ADHERENCIA,
                severidad=3 if sesiones == 0 else 2,
                datos={"dias_entrenados": sesiones}
            ))

        if rpe_prom > self.UMBRALES["rpe_alto"]:
            alertas.append(AlertaDetectada(
                tipo=Anomalia.FATIGA_ACUMULADA,
                severidad=2 if rpe_prom < 9.0 else 3,
                datos={"rpe_promedio": round(rpe_prom, 1)}
            ))

        if vl_prom > self.UMBRALES["velocity_loss_critico"]:
            alertas.append(AlertaDetectada(
                tipo=Anomalia.DELOAD_REQUERIDO,
                severidad=3,
                datos={"perdida_velocidad_promedio_pct": round(vl_prom, 1)}
            ))

        return alertas

    async def _enviar_alerta(self, atleta, alertas: list[AlertaDetectada], tipo_alerta: str):
        alertas_str = "\n".join([f"- {a.tipo.value} (Severidad {a.severidad}): {a.datos}" for a in alertas])

        prompt = f"""Eres Iron Progress, el coach de powerlifting del atleta.
Inicia una conversación proactiva por WhatsApp.
Estas anomalías matemáticas fueron detectadas en su telemetría SQL de los últimos 7 días:
{alertas_str}

Escribe UN mensaje corto (máx 4 líneas). Tono: directo de coach, motivador, sin alarmar.
No uses asteriscos dobles. Hazle una pregunta abierta al final para invitarlo a ajustar su entrenamiento.
Habla en primera persona y NO menciones bases de datos, SQL o telemetría explícitamente."""

        print(f"[PROACTIVIDAD] Pidiendo al LLM que redacte alerta para {atleta['numero_usuario']}...")
        try:
            # chat_llm, NO cliente_ollama directo: pasa por SEM_LLM (una sola
            # inferencia a la vez), respeta MODELO_LLM y aplica el timeout.
            response = await chat_llm(
                [{"role": "user", "content": prompt}],
                # Mismo contexto unificado con un poco más de libertad creativa (0.7)
                opciones={**OPCIONES_QWEN_32B, "temperature": 0.7, "num_predict": 4096},
                pensar=False,
                quien="alerta-proactiva",
            )
        except Exception as e:
            print(f"[ERROR PROACTIVIDAD] el modelo no respondió: {e}")
            return

        mensaje = (response.get("message", {}) or {}).get("content", "").strip()

        print(f"[DEBUG PROACTIVO] Respuesta cruda del LLM: {mensaje}")

        if mensaje:
            try:
                # A un hilo: enviar_mensaje_whatsapp es síncrona (requests +
                # pausas anti rate-limit) y aquí congelaba el event loop entero.
                await asyncio.to_thread(enviar_mensaje_whatsapp,
                                        atleta["numero_usuario"], mensaje)
                await asyncio.to_thread(self._registrar_alerta,
                                        atleta["numero_usuario"], tipo_alerta)
                print(f"[PROACTIVIDAD] Alerta '{tipo_alerta}' enviada con éxito.")
            except Exception as e:
                print(f"[ERROR PROACTIVIDAD] No se pudo enviar mensaje: {e}")

    async def run_scan_diario(self):
        """El motor que corre todos los días buscando anomalías críticas."""
        print("[CRON JOB] Ejecutando escaneo diario de fatiga...")

        # NUEVO: Asegurarnos de que las tablas existan antes de hacer el SELECT
        inicializar_db()

        atletas = self._get_atletas_activos()
        for a in atletas:
            # Un atleta con datos raros no puede tumbar el escaneo de los demás.
            try:
                alertas = await asyncio.to_thread(self._detectar_anomalias, a["numero_usuario"])
                criticas = [x for x in alertas if x.severidad >= 2]
                if criticas and not await asyncio.to_thread(
                        self._alerta_enviada_esta_semana, a["numero_usuario"], "fatiga_diaria"):
                    await self._enviar_alerta(a, criticas, "fatiga_diaria")
            except Exception as e:
                print(f"[ERROR PROACTIVIDAD] escaneo de {a['numero_usuario']} falló: {e}")


def iniciar_scheduler():
    scheduler = AsyncIOScheduler(timezone="America/Mexico_City")
    coach = ProactiveCoachService()

    # 1. MODO PRODUCCIÓN: Programado para las 9:00 AM (o la hora que prefieras)
    scheduler.add_job(
        coach.run_scan_diario,
        trigger=CronTrigger(hour=9, minute=0),
        id="daily_fatigue",
        replace_existing=True
    )

    # 2. LIMPIEZA: Si quedó algún job de pruebas anterior, lo borramos al iniciar
    if scheduler.get_job("test_scan"):
        scheduler.remove_job("test_scan")

    scheduler.start()
    return scheduler