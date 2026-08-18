#!/usr/bin/env python3
"""
Test de regresión OBLIGATORIO antes de tocar el bridge en vivo con
cualquier cambio a `dmr_texto_plano_parser.py` (antes
`baofeng_gps_parser.py` / `test_baofeng_gps_parser.py` — se renombraron
los dos junto con el módulo, ver su docstring). Corre el parser contra
CUATRO bloques ya guardados, casos reales de los tres mecanismos que
reconoce — no dispara ninguna transmisión nueva:

  1-2. `bloque_3425`/`3426` — rebote ICMP (hito de GPS original,
       radio_id 1 → 1007). Regresión histórica, no debe romperse.
  3. `bloque_0028` — UDP directo, mensaje de texto "Test123"
     (radio_id 1001 → 1). No debe tener forma de coordenada ni postearse
     a /api/telemetry.
  4. `bloque_0019` — fragmentos sueltos sin consolidar, coordenada nueva
     (radio_id 1 → 1001). Debe reconstruir al menos parcialmente,
     marcando cuántos bloques faltaron.
  5. `bloque_0473/0475/0498/0528` (sesión de monitoreo en vivo del
     2026-08-18) — beacon GPS automático "APRS" del Baofeng, formato NMEA
     $GPRMC sobre SAP 03 [UDP Comp] + Unconfirmed Delivery (radio_id
     1 → 456, cada ~30s). Deben parsear lat/lon/velocidad/rumbo/hora, Y
     tratarse como 4 eventos DISTINTOS pese a compartir Source/Target
     (dedup por timestamp embebido en el NMEA, no por Source+Target).

Uso (dentro del contenedor sdr-decoder, donde vive el volumen sdr_logs):
    python3 test_dmr_texto_plano_parser.py [directorio_de_logs]
"""

import sys
from pathlib import Path

from dmr_texto_plano_parser import DetectorMensajesDMR

LAT_ESPERADA_HITO = -32.3406
LON_ESPERADA_HITO = -65.0247
TOLERANCIA = 0.001


def _fallo(msg: str) -> None:
    print(f"FALLO: {msg}")


def test_icmp_bounce_hito(logs_dir: Path) -> bool:
    print("\n=== Caso 1: rebote ICMP (hito de GPS, bloques 3425/3426) ===")
    archivos = [
        "bloque_3425_20260817_170256_496195.log",
        "bloque_3426_20260817_170310_881933.log",
    ]
    for nombre in archivos:
        if not (logs_dir / nombre).exists():
            _fallo(f"no se encontró el bloque de referencia {nombre}")
            return False

    detector = DetectorMensajesDMR()
    completos = []
    for nombre in archivos:
        for h in detector.procesar_bloque((logs_dir / nombre).read_text()):
            print(f"  mecanismo={h['mecanismo']} radio_id={h['radio_id']} lat={h['lat']} lon={h['lon']} completo={h['completo']} duplicado={h.get('duplicado')}")
            if h["completo"]:
                completos.append(h)

    if not completos:
        _fallo("ningún hallazgo completo en el caso del hito")
        return False
    mejor = completos[-1]
    ok = (
        mejor["mecanismo"] == "icmp_bounce"
        and mejor["radio_id"] == "1"
        and mejor["radio_id_contacto"] == "1007"
        and abs(mejor["lat"] - LAT_ESPERADA_HITO) <= TOLERANCIA
        and abs(mejor["lon"] - LON_ESPERADA_HITO) <= TOLERANCIA
        and mejor["reconstruido_cruzando_bloques"] is True
    )
    print("PASS" if ok else "FALLO: no reprodujo el hito de GPS")
    return ok


