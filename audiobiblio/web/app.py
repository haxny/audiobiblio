"""
app — FastAPI application factory with scheduler lifespan.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import load_config
from ..db.session import init_db
from ..scheduler import create_scheduler

log = structlog.get_logger()

WEB_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    init_db()

    # Seed stations + programs (idempotent)
    from ..seed import seed_all
    from ..db.session import get_session
    seed_all(get_session())

    scheduler = create_scheduler(
        crawl_interval_minutes=cfg.crawl_interval_minutes,
        download_interval_minutes=cfg.download_interval_minutes,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    log.info("web_started", host=cfg.web_host, port=cfg.web_port)
    yield
    scheduler.shutdown(wait=False)
    log.info("web_stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="audiobiblio", lifespan=lifespan)

    # API routers
    from .routers import system, jobs, episodes, targets, ingest, sse as sse_router
    app.include_router(system.router)
    app.include_router(jobs.router)
    app.include_router(episodes.router)
    app.include_router(targets.router)
    app.include_router(ingest.router)
    app.include_router(sse_router.router)

    # HTML views
    from .views import router as views_router
    app.include_router(views_router)

    # Static files
    static_dir = WEB_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # JSON error handler for API routes
    @app.exception_handler(Exception)
    async def _api_error_handler(request: Request, exc: Exception):
        if request.url.path.startswith("/api/"):
            log.error("api_error", path=request.url.path, error=str(exc))
            return JSONResponse(
                status_code=500,
                content={"detail": str(exc)},
            )
        raise exc

    return app
