#!/usr/bin/env python3
"""
Parser forense del mecanismo de GPS "en texto plano" descubierto en un
Baofeng UV-32 (ver INVESTIGACION_LRRP.md, sección "🎯 HITO — Primera
coordenada GPS real capturada"). Recibe el texto crudo que ya escribe
`dsd-fme` (el mismo formato guardado en logs/) y devuelve las coordenadas
que pueda reconstruir, si las hay.

Mecanismo detectado (no es LRRP/LOCN):
  El handy manda su posición como texto UTF-16LE plano ("Lat: ...",
  "Long: ...", "Speed: ...") dentro del payload de un paquete UDP
  (puerto 4007), transmitido como un burst DMR de datos común
  ("Individual Data" / Multi Block PDU). Eso NO alcanzaría para verlo si
  el destinatario tuviera algo escuchando ese puerto: en el caso real que
  originó este parser, el contacto al que se le mandó el mensaje no tenía
  el puerto UDP abierto, y su stack de red devolvió automáticamente un
  ICMP "Destination Unreachable — Port Unreachable" (RFC 792) que, por
  especificación estándar de ICMP, incluye una COPIA del paquete original
  que lo provocó. Esa copia volvió a viajar por el aire y es lo que este
  parser sabe reconocer y decodificar.

⚠️  ADVERTENCIA — esto es OPORTUNISTA, no un protocolo de posición
confiable ni permanente:
  - Depende por completo de que el destinatario NO tenga el puerto UDP
    4007 escuchando. Si en el futuro alguien configura ese puerto del
    lado receptor (por accidente o a propósito), el rebote ICMP deja de
    producirse y este mecanismo deja de funcionar — sin ningún aviso ni
    forma de detectarlo desde este código.
  - No es una alternativa "encontrada" a LRRP — es una rendija que se
    abrió por un comportamiento de red no intencional, específica de este
    handy y de esta prueba puntual. No asumir que otros equipos comparten
    este comportamiento sin confirmarlo cada vez.
  - La conversión de Lat/Long de formato DMS a decimal ASUME hemisferio
    Sur/Oeste (coherente con operar en Argentina) porque el texto
    capturado hasta ahora nunca incluyó una letra de hemisferio (N/S/E/O)
    — si en una captura futura aparece esa letra, hay que dejar de asumir
    y usarla directamente.
"""

from __future__ import annotations

import re

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
HEX_LINEA_RE = re.compile(r"^[0-9A-Fa-f]+$")
MULTIBLOCK_HEADER_RE = re.compile(r"^Slot (\d+) - Multi Block PDU Message$")

# En el texto UTF-16LE decodificado, un caracter se considera "válido" si
# su byte alto es 0x00 (osea, un code point Latin-1) y su byte bajo es
# imprimible o uno de los símbolos ya vistos en capturas reales (° y ").
_BYTE_BAJO_VALIDO_RE = re.compile(rb"^[\x20-\x7e\xb0]$")

DMS_RE = re.compile(r"(?P<deg>\d+)°\s*(?P<min>\d+)'\s*(?P<sec>[\d.]+)")
CAMPO_ETIQUETA_RE = re.compile(r"^[A-Za-z][A-Za-z ]*:$")


def strip_ansi(linea: str) -> str:
    return ANSI_RE.sub("", linea)


def _radio_id_desde_ip(ip: bytes) -> str:
    """El esquema de direccionamiento visto en las capturas usa los dos
    últimos octetos de la IP como el radio_id DMR en hex (ej. 12.0.3.239 =
    0x03EF = 1007; 12.0.0.1 = 0x0001 = 1) — confirmado cruzando contra los
    Source/Target de los headers CSBK del mismo burst."""
    return str((ip[2] << 8) | ip[3])


def _parsear_icmp_port_unreachable(data: bytes) -> dict | None:
    """Valida y extrae la estructura esperada: paquete IP exterior con
    protocolo ICMP, tipo 3 (Destination Unreachable), que embebe una copia
    del paquete IP/UDP original. Devuelve None si `data` no tiene esta
    forma (no es necesariamente un error — la mayoría de los bursts de
    datos del sistema no lo son, ej. ARS de Base Guardia)."""
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


