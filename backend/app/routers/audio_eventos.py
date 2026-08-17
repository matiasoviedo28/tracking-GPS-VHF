import mimetypes
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import EventoAudio
from app.schemas import AudioEventoOut
from app.websocket_manager import manager

router = APIRouter()

# Persistencia de los archivos de audio: directorio pensado para vivir en un
# volumen Docker nombrado en el servicio "backend" (ver docker-compose.yml,
# volumen "audio_data") — el Dockerfile de backend solo copia el código
# (COPY app ./app), sin volume mount de código, así que sin un volumen
# separado el audio se perdería en cada rebuild/recreate del contenedor
# (mismo criterio ya usado para "db_data" con Postgres). Configurable por env
# var por si se quiere apuntar a otro lado sin tocar código.
AUDIO_STORAGE_DIR = Path(os.environ.get("AUDIO_STORAGE_DIR", "storage/audio"))


def _a_out(evento: EventoAudio) -> AudioEventoOut:
    return AudioEventoOut(
        id=evento.id,
        radio_id=evento.radio_id,
        radio_alias=evento.radio_alias,
        timestamp_inicio=evento.timestamp_inicio,
        duracion_seg=evento.duracion_seg,
        escuchado=evento.escuchado,
        ubicacion=evento.ubicacion,
    )


@router.post("/api/audio-eventos", response_model=AudioEventoOut)
async def crear_audio_evento(
    archivo: UploadFile = File(...),
    timestamp_inicio: datetime = Form(...),
    duracion_seg: float = Form(...),
    radio_id: str | None = Form(None),
    radio_alias: str | None = Form(None),
    db: Session = Depends(get_db),
):
    AUDIO_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    # Nombre propio en disco (no el filename que mandó el cliente) — evita
    # colisiones/paths raros; la extensión sí se conserva si vino.
    extension = Path(archivo.filename or "").suffix or ".wav"
    destino = AUDIO_STORAGE_DIR / f"{uuid.uuid4().hex}{extension}"
    destino.write_bytes(await archivo.read())

    evento = EventoAudio(
        radio_id=radio_id,
        radio_alias=radio_alias,
        timestamp_inicio=timestamp_inicio,
        duracion_seg=duracion_seg,
        path_archivo=str(destino),
        escuchado=False,
    )
    db.add(evento)
    db.commit()
    db.refresh(evento)

    # Se emite solo la metadata, no el archivo — el frontend lo pide aparte
    # vía GET /api/audio-eventos/{id}/file cuando el usuario le da play.
    await manager.broadcast(
        {
            "type": "audio_event",
            "id": evento.id,
            "radio_id": evento.radio_id,
            "radio_alias": evento.radio_alias,
            "timestamp_inicio": evento.timestamp_inicio.isoformat(),
            "duracion_seg": evento.duracion_seg,
            "escuchado": evento.escuchado,
        }
    )

    return _a_out(evento)


@router.get("/api/audio-eventos", response_model=list[AudioEventoOut])
def listar_audio_eventos(db: Session = Depends(get_db)):
    eventos = (
        db.query(EventoAudio)
        .order_by(EventoAudio.timestamp_inicio.desc())
        .all()
    )
    return [_a_out(evento) for evento in eventos]


@router.get("/api/audio-eventos/{evento_id}/file")
def descargar_audio_evento(evento_id: int, db: Session = Depends(get_db)):
    evento = db.query(EventoAudio).filter(EventoAudio.id == evento_id).first()
    if evento is None:
        raise HTTPException(status_code=404, detail="Evento de audio no encontrado")

    path = Path(evento.path_archivo)
    if not path.exists():
        # El registro en base sobrevive aunque el archivo se haya perdido a
        # mano del disco (o del volumen) — se distingue de un 404 de id
        # inexistente con un detalle más específico.
        raise HTTPException(status_code=404, detail="Archivo de audio no encontrado en disco")

    content_type = mimetypes.guess_type(path.name)[0] or "audio/wav"
    return FileResponse(path, media_type=content_type, filename=path.name)


@router.patch("/api/audio-eventos/{evento_id}/escuchado", response_model=AudioEventoOut)
async def marcar_audio_escuchado(evento_id: int, db: Session = Depends(get_db)):
    evento = db.query(EventoAudio).filter(EventoAudio.id == evento_id).first()
    if evento is None:
        raise HTTPException(status_code=404, detail="Evento de audio no encontrado")

    evento.escuchado = True
    db.commit()
    db.refresh(evento)

    # Para que, si hay más de un cliente con el panel abierto, todos vean el
    # cambio de estado sin depender de un refresh (mismo criterio que
    # icono_update en equipos.py).
    await manager.broadcast(
        {
            "type": "audio_event_escuchado",
            "id": evento.id,
            "escuchado": True,
        }
    )

    return _a_out(evento)
