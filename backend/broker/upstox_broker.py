"""Upstox broker for market data: historical candles, LTP, option chain, WebSocket."""

from __future__ import annotations

import calendar as _calendar
import json
import logging
from datetime import date, datetime, timedelta
from typing import Callable

import requests as http_requests
import upstox_client

log = logging.getLogger("upstox")


class UpstoxBroker:
    """Upstox broker for market data only (no order placement).

    Provides historical candle data, LTP, option chain, and
    WebSocket streaming via MarketDataStreamerV3.
    """

    INDEX_CONFIG = {
        "NIFTY": {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "display_name": "NIFTY 50",
            "options_segment": "NSE_FO",
            "shoonya_exchange": "NFO",
        },
        "SENSEX": {
            "instrument_key": "BSE_INDEX|SENSEX",
            "display_name": "SENSEX",
            "options_segment": "BSE_FO",
            "shoonya_exchange": "BFO",
        },
    }

    BASE_URL = "https://api.upstox.com"

    def __init__(self, access_token: str):
        self._token = access_token
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        }

    def _get(self, path: str, params: dict | None = None) -> dict | list | None:
        url = f"{self.BASE_URL}{path}"
        try:
            resp = http_requests.get(url, headers=self._headers, params=params, timeout=15)
            if resp.status_code == 401:
                log.error(
                    "Upstox 401 Unauthorized at %s — access token expired or invalid. "
                    "Generate a fresh token at https://account.upstox.com/developer/apps",
                    path,
                )
                return None
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") == "success":
                return body.get("data")
            log.warning("Upstox API non-success at %s: %s", path, body)
            return None
        except http_requests.exceptions.HTTPError:
            log.exception("Upstox API HTTP error: GET %s", path)
            return None
        except Exception:
            log.exception("Upstox API request failed: GET %s", path)
            return None

    # ── Validation ───────────────────────────────────────────────

    def validate_token(self) -> bool:
        """Quick check that the access token works."""
        try:
            ltp = self.get_ltp("NSE_INDEX|Nifty 50")
            return ltp is not None
        except Exception:
            return False

    # ── LTP ──────────────────────────────────────────────────────

    def get_ltp(self, instrument_key: str) -> float | None:
        data = self._get("/v3/market-quote/ltp", params={"instrument_key": instrument_key})
        if not data or not isinstance(data, dict):
            return None
        for _key, val in data.items():
            lp = val.get("last_price")
            if lp is not None:
                return float(lp)
        return None

    def get_ltp_batch(self, instrument_keys: list[str]) -> dict[str, float]:
        result: dict[str, float] = {}
        for i in range(0, len(instrument_keys), 500):
            batch = instrument_keys[i : i + 500]
            keys_param = ",".join(batch)
            data = self._get("/v3/market-quote/ltp", params={"instrument_key": keys_param})
            if data and isinstance(data, dict):
                for resp_key, val in data.items():
                    inst_key = val.get("instrument_token", resp_key)
                    lp = val.get("last_price")
                    if lp is not None:
                        result[inst_key] = float(lp)
        return result

    # ── Historical Candles ───────────────────────────────────────

    def get_historical_candles(
        self,
        index_id: str,
        from_dt: datetime,
        to_dt: datetime,
        interval: str = "30minute",
    ) -> list[dict]:
        """Fetch OHLC candles. Upstox supports 1minute, 30minute, day, week, month."""
        cfg = self.INDEX_CONFIG.get(index_id)
        if not cfg:
            log.warning("Unknown index for candles: %s", index_id)
            return []

        encoded_key = http_requests.utils.quote(cfg["instrument_key"], safe="")
        to_str = to_dt.strftime("%Y-%m-%d")
        from_str = from_dt.strftime("%Y-%m-%d")

        candles: list[dict] = []

        hist_path = f"/v2/historical-candle/{encoded_key}/{interval}/{to_str}/{from_str}"
        hist_data = self._get(hist_path)
        if hist_data and isinstance(hist_data, dict) and "candles" in hist_data:
            for c in hist_data["candles"]:
                ts = self._parse_candle_ts(c[0])
                if ts:
                    candles.append({"time": ts, "open": c[1], "high": c[2], "low": c[3], "close": c[4]})

        intra_interval = interval if interval in ("1minute", "30minute") else "30minute"
        intra_path = f"/v2/historical-candle/intraday/{encoded_key}/{intra_interval}"
        intra_data = self._get(intra_path)
        if intra_data and isinstance(intra_data, dict) and "candles" in intra_data:
            existing_ts = {c["time"] for c in candles}
            for c in intra_data["candles"]:
                ts = self._parse_candle_ts(c[0])
                if ts and ts not in existing_ts:
                    candles.append({"time": ts, "open": c[1], "high": c[2], "low": c[3], "close": c[4]})

        candles.sort(key=lambda x: x["time"])
        log.debug("Fetched %d candles for %s (%s)", len(candles), index_id, interval)
        return candles

    @staticmethod
    def _parse_candle_ts(ts_str: str) -> int | None:
        try:
            dt = datetime.fromisoformat(ts_str)
            return int(_calendar.timegm(dt.utctimetuple()))
        except Exception:
            return None

    # ── Option Chain ─────────────────────────────────────────────

    def get_nearest_expiry(self, index_id: str) -> str | None:
        cfg = self.INDEX_CONFIG.get(index_id)
        if not cfg:
            return None

        data = self._get("/v2/option/contract", params={"instrument_key": cfg["instrument_key"]})
        if not data or not isinstance(data, list):
            return None

        today = date.today()
        expiries: set[date] = set()
        for contract in data:
            exp_str = contract.get("expiry")
            if exp_str:
                try:
                    exp = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    if exp >= today:
                        expiries.add(exp)
                except ValueError:
                    continue

        if not expiries:
            return None
        return min(expiries).strftime("%Y-%m-%d")

    def get_option_contracts(self, index_id: str, expiry_date: str) -> list[dict]:
        cfg = self.INDEX_CONFIG.get(index_id)
        if not cfg:
            return []

        data = self._get("/v2/option/contract", params={
            "instrument_key": cfg["instrument_key"],
            "expiry_date": expiry_date,
        })
        return data if isinstance(data, list) else []

    def get_option_chain_data(
        self, index_id: str, expiry_date: str, spot_price: float, range_pct: float = 3.0,
    ) -> dict | None:
        """Fetch option chain and build a structure compatible with the frontend.

        Returns {strikes, atm, spot_price, expiry, exchange, index}.
        The ce_symbol / pe_symbol fields are left None; the caller should
        fill them in using ShoonyaBroker.build_trading_symbol().
        """
        cfg = self.INDEX_CONFIG.get(index_id)
        if not cfg:
            return None

        chain_data = self._get("/v2/option/chain", params={
            "instrument_key": cfg["instrument_key"],
            "expiry_date": expiry_date,
        })
        if not chain_data or not isinstance(chain_data, list):
            log.error("Empty option chain for %s expiry=%s", index_id, expiry_date)
            return None

        contracts = self.get_option_contracts(index_id, expiry_date)
        lot_size_map: dict[str, int] = {}
        for c in contracts:
            key = c.get("instrument_key")
            if key:
                lot_size_map[key] = c.get("lot_size", 0)

        lower = spot_price * (1 - range_pct / 100)
        upper = spot_price * (1 + range_pct / 100)

        strikes: dict[float, dict] = {}
        for item in chain_data:
            sp = item.get("strike_price")
            if sp is None or sp < lower or sp > upper:
                continue

            ce = item.get("call_options") or {}
            pe = item.get("put_options") or {}
            ce_key = ce.get("instrument_key")
            pe_key = pe.get("instrument_key")

            strikes[float(sp)] = {
                "strike": float(sp),
                "ce_symbol": None,
                "ce_token": ce_key,
                "ce_lotsize": lot_size_map.get(ce_key, 0),
                "pe_symbol": None,
                "pe_token": pe_key,
                "pe_lotsize": lot_size_map.get(pe_key, 0),
            }

        if not strikes:
            return None

        atm = min(strikes.keys(), key=lambda s: abs(s - spot_price))

        return {
            "strikes": strikes,
            "atm": float(atm),
            "spot_price": spot_price,
            "expiry": expiry_date,
            "exchange": cfg["shoonya_exchange"],
            "index": index_id,
        }

    # ── Margin calculation ────────────────────────────────────────

    def get_basket_margin(self, instruments: list[dict]) -> dict:
        """Calculate margin for a basket of instruments via Upstox ChargeApi.

        Each dict: {instrument_key, quantity, transaction_type, product}
        Returns: {total_margin, span, exposure, margin_benefit, option_premium, error}
        """
        try:
            configuration = upstox_client.Configuration()
            configuration.access_token = self._token
            api_client = upstox_client.ApiClient(configuration)
            api_instance = upstox_client.ChargeApi(api_client)

            sdk_instruments = []
            for inst in instruments:
                sdk_instruments.append(upstox_client.Instrument(
                    instrument_key=inst["instrument_key"],
                    quantity=inst["quantity"],
                    product=inst.get("product", "D"),
                    transaction_type=inst.get("transaction_type", "SELL"),
                ))

            margin_body = upstox_client.MarginRequest(instruments=sdk_instruments)
            resp = api_instance.post_margin(margin_body)

            data = resp
            if hasattr(resp, "data"):
                data = resp.data
            if hasattr(data, "to_dict"):
                data = data.to_dict()
            elif not isinstance(data, dict):
                data = {"required_margin": 0, "final_margin": 0, "margins": []}

            required = data.get("required_margin", 0) or 0
            final = data.get("final_margin", 0) or 0
            benefit = max(0, required - final)

            margins_list = data.get("margins", []) or []
            total_span = 0
            total_exposure = 0
            total_premium = 0
            for m in margins_list:
                if isinstance(m, dict):
                    total_span += m.get("span_margin", 0) or 0
                    total_exposure += m.get("exposure_margin", 0) or 0
                    total_premium += m.get("net_buy_premium", 0) or 0

            return {
                "total_margin": round(final, 2),
                "span": round(total_span, 2),
                "exposure": round(total_exposure, 2),
                "margin_benefit": round(benefit, 2),
                "option_premium": round(total_premium, 2),
                "error": None,
            }
        except Exception as e:
            log.exception("Upstox margin calculation failed")
            error_msg = str(e)
            if hasattr(e, "body"):
                try:
                    body = json.loads(e.body) if isinstance(e.body, str) else e.body
                    error_msg = body.get("message", error_msg) if isinstance(body, dict) else error_msg
                except Exception:
                    pass
            return {"error": error_msg}

    # ── WebSocket helpers ────────────────────────────────────────

    def create_streamer(
        self, instrument_keys: list[str] | None = None, mode: str = "ltpc",
    ) -> upstox_client.MarketDataStreamerV3:
        configuration = upstox_client.Configuration()
        configuration.access_token = self._token
        api_client = upstox_client.ApiClient(configuration)

        if instrument_keys:
            return upstox_client.MarketDataStreamerV3(api_client, instrument_keys, mode)
        return upstox_client.MarketDataStreamerV3(api_client)

    @staticmethod
    def parse_ws_message(message) -> dict[str, float]:
        """Extract instrument_key → ltp from a MarketDataStreamerV3 message."""
        prices: dict[str, float] = {}

        if isinstance(message, str):
            try:
                data = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                return prices
        elif isinstance(message, dict):
            data = message
        else:
            return prices

        if data.get("type") != "live_feed":
            return prices

        feeds = data.get("feeds", {})
        for inst_key, payload in feeds.items():
            ltpc = payload.get("ltpc") or payload.get("ff", {}).get("ltpc")
            if ltpc:
                ltp = ltpc.get("ltp")
                if ltp is not None:
                    prices[inst_key] = float(ltp)
                    continue
            ff = payload.get("ff", {})
            eFeed = ff.get("eFeedDetails") or ff.get("marketFF") or {}
            if "ltpc" in eFeed:
                ltp = eFeed["ltpc"].get("ltp")
                if ltp is not None:
                    prices[inst_key] = float(ltp)

        return prices
