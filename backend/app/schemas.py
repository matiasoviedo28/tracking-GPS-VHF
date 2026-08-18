from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator


class TelemetryIn(BaseModel):
    radio_id: str | None = None
    radio_ip: str | None = None
    radio_alias: str
    lat: float
    lon: float
    timestamp: datetime
    altitud: float | None = None
    velocidad: float | None = None
    rumbo: float | None = None

    @model_validator(mode="after")
    def radio_id_o_radio_ip(self):
        if not self.radio_id and not self.radio_ip:
            raise ValueError("Debe venir al menos uno de radio_id o radio_ip")
        return self


class TelemetryOut(BaseModel):
    status: str = "ok"
    posicion_id: int
    equipo_id: int
    recibido_en: datetime


class PosicionBroadcast(BaseModel):
    equipo_id: int
    radio_id: str | None = None
    radio_alias: str
    lat: float
    lon: float
    altitud: float | None = None
    velocidad: float | None = None
    rumbo: float | None = None
    timestamp: datetime
    recibido_en: datetime


# Eventos de presencia que puede reportar sdr-decoder al decodificar un
# burst DMR válido de un equipo (voz, emergencia, registro ARS, o un
# hallazgo de LRRP/GPS — ver Sesión 16 de INVESTIGACION_LRRP.md: "gps" se
# postea cuando dsd-fme reconoce un token real de LRRP/LOCN, no una
# posición en sí. La posición decodificada de verdad seguiría yendo por
# POST /api/telemetry, con LRRP todavía en investigación por separado).
# "aprs": beacon GPS automático y periódico detectado por el mecanismo
# nmea_beacon de dmr_texto_plano_parser.py (ver INVESTIGACION_LRRP.md,
# hito del beacon automático) — a diferencia de "gps" (un hallazgo de
# posición puntual dentro de un burst de datos, mecanismos icmp_bounce/
# udp_directo/fragmentos_reconstruidos), "aprs" es específicamente el
# formato NMEA estándar mandado solo por la función APRS del handy.
EventoPresencia = Literal["voz", "emergencia", "ars", "gps", "aprs"]


class PresenceIn(BaseModel):
    radio_id: str
    radio_alias: str | None = None
    timestamp: datetime
    evento: EventoPresencia | None = None


class PresenceOut(BaseModel):
    status: str = "ok"
    equipo_id: int
    ultimo_visto: datetime
    ultimo_evento: EventoPresencia | None = None


# Íconos disponibles para representar un equipo en el mapa del frontend,
# pensados para el contexto de bomberos (ver ARQUITECTURA.md). Es una
# elección puramente visual/manual del operador, sin relación con "tipo".
IconoEquipo = Literal[
    "base_vhf", "camion_bomberos", "ambulancia", "fuego", "handy", "bombero"
]


class EquipoIconoIn(BaseModel):
    icono: IconoEquipo


class PosicionResumen(BaseModel):
    lat: float
    lon: float
    altitud: float | None = None
    velocidad: float | None = None
    rumbo: float | None = None
    timestamp: datetime
    recibido_en: datetime


class EquipoOut(BaseModel):
    id: int
    radio_id: str | None = None
    radio_ip: str | None = None
    alias: str
    tipo: str | None = None
    modelo: str | None = None
    activo: bool
    ultimo_visto: datetime | None = None
    ultimo_evento: EventoPresencia | None = None
    icono: IconoEquipo | None = None
    online: bool
    ultima_posicion: PosicionResumen | None = None


# Bitácora de audio (ver docs/API.md) — un clip por bloque de dsd-fme que
# contuvo al menos un evento de voz/emergencia. path_archivo NO se expone acá
# a propósito (es un detalle interno de almacenamiento del servidor, no algo
# que el frontend necesite — el archivo se sirve vía
# GET /api/audio-eventos/{id}/file).
class AudioEventoOut(BaseModel):
    id: int
    radio_id: str | None = None
    radio_alias: str | None = None
    timestamp_inicio: datetime
    duracion_seg: float
    escuchado: bool
    ubicacion: str | None = None


# Estado del hardware SDR, reportado por sdr-decoder en cada bloque
# procesado (ver docs/operacion-sdr.md para qué significa cada uno y qué
# hacer en cada caso):
#   - desconectado: rtl_sdr no pudo abrir el dispositivo.
#   - mala_antena: std de las muestras IQ por encima del umbral configurado.
#   - sin_datos: conexión ok pero sin ningún sync DMR sostenido — puede ser
#     silencio normal o antena floja, no se puede distinguir solo con esto.
#   - ok: hubo sync DMR reciente (cualquier Color Code).
EstadoSdr = Literal["desconectado", "sin_datos", "mala_antena", "ok"]


class SdrStatusIn(BaseModel):
    status: EstadoSdr
    timestamp: datetime
    detalle: str | None = None


class SdrStatusOut(BaseModel):
    status: EstadoSdr
    timestamp: datetime
    detalle: str | None = None


# Histórico de posiciones para trazabilidad en el mapa (ver
# GET /api/equipos/{equipo_id}/posiciones en docs/API.md). Solo los campos
# que el frontend necesita para dibujar el trazado — no expone altitud/rumbo,
# que ya se ven en el popup de la última posición.
class PosicionHistorico(BaseModel):
    lat: float
    lon: float
    timestamp: datetime
    velocidad: float | None = None


class HistoricoPosicionesOut(BaseModel):
    equipo_id: int
    radio_id: str | None = None
    posiciones: list[PosicionHistorico]
    # Cantidad real de filas en el rango, antes de muestrear — permite al
    # frontend mostrar "mostrando X de Y puntos" cuando muestreado=True.
    total_real: int
    muestreado: bool
    # Min/max de velocidad de la serie DEVUELTA (ya muestreada, si aplica) —
    # es lo que necesita la leyenda del modo "por velocidad" del frontend.
    velocidad_min: float | None = None
    velocidad_max: float | None = None
