#!/usr/bin/env python3
"""
Parser de mensajería de datos en texto plano sobre DMR.

Antes se llamaba `baofeng_gps_parser.py`. Se renombró porque el alcance
dejó de ser "GPS de un Baofeng UV-32" para ser más general: **cualquier
mensaje de datos DMR tipo `SAP 04 [IP Based]` sobre el puerto UDP 4007,
de cualquier equipo, sea una coordenada GPS o un texto libre.** El
mecanismo se descubrió con el Baofeng UV-32 (`radio_id 1`, ver
INVESTIGACION_LRRP.md, "🎯 HITO — Primera coordenada GPS real capturada"),
pero se confirmó que no es exclusivo de GPS ni de ese modelo con el
hallazgo "Test123" (mensaje de texto Motorola→Baofeng, sección "🎯
HALLAZGO — El mismo canal expone mensajes de texto, no solo GPS").

Reconoce CUATRO formas de encontrar contenido de posición/texto:

  (a) "icmp_bounce" — el paquete UDP original rebota como ICMP
      "Destination Unreachable — Port Unreachable" porque el destinatario
      no tiene el puerto escuchando (mecanismo del hito de GPS,
      radio_id 1 → 1007). `dsd-fme` sí llega a consolidar el paquete en
      un resumen "Multi Block PDU Message". Contenido: texto UTF-16LE
      ("Lat:"/"Long:"/"Speed:").
  (b) "udp_directo" — el paquete UDP llega derecho, sin rebotar, porque
      el destinatario SÍ tiene algo escuchando y responde (mecanismo del
      hallazgo "Test123", radio_id 1001 → 1). También consolidado por
      `dsd-fme`. Mismo formato de texto que (a).
  (c) "fragmentos_reconstruidos" — con señal marginal, `dsd-fme` a veces
      NO llega a consolidar el resumen y solo deja los bloques sueltos
      del PDU en el log. Este módulo los recolecta y concatena a mano
      (ver `reconstruir_fragmentos_sueltos`). Mismo formato de texto que
      (a)/(b), header `SAP 04 [IP Based]` + `Confirmed Delivery`.
  (d) "nmea_beacon" — NUEVO: el beacon GPS automático y periódico del
      Baofeng UV-32 (función "APRS" del handy, no el "Send" manual de
      (a)/(b)/(c)). Usa un header DMR completamente distinto —
      `SAP 03 [UDP Comp]` (IP comprimido nativo de DMR, no un paquete
      IPv4 normal) + `Unconfirmed Delivery` — y el contenido no es el
      texto UTF-16LE propio de este proyecto sino una sentencia **NMEA
      estándar `$GPRMC`** en ASCII plano, el mismo formato que usa
      cualquier receptor GPS. Ver `extraer_beacons_nmea`.

⚠️ Sigue siendo forense y oportunista, no un protocolo soportado:
  - (a) depende de que el destinatario rechace el paquete.
  - (b) depende de que el destinatario SÍ responda — es decir, (a) y (b)
    son mutuamente excluyentes para un mismo envío, pero cuál de las dos
    ocurre depende del equipo de destino, no de nada que controlemos.
  - (c) es best-effort: `dsd-fme` no imprime ningún número de fragmento
    por bloque de continuación (se confirmó revisando el log de
    referencia del hallazgo de la coordenada nueva — no hay campo FSN ni
    DBSN visible por bloque, solo un total declarado en el header). Por
    eso NO se reordenan fragmentos por número de secuencia (no existe tal
    campo en este texto) — se asume que el orden de aparición en el log
    es el orden real de transmisión (válido: `dsd-fme` procesa el WAV en
    una sola pasada secuencial, no hay reordenamiento posible dentro de
    un mismo archivo). Lo que sí puede pasar es que falten bloques
    enteros (nunca se imprimen si no sincronizan) — eso se refleja en
    `bloques_capturados` vs `bloques_totales`, sin asumir dónde cayó el
    hueco.
  - (d) no depende de rebote ni de nada excepcional — es tráfico
    periódico normal (cada ~30s, configurable en el handy). El único
    riesgo real es la calidad de señal (mismo problema que (c)).
"""

from __future__ import annotations

