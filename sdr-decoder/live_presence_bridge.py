#!/usr/bin/env python3
"""
Bridge de captura/decodificación DMR en vivo — corre dentro del contenedor
`sdr-decoder` (ver Dockerfile y docker-compose.yml). Hasta la
containerización corría suelto en el host; los paths ahora son relativos
al contenedor o configurables por variable de entorno (ver más abajo), no
hardcodeados a una máquina de investigación puntual.

Qué hace (diseño desde la Sesión 12 — ver INVESTIGACION_LRRP.md):
1. Graba un bloque corto (BLOCK_SECONDS) de IQ crudo con `rtl_sdr`,
   escuchando el downlink de la repetidora.
2. Convierte ese bloque a WAV demodulado con `iq_to_wav.py` (versionado
   junto a este script), aplicando la corrección de frecuencia configurada.
3. Corre `dsd-fme` en modo ARCHIVO (no en vivo) sobre ese WAV.
4. Parsea la salida completa del bloque buscando bursts válidos
   (Color Code=01) de voz, emergencia, ARS, o LRRP/GPS.
5. Postea a /api/presence lo que se haya detectado (con rate-limit de 5s
   por radio_id), y a /api/audio-eventos el audio de bloques con voz.
6. Mide, por bloque: el total de líneas "Sync: +DMR" (cualquier Color
   Code, no solo 01) y el desvío estándar de las muestras IQ crudas —
   clasifica el estado del SDR con eso y lo postea a /api/sdr-status
   (ver docs/operacion-sdr.md para qué significa cada estado).
7. Guarda a disco el texto crudo completo de `dsd-fme` de cada bloque
   (para poder auditar retroactivamente qué pasó).
8. Borra los archivos temporales de IQ/WAV del bloque y repite
   indefinidamente (los logs crudos de texto NO se borran solos).

Por qué este diseño y no `dsd-fme -i rtl:...` en vivo: la Sesión 11
encontró que el modo SDR en vivo de `dsd-fme` usa un pipeline interno de
muestreo completamente distinto (1.008 MS/s, oversampling 84x) del
pipeline offline (240 kS/s + `iq_to_wav.py`) que se validó una y otra vez
desde la Sesión 7 — el mismo valor de corrección de frecuencia que
sincroniza perfecto offline no sincronizaba NUNCA en modo vivo. Este
diseño usa exclusivamente el pipeline que sí está probado, a costa de
latencia (el tamaño del bloque) en vez de detección instantánea.

Configuración por variable de entorno (todas opcionales, con default al
último valor confirmado — ver docs/operacion-sdr.md para el procedimiento
manual de recalibración cuando haga falta):
    BACKEND_URL              default http://backend:8000 (nombre del
                              servicio en docker-compose.yml — si se corre
                              el script suelto fuera de Docker, hay que
                              pisarlo, ej. http://localhost:8000)
    SDR_FRECUENCIA_HZ         default 159635000 (downlink de la repetidora)
    SDR_SAMPLE_RATE_HZ        default 240000
    SDR_GANANCIA              default 30
    SDR_FREQ_CORR_HZ          default -7600 (última calibración confirmada,
                              deriva con el tiempo — ver docs/operacion-sdr.md)
    SDR_DEVICE_INDEX          default 0
    MALA_ANTENA_STD_UMBRAL    default 1.5 (ver Parte 6 / docs/operacion-sdr.md)
    VENTANA_SIN_DATOS_BLOQUES default 10
    SCRATCH_DIR               default <directorio del script>/bridge_blocks
    LOGS_DIR                  default <directorio del script>/logs

Uso:
    python3 live_presence_bridge.py
"""

import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from dmr_texto_plano_parser import DetectorMensajesDMR

sys.stdout.reconfigure(line_buffering=True)

SCRIPT_DIR = Path(__file__).resolve().parent

# --- Configuración de RF (ver docs/operacion-sdr.md antes de asumir que
# sigue valiendo) ---
FRECUENCIA_HZ = int(os.environ.get("SDR_FRECUENCIA_HZ", "159635000"))
SAMPLE_RATE_HZ = int(os.environ.get("SDR_SAMPLE_RATE_HZ", "240000"))
GANANCIA = os.environ.get("SDR_GANANCIA", "30")
# Última calibración empírica confirmada: -7600 Hz a 159.635 MHz. Deriva
# durante el día — si el estado del SDR queda en "sin_datos" sostenido con
# antena confirmada OK, es la primera sospecha (ver docs/operacion-sdr.md).
FREQ_CORR_HZ = int(os.environ.get("SDR_FREQ_CORR_HZ", "-7600"))
DEVICE_INDEX = os.environ.get("SDR_DEVICE_INDEX", "0")

