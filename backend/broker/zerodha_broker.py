"""Zerodha broker for margin calculation and historical charting."""

from __future__ import annotations

import calendar as _calendar
import json
import logging
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pyotp
from kiteconnect.exceptions import TokenException

from .kiteconnect_wrapper import Zerodha
from .expiry_utils import is_monthly_expiry
from .interface import ProductType, OrderType, TransactionType

log = logging.getLogger("zerodha")

SESSION_CACHE = Path(tempfile.gettempdir()) / ".zerodha_session_cache"

WEEKLY_MONTH_CODES = {
    1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6",
    7: "7", 8: "8", 9: "9", 10: "O", 11: "N", 12: "D",
}

MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

PRODUCT_MAP = {
    ProductType.INTRADAY: "MIS",
    ProductType.OVERNIGHT: "NRML",
    ProductType.DELIVERY: "CNC",
}

ORDER_TYPE_MAP = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.SL: "SL",
    OrderType.SL_M: "SL-M",
}

TRANSACTION_MAP = {
    TransactionType.BUY: "BUY",
    TransactionType.SELL: "SELL",
}

SYMBOL_PREFIX = {
    "NIFTY": "NIFTY",
    "SENSEX": "SENSEX",
}


KITE_INDEX_TOKENS = {
    "NIFTY": 256265,
    "SENSEX": 265,
}



def _format_strike(strike: float) -> str:
    if strike == int(strike):
        return str(int(strike))
    return str(strike)