import re
from collections import deque

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
HEX_LINEA_RE = re.compile(r"^[0-9A-Fa-f]+$")
MULTIBLOCK_HEADER_RE = re.compile(r"^Slot (\d+) - Multi Block PDU Message$")

# En el texto UTF-16LE decodificado, un caracter se considera "válido" si
# su byte alto es 0x00 (osea, un code point Latin-1) y su byte bajo es
# imprimible o uno de los símbolos ya vistos en capturas reales (° y ").
_BYTE_BAJO_VALIDO_RE = re.compile(rb"^[\x20-\x7e\xb0]$")

DMS_RE = re.compile(r"(?P<deg>\d+)°\s*(?P<min>\d+)'\s*(?P<sec>[\d.]+)")
CAMPO_ETIQUETA_RE = re.compile(r"^[A-Za-z][A-Za-z ]*:$")

# Línea que arma dsd-fme con cada byte entre corchetes: "[43][4E][00]...".
PDU_PAYLOAD_RE = re.compile(r"DMR PDU Payload\s*((?:\[[0-9A-Fa-f]{2}\])+)")
DATA_HEADER_RE = re.compile(
    r"Data Header - Indiv - Confirmed Delivery.*Source: (\d+) Target: (\d+)"
)
BLOCKS_RE = re.compile(r"BLOCKS (\d+)")
TERMINATOR_RE = re.compile(r"Data Terminator \(TD_LC\)")
LIMITE_SECCION_RE = re.compile(r"^(Preamble CSBK|Slot \d+ Data Header)")

VENTANA_DEDUP_BLOQUES = 5  # ~1 minuto a 12-14s por bloque


def strip_ansi(linea: str) -> str:
    return ANSI_RE.sub("", linea)


def _radio_id_desde_ip(ip: bytes) -> str:
    """El esquema de direccionamiento visto en las capturas usa los dos
    últimos octetos de la IP como el radio_id DMR en hex (ej. 12.0.3.239 =
    0x03EF = 1007; 12.0.0.1 = 0x0001 = 1) — confirmado cruzando contra los
    Source/Target de los headers CSBK del mismo burst."""
    return str((ip[2] << 8) | ip[3])


# --- Etapa 1: extracción de texto/campos, independiente de cómo se
# encontró el payload (rebote ICMP, UDP directo, o fragmentos sueltos) ---

def _extraer_campos(texto: str) -> dict:
    """Separa el texto decodificado en pares etiqueta/valor genéricos
    (cualquier token que termine en ':' se toma como etiqueta del token
    siguiente) — a propósito NO asume que los únicos campos posibles son
    Lat/Long/Speed, para no perder silenciosamente un campo nuevo que
    aparezca en una captura futura."""
    tokens = [t.strip() for t in texto.replace("\x00", "").split("\n") if t.strip()]
    campos = {}
    i = 0
    while i < len(tokens) - 1:
        if CAMPO_ETIQUETA_RE.match(tokens[i]):
            campos[tokens[i][:-1]] = tokens[i + 1]
            i += 2
        else:
            i += 1
    return campos


def _dms_a_decimal(valor: str) -> float | None:
    m = DMS_RE.search(valor)
    if not m:
        return None
    grados = float(m.group("deg"))
    minutos = float(m.group("min"))
    segundos = float(m.group("sec"))
    return grados + minutos / 60 + segundos / 3600


