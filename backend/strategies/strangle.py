"""Expiry-day short-strangle automation.

Standalone entry point — intended to be launched by cron on the VPS:

    python -m backend.strategies.strangle

Flags:
    --dry-run        select strikes and log intended orders, but place nothing
    --now            skip the wait and fire immediately (manual/testing)
    --index NIFTY    force a specific index, bypassing expiry detection (testing)
    --config PATH    path to strangle_config.json (default: project root)

Flow:
  1. Load broker creds (.env) + strategy config (strangle_config.json).
  2. Login: Shoonya (order placement) + Upstox (market data — required).
  3. Detect which enabled index expires TODAY, honouring index_priority
     (NIFTY wins over SENSEX when both expire the same day). If none expire
     today, exit 0 (no-op).
  4. Sleep until that index's configured trigger_time (unless --now). If
     started more than max_late_secs after the trigger, abort.
  5. Fetch spot, scan the option chain, and pick the furthest-OTM CE and PE
     strikes whose premium is still just above the configured threshold.
  6. SELL both legs with an LTP-chasing limit order (mirrors the smart-sell
     phases in routers/orders.py).
  7. Log the outcome and exit. No monitoring, stop-loss, or square-off.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import load_config, has_upstox_config
from ..broker.shoonya_broker import ShoonyaBroker
from ..broker.upstox_broker import UpstoxBroker
from .selection import select_strangle_legs
from ..notify import TelegramNotifier

log = logging.getLogger("strangle")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "strangle_config.json"

# LTP-chasing phases — mirrors _smart_sell_one in routers/orders.py.
PHASES = [
    {"name": "LTP+0.10", "adj": 0.10, "wait": 10, "retries": 3},
    {"name": "LTP+0.05", "adj": 0.05, "wait": 5, "retries": 3},
    {"name": "LTP", "adj": 0.00, "wait": 5, "retries": 50},
]


# ── Config ────────────────────────────────────────────────────────

def load_strategy_config(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"Strategy config not found: {path}")
    return json.loads(path.read_text())


# ── Expiry / index selection ──────────────────────────────────────

def _expiry_today(upstox: UpstoxBroker, index: str, today: date) -> bool:
    exp = upstox.get_nearest_expiry(index)
    if not exp:
        log.warning("Could not determine nearest expiry for %s", index)
        return False
    exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
    log.info("%s nearest expiry = %s (today = %s)", index, exp_date, today)
    return exp_date == today


def choose_target_index(upstox: UpstoxBroker, cfg: dict, today: date,
                        forced: str | None) -> str | None:
    indices = cfg["indices"]
    if forced:
        if forced not in indices:
            raise RuntimeError(f"--index {forced} is not in strangle_config.json")
        log.info("Index forced to %s (expiry detection skipped)", forced)
        return forced

    for index in cfg.get("index_priority", list(indices)):
        idx_cfg = indices.get(index)
        if not idx_cfg or not idx_cfg.get("enabled", True):
            continue
        if _expiry_today(upstox, index, today):
            return index
    return None


# ── Timing ────────────────────────────────────────────────────────

def wait_until(trigger_time: str, tz: ZoneInfo, max_late_secs: int) -> bool:
    """Sleep until today's trigger_time. Returns True to proceed, False to abort."""
    hh, mm = (int(x) for x in trigger_time.split(":"))
    now = datetime.now(tz)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    delta = (target - now).total_seconds()

    if delta > 0:
        log.info("Waiting %.0fs until trigger %s %s", delta, trigger_time, tz.key)
        time.sleep(delta)
        return True

    late = -delta
    if late <= max_late_secs:
        log.warning("Started %.0fs after trigger — firing now", late)
        return True

    log.error("Started %.0fs past trigger %s (max_late_secs=%d) — aborting",
              late, trigger_time, max_late_secs)
    return False


# ── Leg preparation ───────────────────────────────────────────────

