import uvicorn
import asyncio
from contextlib import asynccontextmanager # <--- Nuevo
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Importaciones de tu arquitectura
from api.rutas_whatsapp import router as whatsapp_router
from api.rutas_whatsapp import limpiar_cache_videos

from services.proactive_coach import iniciar_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # [STARTUP]: Todo lo que pongas antes del yield se ejecuta al encender
    print("[SISTEMA]: Inicializando motores de Iron Progress...")

    # 1. Tarea en segundo plano original (Limpieza de caché)
    tarea_limpieza = asyncio.create_task(limpiar_cache_videos())

    # 2. NUEVO: Arrancando el cerebro proactivo (Cron Jobs)
    print("[SISTEMA]: Arrancando motor de proactividad (Cron Jobs)...")
    scheduler = iniciar_scheduler()

    yield  # Aquí es donde la aplicación "vive" y atiende peticiones

    # [SHUTDOWN]: Todo lo que pongas después del yield se ejecuta al apagar
    print("[SISTEMA]: Limpiando recursos y apagando orquestador...")

    # Apagamos el motor de proactividad de forma segura
    if scheduler:
        scheduler.shutdown()
        print("[SISTEMA]: Motor de proactividad apagado correctamente.")

    # Apagamos la tarea de limpieza de videos
    tarea_limpieza.cancel()
    try:
        await tarea_limpieza
    except asyncio.CancelledError:
        print("[SISTEMA]: Tarea de limpieza finalizada correctamente.")


# 1. Inicializamos FastAPI con metadatos de produccion
app = FastAPI(
    title="Agente Entrenador Multimodal VBT",
    description="Motor de IA local para planificación de Powerlifting vía WhatsApp",
    version="1.2.0",
    lifespan=lifespan # <--- Registramos el manejador aquí
)

# 2. Configuracion CORS (Cross-Origin Resource Sharing)
# Vital para que servidores externos (como Meta/Ngrok) puedan hacer peticiones a tu API local
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Conectamos el router de WhatsApp
# Le asignamos un prefijo para mantener la arquitectura limpia
app.include_router(whatsapp_router, prefix="/api/v1")



# 4. Endpoint de Salud (Health Check)
# Los balanceadores de carga y herramientas de monitoreo usan esto para saber si tu PC sigue viva
@app.get("/", tags=["Sistema"])
def health_check():
    # Podemos incluso ver el estado del event loop aquí
    return {
        "estado": "operativo",
        "motor": "Lifespan Context Manager activo",
        "versión": "1.1.0"
    }

if __name__ == "__main__":
    # host="0.0.0.0" permite que la app sea accesible desde tu red local y tuneles como Ngrok
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_excludes=["*.sqlite3", "*.json", "data", "resources"]
    )