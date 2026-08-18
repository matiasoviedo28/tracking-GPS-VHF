import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Equipo, Posicion
from app.schemas import (
    EquipoIconoIn,
    EquipoOut,
    HistoricoPosicionesOut,
    PosicionHistorico,
    PosicionResumen,
)
from app.websocket_manager import manager

router = APIRouter()

# Umbral para considerar un equipo "online" en base a ultimo_visto.
# Configurable por env var para no requerir redeploy si el criterio cambia.
ONLINE_THRESHOLD_SECONDS = int(os.environ.get("PRESENCE_ONLINE_THRESHOLD_SECONDS", "300"))


@router.get("/api/equipos", response_model=list[EquipoOut])
def listar_equipos(db: Session = Depends(get_db)):
    equipos = db.query(Equipo).order_by(Equipo.alias).all()
    ahora = datetime.now(timezone.utc)

    resultado = []
    for equipo in equipos:
        online = (
            equipo.ultimo_visto is not None
            and (ahora - equipo.ultimo_visto).total_seconds() <= ONLINE_THRESHOLD_SECONDS
        )

        # Volumen esperado de 6-20 equipos (ver ARQUITECTURA.md sección 4) —
        # una consulta por equipo acá es simple y suficientemente rápida;
        # no se optimiza con una query de agregación hasta que haga falta.
        ultima_posicion = (
            db.query(Posicion)
            .filter(Posicion.equipo_id == equipo.id)
            .order_by(Posicion.timestamp.desc())
            .first()
        )

        resultado.append(
            EquipoOut(
                id=equipo.id,
                radio_id=equipo.radio_id,
                radio_ip=equipo.radio_ip,
                alias=equipo.alias,
                tipo=equipo.tipo,
                modelo=equipo.modelo,
                activo=equipo.activo,
                ultimo_visto=equipo.ultimo_visto,
                ultimo_evento=equipo.ultimo_evento,
                icono=equipo.icono,
                online=online,
                ultima_posicion=(
                    PosicionResumen(
                        lat=ultima_posicion.lat,
                        lon=ultima_posicion.lon,
                        altitud=ultima_posicion.altitud,
                        velocidad=ultima_posicion.velocidad,
                        rumbo=ultima_posicion.rumbo,
                        timestamp=ultima_posicion.timestamp,
                        recibido_en=ultima_posicion.recibido_en,
                    )
                    if ultima_posicion is not None
                    else None
                ),
            )
        )

    return resultado


@router.patch("/api/equipos/{equipo_id}/icono", response_model=EquipoOut)
async def actualizar_icono(equipo_id: int, payload: EquipoIconoIn, db: Session = Depends(get_db)):
    equipo = db.query(Equipo).filter(Equipo.id == equipo_id).first()
    if equipo is None:
        raise HTTPException(status_code=404, detail="Equipo no encontrado")

    equipo.icono = payload.icono
    db.commit()
    db.refresh(equipo)

    # Se emite por WS para que todos los clientes conectados (no solo el que
    # hizo el cambio) actualicen el ícono en el mapa al instante.
    await manager.broadcast(
        {
            "type": "icono_update",
            "equipo_id": equipo.id,
            "icono": equipo.icono,
        }
    )

    online = (
        equipo.ultimo_visto is not None
        and (datetime.now(timezone.utc) - equipo.ultimo_visto).total_seconds() <= ONLINE_THRESHOLD_SECONDS
    )
    ultima_posicion = (
        db.query(Posicion)
        .filter(Posicion.equipo_id == equipo.id)
        .order_by(Posicion.timestamp.desc())
        .first()
    )
    return EquipoOut(
        id=equipo.id,
        radio_id=equipo.radio_id,
        radio_ip=equipo.radio_ip,
        alias=equipo.alias,
        tipo=equipo.tipo,
        modelo=equipo.modelo,
        activo=equipo.activo,
        ultimo_visto=equipo.ultimo_visto,
        ultimo_evento=equipo.ultimo_evento,
        icono=equipo.icono,
        online=online,
        ultima_posicion=(
            PosicionResumen(
                lat=ultima_posicion.lat,
                lon=ultima_posicion.lon,
                altitud=ultima_posicion.altitud,
                velocidad=ultima_posicion.velocidad,
                rumbo=ultima_posicion.rumbo,
                timestamp=ultima_posicion.timestamp,
                recibido_en=ultima_posicion.recibido_en,
            )
            if ultima_posicion is not None
            else None
        ),
    )


@router.get("/api/equipos/{equipo_id}/posiciones", response_model=HistoricoPosicionesOut)
def obtener_historico_posiciones(
    equipo_id: int,
    desde: datetime,
    hasta: datetime,
    # 500 por defecto: suficiente para que un trazado se vea fluido en el
    # mapa sin mandar al frontend recorridos de miles de puntos. Límite duro
    # de 5000 para evitar que un valor arbitrario en la URL fuerce traer
    # rangos enormes a memoria (ver muestreo más abajo).
    max_puntos: int = Query(default=500, ge=2, le=5000),
    db: Session = Depends(get_db),
):
    equipo = db.query(Equipo).filter(Equipo.id == equipo_id).first()
    if equipo is None:
        raise HTTPException(status_code=404, detail="Equipo no encontrado")
    if desde > hasta:
        raise HTTPException(status_code=400, detail="'desde' no puede ser posterior a 'hasta'")

    posiciones = (
        db.query(Posicion)
        .filter(
            Posicion.equipo_id == equipo_id,
            Posicion.timestamp >= desde,
            Posicion.timestamp <= hasta,
        )
        .order_by(Posicion.timestamp.asc())
        .all()
    )

    total_real = len(posiciones)
    muestreado = total_real > max_puntos

    if muestreado:
        # Muestreo uniforme a lo largo del rango temporal completo (no los
        # primeros/últimos N) para preservar la forma real del recorrido —
        # ver PARTE 1.3 del pedido. Se garantiza incluir siempre el primer y
        # el último punto (índices 0 y total_real-1) tomando pasos parejos
        # entre ambos extremos. El set() puede colapsar índices redondeados
        # duplicados cuando max_puntos está cerca de total_real, así que la
        # cantidad final puede ser levemente menor a max_puntos — no rompe
        # el contrato ("hasta max_puntos"), y muestreado sigue reflejando
        # correctamente que hubo recorte.
        paso = (total_real - 1) / (max_puntos - 1)
        indices = sorted({round(i * paso) for i in range(max_puntos)})
        seleccionadas = [posiciones[i] for i in indices]
    else:
        seleccionadas = posiciones

    velocidades = [p.velocidad for p in seleccionadas if p.velocidad is not None]
    velocidad_min = min(velocidades) if velocidades else None
    velocidad_max = max(velocidades) if velocidades else None

    return HistoricoPosicionesOut(
        equipo_id=equipo.id,
        radio_id=equipo.radio_id,
        posiciones=[
            PosicionHistorico(
                lat=p.lat,
                lon=p.lon,
                timestamp=p.timestamp,
                velocidad=p.velocidad,
            )
            for p in seleccionadas
        ],
        total_real=total_real,
        muestreado=muestreado,
        velocidad_min=velocidad_min,
        velocidad_max=velocidad_max,
    )
