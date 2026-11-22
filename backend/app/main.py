"""FastAPI application: JSON API + the built React SPA on one origin."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, settings
from .api import analytics, chat, config, creators, logs, meta, session
from .bootstrap import bootstrap
from .db import AsyncSessionLocal, init_db
from .services import conversation_store as store

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with AsyncSessionLocal() as db:
        app.state.config_version = await bootstrap(db)
    yield


app = FastAPI(
    title="Storefront Multi-Agent Creator Relationship Demo",
    version=__version__,
    description=(
        "A configurable, observable multi-agent system that handles routine creator-brand "
        "interactions with server-enforced business rules."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=DEV_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def session_cookie(request: Request, call_next):
    """Give every browser a stable, httpOnly demo session id."""
    sid = request.cookies.get(settings.SESSION_COOKIE, "")
    is_new = not (sid.startswith("sess_") and len(sid) <= 64)
    if is_new:
        sid = store.new_id("sess")
    request.state.session_id = sid
    response = await call_next(request)
    if is_new:
        response.set_cookie(
            settings.SESSION_COOKIE,
            sid,
            max_age=settings.SESSION_TTL_DAYS * 24 * 3600,
            httponly=True,
            samesite="lax",
            secure=settings.IS_DEPLOYED,
            path="/",
        )
    return response


app.include_router(meta.router)
app.include_router(session.router)
app.include_router(creators.router)
app.include_router(chat.router)
app.include_router(config.router)
app.include_router(logs.router)
app.include_router(analytics.router)


# --- SPA hosting -----------------------------------------------------------
def _static_dir() -> Path | None:
    if not settings.STATIC_DIR:
        return None
    path = Path(settings.STATIC_DIR)
    return path if (path / "index.html").exists() else None


STATIC_DIR = _static_dir()

if STATIC_DIR is not None:
    assets = STATIC_DIR / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(STATIC_DIR / "favicon.svg")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        return FileResponse(STATIC_DIR / "index.html")
else:  # local dev without a build

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "service": "storefront",
            "version": __version__,
            "docs": "/docs",
            "note": "Frontend dev server is not built. Run `npm run dev` in frontend/.",
        }
