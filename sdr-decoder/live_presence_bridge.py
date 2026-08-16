#!/usr/bin/env python3
"""
VERSIÓN DE DESARROLLO — corre suelto en el host con el Python del sistema,
FUERA de Docker. sdr-decoder todavía es un placeholder en docker-compose.yml
(ver README.md de esa carpeta) — este script es el puente manual que se usa
mientras tanto para probar el circuito real SDR -> backend -> frontend.
Cuando sdr-decoder se containerice de verdad, esta lógica se reemplaza por
la implementación real de decodificación (a cargo de Julián, ver
INVESTIGACION_LRRP.md), no por este script.

Qué hace (reescrito en la Sesión 12 — ver INVESTIGACION_LRRP.md):
1. Graba un bloque corto (BLOCK_SECONDS) de IQ crudo con `rtl_sdr`,
   escuchando el downlink de la repetidora (159.635 MHz).
2. Convierte ese bloque a WAV demodulado con `iq_to_wav.py`
   (~/sdr_dmr_test/), aplicando la corrección de frecuencia confirmada.
3. Corre `dsd-fme` en modo ARCHIVO (no en vivo) sobre ese WAV.
4. Parsea la salida completa del bloque buscando bursts válidos
   (Color Code=01) de voz, emergencia, ARS, o LRRP/GPS (ver Sesión 16).
5. Postea a /api/presence lo que se haya detectado (con rate-limit de 5s
   por radio_id).
6. Guarda a disco el texto crudo completo de `dsd-fme` de este bloque
   (ver Sesión 16 — corrige el punto ciego de auditoría encontrado en el
   re-análisis retroactivo: antes se descartaba en memoria y no había
   forma de revisar qué pasó realmente en cada bloque).
7. Borra los archivos temporales de IQ/WAV del bloque y repite
   indefinidamente (los logs crudos de texto NO se borran, ver punto 6).

Sesión 16 — detección de LRRP/GPS agregada: hasta la sesión 15 las regex
solo buscaban voz/emergencia/ARS — nunca los strings que `dsd-fme` imprime
si reconoce un token real de LRRP (`dmr_lrrp()` en `dmr_pdu.c`, ver
INVESTIGACION_LRRP.md, investigación de código). Ahora también se busca
"LRRP SRC:", "MNIS LRRP", "MNIS LOCN", "Immediate Location Request", y
"Triggered Location" en CADA línea del bloque (sin el filtro de Color
Code=01 que se usa para voz/ARS — a diferencia de esos, un hallazgo de
LRRP es tan raro e importante que preferimos el riesgo de un falso
positivo antes que perder uno real por un gate demasiado estricto).

Bitácora de audio agregada: cuando un bloque contiene al menos un evento de
voz o emergencia, además del POST a /api/presence (sin reemplazarlo) se
guarda el audio decodificado de todo el bloque con "-w <file>" de dsd-fme
(confirmado por --help; NO es el mismo mecanismo que -P/Per-Call, que
maneja sus propios nombres de archivo) y se sube a POST /api/audio-eventos
con la metadata (radio_id/alias, inicio aproximado, duración aproximada del
bloque). Si el bloque mezcla voz de más de un radio_id, se usa el primero
como metadata representativa — el audio guardado es el del bloque
completo, no separado por hablante (requeriría el modo Per-Call de
dsd-fme, fuera de alcance de esta versión).

Por qué este diseño y no `dsd-fme -i rtl:...` en vivo (versión anterior):
la Sesión 11 encontró que el modo SDR en vivo de `dsd-fme` usa un pipeline
interno de muestreo completamente distinto (1.008 MS/s, oversampling 84x)
del pipeline offline (240 kS/s + `iq_to_wav.py`) que se validó una y otra
vez desde la Sesión 7 — el mismo valor de corrección de frecuencia que
sincroniza perfecto offline no sincronizaba NUNCA en modo vivo, con
transmisiones reales confirmadas de sobra. Este rediseño usa exclusivamente
el pipeline que sí está probado, a costa de latencia (el tamaño del bloque,
ver BLOCK_SECONDS) en vez de detección instantánea.

Requiere: `rtl_sdr` y `dsd-fme` en el PATH, `~/sdr_dmr_test/iq_to_wav.py`
presente (numpy + scipy instalados), dongle RTL-SDR libre, y el backend de
tracking-GPS-VHF corriendo (docker compose up -d backend).

Uso:
    python3 live_presence_bridge.py
    BACKEND_URL=http://localhost:8000 python3 live_presence_bridge.py

IMPORTANTE — calibración de frecuencia:
FREQ_CORR_HZ de abajo es el valor más reciente confirmado (Sesión 16,
sweep fino sobre grabación real: -7600 Hz a 159.635 MHz, 174 syncs / 165
Color Code=01 sobre una transmisión real — mejor que -8000, -8200, -7800
y -7500 Hz probados en el mismo sweep). Documentado que este offset
deriva durante el día (fue -6504, -6700, -7000, -7800, -7500 y ahora
-7600 Hz en sesiones/momentos consecutivos) — si después de varios
minutos ningún bloque logra sync con Color Code=01 (este script avisa
solo con un ⚠️), hace falta recalibrar: grabar ~40-90s de IQ crudo con
`rtl_sdr` durante una transmisión real y barrer valores de `freq_corr`
con `iq_to_wav.py` + `dsd-fme` en modo archivo hasta encontrar el que
maximice syncs reales, igual que en sesiones anteriores. Antes de
sospechar de la calibración, verificar que la antena esté conectada (la
Sesión 11 y la Sesión 16 perdieron tiempo en eso).
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
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

# --- Configuración de RF (revisar INVESTIGACION_LRRP.md antes de asumir) ---
FRECUENCIA_HZ = 159635000  # downlink de la repetidora
SAMPLE_RATE_HZ = 240000  # mismo usado en todas las grabaciones de investigación
GANANCIA = "30"  # nominal, misma usada en todas las grabaciones de investigación
# Última calibración empírica confirmada (Sesión 16): -7600 Hz a 159.635 MHz,
# NO asumir que sigue valiendo la próxima sesión (ver docstring).
FREQ_CORR_HZ = -7600
DEVICE_INDEX = "0"

BLOCK_SECONDS = 12  # duración de cada bloque grabado (10-15s sugerido)
N_SAMPLES = BLOCK_SECONDS * SAMPLE_RATE_HZ

# Scripts/binarios externos al repo (ver docstring — dev-only, paths fijos
# a la máquina de investigación, no portables).
IQ_TO_WAV_SCRIPT = str(Path.home() / "sdr_dmr_test" / "iq_to_wav.py")
SCRATCH_DIR = Path.home() / "sdr_dmr_test" / "bridge_blocks"

# Sesión 16: logs crudos de dsd-fme por bloque, uno por archivo — a
# diferencia de SCRATCH_DIR (IQ/WAV, se borran siempre), esto NO se borra
# nunca automáticamente, es justamente lo que faltaba para poder auditar
# retroactivamente. En una corrida muy larga esto acumula un archivo por
# bloque (~1 cada 14s) — sin limpieza automática a propósito, revisar y
# limpiar a mano si hace falta liberar espacio.
LOGS_DIR = Path(__file__).resolve().parent / "logs_sesion16"

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
PRESENCE_ENDPOINT = f"{BACKEND_URL}/api/presence"
AUDIO_EVENTOS_ENDPOINT = f"{BACKEND_URL}/api/audio-eventos"

# Bitácora de audio: bytes de un WAV vacío (solo header, sin frames de
# audio) que escribe dsd-fme con "-w" cuando no decodificó nada en el
# bloque — confirmado empíricamente (ver resumen de la sesión que agregó
# esto). Un archivo de este tamaño o menor no tiene audio real, no se
# postea.
AUDIO_WAV_HEADER_BYTES = 44

# Mapeo Source ID -> alias conocido, confirmado en INVESTIGACION_LRRP.md
# (sesiones 7-9). Agregar acá cualquier radio nuevo que se identifique.
ALIAS_CONOCIDOS = {
    "1000": "Base Guardia",
    "1001": "Matías",
    "1002": "BVM1002",
}

RATE_LIMIT_SEG = 5
BLOQUES_SIN_SYNC_PARA_AVISO = 5  # ~5 bloques de 12s ≈ 1 minuto sin ningún sync

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
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

# Sesión 16 — strings confirmados en dmr_pdu.c/dmr_block.c que dsd-fme
# imprime SOLO si reconoce un token real de LRRP/LOCN (ver investigación de
# código en INVESTIGACION_LRRP.md). Nunca se buscaron antes de esta sesión.
LRRP_GPS_RE = re.compile(
    r"LRRP SRC:|MNIS LRRP|MNIS LOCN|Immediate Location Request|Triggered Location"
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


def _codificar_multipart(campos: dict, archivo_path: Path, nombre_campo_archivo: str) -> tuple[bytes, str]:
    """Arma un cuerpo multipart/form-data a mano (sin depender de `requests`,
    que no es una dependencia de este script — ver docstring del módulo,
    solo librerías estándar)."""
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
    (eventos, hubo_sync_cc01, hallazgos_lrrp).

    eventos: [(radio_id, evento), ...] para voz/emergencia/ars — misma
    lógica de estado secuencial (solo confiar en SRC=/MNIS si viene justo
    después de un header Color Code=01) que la versión en vivo anterior,
    ahora sobre texto ya completo en vez de una cola de líneas en tiempo
    real.

    hallazgos_lrrp: [(radio_id_o_None, linea_completa), ...] para
    LRRP/GPS — Sesión 16. A propósito NO se filtra por Color Code=01 como
    el resto: un hallazgo de LRRP es tan raro e importante que preferimos
    el riesgo de un falso positivo antes que perder uno real por un gate
    demasiado estricto."""
    eventos = []
    hallazgos_lrrp = []
    ultima_cc = None
    mnis_src_pendiente = None
    hubo_sync_cc01 = False

    for linea_cruda in texto.splitlines():
        linea = strip_ansi(linea_cruda).strip()
        if not linea:
            continue

        if LRRP_GPS_RE.search(linea):
            hallazgos_lrrp.append((mnis_src_pendiente, linea))

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

    return eventos, hubo_sync_cc01, hallazgos_lrrp