def prepare_legs(broker: ShoonyaBroker, upstox: UpstoxBroker, index: str,
                 idx_cfg: dict, expiry_str: str, expiry_date: date) -> list[dict]:
    instrument_key = UpstoxBroker.INDEX_CONFIG[index]["instrument_key"]
    spot = upstox.get_ltp(instrument_key)
    if spot is None:
        log.error("Could not fetch spot price for %s — aborting", index)
        return []
    log.info("%s spot = %.2f", index, spot)

    threshold = float(idx_cfg["premium_threshold"])
    rows = upstox.get_chain_premiums(index, expiry_str, spot, float(idx_cfg.get("scan_range_pct", 6.0)))
    if not rows:
        log.error("Empty option chain for %s expiry=%s — aborting", index, expiry_str)
        return []

    ce_leg, pe_leg = select_strangle_legs(rows, spot, threshold)
    qty = int(idx_cfg["lots"]) * int(idx_cfg["lot_size"])

    legs: list[dict] = []
    for sel in (ce_leg, pe_leg):
        if sel is None:
            log.error("No usable %s strike found in chain", "CE" if sel is ce_leg else "PE")
            continue
        if sel.ltp <= threshold:
            log.warning("%s: no strike above threshold %.2f — using richest OTM strike %.0f @ %.2f",
                        sel.side, threshold, sel.strike, sel.ltp)

        inst = broker.lookup_option(index, expiry_date, sel.strike, sel.side)
        if inst is None:
            log.error("Could not resolve Shoonya symbol for %s %s %.0f — skipping leg",
                      index, sel.side, sel.strike)
            continue
        if inst["lot_size"] != int(idx_cfg["lot_size"]):
            log.warning("Configured lot_size %d != broker lot_size %d for %s (%s) — using configured, order may reject",
                        int(idx_cfg["lot_size"]), inst["lot_size"], index, inst["symbol"])

        row = next((r for r in rows if r["strike"] == sel.strike), None)
        upstox_key = (row["ce_key"] if sel.side == "CE" else row["pe_key"]) if row else None

        legs.append({
            "side": sel.side,
            "strike": sel.strike,
            "premium": sel.ltp,
            "symbol": inst["symbol"],
            "token": inst["token"],
            "exchange": inst["exchange"],
            "upstox_key": upstox_key,
            "qty": qty,
        })
        log.info("%s leg ready: strike=%.0f premium=%.2f symbol=%s token=%s qty=%d",
                 sel.side, sel.strike, sel.ltp, inst["symbol"], inst["token"], qty)

    return legs


# ── Execution (LTP-chasing sell) ──────────────────────────────────

def _leg_ltp(broker: ShoonyaBroker, upstox: UpstoxBroker, leg: dict) -> float | None:
    if upstox and leg.get("upstox_key"):
        v = upstox.get_ltp(leg["upstox_key"])
        if v is not None:
            return v
    return broker.get_ltp(leg["exchange"], leg["token"])


async def chase_sell(broker: ShoonyaBroker, upstox: UpstoxBroker, leg: dict,
                     product_type: str, dry_run: bool, notifier: TelegramNotifier) -> dict:
    sym = leg["symbol"]
    qty = leg["qty"]
    exchange = leg["exchange"]
    result = {"symbol": sym, "side": leg["side"], "strike": leg["strike"],
              "qty": qty, "status": "PENDING", "order_id": None,
              "avg_price": 0.0, "error": None}

    ltp = await asyncio.to_thread(_leg_ltp, broker, upstox, leg)
    if ltp is None:
        log.error("Could not fetch LTP for %s — aborting leg", sym)
        result.update(status="FAILED", error="Could not fetch LTP")
        await asyncio.to_thread(notifier.send, f"❌ Trade failed: {sym} — could not fetch LTP")
        return result

    if dry_run:
        log.info("[DRY-RUN] would SELL %s qty=%d near LTP=%.2f (product=%s)",
                 sym, qty, ltp, product_type)
        result.update(status="DRY_RUN", avg_price=ltp)
        await asyncio.to_thread(notifier.send,
                                f"🧪 [DRY-RUN] would SELL {sym} x{qty} near {ltp:.2f}")
        return result

    order_id = None
    for phase in PHASES:
        for attempt in range(1, phase["retries"] + 1):
            fresh = await asyncio.to_thread(_leg_ltp, broker, upstox, leg)
            if fresh is not None:
                ltp = fresh
            price = round(ltp + phase["adj"], 2)
            log.debug("%s phase=%s attempt=%d ltp=%.2f price=%.2f",
                      sym, phase["name"], attempt, ltp, price)

            if order_id is None:
                res = await asyncio.to_thread(
                    broker.place_sell_order, exchange, leg["token"], sym, qty, price, product_type)
                if res["status"] == "FAILED":
                    log.error("SELL failed for %s: %s", sym, res.get("error"))
                    err = res.get("error", "Place failed")
                    result.update(status="FAILED", error=err)
                    await asyncio.to_thread(notifier.send, f"❌ Trade failed: {sym} — {err}")
                    return result
                order_id = res["order_id"]
                result["order_id"] = order_id
                result["status"] = "PLACED"
                log.info("Order placed for %s — order_id=%s price=%.2f", sym, order_id, price)
            else:
                await asyncio.to_thread(broker.modify_order_price, order_id, exchange, sym, qty, price)

            await asyncio.sleep(phase["wait"])

            ost = await asyncio.to_thread(broker.get_order_status, order_id)
            if ost is None:
                continue
            status = ost["status"].upper()
            result["status"] = status
            result["avg_price"] = ost.get("avg_price", 0.0)

            if status in ("COMPLETE", "FILLED"):
                result["status"] = "FILLED"
                log.info("Order FILLED — %s order_id=%s avg_price=%.2f", sym, order_id, ost["avg_price"])
                await asyncio.to_thread(notifier.send,
                                        f"✅ Trade taken: SELL {sym} x{qty} @ {ost['avg_price']:.2f}")
                return result
            if status in ("REJECTED", "CANCELLED", "CANCELED"):
                reason = ost.get("rejection_reason", "Rejected")
                log.error("Order %s for %s was %s: %s", order_id, sym, status, reason)
                result.update(status="FAILED", error=reason)
                await asyncio.to_thread(notifier.send, f"❌ Trade failed: {sym} — {reason}")
                return result

    log.warning("Order not filled after all phases — %s order_id=%s", sym, order_id)
    result.update(status="PENDING", error="Not filled after all attempts")
    await asyncio.to_thread(notifier.send,
                            f"⚠️ {sym} placed but not filled after all attempts (order_id={order_id})")
    return result


