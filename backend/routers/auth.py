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


@router.post("/login")
async def force_login(request: Request):
    broker = request.app.state.broker
    log.info("Manual login requested")
    return _login_response(broker.login())
