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
# burst DMR válido de un equipo (voz, emergencia o registro ARS) — no
# incluye posición: eso sigue siendo POST /api/telemetry, con LRRP todavía
# en investigación por separado (ver sdr-decoder/INVESTIGACION_LRRP.md).
EventoPresencia = Literal["voz", "emergencia", "ars"]


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
    online: bool
    ultima_posicion: PosicionResumen | None = None