async def execute_legs(broker: ShoonyaBroker, upstox: UpstoxBroker, legs: list[dict],
                       product_type: str, dry_run: bool, notifier: TelegramNotifier) -> list[dict]:
    tasks = [asyncio.create_task(chase_sell(broker, upstox, leg, product_type, dry_run, notifier))
             for leg in legs]
    return await asyncio.gather(*tasks)


# ── Entry point ───────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expiry-day short strangle")
    parser.add_argument("--dry-run", action="store_true",
                        help="select strikes and log, but place no orders")
    parser.add_argument("--now", action="store_true",
                        help="skip the wait and fire immediately")
    parser.add_argument("--index", choices=["NIFTY", "SENSEX"],
                        help="force an index, bypassing expiry detection")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH,
                        help="path to strangle_config.json")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for _lib in ("urllib3", "httpx", "websockets", "asyncio", "playwright"):
        logging.getLogger(_lib).setLevel(logging.WARNING)

    cfg = load_strategy_config(args.config)
    tz = ZoneInfo(cfg.get("timezone", "Asia/Kolkata"))
    today = datetime.now(tz).date()

    creds = load_config()
    notifier = TelegramNotifier.from_config(creds)

    now = datetime.now(tz)
    mode = "DRY-RUN" if args.dry_run else "LIVE"
    log.info("Telegram notifications %s", "enabled" if notifier.enabled else "disabled")
    notifier.send(f"🚀 Strangle started\nTime: {now:%Y-%m-%d %H:%M:%S} {tz.key}\nMode: {mode}")

    def _login_line(name: str, r: dict) -> str:
        ok = r.get("ok")
        return f"{name}: {'✅ ' + r.get('msg', 'token OK') if ok else '❌ ' + str(r.get('error', 'failed'))}"

    broker = ShoonyaBroker(creds)
    sh = broker.login()

    if not has_upstox_config(creds):
        notifier.send("Broker login:\n" + _login_line("Shoonya", sh) + "\nUpstox: ❌ not configured")
        log.error("Upstox credentials required for market data — aborting")
        return 2

    upstox = UpstoxBroker(creds)
    up = upstox.login()

    notifier.send("Broker login:\n" + _login_line("Shoonya", sh) + "\n" + _login_line("Upstox", up))

    if not sh.get("ok"):
        log.error("Shoonya login failed: %s", sh.get("error"))
        notifier.send("❌ Aborting — Shoonya login failed")
        return 2
    if not up.get("ok"):
        log.error("Upstox login failed: %s", up.get("error"))
        notifier.send("❌ Aborting — Upstox login failed")
        return 2

    index = choose_target_index(upstox, cfg, today, args.index)
    if index is None:
        msg = f"ℹ️ No configured index expires today ({today}) — nothing to do"
        log.info(msg)
        notifier.send(msg)
        return 0

    idx_cfg = cfg["indices"][index]
    log.info("Target index: %s — %s", index, idx_cfg)

    if not args.now:
        if not wait_until(idx_cfg["trigger_time"], tz, int(cfg.get("max_late_secs", 900))):
            return 3

    scfg = ShoonyaBroker.INDEX_CONFIG[index]
    broker.download_symbols(scfg["symbols_url"], scfg["options_exchange"])

    expiry_str = upstox.get_nearest_expiry(index)
    if not expiry_str:
        log.error("Lost expiry for %s at trigger time — aborting", index)
        notifier.send(f"❌ Aborting — lost expiry for {index} at trigger time")
        return 4
    expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()

    legs = prepare_legs(broker, upstox, index, idx_cfg, expiry_str, expiry_date)
    if not legs:
        log.error("No legs prepared for %s — aborting", index)
        notifier.send(f"❌ Aborting — no legs prepared for {index}")
        return 5

    notifier.send("📊 %s legs selected (expiry %s):\n%s" % (
        index, expiry_date,
        "\n".join(f"SELL {l['side']} {l['symbol']} @~{l['premium']:.2f} x{l['qty']}" for l in legs)))

    results = asyncio.run(execute_legs(broker, upstox, legs, idx_cfg["product_type"], args.dry_run, notifier))

    log.info("=== Strangle complete for %s (expiry %s) ===", index, expiry_date)
    for r in results:
        log.info("  %s %s strike=%.0f qty=%d -> %s avg_price=%.2f %s",
                 r["side"], r["symbol"], r["strike"], r["qty"], r["status"],
                 r["avg_price"], f"({r['error']})" if r["error"] else "")

    failed = [r for r in results if r["status"] == "FAILED"]
    return 6 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