BLOCK_SECONDS = 12  # duración de cada bloque grabado (10-15s sugerido)
N_SAMPLES = BLOCK_SECONDS * SAMPLE_RATE_HZ

# iq_to_wav.py vive junto a este script (versionado en el repo — antes
# vivía solo en ~/sdr_dmr_test/ en la máquina de investigación).
IQ_TO_WAV_SCRIPT = str(SCRIPT_DIR / "iq_to_wav.py")
SCRATCH_DIR = Path(os.environ.get("SCRATCH_DIR", str(SCRIPT_DIR / "bridge_blocks")))

# Logs crudos de dsd-fme por bloque, uno por archivo — NO se borra nunca
# automáticamente (es lo que permite auditar retroactivamente). Persistir
# como volumen en docker-compose.yml si se quiere conservar entre
# recreaciones del contenedor.
LOGS_DIR = Path(os.environ.get("LOGS_DIR", str(SCRIPT_DIR / "logs")))

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8000")
PRESENCE_ENDPOINT = f"{BACKEND_URL}/api/presence"
AUDIO_EVENTOS_ENDPOINT = f"{BACKEND_URL}/api/audio-eventos"
SDR_STATUS_ENDPOINT = f"{BACKEND_URL}/api/sdr-status"
TELEMETRY_ENDPOINT = f"{BACKEND_URL}/api/telemetry"

# Mensajería de datos en texto plano sobre DMR (ver
# dmr_texto_plano_parser.py e INVESTIGACION_LRRP.md, secciones "🎯 HITO —
# Primera coordenada GPS real capturada" y "🎯 HALLAZGO — El mismo canal
# expone mensajes de texto, no solo GPS"). Reconoce coordenadas GPS (se
# postean a /api/telemetry) y mensajes de texto libre sin forma de
# coordenada (se guardan aparte, ver MENSAJES_INTERCEPTADOS_LOG).
# ⚠️ Mecanismo OPORTUNISTA/forense, no un protocolo soportado:
#   - El camino "icmp_bounce" solo funciona mientras el destinatario NO
#     tenga el puerto UDP escuchando (lo que provoca el rebote que
#     capturamos). Si eso deja de pasar, no hay forma de saberlo desde acá.
#   - El camino "udp_directo" depende de que el destinatario SÍ tenga algo
#     escuchando y el paquete se decodifique sin error.
#   - El camino "fragmentos_reconstruidos" es best-effort sobre datos
#     parciales — puede quedar incompleto o no encontrar nada, sin aviso.
# No tratar ninguno de los tres como un reemplazo confiable de LRRP.
DETECTOR_MENSAJES_DMR = DetectorMensajesDMR()
MENSAJES_INTERCEPTADOS_LOG = LOGS_DIR / "mensajes_interceptados.log"

# Bitácora de audio: bytes de un WAV vacío (solo header, sin frames de
# audio) que escribe dsd-fme con "-w" cuando no decodificó nada en el
# bloque — confirmado empíricamente. Un archivo de este tamaño o menor no
# tiene audio real, no se postea.
AUDIO_WAV_HEADER_BYTES = 44

# Estado del SDR (ver docs/operacion-sdr.md): std de los bytes IQ crudos
# (escala 0-255) por encima de esto se interpreta como antena mal
# conectada o desconectada. Referencia empírica de sesiones anteriores:
# ~0.47-0.60 en recepción normal, ~3+ con antena improvisada/mala (ver
# INVESTIGACION_LRRP.md) — 1.5 queda a mitad de camino entre ambos rangos.
MALA_ANTENA_STD_UMBRAL = float(os.environ.get("MALA_ANTENA_STD_UMBRAL", "1.5"))
# Bloques consecutivos sin NINGÚN sync total (cualquier Color Code) para
# pasar a "sin_datos" — ~10 bloques de 12s+proceso ≈ 2-3 minutos.
VENTANA_SIN_DATOS_BLOQUES = int(os.environ.get("VENTANA_SIN_DATOS_BLOQUES", "10"))

# Mapeo Source ID -> alias conocido, confirmado en INVESTIGACION_LRRP.md
# (sesiones 7-9). Agregar acá cualquier radio nuevo que se identifique.
ALIAS_CONOCIDOS = {
    "1000": "Base Guardia",
    "1001": "Matías",
    "1002": "BVM1002",
}

