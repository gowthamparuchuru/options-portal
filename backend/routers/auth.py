import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
log = logging.getLogger("auth")


@router.get("/broker-status")
async def broker_status(request: Request):
    """Single endpoint: login Shoonya if needed, validate both brokers."""
    shoonya = request.app.state.broker
    upstox = getattr(request.app.state, "upstox_broker", None)

    # ── Shoonya ──────────────────────────────────────────
    shoonya_ok = False
    shoonya_error = None
    try:
        if not shoonya.is_logged_in():
            log.info("Shoonya not logged in — triggering login")
            result = shoonya.login()
            if not result["ok"]:
                shoonya_error = result.get("error", "Login failed")

        if shoonya.is_logged_in():
            test = shoonya._retry_api(
                shoonya._api.get_quotes, exchange="NSE", token="26000",
                max_retries=1,
            )
            shoonya_ok = bool(test and test.get("stat") == "Ok")
            if not shoonya_ok:
                shoonya_error = shoonya_error or "Test quote call failed"
    except Exception as e:
        shoonya_error = str(e)

    # ── Upstox ───────────────────────────────────────────
    upstox_ok = False
    upstox_error = None
    if upstox is None:
        upstox_error = "Not configured"
    else:
        try:
            if not upstox.is_logged_in():
                log.info("Upstox not logged in — triggering login")
                result = upstox.login()
                if not result["ok"]:
                    upstox_error = result.get("error", "Login failed")

            if upstox.is_logged_in():
                profile = upstox.check_profile()
                upstox_ok = profile["ok"]
                if not upstox_ok:
                    upstox_error = upstox_error or profile.get("error", "Profile check failed")
        except Exception as e:
            upstox_error = str(e)

    return JSONResponse(content={
        "shoonya": {"ok": shoonya_ok, "error": shoonya_error},
        "upstox": {"ok": upstox_ok, "error": upstox_error},
    })


@router.post("/login")
async def force_login(request: Request):
    broker = request.app.state.broker
    log.info("Manual login requested")
    result = broker.login()
    if result["ok"]:
        return JSONResponse(content={"ok": True}, status_code=200)
    return JSONResponse(content={"ok": False, "error": result.get("error")}, status_code=503)
