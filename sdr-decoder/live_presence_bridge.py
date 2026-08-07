#!/usr/bin/env python3
"""
VERSIÓN DE DESARROLLO — corre suelto en el host con el Python del sistema,
FUERA de Docker. sdr-decoder todavía es un placeholder en docker-compose.yml
(ver README.md de esa carpeta) — este script es el puente manual que se usa
mientras tanto para probar el circuito real SDR -> backend -> frontend.
Cuando sdr-decoder se containerice de verdad, esta lógica se reemplaza por
la implementación real de decodificación (a cargo de Julián, ver
INVESTIGACION_LRRP.md), no por este script.

Qué hace:
1. Corre `dsd-fme` en modo SDR EN VIVO (no archivo) escuchando el downlink
   de la repetidora (159.635 MHz, Color Code 1).
2. Lee su salida línea por línea en tiempo real (no espera a que termine).
3. Detecta bursts válidos (Color Code=01) de voz normal, emergencia, o
   registro ARS, según los patrones confirmados en INVESTIGACION_LRRP.md
   (sesiones 7-9).
4. Por cada evento nuevo (con rate-limit de 5s por radio_id), hace POST a
   /api/presence.

Requiere: dsd-fme compilado en el PATH (ver ~/sdr_dmr_test/), dongle RTL-SDR
libre, y el backend de tracking-GPS-VHF corriendo (docker compose up -d
backend).

Uso:
    python3 live_presence_bridge.py
    BACKEND_URL=http://localhost:8000 python3 live_presence_bridge.py

IMPORTANTE — calibración de frecuencia:
El valor de PPM de abajo (FREQ_CORR_PPM) es el más reciente conocido al
momento de escribir este script (ver INVESTIGACION_LRRP.md, Sesión 9:
offset medido de -7000 Hz a 159.635 MHz). Está DOCUMENTADO que este offset
varía durante el día (deriva térmica del cristal del dongle, ver sesiones
7/8/9 — fue -6504, -6700 y -7000 Hz en 3 sesiones consecutivas). Si después
de 60-90s de arrancado este script no reporta NINGÚN sync real, lo más
probable es que haga falta recalibrar (repetir el barrido empírico de
freq_corr descripto en la Sesión 8/9 sobre una grabación corta nueva) antes
de seguir esperando a ciegas.
"""

import os
import re
import subprocess
import sys
import time
import urllib.error

# Sin esto, los print() de este script quedan en buffer de bloque al
# correr con stdout redirigido a un pipe/archivo (no a una TTY) — el
# usuario no vería nada "en vivo" en la terminal hasta que el buffer se
# llenase o el proceso terminara, justo lo contrario de lo que se pide.
sys.stdout.reconfigure(line_buffering=True)
import urllib.request
import json
from datetime import datetime, timezone

# --- Configuración de RF (revisar INVESTIGACION_LRRP.md antes de asumir) ---
FRECUENCIA = "159.635M"  # downlink de la repetidora
GANANCIA = "30"  # nominal, misma usada en todas las grabaciones de investigación
FREQ_CORR_PPM = "-44"  # equivalente a los -7000 Hz medidos en la Sesión 9 (ver docstring)
BANDWIDTH_KHZ = "12"
SQUELCH = "0"
VOLUMEN = "2"
DEVICE_INDEX = "0"

DSD_FME_CMD = [
    "dsd-fme",
    "-fs",
    "-i",
    f"rtl:{DEVICE_INDEX}:{FRECUENCIA}:{GANANCIA}:{FREQ_CORR_PPM}:{BANDWIDTH_KHZ}:{SQUELCH}:{VOLUMEN}",
    "-Z",
    "-o",
    "null",
]

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
PRESENCE_ENDPOINT = f"{BACKEND_URL}/api/presence"

# Mapeo Source ID -> alias conocido, confirmado en INVESTIGACION_LRRP.md
# (sesiones 7-9). Agregar acá cualquier radio nuevo que se identifique.
ALIAS_CONOCIDOS = {
    "1000": "Base Guardia",
    "1001": "Matías",
    "1002": "BVM1002",
}

RATE_LIMIT_SEG = 5
AVISO_SIN_SYNC_SEG = 75  # ver docstring — aviso de recalibración

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
SYNC_CC_RE = re.compile(r"Sync:\s*\+?DMR.*\|\s*Color Code=(\S+)")
SRC_VOZ_RE = re.compile(r"SRC=(\d+).*?(Group Emergency Call|Group Call)")
MNIS_SRC_RE = re.compile(r"SRC\(MNIS\):\s*0*(\d+)")
MNIS_ARS_RE = re.compile(r"MNIS ARS")


