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
    radio_id = Column(String, unique=True, nullable=False, index=True)
    radio_ip = Column(String, nullable=True)
    alias = Column(String, nullable=False)
    tipo = Column(String, nullable=True)  # handy | movil | base
    modelo = Column(String, nullable=True)
    activo = Column(Boolean, nullable=False, default=True)

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
