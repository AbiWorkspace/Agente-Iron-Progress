#!/usr/bin/env bash
# ==============================================================================
# Cazador de Colisiones APU vs dGPU — Iron Progress
# Detecta si el Ryzen 7 5700G está secuestrando las cargas de ROCm.
# ==============================================================================
set -euo pipefail

echo "======================================================================"
echo "🎯 RASTREADOR DE TOPOLOGÍA MULTI-GPU (APU vs RX 7900 XTX)"
echo "======================================================================"

# 1. CENSO DE DISPOSITIVOS GRÁFICOS (La pelea por el ID 0)
echo -e "\n[1] MAPA FÍSICO DEL BUS PCI:"
lspci | grep -i vga | awk '{print "  Detectado en bus " $1 ": " $3 " " $4 " " $5 " " $6}'

echo -e "\n[2] CENSO DE AGENTES ROCM (¿Quién es el nodo 0?):"
# ROCm asigna un ID a cada gráfica. Si la integrada es el ID 0, Ollama la usará por defecto.
if command -v rocminfo &>/dev/null; then
    rocminfo | grep -iE 'Agent [0-9]|Name:|Pool Info:' | grep -v 'System' | grep -iE 'gfx|Name' | awk '
    /Name:/ {name=$2}
    /gfx/ {print "  GPU encontrada: " name " (" $1 ")"}
    ' || echo "  ❌ rocminfo falló al listar agentes."
else
    echo "  ❌ rocminfo no disponible."
fi

# 3. AISLAMIENTO DEL ENTORNO (El chaleco salvavidas)
echo -e "\n[3] VARIABLES DE AISLAMIENTO HIP/ROCM:"
echo "  Si tienes dos GPUs, debes obligar a ROCm a ignorar el R7 5700G."
if [ -z "${HIP_VISIBLE_DEVICES:-}" ]; then
    echo "  ❌ HIP_VISIBLE_DEVICES no está configurada. (ROCm ve ambas GPUs)."
else
    echo "  ✅ HIP_VISIBLE_DEVICES = $HIP_VISIBLE_DEVICES"
fi

if [ -z "${ROCR_VISIBLE_DEVICES:-}" ]; then
    echo "  ❌ ROCR_VISIBLE_DEVICES no está configurada."
else
    echo "  ✅ ROCR_VISIBLE_DEVICES = $ROCR_VISIBLE_DEVICES"
fi

# 4. RASTREO DEL DESBORDAMIENTO A RAM (GTT Spill)
echo -e "\n[4] MEMORIA GTT (Graphics Translation Table):"
echo "  La GTT es la porción de tu RAM (16GB) que AMD usa cuando se queda sin VRAM."
echo "  Si este número es alto y tu VRAM está vacía, el 5700G está ejecutando el modelo."
if command -v rocm-smi &>/dev/null; then
    rocm-smi --showmeminfo gtt | grep -i gtt || echo "  No se pudo leer la GTT."
else
    echo "  ❌ rocm-smi no está instalado."
fi

# 5. EVIDENCIA CRIMINAL EN LOS LOGS DE OLLAMA
echo -e "\n[5] ¿A QUIÉN ELIGIÓ OLLAMA EN SU ÚLTIMO ARRANQUE?"
echo "  Revisando qué tarjeta reclamó el servidor interno..."
journalctl -u ollama -n 100 --no-pager | grep -iE 'discovering|amd|gpu|gfx' | tail -n 5 || echo "  No hay logs de descubrimiento de GPU recientes."

echo "======================================================================"
echo "FIN DEL RASTREO TOPOLÓGICO."
