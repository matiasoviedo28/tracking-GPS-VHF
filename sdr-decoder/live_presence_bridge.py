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
momento de escribir este script (prueba end-to-end en vivo: offset medido de
-7800 Hz a 159.635 MHz, convertido a ppm). Está DOCUMENTADO que este offset
varía durante el día (deriva térmica del cristal del dongle — fue -6504,
-6700, -7000 y -7800 Hz en sesiones/momentos consecutivos, ver
INVESTIGACION_LRRP.md sesiones 7/8/9 y el resumen de la prueba end-to-end).
Si después de 60-90s de arrancado este script no reporta NINGÚN sync real,
avisa solo con un ⚠️ en la terminal — hace falta recalibrar: grabar ~40-90s
de IQ crudo con `rtl_sdr` durante una transmisión real, y barrer valores de
`freq_corr` con `iq_to_wav.py` + `dsd-fme` en modo archivo (offline) hasta
encontrar el que maximice syncs reales, igual que se hizo para llegar a este
valor. NO se puede recalibrar solo escuchando en vivo sin grabar primero —
en modo SDR en vivo, dsd-fme no imprime nada útil para comparar valores de
ppm uno por uno en tiempo real de forma práctica.
"""

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Sin esto, los print() de este script quedan en buffer de bloque al
# correr con stdout redirigido a un pipe/archivo (no a una TTY) — el
# usuario no vería nada "en vivo" en la terminal hasta que el buffer se
# llenase o el proceso terminara, justo lo contrario de lo que se pide.
sys.stdout.reconfigure(line_buffering=True)

# --- Configuración de RF (revisar INVESTIGACION_LRRP.md antes de asumir) ---
FRECUENCIA = "159.635M"  # downlink de la repetidora
GANANCIA = "30"  # nominal, misma usada en todas las grabaciones de investigación
# Última calibración empírica confirmada (ver resumen de sesión): -7800 Hz a
# 159.635 MHz, medido con una transmisión real durante esta misma sesión de
# prueba end-to-end. NO asumir que sigue valiendo en la próxima sesión — ya
# se documentó que deriva durante el día (fue -6504, -6700, -7000 y ahora
# -7800 Hz en sesiones/momentos consecutivos).
FREQ_CORR_PPM = "-49"
BANDWIDTH_KHZ = "12"
SQUELCH = "0"
VOLUMEN = "2"
DEVICE_INDEX = "0"

DSD_FME_CMD = [
    # stdbuf -oL -eL: fuerza line-buffering en el proceso hijo. Sin esto,
    # dsd-fme bufferiza su salida por bloque al detectar que no está
    # conectado a una TTY (comportamiento normal de glibc) — el resultado es
    # que este script no ve NADA (ni el banner de arranque) durante minutos,
    # aunque dsd-fme esté decodificando bien del otro lado.
    "stdbuf",
    "-oL",
    "-eL",
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

    # Hilo lector separado: en modo SDR en vivo, con el canal en silencio
    # real, dsd-fme puede no imprimir NADA durante minutos (a diferencia del
    # modo archivo, que sí "chatea" constantemente incluso sin señal). Si
    # leyéramos las líneas directo en el loop principal con un simple
    # `for linea in proc.stdout`, el chequeo de recalibración (que depende
    # del reloj, no de que lleguen líneas) quedaría bloqueado indefinidamente
    # esperando la próxima línea y nunca podría dispararse. Con un hilo que
    # solo lee y encola, el loop principal puede hacer `queue.get(timeout=…)`
    # y revisar el reloj aunque no llegue nada.
    lineas = queue.Queue()

    def leer_stdout():
        for linea_cruda in proc.stdout:
            lineas.put(linea_cruda)
        lineas.put(None)  # centinela: el proceso cerró su stdout

    hilo_lector = threading.Thread(target=leer_stdout, daemon=True)
    hilo_lector.start()

    try:
        while True:
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

            try:
                linea_cruda = lineas.get(timeout=1)
            except queue.Empty:
                continue  # nada nuevo todavía — vuelve arriba a re-chequear el reloj

            if linea_cruda is None:
                print("dsd-fme cerró su salida (¿se cayó el proceso o el dongle?).", flush=True)
                break

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
