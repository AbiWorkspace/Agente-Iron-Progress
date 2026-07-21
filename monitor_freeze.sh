#!/usr/bin/env bash
# ==============================================================================
# monitor_freeze.sh — TESTIGO NEGRO del congelamiento (Iron Progress)
#
# Registra cada 2 s a un CSV con sync inmediato, para que la evidencia SOBREVIVA
# al congelamiento (si la PC muere, el archivo ya está en disco).
#
# Uso:
#   chmod +x monitor_freeze.sh
#   ./monitor_freeze.sh                # loguea en ~/iron_diag/
#   (en otra terminal) arranca el agente y trabaja hasta que congele
#   Tras reiniciar:  ./monitor_freeze.sh --leer     # muestra los últimos 40 samples
#
# Columnas clave:
#   ram_disp_mb   -> RAM disponible. Si cae < 800 antes del freeze: culpable RAM.
#   swap_usada_mb -> si sube rápido: thrashing (la firma del freeze de Linux).
#   psi_mem10     -> presión de memoria (0-100). >60 = el sistema está ahogado.
#   psi_io10      -> presión de E/S. >80 con swap subiendo = thrashing confirmado.
#   vram_mb       -> VRAM usada. Si toca el techo (~24500) antes del freeze:
#                    culpable VRAM (Ollama + Whisper/YOLO peleándose la GPU).
# ==============================================================================
set -u
DEST="${HOME}/iron_diag"

if [[ "${1:-}" == "--leer" ]]; then
    ULTIMO=$(ls -t "${DEST}"/telemetria_*.csv 2>/dev/null | head -1)
    if [[ -z "${ULTIMO}" ]]; then echo "No hay registros en ${DEST}"; exit 1; fi
    echo "=== Últimos 40 samples de ${ULTIMO} ==="
    column -s, -t < <(head -1 "${ULTIMO}"; tail -40 "${ULTIMO}")
    echo
    echo "=== Kernel del arranque ANTERIOR (GPU resets / OOM) ==="
    journalctl -k -b -1 --no-pager 2>/dev/null | \
        grep -iE "amdgpu|ring .*timeout|GPU reset|out of memory|oom-kill|killed process" | tail -25 \
        || echo "(sin journal persistente del arranque anterior; ver FASE 0 del plan)"
    exit 0
fi

mkdir -p "${DEST}"
CSV="${DEST}/telemetria_$(date +%Y%m%d_%H%M%S).csv"
echo "hora,ram_disp_mb,swap_usada_mb,psi_mem10,psi_io10,vram_mb,vram_total_mb,proc1,proc2,proc3" > "${CSV}"
sync

leer_vram() {
    # Best effort: rocm-smi y amd-smi cambian de formato entre versiones.
    local out
    out=$(rocm-smi --showmeminfo vram 2>/dev/null | grep -iE "used|total" | grep -oE "[0-9]+" | head -2)
    if [[ -n "${out}" ]]; then
        local used total
        used=$(echo "${out}" | sed -n 1p); total=$(echo "${out}" | sed -n 2p)
        # Heurística: si vienen en bytes, pasar a MB
        [[ ${used:-0} -gt 100000000 ]] && used=$((used/1048576))
        [[ ${total:-0} -gt 100000000 ]] && total=$((total/1048576))
        echo "${used:-NA},${total:-NA}"; return
    fi
    out=$(amd-smi metric -m 2>/dev/null | grep -iE "VRAM_USED|VRAM_TOTAL" | grep -oE "[0-9]+" | head -2)
    if [[ -n "${out}" ]]; then
        echo "$(echo "${out}" | sed -n 1p),$(echo "${out}" | sed -n 2p)"; return
    fi
    echo "NA,NA"
}

echo "[MONITOR] Registrando en ${CSV}"
echo "[MONITOR] Deja esto corriendo, arranca el agente y usa la PC hasta que congele."
echo "[MONITOR] Ctrl+C para parar. Tras reiniciar:  $0 --leer"
echo

while true; do
    HORA=$(date +%H:%M:%S)
    RAM=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
    SWT=$(awk '/SwapTotal/{print int($2/1024)}' /proc/meminfo)
    SWF=$(awk '/SwapFree/{print int($2/1024)}' /proc/meminfo)
    SWU=$((SWT - SWF))
    PSIM=$(awk '/^some/{split($2,a,"="); print a[2]; exit}' /proc/pressure/memory 2>/dev/null || echo NA)
    PSII=$(awk '/^some/{split($2,a,"="); print a[2]; exit}' /proc/pressure/io 2>/dev/null || echo NA)
    VRAM=$(leer_vram)
    PROCS=$(ps -eo rss=,comm= --sort=-rss | head -3 | awk '{printf "%s(%dMB) ", $2, $1/1024}')
    P1=$(echo "${PROCS}" | awk '{print $1}')
    P2=$(echo "${PROCS}" | awk '{print $2}')
    P3=$(echo "${PROCS}" | awk '{print $3}')

    echo "${HORA},${RAM},${SWU},${PSIM},${PSII},${VRAM},${P1},${P2},${P3}" >> "${CSV}"
    sync   # crítico: sin esto, el freeze se lleva las últimas líneas

    # Aviso en pantalla cuando entra en zona de peligro
    if [[ "${RAM}" -lt 1200 ]]; then
        echo "[!!] $(date +%H:%M:%S) RAM disponible ${RAM}MB — swap ${SWU}MB — PSI mem ${PSIM}"
    fi
    sleep 2
done
