from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.auth_routes import router as auth_router
from backend.app.api.routes import register_error_handlers
from backend.app.api.routes import router as api_router
from backend.app.config import get_settings
from backend.app.security.gate import gate
from backend.app.services import events, thermal_repo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()

try:                                     # Docker image: src/netguard.py is copied next to the backend
    from src import netguard
except ImportError:                      # development checkout
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "industrial-data"))
    from src import netguard
if netguard.install_from_env():
    logger.info("OFFLINE_MODE: this API refuses every outbound connection except the FIRMS hosts and the private network")


@asynccontextmanager
async def lifespan(_: FastAPI):
    loop = asyncio.get_running_loop()
    if settings.data_mode == "postgis":
        events.hub.start(loop)
    fb = thermal_repo._file_singleton()

    def thermal_info() -> dict:
        info = fb.run_info()
        summary = info.get("summary", {})
        return {"sources": summary.get("sources"), "alerts": summary.get("alerts"),
                "latest_detection": (info.get("status") or {}).get("latest_detection_at")}

    events.hub._stop.clear()
    events.hub.start_file_watcher(loop, fb.mtime, thermal_info)
    try:
        yield
    finally:
        events.hub.stop()


app = FastAPI(
    title="ThermoTrace - India Industrial Intelligence API",
    version="0.2.0",
    description=(
        "Web GIS API for industrial sites mapped in OpenStreetMap, kept in sync by a background worker. "
        f"Data mode: {settings.data_mode}. Records are NOT government-verified."
    ),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,  # no cookies/sessions are used
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Token", "X-Analyst-Token", "X-Requested-With", "X-Request-ID", "Last-Event-ID"],
    expose_headers=["X-Total-Count", "X-Data-Revision", "Content-Disposition", "X-Request-ID"],
)

@app.middleware("http")
async def read_only_demo(request, call_next):
    """DEMO_READONLY=true: the public demo user can read everything and change nothing."""
    if get_settings().demo_readonly and request.method not in ("GET", "HEAD", "OPTIONS"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "This is a read-only public demo; decisions and admin actions are disabled."}, status_code=403)
    return await call_next(request)


app.middleware("http")(gate)      # added last = outermost: request id + access log, rate limits, authentication, CSRF header

app.include_router(auth_router)
app.include_router(api_router)
register_error_handlers(app)

logger.info("App configured - data_mode=%s, allowed_origins=%s", settings.data_mode, settings.allowed_origins)
