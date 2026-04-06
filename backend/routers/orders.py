from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from ..models import ExecuteBasketRequest, BasketItem, MarginItem, MarginRequest, MarginResponse
from ..broker.shoonya_broker import ShoonyaBroker
from ..broker.upstox_broker import UpstoxBroker

router = APIRouter()
log = logging.getLogger("orders")


@router.post("/execute")
async def execute_basket(req: ExecuteBasketRequest, request: Request):
    broker: ShoonyaBroker = request.app.state.broker
    upstox: UpstoxBroker | None = request.app.state.upstox_broker

    if not broker.is_logged_in():
        log.warning("Execute basket rejected — broker not authenticated")
        return {"error": "Not authenticated"}

    exec_id = str(uuid.uuid4())[:8]
    log.info("Basket execution started — exec_id=%s orders=%d", exec_id, len(req.orders))
    statuses = {}

    for item in req.orders:
        statuses[item.symbol] = {
            "order_id": None,
            "symbol": item.symbol,
            "token": item.token,
            "exchange": item.exchange,
            "strike": item.strike,
            "option_type": item.option_type,
            "lots": item.lots,
            "lot_size": item.lot_size,
            "quantity": item.lots * item.lot_size,
            "status": "PENDING",
            "phase": "",
            "attempt": 0,
            "price": 0,
            "avg_price": 0,
            "error": None,
        }

    request.app.state.active_executions[exec_id] = statuses

    tasks = [
        asyncio.create_task(_smart_sell_one(broker, upstox, item, statuses))
        for item in req.orders
    ]
    asyncio.create_task(_await_all(tasks))

    log.info("Basket execution queued — exec_id=%s symbols=%s",
             exec_id, [item.symbol for item in req.orders])
    return {"execution_id": exec_id, "count": len(req.orders)}


async def _await_all(tasks):
    for t in asyncio.as_completed(tasks):
        try:
            await t
        except Exception:
            log.exception("Unexpected error in smart sell task")


def _fetch_ltp(broker: ShoonyaBroker, upstox: UpstoxBroker | None, item: BasketItem) -> float | None:
    """Fetch LTP using Upstox (preferred) with Shoonya fallback."""
    if upstox and item.token:
        ltp = upstox.get_ltp(item.token)
        if ltp is not None:
            return ltp
        log.debug("Upstox LTP failed for %s, trying Shoonya", item.token)

    return broker.get_ltp(item.exchange, item.token)


async def _smart_sell_one(
    broker: ShoonyaBroker, upstox: UpstoxBroker | None,
    item: BasketItem, statuses: dict,
):
    sym = item.symbol
    qty = item.lots * item.lot_size
    exchange = item.exchange

    log.info("Smart sell starting — symbol=%s qty=%d exchange=%s", sym, qty, exchange)

    ltp = await asyncio.to_thread(_fetch_ltp, broker, upstox, item)
    if ltp is None:
        log.error("Could not fetch LTP for %s — aborting", sym)
        statuses[sym]["status"] = "FAILED"
        statuses[sym]["error"] = "Could not fetch LTP"
        return

    log.debug("Initial LTP for %s = %.2f", sym, ltp)
    order_id = None
    filled = False

    phases = [
        {"name": "LTP+0.10", "adj": 0.10, "wait": 10, "retries": 3},
        {"name": "LTP+0.05", "adj": 0.05, "wait": 5, "retries": 3},
        {"name": "LTP",      "adj": 0.00, "wait": 5, "retries": 50},
    ]

    for phase in phases:
        if filled:
            break

        log.info("Entering phase '%s' for %s", phase["name"], sym)
        statuses[sym]["phase"] = phase["name"]

        for attempt in range(1, phase["retries"] + 1):
            fresh_ltp = await asyncio.to_thread(_fetch_ltp, broker, upstox, item)
            if fresh_ltp is not None:
                ltp = fresh_ltp

            price = round(ltp + phase["adj"], 2)
            statuses[sym]["attempt"] = attempt
            statuses[sym]["price"] = price

            log.debug("%s phase=%s attempt=%d/%d ltp=%.2f price=%.2f",
                      sym, phase["name"], attempt, phase["retries"], ltp, price)

            if order_id is None:
                res = broker.place_sell_order(exchange, item.token, sym, qty, price)
                if res["status"] == "FAILED":
                    statuses[sym]["status"] = "FAILED"
                    statuses[sym]["error"] = res.get("error", "Place failed")
                    log.error("SELL failed for %s: %s", sym, res.get("error"))
                    filled = True
                    break
                order_id = res["order_id"]
                statuses[sym]["order_id"] = order_id
                statuses[sym]["status"] = "PLACED"
                log.info("Order placed for %s — order_id=%s price=%.2f", sym, order_id, price)
            else:
                log.debug("Modifying order %s for %s to price=%.2f", order_id, sym, price)
                broker.modify_order_price(order_id, exchange, sym, qty, price)

            await asyncio.sleep(phase["wait"])

            ost = broker.get_order_status(order_id)
            if ost is None:
                log.debug("No status returned for order %s, retrying", order_id)
                continue

            statuses[sym]["status"] = ost["status"].upper()
            statuses[sym]["avg_price"] = ost.get("avg_price", 0)

            if ost["status"].upper() in ("COMPLETE", "FILLED"):
                statuses[sym]["status"] = "FILLED"
                statuses[sym]["avg_price"] = ost["avg_price"]
                log.info("Order FILLED — symbol=%s order_id=%s avg_price=%.2f",
                         sym, order_id, ost["avg_price"])
                filled = True
                break

            if ost["status"].upper() in ("REJECTED", "CANCELLED", "CANCELED"):
                reason = ost.get("rejection_reason", "Rejected")
                statuses[sym]["status"] = "FAILED"
                statuses[sym]["error"] = reason
                log.error("Order %s for %s was %s: %s",
                          order_id, sym, ost["status"].upper(), reason)
                filled = True
                break

    if not filled:
        log.warning("Order not filled after all phases — symbol=%s order_id=%s", sym, order_id)
        statuses[sym]["status"] = "PENDING"
        statuses[sym]["error"] = "Not filled after all attempts"


