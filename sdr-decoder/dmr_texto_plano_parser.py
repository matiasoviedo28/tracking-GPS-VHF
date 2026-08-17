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

Reconoce TRES formas de encontrar el mismo tipo de contenido (texto
UTF-16LE, con o sin forma de coordenada Lat/Long/Speed):

  (a) "icmp_bounce" — el paquete UDP original rebota como ICMP
      "Destination Unreachable — Port Unreachable" porque el destinatario
      no tiene el puerto escuchando (mecanismo del hito de GPS,
      radio_id 1 → 1007). `dsd-fme` sí llega a consolidar el paquete en
      un resumen "Multi Block PDU Message".
  (b) "udp_directo" — el paquete UDP llega derecho, sin rebotar, porque
      el destinatario SÍ tiene algo escuchando y responde (mecanismo del
      hallazgo "Test123", radio_id 1001 → 1). También consolidado por
      `dsd-fme`.
  (c) "fragmentos_reconstruidos" — con señal marginal, `dsd-fme` a veces
      NO llega a consolidar el resumen y solo deja los bloques sueltos
      del PDU en el log. Este módulo los recolecta y concatena a mano
      (ver `reconstruir_fragmentos_sueltos`).

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

        # recolectar líneas hasta el próximo header/preamble o terminador
        j = i + 1
        payload_lineas = []
        consolidado_presente = False
        while j < len(lineas):
            linea = lineas[j].strip()
            if TERMINATOR_RE.search(linea):
                j += 1
                break
            if MULTIBLOCK_HEADER_RE.match(linea):
                consolidado_presente = True
            if j > i + 1 and LIMITE_SECCION_RE.match(linea):
                break
            if PDU_PAYLOAD_RE.search(linea):
                payload_lineas.append(linea)
            j += 1
        i = j

        if consolidado_presente:
            # dsd-fme ya lo consolidó — lo toma extraer_candidatos(), acá
            # se ignora para no detectarlo dos veces desde el origen.
            continue
        if len(payload_lineas) < 2:
            continue  # ni el bloque de framing quedó, no hay nada que armar

        # descartar el primer bloque (framing DMR, no payload IP/UDP)
        bloques_capturados = payload_lineas[1:]
        crudo = b"".join(_bytes_desde_pdu_payload(l) for l in bloques_capturados)
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
            "bloques_capturados": len(bloques_capturados),
        })
        hallazgos.append(resultado)

    return hallazgos


class DetectorMensajesDMR:
    """Antes `BaofengGpsDetector`. Mantiene un pequeño estado entre
    bloques para (1) cruzar dos capturas consecutivas del mismo paquete
    consolidado (mismo `outer_ip_id` y mecanismo) cuando dsd-fme las
    decodifica en bloques distintos, y (2) evitar reportar el mismo
    mensaje lógico dos veces si lo vieron mecanismos distintos (ej. un
    mensaje capturado consolidado Y también reconstruido a mano, o el
    mismo contenido visto por rebote ICMP y por UDP directo)."""

    def __init__(self):
        self._pendientes: dict[tuple[int, str], _Candidato] = {}
        self._vistos: deque[tuple[str, int]] = deque()  # (clave, bloque_nro)
        self._contador_bloques = 0

    def _clave_dedup(self, hallazgo: dict) -> str:
        base = f"{hallazgo['radio_id']}|{hallazgo['radio_id_contacto']}|"
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
