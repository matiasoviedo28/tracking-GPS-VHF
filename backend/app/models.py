from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Equipo(Base):
    __tablename__ = "equipos"

    id = Column(Integer, primary_key=True)
    # Al menos uno de radio_id/radio_ip siempre viene informado (validado en
    # TelemetryIn) — ambos nullable acá porque un equipo puede llegar a
    # persistirse habiendo recibido telemetría con uno solo de los dos.
    radio_id = Column(String, unique=True, nullable=True, index=True)
    radio_ip = Column(String, nullable=True, index=True)
    alias = Column(String, nullable=False)
    tipo = Column(String, nullable=True)  # handy | movil | base
    modelo = Column(String, nullable=True)
    activo = Column(Boolean, nullable=False, default=True)
    # Presencia: última vez que se decodificó CUALQUIER burst válido de este
    # equipo (voz, emergencia, ARS, o una posición) — no confundir con la
    # última posición GPS conocida (eso vive en Posicion). ultimo_evento
    # queda None cuando el último "visto" vino de una posición, no de un
    # evento de presencia con tipo propio.
    ultimo_visto = Column(DateTime(timezone=True), nullable=True)
    ultimo_evento = Column(String, nullable=True)  # voz | emergencia | ars | gps

    posiciones = relationship("Posicion", back_populates="equipo")


class Posicion(Base):
    __tablename__ = "posiciones"

    id = Column(Integer, primary_key=True)
    equipo_id = Column(Integer, ForeignKey("equipos.id"), nullable=False, index=True)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    altitud = Column(Float, nullable=True)
    velocidad = Column(Float, nullable=True)
    rumbo = Column(Float, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    recibido_en = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    equipo = relationship("Equipo", back_populates="posiciones")