@router.get("/funds")
async def get_funds(request: Request):
    broker: ShoonyaBroker = request.app.state.broker
    if not broker.is_logged_in():
        return {"error": "Not authenticated"}
    result = broker.get_available_margin()
    if result is None:
        return {"error": "Failed to fetch funds"}
    return result


@router.post("/margin", response_model=MarginResponse)
async def calculate_basket_margin(req: MarginRequest, request: Request):
    upstox: UpstoxBroker | None = request.app.state.upstox_broker
    if upstox is None:
        return MarginResponse(error="Upstox not configured — margin calculation unavailable")

    instruments = []
    for item in req.orders:
        inst_key = _resolve_instrument_key(upstox, item)
        if not inst_key:
            return MarginResponse(error=f"Could not resolve instrument for {item.index_id} {item.strike} {item.option_type}")
        instruments.append({
            "instrument_key": inst_key,
            "quantity": item.lots * item.lot_size,
            "transaction_type": "SELL",
            "product": "D",
        })

    log.debug("Calculating Upstox basket margin for %d instruments", len(instruments))
    result = upstox.get_basket_margin(instruments)

    if result.get("error"):
        log.error("Margin calculation failed: %s", result["error"])
    else:
        log.debug("Margin result — total=%.2f span=%.2f exposure=%.2f benefit=%.2f",
                  result.get("total_margin", 0), result.get("span", 0),
                  result.get("exposure", 0), result.get("margin_benefit", 0))
    return MarginResponse(**result)


def _resolve_instrument_key(upstox: UpstoxBroker, item: MarginItem) -> str | None:
    """Resolve a MarginItem to an Upstox instrument_key by matching strike + option type."""
    from datetime import datetime

    try:
        expiry = datetime.strptime(item.expiry, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        try:
            expiry = datetime.strptime(item.expiry, "%d-%b-%Y").strftime("%Y-%m-%d")
        except ValueError:
            log.error("Unparseable expiry format: %s", item.expiry)
            return None

    contracts = upstox.get_option_contracts(item.index_id, expiry)
    for c in contracts:
        if (c.get("strike_price") == item.strike
                and c.get("instrument_type") == item.option_type):
            return c.get("instrument_key")
    return None


@router.get("/status/{exec_id}")
async def get_execution_status(exec_id: str, request: Request):
    statuses = request.app.state.active_executions.get(exec_id)
    if statuses is None:
        return {"error": "Unknown execution_id"}
    return {"execution_id": exec_id, "orders": list(statuses.values())}


@router.websocket("/ws/{exec_id}")
async def order_status_ws(ws: WebSocket, exec_id: str):
    await ws.accept()
    statuses = ws.app.state.active_executions.get(exec_id)
    if statuses is None:
        await ws.send_json({"type": "error", "message": "Unknown execution_id"})
        await ws.close()
        return

    try:
        while True:
            all_done = all(
                s["status"] in ("FILLED", "FAILED", "REJECTED", "CANCELLED")
                for s in statuses.values()
            )
            await ws.send_json({
                "type": "status",
                "orders": list(statuses.values()),
                "done": all_done,
            })
            if all_done:
                await asyncio.sleep(1)
                await ws.send_json({"type": "done", "orders": list(statuses.values())})
                break
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        log.info("Order WS disconnected")
    except Exception:
        log.exception("Order WS error")
