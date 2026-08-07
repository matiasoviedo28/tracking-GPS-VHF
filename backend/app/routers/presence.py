from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Equipo
from app.schemas import PresenceIn, PresenceOut
from app.websocket_manager import manager

router = APIRouter()


@router.post("/api/presence", response_model=PresenceOut)
async def recibir_presencia(payload: PresenceIn, db: Session = Depends(get_db)):
    equipo = db.query(Equipo).filter(Equipo.radio_id == payload.radio_id).first()

    if equipo is None:
        # A diferencia de /api/telemetry, acá radio_alias es opcional (un
        # evento de presencia puede llegar antes que cualquier telemetría de
        # posición) — si no vino, usamos el radio_id como alias provisorio,
        # ya que Equipo.alias es NOT NULL. Se pisa por el alias real en
        # cuanto llegue un envío (de presencia o telemetría) que sí lo traiga.
        equipo = Equipo(
            radio_id=payload.radio_id,
            alias=payload.radio_alias or payload.radio_id,
        )
        db.add(equipo)
        db.flush()
    else:
        if payload.radio_alias is not None:
            equipo.alias = payload.radio_alias

    equipo.ultimo_visto = payload.timestamp
    # Si este envío no trae evento (ej. un keepalive genérico), se conserva
    # el último tipo de evento conocido en vez de pisarlo con None.
    if payload.evento is not None:
        equipo.ultimo_evento = payload.evento

    db.commit()
    db.refresh(equipo)

    await manager.broadcast(
        {
            "type": "presence_update",
            "equipo_id": equipo.id,
            "radio_id": equipo.radio_id,
            "radio_alias": equipo.alias,
            "ultimo_visto": equipo.ultimo_visto.isoformat(),
            "ultimo_evento": equipo.ultimo_evento,
        }
    )

    return PresenceOut(
        equipo_id=equipo.id,
        ultimo_visto=equipo.ultimo_visto,
        ultimo_evento=equipo.ultimo_evento,
    )
