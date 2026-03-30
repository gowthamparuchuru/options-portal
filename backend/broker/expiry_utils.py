"""Expiry utilities backed by real exchange instrument data (Shoonya symbols files)."""

from __future__ import annotations

import tempfile
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .shoonya_broker import ShoonyaBroker

_EXPIRY_FMT = "%d-%b-%Y"


def _symbols_path(index_name: str) -> Path | None:
    """Return the cached symbols txt path for the given index, if it exists."""
    cfg = ShoonyaBroker.INDEX_CONFIG.get(index_name)
    if not cfg:
        return None
    prefix = cfg["options_exchange"]
    p = Path(tempfile.gettempdir()) / f"{prefix}_symbols.txt"
    return p if p.exists() else None


@lru_cache(maxsize=16)
def _monthly_expiries(index_name: str, file_mtime: float) -> set[date]:
    """Parse the symbols file and return the set of monthly expiry dates.

    file_mtime is used as a cache key so the result refreshes when the
    file is re-downloaded.
    """
    path = _symbols_path(index_name)
    if path is None:
        return set()

    cfg = ShoonyaBroker.INDEX_CONFIG[index_name]
    df = pd.read_csv(path)
    df = df[
        (df["Symbol"].isin(cfg["symbol_names"]))
        & (df["Instrument"] == cfg["instrument_type"])
    ]

    expiry_dates = df["Expiry"].dropna().unique()
    parsed: list[date] = []
    for raw in expiry_dates:
        try:
            parsed.append(datetime.strptime(raw.strip(), _EXPIRY_FMT).date())
        except ValueError:
            continue

    monthly: set[date] = set()
    by_month: dict[tuple[int, int], date] = {}
    for d in parsed:
        key = (d.year, d.month)
        if key not in by_month or d > by_month[key]:
            by_month[key] = d
    monthly = set(by_month.values())
    return monthly


def is_monthly_expiry(d: date, index_name: str) -> bool:
    """Check if *d* is the last (monthly) expiry for the given index.

    Uses the actual expiry dates from the Shoonya instruments file.
    """
    path = _symbols_path(index_name)
    if path is None:
        return False
    mtime = path.stat().st_mtime
    return d in _monthly_expiries(index_name, mtime)
