from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import urllib.parse
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import concurrent.futures

import pyotp
import requests
from NorenRestApiPy.NorenApi import NorenApi
from playwright.sync_api import sync_playwright

from .interface import BrokerInterface, ProductType, OrderType, TransactionType

OAUTH_LOGIN_URL = "https://trade.shoonya.com/OAuthlogin/authorize/oauth?client_id={client_id}_U"
OAUTH_REDIRECT_PREFIX = "https://trade.shoonya.com/OAuthlogin"
GEN_TOKEN_URL = "https://api.shoonya.com/NorenWClientAPI/GenAcsTok"

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
        user_id = self._cfg["SHOONYA_USER_ID"]
        log.info("Login attempt for user: %s", user_id)
        cached = self._load_cached_session()
        if cached:
            log.debug("Found cached session for %s, validating...", user_id)
            self._api.set_session(
                userid=user_id,
                password=self._cfg["SHOONYA_PASSWORD"],
                usertoken=cached,
            )
            test = self._api.get_quotes(exchange="NSE", token="26000")
            if test and test.get("stat") == "Ok":
                self._logged_in = True
                log.info("Login successful (cached session) for user: %s", user_id)
                return {"ok": True, "msg": "Using cached session"}
            log.warning("Cached session expired for %s — proceeding with fresh OAuth login", user_id)
        else:
            log.debug("No valid cached session found for %s — proceeding with OAuth login", user_id)

        try:
            token = self._oauth_login()
        except Exception as exc:
            log.error("OAuth login failed for %s: %s", user_id, exc)
            return {"ok": False, "error": str(exc)}

        log.debug("OAuth token obtained, establishing session for %s", user_id)
        self._api.set_session(
            userid=user_id,
            password=self._cfg["SHOONYA_PASSWORD"],
            usertoken=token,
        )
        self._save_session_cache(token)
        self._logged_in = True
        log.info("Login successful (fresh OAuth) for user: %s", user_id)
        return {"ok": True, "msg": "Login successful"}

    def _oauth_login(self) -> str:
        user_id = self._cfg["SHOONYA_USER_ID"]
        password = self._cfg["SHOONYA_PASSWORD"]
        totp_secret = self._cfg["SHOONYA_TOTP_SECRET"]
        oauth_secret = self._cfg["SHOONYA_OAUTH_SECRET"]

        # Step 1-3: Browser automation to obtain OAuth code
        login_url = OAUTH_LOGIN_URL.format(client_id=user_id)
        log.info("Starting OAuth browser automation for user: %s", user_id)
        log.debug("OAuth login URL: %s", login_url)

        def _run_browser() -> str:
            log.debug("Launching headless Chromium for OAuth login")
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                log.debug("Navigating to OAuth login page")
                page.goto(login_url)
                page.wait_for_load_state("networkidle")

                log.debug("Filling login credentials")
                page.fill("#lgnusrid", user_id)
                page.fill("#lgnpwd", password)

                totp = pyotp.TOTP(totp_secret).now()
                log.debug("Generated TOTP, submitting login form")
                page.fill("#lgnotp", totp)

                page.click(".lgnBtnClss")
                log.debug("Waiting for OAuth redirect...")
                page.wait_for_url(f"{OAUTH_REDIRECT_PREFIX}**code=**", timeout=30000)

                url = page.url
                browser.close()
            return url

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            redirect_url = executor.submit(_run_browser).result()

        log.info("OAuth browser step completed, extracting authorization code")
        log.debug("OAuth redirect URL: %s", redirect_url)
        params = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url).query)
        code = params.get("code", [None])[0]
        if not code:
            raise RuntimeError(f"Could not extract code from redirect URL: {redirect_url}")

        log.debug("Authorization code obtained, exchanging for access token")
        # Step 4: Exchange code for access token
        checksum = hashlib.sha256(f"{user_id}_U{oauth_secret}{code}".encode()).hexdigest()
        payload = f"jData={json.dumps({'code': code, 'checksum': checksum})}"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        resp = requests.post(GEN_TOKEN_URL, headers=headers, data=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("stat") != "Ok":
            raise RuntimeError(f"Token exchange failed: {data.get('emsg', data)}")

        log.info("OAuth token exchange successful")
        return data["access_token"]

    def is_logged_in(self) -> bool:
        return self._logged_in

    # ── Funds ──────────────────────────────────────────────────────

    def get_available_margin(self) -> dict | None:
        log.debug("Fetching available margin/funds from Shoonya")
        resp = self._api.get_limits()
        if not resp or resp.get("stat") != "Ok":
            log.warning("get_limits failed: %s", resp)
            return None
        collateral = float(resp.get("collateral", 0))
        cash = float(resp.get("cash", 0))
        margin_used = float(resp.get("marginused", 0))
        available = round(collateral + cash - margin_used, 2)
        log.debug("Funds — cash: %.2f, collateral: %.2f, used: %.2f, available: %.2f",
                  cash, collateral, margin_used, available)
        return {
            "collateral": round(collateral, 2),
            "cash": round(cash, 2),
            "margin_used": round(margin_used, 2),
            "available": available,
        }

    # ── Market data ───────────────────────────────────────────────

    def get_spot_price(self, exchange: str, token: str) -> float | None:
        log.debug("Fetching spot price for %s|%s", exchange, token)
        resp = self._api.get_quotes(exchange=exchange, token=token)
        if resp and resp.get("stat") == "Ok":
            ltp = float(resp.get("lp", 0))
            if ltp > 0:
                log.debug("Spot price %s|%s = %.2f", exchange, token, ltp)
                return ltp
            return None
        log.warning("get_spot_price failed for %s|%s: %s", exchange, token, resp)
        return None

    def get_ltp(self, exchange: str, token: str) -> float | None:
        log.debug("Fetching LTP for %s|%s", exchange, token)
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
        log.info("Placing SELL order — symbol=%s qty=%d price=%.2f exchange=%s",
                 symbol, quantity, price, exchange)
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
                log.error("Place order returned no response for %s", symbol)
                return {"status": "FAILED", "order_id": None, "error": "No response"}
            if resp.get("stat") == "Ok":
                oid = resp.get("norenordno")
                log.info("Order placed successfully — order_id=%s symbol=%s", oid, symbol)
                return {"status": "SUCCESS", "order_id": oid, "error": None}
            err = resp.get("emsg", "Unknown error")
            is_margin = any(k in err.lower() for k in ("margin", "insufficient", "funds"))
            log.error("Order placement rejected for %s: %s (margin_error=%s)", symbol, err, is_margin)
            return {"status": "FAILED", "order_id": None, "error": err, "is_margin_error": is_margin}
        except Exception as e:
            log.exception("Exception while placing order for %s", symbol)
            return {"status": "FAILED", "order_id": None, "error": str(e)}

    def modify_order_price(self, order_id: str, exchange: str,
                            tradingsymbol: str, quantity: int,
                            new_price: float) -> bool:
        log.info("Modifying order — order_id=%s symbol=%s new_price=%.2f",
                 order_id, tradingsymbol, new_price)
        try:
            resp = self._api.modify_order(
                orderno=order_id,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                newquantity=quantity,
                newprice_type="LMT",
                newprice=new_price,
            )
            success = bool(resp and resp.get("stat") == "Ok")
            if success:
                log.debug("Order %s modified to %.2f successfully", order_id, new_price)
            else:
                log.warning("Order modify failed for %s: %s", order_id, resp)
            return success
        except Exception:
            log.exception("Exception while modifying order %s", order_id)
            return False

    def cancel_order(self, order_id: str) -> bool:
        log.info("Cancelling order — order_id=%s", order_id)
        try:
            resp = self._api.cancel_order(orderno=order_id)
            success = bool(resp and resp.get("stat") == "Ok")
            if success:
                log.info("Order %s cancelled successfully", order_id)
            else:
                log.warning("Order cancel failed for %s: %s", order_id, resp)
            return success
        except Exception:
            log.exception("Exception while cancelling order %s", order_id)
            return False

    def get_order_status(self, order_id: str) -> dict | None:
        log.debug("Fetching status for order %s", order_id)
        try:
            resp = self._api.single_order_history(orderno=order_id)
            if not resp or not isinstance(resp, list) or len(resp) == 0:
                log.debug("No order history returned for %s", order_id)
                return None
            latest = resp[0]
            status = latest.get("status", "UNKNOWN")
            log.debug("Order %s — status=%s rpt=%s filled=%s/%s avg_price=%s",
                      order_id, status, latest.get("rpt"),
                      latest.get("fillshares", 0), latest.get("qty", 0),
                      latest.get("avgprc", 0))
            return {
                "order_id": order_id,
                "status": status,
                "filled_qty": int(latest.get("fillshares", 0) or 0),
                "quantity": int(latest.get("qty", 0) or 0),
                "price": float(latest.get("prc", 0) or 0),
                "avg_price": float(latest.get("avgprc", 0) or 0),
                "rejection_reason": latest.get("rejreason", "").strip(),
                "symbol": latest.get("tsym", ""),
            }
        except Exception:
            log.exception("Exception while fetching status for order %s", order_id)
            return None

    # ── Internal ──────────────────────────────────────────────────

    def _load_cached_session(self) -> str | None:
        log.debug("Checking session cache at: %s", SESSION_CACHE)
        if not SESSION_CACHE.exists():
            log.debug("No session cache file found")
            return None
        try:
            data = json.loads(SESSION_CACHE.read_text())
            if data.get("date") == str(date.today()):
                log.debug("Valid session cache found for today")
                return data.get("session_token")
            log.debug("Session cache is from %s (today is %s), ignoring", data.get("date"), date.today())
        except (json.JSONDecodeError, KeyError):
            log.warning("Failed to parse session cache at %s", SESSION_CACHE)
        return None

    def _save_session_cache(self, token: str):
        SESSION_CACHE.write_text(json.dumps({"date": str(date.today()), "session_token": token}))
        log.debug("Session token cached to %s", SESSION_CACHE)
