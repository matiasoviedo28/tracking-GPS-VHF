from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Equipo, Posicion
from app.schemas import TelemetryIn, TelemetryOut
from app.websocket_manager import manager

router = APIRouter()


@router.post("/api/telemetry", response_model=TelemetryOut)
async def recibir_telemetria(payload: TelemetryIn, db: Session = Depends(get_db)):
    if not -90 <= payload.lat <= 90:
        raise HTTPException(status_code=400, detail="lat fuera de rango válido (-90 a 90)")
    if not -180 <= payload.lon <= 180:
        raise HTTPException(status_code=400, detail="lon fuera de rango válido (-180 a 180)")

    equipo = None
    if payload.radio_id is not None:
        equipo = db.query(Equipo).filter(Equipo.radio_id == payload.radio_id).first()
    if equipo is None and payload.radio_ip is not None:
        equipo = db.query(Equipo).filter(Equipo.radio_ip == payload.radio_ip).first()

    if equipo is None:
        equipo = Equipo(
            radio_id=payload.radio_id,
            radio_ip=payload.radio_ip,
            alias=payload.radio_alias,
        )
        db.add(equipo)
        db.flush()
    else:
        # Solo se pisa el campo que vino informado en este reporte puntual —
        # si este envío solo trae uno de los dos, no se borra el otro que ya
        # se conocía de un envío anterior.
        if payload.radio_id is not None:
            equipo.radio_id = payload.radio_id
        if payload.radio_ip is not None:
            equipo.radio_ip = payload.radio_ip
        equipo.alias = payload.radio_alias

    # Una posición también es evidencia de que el equipo está "vivo" —
    # se actualiza ultimo_visto igual que en /api/presence, pero sin tocar
    # ultimo_evento (una posición no es un evento de voz/emergencia/ars).
    equipo.ultimo_visto = payload.timestamp

    posicion = Posicion(
        equipo_id=equipo.id,
        lat=payload.lat,
        lon=payload.lon,
        altitud=payload.altitud,
        velocidad=payload.velocidad,
        rumbo=payload.rumbo,
        timestamp=payload.timestamp,
        recibido_en=datetime.now(timezone.utc),
    )
    db.add(posicion)
    db.commit()
    db.refresh(posicion)

    await manager.broadcast(
        {
            "type": "position_update",
            "equipo_id": equipo.id,
            "radio_id": equipo.radio_id,
            "radio_alias": equipo.alias,
            "lat": posicion.lat,
            "lon": posicion.lon,
            "altitud": posicion.altitud,
            "velocidad": posicion.velocidad,
            "rumbo": posicion.rumbo,
            "timestamp": posicion.timestamp.isoformat(),
            "recibido_en": posicion.recibido_en.isoformat(),
        }
    )

    return TelemetryOut(
        posicion_id=posicion.id,
        equipo_id=equipo.id,
        recibido_en=posicion.recibido_en,
    )


@router.websocket("/ws/telemetry")
async def ws_telemetria(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # No se espera mensaje del cliente; se mantiene la conexión abierta
            # hasta que el cliente la cierre.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