RATE_LIMIT_SEG = 5
BLOQUES_SIN_SYNC_PARA_AVISO = 5  # ~5 bloques de 12s ≈ 1 minuto sin sync CC=01

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# Cualquier línea de sync DMR, tenga o no Color Code decodificado (incluye
# bursts que fallan CRC/FEC antes de llegar a esa etapa) — nueva métrica
# para diferenciar "hay estructura DMR real en el aire" de "silencio".
SYNC_ANY_RE = re.compile(r"Sync:\s*\+?DMR")
SYNC_CC_RE = re.compile(r"Sync:\s*\+?DMR.*\|\s*Color Code=(\S+)")
# "Group TXI Call" agregado tras una prueba real con la bitácora de audio:
# una transmisión real de Base Guardia (SRC=1000, FID=0x10) no matcheaba
# "Group Call" porque dsd-fme la imprime como "Group TXI Call" (llamada de
# grupo con flag de Transmit Interrupt) — sigue siendo tráfico de voz real
# (confirmado por "Activity Update TS1: Group Voice" en la misma línea de
# log), solo con un FID de fabricante distinto al 0x00 genérico.
SRC_VOZ_RE = re.compile(r"SRC=(\d+).*?(Group Emergency Call|Group TXI Call|Group Call)")
MNIS_SRC_RE = re.compile(r"SRC\(MNIS\):\s*0*(\d+)")
MNIS_ARS_RE = re.compile(r"MNIS ARS")

# Strings confirmados en dmr_pdu.c/dmr_block.c que dsd-fme imprime SOLO si
# reconoce un token real de LRRP/LOCN (ver investigación de código en
# INVESTIGACION_LRRP.md).
LRRP_GPS_RE = re.compile(
    r"LRRP SRC:|MNIS LRRP|MNIS LOCN|Immediate Location Request|Triggered Location"
)

# Mensajes exactos que imprime el binario rtl_sdr cuando no puede abrir el
# dispositivo (confirmado con `strings` sobre el binario real, no
# adivinado) — distingue "desconectado" de una grabación que simplemente
# vino vacía por otra razón transitoria.
RTL_SDR_DESCONECTADO_MARCAS = (
    "Failed to open rtlsdr device",
    "No supported devices found",
    "Failed to open",
)


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
            print(f"  [{etiqueta}] evento={evento} -> POST {resp.status}", flush=True)
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", "replace")
        print(f"  [{etiqueta}] evento={evento} -> ERROR {exc.code}: {detalle}", flush=True)
    except urllib.error.URLError as exc:
        print(f"  [{etiqueta}] evento={evento} -> NO SE PUDO CONECTAR AL BACKEND: {exc.reason}", flush=True)


def enviar_telemetry(radio_id: str, lat: float, lon: float, velocidad_kmh, timestamp_iso: str) -> None:
    """POST a /api/telemetry (ver docs/API.md) — usado por el detector de
    mensajería DMR en texto plano (`dmr_texto_plano_parser.py`) cuando el
    contenido decodificado tiene forma de coordenada. A diferencia de
    /api/presence, `radio_alias` es un campo requerido por el contrato —
    si no hay uno cargado en ALIAS_CONOCIDOS, se manda el propio radio_id
    como alias (mismo comportamiento default que ya aplica el backend en
    /api/presence)."""
    alias = ALIAS_CONOCIDOS.get(radio_id, radio_id)
    payload = {
        "radio_id": radio_id,
        "radio_alias": alias,
        "lat": lat,
        "lon": lon,
        "timestamp": timestamp_iso,
    }
    if velocidad_kmh is not None:
        payload["velocidad"] = velocidad_kmh

    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        TELEMETRY_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            print(f"  [{alias}] telemetría GPS (texto plano DMR) -> POST {resp.status}", flush=True)
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", "replace")
        print(f"  [{alias}] telemetría GPS (texto plano DMR) -> ERROR {exc.code}: {detalle}", flush=True)
    except urllib.error.URLError as exc:
        print(f"  [{alias}] telemetría GPS (texto plano DMR) -> NO SE PUDO CONECTAR AL BACKEND: {exc.reason}", flush=True)


def guardar_mensaje_interceptado(indice: int, ts: str, hallazgo: dict) -> None:
    """Guarda un mensaje de texto interceptado (SIN forma de coordenada,
    por lo tanto no apto para /api/telemetry) en un log aparte —
    `mensajes_interceptados.log`, junto a los logs crudos por bloque. Es
    un hallazgo de seguridad/curiosidad (ver INVESTIGACION_LRRP.md, "🎯
    HALLAZGO — El mismo canal expone mensajes de texto, no solo GPS"), no
    telemetría de posición — no se postea a ningún endpoint del backend."""
    linea = (
        f"[{datetime.now(timezone.utc).isoformat()}] bloque={indice} "
        f"log=bloque_{indice:04d}_{ts}.log mecanismo={hallazgo['mecanismo']} "
        f"radio_id={hallazgo['radio_id']} contacto={hallazgo['radio_id_contacto']} "
        f"crc_error={hallazgo['crc_error']} texto={hallazgo['texto_crudo']!r}\n"
    )
    try:
        with open(MENSAJES_INTERCEPTADOS_LOG, "a") as f:
            f.write(linea)
    except OSError as exc:
        print(f"  ERROR guardando mensaje interceptado: {exc}", flush=True)


