from typing import Optional, List

from pydantic import BaseModel, Field


class AuthStatus(BaseModel):
    authenticated: bool
    error: Optional[str] = None
    message: Optional[str] = None


class OptionChainRequest(BaseModel):
    index: str = Field(..., pattern="^(NIFTY|SENSEX)$")


class BasketItem(BaseModel):
    symbol: str
    token: str
    exchange: str
    strike: float
    option_type: str = Field(..., pattern="^(CE|PE)$")
    lots: int = Field(..., ge=1)
    lot_size: int = Field(..., ge=1)


class ExecuteBasketRequest(BaseModel):
    orders: List[BasketItem]


class OrderStatusResponse(BaseModel):
    order_id: Optional[str]
    symbol: str
    status: str
    filled_qty: int = 0
    quantity: int = 0
    price: float = 0
    avg_price: float = 0
    error: Optional[str] = None


class MarginItem(BaseModel):
    exchange: str
    index_id: str = Field(..., pattern="^(NIFTY|SENSEX)$")
    strike: float
    option_type: str = Field(..., pattern="^(CE|PE)$")
    lots: int = Field(..., ge=1)
    lot_size: int = Field(..., ge=1)
    expiry: str


class MarginRequest(BaseModel):
    orders: List[MarginItem]


class MarginResponse(BaseModel):
    total_margin: float = 0
    span: float = 0
    exposure: float = 0
    margin_benefit: float = 0
    option_premium: float = 0
    error: Optional[str] = None
