"""FastAPI application entry point.

In dev: uvicorn --reload runs this module; the frontend dev server proxies
/api/* to the backend at port 8000.

In prod (later, Task 15): the same FastAPI app will also serve the built
React static assets at /. For now we only expose /api/*.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin as admin_api
from app.api import audio as audio_api
from app.api import chat as chat_api
from app.api import curriculum as curriculum_api
from app.api import profile as profile_api
from app.api import sessions as sessions_api
from app.api import uploads as uploads_api
from app.api import users as users_api
from app.api import voice as voice_api
from app.config import get_settings
from app.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    logger.info("Starting up. Data dir: %s", settings.data_dir)
    init_db()
    yield
    logger.info("Shutting down.")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Japanese Conversation Practice API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness/readiness probe.

        Returns a stable shape that the frontend home page renders to verify
        end-to-end connectivity.
        """
        return {"status": "ok", "service": "japanese-study-backend"}

    app.include_router(users_api.router)
    app.include_router(admin_api.router)
    app.include_router(chat_api.router)
    app.include_router(voice_api.router)
    app.include_router(audio_api.router)
    app.include_router(curriculum_api.router)
    app.include_router(sessions_api.router)
    app.include_router(uploads_api.router)
    app.include_router(profile_api.router)

    return app


app = create_app()