def enviar_estado_sdr(status: str, detalle: str) -> None:
    """Postea SIEMPRE (uno por bloque) — el backend decide si eso implica
    un cambio real y solo en ese caso lo emite por WebSocket (ver
    docs/API.md, POST /api/sdr-status)."""
    payload = {
        "status": status,
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
        "detalle": detalle,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        SDR_STATUS_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        print(f"  ERROR posteando estado SDR ({status}): HTTP {exc.code} {exc.read().decode('utf-8', 'replace')}", flush=True)
    except urllib.error.URLError as exc:
        print(f"  ERROR posteando estado SDR ({status}): NO SE PUDO CONECTAR AL BACKEND: {exc.reason}", flush=True)


def _codificar_multipart(campos: dict, archivo_path: Path, nombre_campo_archivo: str) -> tuple[bytes, str]:
    """Arma un cuerpo multipart/form-data a mano (sin depender de `requests`,
    que no es una dependencia de este script — solo librerías estándar +
    numpy/scipy)."""
    boundary = uuid.uuid4().hex
    partes = []

    for clave, valor in campos.items():
        partes.append(f"--{boundary}\r\n".encode())
        partes.append(f'Content-Disposition: form-data; name="{clave}"\r\n\r\n'.encode())
        partes.append(f"{valor}\r\n".encode())

    content_type = mimetypes.guess_type(archivo_path.name)[0] or "application/octet-stream"
    partes.append(f"--{boundary}\r\n".encode())
    partes.append(
        (
            f'Content-Disposition: form-data; name="{nombre_campo_archivo}"; '
            f'filename="{archivo_path.name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
    )
    partes.append(archivo_path.read_bytes())
    partes.append(b"\r\n")
    partes.append(f"--{boundary}--\r\n".encode())

    return b"".join(partes), f"multipart/form-data; boundary={boundary}"


def enviar_audio_evento(radio_id, radio_alias, timestamp_inicio_iso: str, duracion_seg: float, audio_path: Path) -> None:
    campos = {
        "timestamp_inicio": timestamp_inicio_iso,
        "duracion_seg": str(duracion_seg),
    }
    if radio_id is not None:
        campos["radio_id"] = radio_id
    if radio_alias is not None:
        campos["radio_alias"] = radio_alias

    cuerpo, content_type = _codificar_multipart(campos, audio_path, "archivo")
    request = urllib.request.Request(
        AUDIO_EVENTOS_ENDPOINT,
        data=cuerpo,
        headers={"Content-Type": content_type},
        method="POST",
    )
    etiqueta = radio_alias or radio_id or "desconocido"
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            print(f"  [{etiqueta}] audio del bloque -> POST {resp.status}", flush=True)
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", "replace")
        print(f"  [{etiqueta}] audio del bloque -> ERROR {exc.code}: {detalle}", flush=True)
    except urllib.error.URLError as exc:
        print(f"  [{etiqueta}] audio del bloque -> NO SE PUDO CONECTAR AL BACKEND: {exc.reason}", flush=True)


def parsear_bloque(texto: str):
    """Recorre la salida completa de dsd-fme sobre un bloque y devuelve
    (eventos, hubo_sync_cc01, hallazgos_lrrp, total_sync).

    eventos: [(radio_id, evento), ...] para voz/emergencia/ars — solo se
    confía en SRC=/MNIS si viene justo después de un header Color Code=01.

    hallazgos_lrrp: [(radio_id_o_None, linea_completa), ...] — a propósito
    NO se filtra por Color Code=01: un hallazgo de LRRP es tan raro e
    importante que preferimos el riesgo de un falso positivo antes que
    perder uno real por un gate demasiado estricto.

    total_sync: cantidad de líneas "Sync: +DMR" en total, cualquier Color
    Code (incluye bursts que fallan CRC/FEC) — usado para clasificar el
    estado del SDR (ver docs/operacion-sdr.md), independiente de si el
    burst llegó a decodificarse como evento reconocido."""
    eventos = []
    hallazgos_lrrp = []
    ultima_cc = None
    mnis_src_pendiente = None
    hubo_sync_cc01 = False
    total_sync = 0

    for linea_cruda in texto.splitlines():
        linea = strip_ansi(linea_cruda).strip()
        if not linea:
            continue

        if LRRP_GPS_RE.search(linea):
            hallazgos_lrrp.append((mnis_src_pendiente, linea))

        if SYNC_ANY_RE.search(linea):
            total_sync += 1

        m = SYNC_CC_RE.search(linea)
        if m:
            ultima_cc = m.group(1)
            if ultima_cc == "01":
                hubo_sync_cc01 = True
            continue

        if ultima_cc != "01":
            continue

        m = SRC_VOZ_RE.search(linea)
        if m:
            radio_id, tipo_llamada = m.group(1), m.group(2)
            evento = "emergencia" if "Emergency" in tipo_llamada else "voz"
            eventos.append((radio_id, evento))
            continue

        m = MNIS_SRC_RE.search(linea)
        if m:
            mnis_src_pendiente = m.group(1)
            continue

        if MNIS_ARS_RE.search(linea) and mnis_src_pendiente is not None:
            eventos.append((mnis_src_pendiente, "ars"))
            mnis_src_pendiente = None
            continue

    return eventos, hubo_sync_cc01, hallazgos_lrrp, total_sync


def grabar_bloque(iq_path: Path) -> tuple[bool, str | None]:
    """Devuelve (éxito, motivo_si_falló). motivo es "desconectado" solo si
    rtl_sdr reporta explícitamente no haber podido ABRIR el dispositivo
    (mensajes confirmados con `strings` sobre el binario real) — distinto
    de una grabación que simplemente vino vacía o tardó de más."""
    cmd = [
        "rtl_sdr",
        "-f", str(FRECUENCIA_HZ),
        "-s", str(SAMPLE_RATE_HZ),
        "-g", GANANCIA,
        "-d", DEVICE_INDEX,
        "-n", str(N_SAMPLES),
        str(iq_path),
    ]
    try:
        resultado = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, timeout=BLOCK_SECONDS + 20,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"  ERROR grabando bloque con rtl_sdr: {exc}", flush=True)
        return False, "excepcion_subprocess"

    stderr = resultado.stderr or ""
    if any(marca in stderr for marca in RTL_SDR_DESCONECTADO_MARCAS):
        ultima_linea = stderr.strip().splitlines()[-1] if stderr.strip() else "(sin detalle)"
        print(f"  ERROR: rtl_sdr no pudo abrir el dispositivo SDR: {ultima_linea}", flush=True)
        return False, "desconectado"

    if resultado.returncode != 0:
        print(f"  ERROR grabando bloque con rtl_sdr (código {resultado.returncode}): {stderr.strip()[-300:]}", flush=True)
        return False, "error_grabacion"

    ok = iq_path.exists() and iq_path.stat().st_size > 0
    return ok, (None if ok else "archivo_vacio")


def medir_std_iq(iq_path: Path) -> float | None:
    """Desvío estándar de los bytes IQ crudos (escala 0-255) — mismo
    método usado a mano en sesiones de investigación anteriores para
    diagnosticar antena (ver INVESTIGACION_LRRP.md: ~0.47-0.60 en
    recepción normal, ~3+ con antena improvisada/mal conectada). Ahora
    automatizado por bloque para clasificar el estado del SDR (ver
    MALA_ANTENA_STD_UMBRAL / docs/operacion-sdr.md)."""
    try:
        raw = np.fromfile(str(iq_path), dtype=np.uint8)
        if raw.size == 0:
            return None
        return float(np.std(raw))
    except OSError as exc:
        print(f"  ERROR midiendo std del IQ crudo: {exc}", flush=True)
        return None


def convertir_bloque(iq_path: Path, wav_path: Path) -> bool:
    cmd = [
        "python3", IQ_TO_WAV_SCRIPT,
        str(iq_path), str(wav_path), str(SAMPLE_RATE_HZ), str(FREQ_CORR_HZ),
    ]
    try:
        subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"  ERROR convirtiendo bloque con iq_to_wav.py: {exc}", flush=True)
        return False
    return wav_path.exists() and wav_path.stat().st_size > 0


