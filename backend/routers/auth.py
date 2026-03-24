import logging

from fastapi import APIRouter, Request

from ..models import AuthStatus

router = APIRouter()
log = logging.getLogger("auth")


@router.get("/status", response_model=AuthStatus)
async def auth_status(request: Request):
    broker = request.app.state.broker
    if broker.is_logged_in():
        return AuthStatus(authenticated=True, message="Already logged in")

    result = broker.login()
    if result["ok"]:
        return AuthStatus(authenticated=True, message=result.get("msg"))
    return AuthStatus(authenticated=False, error=result.get("error"))


@router.post("/login", response_model=AuthStatus)
async def force_login(request: Request):
    broker = request.app.state.broker
    result = broker.login()
    if result["ok"]:
        return AuthStatus(authenticated=True, message=result.get("msg"))
    return AuthStatus(authenticated=False, error=result.get("error"))
