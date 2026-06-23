"""Upstox broker for market data: historical candles, LTP, option chain, WebSocket."""

from __future__ import annotations

import calendar as _calendar
import concurrent.futures
import json
import logging
import re
import tempfile
import urllib.parse
from datetime import date, datetime
from pathlib import Path

import pyotp
import upstox_client
from upstox_client.rest import ApiException
from playwright.sync_api import sync_playwright

log = logging.getLogger("upstox")

SESSION_CACHE = Path(tempfile.gettempdir()) / ".upstox_session_cache"

UPSTOX_LOGIN_URL = (
    "https://api.upstox.com/v2/login/authorization/dialog"
    "?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}"
)


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

    def __init__(self, config: dict):
        self._cfg = config
        self._token: str | None = None
        self._logged_in = False

    def _make_api_client(self) -> upstox_client.ApiClient:
        configuration = upstox_client.Configuration()
        configuration.access_token = self._token
        return upstox_client.ApiClient(configuration)

    # ── Auth ──────────────────────────────────────────────────────

    def login(self) -> dict:
        log.info("Upstox login attempt")
        cached = self._load_cached_session()
        if cached:
            log.debug("Found cached Upstox token, validating...")
            self._token = cached
            if self.validate_token():
                self._logged_in = True
                log.info("Upstox login successful (cached session)")
                return {"ok": True, "msg": "Using cached session"}
            log.warning("Cached Upstox token expired — proceeding with fresh OAuth login")

        try:
            code = self._oauth_browser_login()
            token = self._exchange_code_for_token(code)
            self._token = token
            self._save_session_cache(token)
            self._logged_in = True
            log.info("Upstox login successful (fresh OAuth)")
            return {"ok": True, "msg": "Login successful"}
        except Exception as exc:
            log.error("Upstox OAuth login failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def _oauth_browser_login(self) -> str:
        """Automate the Upstox OAuth flow via headless Chromium to obtain the auth code."""
        api_key = self._cfg["UPSTOX_API_KEY"]
        redirect_uri = self._cfg["UPSTOX_REDIRECT_URI"]
        mobile = self._cfg["UPSTOX_MOBILE_NUMBER"]
        totp_secret = self._cfg["UPSTOX_TOTP_SECRET"]
        pin = self._cfg["UPSTOX_PIN"]

        encoded_redirect = urllib.parse.quote(redirect_uri, safe="")
        login_url = UPSTOX_LOGIN_URL.format(client_id=api_key, redirect_uri=encoded_redirect)
        log.info("Starting Upstox OAuth browser automation")
        log.debug("Login URL: %s", login_url)

        def _run_browser() -> str:
            log.debug("Launching headless Chromium for Upstox OAuth")
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    no_viewport=True,
                    ignore_https_errors=True,
                )
                context.clear_cookies()
                page = context.new_page()

                page.goto(login_url, wait_until="networkidle")

                log.debug("Step 1: Entering mobile number")
                page.wait_for_selector("#mobileNum", state="visible", timeout=15000)
                page.fill("#mobileNum", mobile)
                page.wait_for_selector("#getOtp:not([disabled])", timeout=10000)
                page.click("#getOtp")

                log.debug("Step 2: Entering TOTP")
                page.wait_for_selector("#otpNum", state="visible", timeout=30000)
                totp = pyotp.TOTP(totp_secret).now()
                page.fill("#otpNum", totp)
                page.wait_for_selector("#continueBtn:not([disabled])", timeout=10000)
                page.click("#continueBtn")

                log.debug("Step 3: Entering PIN")
                page.wait_for_selector("#pinCode", state="visible", timeout=30000)
                page.fill("#pinCode", pin)
                page.wait_for_selector("#pinContinueBtn:not([disabled])", timeout=10000)

                log.debug("Clicking Continue and waiting for redirect request...")
                with page.expect_request(
                    lambda req: req.url.startswith(redirect_uri),
                    timeout=30000,
                ) as req_info:
                    page.click("#pinContinueBtn")

                redirect_url = req_info.value.url
                log.debug("Captured redirect URL: %s", redirect_url)
                browser.close()

            params = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url).query)
            code = params.get("code", [None])[0]
            if not code:
                raise RuntimeError(f"No code param in redirect URL: {redirect_url}")
            return code

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(_run_browser).result()

    def _exchange_code_for_token(self, code: str) -> str:
        """Exchange the OAuth authorization code for an access token."""
        api_key = self._cfg["UPSTOX_API_KEY"]
        api_secret = self._cfg["UPSTOX_API_SECRET"]
        redirect_uri = self._cfg["UPSTOX_REDIRECT_URI"]

        log.debug("Exchanging auth code for access token")
        api_instance = upstox_client.LoginApi(upstox_client.ApiClient(upstox_client.Configuration()))
        try:
            resp = api_instance.token(
                "2.0",
                code=code,
                client_id=api_key,
                client_secret=api_secret,
                redirect_uri=redirect_uri,
                grant_type="authorization_code",
            )
        except ApiException as e:
            raise RuntimeError(f"Token exchange failed (HTTP {e.status}): {e.body}") from e

        if hasattr(resp, "access_token") and resp.access_token:
            log.info("Upstox token exchange successful")
            return resp.access_token

        raise RuntimeError(f"Token exchange returned no access_token: {resp}")

    def is_logged_in(self) -> bool:
        return self._logged_in

    def _load_cached_session(self) -> str | None:
        log.debug("Checking Upstox session cache at: %s", SESSION_CACHE)
        if not SESSION_CACHE.exists():
            log.debug("No Upstox session cache file found")
            return None
        try:
            data = json.loads(SESSION_CACHE.read_text())
            if data.get("date") == str(date.today()):
                log.debug("Valid Upstox session cache found for today")
                return data.get("access_token")
            log.debug("Upstox session cache is from %s (today is %s), ignoring",
                       data.get("date"), date.today())
        except (json.JSONDecodeError, KeyError):
            log.warning("Failed to parse Upstox session cache at %s", SESSION_CACHE)
        return None

    def _save_session_cache(self, token: str):
        SESSION_CACHE.write_text(json.dumps({"date": str(date.today()), "access_token": token}))
        log.debug("Upstox access token cached to %s", SESSION_CACHE)

    # ── Validation ───────────────────────────────────────────────

    def validate_token(self) -> bool:
        """Quick check that the access token works."""
        if not self._token:
            return False
        try:
            ltp = self.get_ltp("NSE_INDEX|Nifty 50")
            return ltp is not None
        except Exception:
            return False

    def check_profile(self) -> dict:
        """Call get_profile to verify connection. Returns {ok, error}."""
        try:
            api_instance = upstox_client.UserApi(self._make_api_client())
            resp = api_instance.get_profile("2.0")
            if resp and resp.status == "success":
                return {"ok": True}
            return {"ok": False, "error": "Profile returned non-success status"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── LTP ──────────────────────────────────────────────────────

    def get_ltp(self, instrument_key: str) -> float | None:
        try:
            quote_api = upstox_client.MarketQuoteV3Api(self._make_api_client())
            resp = quote_api.get_ltp(instrument_key=instrument_key)
            if resp.status == "success" and resp.data:
                for val in resp.data.values():
                    if val.last_price is not None:
                        return float(val.last_price)
        except Exception:
            log.exception("Upstox LTP fetch failed for %s", instrument_key)
        return None

    def get_ltp_batch(self, instrument_keys: list[str]) -> dict[str, float]:
        result: dict[str, float] = {}
        quote_api = upstox_client.MarketQuoteV3Api(self._make_api_client())
        for i in range(0, len(instrument_keys), 500):
            batch = instrument_keys[i : i + 500]
            keys_param = ",".join(batch)
            try:
                resp = quote_api.get_ltp(instrument_key=keys_param)
                if resp.status == "success" and resp.data:
                    for val in resp.data.values():
                        if val.last_price is not None:
                            result[val.instrument_token] = float(val.last_price)
            except Exception:
                log.exception("Upstox LTP batch fetch failed")
        return result

    # ── Historical Candles ───────────────────────────────────────

    def get_historical_candles(
        self,
        index_id: str,
        from_dt: datetime,
        to_dt: datetime,
        unit: str = "minutes",
        interval: int = 15,
    ) -> list[dict]:
        """Fetch OHLC candles via Upstox V3 SDK (HistoryV3Api).

        unit: minutes | hours | days | weeks | months
        interval: 1-300 for minutes, 1-5 for hours, 1 for days/weeks/months
        """
        cfg = self.INDEX_CONFIG.get(index_id)
        if not cfg:
            log.warning("Unknown index for candles: %s", index_id)
            return []

        history_api = upstox_client.HistoryV3Api(self._make_api_client())

        instrument_key = cfg["instrument_key"]
        to_str = to_dt.strftime("%Y-%m-%d")
        from_str = from_dt.strftime("%Y-%m-%d")

        candles: list[dict] = []

        try:
            resp = history_api.get_historical_candle_data1(
                instrument_key, unit, interval, to_str, from_str,
            )
            if resp.status == "success" and resp.data and resp.data.candles:
                for c in resp.data.candles:
                    ts = self._parse_candle_ts(c[0])
                    if ts:
                        candles.append({"time": ts, "open": c[1], "high": c[2], "low": c[3], "close": c[4]})
        except Exception:
            log.exception("Upstox V3 historical candle fetch failed for %s", index_id)

        try:
            intra_resp = history_api.get_intra_day_candle_data(
                instrument_key, unit, interval,
            )
            if intra_resp.status == "success" and intra_resp.data and intra_resp.data.candles:
                existing_ts = {c["time"] for c in candles}
                for c in intra_resp.data.candles:
                    ts = self._parse_candle_ts(c[0])
                    if ts and ts not in existing_ts:
                        candles.append({"time": ts, "open": c[1], "high": c[2], "low": c[3], "close": c[4]})
        except Exception:
            log.exception("Upstox V3 intraday candle fetch failed for %s", index_id)

        candles.sort(key=lambda x: x["time"])
        log.debug("Fetched %d candles for %s (%s/%s)", len(candles), index_id, unit, interval)
        return candles

    @staticmethod
    def _parse_candle_ts(ts_str: str) -> int | None:
        try:
            dt = datetime.fromisoformat(ts_str)
            return int(_calendar.timegm(dt.timetuple()))
        except Exception:
            return None

    # ── Option Chain ─────────────────────────────────────────────

    def get_nearest_expiry(self, index_id: str) -> str | None:
        cfg = self.INDEX_CONFIG.get(index_id)
        if not cfg:
            return None

        try:
            options_api = upstox_client.OptionsApi(self._make_api_client())
            resp = options_api.get_option_contracts(cfg["instrument_key"])
            if resp.status != "success" or not resp.data:
                return None

            today = date.today()
            expiries: set[date] = set()
            for contract in resp.data:
                if contract.expiry:
                    exp = contract.expiry.date() if isinstance(contract.expiry, datetime) else contract.expiry
                    if exp >= today:
                        expiries.add(exp)

            if not expiries:
                return None
            return min(expiries).strftime("%Y-%m-%d")
        except Exception:
            log.exception("Upstox get_nearest_expiry failed for %s", index_id)
            return None

    def get_option_contracts(self, index_id: str, expiry_date: str) -> list[dict]:
        cfg = self.INDEX_CONFIG.get(index_id)
        if not cfg:
            return []

        try:
            options_api = upstox_client.OptionsApi(self._make_api_client())
            resp = options_api.get_option_contracts(cfg["instrument_key"], expiry_date=expiry_date)
            if resp.status == "success" and resp.data:
                return [c.to_dict() for c in resp.data]
        except Exception:
            log.exception("Upstox get_option_contracts failed for %s", index_id)
        return []

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

        try:
            options_api = upstox_client.OptionsApi(self._make_api_client())
            resp = options_api.get_put_call_option_chain(cfg["instrument_key"], expiry_date)
            if resp.status != "success" or not resp.data:
                log.error("Empty option chain for %s expiry=%s", index_id, expiry_date)
                return None
            chain_data = resp.data
        except Exception:
            log.exception("Upstox option chain fetch failed for %s", index_id)
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
            sp = item.strike_price
            if sp is None or sp < lower or sp > upper:
                continue

            ce_key = item.call_options.instrument_key if item.call_options else None
            pe_key = item.put_options.instrument_key if item.put_options else None

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
            api_instance = upstox_client.ChargeApi(self._make_api_client())

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
        api_client = self._make_api_client()
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
