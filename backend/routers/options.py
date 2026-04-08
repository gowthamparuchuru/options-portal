from __future__ import annotations

import calendar as _calendar
import json
import logging
import asyncio
import time
from datetime import datetime, time as dtime, timedelta
from threading import Lock

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from ..broker.shoonya_broker import ShoonyaBroker
from ..broker.upstox_broker import UpstoxBroker

router = APIRouter()
log = logging.getLogger("options")


@router.get("/indices")
async def list_indices():
    return [
        {"id": "NIFTY", "name": "NIFTY 50"},
        {"id": "SENSEX", "name": "SENSEX"},
    ]


@router.get("/candles/{index_id}")
async def get_candles(index_id: str, request: Request):
    upstox: UpstoxBroker | None = request.app.state.upstox_broker
    if upstox is None:
        return {"error": "Upstox not configured — chart unavailable"}

    if index_id not in UpstoxBroker.INDEX_CONFIG:
        return {"error": f"Unknown index: {index_id}"}

    now = datetime.now()
    market_open = dtime(hour=9, minute=15)
    from_dt = datetime.combine(now.date() - timedelta(days=5), market_open)
    to_dt = now

    candles = upstox.get_historical_candles(index_id, from_dt, to_dt, unit="minutes", interval=15)

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


@router.get("/chain/{index_id}")
async def get_option_chain_snapshot(index_id: str, request: Request):
    """REST endpoint: returns a one-time snapshot of the option chain."""
    upstox: UpstoxBroker | None = request.app.state.upstox_broker
    broker: ShoonyaBroker = request.app.state.broker

    if upstox is None:
        return {"error": "Upstox not configured"}

    cfg = UpstoxBroker.INDEX_CONFIG.get(index_id)
    if not cfg:
        return {"error": f"Unknown index: {index_id}"}

    expiry = upstox.get_nearest_expiry(index_id)
    if not expiry:
        return {"error": "Could not determine nearest expiry"}

    spot = upstox.get_ltp(cfg["instrument_key"])
    if spot is None:
        return {"error": "Failed to fetch spot price"}

    chain = upstox.get_option_chain_data(index_id, expiry, spot, range_pct=3.5)
    if not chain:
        return {"error": "No strikes found in range"}

    expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
    for strike_data in chain["strikes"].values():
        strike = strike_data["strike"]
        strike_data["ce_symbol"] = broker.build_trading_symbol(
            index_id, expiry_date, strike, "CE",
        )
        strike_data["pe_symbol"] = broker.build_trading_symbol(
            index_id, expiry_date, strike, "PE",
        )

    return chain


ORPHAN_TIMEOUT_SECS = 30 * 60

COMPANION_INDICES = {
    "NIFTY": [
        {"key": "NSE_INDEX|Nifty 50", "label": "NIFTY 50"},
        {"key": "GLOBAL_INDEX|SGX+NIFTY", "label": "SGX NIFTY"},
        {"key": "NSE_INDEX|India VIX", "label": "INDIA VIX"},
    ],
    "SENSEX": [
        {"key": "BSE_INDEX|SENSEX", "label": "SENSEX"},
        {"key": "NSE_INDEX|Nifty 50", "label": "NIFTY 50"},
        {"key": "GLOBAL_INDEX|SGX+NIFTY", "label": "SGX NIFTY"},
        {"key": "NSE_INDEX|India VIX", "label": "INDIA VIX"},
    ],
}


class _LiveFeed:
    """Manages one Upstox MarketDataStreamerV3 and fans ticks to browser clients."""

    def __init__(self):
        self.lock = Lock()
        self.prices: dict[str, float] = {}
        self.closes: dict[str, float] = {}
        self.connected = False
        self._started = False
        self._client_count = 0
        self._last_client_left: float | None = None
        self._streamer = None
        self._upstox: UpstoxBroker | None = None

    def _on_message(self, message):
        if isinstance(message, str):
            try:
                data = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                return
        elif isinstance(message, dict):
            data = message
        else:
            return

        if data.get("type") != "live_feed":
            return

        prices, closes = {}, {}
        for inst_key, payload in data.get("feeds", {}).items():
            ltpc = payload.get("ltpc") or payload.get("ff", {}).get("ltpc")
            if not ltpc:
                ff = payload.get("ff", {})
                ltpc = (ff.get("eFeedDetails") or ff.get("marketFF") or {}).get("ltpc")
            if ltpc:
                ltp = ltpc.get("ltp")
                if ltp is not None:
                    prices[inst_key] = float(ltp)
                cp = ltpc.get("cp")
                if cp is not None:
                    closes[inst_key] = float(cp)

        with self.lock:
            if prices:
                self.prices.update(prices)
            if closes:
                self.closes.update(closes)

    def _on_open(self):
        with self.lock:
            self.connected = True
        log.info("Upstox WS connected")

    def _on_close(self):
        with self.lock:
            self.connected = False
        log.warning("Upstox WS disconnected")

    def _on_error(self, error):
        log.error("Upstox WS error: %s", error)

    def start(self, upstox: UpstoxBroker, instrument_keys: list[str]):
        with self.lock:
            if self._started:
                return
            self._upstox = upstox

        streamer = upstox.create_streamer(instrument_keys, "ltpc")
        streamer.on("message", self._on_message)
        streamer.on("open", self._on_open)
        streamer.on("close", self._on_close)
        streamer.on("error", self._on_error)
        streamer.auto_reconnect(True, 5, 50)

        with self.lock:
            self._streamer = streamer
            self._started = True

        streamer.connect()
        log.info("Upstox WS streamer started with %d instruments", len(instrument_keys))

    def subscribe(self, instrument_keys: list[str]):
        with self.lock:
            streamer = self._streamer
            is_connected = self.connected
        if streamer and is_connected:
            try:
                streamer.subscribe(instrument_keys, "ltpc")
                log.debug("Subscribed to %d additional instruments", len(instrument_keys))
            except Exception:
                log.exception("Failed to subscribe to instruments")

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.prices)

    def snapshot_closes(self) -> dict:
        with self.lock:
            return dict(self.closes)

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
            streamer = self._streamer
            self._started = False
            self.connected = False
            self.prices.clear()
            self.closes.clear()
            self._streamer = None
            self._upstox = None
            self._last_client_left = None
        if streamer:
            log.info("Stopping Upstox WS feed")
            try:
                streamer.disconnect()
            except Exception:
                log.exception("Error disconnecting Upstox WS")