def grabar_bloque(iq_path: Path) -> bool:
    """True si rtl_sdr terminó ok y el archivo tiene contenido."""
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
        subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=BLOCK_SECONDS + 20, check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"  ERROR grabando bloque con rtl_sdr: {exc}", flush=True)
        return False
    return iq_path.exists() and iq_path.stat().st_size > 0


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
    # el bloque (confirmado en "dsd-fme --help", no asumido — ver resumen).
    # Si el bloque no tuvo voz, el archivo queda con solo el header (44
    # bytes), sin frames — se descarta después sin postear (AUDIO_WAV_HEADER_BYTES).
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


def guardar_log_crudo(indice: int, ts: str, salida: str) -> None:
    """Sesión 16 — guarda el texto crudo completo de dsd-fme para este
    bloque, con separadores claros, para poder re-auditar después (esto es
    justamente lo que faltaba antes de esta sesión, ver
    INVESTIGACION_LRRP.md, re-análisis retroactivo)."""
    log_path = LOGS_DIR / f"bloque_{indice:04d}_{ts}.log"
    try:
        with open(log_path, "w") as f:
            f.write(f"=== Bloque {indice} — {datetime.now().isoformat()} ===\n")
            f.write(salida)
            f.write(f"\n=== fin bloque {indice} ===\n")
    except OSError as exc:
        print(f"  ERROR guardando log crudo del bloque {indice}: {exc}", flush=True)


