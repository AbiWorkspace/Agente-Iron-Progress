import re
import ollama as ol
import json
from pathlib import Path

# NOTA v3: se eliminaron imports sin uso (pandas, seaborn, mplcursors, requests).
# Si algo más de este archivo los usaba, restáuralos.

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
        self.modelo = "Qwen3.8:agent"
        self.prompt_sistema = self._cargar_prompt_sistema()

    # ------------------------------------------------------------------ carga
    def _cargar_prompt_sistema(self) -> str:
        ruta_script = Path(__file__).resolve()
        ruta_raiz = ruta_script.parent.parent.parent
        ruta_prompt = ruta_raiz / "data" / "prompt_lista_ejercicios.json"
        try:
            with open(ruta_prompt, "r", encoding="utf-8") as archivo:
                datos_prompt = json.load(archivo)
                return datos_prompt.get("system_prompt", datos_prompt.get("promt", ""))
        except Exception as e:
            print(f"[ADVERTENCIA]: No se pudo cargar el prompt. Detalle: {e}")
            return "Extrae las preferencias en formato LISTA_BLANCA: []."

    def _cargar_base_de_datos(self) -> dict:
        ruta_script = Path(__file__).resolve()
        ruta_raiz = ruta_script.parent.parent.parent
        ruta_db = ruta_raiz / "resources" / "base_de_datos_ejercicios.json"
        try:
            with open(ruta_db, "r", encoding="utf-8") as archivo:
                datos_crudos = json.load(archivo)
                lista_plana = []
                for categoria, lista_ejercicios in datos_crudos.items():
                    if isinstance(lista_ejercicios, list):
                        lista_plana.extend(lista_ejercicios)
                print(f"[MOTOR]: Catálogo cargado: {len(lista_plana)} ejercicios.")
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
                    # --- ENRUTAMIENTO ANATÓMICO Y COMPLEMENTARIOS ---
                    if "vuelos laterales" in nombre:
                        db_dividida["Torso_Anterior"].append(ej)
                    elif "abductor" in nombre or "aductor" in nombre:
                        db_dividida["Pierna_Anterior"].append(ej)
                    elif "tobillo" in nombre or "gemelo" in nombre:
                        db_dividida["Pierna_Posterior"].append(ej)
                    elif "crunch" in nombre or "abdomen" in agonistas:
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
            # v3 FIX: antes retornaba un STRING por copy-paste, lo que rompía
            # el .values() del caller. Ahora retorna dict vacío.
            print(f"[ADVERTENCIA]: No se pudo cargar la base de ejercicios. Detalle: {e}")
            return {}

    # ------------------------------------------------------- criterio por meta
    @staticmethod
    def _normalizar_meta(texto) -> str:
        """Convierte la meta (o el contexto libre) en uno de los 4 perfiles."""
        t = str(texto or "").lower()
        if "culturis" in t:
            return "culturismo"
        if "powerbuild" in t:
            return "powerbuilding"
        if "powerlift" in t or re.search(r"\bfuerza\b", t):
            return "powerlifting"
        if re.search(r"hipertrofia|\bmasa\b|volumen|m[uú]sculo", t):
            return "hipertrofia"
        return "powerbuilding"   # perfil por defecto: el más balanceado

    @staticmethod
    def _clasificar_mecanica(ej: dict) -> str:
        """LIBRE / GUIADO / POLEA a partir del campo 'mecanica' o del nombre."""
        texto = (str(ej.get("mecanica", "")) + " " + str(ej.get("nombre", ""))).lower()
        if re.search(r"polea|cable|cruce", texto):
            return "polea"
        if re.search(r"m[aá]quina|maquina|guiad|smith|prensa|hack", texto):
            return "guiado"
        return "libre"

    # Preferencia de mecánica por perfil (para el auto-rescate determinista)
    _PRIORIDAD_MECANICA = {
        "powerlifting": ["libre", "guiado", "polea"],
        "powerbuilding": ["libre", "guiado", "polea"],
        "hipertrofia": ["guiado", "polea", "libre"],
        "culturismo": ["guiado", "polea", "libre"],
    }

    def _ordenar_por_perfil(self, ejercicios: list, perfil: str) -> list:
        orden = self._PRIORIDAD_MECANICA.get(perfil, ["libre", "guiado", "polea"])
        return sorted(ejercicios,
                      key=lambda e: orden.index(self._clasificar_mecanica(e))
                      if self._clasificar_mecanica(e) in orden else 99)

    # ------------------------------------------------------------- selección
    def extraer_listas_por_llm(self, contexto_usuario, rango_compuestos,
                               rango_aislamiento, meta: str = "") -> list:
        """
        v3 — La META ahora es variable de primera clase del motor de reglas.
        Pásale datos['meta'] desde el orquestador de generación; si no llega,
        se deduce del contexto libre como respaldo.
        """
        db_dividida = self._cargar_base_de_datos()
        if not db_dividida:
            return []

        perfil = self._normalizar_meta(meta or contexto_usuario)
        print(f"[MOTOR]: PERFIL DE SELECCIÓN = '{perfil}' "
              f"(meta recibida: {meta!r}; deducida del contexto: {not bool(meta)})")

        lista_blanca_maestra = []
        # 1. BYPASS: los complementarios entran siempre a la lista blanca
        for ejercicios in db_dividida.values():
            for ej in ejercicios:
                if "complementario" in ej.get("tipo", "").lower():
                    lista_blanca_maestra.append(ej.get("nombre"))

        zonas = ["Torso_Anterior", "Torso_Posterior", "Pierna_Anterior", "Pierna_Posterior"]
        for zona in zonas:
            ejercicios_de_la_zona = db_dividida.get(zona, [])
            if not ejercicios_de_la_zona:
                continue
            print(f"\n[MOTOR]: Analizando zona: {zona} (perfil {perfil})")

            prompt_instrucciones = self.prompt_sistema.format(
                rango_compuesto=rango_compuestos,
                rango_aislado=rango_aislamiento,
                zona_actual=zona.replace("_", " "),
                meta=perfil
            )

            # v3: el catálogo ahora expone la MECÁNICA — sin esto, el modelo no
            # podía distinguir máquina/libre/polea y elegía igual para toda meta.
            catalogo_texto = "\n".join(
                f"- {ej.get('nombre', 'N/A')} | Tipo: {ej.get('tipo', 'N/A')} "
                f"| Mecánica: {ej.get('mecanica', '') or self._clasificar_mecanica(ej)}"
                for ej in ejercicios_de_la_zona)

            try:
                respuesta_ia = ol.chat(
                    model=self.modelo,
                    messages=[
                        {"role": "system", "content": prompt_instrucciones},
                        {"role": "user", "content": f"Contexto: {contexto_usuario}\nCatalogo:\n{catalogo_texto}"}
                    ],
                    tools=[tool_ejercicios],
                    # v3 FIX CRÍTICO: qwen3.6 PIENSA POR DEFECTO. Sin think=False
                    # quemaba los 800 tokens razonando, el tool call salía vacío
                    # y el auto-rescate (primeros N de la DB, idénticos siempre)
                    # elegía por ti IGNORANDO la meta. Esa era la causa principal
                    # de "mismos ejercicios para cualquier meta".
                    think=False,
                    # num_ctx DEBE coincidir con OPCIONES_QWEN_32B de
                    # services/llm_service.py: es el mismo modelo cargado en la misma
                    # GPU, y si los dos valores difieren Ollama recarga el modelo
                    # entero cada vez que se alternan (thrashing de recargas, ~40 s).
                    options={"num_ctx": 32768, "num_predict": 4096, "temperature": 0}
                )
                ejercicios_zona = []
                msg = respuesta_ia.get('message', {})

                # A) Extracción por Tool Call
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

                # C) AUTO-RESCATE — v3: ahora respeta el PERFIL de la meta en vez
                # de tomar a ciegas los primeros N de la base de datos.
                if not ejercicios_zona:
                    print(f"[ALERTA] La IA devolvió 0 ejercicios en {zona}. "
                          f"Auto-rescate con prioridad de mecánica del perfil '{perfil}'...")
                    compuestos = self._ordenar_por_perfil(
                        [e for e in ejercicios_de_la_zona
                         if 'multiarticular' in e.get('tipo', '').lower()], perfil)
                    aislados = self._ordenar_por_perfil(
                        [e for e in ejercicios_de_la_zona
                         if ('monoarticular' in e.get('tipo', '').lower()
                             or 'analitico' in e.get('tipo', '').lower())], perfil)
                    ejercicios_zona.extend(e['nombre'] for e in compuestos[:int(rango_compuestos)])
                    ejercicios_zona.extend(e['nombre'] for e in aislados[:int(rango_aislamiento)])

                # D) Consolidar sin duplicados
                agregados = 0
                for e in ejercicios_zona:
                    nombre_limpio = str(e).split('|')[0].strip()
                    if nombre_limpio and nombre_limpio not in lista_blanca_maestra:
                        lista_blanca_maestra.append(nombre_limpio)
                        agregados += 1
                print(f"[OK] Zona {zona} procesada: {agregados} ejercicios asignados.")

            except Exception as e:
                print(f"[ERROR LLM en {zona}]: {e}")

        return lista_blanca_maestra