_feed = _LiveFeed()


async def run_orphan_watcher():
    """Background task: closes Upstox WS if no browser clients for 30 min."""
    while True:
        await asyncio.sleep(60)
        if _feed.is_orphaned():
            log.info("Upstox WS orphaned for >%d min — shutting down",
                     ORPHAN_TIMEOUT_SECS // 60)
            _feed.shutdown()


@router.websocket("/ws/{index_id}")
async def option_chain_ws(ws: WebSocket, index_id: str):
    await ws.accept()
    upstox: UpstoxBroker | None = ws.app.state.upstox_broker
    broker: ShoonyaBroker = ws.app.state.broker

    if upstox is None:
        await ws.send_json({"type": "error", "message": "Upstox not configured"})
        await ws.close()
        return

    cfg = UpstoxBroker.INDEX_CONFIG.get(index_id)
    if not cfg:
        await ws.send_json({"type": "error", "message": f"Unknown index: {index_id}"})
        await ws.close()
        return

    expiry = upstox.get_nearest_expiry(index_id)
    if not expiry:
        await ws.send_json({"type": "error", "message": "Could not determine nearest expiry"})
        await ws.close()
        return

    spot = upstox.get_ltp(cfg["instrument_key"])
    if spot is None:
        await ws.send_json({"type": "error", "message": "Failed to get spot price"})
        await ws.close()
        return

    chain = upstox.get_option_chain_data(index_id, expiry, spot, range_pct=3.5)
    if not chain:
        await ws.send_json({"type": "error", "message": "No strikes in range"})
        await ws.close()
        return

    expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
    for strike_data in chain["strikes"].values():
        strike = strike_data["strike"]
        strike_data["ce_symbol"] = broker.build_trading_symbol(
            index_id, expiry_date, strike, "CE",
        )
        strike_data["pe_symbol"] = broker.build_trading_symbol(
            index_id, expiry_date, strike, "PE",
        )

    tokens_to_sub = [cfg["instrument_key"]]
    for s_data in chain["strikes"].values():
        if s_data["ce_token"]:
            tokens_to_sub.append(s_data["ce_token"])
        if s_data["pe_token"]:
            tokens_to_sub.append(s_data["pe_token"])

    companion_defs = COMPANION_INDICES.get(index_id, [])
    companion_keys = [ci["key"] for ci in companion_defs]
    for k in companion_keys:
        if k not in tokens_to_sub:
            tokens_to_sub.append(k)

    companion_ltps = upstox.get_ltp_batch(companion_keys) if companion_keys else {}

    if not _feed._started:
        _feed.start(upstox, tokens_to_sub)
        await asyncio.sleep(3)
    else:
        _feed.subscribe(tokens_to_sub)

    spot_key = cfg["instrument_key"]

    companion_init = []
    for ci in companion_defs:
        companion_init.append({
            "key": ci["key"],
            "label": ci["label"],
            "price": companion_ltps.get(ci["key"]),
            "change": None,
        })

    await ws.send_json({
        "type": "init",
        "spot_price": spot,
        "expiry": expiry,
        "exchange": cfg["shoonya_exchange"],
        "spot_token": spot_key,
        "strikes": chain["strikes"],
        "atm": chain["atm"],
        "companion": companion_init,
    })

    _feed.add_client()
    try:
        while True:
            prices = _feed.snapshot()
            closes = _feed.snapshot_closes()
            spot_now = prices.get(spot_key, spot)

            companion_tick = []
            for ci in companion_defs:
                ltp = prices.get(ci["key"])
                cp = closes.get(ci["key"])
                change = None
                if ltp is not None and cp is not None and cp > 0:
                    change = round(((ltp - cp) / cp) * 100, 2)
                companion_tick.append({
                    "key": ci["key"],
                    "label": ci["label"],
                    "price": ltp,
                    "change": change,
                })

            await ws.send_json({
                "type": "tick",
                "prices": prices,
                "spot": spot_now,
                "companion": companion_tick,
            })
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        log.info("Browser WS disconnected")
    except Exception:
        log.exception("WS error")
    finally:
        _feed.remove_client()