def interpretar_payload(payload: bytes) -> dict:
    """Función central (Etapa 1): recibe el payload UDP crudo YA
    identificado (venga de un rebote ICMP, un UDP directo, o fragmentos
    reconstruidos a mano) y extrae texto/campos/coordenada. Los primeros
    8 bytes del payload son un pequeño header de aplicación (visto
    consistentemente en las tres capturas de referencia: GPS, "Test123",
    y la coordenada con fragmentos sueltos) — el texto UTF-16LE empieza
    en el byte 8."""
    cuerpo = payload[8:] if len(payload) > 8 else b""
    texto = cuerpo.decode("utf-16-le", errors="replace") if cuerpo else ""
    campos = _extraer_campos(texto) if texto else {}

    if texto and ("Lat" not in campos or "Long" not in campos):
        # Fallback para texto reconstruido de fragmentos sueltos: las
        # etiquetas ("Lat:"/"Long:") pueden llegar corruptas o partidas
        # por un hueco de bloques faltantes, pero el propio patrón DMS
        # (grados°minutos'segundos) suele sobrevivir intacto dentro de un
        # mismo bloque de 12-16 bytes. Si aparecen 2+ coincidencias de
        # DMS_RE en el texto, se asume que la primera es Lat y la
        # segunda es Long (mismo orden que manda el handy en todas las
        # capturas de referencia) — best-effort, no reemplaza la
        # extracción por etiqueta cuando esta sí funciona.
        coincidencias_dms = list(DMS_RE.finditer(texto))
        if len(coincidencias_dms) >= 2:
            campos.setdefault("Lat", coincidencias_dms[0].group(0))
            campos.setdefault("Long", coincidencias_dms[1].group(0))

    lat = lon = velocidad_kmh = None
    if "Lat" in campos:
        lat = _dms_a_decimal(campos["Lat"])
        if lat is not None:
            lat = -round(lat, 6)  # hemisferio Sur asumido, ver INVESTIGACION_LRRP.md
    if "Long" in campos:
        lon = _dms_a_decimal(campos["Long"])
        if lon is not None:
            lon = -round(lon, 6)  # hemisferio Oeste asumido, ver INVESTIGACION_LRRP.md
    if "Speed" in campos:
        m = re.search(r"([\d.]+)", campos["Speed"])
        if m:
            velocidad_kmh = float(m.group(1))

    return {
        "lat": lat,
        "lon": lon,
        "velocidad_kmh": velocidad_kmh,
        "rumbo": None,  # este formato (a/b/c) no trae rumbo, solo lo da (d) nmea_beacon
        "timestamp_gps_iso": None,  # idem — solo (d) trae hora propia embebida
        "campos": campos,
        "texto_crudo": texto,
        "completo": lat is not None and lon is not None,
    }


# --- Reconocimiento del envoltorio de red: (a) rebote ICMP, (b) UDP directo ---

def _parsear_icmp_port_unreachable(data: bytes) -> dict | None:
    """Valida y extrae la estructura del mecanismo (a): paquete IP
    exterior con protocolo ICMP, tipo 3 (Destination Unreachable), que
    embebe una copia del paquete IP/UDP original. Devuelve None si `data`
    no tiene esta forma."""
    if len(data) < 20:
        return None
    if data[0] >> 4 != 4:
        return None
    ihl = (data[0] & 0x0F) * 4
    if ihl < 20 or len(data) < ihl + 8:
        return None
    if data[9] != 1:  # protocolo ICMP
        return None

    outer_ip_id = int.from_bytes(data[4:6], "big")
    icmp = data[ihl:]
    if icmp[0] != 3:  # ICMP type 3 = Destination Unreachable
        return None

    embebido = icmp[8:]  # header ICMP fijo de 8 bytes, después el paquete original
    if len(embebido) < 20:
        return None
    if embebido[0] >> 4 != 4:
        return None
    emb_ihl = (embebido[0] & 0x0F) * 4
    if emb_ihl < 20 or len(embebido) < emb_ihl + 8:
        return None
    if embebido[9] != 17:  # protocolo UDP
        return None

    src_ip = embebido[12:16]
    dst_ip = embebido[16:20]
    udp = embebido[emb_ihl:]
    udp_len = int.from_bytes(udp[4:6], "big")
    payload = udp[8:]
    largo_declarado = udp_len - 8
    if 0 <= largo_declarado <= len(payload):
        payload = payload[:largo_declarado]

    return {
        "outer_ip_id": outer_ip_id,
        "src_ip": bytes(src_ip),
        "dst_ip": bytes(dst_ip),
        "src_port": int.from_bytes(udp[0:2], "big"),
        "dst_port": int.from_bytes(udp[2:4], "big"),
        "payload": bytes(payload),
    }


