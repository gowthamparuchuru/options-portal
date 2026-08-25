import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from ..broker.shoonya_broker import LOGIN_SCREENSHOT_DIR

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
    shoonya_screenshot = None
    try:
        if not shoonya.is_logged_in():
            log.info("Shoonya not logged in — triggering login")
            result = shoonya.login()
            if not result["ok"]:
                shoonya_error = result.get("error", "Login failed")
                shoonya_screenshot = result.get("screenshot")
                if shoonya_screenshot:
                    shoonya_screenshot = f"/api/auth/login-screenshot/{shoonya_screenshot}"

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
        "shoonya": {
            "ok": shoonya_ok,
            "error": shoonya_error,
            "screenshot": shoonya_screenshot,
        },
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


@router.get("/login-screenshot/{filename}")
async def login_screenshot(filename: str):
    """Serve a saved OAuth-login-failure screenshot by filename."""
    # Guard against path traversal — only serve plain PNG files from the dir.
    if "/" in filename or "\\" in filename or not filename.endswith(".png"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = (LOGIN_SCREENSHOT_DIR / filename).resolve()
    if path.parent != LOGIN_SCREENSHOT_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path, media_type="image/png")
