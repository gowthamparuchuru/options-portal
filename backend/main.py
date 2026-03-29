import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .config import load_config, has_zerodha_config
from .broker.shoonya_broker import ShoonyaBroker
from .broker.zerodha_broker import ZerodhaBroker, KITE_INDEX_TOKENS
from .routers import auth, options, orders
from .routers.options import _feed, run_orphan_watcher

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

    app.state.margin_broker = None
    if has_zerodha_config(cfg):
        margin_broker = ZerodhaBroker(cfg)
        result = margin_broker.login()
        if result.get("ok"):
            app.state.margin_broker = margin_broker
            log.info("Zerodha margin broker ready")
            margin_broker.start_kite_ticker(list(KITE_INDEX_TOKENS.values()))
        else:
            log.warning("Zerodha login failed: %s — margin calculation disabled",
                        result.get("error"))
    else:
        log.info("Zerodha credentials not configured — margin calculation disabled")

    watcher_task = asyncio.create_task(run_orphan_watcher())
    log.info("App started — broker ready (orphan watcher active)")
    yield

    watcher_task.cancel()
    _feed.shutdown()
    if app.state.margin_broker:
        app.state.margin_broker.stop_kite_ticker()
    log.info("Shutting down — feeds closed")


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
