import uvicorn
import asyncio
import json
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Importaciones de tu arquitectura
from api.rutas_whatsapp import router as whatsapp_router
from api.rutas_whatsapp import limpiar_cache_videos

# V9: Importamos el motor proactivo desde el nuevo archivo
from services.proactividad import iniciar_scheduler

def mis_usuarios_activos():
    """Lee el estado de los atletas y filtra a quién enviarle el Readiness a las 6:30 AM."""
    ruta_estado = os.path.join("data", "estado_usuarios.json")
    activos = []
    try:
        with open(ruta_estado, "r", encoding="utf-8") as f:
            estados = json.load(f)
        for numero, estado in estados.items():
            # Filtros V9: No enviamos a atletas pausados o en onboarding
            if estado.get("plan_pausado") == 1:
                continue
            if estado.get("onboarding_activo"):
                continue
            # Agregamos al atleta con un nombre por defecto (el bot lo usará en la plantilla)
            activos.append((numero, "campeón"))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return activos

@asynccontextmanager
async def lifespan(app: FastAPI):
    # [STARTUP]: Todo lo que pongas antes del yield se ejecuta al encender
    print("[SISTEMA]: Inicializando motores de Iron Progress V9...")

    # 1. Tarea en segundo plano original (Limpieza de caché)
    tarea_limpieza = asyncio.create_task(limpiar_cache_videos())

    # 2. NUEVO: Arrancando el cerebro proactivo (Cron Jobs a las 6:30 AM)
    # scheduler se inicializa antes del try: si iniciar_scheduler revienta,
    # el shutdown de abajo daría UnboundLocalError y la app no cerraría limpio.
    scheduler = None
    try:
        print("[SISTEMA]: Arrancando motor de proactividad matutino...")
        scheduler = iniciar_scheduler(mis_usuarios_activos, hora=6, minuto=30)
    except Exception as e:
        print(f"[SISTEMA]: ERROR al arrancar la proactividad -> {e}")

    yield  # Aquí es donde la aplicación "vive" y atiende peticiones

    # [SHUTDOWN]: Todo lo que pongas después del yield se ejecuta al apagar
    print("[SISTEMA]: Limpiando recursos y apagando orquestador...")

    # Apagamos el motor de proactividad de forma segura
    if scheduler:
        scheduler.shutdown(wait=False)
        print("[SISTEMA]: Motor de proactividad apagado correctamente.")

    # Apagamos la tarea de limpieza de videos
    tarea_limpieza.cancel()
    try:
        await tarea_limpieza
    except asyncio.CancelledError:
        print("[SISTEMA]: Tarea de limpieza finalizada correctamente.")


# 1. Inicializamos FastAPI con metadatos de produccion
app = FastAPI(
    title="Iron Progress V9 - Coach Predictivo",
    description="Motor de IA local para planificación proactiva vía WhatsApp",
    version="9.0.0",
    lifespan=lifespan
)

# 2. Configuracion CORS (Cross-Origin Resource Sharing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
        "versión": "9.0.0"
    }

if __name__ == "__main__":
    # IMPORTANTE: reload=True levanta DOS procesos en FastAPI, el APScheduler
    # corre doble y el readiness sale duplicado a cada atleta. Por eso ahora
    # depende del entorno: solo recarga si ENTORNO != "prod" (default: dev).
    entorno = os.getenv("ENTORNO", "dev").lower()
    print(f"[SISTEMA]: Entorno '{entorno}' -> reload={entorno != 'prod'}")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=(entorno != "prod"),
        reload_excludes=["*.sqlite3", "*.json", "data", "resources", "tmp"]
    )