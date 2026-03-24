import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .config import load_config
from .broker.shoonya_broker import ShoonyaBroker
from .routers import auth, options, orders

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("app")

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    broker = ShoonyaBroker(cfg)
    app.state.broker = broker
    app.state.config = cfg
    app.state.active_executions = {}
    log.info("App started — broker ready")
    yield
    log.info("Shutting down")


app = FastAPI(title="Options Portal", lifespan=lifespan)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(options.router, prefix="/api/options", tags=["options"])
app.include_router(orders.router, prefix="/api/orders", tags=["orders"])

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file = FRONTEND_DIST / full_path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(FRONTEND_DIST / "index.html")
