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
    # Ícono elegido a mano para representar el equipo en el mapa del
    # frontend (ver IconoEquipo en schemas.py) — no tiene relación con
    # "tipo" (handy/movil/base, categoría del equipo en sí), es solo la
    # representación visual que eligió el operador desde el popup del mapa.
    icono = Column(String, nullable=True)

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


class EventoAudio(Base):
    __tablename__ = "eventos_audio"

    id = Column(Integer, primary_key=True)
    # Denormalizado a propósito (sin FK a Equipo): esto es una bitácora de lo
    # que efectivamente se escuchó, no un dato de identidad de equipo — un
    # radio_id desconocido o sin alias igual tiene que poder loggearse.
    radio_id = Column(String, nullable=True, index=True)
    radio_alias = Column(String, nullable=True)
    timestamp_inicio = Column(DateTime(timezone=True), nullable=False)
    duracion_seg = Column(Float, nullable=False)
    path_archivo = Column(String, nullable=False)
    escuchado = Column(Boolean, nullable=False, default=False)
    # Preparado para cuando LRRP dé una posición real (ver
    # sdr-decoder/INVESTIGACION_LRRP.md) — sin usar todavía, no hay GPS real
    # aún. Formato sin definir (podría terminar siendo lat/lon separados o
    # un FK a Posicion) — placeholder simple a propósito.
    ubicacion = Column(String, nullable=True)


class EstadoSDR(Base):
    """Última lectura conocida del estado del hardware SDR (ver
    docs/operacion-sdr.md) — una sola fila, no historial: el bridge postea
    esto en cada bloque (~cada 12-14s) como heartbeat, y solo interesa el
    valor más reciente, no acumular una fila por bloque para siempre."""

    __tablename__ = "estado_sdr"

    id = Column(Integer, primary_key=True)
    status = Column(String, nullable=False)  # desconectado | sin_datos | mala_antena | ok
    timestamp = Column(DateTime(timezone=True), nullable=False)
    detalle = Column(String, nullable=True)
