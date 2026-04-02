import asyncio
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from ..models import ExecuteBasketRequest, BasketItem, MarginRequest, MarginResponse
from ..broker.shoonya_broker import ShoonyaBroker
from ..broker.zerodha_broker import ZerodhaBroker

router = APIRouter()
log = logging.getLogger("orders")


@router.post("/execute")
async def execute_basket(req: ExecuteBasketRequest, request: Request):
    broker: ShoonyaBroker = request.app.state.broker
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
        asyncio.create_task(_smart_sell_one(broker, item, statuses))
        for item in req.orders
    ]
    asyncio.create_task(_await_all(tasks))

    log.info("Basket execution queued — exec_id=%s symbols=%s",
             exec_id, [item.symbol for item in req.orders])
    return {"execution_id": exec_id, "count": len(req.orders)}


async def _await_all(tasks):
    """Wait for all order tasks; log any unexpected errors."""
    for t in asyncio.as_completed(tasks):
        try:
            await t
        except Exception:
            log.exception("Unexpected error in smart sell task")


async def _smart_sell_one(broker: ShoonyaBroker, item: BasketItem, statuses: dict):
    """Execute smart sell strategy for a single basket item."""
    sym = item.symbol
    qty = item.lots * item.lot_size
    exchange = item.exchange

    log.info("Smart sell starting — symbol=%s qty=%d exchange=%s", sym, qty, exchange)

    ltp = broker.get_ltp(exchange, item.token)
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
            fresh_ltp = broker.get_ltp(exchange, item.token)
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
    margin_broker: ZerodhaBroker | None = request.app.state.margin_broker
    if margin_broker is None:
        log.debug("Margin calculation requested but Zerodha not configured")
        return MarginResponse(error="Margin calculation not available (Zerodha not configured)")

    if not margin_broker.is_logged_in():
        log.info("Zerodha not logged in, attempting login before margin calculation")
        result = margin_broker.login()
        if not result.get("ok"):
            log.error("Zerodha login failed during margin request: %s", result.get("error"))
            return MarginResponse(error=f"Zerodha login failed: {result.get('error')}")

    kite_orders = []
    for item in req.orders:
        try:
            expiry_date = datetime.strptime(item.expiry, "%d-%b-%Y").date()
        except ValueError:
            return MarginResponse(error=f"Invalid expiry format: {item.expiry}")

        tradingsymbol = margin_broker.build_trading_symbol(
            item.index_id, expiry_date, item.strike, item.option_type,
        )
        kite_orders.append({
            "exchange": item.exchange,
            "tradingsymbol": tradingsymbol,
            "transaction_type": "SELL",
            "quantity": item.lots * item.lot_size,
        })

    log.debug("Calculating basket margin for %d orders", len(kite_orders))
    result = margin_broker.get_basket_margin(kite_orders)
    if result.get("error"):
        log.error("Margin calculation failed: %s", result["error"])
    else:
        log.debug("Margin result — total=%.2f span=%.2f exposure=%.2f benefit=%.2f",
                  result.get("total_margin", 0), result.get("span", 0),
                  result.get("exposure", 0), result.get("margin_benefit", 0))
    return MarginResponse(**result)


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
