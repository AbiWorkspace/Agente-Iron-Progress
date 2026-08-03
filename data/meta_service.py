import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.config import settings

# ==============================================================================
# SESIÓN HTTP REUTILIZABLE
# ==============================================================================
# Antes cada mensaje abría una conexión TLS nueva contra graph.facebook.com:
# handshake completo (~150-300 ms) por cada respuesta del coach. Con una sesión
# única la conexión queda viva (keep-alive) y el segundo mensaje en adelante
# sale en el tiempo de ida y vuelta puro.
#
# El Retry cubre lo que de verdad es transitorio (429 y 5xx) con backoff
# exponencial; los 4xx de negocio (401 token muerto, 403 fuera de ventana)
# NO se reintentan porque reintentarlos no arregla nada y sí gasta segundos.
_REINTENTOS = Retry(
    total=2,
    backoff_factor=0.5,                    # 0.5 s, 1 s
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["POST", "GET"]),
    raise_on_status=False,
)

_sesion = requests.Session()
_adaptador = HTTPAdapter(max_retries=_REINTENTOS, pool_connections=8, pool_maxsize=16)
_sesion.mount("https://", _adaptador)
_sesion.mount("http://", _adaptador)

# Timeout (conexión, lectura). Sin esto un cuelgue de Meta bloquea el hilo para
# siempre: en producción eso es un worker perdido por cada mensaje colgado.
TIMEOUT_HTTP = (5.0, 20.0)

# Límite de seguridad (Meta permite 4096; usamos 4000 para dejar margen a los
# emojis y saltos de línea).
LIMITE_CARACTERES = 4000

# Pausa anti rate-limit ENTRE fragmentos. Antes se dormía también después del
# último fragmento: un segundo tirado a la basura en cada mensaje largo.
PAUSA_ENTRE_FRAGMENTOS = 1.0


def _partir_texto(texto: str) -> list[str]:
    """Divide respetando saltos de línea cuando se puede, para no cortar una
    palabra (ni un emoji multi-byte) a la mitad."""
    if len(texto) <= LIMITE_CARACTERES:
        return [texto]

    fragmentos, actual = [], ""
    for linea in texto.splitlines(keepends=True):
        if len(actual) + len(linea) > LIMITE_CARACTERES:
            if actual:
                fragmentos.append(actual)
                actual = ""
            # Una sola línea gigante: se parte por longitud, sin más remedio.
            while len(linea) > LIMITE_CARACTERES:
                fragmentos.append(linea[:LIMITE_CARACTERES])
                linea = linea[LIMITE_CARACTERES:]
        actual += linea
    if actual:
        fragmentos.append(actual)
    return fragmentos or [texto[:LIMITE_CARACTERES]]


def enviar_mensaje_whatsapp(telefono_destino: str, texto: str) -> bool:
    """SÍNCRONA a propósito (requests + pausas anti rate-limit). Desde código async
    hay que invocarla con `await asyncio.to_thread(...)`, nunca directamente.

    Devuelve True si TODOS los fragmentos se entregaron a Meta.
    """
    if not texto or not str(texto).strip():
        print("[ADVERTENCIA] Se intentó enviar un mensaje vacío; se omite.")
        return False
    if not settings.ACCESS_TOKEN or not settings.PHONE_NUMBER_ID:
        print("[ERROR CONFIG] Falta WHATSAPP_ACCESS_TOKEN o PHONE_NUMBER_ID en el .env; "
              "no se puede enviar nada.")
        return False

    url = f"{settings.GRAPH_URL}/{settings.PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    fragmentos = _partir_texto(str(texto))
    if len(fragmentos) > 1:
        print(f"[SISTEMA] Alerta: Mensaje muy largo ({len(texto)} caracteres). "
              f"Dividiendo en {len(fragmentos)} partes...")

    total = len(fragmentos)
    todo_ok = True

    for indice, fragmento in enumerate(fragmentos):
        data = {
            "messaging_product": "whatsapp",
            "to": telefono_destino,
            "type": "text",
            "text": {"body": fragmento},
        }

        try:
            response = _sesion.post(url, headers=headers, json=data, timeout=TIMEOUT_HTTP)
        except requests.RequestException as e:
            print(f"[ERROR RED] Parte {indice + 1}/{total} no salió: {type(e).__name__}: {e}")
            todo_ok = False
            continue

        codigo = response.status_code

        if codigo == 200:
            try:
                respuesta_json = response.json()
            except ValueError:
                respuesta_json = {}
            if "messages" in respuesta_json:
                wamid = respuesta_json["messages"][0].get("id", "?")
                print(f"[EXITO] Parte {indice + 1}/{total} entregada a Meta. ID: {wamid}")
            else:
                print(f"[ADVERTENCIA] Meta dio 200 OK pero no hay ID: {response.text[:300]}")
        else:
            todo_ok = False
            diagnosticos = {
                401: "Tu ACCESS_TOKEN expiro o es invalido.",
                403: "Meta bloqueo la accion (Cuenta restringida o fuera de ventana de 24h).",
                400: "Meta no entiende el formato. Numero invalido o JSON mal estructurado.",
                429: "Rate Limit alcanzado. Estas enviando demasiados mensajes por segundo.",
            }
            motivo = diagnosticos.get(codigo, f"Fallo en el envio. Codigo {codigo}")
            print(f"[ERROR {codigo}] {motivo}")
            print(f"Detalle exacto de Meta: {response.text[:500]}")

        # Respiro SOLO entre fragmentos: tras el último no hay nada que esperar.
        if indice < total - 1:
            time.sleep(PAUSA_ENTRE_FRAGMENTOS)

    return todo_ok
