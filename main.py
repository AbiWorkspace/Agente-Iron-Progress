import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings

# Importaciones de tu arquitectura
from api.rutas_whatsapp import router as whatsapp_router
from api.rutas_whatsapp import limpiar_cache_videos, cerrar_cliente_http

# V9: Importamos el motor proactivo desde el nuevo archivo
from services.proactividad import iniciar_scheduler

VERSION = "9.1.0"

# Ruta ABSOLUTA. Antes era "data/estado_usuarios.json" relativa al directorio de
# trabajo: si uvicorn se arrancaba desde otra carpeta (systemd, Docker, un
# supervisor), el archivo no se encontraba, el except se lo tragaba y el
# readiness matutino no salía para NADIE, en silencio.
RUTA_ESTADO_USUARIOS = Path(__file__).resolve().parent / "data" / "estado_usuarios.json"


def mis_usuarios_activos():
    """Lee el estado de los atletas y filtra a quién enviarle el Readiness a las 6:30 AM."""
    activos = []
    try:
        with open(RUTA_ESTADO_USUARIOS, "r", encoding="utf-8") as f:
            estados = json.load(f)
    except FileNotFoundError:
        print(f"[READINESS]: no existe {RUTA_ESTADO_USUARIOS}; no hay atletas a los que escribir.")
        return activos
    except json.JSONDecodeError as e:
        print(f"[READINESS]: estado_usuarios.json corrupto ({e}); se omite el broadcast.")
        return activos

    if not isinstance(estados, dict):
        return activos

    for numero, estado in estados.items():
        if not isinstance(estado, dict):
            continue
        # Filtros V9: No enviamos a atletas pausados o en onboarding
        if estado.get("plan_pausado") == 1:
            continue
        if estado.get("onboarding_activo"):
            continue
        # Agregamos al atleta con un nombre por defecto (el bot lo usará en la plantilla)
        activos.append((numero, "campeón"))
    return activos


@asynccontextmanager
async def lifespan(app: FastAPI):
    # [STARTUP]: Todo lo que pongas antes del yield se ejecuta al encender
    print("[SISTEMA]: Inicializando motores de Iron Progress V9...")
    print("[SISTEMA]: Configuración detectada:")
    print(settings.diagnostico())

    faltantes = settings.faltantes()
    if faltantes:
        # No abortamos el arranque (el health check sigue sirviendo para
        # diagnosticar), pero que quede MUY claro en el log por qué el bot no
        # responderá a nadie.
        print(f"[SISTEMA]: ⚠ FALTAN VARIABLES CRÍTICAS EN EL .env -> {', '.join(faltantes)}. "
              "El bot NO podrá enviar ni verificar mensajes hasta que las definas.")

    # 1. Tarea en segundo plano original (Limpieza de caché)
    tarea_limpieza = asyncio.create_task(limpiar_cache_videos())

    # 2. Arrancando el cerebro proactivo (Cron Jobs a las 6:30 AM)
    # scheduler se inicializa antes del try: si iniciar_scheduler revienta,
    # el shutdown de abajo daría UnboundLocalError y la app no cerraría limpio.
    scheduler = None
    try:
        print("[SISTEMA]: Arrancando motor de proactividad matutino...")
        scheduler = iniciar_scheduler(mis_usuarios_activos, hora=6, minuto=30)
    except Exception as e:
        print(f"[SISTEMA]: ERROR al arrancar la proactividad -> {e}")

    print(f"[SISTEMA]: Iron Progress v{VERSION} operativo.")

    yield  # Aquí es donde la aplicación "vive" y atiende peticiones

    # [SHUTDOWN]: Todo lo que pongas después del yield se ejecuta al apagar
    print("[SISTEMA]: Limpiando recursos y apagando orquestador...")

    # Apagamos el motor de proactividad de forma segura
    if scheduler:
        try:
            scheduler.shutdown(wait=False)
            print("[SISTEMA]: Motor de proactividad apagado correctamente.")
        except Exception as e:
            print(f"[SISTEMA]: fallo al apagar el scheduler -> {e}")

    # Apagamos la tarea de limpieza de videos
    tarea_limpieza.cancel()
    try:
        await tarea_limpieza
    except asyncio.CancelledError:
        print("[SISTEMA]: Tarea de limpieza finalizada correctamente.")

    # Cerramos el pool HTTP compartido (conexiones keep-alive contra Meta)
    try:
        await cerrar_cliente_http()
        print("[SISTEMA]: Conexiones HTTP cerradas.")
    except Exception as e:
        print(f"[SISTEMA]: fallo al cerrar el cliente HTTP -> {e}")


# 1. Inicializamos FastAPI con metadatos de produccion
app = FastAPI(
    title="Iron Progress V9 - Coach Predictivo",
    description="Motor de IA local para planificación proactiva vía WhatsApp",
    version=VERSION,
    lifespan=lifespan,
)

# 2. Configuracion CORS (Cross-Origin Resource Sharing)
# allow_credentials=True junto a allow_origins=["*"] es una combinación que el propio
# estándar prohíbe (los navegadores la rechazan). Esta API la consume Meta por
# webhook, no un navegador con cookies: no hacen falta credenciales cruzadas.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Conectamos el router de WhatsApp
app.include_router(whatsapp_router, prefix="/api/v1")


# 4. Endpoint de Salud (Health Check)
@app.get("/", tags=["Sistema"])
def health_check():
    return {
        "estado": "operativo",
        "motor": "Iron Progress V9 (Lifespan + APScheduler activos)",
        "version": VERSION,
        "entorno": settings.ENTORNO,
        "config_incompleta": settings.faltantes(),
        "firma_webhook": "activa" if settings.APP_SECRET else "desactivada",
    }


if __name__ == "__main__":
    # IMPORTANTE: reload=True levanta DOS procesos en FastAPI, el APScheduler
    # corre doble y el readiness sale duplicado a cada atleta. Por eso ahora
    # depende del entorno: solo recarga si ENTORNO != "prod" (default: dev).
    recargar = not settings.es_produccion
    print(f"[SISTEMA]: Entorno '{settings.ENTORNO}' -> reload={recargar}")

    opciones = dict(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        log_level="info",
    )
    if recargar:
        opciones["reload"] = True
        opciones["reload_excludes"] = ["*.sqlite3", "*.json", "*.db",
                                       "data", "resources", "tmp"]
    else:
        # workers=1 es OBLIGATORIO: el estado vive en memoria (historial, videos
        # pendientes, semáforo del LLM) y el scheduler mandaría un readiness por
        # worker. Escalar aquí significa duplicar mensajes al atleta.
        opciones["workers"] = 1

    uvicorn.run("main:app", **opciones)