def _parsear_ip_udp_directo(data: bytes) -> dict | None:
    """Valida y extrae la estructura del mecanismo (b): paquete IP
    exterior con protocolo UDP directo (sin envoltorio ICMP) — visto
    cuando el destinatario SÍ tiene el puerto abierto y responde/recibe
    sin error de red (hallazgo "Test123"). Devuelve None si `data` no
    tiene esta forma."""
    if len(data) < 20:
        return None
    if data[0] >> 4 != 4:
        return None
    ihl = (data[0] & 0x0F) * 4
    if ihl < 20 or len(data) < ihl + 8:
        return None
    if data[9] != 17:  # protocolo UDP directo
        return None

    outer_ip_id = int.from_bytes(data[4:6], "big")
    src_ip = data[12:16]
    dst_ip = data[16:20]
    udp = data[ihl:]
    udp_len = int.from_bytes(udp[4:6], "big")
    payload = udp[8:]
    largo_declarado = udp_len - 8
    if 0 <= largo_declarado <= len(payload):
        payload = payload[:largo_declarado]

    return {
        "outer_ip_id": outer_ip_id,
        "src_ip": bytes(src_ip),
        "dst_ip": bytes(dst_ip),
        "src_port": int.from_bytes(udp[0:2], "big"),
        "dst_port": int.from_bytes(udp[2:4], "big"),
        "payload": bytes(payload),
    }


def _parsear_paquete(data: bytes) -> dict | None:
    """Punto de entrada único para candidatos ya consolidados por
    `dsd-fme`: prueba primero el rebote ICMP (a) y, si no matchea, el UDP
    directo (b). Devuelve None si `data` no tiene ninguna de las dos
    formas (la mayoría de los bursts de datos del sistema no la tienen,
    ej. el ARS de Base Guardia)."""
    info = _parsear_icmp_port_unreachable(data)
    if info is not None:
        info["mecanismo"] = "icmp_bounce"
        return info
    info = _parsear_ip_udp_directo(data)
    if info is not None:
        info["mecanismo"] = "udp_directo"
        return info
    return None


class _Candidato:
    __slots__ = ("outer_ip_id", "data", "crc_error", "mecanismo")

    def __init__(self, outer_ip_id: int, data: bytes, crc_error: bool, mecanismo: str):
        self.outer_ip_id = outer_ip_id
        self.data = data
        self.crc_error = crc_error
        self.mecanismo = mecanismo


def extraer_candidatos(texto: str) -> list[_Candidato]:
    """Recorre el texto crudo de UN bloque de `dsd-fme` y devuelve un
    candidato por cada sección "Slot N - Multi Block PDU Message" cuyo
    contenido decodifica como (a) o (b) (ver `_parsear_paquete`)."""
    candidatos = []
    lineas = [strip_ansi(l) for l in texto.splitlines()]
    i = 0
    while i < len(lineas):
        m = MULTIBLOCK_HEADER_RE.match(lineas[i].strip())
        if not m:
            i += 1
            continue

        crc_error = False
        for j in range(i - 1, max(i - 4, -1), -1):
            previa = lineas[j].strip()
            if not previa:
                continue
            crc_error = "CRC32 ERR" in previa
            break

        hex_lineas = []
        k = i + 1
        while k < len(lineas) and HEX_LINEA_RE.match(lineas[k].strip()):
            hex_lineas.append(lineas[k].strip())
            k += 1
        i = k

        hex_str = "".join(hex_lineas)
        if len(hex_str) % 2:
            hex_str = hex_str[:-1]
        if not hex_str:
            continue
        try:
            data = bytes.fromhex(hex_str)
        except ValueError:
            continue

        info = _parsear_paquete(data)
        if info is None:
            continue
        candidatos.append(_Candidato(info["outer_ip_id"], data, crc_error, info["mecanismo"]))

    return candidatos


def _fusionar(candidatos: list[_Candidato]) -> bytes:
    """Fusiona 2+ capturas del MISMO paquete (mismo outer_ip_id, mismo
    mecanismo), byte a byte, en pares de 16 bits — la granularidad de un
    caracter UTF-16LE. En cada posición, si las capturas difieren, se
    prefiere el par cuyo byte alto es 0x00 y byte bajo es imprimible. Si
    ninguna copia da un par válido ahí, se conserva la primera captura tal
    cual — no se descarta el mensaje por una sola posición sin resolver."""
    largo = max(len(c.data) for c in candidatos)
    base = bytearray(candidatos[0].data.ljust(largo, b"\x00"))

    for offset in range(0, largo - 1, 2):
        elegido = None
        for c in candidatos:
            if offset + 1 >= len(c.data):
                continue
            par = c.data[offset:offset + 2]
            if par[1] == 0x00 and _BYTE_BAJO_VALIDO_RE.match(par[0:1]):
                elegido = par
                break
        if elegido is not None:
            base[offset:offset + 2] = elegido

    return bytes(base)


