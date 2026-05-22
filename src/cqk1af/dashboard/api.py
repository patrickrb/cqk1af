"""FastAPI app + WS endpoint for the operator dashboard."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..app import App
from ..mcp_server.tools import MCPTools
from ..util.logging import get_logger
from .routes.control import build_control_router
from .routes.operate import build_operate_router
from .routes.qsos import build_qso_router
from .routes.radio import build_radio_router
from .routes.settings import build_settings_router
from .ws import WSHub, ws_endpoint

log = get_logger(__name__)


def build_dashboard_app(app: App) -> FastAPI:
    """Build a FastAPI app bound to the given ``App`` (event bus, FSM, radio)."""
    tools = MCPTools(app)
    hub = WSHub(app)

    @asynccontextmanager
    async def lifespan(_fastapi_app: FastAPI):
        await app.start_background_tasks()
        await hub.start()
        try:
            yield
        finally:
            await hub.stop()

    fastapi_app = FastAPI(title="CQK1AF", version="0.1.0", lifespan=lifespan)
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=app.settings.dashboard.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    fastapi_app.include_router(build_radio_router(lambda: tools))
    fastapi_app.include_router(build_control_router(lambda: tools))
    fastapi_app.include_router(build_operate_router(lambda: tools))
    fastapi_app.include_router(build_settings_router(lambda: app.settings))
    fastapi_app.include_router(build_qso_router(lambda: tools))

    @fastapi_app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        await ws_endpoint(ws, hub)

    @fastapi_app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "state": str(app.fsm.state), "armed": app.fsm.session.armed}

    # Serve the built dashboard if it exists. Avoids the Vite dev server's
    # cold-start overhead (16+ seconds for module pre-bundling) for normal
    # operation. Vite is only needed when actively editing dashboard source.
    dist_dir = Path("web/dist")
    if dist_dir.exists() and (dist_dir / "index.html").exists():
        fastapi_app.mount(
            "/assets",
            StaticFiles(directory=str(dist_dir / "assets")),
            name="assets",
        )

        @fastapi_app.get("/")
        async def _root() -> FileResponse:
            return FileResponse(str(dist_dir / "index.html"))

        # SPA fallback — anything that isn't /api or /ws falls back to index.html
        # so client-side routes (/log, /settings) work on direct reload.
        from fastapi import HTTPException

        @fastapi_app.get("/{full_path:path}")
        async def _spa_fallback(full_path: str):
            # Explicitly stay out of API / WS namespaces. Without this, a typo'd
            # /api/foo would silently return index.html instead of 404.
            if full_path.startswith(("api/", "api", "ws")):
                raise HTTPException(status_code=404)
            candidate = dist_dir / full_path
            if candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(dist_dir / "index.html"))
    else:
        log.warning(
            "dashboard.dist_missing",
            message=(
                "web/dist not built — dashboard will only be reachable via the "
                "Vite dev server. Run `cd web && npm run build` for the fast path."
            ),
        )

    return fastapi_app


def _maybe_build_dashboard() -> bool:
    """Auto-build web/dist if it's missing and npm is available.

    Best-effort: silently skips if npm isn't on PATH or the build fails. The
    server still starts in that case — just without serving the UI.
    """
    import shutil
    import subprocess

    web_dir = Path("web")
    dist_index = web_dir / "dist" / "index.html"
    if dist_index.exists():
        return True
    if not web_dir.exists() or not (web_dir / "package.json").exists():
        return False
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        return False
    log.info("dashboard.auto_build", message="web/dist missing — running npm run build")
    try:
        if not (web_dir / "node_modules").exists():
            subprocess.run(
                [npm, "install", "--silent"], cwd=str(web_dir), check=True, timeout=300
            )
        subprocess.run(
            [npm, "run", "build"], cwd=str(web_dir), check=True, timeout=300
        )
    except Exception:
        log.exception("dashboard.auto_build_failed")
        return False
    return dist_index.exists()


async def run_dashboard_server(app: App) -> None:
    import uvicorn

    # If the build artifact is missing, try to produce it before starting the
    # server. The hot path is the if-exists check — no-op when the bundle is
    # already there.
    built = _maybe_build_dashboard()

    fastapi_app = build_dashboard_app(app)
    host = app.settings.dashboard.host
    port = app.settings.dashboard.port
    if built:
        log.info(
            "dashboard.open_url",
            url=f"http://{host}:{port}/",
            message="Production-built dashboard served from web/dist (fast path).",
        )
    else:
        log.warning(
            "dashboard.open_url",
            url=f"http://{host}:{port}/api/health",
            message=(
                "web/dist not built and auto-build failed. Run "
                "`cd web && npm install && npm run build` manually, or use "
                "`cd web && npm run dev` for HMR on :5173 (slow cold start)."
            ),
        )
    config = uvicorn.Config(
        fastapi_app,
        host=host,
        port=port,
        log_level=app.settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    await server.serve()
