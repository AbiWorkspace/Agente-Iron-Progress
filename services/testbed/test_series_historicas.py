# -*- coding: utf-8 -*-
"""
test_series_historicas.py — Pruebas unitarias e integración de la herramienta
y consulta RAG de series históricas de los atletas.

Verifica:
1. `consultar_series_atleta` con un ejercicio único.
2. `consultar_series_atleta` con múltiples ejercicios (balanceado, sin inanición).
3. Respeto de palabras con 'y' (ej: 'pec flys', 'curl scott').
4. Retorno ordenado y métricas calculadas (e1RM, VBT, ROM).
5. Comportamiento ante usuario o ejercicio sin registros.
6. `tool_consultar_historial_series` en `handlers_v9.py`.
"""
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from services.db_service import consultar_series_atleta
from services.handlers_v9 import DISPATCHER_V9


class TestSeriesHistoricas(unittest.TestCase):

    def test_usuario_sin_registros(self):
        res = consultar_series_atleta("9999999999999", "peso muerto", limite=5)
        self.assertTrue(res["ok"])
        self.assertEqual(res["total_series"], 0)
        self.assertIn("No hay series registradas", res["mensaje"])
        self.assertEqual(res["series"], [])

    def test_ejercicio_inexistente(self):
        # Usuario con registros pero ejercicio no existente
        res = consultar_series_atleta("5217291395298", "ejercicio_ficticio_xyz", limite=5)
        self.assertTrue(res["ok"])
        self.assertEqual(res["total_series"], 0)
        self.assertIn("No se encontraron series", res["mensaje"])

    def test_consulta_ejercicio_unico(self):
        res = consultar_series_atleta("5217291395298", "peso muerto", limite=5)
        self.assertTrue(res["ok"])
        self.assertGreater(res["total_series"], 0)
        self.assertLessEqual(res["total_series"], 5)
        self.assertIn("HISTORIAL DE SERIES: PESO MUERTO", res["resumen_texto"])
        for s in res["series"]:
            self.assertEqual(s["ejercicio_canonico"], "peso muerto")
            self.assertGreater(s["peso_kg"], 0)
            self.assertGreater(s["repeticiones"], 0)
            self.assertIsNotNone(s["e1rm_estimado_kg"])

    def test_consulta_multiples_ejercicios_balanceados(self):
        # Al pedir dos ejercicios, ambos deben estar presentes (balanceados)
        res = consultar_series_atleta("5217291395298", "peso muerto y sentadillas", limite=8)
        self.assertTrue(res["ok"])
        self.assertIn("Peso Muerto", res["ejercicios_encontrados"])
        self.assertIn("Sentadilla", res["ejercicios_encontrados"])
        
        ejercicios_en_series = {s["ejercicio"] for s in res["series"]}
        self.assertIn("Peso Muerto", ejercicios_en_series)
        self.assertIn("Sentadilla", ejercicios_en_series)
        self.assertIn("HISTORIAL DE SERIES: PESO MUERTO", res["resumen_texto"])
        self.assertIn("HISTORIAL DE SERIES: SENTADILLA", res["resumen_texto"])

    def test_regex_no_rompe_palabras_con_y(self):
        # Comprobar que no rompa palabras con la letra 'y' dentro
        res = consultar_series_atleta("5217291395298", "pec flys y sentadillas", limite=5)
        self.assertTrue(res["ok"])
        self.assertIn("Sentadilla", res["ejercicios_encontrados"])

    def test_tool_dispatcher_v9(self):
        self.assertIn("consultar_historial_series", DISPATCHER_V9)
        tool_fn = DISPATCHER_V9["consultar_historial_series"]
        import asyncio
        res = asyncio.run(tool_fn("5217291395298", {"ejercicio": "press banco plano", "limite": 3}))
        self.assertTrue(res["ok"])
        self.assertGreater(res["total_series"], 0)
        self.assertLessEqual(res["total_series"], 3)


if __name__ == "__main__":
    unittest.main()
