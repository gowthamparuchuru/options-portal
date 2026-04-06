import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..models import AuthStatus

router = APIRouter()
log = logging.getLogger("auth")


def _login_response(result: dict) -> JSONResponse:
    if result["ok"]:
        body = AuthStatus(authenticated=True, message=result.get("msg"))
        return JSONResponse(content=body.model_dump(), status_code=200)
    body = AuthStatus(authenticated=False, error=result.get("error"))
    return JSONResponse(content=body.model_dump(), status_code=503)


@router.get("/status")
async def auth_status(request: Request):
    broker = request.app.state.broker
    if broker.is_logged_in():
        log.debug("Auth status check — already logged in")
        body = AuthStatus(authenticated=True, message="Already logged in")
        return JSONResponse(content=body.model_dump(), status_code=200)

    log.info("Auth status check — not logged in, triggering login")
    return _login_response(broker.login())


@router.get("/broker-status")
async def broker_status(request: Request):
    """Return individual connection status for Shoonya and Upstox."""
    shoonya_broker = request.app.state.broker
    upstox_broker = getattr(request.app.state, "upstox_broker", None)

    shoonya_ok = False
    shoonya_error = None
    try:
        if shoonya_broker.is_logged_in():
            test = shoonya_broker._retry_api(
                shoonya_broker._api.get_quotes, exchange="NSE", token="26000",
                max_retries=1,
            )
            shoonya_ok = bool(test and test.get("stat") == "Ok")
            if not shoonya_ok:
                shoonya_error = "Test quote call failed"
        else:
            shoonya_error = "Not logged in"
    except Exception as e:
        shoonya_error = str(e)

    upstox_ok = False
    upstox_error = None
    if upstox_broker is None:
        upstox_error = "Not configured"
    else:
        result = upstox_broker.check_profile()
        upstox_ok = result["ok"]
        if not upstox_ok:
            upstox_error = result.get("error", "Unknown error")

    return JSONResponse(content={
        "shoonya": {"ok": shoonya_ok, "error": shoonya_error},
        "upstox": {"ok": upstox_ok, "error": upstox_error},
    })


@router.post("/login")
async def force_login(request: Request):
    broker = request.app.state.broker
    log.info("Manual login requested")
    return _login_response(broker.login())
