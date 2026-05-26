from pathlib import Path
from threading import Thread

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import settings
from app.services.transcription import prewarm_transcription_runtime


app = FastAPI(title="VibeMotion", version="0.1.0-pre-alpha.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")

static_dir = Path(__file__).resolve().parent / "static"
style_presets_dir = settings.project_root / "style_presets"
style_presets_dir.mkdir(parents=True, exist_ok=True)
app.mount("/app", StaticFiles(directory=static_dir, html=True), name="app")
app.mount("/projects", StaticFiles(directory=settings.projects_root), name="projects")
app.mount("/style-presets", StaticFiles(directory=style_presets_dir), name="style-presets")


@app.get("/")
def root() -> dict[str, str]:
    return {"app": "VibeMotion", "version": "0.1.0-pre-alpha.1", "ui": "/app/index.html"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.on_event("startup")
def startup_event() -> None:
    Thread(target=prewarm_transcription_runtime, daemon=True).start()
