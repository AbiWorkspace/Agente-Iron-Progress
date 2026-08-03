#!/usr/bin/env bash
# ==============================================================================
# Auditor de Hardware y Aceleración — Iron Progress
# Diagnostica por qué Ollama/PyTorch ignoran la RX 7900 XTX y saturan la RAM.
# ==============================================================================
set -euo pipefail

echo "======================================================================"
echo "🕵️ AUDITORÍA DE ENRUTAMIENTO GPU (ROCm 7.2.1 -> RX 7900 XTX)"
echo "======================================================================"

# 1. PRUEBA DE IDENTIFICACIÓN DE HARDWARE Y DRIVER
echo -e "\n[1] VERIFICACIÓN DEL KERNEL Y MÓDULOS AMDGPU:"
if lsmod | grep -q amdgpu; then
    echo "  ✅ Módulo amdgpu cargado en el kernel."
else
    echo "  ❌ Módulo amdgpu NO cargado (Problema de Secure Boot / MOK)."
fi

echo -n "  Detectando RX 7900 XTX en PCI: "
lspci | grep -iE 'vga|3d|display' | grep -i 'advanced micro' || echo "No encontrada"

# 2. PRUEBA DE PERMISOS DE USUARIO (El error más común del Fallback)
echo -e "\n[2] VERIFICACIÓN DE PERMISOS DE CÓMPUTO (/dev/kfd y /dev/dri):"
# /dev/kfd es el nodo de cómputo de ROCm. Sin acceso a él, el modelo se va a la RAM.
if [ -w /dev/kfd ]; then
    echo "  ✅ El usuario '$USER' tiene permisos de escritura en /dev/kfd."
else
    echo "  ❌ Permiso denegado en /dev/kfd. Falta agregar al usuario al grupo 'render'."
fi

echo -n "  Grupos del usuario actual: "
groups "$USER" | grep -oE 'video|render' | tr '\n' ' ' || echo "❌ Faltan grupos render/video."
echo ""

# Verificar si existe el usuario ollama (típico si está instalado como servicio systemd)
if id "ollama" &>/dev/null; then
    echo -n "  Grupos del servicio Ollama: "
    groups ollama | grep -oE 'video|render' | tr '\n' ' ' || echo "❌ A Ollama le faltan grupos."
    echo ""
else
    echo "  ⚠️ Usuario 'ollama' no existe (ejecutando como usuario local)."
fi

# 3. PRUEBA DEL ENTORNO ROCM (7.2.1)
echo -e "\n[3] VERIFICACIÓN DE LIBRERÍAS ROCm (7.2.1):"
if [ -d "/opt/rocm-7.2.1" ]; then
    echo "  ✅ Directorio /opt/rocm-7.2.1 encontrado."
else
    echo "  ❌ No se encuentra la instalación base de ROCm 7.2.1."
fi

echo -n "  Estatus de rocminfo: "
if command -v rocminfo &>/dev/null; then
    rocminfo | grep -i "gfx1100" >/dev/null && echo "✅ Arquitectura gfx1100 (RDNA3) detectada." || echo "❌ Tarjeta no reconocida por rocminfo."
else
    echo "❌ Comando rocminfo no encontrado en el PATH."
fi

# 4. PRUEBA DE SYSTEMD Y VARIABLES DE ENTORNO OLLAMA
echo -e "\n[4] VERIFICACIÓN DE VARIABLES DE ENTORNO EN OLLAMA:"
if systemctl is-active --quiet ollama; then
    echo "  ✅ Servicio Ollama activo."
    echo "  Variables inyectadas en el demonio:"
    systemctl show ollama | grep -E "Environment=" | grep -i "HSA" || echo "  ❌ No se encontró HSA_OVERRIDE_GFX_VERSION."
else
    echo "  ⚠️ Ollama no se está ejecutando vía systemd."
fi

# 5. PRUEBA DE COLISIÓN DE ENTORNOS PYTHON Y PYTORCH
echo -e "\n[5] VERIFICACIÓN DEL ENTORNO DE DEEP LEARNING (Iron Progress):"
echo "  PATH Actual: ${PATH:0:60}..."
if [[ "$PATH" == *".venv"* ]] && [[ "$PATH" == *"conda"* ]]; then
    echo "  ⚠️ PELIGRO: Tienes Conda y un .venv activos simultáneamente."
    echo "     Esto causa que las librerías C++ de ROCm apunten a rutas equivocadas (CPU)."
fi

echo -n "  Prueba de PyTorch con HIP (ROCm): "
python3 -c "
import torch
if torch.cuda.is_available():
    print('✅ PyTorch se enlazó a ROCm (GPU detectada: ' + torch.cuda.get_device_name(0) + ')')
else:
    print('❌ PyTorch está en modo CPU (Fallback a la RAM activado).')
" 2>/dev/null || echo "❌ PyTorch no instalado en este entorno o error de importación."

echo "======================================================================"
echo "FIN DEL DIAGNÓSTICO."