def test_udp_directo_test123(logs_dir: Path) -> bool:
    print("\n=== Caso 2: UDP directo, mensaje de texto (bloque 0028, 'Test123') ===")
    nombre = "bloque_0028_20260817_214606_903898.log"
    if not (logs_dir / nombre).exists():
        _fallo(f"no se encontró el bloque de referencia {nombre}")
        return False

    detector = DetectorMensajesDMR()
    hallazgos = detector.procesar_bloque((logs_dir / nombre).read_text())
    candidatos = [h for h in hallazgos if h["mecanismo"] == "udp_directo" and h["radio_id"] == "1001"]
    for h in hallazgos:
        print(f"  mecanismo={h['mecanismo']} radio_id={h['radio_id']} contacto={h['radio_id_contacto']} completo={h['completo']} texto={h['texto_crudo']!r}")

    if not candidatos:
        _fallo("no se encontró ningún hallazgo udp_directo de radio_id=1001")
        return False

    mensaje = candidatos[0]
    texto_normalizado = mensaje["texto_crudo"].replace("\x00", "").strip()
    # Tolerante a los 2 bits corruptos ya documentados (INVESTIGACION_LRRP.md):
    # se espera 'T' al inicio y 't123' al final ("Test123" con la 'e' y la
    # 's' corrompidas en el medio) — no una igualdad de string exacta.
    ok_texto = texto_normalizado.startswith("T") and texto_normalizado.endswith("t123")
    ok_no_coordenada = mensaje["completo"] is False
    ok_radio_contacto = mensaje["radio_id_contacto"] == "1"

    print(f"texto contiene 'Test123' (tolerando bits corruptos): {ok_texto}")
    print(f"NO tiene forma de coordenada (no se postearía a /api/telemetry): {ok_no_coordenada}")
    print(f"radio_id_contacto == '1' (el Baofeng): {ok_radio_contacto}")

    ok = ok_texto and ok_no_coordenada and ok_radio_contacto
    print("PASS" if ok else "FALLO: no reprodujo el hallazgo 'Test123'")
    return ok


def test_fragmentos_sueltos_coordenada_nueva(logs_dir: Path) -> bool:
    print("\n=== Caso 3: fragmentos sueltos sin consolidar (bloque 0019, coordenada nueva) ===")
    nombre = "bloque_0019_20260817_214358_525804.log"
    if not (logs_dir / nombre).exists():
        _fallo(f"no se encontró el bloque de referencia {nombre}")
        return False

    detector = DetectorMensajesDMR()
    hallazgos = detector.procesar_bloque((logs_dir / nombre).read_text())
    reconstruidos = [h for h in hallazgos if h["mecanismo"] == "fragmentos_reconstruidos"]
    for h in reconstruidos:
        print(
            f"  radio_id={h['radio_id']} contacto={h['radio_id_contacto']} "
            f"bloques={h['bloques_capturados']}/{h['bloques_totales']} "
            f"campos={h['campos']}"
        )

    if not reconstruidos:
        _fallo("no se encontró ningún hallazgo de fragmentos_reconstruidos")
        return False

    hallazgo = reconstruidos[0]
    ok_radio = hallazgo["radio_id"] == "1" and hallazgo["radio_id_contacto"] == "1001"
    ok_incompleto_marcado = (
        hallazgo["bloques_totales"] is not None
        and hallazgo["bloques_capturados"] is not None
        and hallazgo["bloques_capturados"] < hallazgo["bloques_totales"]
    )
    # Coordenada NUEVA y distinta a la del hito (mismo lugar aprox, otro
    # fix GPS) — no se espera que coincida con -32.3406/-65.0247, solo que
    # esté en el mismo orden de magnitud (Merlo, San Luis).
    ok_coordenada = (
        hallazgo["completo"]
        and abs(hallazgo["lat"] - (-32.34)) < 0.05
        and abs(hallazgo["lon"] - (-65.03)) < 0.05
    )

    print(f"radio_id=1 -> contacto=1001: {ok_radio}")
    print(f"marca bloques faltantes ({hallazgo['bloques_capturados']}/{hallazgo['bloques_totales']}): {ok_incompleto_marcado}")
    print(f"recuperó lat/lon completos y plausibles: {ok_coordenada} (lat={hallazgo['lat']}, lon={hallazgo['lon']})")

    ok = ok_radio and ok_incompleto_marcado and ok_coordenada
    print("PASS" if ok else "FALLO: no reconstruyó nada útil de los fragmentos sueltos")
    return ok


