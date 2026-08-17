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


def main() -> int:
    logs_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "logs"

    resultados = [
        test_icmp_bounce_hito(logs_dir),
        test_udp_directo_test123(logs_dir),
        test_fragmentos_sueltos_coordenada_nueva(logs_dir),
    ]

    print("\n=== Resumen ===")
    nombres = ["rebote ICMP (hito)", "UDP directo (Test123)", "fragmentos sueltos (coordenada nueva)"]
    for nombre, ok in zip(nombres, resultados):
        print(f"  {'PASS' if ok else 'FALLO'} — {nombre}")

    return 0 if all(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