def _decodificar_consolidado(data: bytes, mecanismo: str, crc_error: bool, reconstruido: bool) -> dict | None:
    if mecanismo == "icmp_bounce":
        info = _parsear_icmp_port_unreachable(data)
    else:
        info = _parsear_ip_udp_directo(data)
    if info is None:
        return None

    resultado = interpretar_payload(info["payload"])
    resultado.update({
        "mecanismo": mecanismo,
        "radio_id": _radio_id_desde_ip(info["src_ip"]),
        "radio_id_contacto": _radio_id_desde_ip(info["dst_ip"]),
        "crc_error": crc_error,
        "reconstruido_cruzando_bloques": reconstruido,
        "bloques_totales": None,
        "bloques_capturados": None,
    })
    return resultado


# --- Etapa 3: reconstrucción de fragmentos sueltos, cuando dsd-fme no
# llega a consolidar el resumen "Multi Block PDU Message" ---

def _bytes_desde_pdu_payload(linea: str) -> bytes:
    m = PDU_PAYLOAD_RE.search(linea)
    if not m:
        return b""
    return bytes(int(h, 16) for h in re.findall(r"\[([0-9A-Fa-f]{2})\]", m.group(1)))


def _recolectar_bloques_de_seccion(lineas: list[str], inicio_header: int) -> tuple[list[bytes], bool, int]:
    """A partir del índice de una línea "Data Header", recolecta los
    bytes de los bloques de continuación que dsd-fme imprime sueltos
    (descartando el primero, que es el propio framing DMR — SAP/FMF/
    BLOCKS/etc, ya representado en texto por dsd-fme en la línea de
    arriba, no el payload IP/UDP embebido) hasta el terminador o el
    próximo header/preamble. Devuelve (bloques_de_bytes,
    hubo_resumen_consolidado, índice_donde_seguir escaneando). Compartido
    entre `reconstruir_fragmentos_sueltos` (c) y `extraer_beacons_nmea`
    (d) — misma mecánica de bajo nivel, cada una decide qué hacer con el
    resultado."""
    j = inicio_header + 1
    payload_lineas = []
    consolidado = False
    while j < len(lineas):
        linea = lineas[j].strip()
        if TERMINATOR_RE.search(linea):
            j += 1
            break
        if MULTIBLOCK_HEADER_RE.match(linea):
            consolidado = True
        if j > inicio_header + 1 and LIMITE_SECCION_RE.match(linea):
            break
        if PDU_PAYLOAD_RE.search(linea):
            payload_lineas.append(linea)
        j += 1
    bloques = [_bytes_desde_pdu_payload(l) for l in payload_lineas[1:]]
    return bloques, consolidado, j


def reconstruir_fragmentos_sueltos(texto: str) -> list[dict]:
    """Busca secciones "Slot 1 Data Header - Indiv - Confirmed Delivery"
    que NO tengan un resumen "Multi Block PDU Message" consolidado antes
    del siguiente header/terminador, y arma a mano el contenido con los
    bloques sueltos que sí se imprimieron.

    El primer "DMR PDU Payload" inmediatamente después de la línea de
    Data Header es el propio framing DMR (SAP/FMF/BLOCKS/etc, ya
    representado en texto por dsd-fme en la línea de arriba) — NO forma
    parte del paquete IP/UDP embebido y se descarta. Confirmado
    comparando contra casos donde dsd-fme SÍ consolidó el resumen: ese
    primer bloque nunca aparece en el hex dump consolidado.

    No hay ningún campo de número de fragmento por bloque en este texto
    (se revisó el log de referencia para confirmarlo) — se asume que el
    orden de aparición en el log es el orden real (válido para una pasada
    secuencial de dsd-fme sobre un único WAV). Lo que sí puede faltar son
    bloques completos (nunca sincronizados) — se refleja en
    `bloques_capturados` vs `bloques_totales`, sin asumir en qué posición
    cayó el hueco."""
    lineas = [strip_ansi(l) for l in texto.splitlines()]
    hallazgos = []
    i = 0
    while i < len(lineas):
        m = DATA_HEADER_RE.search(lineas[i])
        if not m:
            i += 1
            continue

        radio_id, radio_id_contacto = m.group(1), m.group(2)
        bloques_totales = None
        if i + 1 < len(lineas):
            mb = BLOCKS_RE.search(lineas[i + 1])
            if mb:
                bloques_totales = int(mb.group(1))

        bloques, consolidado_presente, j = _recolectar_bloques_de_seccion(lineas, i)
        i = j

        if consolidado_presente:
            # dsd-fme ya lo consolidó — lo toma extraer_candidatos(), acá
            # se ignora para no detectarlo dos veces desde el origen.
            continue
        if not bloques:
            continue  # ni el bloque de framing quedó, no hay nada que armar

        crudo = b"".join(bloques)
        if not crudo:
            continue

        resultado = interpretar_payload(crudo)
        resultado.update({
            "mecanismo": "fragmentos_reconstruidos",
            "radio_id": radio_id,
            "radio_id_contacto": radio_id_contacto,
            "crc_error": True,  # por definición, esta sección no se consolidó limpia
            "reconstruido_cruzando_bloques": False,
            "bloques_totales": bloques_totales,
            "bloques_capturados": len(bloques),
        })
        hallazgos.append(resultado)

    return hallazgos