def strip_ansi(linea: str) -> str:
    return ANSI_RE.sub("", linea)


def enviar_presencia(radio_id: str, evento: str) -> None:
    alias = ALIAS_CONOCIDOS.get(radio_id)
    payload = {
        "radio_id": radio_id,
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
        "evento": evento,
    }
    if alias is not None:
        payload["radio_alias"] = alias

    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        PRESENCE_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    etiqueta = alias or radio_id
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            print(f"[{etiqueta}] evento={evento} -> POST {resp.status}", flush=True)
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", "replace")
        print(f"[{etiqueta}] evento={evento} -> ERROR {exc.code}: {detalle}", flush=True)
    except urllib.error.URLError as exc:
        print(f"[{etiqueta}] evento={evento} -> NO SE PUDO CONECTAR AL BACKEND: {exc.reason}", flush=True)


def main() -> None:
    print("Comando dsd-fme:", " ".join(DSD_FME_CMD))
    print(f"POSTeando eventos a: {PRESENCE_ENDPOINT}")
    print("Ctrl+C para cortar.\n")

    proc = subprocess.Popen(
        DSD_FME_CMD,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # dsd-fme imprime por stderr, lo mergeamos
        text=True,
        bufsize=1,
    )

    ultimo_post = {}  # radio_id -> monotonic() del último POST
    ultima_cc = None  # Color Code de la última línea "Sync: +DMR ..."
    mnis_src_pendiente = None  # último "SRC(MNIS)" visto, a la espera de un "MNIS ARS"
    inicio = time.monotonic()
    ultimo_sync_ok = None
    aviso_emitido = False

    def deberia_postear(radio_id: str) -> bool:
        ahora = time.monotonic()
        if ahora - ultimo_post.get(radio_id, 0) < RATE_LIMIT_SEG:
            return False
        ultimo_post[radio_id] = ahora
        return True

    try:
        for linea_cruda in proc.stdout:
            # Chequeo de recalibración: corre en CADA línea leída (haya o no
            # matcheado algo), porque si nunca hay sync real, todas las
            # líneas terminan en el "continue" del filtro de Color Code de
            # abajo y este aviso nunca llegaría a ejecutarse si estuviera
            # después de esos continue.
            if (
                not aviso_emitido
                and ultimo_sync_ok is None
                and time.monotonic() - inicio > AVISO_SIN_SYNC_SEG
            ):
                print(
                    f"\n⚠️  Pasaron {AVISO_SIN_SYNC_SEG}s sin ningún sync con Color Code=01. "
                    "Puede hacer falta recalibrar FREQ_CORR_PPM (el offset de frecuencia "
                    "deriva durante el día, ver INVESTIGACION_LRRP.md Sesión 8/9) antes de "
                    "seguir esperando a ciegas.\n",
                    flush=True,
                )
                aviso_emitido = True

            linea = strip_ansi(linea_cruda).strip()
            if not linea:
                continue

            m = SYNC_CC_RE.search(linea)
            if m:
                ultima_cc = m.group(1)
                if ultima_cc == "01":
                    ultimo_sync_ok = time.monotonic()
                continue

            if ultima_cc != "01":
                # Todo lo demás (voz/ARS) solo se confía si vino inmediatamente
                # después de un header con Color Code=01 válido (ver
                # INVESTIGACION_LRRP.md: Color Code != 01 es ruido/decodificación
                # marginal en este sistema).
                continue

            m = SRC_VOZ_RE.search(linea)
            if m:
                radio_id, tipo_llamada = m.group(1), m.group(2)
                evento = "emergencia" if "Emergency" in tipo_llamada else "voz"
                if deberia_postear(radio_id):
                    enviar_presencia(radio_id, evento)
                continue

            m = MNIS_SRC_RE.search(linea)
            if m:
                mnis_src_pendiente = m.group(1)
                continue

            if MNIS_ARS_RE.search(linea) and mnis_src_pendiente is not None:
                if deberia_postear(mnis_src_pendiente):
                    enviar_presencia(mnis_src_pendiente, "ars")
                mnis_src_pendiente = None
                continue

    except KeyboardInterrupt:
        print("\nCortado por el usuario.")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
