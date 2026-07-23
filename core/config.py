import os
from dotenv import load_dotenv

# Cargamos las variables del .env
load_dotenv()

class Settings:
    """Clase para centralizar todas las variables de entorno."""
    VERIFY_TOKEN: str = os.getenv("VERIFY_TOKEN")
    ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN")
    PHONE_NUMBER_ID: str = os.getenv("PHONE_NUMBER_ID")  # Agrega esta línea
    # Versión única del Graph API: antes había v18 en meta_service y v21 en proactividad.
    GRAPH_VERSION: str = os.getenv("GRAPH_VERSION", "v21.0")
    GRAPH_URL: str = f"https://graph.facebook.com/{os.getenv('GRAPH_VERSION', 'v21.0')}"
    # Cuando agreguemos Ollama o la Base de Datos, pondremos sus configuraciones aquí.

# Instanciamos la clase para poder importarla desde cualquier otro archivo
settings = Settings()