from datetime import datetime

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