class ZerodhaBroker:
    """Zerodha broker focused on margin calculation.

    Not a full BrokerInterface implementation — only provides symbol building
    and basket margin APIs needed for pre-trade margin estimation.
    """

    def __init__(self, config: dict):
        self._cfg = config
        self._kite: Zerodha | None = None
        self._logged_in = False

    # ── Auth ──────────────────────────────────────────────────────

    def _fresh_login(self) -> bool:
        """Do a fresh TOTP-based login. Returns True on success."""
        try:
            totp = pyotp.TOTP(self._cfg["ZERODHA_TOTP_SECRET"]).now()
            kite = Zerodha(
                user_id=self._cfg["ZERODHA_USER_ID"],
                password=self._cfg["ZERODHA_PASSWORD"],
                twofa=totp,
            )
            kite.login()
            self._kite = kite
            self._logged_in = True
            self._save_session_cache(kite)
            log.info("Zerodha fresh login successful")
            return True
        except Exception:
            log.exception("Zerodha fresh login failed")
            return False

    def login(self) -> dict:
        cached = self._load_cached_session()
        if cached:
            try:
                kite = Zerodha(user_id=self._cfg["ZERODHA_USER_ID"])
                kite.reqsession = cached["session"]
                kite.enc_token = cached["enc_token"]
                kite.user_id = self._cfg["ZERODHA_USER_ID"]
                self._kite = kite
                self._logged_in = True
                log.info("Using cached Zerodha session")
                return {"ok": True, "msg": "Using cached session"}
            except Exception:
                log.warning("Cached session invalid, doing fresh login")

        if self._fresh_login():
            return {"ok": True, "msg": "Login successful"}
        return {"ok": False, "error": "Login failed"}

    def is_logged_in(self) -> bool:
        return self._logged_in

    def _with_retry(self, fn):
        """Execute fn(); on TokenException re-login once and retry."""
        try:
            return fn()
        except TokenException:
            log.warning("Kite TokenException — attempting re-login")
            if self._fresh_login():
                return fn()
            raise

    # ── Symbol building ──────────────────────────────────────────

    def build_trading_symbol(self, index_name: str, expiry: date,
                              strike: float, option_type: str) -> str:
        """Build Zerodha-format trading symbol for an index option.

        Weekly:  {SYMBOL}{YY}{M}{DD}{STRIKE}{CE/PE}  e.g. NIFTY2640723000CE
        Monthly: {SYMBOL}{YY}{MON}{STRIKE}{CE/PE}    e.g. NIFTY26MAR23000CE

        Month codes for weekly: 1-9 for Jan-Sep, O/N/D for Oct-Dec.
        """
        prefix = SYMBOL_PREFIX.get(index_name, index_name)
        yy = f"{expiry.year % 100:02d}"
        strike_str = _format_strike(strike)

        if is_monthly_expiry(expiry, index_name):
            mon = MONTH_ABBR[expiry.month]
            return f"{prefix}{yy}{mon}{strike_str}{option_type}"
        else:
            m = WEEKLY_MONTH_CODES[expiry.month]
            dd = f"{expiry.day:02d}"
            return f"{prefix}{yy}{m}{dd}{strike_str}{option_type}"

    # ── Enum resolution ──────────────────────────────────────────

    def resolve_product_type(self, product_type: ProductType) -> str:
        return PRODUCT_MAP[product_type]

    def resolve_order_type(self, order_type: OrderType) -> str:
        return ORDER_TYPE_MAP[order_type]

    def resolve_transaction_type(self, txn_type: TransactionType) -> str:
        return TRANSACTION_MAP[txn_type]

    # ── Margin calculation ───────────────────────────────────────

    def get_basket_margin(self, orders: list[dict]) -> dict:
        """Calculate combined margin for a basket of orders via Kite API.

        Each order dict: {exchange, tradingsymbol, transaction_type, quantity}
        Returns: {total_margin, span, exposure, margin_benefit, option_premium, error}
        """
        if not self._kite:
            return {"error": "Zerodha not logged in"}

        params = []
        for o in orders:
            params.append({
                "exchange": o["exchange"],
                "tradingsymbol": o["tradingsymbol"],
                "transaction_type": o.get("transaction_type", "SELL"),
                "variety": "regular",
                "product": o.get("product", "NRML"),
                "order_type": "MARKET",
                "quantity": o["quantity"],
            })

        try:
            def _call():
                return self._kite.basket_order_margins(params)

            resp = self._with_retry(_call)
            final = resp.get("final", {})
            individual_total = sum(
                o.get("total", 0) for o in resp.get("orders", [])
            )
            combined_total = final.get("total", 0)
            benefit = individual_total - combined_total

            return {
                "total_margin": round(combined_total, 2),
                "span": round(final.get("span", 0), 2),
                "exposure": round(final.get("exposure", 0), 2),
                "margin_benefit": round(max(0, benefit), 2),
                "option_premium": round(final.get("option_premium", 0), 2),
                "error": None,
            }
        except Exception as e:
            log.exception("Margin calculation failed")
            return {"error": str(e)}

    # ── Historical candles ────────────────────────────────────

    def get_historical_candles(
        self, index_id: str, from_date: datetime, to_date: datetime,
        interval: str = "15minute",
    ) -> list[dict]:
        if not self._kite:
            return []
        token = KITE_INDEX_TOKENS.get(index_id)
        if not token:
            return []
        try:
            def _call():
                return self._kite.historical_data(
                    instrument_token=token,
                    from_date=from_date,
                    to_date=to_date,
                    interval=interval,
                )

            data = self._with_retry(_call)
            candles = []
            for c in data:
                dt = c["date"]
                ts = int(_calendar.timegm(dt.timetuple()))
                candles.append({
                    "time": ts,
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                })
            return candles
        except Exception:
            log.exception("Failed to fetch historical candles for %s", index_id)
            return []

    # ── Session cache ────────────────────────────────────────────

    def _load_cached_session(self) -> dict | None:
        if not SESSION_CACHE.exists():
            return None
        try:
            data = json.loads(SESSION_CACHE.read_text())
            if data.get("date") != str(date.today()):
                return None
            import pickle, base64
            session = pickle.loads(base64.b64decode(data["session_pickle"]))
            return {"session": session, "enc_token": data["enc_token"]}
        except Exception:
            log.warning("Failed to load cached Zerodha session")
            return None

    def _save_session_cache(self, kite: Zerodha):
        try:
            import pickle, base64
            session_bytes = pickle.dumps(kite.reqsession)
            SESSION_CACHE.write_text(json.dumps({
                "date": str(date.today()),
                "enc_token": kite.enc_token,
                "session_pickle": base64.b64encode(session_bytes).decode(),
            }))
        except Exception:
            log.warning("Failed to cache Zerodha session")
