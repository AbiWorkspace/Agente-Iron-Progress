import pandas as pd
import seaborn as sns
import mplcursors
import requests
import re
import ollama as ol
import json as json
from pathlib import Path

tool_ejercicios = {
    'type': 'function',
    'function': {
        'name': 'filtrar_ejercicios',
        'description': 'Filtra y selecciona los ejercicios ideales.',
        'parameters': {
            'type': 'object',
            'properties': {
                'ejercicios': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Lista de nombres de ejercicios.'
                }
            },
            'required': ['ejercicios']
        }
    }
}

class ServicioEntrenamiento:
    def __init__(self):
        self.modelo = "qwen3:32b"
        self.prompt_sistema = self._cargar_prompt_sistema()

    def _cargar_prompt_sistema(self) -> str:
        from pathlib import Path
        import json

        ruta_script = Path(__file__).resolve()
        # Subimos 3 niveles: archivo -> resources -> scripts -> Mi proyecto (raíz)
        ruta_raiz = ruta_script.parent.parent.parent
        ruta_prompt = ruta_raiz / "data" / "prompt_lista_ejercicios.json"

        try:
            with open(ruta_prompt, "r", encoding="utf-8") as archivo:
                datos_prompt = json.load(archivo)

                # ATENCIÓN AQUÍ: Asegúrate de usar la llave correcta.
                # En tus mensajes anteriores, vi que tu JSON decía "promt" sin 'p'.
                # Usa la llave exacta que tengas escrita en ese archivo JSON.
                return datos_prompt.get("system_prompt", datos_prompt.get("promt", ""))

        except Exception as e:
            print(f"[ADVERTENCIA]: No se pudo cargar el prompt. Detalle: {e}")
            return "Extrae las preferencias en formato LISTA_BLANCA: []."

    def _cargar_base_de_datos(self) -> dict[str, list[dict]]:
        ruta_script = Path(__file__).resolve()
        # Lo mismo aquí, subimos 3 niveles a la raíz
        ruta_raiz = ruta_script.parent.parent.parent
        ruta_db = ruta_raiz / "resources" / "base_de_datos_ejercicios.json"

        try:
            with open(ruta_db, "r", encoding="utf-8") as archivo:
                datos_crudos = json.load(archivo)
                print(datos_crudos)
                lista_plana = []
                for categoria, lista_ejercicios in datos_crudos.items():
                    if isinstance(lista_ejercicios, list):
                        lista_plana.extend(lista_ejercicios)

                db_dividida = {
                    "Torso_Anterior": [],
                    "Torso_Posterior": [],
                    "Pierna_Anterior": [],
                    "Pierna_Posterior": []
                }

                for ej in lista_plana:
                    nombre = ej.get("nombre", "").lower()
                    agonistas = " ".join(ej.get("agonistas", [])).lower()
                    patron = ej.get("patron_movimiento", "").lower()
                    mecanica = ej.get("mecanica", "").lower()
                    # --- ENRUTAMIENTO ANATÓMICO Y COMPLEMENTARIOS ---
                    # Nos aseguramos de que los ejercicios del RADAR caigan en la zona correcta
                    if "vuelos laterales" in nombre:
                        db_dividida["Torso_Anterior"].append(ej)
                    elif "abductor" in nombre or "aductor" in nombre:
                        db_dividida["Pierna_Anterior"].append(ej)
                    elif "tobillo" in nombre or "gemelo" in nombre:
                        db_dividida["Pierna_Posterior"].append(ej)
                    elif "crunch" in nombre or "abdomen" in agonistas:
                        # Mandamos el abdomen a Torso Anterior para que el LLM lo encuentre
                        db_dividida["Torso_Anterior"].append(ej)

                    # --- El resto del filtrado normal ---
                    elif "pecho" in agonistas or ("empuje" in patron and "cuadriceps" not in agonistas):
                        db_dividida["Torso_Anterior"].append(ej)
                    elif "dorsal" in agonistas or "traccion" in patron and "isquiosurales" not in agonistas:
                        db_dividida["Torso_Posterior"].append(ej)
                    elif "cuadriceps" in agonistas or "rodilla" in patron:
                        db_dividida["Pierna_Anterior"].append(ej)
                    elif "isquio" in agonistas or "gluteo" in agonistas or "cadera" in patron:
                        db_dividida["Pierna_Posterior"].append(ej)
                    else:
                        db_dividida["Torso_Anterior"].append(ej)

                return db_dividida


        except Exception as e:

            print(f"[ADVERTENCIA]: No se pudo cargar el prompt. Detalle: {e}")

            return "Extrae las preferencias en formato LISTA_BLANCA: []."

    def extraer_listas_por_llm(self, contexto_usuario, rango_compuestos, rango_aislamiento) -> list[str]:
        db_dividida = self._cargar_base_de_datos()
        if not db_dividida:
            return []

        lista_blanca_maestra = []

        # 🔥 1. EL BYPASS MÁGICO: Inyectar TODOS los complementarios a la lista blanca automáticamente
        # Así el motor principal (Entrenamiento.py) jamás los borra, aunque la IA sea tonta y no los elija.
        for ejercicios in db_dividida.values():
            for ej in ejercicios:
                if "complementario" in ej.get("tipo", "").lower():
                    lista_blanca_maestra.append(ej.get("nombre"))

        zonas = ["Torso_Anterior", "Torso_Posterior", "Pierna_Anterior", "Pierna_Posterior"]

        for zona in zonas:
            ejercicios_de_la_zona = db_dividida.get(zona, [])
            if not ejercicios_de_la_zona:
                continue

            print(f"\n[MOTOR]: Analizando zona: {zona}")

            prompt_instrucciones = self.prompt_sistema.format(
                rango_compuesto=rango_compuestos,
                rango_aislado=rango_aislamiento,
                zona_actual=zona.replace("_", " ")
            )
            # Simplificamos el catálogo para no confundir a la IA
            catalogo_texto = "\n".join(
                [f"- {ej.get('nombre', 'N/A')} | Tipo: {ej.get('tipo', 'N/A')}" for ej in ejercicios_de_la_zona])

            try:
                respuesta_ia = ol.chat(
                    model=self.modelo,
                    messages=[
                        {"role": "system", "content": prompt_instrucciones},
                        {"role": "user", "content": f"Contexto: {contexto_usuario}\nCatalogo:\n{catalogo_texto}"}
                    ],
                    tools=[tool_ejercicios],
                    options={"num_ctx": 16384, "num_predict": 800, "temperature": 0}
                )

                ejercicios_zona = []
                msg = respuesta_ia.get('message', {})

                # A) Intento de extracción por Tool Call
                if msg.get('tool_calls'):
                    for tool in msg['tool_calls']:
                        if tool['function']['name'] == 'filtrar_ejercicios':
                            args = tool['function']['arguments']
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except json.JSONDecodeError:
                                    args = {}
                            if isinstance(args, dict):
                                ejercicios_zona = args.get("ejercicios", [])

                # B) Respaldo: Regex si la IA falló el Tool Call
                if not ejercicios_zona:
                    texto = msg.get('content', '')
                    match = re.search(r'\[(.*?)\]', texto, re.DOTALL)
                    if match:
                        ejercicios_zona = re.findall(r'["\']([^"\']+)["\']', match.group(1))

                # 🔥 2. AUTO-RESCATE PYTHON: Si la IA devuelve 0 ejercicios, Python salva la rutina
                if not ejercicios_zona:
                    print(f"[ALERTA] La IA devolvió 0 ejercicios en {zona}. Activando auto-rescate de emergencia...")
                    compuestos = [e['nombre'] for e in ejercicios_de_la_zona if
                                  'multiarticular' in e.get('tipo', '').lower()]
                    aislados = [e['nombre'] for e in ejercicios_de_la_zona if (
                                'monoarticular' in e.get('tipo', '').lower() or 'analitico' in e.get('tipo',
                                                                                                     '').lower())]

                    # Tomamos al menos 2 compuestos y 2 aislados de la base de datos para no dejar el día vacío
                    ejercicios_zona.extend(compuestos[:int(rango_compuestos)])
                    ejercicios_zona.extend(aislados[:int(rango_aislamiento)])

                # 3. Consolidar en Lista Maestra sin duplicados
                agregados = 0
                for e in ejercicios_zona:
                    nombre_limpio = e.split('|')[0].strip()  # Limpia basura extra
                    if nombre_limpio and nombre_limpio not in lista_blanca_maestra:
                        lista_blanca_maestra.append(nombre_limpio)
                        agregados += 1

                print(f"[OK] Zona {zona} procesada: {agregados} ejercicios asignados.")

            except Exception as e:
                print(f"[ERROR LLM en {zona}]: {e}")

        return lista_blanca_maestra