# --- Etapa 1 (nueva, mecanismo d): beacon GPS automático "APRS" del
# Baofeng, formato NMEA sobre header SAP 03 [UDP Comp] + Unconfirmed
# Delivery — ver INVESTIGACION_LRRP.md, sección del beacon automático ---

DATA_HEADER_NMEA_RE = re.compile(
    r"Data Header - Indiv - Unconfirmed Delivery.*Source: (\d+) Target: (\d+)"
)
SAP03_RE = re.compile(r"SAP 03 \[UDP Comp\]")
# Cualquier sentencia NMEA: "$" + 5 letras mayúsculas (talker+tipo, ej.
# GPRMC) + campos separados por coma + "*" + checksum hex de 2 dígitos.
NMEA_SENTENCE_RE = re.compile(rb"\$([A-Z]{5}),([\x20-\x7e]*?)\*([0-9A-Fa-f]{2})")


def _checksum_nmea(cuerpo: str) -> int:
    """XOR de todos los bytes entre '$' y '*' (sin incluirlos) — checksum
    estándar NMEA-0183."""
    chk = 0
    for c in cuerpo:
        chk ^= ord(c)
    return chk


def _parsear_gprmc(campos: list[str]) -> dict | None:
    """Sentencia $GPRMC ya separada por coma (sin el "GPRMC" inicial).
    Formato estándar NMEA-0183:
      hhmmss.sss,status,lat,N/S,lon,E/W,vel_nudos,rumbo,ddmmyy,var_mag,var_mag_dir,modo
    Diseñado para ser uno de varios parsers posibles (ver
    `_PARSERS_NMEA`) — si en el futuro aparece, por ejemplo, $GPGGA, se
    agrega su propio parser sin tocar este. No se implementa ninguna otra
    sentencia todavía por falta de evidencia real de que el Baofeng las
    mande."""
    if len(campos) < 9:
        return None
    hora_str, status, lat_str, ns, lon_str, ew, vel_str, rumbo_str, fecha_str = campos[:9]
    if not lat_str or not lon_str or not ns or not ew:
        return None
    try:
        lat_deg = int(lat_str[:2])
        lat_min = float(lat_str[2:])
        lon_deg = int(lon_str[:3])
        lon_min = float(lon_str[3:])
    except ValueError:
        return None

    lat = lat_deg + lat_min / 60
    lon = lon_deg + lon_min / 60
    if ns.upper() == "S":
        lat = -lat
    if ew.upper() == "W":
        lon = -lon

    velocidad_kmh = None
    if vel_str:
        try:
            # 1 nudo = 1.852 km/h — conversión estándar, no aproximada.
            velocidad_kmh = round(float(vel_str) * 1.852, 3)
        except ValueError:
            pass

    rumbo = None
    if rumbo_str:
        try:
            rumbo = float(rumbo_str)
        except ValueError:
            pass

    timestamp_gps_iso = None
    if hora_str and fecha_str and len(fecha_str) == 6:
        try:
            hh, mm, ss = int(hora_str[0:2]), int(hora_str[2:4]), float(hora_str[4:])
            dd, mo, yy = int(fecha_str[0:2]), int(fecha_str[2:4]), int(fecha_str[4:6])
            # Se arma el ISO a mano con los campos del propio NMEA — sin
            # depender de la hora del sistema en ningún momento, es el
            # timestamp real del fix GPS, no el de cuándo lo escuchamos.
            timestamp_gps_iso = f"20{yy:02d}-{mo:02d}-{dd:02d}T{hh:02d}:{mm:02d}:{ss:06.3f}+00:00"
        except ValueError:
            pass

    return {
        "valido": status.upper() == "A",
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "velocidad_kmh": velocidad_kmh,
        "rumbo": rumbo,
        "timestamp_gps_iso": timestamp_gps_iso,
    }


