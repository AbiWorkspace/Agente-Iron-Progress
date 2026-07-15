import requests
import time
from core.config import settings
from pathlib import Path


def enviar_mensaje_whatsapp(telefono_destino: str, texto: str):
    url = f"https://graph.facebook.com/v18.0/{settings.PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {settings.ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    # Límite de seguridad (Meta permite 4096, usamos 4000 para dejar margen a los emojis y saltos)
    LIMITE_CARACTERES = 4000

    # Si el texto es corto, lo metemos en una lista de 1 solo elemento
    if len(texto) <= LIMITE_CARACTERES:
        fragmentos = [texto]
    else:
        # Si el texto es gigante, lo partimos en pedazos de 4000 caracteres
        fragmentos = [texto[i:i + LIMITE_CARACTERES] for i in range(0, len(texto), LIMITE_CARACTERES)]
        print(
            f"[SISTEMA] Alerta: Mensaje muy largo ({len(texto)} caracteres). Dividiendo en {len(fragmentos)} partes...")

    # Iteramos y enviamos cada pedazo secuencialmente
    for indice, fragmento in enumerate(fragmentos):
        data = {
            "messaging_product": "whatsapp",
            "to": telefono_destino,
            "type": "text",
            "text": {"body": fragmento},
        }

        response = requests.post(url, headers=headers, json=data)

        # 1. MANEJO DE ÉXITO
        if response.status_code == 200:
            respuesta_json = response.json()
            if "messages" in respuesta_json:
                wamid = respuesta_json["messages"][0]["id"]
                print(f"[EXITO] Parte {indice + 1}/{len(fragmentos)} entregada a Meta. ID: {wamid}")
            else:
                print(f"[ADVERTENCIA] Meta dio 200 OK pero no hay ID: {response.text}")

        # 2. MANEJO DE ERRORES
        elif response.status_code == 401:
            print("[ERROR 401] Tu ACCESS_TOKEN expiro o es invalido.")
            print(f"Detalle exacto de Meta: {response.text}")

        elif response.status_code == 403:
            print("[ERROR 403] Meta bloqueo la accion (Cuenta restringida o fuera de ventana de 24h).")
            print(f"Detalle exacto de Meta: {response.text}")

        elif response.status_code == 400:
            print("[ERROR 400] Meta no entiende el formato. Numero invalido o JSON mal estructurado.")
            print(f"Detalle exacto de Meta: {response.text}")

        elif response.status_code == 429:
            print("[ERROR 429] Rate Limit alcanzado. Estas enviando demasiados mensajes por segundo.")
            print(f"Detalle exacto de Meta: {response.text}")

        else:
            print(f"[ERROR CRITICO] Fallo en el envio. Codigo {response.status_code}")
            print(f"Detalle exacto de Meta: {response.text}")

        # Pausa de 1 segundo entre mensajes para no saturar la API de Meta (Rate Limit 429)
        if len(fragmentos) > 1:
            time.sleep(1)