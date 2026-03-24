from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable


class BrokerInterface(ABC):
    """Abstract interface for broker implementations.
    
    To add a new broker, subclass this and implement every method.
    Then register it in broker/factory or config.
    """

    # ── Authentication ────────────────────────────────────────────

    @abstractmethod
    def login(self) -> dict:
        """Authenticate with the broker.
        Returns {"ok": True} or {"ok": False, "error": "..."}
        """

    @abstractmethod
    def is_logged_in(self) -> bool:
        ...

    # ── Market data ───────────────────────────────────────────────

    @abstractmethod
    def get_spot_price(self, exchange: str, token: str) -> float | None:
        ...

    @abstractmethod
    def get_ltp(self, exchange: str, token: str) -> float | None:
        ...

    @abstractmethod
    def download_symbols(self, url: str, prefix: str) -> str:
        """Download symbol master file. Returns path to extracted txt."""

    @abstractmethod
    def get_option_chain_tokens(self, options_df, spot_price: float, range_pct: float) -> dict:
        """Return structured strike data for the option chain."""

    # ── WebSocket feed ────────────────────────────────────────────

    @abstractmethod
    def start_websocket(self, on_tick: Callable, on_open: Callable, on_close: Callable):
        ...

    @abstractmethod
    def subscribe(self, tokens: list[str]):
        ...

    # ── Orders ────────────────────────────────────────────────────

    @abstractmethod
    def place_sell_order(self, exchange: str, token: str, symbol: str,
                         quantity: int, price: float, product_type: str = "M") -> dict:
        """Returns {"status": "SUCCESS"|"FAILED", "order_id": ..., "error": ...}"""

    @abstractmethod
    def modify_order_price(self, order_id: str, exchange: str,
                            tradingsymbol: str, quantity: int,
                            new_price: float) -> bool:
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> dict | None:
        ...