_PARSERS_NMEA = {"GPRMC": _parsear_gprmc}


def _parsear_beacon_nmea(data: bytes) -> dict | None:
    """Busca una sentencia NMEA dentro de `data` (bytes crudos ya
    concatenados de los bloques de continuación) y la parsea. Devuelve
    None si no hay ninguna sentencia NMEA reconocible en absoluto (no es
    este mecanismo) — pero si hay una sentencia con un tipo sin parser
    todavía, o con checksum inválido, NO se descarta silenciosamente: se
    devuelve igual con `completo=False`/`crc_error=True` según
    corresponda, mismo criterio que el resto del módulo."""
    m = NMEA_SENTENCE_RE.search(data)
    if m is None:
        return None

    tipo = m.group(1).decode("ascii", errors="replace")
    cuerpo = m.group(2).decode("ascii", errors="replace")
    checksum_declarado_hex = m.group(3).decode("ascii")
    checksum_ok = _checksum_nmea(f"{tipo},{cuerpo}") == int(checksum_declarado_hex, 16)
    texto_crudo = f"${tipo},{cuerpo}*{checksum_declarado_hex}"

    base = {
        "lat": None, "lon": None, "velocidad_kmh": None, "rumbo": None,
        "timestamp_gps_iso": None, "campos": {"tipo_nmea": tipo, "cuerpo": cuerpo},
        "texto_crudo": texto_crudo, "completo": False, "crc_error": not checksum_ok,
    }

    parser = _PARSERS_NMEA.get(tipo)
    if parser is None:
        return base  # sentencia NMEA real, pero de un tipo sin parser (ver docstring)

    resultado = parser(cuerpo.split(","))
    if resultado is None:
        return base

    base.update(resultado)
    base["completo"] = resultado.pop("valido") and resultado["lat"] is not None and resultado["lon"] is not None
    return base


def extraer_beacons_nmea(texto: str) -> list[dict]:
    """Recorre el texto crudo de UN bloque buscando secciones "Slot 1
    Data Header - Indiv - Unconfirmed Delivery" con "SAP 03 [UDP Comp]"
    (la firma del beacon GPS automático, distinta de "SAP 04 [IP Based]"
    + "Confirmed Delivery" que usan (a)/(b)/(c)) y arma la sentencia NMEA
    con los bloques de continuación — consolidados por dsd-fme o no, da
    igual: se buscan los bytes crudos directamente, no se depende del
    resumen "Multi Block PDU Message" en absoluto para este mecanismo."""
    lineas = [strip_ansi(l) for l in texto.splitlines()]
    hallazgos = []
    i = 0
    while i < len(lineas):
        m = DATA_HEADER_NMEA_RE.search(lineas[i])
        if not m:
            i += 1
            continue

        radio_id, radio_id_contacto = m.group(1), m.group(2)
        es_sap03 = i + 1 < len(lineas) and bool(SAP03_RE.search(lineas[i + 1]))
        bloques_totales = None
        if i + 1 < len(lineas):
            mb = BLOCKS_RE.search(lineas[i + 1])
            if mb:
                bloques_totales = int(mb.group(1))

        bloques, _consolidado, j = _recolectar_bloques_de_seccion(lineas, i)
        i = j

        if not es_sap03 or not bloques:
            continue

        crudo = b"".join(bloques)
        hallazgo = _parsear_beacon_nmea(crudo)
        if hallazgo is None:
            continue  # no había ninguna sentencia NMEA reconocible acá

        hallazgo.update({
            "mecanismo": "nmea_beacon",
            "radio_id": radio_id,
            "radio_id_contacto": radio_id_contacto,
            "reconstruido_cruzando_bloques": False,
            "bloques_totales": bloques_totales,
            "bloques_capturados": len(bloques),
        })
        hallazgos.append(hallazgo)

    return hallazgos


