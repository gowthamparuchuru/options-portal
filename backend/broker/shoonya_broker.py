from __future__ import annotations

import json
import logging
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import pyotp
import requests
from NorenRestApiPy.NorenApi import NorenApi

from .interface import BrokerInterface, ProductType, OrderType, TransactionType

MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

log = logging.getLogger("shoonya")

SESSION_CACHE = Path(tempfile.gettempdir()) / ".shoonya_session_cache"


class _ShoonyaApi(NorenApi):
    def __init__(self):
        super().__init__(
            host="https://api.shoonya.com/NorenWClientTP/",
            websocket="wss://api.shoonya.com/NorenWSTP/",
        )


class ShoonyaBroker(BrokerInterface):

    INDEX_CONFIG = {
        "NIFTY": {
            "name": "NIFTY",
            "display_name": "NIFTY 50",
            "spot_exchange": "NSE",
            "spot_token": "26000",
            "options_exchange": "NFO",
            "symbols_url": "https://api.shoonya.com/NFO_symbols.txt.zip",
            "symbol_names": ["NIFTY"],
            "instrument_type": "OPTIDX",
        },
        "SENSEX": {
            "name": "SENSEX",
            "display_name": "SENSEX",
            "spot_exchange": "BSE",
            "spot_token": "1",
            "options_exchange": "BFO",
            "symbols_url": "https://api.shoonya.com/BFO_symbols.txt.zip",
            "symbol_names": ["BSXOPT"],
            "instrument_type": "OPTIDX",
        },
    }

    PRODUCT_MAP = {
        ProductType.INTRADAY: "I",
        ProductType.OVERNIGHT: "M",
        ProductType.DELIVERY: "C",
    }

    ORDER_TYPE_MAP = {
        OrderType.MARKET: "MKT",
        OrderType.LIMIT: "LMT",
        OrderType.SL: "SL-LMT",
        OrderType.SL_M: "SL-MKT",
    }

    TRANSACTION_MAP = {
        TransactionType.BUY: "B",
        TransactionType.SELL: "S",
    }

    SYMBOL_PREFIX = {
        "NIFTY": "NIFTY",
        "SENSEX": "SENSEX",
    }

    def __init__(self, config: dict):
        self._cfg = config
        self._api = _ShoonyaApi()
        self._logged_in = False

    # ── Symbol building ────────────────────────────────────────────

    def build_trading_symbol(self, index_name: str, expiry: date,
                              strike: float, option_type: str) -> str:
        prefix = self.SYMBOL_PREFIX.get(index_name, index_name)
        dd = f"{expiry.day:02d}"
        mon = MONTH_ABBR[expiry.month]
        yy = f"{expiry.year % 100:02d}"
        ot = option_type[0]  # "CE" → "C", "PE" → "P"
        strike_str = str(int(strike)) if strike == int(strike) else str(strike)
        return f"{prefix}{dd}{mon}{yy}{ot}{strike_str}"

    # ── Enum resolution ────────────────────────────────────────────

    def resolve_product_type(self, product_type: ProductType) -> str:
        return self.PRODUCT_MAP[product_type]

    def resolve_order_type(self, order_type: OrderType) -> str:
        return self.ORDER_TYPE_MAP[order_type]

    def resolve_transaction_type(self, txn_type: TransactionType) -> str:
        return self.TRANSACTION_MAP[txn_type]

    # ── Auth ──────────────────────────────────────────────────────

    def login(self) -> dict:
        cached = self._load_cached_session()
        if cached:
            self._api.set_session(
                userid=self._cfg["SHOONYA_USER_ID"],
                password=self._cfg["SHOONYA_PASSWORD"],
                usertoken=cached,
            )
            test = self._api.get_quotes(exchange="NSE", token="26000")
            if test and test.get("stat") == "Ok":
                self._logged_in = True
                log.info("Using cached session")
                return {"ok": True, "msg": "Using cached session"}
            log.warning("Cached session expired, doing fresh login")

        totp = pyotp.TOTP(self._cfg["SHOONYA_TOTP_SECRET"]).now()
        log.info("Generated TOTP, attempting login")

        resp = self._api.login(
            userid=self._cfg["SHOONYA_USER_ID"],
            password=self._cfg["SHOONYA_PASSWORD"],
            twoFA=totp,
            vendor_code=self._cfg["SHOONYA_VENDOR_CODE"],
            api_secret=self._cfg["SHOONYA_API_SECRET"],
            imei=self._cfg["SHOONYA_IMEI"],
        )

        if resp is None or resp.get("stat") != "Ok":
            err = resp.get("emsg", "Unknown error") if resp else "No response from broker"
            log.error("Login failed: %s", err)
            return {"ok": False, "error": err}

        token = resp.get("susertoken")
        if token:
            self._save_session_cache(token)
        self._logged_in = True
        log.info("Login successful")
        return {"ok": True, "msg": "Login successful"}

    def is_logged_in(self) -> bool:
        return self._logged_in

    # ── Funds ──────────────────────────────────────────────────────

    def get_available_margin(self) -> dict | None:
        resp = self._api.get_limits()
        if not resp or resp.get("stat") != "Ok":
            log.warning("get_limits failed: %s", resp)
            return None
        collateral = float(resp.get("collateral", 0))
        cash = float(resp.get("cash", 0))
        margin_used = float(resp.get("marginused", 0))
        return {
            "collateral": round(collateral, 2),
            "cash": round(cash, 2),
            "margin_used": round(margin_used, 2),
            "available": round(collateral + cash - margin_used, 2),
        }

    # ── Market data ───────────────────────────────────────────────

    def get_spot_price(self, exchange: str, token: str) -> float | None:
        resp = self._api.get_quotes(exchange=exchange, token=token)
        if resp and resp.get("stat") == "Ok":
            ltp = float(resp.get("lp", 0))
            return ltp if ltp > 0 else None
        log.warning("get_spot_price failed for %s|%s: %s", exchange, token, resp)
        return None

    def get_ltp(self, exchange: str, token: str) -> float | None:
        return self.get_spot_price(exchange, token)

    def download_symbols(self, url: str, prefix: str) -> str:
        tmp = Path(tempfile.gettempdir())
        zip_path = tmp / f"{prefix}_symbols.zip"
        txt_path = tmp / f"{prefix}_symbols.txt"

        if txt_path.exists():
            mod = datetime.fromtimestamp(txt_path.stat().st_mtime).date()
            if mod == date.today():
                return str(txt_path)

        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        zip_path.write_bytes(resp.content)

        with zipfile.ZipFile(zip_path) as zf:
            name = zf.namelist()[0]
            with zf.open(name) as src, open(txt_path, "wb") as dst:
                dst.write(src.read())
        zip_path.unlink()
        return str(txt_path)

    def get_option_chain_tokens(self, options_df, spot_price: float, range_pct: float) -> dict:
        lower = spot_price * (1 - range_pct / 100)
        upper = spot_price * (1 + range_pct / 100)

        in_range = options_df[
            (options_df["StrikePrice"] >= lower) & (options_df["StrikePrice"] <= upper)
        ].copy()

        strikes = sorted(in_range["StrikePrice"].unique())
        if not strikes:
            return {}

        atm = min(strikes, key=lambda s: abs(s - spot_price))
        result = {}

        for strike in strikes:
            ce = in_range[(in_range["StrikePrice"] == strike) & (in_range["OptionType"] == "CE")]
            pe = in_range[(in_range["StrikePrice"] == strike) & (in_range["OptionType"] == "PE")]
            result[float(strike)] = {
                "strike": float(strike),
                "ce_symbol": ce["TradingSymbol"].values[0] if not ce.empty else None,
                "ce_token": str(ce["Token"].values[0]) if not ce.empty else None,
                "ce_lotsize": int(ce["LotSize"].values[0]) if not ce.empty else 0,
                "pe_symbol": pe["TradingSymbol"].values[0] if not pe.empty else None,
                "pe_token": str(pe["Token"].values[0]) if not pe.empty else None,
                "pe_lotsize": int(pe["LotSize"].values[0]) if not pe.empty else 0,
            }

        return {"strikes": result, "atm": float(atm), "lower": lower, "upper": upper}

    # ── WebSocket ─────────────────────────────────────────────────

    def start_websocket(self, on_tick: Callable, on_open: Callable, on_close: Callable):
        self._api.start_websocket(
            subscribe_callback=on_tick,
            socket_open_callback=on_open,
            socket_close_callback=on_close,
        )

    def stop_websocket(self):
        try:
            self._api.close_websocket()
        except Exception:
            log.exception("Error closing Shoonya WS")

    def subscribe(self, tokens: list[str]):
        self._api.subscribe(tokens)

    def unsubscribe(self, tokens: list[str]):
        try:
            self._api.unsubscribe(tokens)
        except Exception:
            log.exception("Error unsubscribing tokens")

    # ── Orders ────────────────────────────────────────────────────

    def place_sell_order(self, exchange: str, token: str, symbol: str,
                         quantity: int, price: float, product_type: str = "M") -> dict:
        log.info("Placing SELL %s qty=%d price=%.2f", symbol, quantity, price)
        try:
            resp = self._api.place_order(
                buy_or_sell="S",
                product_type=product_type,
                exchange=exchange,
                tradingsymbol=symbol,
                quantity=quantity,
                discloseqty=0,
                price_type="LMT",
                price=price,
                trigger_price=None,
                retention="DAY",
                remarks="portal_sell",
            )
            if resp is None:
                return {"status": "FAILED", "order_id": None, "error": "No response"}
            if resp.get("stat") == "Ok":
                oid = resp.get("norenordno")
                log.info("Order placed: %s", oid)
                return {"status": "SUCCESS", "order_id": oid, "error": None}
            err = resp.get("emsg", "Unknown error")
            is_margin = any(k in err.lower() for k in ("margin", "insufficient", "funds"))
            return {"status": "FAILED", "order_id": None, "error": err, "is_margin_error": is_margin}
        except Exception as e:
            log.exception("Order exception")
            return {"status": "FAILED", "order_id": None, "error": str(e)}

    def modify_order_price(self, order_id: str, exchange: str,
                            tradingsymbol: str, quantity: int,
                            new_price: float) -> bool:
        log.info("Modifying %s -> %.2f", order_id, new_price)
        try:
            resp = self._api.modify_order(
                orderno=order_id,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                newquantity=quantity,
                newprice_type="LMT",
                newprice=new_price,
            )
            return bool(resp and resp.get("stat") == "Ok")
        except Exception:
            log.exception("Modify exception")
            return False

    def cancel_order(self, order_id: str) -> bool:
        try:
            resp = self._api.cancel_order(orderno=order_id)
            return bool(resp and resp.get("stat") == "Ok")
        except Exception:
            log.exception("Cancel exception")
            return False

    def get_order_status(self, order_id: str) -> dict | None:
        try:
            resp = self._api.single_order_history(orderno=order_id)
            if not resp or not isinstance(resp, list) or len(resp) == 0:
                return None
            latest = resp[0]
            log.info("Order %s: status=%s rpt=%s", order_id, latest.get("status"), latest.get("rpt"))
            return {
                "order_id": order_id,
                "status": latest.get("status", "UNKNOWN"),
                "filled_qty": int(latest.get("fillshares", 0) or 0),
                "quantity": int(latest.get("qty", 0) or 0),
                "price": float(latest.get("prc", 0) or 0),
                "avg_price": float(latest.get("avgprc", 0) or 0),
                "rejection_reason": latest.get("rejreason", "").strip(),
                "symbol": latest.get("tsym", ""),
            }
        except Exception:
            log.exception("Status exception")
            return None

    # ── Internal ──────────────────────────────────────────────────

    def _load_cached_session(self) -> str | None:
        if not SESSION_CACHE.exists():
            return None
        try:
            data = json.loads(SESSION_CACHE.read_text())
            if data.get("date") == str(date.today()):
                return data.get("session_token")
        except (json.JSONDecodeError, KeyError):
            pass
        return None

    def _save_session_cache(self, token: str):
        SESSION_CACHE.write_text(json.dumps({"date": str(date.today()), "session_token": token}))