class _Candidato:
    __slots__ = ("outer_ip_id", "data", "crc_error")

    def __init__(self, outer_ip_id: int, data: bytes, crc_error: bool):
        self.outer_ip_id = outer_ip_id
        self.data = data
        self.crc_error = crc_error


def extraer_candidatos(texto: str) -> list[_Candidato]:
    """Recorre el texto crudo de UN bloque de `dsd-fme` y devuelve un
    candidato por cada sección "Slot N - Multi Block PDU Message" cuyo
    contenido decodifica como un rebote ICMP Port Unreachable (ver
    `_parsear_icmp_port_unreachable`). No hace falta que dsd-fme haya
    marcado el bloque como CRC32 ERR — también se procesan capturas
    limpias, que son las que en la práctica sirven para reconstruir una
    captura corrupta del mismo paquete vista en otro bloque."""
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

        info = _parsear_icmp_port_unreachable(data)
        if info is None:
            continue
        candidatos.append(_Candidato(info["outer_ip_id"], data, crc_error))

    return candidatos


def _fusionar(candidatos: list[_Candidato]) -> bytes:
    """Fusiona 2+ capturas del MISMO paquete (mismo outer_ip_id), byte a
    byte, en pares de 16 bits — la granularidad de un caracter UTF-16LE.
    En cada posición, si las capturas difieren, se prefiere el par cuyo
    byte alto es 0x00 y byte bajo es imprimible (la corrupción de bits
    real observada en el hito cae fuera de ese rango en la inmensa
    mayoría de los casos). Si ninguna copia da un par válido ahí, se
    conserva la primera captura tal cual — no se descarta el mensaje por
    una sola posición sin resolver."""
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


def _decodificar(data: bytes, crc_error: bool, reconstruido: bool) -> dict | None:
    info = _parsear_icmp_port_unreachable(data)
    if info is None:
        return None

    texto = info["payload"].decode("utf-16-le", errors="replace")
    campos = _extraer_campos(texto)

    lat = lon = velocidad_kmh = None
    if "Lat" in campos:
        lat = _dms_a_decimal(campos["Lat"])
        if lat is not None:
            lat = -round(lat, 6)  # hemisferio Sur asumido, ver docstring del módulo
    if "Long" in campos:
        lon = _dms_a_decimal(campos["Long"])
        if lon is not None:
            lon = -round(lon, 6)  # hemisferio Oeste asumido, ver docstring del módulo
    if "Speed" in campos:
        m = re.search(r"([\d.]+)", campos["Speed"])
        if m:
            velocidad_kmh = float(m.group(1))

    return {
        "radio_id": _radio_id_desde_ip(info["src_ip"]),
        "radio_id_contacto": _radio_id_desde_ip(info["dst_ip"]),
        "lat": lat,
        "lon": lon,
        "velocidad_kmh": velocidad_kmh,
        "campos": campos,
        "texto_crudo": texto,
        "completo": lat is not None and lon is not None,
        "crc_error": crc_error,
        "reconstruido_cruzando_bloques": reconstruido,
    }


class BaofengGpsDetector:
    """Mantiene un pequeño estado entre bloques para poder cruzar dos
    capturas consecutivas del mismo paquete (mismo `outer_ip_id`) cuando
    dsd-fme las decodifica en bloques distintos, como pasó en el hito de
    referencia (bloques 3425 y 3426). No tiene TTL: en el volumen de
    tráfico de este sistema (un puñado de bursts de datos por sesión) no
    hace falta, y ninguna captura se descarta por vencimiento."""

    def __init__(self):
        self._pendientes: dict[int, _Candidato] = {}

    def procesar_bloque(self, texto: str) -> list[dict]:
        resultados = []
        for candidato in extraer_candidatos(texto):
            previo = self._pendientes.get(candidato.outer_ip_id)
            if previo is not None and previo.data != candidato.data:
                fusionado = _fusionar([previo, candidato])
                hallazgo = _decodificar(fusionado, crc_error=False, reconstruido=True)
                del self._pendientes[candidato.outer_ip_id]
            else:
                hallazgo = _decodificar(candidato.data, crc_error=candidato.crc_error, reconstruido=False)
                self._pendientes[candidato.outer_ip_id] = candidato
            if hallazgo is not None:
                resultados.append(hallazgo)
        return resultados
