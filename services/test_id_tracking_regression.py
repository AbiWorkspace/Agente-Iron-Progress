#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de Regresión A/B para ID Tracking VBT
=========================================
Compara métricas de tracking entre la versión baseline y la versión mejorada
para todos los videos de prueba.

Uso:
    python test_id_tracking_regression.py [--baseline JSON] [--improved JSON]
"""
import argparse
import json
import os
import sys
from pathlib import Path


def comparar_reportes(ruta_baseline: str, ruta_improved: str) -> bool:
    if not os.path.exists(ruta_baseline):
        print(f"❌ No existe archivo baseline: {ruta_baseline}")
        return False
    if not os.path.exists(ruta_improved):
        print(f"❌ No existe archivo improved: {ruta_improved}")
        return False

    with open(ruta_baseline) as f:
        b_data = json.load(f)
    with open(ruta_improved) as f:
        i_data = json.load(f)

    b_summary = b_data["summary"]
    i_summary = i_data["summary"]

    print("\n" + "=" * 70)
    print("  COMPARATIVA GLOBAL A/B (BASELINE vs IMPROVED)")
    print("=" * 70)
    print(f"  {'Métrica':<25} | {'Baseline':<15} | {'Improved':<15} | {'Delta':<10}")
    print("-" * 70)

    delta_switches = i_summary["total_id_switches"] - b_summary["total_id_switches"]
    delta_cov = i_summary["avg_coverage_pct"] - b_summary["avg_coverage_pct"]
    delta_stab = i_summary["avg_stability"] - b_summary["avg_stability"]
    delta_jumps = i_summary["total_pos_jumps"] - b_summary["total_pos_jumps"]

    def fmt_delta(val, invert=False):
        if val == 0:
            return "  0 (igual)"
        es_mejora = (val < 0) if invert else (val > 0)
        signo = "+" if val > 0 else ""
        icon = "✅" if es_mejora else "❌"
        return f"{signo}{val:.2f} {icon}"

    print(f"  {'ID Switches':<25} | {b_summary['total_id_switches']:<15} | {i_summary['total_id_switches']:<15} | {fmt_delta(delta_switches, invert=True)}")
    print(f"  {'Cobertura Media (%)':<25} | {b_summary['avg_coverage_pct']:<15.1f} | {i_summary['avg_coverage_pct']:<15.1f} | {fmt_delta(delta_cov)}")
    print(f"  {'Estabilidad Media':<25} | {b_summary['avg_stability']:<15.4f} | {i_summary['avg_stability']:<15.4f} | {fmt_delta(delta_stab)}")
    print(f"  {'Saltos de Posición':<25} | {b_summary['total_pos_jumps']:<15} | {i_summary['total_pos_jumps']:<15} | {fmt_delta(delta_jumps, invert=True)}")
    print("=" * 70)

    # Detalle por video
    b_vids = {os.path.basename(v["video"]): v for v in b_data.get("per_video", [])}
    i_vids = {os.path.basename(v["video"]): v for v in i_data.get("per_video", [])}

    print("\n  DETALLE POR VIDEO:")
    print(f"  {'Video':<40} | {'Switches (B->I)':<16} | {'Cov (B->I)':<16} | {'Estado'}")
    print("-" * 85)

    regresion_detectada = False
    for vname, b_v in sorted(b_vids.items()):
        i_v = i_vids.get(vname)
        if not i_v:
            continue

        sw_b = b_v["id_switches"]
        sw_i = i_v["id_switches"]
        cov_b = b_v["coverage_pct"]
        cov_i = i_v["coverage_pct"]

        # Es regresión si los switches subieron o si la cobertura cayó > 3%
        if sw_i > sw_b or cov_i < (cov_b - 3.0):
            estado = "❌ REGRESIÓN"
            regresion_detectada = True
        elif sw_i < sw_b or cov_i > cov_b:
            estado = "✅ MEJORA"
        else:
            estado = "➖ SIN CAMBIO"

        print(f"  {vname:<40} | {sw_b:>2d} -> {sw_i:>2d}         | {cov_b:>5.1f}% -> {cov_i:>5.1f}% | {estado}")

    print("=" * 85)
    if regresion_detectada:
        print("⚠️ ADVERTENCIA: Se detectaron regresiones en algunos videos.")
        return False
    else:
        print("🎉 ÉXITO: No se detectaron regresiones y el sistema es estable.")
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="tracking_report_baseline.json")
    parser.add_argument("--improved", default="tracking_report_improved.json")
    args = parser.parse_args()

    ok = comparar_reportes(args.baseline, args.improved)
    sys.exit(0 if ok else 1)