def decodificar_bloque(wav_path: Path, audio_out_path: Path) -> str:
    # "-w <file>": vuelca a un WAV el audio sintetizado/decodificado de todo
    # el bloque. Si el bloque no tuvo voz, el archivo queda con solo el
    # header (44 bytes), sin frames — se descarta después sin postear
    # (AUDIO_WAV_HEADER_BYTES).
    cmd = [
        "dsd-fme", "-fs", "-i", str(wav_path), "-s", "48000", "-Z", "-o", "null",
        "-w", str(audio_out_path),
    ]
    try:
        resultado = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=30,
        )
        return resultado.stdout
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"  ERROR corriendo dsd-fme sobre el bloque: {exc}", flush=True)
        return ""


def medir_duracion_wav(wav_path: Path) -> float:
    """Duración real del audio sintetizado por dsd-fme (no la del bloque
    grabado): "-w" solo escribe muestras cuando efectivamente decodifica
    voz, sin rellenar con silencio el resto del bloque. Si por lo que sea
    no se puede leer el WAV, se cae de vuelta a BLOCK_SECONDS en vez de
    romper el posteo del clip."""
    try:
        with wave.open(str(wav_path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            return frames / rate if rate else float(BLOCK_SECONDS)
    except (wave.Error, OSError) as exc:
        print(f"  ERROR midiendo duración real del audio ({wav_path.name}): {exc}", flush=True)
        return float(BLOCK_SECONDS)


def guardar_log_crudo(indice: int, ts: str, salida: str) -> None:
    """Guarda el texto crudo completo de dsd-fme para este bloque, con
    separadores claros, para poder re-auditar después."""
    log_path = LOGS_DIR / f"bloque_{indice:04d}_{ts}.log"
    try:
        with open(log_path, "w") as f:
            f.write(f"=== Bloque {indice} — {datetime.now().isoformat()} ===\n")
            f.write(salida)
            f.write(f"\n=== fin bloque {indice} ===\n")
    except OSError as exc:
        print(f"  ERROR guardando log crudo del bloque {indice}: {exc}", flush=True)


def procesar_un_bloque(indice: int, ultimo_post: dict) -> dict:
    """Procesa un bloque completo. Devuelve un dict para que main() lleve
    el estado entre bloques (aviso de recalibración de PPM, clasificación
    del estado del SDR):
      - desconectado: True si rtl_sdr no pudo abrir el dispositivo (no se
        pudo grabar nada, ni siquiera medir std).
      - hubo_sync_cc01: hubo al menos un burst con Color Code=01 real.
      - total_sync: cantidad total de líneas "Sync: +DMR" (cualquier CC).
      - std: desvío estándar de los bytes IQ crudos del bloque, o None si
        no se pudo grabar/leer."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    iq_path = SCRATCH_DIR / f"block_{ts}.cu8"
    wav_path = SCRATCH_DIR / f"block_{ts}.wav"
    audio_path = SCRATCH_DIR / f"block_{ts}_audio.wav"
    # Aproximación de "cuándo empezó la transmisión" para la bitácora de
    # audio: el inicio de la grabación de este bloque.
    bloque_inicio_iso = datetime.now(timezone.utc).astimezone().isoformat()

    resultado_vacio = {"desconectado": False, "hubo_sync_cc01": False, "total_sync": 0, "std": None}

    t0 = time.monotonic()
    try:
        grabado, motivo = grabar_bloque(iq_path)
        if motivo == "desconectado":
            return {**resultado_vacio, "desconectado": True}
        if not grabado:
            print(f"[bloque {indice}] grabación falló o vino vacía, sigo con el próximo.", flush=True)
            return resultado_vacio

        std = medir_std_iq(iq_path)

        if not convertir_bloque(iq_path, wav_path):
            print(f"[bloque {indice}] conversión falló, sigo con el próximo.", flush=True)
            return {**resultado_vacio, "std": std}

        salida = decodificar_bloque(wav_path, audio_path)
        guardar_log_crudo(indice, ts, salida)
        eventos, hubo_sync, hallazgos_lrrp, total_sync = parsear_bloque(salida)
        duracion = time.monotonic() - t0
        std_txt = f"{std:.2f}" if std is not None else "n/d"

        if hallazgos_lrrp:
            print("\n" + "#" * 70, flush=True)
            print(f"### 🚨 POSIBLE LRRP/GPS DETECTADO — BLOQUE {indice} 🚨", flush=True)
            print("#" * 70, flush=True)
            for radio_id, linea in hallazgos_lrrp:
                print(f"### radio_id={radio_id or 'DESCONOCIDO'} | línea: {linea}", flush=True)
            print(f"### log crudo completo guardado en: {LOGS_DIR.name}/bloque_{indice:04d}_{ts}.log", flush=True)
            print("#" * 70 + "\n", flush=True)
            for radio_id, _linea in hallazgos_lrrp:
                if radio_id is None:
                    print("  (radio_id desconocido para este hallazgo, no se postea — revisar el log crudo a mano)", flush=True)
                    continue
                ahora = time.monotonic()
                if ahora - ultimo_post.get(radio_id, 0) < RATE_LIMIT_SEG:
                    print(f"  [{radio_id}] evento=gps -> rate-limited, no se re-postea", flush=True)
                    continue
                ultimo_post[radio_id] = ahora
                enviar_presencia(radio_id, "gps")

        hallazgos_dmr = DETECTOR_MENSAJES_DMR.procesar_bloque(salida)
        for hallazgo in hallazgos_dmr:
            if hallazgo["duplicado"]:
                print(
                    f"  [{hallazgo['mecanismo']}] radio_id={hallazgo['radio_id']} -> ya visto por otro "
                    "mecanismo/bloque reciente, no se re-postea ni re-loguea (dedup)",
                    flush=True,
                )
                continue

            if not hallazgo["completo"]:
                if hallazgo["campos"] or hallazgo["texto_crudo"].strip():
                    print(
                        f"  [📨 mensaje interceptado] mecanismo={hallazgo['mecanismo']} "
                        f"radio_id={hallazgo['radio_id']} contacto={hallazgo['radio_id_contacto']} "
                        f"(sin forma de coordenada, no se postea a /api/telemetry) "
                        f"texto={hallazgo['texto_crudo']!r}",
                        flush=True,
                    )
                    guardar_mensaje_interceptado(indice, ts, hallazgo)
                else:
                    print(
                        f"  [{hallazgo['mecanismo']}] hallazgo vacío de radio_id={hallazgo['radio_id']} "
                        "(sin campos ni texto, ej. paquete keepalive) — se ignora",
                        flush=True,
                    )
                continue

            frags = ""
            if hallazgo["bloques_totales"] is not None:
                frags = f" bloques={hallazgo['bloques_capturados']}/{hallazgo['bloques_totales']}"
            print("\n" + "#" * 70, flush=True)
            print(f"### 🎯 GPS (texto plano DMR, mecanismo={hallazgo['mecanismo']}) — BLOQUE {indice} 🎯", flush=True)
            print("#" * 70, flush=True)
            print(
                f"### radio_id={hallazgo['radio_id']} (vía contacto {hallazgo['radio_id_contacto']}) "
                f"lat={hallazgo['lat']} lon={hallazgo['lon']} vel={hallazgo['velocidad_kmh']}{frags}",
                flush=True,
            )
            print(
                f"### reconstruido_cruzando_bloques={hallazgo['reconstruido_cruzando_bloques']} "
                f"crc_error_residual={hallazgo['crc_error']}",
                flush=True,
            )
            print(f"### log crudo completo guardado en: {LOGS_DIR.name}/bloque_{indice:04d}_{ts}.log", flush=True)
            print("#" * 70 + "\n", flush=True)
            enviar_telemetry(
                hallazgo["radio_id"], hallazgo["lat"], hallazgo["lon"],
                hallazgo["velocidad_kmh"], bloque_inicio_iso,
            )

        if not eventos:
            estado = "sync CC=01 pero sin evento reconocido" if hubo_sync else "sin actividad"
            print(
                f"[bloque {indice}] {estado} ({duracion:.1f}s, std={std_txt}, syncs={total_sync}). "
                "Normal en silencio de radio.",
                flush=True,
            )
        else:
            print(
                f"[bloque {indice}] {len(eventos)} evento(s) detectado(s) "
                f"({duracion:.1f}s, std={std_txt}, syncs={total_sync}):",
                flush=True,
            )
            vistos = set()
            for radio_id, evento in eventos:
                clave = (radio_id, evento)
                if clave in vistos:
                    continue
                vistos.add(clave)
                ahora = time.monotonic()
                if ahora - ultimo_post.get(radio_id, 0) < RATE_LIMIT_SEG:
                    print(f"  [{radio_id}] evento={evento} -> rate-limited, no se re-postea", flush=True)
                    continue
                ultimo_post[radio_id] = ahora
                enviar_presencia(radio_id, evento)

        # Bitácora de audio: todo evento de voz o emergencia detectado en el
        # bloque (Base Guardia incluida, sin filtrar por equipo — ambos son
        # llamadas de voz reales, con AMBE; "ars" es solo un registro de
        # datos, sin audio). Un bloque = un clip.
        eventos_voz = [(radio_id, evento) for radio_id, evento in eventos if evento in ("voz", "emergencia")]
        if eventos_voz and audio_path.exists() and audio_path.stat().st_size > AUDIO_WAV_HEADER_BYTES:
            radio_id_repr, _ = eventos_voz[0]
            alias_repr = ALIAS_CONOCIDOS.get(radio_id_repr)
            duracion_real = medir_duracion_wav(audio_path)
            enviar_audio_evento(radio_id_repr, alias_repr, bloque_inicio_iso, duracion_real, audio_path)

        return {"desconectado": False, "hubo_sync_cc01": hubo_sync, "total_sync": total_sync, "std": std}
    finally:
        # Limpieza del bloque, pase lo que pase (no acumular archivos
        # IQ/WAV/audio — el log crudo de texto, guardado arriba, NO se
        # borra; el audio "permanente" ya quedó en el backend, si
        # correspondía guardarlo).
        iq_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)


def main() -> None:
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Bloques de {BLOCK_SECONDS}s a {FRECUENCIA_HZ/1e6} MHz, freq_corr={FREQ_CORR_HZ:+d} Hz, gain={GANANCIA}", flush=True)
    print(f"POSTeando eventos a: {PRESENCE_ENDPOINT}", flush=True)
    print(f"Estado del SDR a: {SDR_STATUS_ENDPOINT}", flush=True)
    print(f"Logs crudos por bloque en: {LOGS_DIR}", flush=True)
    print("Ctrl+C para cortar.\n", flush=True)

    ultimo_post = {}

    # Aviso de recalibración de PPM (ya existía) — basado específicamente
    # en Color Code=01 (bursts realmente decodificados).
    bloques_sin_sync_cc01 = 0
    aviso_emitido = False

    # Clasificación del estado del SDR (nuevo) — basado en total_sync
    # (cualquier Color Code) con histéresis: una vez "ok", se mantiene así
    # hasta acumular VENTANA_SIN_DATOS_BLOQUES consecutivos en cero, para
    # no alternar en cada bloque individual de silencio normal entre
    # transmisiones. Arranca en "sin_datos" (pesimista) hasta confirmar
    # algo, salvo que el primer bloque ya muestre mala antena.
    bloques_sin_sync_total = 0
    estado_sdr = "sin_datos"
    estado_sdr_impreso = None

    indice = 0

    try:
        while True:
            indice += 1
            resultado = procesar_un_bloque(indice, ultimo_post)

            if resultado["desconectado"]:
                estado_sdr = "desconectado"
                enviar_estado_sdr(estado_sdr, "rtl_sdr no pudo abrir el dispositivo SDR")
                if estado_sdr != estado_sdr_impreso:
                    print(f"\n🔴 Estado SDR: {estado_sdr} — rtl_sdr no pudo abrir el dispositivo.\n", flush=True)
                    estado_sdr_impreso = estado_sdr
                time.sleep(5)  # evitar loop apretado reintentando un dispositivo ausente
                continue

            hubo_sync_cc01 = resultado["hubo_sync_cc01"]
            total_sync = resultado["total_sync"]
            std = resultado["std"]

            if hubo_sync_cc01:
                bloques_sin_sync_cc01 = 0
                aviso_emitido = False
            else:
                bloques_sin_sync_cc01 += 1
                if bloques_sin_sync_cc01 >= BLOQUES_SIN_SYNC_PARA_AVISO and not aviso_emitido:
                    print(
                        f"\n⚠️  {bloques_sin_sync_cc01} bloques seguidos sin ningún sync con Color Code=01. "
                        "Puede hacer falta recalibrar SDR_FREQ_CORR_HZ (ver docs/operacion-sdr.md) — "
                        "verificar antena conectada antes de asumir que es la calibración.\n",
                        flush=True,
                    )
                    aviso_emitido = True

            std_txt = f"std={std:.2f}" if std is not None else "std=n/d"
            if std is not None and std > MALA_ANTENA_STD_UMBRAL:
                estado_sdr = "mala_antena"
                detalle = f"{std_txt} (umbral={MALA_ANTENA_STD_UMBRAL}), posible antena mal conectada o desconectada"
            elif total_sync > 0:
                bloques_sin_sync_total = 0
                estado_sdr = "ok"
                detalle = f"{std_txt} normal, {total_sync} sync(s) DMR en este bloque"
            else:
                bloques_sin_sync_total += 1
                detalle = f"{std_txt} normal, 0 syncs en los últimos {bloques_sin_sync_total} bloque(s)"
                if bloques_sin_sync_total >= VENTANA_SIN_DATOS_BLOQUES:
                    estado_sdr = "sin_datos"
                # si todavía no llegó al umbral, se mantiene el estado
                # anterior a propósito (histéresis) — no flapear por cada
                # bloque individual de silencio normal entre transmisiones.

            enviar_estado_sdr(estado_sdr, detalle)
            if estado_sdr != estado_sdr_impreso:
                emoji = {"ok": "🟢", "sin_datos": "🟡", "mala_antena": "🟠", "desconectado": "🔴"}.get(estado_sdr, "")
                print(f"\n{emoji} Estado SDR cambió a: {estado_sdr} — {detalle}\n", flush=True)
                estado_sdr_impreso = estado_sdr

    except KeyboardInterrupt:
        print("\nCortado por el usuario.", flush=True)


if __name__ == "__main__":
    main()
