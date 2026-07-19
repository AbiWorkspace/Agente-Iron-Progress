import asyncio
import sqlite3
import os
import json
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Importamos las herramientas de tu proyecto
from data.meta_service import enviar_mensaje_whatsapp
from services.llm_service import cliente_ollama, ruta_estado, OPCIONES_QWEN_32B
from services.db_service import ruta_db, inicializar_db


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
        if not os.path.exists(ruta_estado):
            return []
        with open(ruta_estado, "r", encoding="utf-8") as f:
            estados = json.load(f)
        return [{"numero_usuario": num} for num in estados.keys()]

    def _alerta_enviada_esta_semana(self, numero_usuario: str, tipo_alerta: str) -> bool:
        """Verifica el escudo anti-spam con la semana ISO actual."""
        semana_iso = datetime.now().strftime('%G-W%V')
        conn = sqlite3.connect(ruta_db)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 1 FROM alertas_enviadas 
            WHERE numero_usuario = ? AND semana_iso = ? AND tipo = ?
        ''', (numero_usuario, semana_iso, tipo_alerta))
        resultado = cursor.fetchone()
        conn.close()
        return bool(resultado)

    def _registrar_alerta(self, numero_usuario: str, tipo_alerta: str):
        semana_iso = datetime.now().strftime('%G-W%V')
        conn = sqlite3.connect(ruta_db)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO alertas_enviadas (numero_usuario, tipo, semana_iso)
            VALUES (?, ?, ?)
        ''', (numero_usuario, tipo_alerta, semana_iso))
        conn.commit()
        conn.close()

    def _detectar_anomalias(self, numero_usuario: str) -> list[AlertaDetectada]:
        alertas = []
        conn = sqlite3.connect(ruta_db)
        cursor = conn.cursor()

        # 1. Conteo de sesiones (Adherencia)
        cursor.execute('''
            SELECT COUNT(DISTINCT date(fecha_hora)) FROM series_log
            WHERE numero_usuario = ? AND date(fecha_hora) >= date('now', '-7 days')
        ''', (numero_usuario,))
        sesiones = cursor.fetchone()[0] or 0

        # 2. RPE Promedio
        cursor.execute('''
            SELECT AVG(rpe_reportado) FROM series_log
            WHERE numero_usuario = ? AND date(fecha_hora) >= date('now', '-5 days')
            AND rpe_reportado IS NOT NULL
        ''', (numero_usuario,))
        rpe_row = cursor.fetchone()
        rpe_prom = rpe_row[0] if rpe_row[0] else 0

        # 3. Fatiga VBT Promedio
        cursor.execute('''
            SELECT AVG(vbt_perdida_velocidad) FROM series_log
            WHERE numero_usuario = ? AND date(fecha_hora) >= date('now', '-7 days')
            AND vbt_perdida_velocidad IS NOT NULL
        ''', (numero_usuario,))
        vl_row = cursor.fetchone()
        vl_prom = vl_row[0] if vl_row[0] else 0

        conn.close()

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
        response = await cliente_ollama.chat(
            model="qwen3.6:27b",
            keep_alive=-1,
            think=False,
            # Usamos el contexto unificado, pero dándole un poco más de libertad creativa (0.7)
            options={**OPCIONES_QWEN_32B, "temperature": 0.7, "num_predict": 4096}
        )

        mensaje = response.get("message", {}).get("content", "").strip()

        print(f"[DEBUG PROACTIVO] Respuesta cruda del LLM: {mensaje}")

        if mensaje:
            try:
                enviar_mensaje_whatsapp(atleta["numero_usuario"], mensaje)
                self._registrar_alerta(atleta["numero_usuario"], tipo_alerta)
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
            alertas = self._detectar_anomalias(a["numero_usuario"])
            criticas = [x for x in alertas if x.severidad >= 2]
            if criticas:
                if not self._alerta_enviada_esta_semana(a["numero_usuario"], "fatiga_diaria"):
                    await self._enviar_alerta(a, criticas, "fatiga_diaria")


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