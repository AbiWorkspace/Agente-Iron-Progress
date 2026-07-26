import os
from dotenv import load_dotenv

# Cargamos las variables del .env
load_dotenv()


def _env(clave: str, defecto: str = "") -> str:
    """Lee una variable de entorno y le quita espacios/comillas sobrantes.

    Un `.env` escrito a mano suele traer 'PHONE_NUMBER_ID = 12345' o el valor
    entre comillas. Sin este saneado el token viaja con un espacio delante y
    Meta responde 401 sin explicar por qué.
    """
    valor = os.getenv(clave, defecto)
    if valor is None:
        return ""
    return valor.strip().strip('"').strip("'")


class Settings:
    """Clase para centralizar todas las variables de entorno."""
    VERIFY_TOKEN: str = _env("VERIFY_TOKEN")
    ACCESS_TOKEN: str = _env("WHATSAPP_ACCESS_TOKEN")
    PHONE_NUMBER_ID: str = _env("PHONE_NUMBER_ID")
    # Secreto de la app de Meta: habilita la validación de firma (X-Hub-Signature-256)
    # del webhook. Si no está definido, el webhook sigue funcionando SIN validar.
    APP_SECRET: str = _env("META_APP_SECRET")
    # Versión única del Graph API: antes había v18 escrita a mano en meta_service,
    # audio_service, vbt_service y rutas_whatsapp, y v21 aquí. v18 ya está fuera de
    # soporte, así que esas rutas (descarga de audio y video) tenían fecha de caducidad.
    GRAPH_VERSION: str = _env("GRAPH_VERSION", "v21.0")
    GRAPH_URL: str = f"https://graph.facebook.com/{GRAPH_VERSION}"

    # 'prod' desactiva el reload de uvicorn y baja el log de depuración.
    ENTORNO: str = _env("ENTORNO", "dev").lower()

    @property
    def es_produccion(self) -> bool:
        return self.ENTORNO in ("prod", "produccion", "production")

    def faltantes(self) -> list[str]:
        """Variables imprescindibles que NO están puestas.

        Se consulta al arrancar (main.py) en vez de descubrirlo con un 401 en
        mitad de una conversación real con un atleta.
        """
        obligatorias = {
            "VERIFY_TOKEN": self.VERIFY_TOKEN,
            "WHATSAPP_ACCESS_TOKEN": self.ACCESS_TOKEN,
            "PHONE_NUMBER_ID": self.PHONE_NUMBER_ID,
        }
        return [k for k, v in obligatorias.items() if not v]

    def diagnostico(self) -> str:
        """Resumen legible del estado de la configuración (sin exponer secretos)."""
        faltan = self.faltantes()
        lineas = [
            f"  · Entorno         : {self.ENTORNO}",
            f"  · Graph API       : {self.GRAPH_VERSION}",
            f"  · Phone Number ID : {'OK' if self.PHONE_NUMBER_ID else 'FALTA'}",
            f"  · Access Token    : {'OK' if self.ACCESS_TOKEN else 'FALTA'}",
            f"  · Verify Token    : {'OK' if self.VERIFY_TOKEN else 'FALTA'}",
            f"  · Firma webhook   : {'ACTIVA' if self.APP_SECRET else 'DESACTIVADA (define META_APP_SECRET)'}",
        ]
        if faltan:
            lineas.append(f"  · FALTAN en el .env: {', '.join(faltan)}")
        return "\n".join(lineas)


# Instanciamos la clase para poder importarla desde cualquier otro archivo
settings = Settings()
