#!/bin/bash
#
# check_sdr.sh — chequeo previo del SDR en el HOST, antes de `docker compose up`.
#
# IMPORTANTE: esto corre en el host, NUNCA dentro de un contenedor. El
# driver DVB del kernel y las reglas udev son configuración del kernel del
# HOST — ningún namespace ni flag de Docker puede resolver esto desde
# adentro de un contenedor (investigado explícitamente, ver
# docs/ARQUITECTURA.md sección 2 y docs/operacion-sdr.md). Pensado para
# correrse a mano o desde un healthcheck/script de arranque del host antes
# de levantar el stack — no es parte del Dockerfile ni de
# docker-compose.yml.
#
# Uso:
#   ./check_sdr.sh
#   echo $?   # 0 = OK, distinto de 0 = hay un problema, ver el mensaje
#
# Ver docs/ARQUITECTURA.md sección 9 para el detalle de la regla udev, y
# docs/operacion-sdr.md para qué hacer si esto falla.

set -u

SYMLINK="/dev/sdr_bomberos"
DVB_MODULO="dvb_usb_rtl28xxu"
GRUPO_ESPERADO="plugdev"
MODO_ESPERADO="660"

fallos=0

echo "=== check_sdr.sh — chequeo del SDR en el host ==="
echo

# 1. Confirmar que el driver DVB no esté cargado (si está, reclamó el
# dongle antes que nadie pueda abrirlo vía libusb).
if lsmod | grep -q "^${DVB_MODULO}"; then
    echo "✗ El driver del kernel '${DVB_MODULO}' está cargado — va a reclamar el"
    echo "  dongle como sintonizador de TV antes que rtl_sdr pueda abrirlo."
    echo "  Solución: sudo modprobe -r ${DVB_MODULO}"
    echo "  Para que no vuelva a cargarse en el próximo boot, confirmar que existe"
    echo "  un blacklist persistente (ver paso 2)."
    fallos=$((fallos + 1))
else
    echo "✓ Driver '${DVB_MODULO}' no está cargado."
fi

# 2. Confirmar que el blacklist persistente exista (para que sobreviva a
# un reinicio del host, no solo a este chequeo puntual).
if grep -rq "blacklist ${DVB_MODULO}" /etc/modprobe.d/ 2>/dev/null; then
    echo "✓ Blacklist persistente de '${DVB_MODULO}' encontrado en /etc/modprobe.d/."
else
    echo "✗ No se encontró un blacklist persistente de '${DVB_MODULO}' en /etc/modprobe.d/."
    echo "  Sin esto, el driver puede volver a cargarse en el próximo reinicio del host."
    echo "  Solución: crear /etc/modprobe.d/blacklist-rtlsdr.conf con la línea:"
    echo "    blacklist ${DVB_MODULO}"
    fallos=$((fallos + 1))
fi

# 3. Confirmar que el symlink estable de la regla udev exista.
if [ -L "$SYMLINK" ]; then
    echo "✓ Symlink '${SYMLINK}' existe (regla udev aplicó)."
else
    echo "✗ '${SYMLINK}' no existe — la regla udev no aplicó (¿dongle desconectado?"
    echo "  ¿serial no coincide? ver /etc/udev/rules.d/99-rtlsdr-tracking.rules)."
    echo "  Solución: confirmar que el dongle esté conectado, y probar"
    echo "    sudo udevadm control --reload-rules && sudo udevadm trigger"
    fallos=$((fallos + 1))
fi

# 4. Confirmar permisos del nodo real detrás del symlink (grupo/modo).
if [ -L "$SYMLINK" ]; then
    real_path=$(readlink -f "$SYMLINK")
    if [ -e "$real_path" ]; then
        grupo=$(stat -c "%G" "$real_path")
        modo=$(stat -c "%a" "$real_path")
        if [ "$grupo" = "$GRUPO_ESPERADO" ] && [ "$modo" = "$MODO_ESPERADO" ]; then
            echo "✓ Permisos correctos en ${real_path} (grupo=${grupo}, modo=${modo})."
        else
            echo "✗ Permisos inesperados en ${real_path} (grupo=${grupo}, modo=${modo};"
            echo "  se esperaba grupo=${GRUPO_ESPERADO}, modo=${MODO_ESPERADO})."
            echo "  Revisar /etc/udev/rules.d/99-rtlsdr-tracking.rules."
            fallos=$((fallos + 1))
        fi
    else
        echo "✗ '${SYMLINK}' apunta a '${real_path}', que no existe (dongle desconectado)."
        fallos=$((fallos + 1))
    fi
fi

echo
if [ "$fallos" -eq 0 ]; then
    echo "OK — todo listo para levantar sdr-decoder con 'docker compose up'."
    exit 0
else
    echo "FALLÓ (${fallos} problema(s)) — resolver lo de arriba antes de levantar sdr-decoder."
    exit 1
fi