def test_nmea_beacon_reales(logs_dir: Path) -> bool:
    print("\n=== Caso 4: beacon GPS automático NMEA (4 beacons reales, 2026-08-18) ===")
    # (nombre_archivo, hora_esperada_hhmmss, lat_esperada, lon_esperada, vel_nudos_esperada, rumbo_esperado)
    casos = [
        ("bloque_0473_20260818_004626_153403.log", "004627", -32.340215, -65.024674, 0.00, 258.69),
        ("bloque_0475_20260818_004654_808856.log", "004655", -32.340218, -65.024662, 0.00, 258.69),
        ("bloque_0498_20260818_005223_900421.log", "005222", -32.340208, -65.024755, 0.86, 282.68),
        ("bloque_0528_20260818_005932_837888.log", "005942", -32.340260, -65.024721, 0.55, 99.28),
    ]
    for nombre, *_ in casos:
        if not (logs_dir / nombre).exists():
            _fallo(f"no se encontró el bloque de referencia {nombre}")
            return False

    detector = DetectorMensajesDMR()
    beacons = []
    for nombre, hora_esperada, lat_esp, lon_esp, vel_esp, rumbo_esp in casos:
        hallazgos = detector.procesar_bloque((logs_dir / nombre).read_text())
        encontrados = [h for h in hallazgos if h["mecanismo"] == "nmea_beacon"]
        if not encontrados:
            _fallo(f"{nombre}: no se encontró ningún beacon nmea_beacon")
            return False
        h = encontrados[0]
        print(
            f"  {nombre}: radio_id={h['radio_id']} contacto={h['radio_id_contacto']} "
            f"lat={h['lat']} lon={h['lon']} vel_kmh={h['velocidad_kmh']} rumbo={h['rumbo']} "
            f"ts_gps={h['timestamp_gps_iso']} crc_error={h['crc_error']} duplicado={h.get('duplicado')}"
        )
        ok_radio = h["radio_id"] == "1" and h["radio_id_contacto"] == "456"
        ok_hora = h["timestamp_gps_iso"] is not None and hora_esperada[:2] + ":" + hora_esperada[2:4] + ":" + hora_esperada[4:] in h["timestamp_gps_iso"]
        ok_lat = h["lat"] is not None and abs(h["lat"] - lat_esp) <= 0.001
        ok_lon = h["lon"] is not None and abs(h["lon"] - lon_esp) <= 0.001
        ok_vel = h["velocidad_kmh"] is not None and abs(h["velocidad_kmh"] - round(vel_esp * 1.852, 3)) <= 0.01
        ok_rumbo = h["rumbo"] is not None and abs(h["rumbo"] - rumbo_esp) <= 0.01
        ok_completo = h["completo"] is True
        if not all([ok_radio, ok_hora, ok_lat, ok_lon, ok_vel, ok_rumbo, ok_completo]):
            _fallo(
                f"{nombre}: radio={ok_radio} hora={ok_hora} lat={ok_lat} lon={ok_lon} "
                f"vel={ok_vel} rumbo={ok_rumbo} completo={ok_completo}"
            )
            return False
        beacons.append(h)

    # El punto central de la Etapa 2: 4 beacons reales, mismo Source=1 /
    # Target=456, NO deben deduplicarse entre sí — cada uno es un evento
    # de tracking legítimo y distinto (hora/posición cambian de verdad).
    todos_no_duplicados = all(h["duplicado"] is False for h in beacons)
    print(f"los 4 beacons se trataron como eventos DISTINTOS (ninguno marcado duplicado): {todos_no_duplicados}")

    # Checksum NMEA: se confirmó (fuera de este test, ver INVESTIGACION_LRRP.md)
    # que el Baofeng manda un checksum que NO valida contra la fórmula
    # estándar en NINGUNO de los 4 casos reales — comportamiento sistemático
    # del firmware, no corrupción de captura. Se verifica que el parser lo
    # marca como tal (crc_error=True) SIN por eso descartar los datos.
    todos_marcados_crc_error = all(h["crc_error"] is True for h in beacons)
    print(f"los 4 beacons quedaron marcados crc_error=True (checksum no estándar, dato igual parseado): {todos_marcados_crc_error}")

    ok = todos_no_duplicados and todos_marcados_crc_error
    print("PASS" if ok else "FALLO: algo no se comportó como se esperaba con los beacons NMEA")
    return ok


def main() -> int:
    logs_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "logs"

    resultados = [
        test_icmp_bounce_hito(logs_dir),
        test_udp_directo_test123(logs_dir),
        test_fragmentos_sueltos_coordenada_nueva(logs_dir),
        test_nmea_beacon_reales(logs_dir),
    ]

    print("\n=== Resumen ===")
    nombres = [
        "rebote ICMP (hito)", "UDP directo (Test123)",
        "fragmentos sueltos (coordenada nueva)", "beacon NMEA automático (4 reales)",
    ]
    for nombre, ok in zip(nombres, resultados):
        print(f"  {'PASS' if ok else 'FALLO'} — {nombre}")

    return 0 if all(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
