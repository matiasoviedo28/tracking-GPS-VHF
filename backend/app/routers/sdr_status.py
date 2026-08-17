from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import EstadoSDR
from app.schemas import SdrStatusIn, SdrStatusOut
from app.websocket_manager import manager

router = APIRouter()


@router.post("/api/sdr-status", response_model=SdrStatusOut)
async def actualizar_estado_sdr(payload: SdrStatusIn, db: Session = Depends(get_db)):
    fila = db.query(EstadoSDR).first()
    cambio = fila is None or fila.status != payload.status

    if fila is None:
        fila = EstadoSDR(status=payload.status, timestamp=payload.timestamp, detalle=payload.detalle)
        db.add(fila)
    else:
        fila.status = payload.status
        fila.timestamp = payload.timestamp
        fila.detalle = payload.detalle

    db.commit()
    db.refresh(fila)

    # sdr-decoder postea esto en CADA bloque (~cada 12-14s, ver
    # live_presence_bridge.py) como heartbeat — acá solo se emite por
    # WebSocket cuando el status realmente cambió, para no spamear a los
    # clientes conectados con un mensaje idéntico cada pocos segundos.
    if cambio:
        await manager.broadcast(
            {
                "type": "sdr_status_update",
                "status": fila.status,
                "timestamp": fila.timestamp.isoformat(),
                "detalle": fila.detalle,
            }
        )

    return SdrStatusOut(status=fila.status, timestamp=fila.timestamp, detalle=fila.detalle)


@router.get("/api/sdr-status", response_model=SdrStatusOut)
def obtener_estado_sdr(db: Session = Depends(get_db)):
    fila = db.query(EstadoSDR).first()
    if fila is None:
        # Todavía no llegó ningún POST del bridge (por ejemplo, backend
        # recién levantado o sdr-decoder todavía no procesó su primer
        # bloque) — estado neutro en vez de 404, para que el frontend no
        # tenga que tratar esto como un caso de error aparte.
        return SdrStatusOut(
            status="sin_datos",
            timestamp=datetime.now(timezone.utc),
            detalle="Todavía no se recibió ningún reporte de sdr-decoder",
        )
    return SdrStatusOut(status=fila.status, timestamp=fila.timestamp, detalle=fila.detalle)
