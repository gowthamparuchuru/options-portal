import logging
import json
import asyncio
import time
from threading import Lock

import pandas as pd
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from ..broker.shoonya_broker import ShoonyaBroker

router = APIRouter()
log = logging.getLogger("options")


@router.get("/indices")
async def list_indices():
    return [
        {"id": "NIFTY", "name": "NIFTY 50"},
        {"id": "SENSEX", "name": "SENSEX"},
    ]


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


class _LiveFeed:
    """Manages one Shoonya WS connection and fans out ticks to browser clients."""

    def __init__(self):
        self.lock = Lock()
        self.prices: dict[str, float] = {}
        self.connected = False
        self._started = False

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


_feed = _LiveFeed()


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
