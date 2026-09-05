from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.routes import router as api_router
from backend.app.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title="India Industrial Intelligence Platform",
    version="0.1.0",
    description=(
        "Web GIS platform for industrial sites across India. "
        f"Data mode: {settings.data_mode}."
    ),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    # In development ALLOWED_ORIGINS defaults to ["*"].
    # Set ALLOWED_ORIGINS=https://your-domain.com in production.
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

logger.info(
    "App started — data_mode=%s, allowed_origins=%s",
    settings.data_mode,
    settings.allowed_origins,
)
