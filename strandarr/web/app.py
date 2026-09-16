from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from strandarr.web.api import router

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="strandarr")
    app.include_router(router)
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()
