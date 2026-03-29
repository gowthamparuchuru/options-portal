import calendar as _calendar
import logging
import json
import asyncio
import time
from datetime import datetime, time as dtime, timedelta
from threading import Lock

import pandas as pd
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from ..broker.shoonya_broker import ShoonyaBroker
from ..broker.zerodha_broker import KITE_INDEX_TOKENS

router = APIRouter()
log = logging.getLogger("options")


@router.get("/indices")
async def list_indices():
    return [
        {"id": "NIFTY", "name": "NIFTY 50"},
        {"id": "SENSEX", "name": "SENSEX"},
    ]


def _previous_trading_day(d) -> "date":
    """Step back to the most recent weekday (skips Sat/Sun)."""
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


@router.get("/candles/{index_id}")
async def get_candles(index_id: str, request: Request):
    margin_broker = request.app.state.margin_broker
    if margin_broker is None:
        return {"error": "Zerodha not configured — chart unavailable"}

    if index_id not in KITE_INDEX_TOKENS:
        return {"error": f"Unknown index: {index_id}"}

    now = datetime.now()
    prev_day = _previous_trading_day(now.date())
    market_open = dtime(hour=6, minute=0) if index_id == "GIFTNIFTY" else dtime(hour=9, minute=15)
    from_dt = datetime.combine(prev_day, market_open)
    to_dt = now

    candles = margin_broker.get_historical_candles(
        index_id, from_dt, to_dt, "15minute"
    )

    if not candles:
        return {"error": "No candle data available"}

    today_open_ts = int(_calendar.timegm(
        datetime.combine(now.date(), market_open).timetuple()
    ))
    prev_close = None
    for c in candles:
        if c["time"] < today_open_ts:
            prev_close = c["close"]

    return {
        "candles": candles,
        "index": index_id,
        "interval": "15minute",
        "prev_close": prev_close,
    }


@router.websocket("/kite-ws")
async def kite_live_ws(ws: WebSocket):
    """Stream live prices from Kite ticker to browser clients."""
    await ws.accept()
    margin_broker = ws.app.state.margin_broker
    if not margin_broker:
        await ws.send_json({"type": "error", "message": "Zerodha not configured"})
        await ws.close()
        return
    try:
        while True:
            prices = margin_broker.kite_price_snapshot()
            if prices:
                await ws.send_json({"type": "tick", "prices": prices})
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Kite WS error")


@router.get("/chain/{index_id}")
async def get_option_chain_snapshot(index_id: str, request: Request):
    """REST endpoint: returns a one-time snapshot of the option chain."""
    broker: ShoonyaBroker = request.app.state.broker
    if not broker.is_logged_in():
        return {"error": "Not authenticated"}

    idx_cfg = ShoonyaBroker.INDEX_CONFIG.get(index_id)
    if not idx_cfg:
        return {"error": f"Unknown index: {index_id}"}

    symbols_path = broker.download_symbols(idx_cfg["symbols_url"], idx_cfg["options_exchange"])
    df = pd.read_csv(symbols_path)
    df = df.loc[:, ~df.columns.str.contains("^Unnamed")]

    from datetime import datetime
    today_exp = datetime.now().strftime("%d-%b-%Y").upper()

    options_df = df[
        (df["Symbol"].isin(idx_cfg["symbol_names"]))
        & (df["Instrument"] == idx_cfg["instrument_type"])
        & (df["Expiry"] == today_exp)
    ].copy()

    if options_df.empty:
        all_expiries = df[
            (df["Symbol"].isin(idx_cfg["symbol_names"]))
            & (df["Instrument"] == idx_cfg["instrument_type"])
        ]["Expiry"].unique()
        nearest = sorted(all_expiries)[0] if len(all_expiries) else None
        if nearest:
            options_df = df[
                (df["Symbol"].isin(idx_cfg["symbol_names"]))
                & (df["Instrument"] == idx_cfg["instrument_type"])
                & (df["Expiry"] == nearest)
            ].copy()
            today_exp = nearest

    spot = broker.get_spot_price(idx_cfg["spot_exchange"], idx_cfg["spot_token"])
    if spot is None:
        return {"error": "Failed to fetch spot price"}

    chain = broker.get_option_chain_tokens(options_df, spot, 3.0)
    if not chain:
        return {"error": "No strikes found in range"}

    chain["spot_price"] = spot
    chain["index"] = index_id
    chain["expiry"] = today_exp
    chain["exchange"] = idx_cfg["options_exchange"]
    return chain


ORPHAN_TIMEOUT_SECS = 30 * 60