def procesar_un_bloque(indice: int, ultimo_post: dict) -> bool:
    """Devuelve True si hubo sync con Color Code=01 en este bloque (para
    el conteo de bloques consecutivos sin sync)."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    iq_path = SCRATCH_DIR / f"block_{ts}.cu8"
    wav_path = SCRATCH_DIR / f"block_{ts}.wav"
    audio_path = SCRATCH_DIR / f"block_{ts}_audio.wav"
    # Aproximación de "cuándo empezó la transmisión" para la bitácora de
    # audio: el inicio de la grabación de este bloque (no se sabe el
    # instante exacto dentro del bloque en que arrancó a hablar realmente).
    bloque_inicio_iso = datetime.now(timezone.utc).astimezone().isoformat()

    t0 = time.monotonic()
    try:
        if not grabar_bloque(iq_path):
            print(f"[bloque {indice}] grabación falló o vino vacía, sigo con el próximo.", flush=True)
            return False

        if not convertir_bloque(iq_path, wav_path):
            print(f"[bloque {indice}] conversión falló, sigo con el próximo.", flush=True)
            return False

        salida = decodificar_bloque(wav_path, audio_path)
        guardar_log_crudo(indice, ts, salida)
        eventos, hubo_sync, hallazgos_lrrp = parsear_bloque(salida)
        duracion = time.monotonic() - t0

        if hallazgos_lrrp:
            print("\n" + "#" * 70, flush=True)
            print(f"### 🚨 POSIBLE LRRP/GPS DETECTADO — BLOQUE {indice} 🚨", flush=True)
            print("#" * 70, flush=True)
            for radio_id, linea in hallazgos_lrrp:
                print(f"### radio_id={radio_id or 'DESCONOCIDO'} | línea: {linea}", flush=True)
            print(f"### log crudo completo guardado en: logs_sesion16/bloque_{indice:04d}_{ts}.log", flush=True)
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

        if not eventos:
            estado = "sync CC=01 pero sin evento reconocido" if hubo_sync else "sin actividad"
            print(f"[bloque {indice}] {estado} ({duracion:.1f}s de procesamiento). Normal en silencio de radio.", flush=True)
        else:
            print(f"[bloque {indice}] {len(eventos)} evento(s) detectado(s) ({duracion:.1f}s de procesamiento):", flush=True)
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
        # datos, sin audio). Un bloque = un clip: si hubo más de un
        # radio_id/evento distinto mezclado en el mismo bloque, se usa el
        # primero como metadata representativa, pero el audio guardado es el
        # del bloque completo (mismo criterio de granularidad ya aceptado
        # para transmisiones largas partidas en 2+ bloques).
        eventos_voz = [(radio_id, evento) for radio_id, evento in eventos if evento in ("voz", "emergencia")]
        if eventos_voz and audio_path.exists() and audio_path.stat().st_size > AUDIO_WAV_HEADER_BYTES:
            radio_id_repr, _ = eventos_voz[0]
            alias_repr = ALIAS_CONOCIDOS.get(radio_id_repr)
            enviar_audio_evento(radio_id_repr, alias_repr, bloque_inicio_iso, BLOCK_SECONDS, audio_path)

        return hubo_sync
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
    print(f"Logs crudos por bloque en: {LOGS_DIR}", flush=True)
    print("Ctrl+C para cortar.\n", flush=True)

    ultimo_post = {}
    bloques_sin_sync = 0
    aviso_emitido = False
    indice = 0

    try:
        while True:
            indice += 1
            hubo_sync = procesar_un_bloque(indice, ultimo_post)

            if hubo_sync:
                bloques_sin_sync = 0
                aviso_emitido = False
            else:
                bloques_sin_sync += 1
                if bloques_sin_sync >= BLOQUES_SIN_SYNC_PARA_AVISO and not aviso_emitido:
                    print(
                        f"\n⚠️  {bloques_sin_sync} bloques seguidos sin ningún sync con Color Code=01. "
                        "Puede hacer falta recalibrar FREQ_CORR_HZ (ver INVESTIGACION_LRRP.md) — "
                        "verificar antena conectada antes de asumir que es la calibración.\n",
                        flush=True,
                    )
                    aviso_emitido = True

    except KeyboardInterrupt:
        print("\nCortado por el usuario.", flush=True)


if __name__ == "__main__":
    main()
