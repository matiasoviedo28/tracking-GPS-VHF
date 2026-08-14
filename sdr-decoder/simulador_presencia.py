#!/usr/bin/env python3
"""
HERRAMIENTA DE DESARROLLO — NO ES EL DECODIFICADOR REAL.

Manda POSTs de prueba a /api/presence cada tanto, simulando varios equipos
transmitiendo con distinta frecuencia, para poder probar el panel de
presencia (backend + frontend) sin depender de que la decodificación DMR
real (a cargo de Julián, ver INVESTIGACION_LRRP.md) esté terminada.

Uso:
    python3 simulador_presencia.py
    BACKEND_URL=http://localhost:8000 python3 simulador_presencia.py

Sin dependencias externas (solo stdlib) a propósito, para no requerir pip
install en una máquina de prueba cualquiera.
"""

import json
import os
import random
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
PRESENCE_ENDPOINT = f"{BACKEND_URL}/api/presence"

# Equipos simulados, cada uno con su propia frecuencia de transmisión
# (segundos, rango min-max entre envíos) y distribución de eventos —
# pensado para imitar, de forma aproximada, lo observado en las sesiones
# reales de investigación (ver INVESTIGACION_LRRP.md): una base fija que
# manda ARS de tanto en tanto, y handys que transmiten voz más seguido.
EQUIPOS_SIMULADOS = [
    {
        "radio_id": "1000",
        "radio_alias": "Base Guardia (SIMULADO)",
        "intervalo_seg": (60, 180),
        "eventos": ["ars", "ars", "voz"],
    },
    {
        "radio_id": "1001",
        "radio_alias": "Matías (SIMULADO)",
        "intervalo_seg": (15, 60),
        "eventos": ["voz", "voz", "voz", "emergencia"],
    },
    {
        "radio_id": "1002",
        "radio_alias": "BVM1002 (SIMULADO)",
        "intervalo_seg": (20, 90),
        "eventos": ["voz", "voz"],
    },
    {
        "radio_id": "1003",
        "radio_alias": "Móvil 4 (SIMULADO)",
        "intervalo_seg": (30, 120),
        "eventos": ["voz"],
    },
]


def enviar_presencia(radio_id: str, radio_alias: str, evento: str) -> None:
    payload = {
        "radio_id": radio_id,
        "radio_alias": radio_alias,
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
        "evento": evento,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        PRESENCE_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            print(f"[{radio_alias}] {evento} -> {resp.status}")
    except urllib.error.HTTPError as exc:
        print(f"[{radio_alias}] {evento} -> ERROR {exc.code}: {exc.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as exc:
        print(f"[{radio_alias}] {evento} -> NO SE PUDO CONECTAR: {exc.reason}")


def proximo_disparo(equipo: dict) -> float:
    minimo, maximo = equipo["intervalo_seg"]
    return random.uniform(minimo, maximo)


def main() -> None:
    print(f"Simulador de presencia — enviando a {PRESENCE_ENDPOINT}")
    print("Ctrl+C para cortar.\n")

    proximo = {
        equipo["radio_id"]: time.monotonic() + proximo_disparo(equipo) for equipo in EQUIPOS_SIMULADOS
    }

    try:
        while True:
            ahora = time.monotonic()
            for equipo in EQUIPOS_SIMULADOS:
                if ahora >= proximo[equipo["radio_id"]]:
                    evento = random.choice(equipo["eventos"])
                    enviar_presencia(equipo["radio_id"], equipo["radio_alias"], evento)
                    proximo[equipo["radio_id"]] = ahora + proximo_disparo(equipo)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nCortado por el usuario.")


if __name__ == "__main__":
    main()
