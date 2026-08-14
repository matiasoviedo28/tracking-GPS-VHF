from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.routers import equipos, presence, telemetry

Base.metadata.create_all(bind=engine)

app = FastAPI(title="tracking-GPS-VHF backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(telemetry.router)
app.include_router(presence.router)
app.include_router(equipos.router)


@app.get("/health")
def health():
    return {"status": "ok"}