class _LiveFeed:
    """Manages one Shoonya WS connection and fans out ticks to browser clients."""

    def __init__(self):
        self.lock = Lock()
        self.prices: dict[str, float] = {}
        self.connected = False
        self._started = False
        self._client_count = 0
        self._last_client_left: float | None = None
        self._broker: ShoonyaBroker | None = None

    def on_tick(self, tick):
        tk = tick.get("tk")
        lp = tick.get("lp")
        if tk and lp:
            with self.lock:
                self.prices[tk] = float(lp)

    def on_open(self):
        with self.lock:
            self.connected = True
        log.info("Shoonya WS connected")

    def on_close(self):
        with self.lock:
            self.connected = False
        log.warning("Shoonya WS disconnected")

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.prices)

    def add_client(self):
        with self.lock:
            self._client_count += 1
            self._last_client_left = None
        log.info("Browser client connected (active: %d)", self._client_count)

    def remove_client(self):
        with self.lock:
            self._client_count = max(0, self._client_count - 1)
            if self._client_count == 0:
                self._last_client_left = time.time()
        log.info("Browser client disconnected (active: %d)", self._client_count)

    def is_orphaned(self) -> bool:
        with self.lock:
            return (
                self._started
                and self._client_count == 0
                and self._last_client_left is not None
                and time.time() - self._last_client_left > ORPHAN_TIMEOUT_SECS
            )

    def shutdown(self):
        with self.lock:
            if not self._started:
                return
            broker = self._broker
            self._started = False
            self.connected = False
            self.prices.clear()
            self._broker = None
            self._last_client_left = None
        if broker:
            log.info("Stopping Shoonya WS feed")
            broker.stop_websocket()


_feed = _LiveFeed()


async def run_orphan_watcher():
    """Background task: closes Shoonya WS if no browser clients for 30 min."""
    while True:
        await asyncio.sleep(60)
        if _feed.is_orphaned():
            log.info("Shoonya WS orphaned for >%d min — shutting down",
                     ORPHAN_TIMEOUT_SECS // 60)
            _feed.shutdown()


@router.websocket("/ws/{index_id}")
async def option_chain_ws(ws: WebSocket, index_id: str):
    await ws.accept()
    broker: ShoonyaBroker = ws.app.state.broker

    if not broker.is_logged_in():
        await ws.send_json({"type": "error", "message": "Not authenticated"})
        await ws.close()
        return

    idx_cfg = ShoonyaBroker.INDEX_CONFIG.get(index_id)
    if not idx_cfg:
        await ws.send_json({"type": "error", "message": f"Unknown index: {index_id}"})
        await ws.close()
        return

    symbols_path = broker.download_symbols(idx_cfg["symbols_url"], idx_cfg["options_exchange"])
    df = pd.read_csv(symbols_path)
    df = df.loc[:, ~df.columns.str.contains("^Unnamed")]

    from datetime import datetime
    today_exp = datetime.now().strftime("%d-%b-%Y").upper()

    options_df = df[
        (df["Symbol"].isin(idx_cfg["symbol_names"]))
        & (df["Instrument"] == idx_cfg["instrument_type"])
        & (df["Expiry"] == today_exp)
    ].copy()

    if options_df.empty:
        all_expiries = df[
            (df["Symbol"].isin(idx_cfg["symbol_names"]))
            & (df["Instrument"] == idx_cfg["instrument_type"])
        ]["Expiry"].unique()
        nearest = sorted(all_expiries)[0] if len(all_expiries) else None
        if nearest:
            options_df = df[
                (df["Symbol"].isin(idx_cfg["symbol_names"]))
                & (df["Instrument"] == idx_cfg["instrument_type"])
                & (df["Expiry"] == nearest)
            ].copy()
            today_exp = nearest

    spot = broker.get_spot_price(idx_cfg["spot_exchange"], idx_cfg["spot_token"])
    if spot is None:
        await ws.send_json({"type": "error", "message": "Failed to get spot price"})
        await ws.close()
        return

    chain = broker.get_option_chain_tokens(options_df, spot, 3.0)
    if not chain:
        await ws.send_json({"type": "error", "message": "No strikes in range"})
        await ws.close()
        return

    tokens_to_sub = [f"{idx_cfg['spot_exchange']}|{idx_cfg['spot_token']}"]
    for s_data in chain["strikes"].values():
        if s_data["ce_token"]:
            tokens_to_sub.append(f"{idx_cfg['options_exchange']}|{s_data['ce_token']}")
        if s_data["pe_token"]:
            tokens_to_sub.append(f"{idx_cfg['options_exchange']}|{s_data['pe_token']}")

    if not _feed._started:
        _feed._broker = broker
        broker.start_websocket(_feed.on_tick, _feed.on_open, _feed.on_close)
        _feed._started = True
        await asyncio.sleep(2)

    broker.subscribe(tokens_to_sub)

    await ws.send_json({
        "type": "init",
        "spot_price": spot,
        "expiry": today_exp,
        "exchange": idx_cfg["options_exchange"],
        "spot_token": idx_cfg["spot_token"],
        "strikes": chain["strikes"],
        "atm": chain["atm"],
    })

    _feed.add_client()
    try:
        while True:
            prices = _feed.snapshot()
            spot_now = prices.get(idx_cfg["spot_token"], spot)
            await ws.send_json({"type": "tick", "prices": prices, "spot": spot_now})
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        log.info("Browser WS disconnected")
    except Exception as e:
        log.exception("WS error")
    finally:
        _feed.remove_client()
