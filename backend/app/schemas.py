from datetime import datetime

from pydantic import BaseModel


class TelemetryIn(BaseModel):
    radio_id: str
    radio_ip: str
    radio_alias: str
    lat: float
    lon: float
    timestamp: datetime
    altitud: float | None = None
    velocidad: float | None = None
    rumbo: float | None = None


class TelemetryOut(BaseModel):
    status: str = "ok"
    posicion_id: int
    equipo_id: int
    recibido_en: datetime


class PosicionBroadcast(BaseModel):
    equipo_id: int
    radio_id: str
    radio_alias: str
    lat: float
    lon: float
    altitud: float | None = None
    velocidad: float | None = None
    rumbo: float | None = None
    timestamp: datetime
    recibido_en: datetime