class DetectorMensajesDMR:
    """Antes `BaofengGpsDetector`. Mantiene un pequeño estado entre
    bloques para (1) cruzar dos capturas consecutivas del mismo paquete
    consolidado (mismo `outer_ip_id` y mecanismo) cuando dsd-fme las
    decodifica en bloques distintos, y (2) evitar reportar el mismo
    mensaje lógico dos veces si lo vieron mecanismos distintos (ej. un
    mensaje capturado consolidado Y también reconstruido a mano, o el
    mismo contenido visto por rebote ICMP y por UDP directo). Para el
    mecanismo (d) nmea_beacon, la dedup usa el timestamp embebido en el
    propio NMEA en vez de lat/lon — ver `_clave_dedup`."""

    def __init__(self):
        self._pendientes: dict[tuple[int, str], _Candidato] = {}
        self._vistos: deque[tuple[str, int]] = deque()  # (clave, bloque_nro)
        self._contador_bloques = 0

    def _clave_dedup(self, hallazgo: dict) -> str:
        base = f"{hallazgo['radio_id']}|{hallazgo['radio_id_contacto']}|"
        if hallazgo.get("timestamp_gps_iso"):
            # Beacon periódico (nmea_beacon): cada fix trae su propia hora
            # real. NO alcanza con Source+Target+ventana de bloque para
            # dedup acá — un beacon nuevo cada ~30s es un evento legítimo
            # y distinto, no un duplicado del anterior, aunque comparta
            # radio_id/contacto y hasta lat/lon casi iguales. Solo se
            # considera duplicado si es EXACTAMENTE el mismo timestamp de
            # GPS (mismo fix visto dos veces, ej. reprocesado).
            return base + hallazgo["timestamp_gps_iso"]
        if hallazgo["completo"]:
            return base + f"{hallazgo['lat']},{hallazgo['lon']}"
        return base + re.sub(r"\s+", "", hallazgo["texto_crudo"])[:64]

    def _es_duplicado(self, clave: str) -> bool:
        limite = self._contador_bloques - VENTANA_DEDUP_BLOQUES
        # limpiar entradas fuera de ventana
        while self._vistos and self._vistos[0][1] < limite:
            self._vistos.popleft()
        return any(c == clave for c, _ in self._vistos)

    def procesar_bloque(self, texto: str) -> list[dict]:
        self._contador_bloques += 1
        resultados = []

        for candidato in extraer_candidatos(texto):
            clave_pendiente = (candidato.outer_ip_id, candidato.mecanismo)
            previo = self._pendientes.get(clave_pendiente)
            if previo is not None and previo.data != candidato.data:
                fusionado = _fusionar([previo, candidato])
                hallazgo = _decodificar_consolidado(
                    fusionado, candidato.mecanismo, crc_error=False, reconstruido=True
                )
                del self._pendientes[clave_pendiente]
            else:
                hallazgo = _decodificar_consolidado(
                    candidato.data, candidato.mecanismo,
                    crc_error=candidato.crc_error, reconstruido=False,
                )
                self._pendientes[clave_pendiente] = candidato
            if hallazgo is not None:
                resultados.append(hallazgo)

        resultados.extend(reconstruir_fragmentos_sueltos(texto))
        resultados.extend(extraer_beacons_nmea(texto))

        # Etapa 4: dedup — solo tiene sentido marcarlo en hallazgos con
        # contenido real (coordenada completa, o campos no vacíos); los
        # "keepalive" vacíos no se deduplican porque no representan nada
        # que valga la pena rastrear.
        for hallazgo in resultados:
            if not hallazgo["completo"] and not hallazgo["campos"]:
                hallazgo["duplicado"] = False
                continue
            clave = self._clave_dedup(hallazgo)
            hallazgo["duplicado"] = self._es_duplicado(clave)
            self._vistos.append((clave, self._contador_bloques))

        return resultados
