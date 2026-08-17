#!/usr/bin/env python3
"""
Test de regresión OBLIGATORIO antes de integrar `baofeng_gps_parser.py` al
bridge (ver INVESTIGACION_LRRP.md, sección "🎯 HITO — Primera coordenada
GPS real capturada"). Corre el parser contra los DOS bloques ya guardados
que originaron el hallazgo — no dispara ninguna transmisión nueva — y
confirma que reproduce la misma coordenada reconstruida a mano en su
momento.

Uso (dentro del contenedor sdr-decoder, donde vive el volumen sdr_logs):
    python3 test_baofeng_gps_parser.py [directorio_de_logs]

Sin argumento, usa LOGS_DIR (mismo default que live_presence_bridge.py).
"""

import sys
from pathlib import Path

from baofeng_gps_parser import BaofengGpsDetector

BLOQUES_REFERENCIA = [
    "bloque_3425_20260817_170256_496195.log",
    "bloque_3426_20260817_170310_881933.log",
]

LAT_ESPERADA = -32.3406
LON_ESPERADA = -65.0247
TOLERANCIA = 0.001


def main() -> int:
    logs_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "logs"

    for nombre in BLOQUES_REFERENCIA:
        ruta = logs_dir / nombre
        if not ruta.exists():
            print(f"FALLO: no se encontró el bloque de referencia {ruta}")
            return 1

    detector = BaofengGpsDetector()
    todos_los_hallazgos = []
    for nombre in BLOQUES_REFERENCIA:
        texto = (logs_dir / nombre).read_text()
        hallazgos = detector.procesar_bloque(texto)
        print(f"--- {nombre}: {len(hallazgos)} hallazgo(s) ---")
        for h in hallazgos:
            print(
                f"  radio_id={h['radio_id']} (contacto={h['radio_id_contacto']}) "
                f"lat={h['lat']} lon={h['lon']} vel={h['velocidad_kmh']} "
                f"completo={h['completo']} crc_error={h['crc_error']} "
                f"reconstruido={h['reconstruido_cruzando_bloques']}"
            )
            print(f"  campos crudos: {h['campos']}")
            todos_los_hallazgos.append(h)

    completos = [h for h in todos_los_hallazgos if h["completo"]]
    if not completos:
        print("\nFALLO: ningún hallazgo llegó a decodificar lat/lon completos.")
        return 1

    mejor = completos[-1]  # el último completo debería ser el reconstruido cruzando bloques
    ok_radio = mejor["radio_id"] == "1"
    ok_contacto = mejor["radio_id_contacto"] == "1007"
    ok_lat = abs(mejor["lat"] - LAT_ESPERADA) <= TOLERANCIA
    ok_lon = abs(mejor["lon"] - LON_ESPERADA) <= TOLERANCIA
    ok_reconstruido = mejor["reconstruido_cruzando_bloques"] is True

    print("\n=== Verificación contra el hallazgo ya reconstruido a mano ===")
    print(f"radio_id == '1':                 {ok_radio} (obtenido: {mejor['radio_id']})")
    print(f"radio_id_contacto == '1007':     {ok_contacto} (obtenido: {mejor['radio_id_contacto']})")
    print(f"lat ≈ {LAT_ESPERADA} (±{TOLERANCIA}):      {ok_lat} (obtenido: {mejor['lat']})")
    print(f"lon ≈ {LON_ESPERADA} (±{TOLERANCIA}):      {ok_lon} (obtenido: {mejor['lon']})")
    print(f"reconstruido cruzando bloques:   {ok_reconstruido}")

    if all([ok_radio, ok_contacto, ok_lat, ok_lon, ok_reconstruido]):
        print("\nPASS: el parser reproduce la coordenada del hito sin necesitar una transmisión nueva.")
        return 0

    print("\nFALLO: el parser NO reprodujo exactamente el resultado ya conocido.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
